"""MQTT 3.1.1 transport. The demo source is injected at the publisher boundary."""
import json
import logging
import os
import signal
import ssl
import threading
from pathlib import Path

import paho.mqtt.client as mqtt

from .core import TOPIC_PREFIX
from .fixtures import get_modbus_data

log = logging.getLogger(__name__)


def create_client(client_id, *, durable):
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id=client_id,
        clean_session=not durable, protocol=mqtt.MQTTv311, manual_ack=durable,
    )
    client.enable_logger(log)
    client.reconnect_delay_set(min_delay=1, max_delay=20)
    username = os.getenv("MQTT_USERNAME")
    if username:
        client.username_pw_set(username, os.getenv("MQTT_PASSWORD"))
    if os.getenv("MQTT_TLS", "0") == "1":
        client.tls_set_context(ssl.create_default_context(cafile=os.getenv("MQTT_CA_FILE")))
    return client


def endpoint():
    tls = os.getenv("MQTT_TLS", "0") == "1"
    return os.getenv("MQTT_HOST", "localhost"), int(os.getenv("MQTT_PORT", "8883" if tls else "1883"))


def consume(transformer):
    client_id = os.getenv("MQTT_CLIENT_ID", "sibur-transform-v1")
    if not client_id:
        raise ValueError("A stable MQTT_CLIENT_ID is required")
    client = create_client(client_id, durable=True)
    ready = Path(os.getenv("READY_FILE", "/tmp/sibur-transform-ready"))
    ready.unlink(missing_ok=True)
    fatal = None
    stopping = False

    def stop_with(error):
        nonlocal fatal
        fatal = error
        client.disconnect()

    def on_connect(client, userdata, flags, reason, properties):
        if reason.is_failure:
            stop_with(RuntimeError(f"MQTT connection refused: {reason}"))
            return
        status, _ = client.subscribe(f"{TOPIC_PREFIX}/+/+", qos=1)
        if status != mqtt.MQTT_ERR_SUCCESS:
            stop_with(RuntimeError(f"MQTT subscribe failed: {status}"))

    def on_subscribe(client, userdata, mid, reasons, properties):
        if not reasons or any(reason.is_failure or reason.value != 1 for reason in reasons):
            stop_with(RuntimeError(f"Broker did not grant QoS 1: {reasons}"))
            return
        ready.write_text("ready\n", encoding="utf-8")
        log.info("Subscribed to %s/+/+ with QoS 1", TOPIC_PREFIX)

    def on_disconnect(client, userdata, flags, reason, properties):
        ready.unlink(missing_ok=True)
        log.info("MQTT disconnected: %s", reason)

    def on_message(client, userdata, message):
        try:
            result, path = transformer.handle(
                message.topic, message.payload, retained=message.retain, qos=message.qos
            )
            if message.qos:
                status = client.ack(message.mid, message.qos)
                if status != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError(f"MQTT ACK failed: {status}")
            log.info("%s topic=%s output=%s", result, message.topic, path.name)
        except Exception as error:
            # Leave transient failures unacknowledged for the durable session.
            log.exception("Processing failed before ACK")
            stop_with(error)

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    def shutdown(*_):
        nonlocal stopping
        stopping = True
        client.disconnect()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, shutdown)
    try:
        client.connect_async(*endpoint(), keepalive=45)
        status = client.loop_forever(retry_first_connection=True)
        if fatal is not None:
            raise fatal
        if not stopping and status != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT loop stopped: {status}")
    finally:
        ready.unlink(missing_ok=True)


def publish_demo():
    client = create_client("", durable=False)
    connected = threading.Event()
    reasons = []

    def on_connect(client, userdata, flags, reason, properties):
        reasons.append(reason)
        connected.set()

    client.on_connect = on_connect
    client.connect(*endpoint(), keepalive=45)
    client.loop_start()
    try:
        if not connected.wait(10):
            raise TimeoutError("No MQTT CONNACK within 10 seconds")
        if reasons[0].is_failure:
            raise RuntimeError(f"MQTT connection refused: {reasons[0]}")
        for event in get_modbus_data():
            topic = f"{TOPIC_PREFIX}/{event['device_id']}/{event['poll_id']}"
            sent = client.publish(topic, json.dumps(event, separators=(",", ":")),
                                  qos=1, retain=False)
            sent.wait_for_publish(timeout=10)
            if not sent.is_published():
                raise TimeoutError(f"Broker did not acknowledge {event['event_id']}")
            log.info("Published %s -> %s", event["event_id"], topic)
    finally:
        client.disconnect()
        client.loop_stop()
