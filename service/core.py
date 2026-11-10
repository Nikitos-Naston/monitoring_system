"""Validate a poll, apply its Starlark template and commit one JSON outcome."""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from .templates import TemplateUnavailable

MAX_PAYLOAD = 65536
IDENTIFIER = re.compile(r"[a-zA-Z0-9_-]{1,80}")
TOPIC_PREFIX = "sibur/modbus"


class Rejected(ValueError):
    pass


def unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise Rejected(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def decode(topic, payload):
    if len(payload) > MAX_PAYLOAD:
        raise Rejected("Payload exceeds 64 KiB")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object)
    except Rejected:
        raise
    except (ValueError, RecursionError) as error:
        raise Rejected("Expected a UTF-8 JSON message") from error
    if not isinstance(value, dict):
        raise Rejected("Expected a JSON object")
    for name in ("event_id", "device_id", "poll_id"):
        part = value.get(name)
        if not isinstance(part, str) or IDENTIFIER.fullmatch(part) is None:
            raise Rejected(f"Invalid {name}")
    if topic != f"{TOPIC_PREFIX}/{value['device_id']}/{value['poll_id']}":
        raise Rejected("MQTT topic does not match device_id and poll_id")
    timestamp = value.get("observed_at")
    if not isinstance(timestamp, str) or len(timestamp) > 40 or "T" not in timestamp:
        raise Rejected("observed_at must be ISO 8601 with a timezone")
    try:
        if datetime.fromisoformat(timestamp).utcoffset() is None:
            raise ValueError
    except ValueError as error:
        raise Rejected("observed_at must be ISO 8601 with a timezone") from error
    registers = value.get("registers")
    if not isinstance(registers, dict) or not 1 <= len(registers) <= 125:
        raise Rejected("registers must contain 1 to 125 words")
    for address, word in registers.items():
        if (re.fullmatch(r"0|[1-9][0-9]{0,4}", address) is None
                or int(address) > 65535 or type(word) is not int or not 0 <= word <= 65535):
            raise Rejected(f"Invalid register address or 16-bit word: {address[:24]}")
    return value


def run_starlark(source, registers, order, timeout):
    try:
        process = subprocess.run(
            [sys.executable, "-m", "service.star_worker"],
            input=json.dumps({"source": source, "registers": registers, "order": order}),
            text=True, encoding="utf-8", capture_output=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise Rejected("Starlark execution timed out") from error
    if process.returncode == 2:
        raise Rejected("Starlark: " + process.stderr.strip()[:512])
    if process.returncode != 0:
        raise RuntimeError(f"Starlark worker exited with status {process.returncode}")
    return json.loads(process.stdout)


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class JsonSink:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("decoded", "rejected"):
            (self.root / name).mkdir(exist_ok=True)
        sync_directory(self.root)

    def store(self, path, data):
        # A temporary file in the same directory makes publication atomic.
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pending-", delete=False) as out:
            pending = Path(out.name)
            try:
                out.write((json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
                           + "\n").encode("utf-8"))
                out.flush()
                os.fsync(out.fileno())
                try:
                    os.link(pending, path)
                except FileExistsError:
                    pass
                sync_directory(path.parent)
            finally:
                pending.unlink(missing_ok=True)

    def check_replay(self, path, digest):
        try:
            with path.open(encoding="utf-8") as existing:
                result = json.load(existing)
        except FileNotFoundError:
            return False
        if result["payload_sha256"] != digest:
            raise Rejected("event_id was already used with a different payload")
        sync_directory(path.parent)
        return True

    def reject(self, topic, payload, reason):
        digest = hashlib.sha256(topic.encode() + b"\0" + payload).hexdigest()
        path = self.root / "rejected" / f"{digest}.json"
        self.store(path, {
            "topic": topic, "reason": str(reason), "payload_size": len(payload),
            "payload_base64": base64.b64encode(payload[:MAX_PAYLOAD]).decode("ascii"),
            "truncated": len(payload) > MAX_PAYLOAD,
        })
        return "rejected", path


class Transformer:
    def __init__(self, templates, sink, timeout=3):
        self.templates = templates
        self.sink = sink
        self.timeout = timeout

    def handle(self, topic, payload, *, retained=False, qos=1):
        try:
            if retained or qos != 1:
                raise Rejected("Expected a non-retained QoS 1 reading")
            event = decode(topic, payload)
            device, poll, event_id = (event[name] for name in ("device_id", "poll_id", "event_id"))
            key = hashlib.sha256(f"{device}/{poll}/{event_id}".encode()).hexdigest()
            digest = hashlib.sha256(payload).hexdigest()
            path = self.sink.root / "decoded" / f"{key}.json"
            if self.sink.check_replay(path, digest):
                return "duplicate", path

            route, template = self.templates.resolve(device, poll)
            raw = event["registers"]
            expected = {str(route.start + offset) for offset in range(route.count)}
            if raw.keys() != expected:
                raise Rejected(f"Register block mismatch: missing={sorted(expected - raw.keys())}, "
                               f"extra={sorted(raw.keys() - expected)}")
            registers = {int(address) - route.start: word for address, word in raw.items()}
            readings = run_starlark(template.source, registers, route.word_order, self.timeout)
            self.sink.store(path, {
                "event_id": event_id, "device_id": device, "poll_id": poll,
                "asset_id": route.asset_id, "observed_at": event["observed_at"],
                "profile": template.profile, "template_version": template.version,
                "start_address": route.start, "word_order": route.word_order,
                "payload_sha256": digest, "data": readings,
            })
            # Also handles a simultaneous write of the same event from another worker.
            self.sink.check_replay(path, digest)
            return "decoded", path
        except (Rejected, TemplateUnavailable) as error:
            return self.sink.reject(topic, payload, error)
