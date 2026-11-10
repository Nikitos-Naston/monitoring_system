import base64
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from service.core import JsonSink, TOPIC_PREFIX, Transformer, run_starlark, Rejected
from service.fixtures import MESSAGES, ROUTES, TEMPLATES
from service.templates import FixtureTemplates


def envelope(event):
    return f"{TOPIC_PREFIX}/{event['device_id']}/{event['poll_id']}", json.dumps(event).encode()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = FixtureTemplates()
        self.worker = Transformer(self.repo, JsonSink(self.root))

    def handle(self, event, **kwargs):
        return self.worker.handle(*envelope(event), **kwargs)

    def test_all_fixtures_and_profiles(self):
        records = {}
        for event in MESSAGES:
            with self.subTest(event=event["event_id"]):
                status, path = self.handle(event)
                self.assertEqual(status, "decoded")
                records[event["event_id"]] = read(path)
        self.assertEqual(len(records), 24)
        self.assertEqual({x["profile"] for x in records.values()}, {t.profile for t in TEMPLATES})
        self.assertEqual(len({(x["device_id"], x["poll_id"]) for x in records.values()}), len(ROUTES))
        self.assertEqual(records["demo-02"]["data"]["temperature_c"], -1.25)
        self.assertEqual(records["demo-06"]["data"],
                         {"door_open": True, "leak": False, "ups_bypass": True, "ups_low_bat": False})
        self.assertAlmostEqual(records["demo-09"]["data"]["dust_mg_m3"], 1.23456)
        self.assertEqual(records["demo-13"]["data"]["energy_c_kwh"], 8901.23)
        self.assertEqual(records["demo-15"]["data"]["power_a_w"], 655.38)
        self.assertEqual(records["demo-15"]["word_order"], "low_first")
        self.assertEqual(records["demo-17"]["data"]["pressure_mmhg"], 760.1)
        self.assertTrue(all(records["demo-24"]["data"].values()))

    def test_same_event_replay_uses_saved_version(self):
        _, path = self.handle(MESSAGES[0])
        self.repo.templates["cc_environment"] = replace(TEMPLATES[0], version=2, source="broken")
        status, second_path = self.handle(MESSAGES[0])
        self.assertEqual((status, second_path), ("duplicate", path))
        self.assertEqual(read(path)["template_version"], 1)
        self.assertEqual(len(list((self.root / "decoded").glob("*.json"))), 1)

    def test_same_id_with_changed_payload_is_quarantined(self):
        _, path = self.handle(MESSAGES[0])
        before = path.read_bytes()
        status, rejected = self.handle({**MESSAGES[0], "registers": {"256": 100, "257": 200}})
        self.assertEqual(status, "rejected")
        self.assertIn("different payload", read(rejected)["reason"])
        self.assertEqual(path.read_bytes(), before)

    def test_wrong_topic(self):
        topic, payload = envelope(MESSAGES[0])
        status, path = self.worker.handle(topic.replace("cc01", "cc14"), payload)
        self.assertEqual(status, "rejected")
        self.assertIn("does not match", read(path)["reason"])

    def test_wrong_device(self):
        status, path = self.handle({**MESSAGES[0], "device_id": "unconfigured"})
        self.assertEqual(status, "rejected")
        self.assertIn("Unknown device", read(path)["reason"])

    def test_incomplete_block(self):
        status, path = self.handle({**MESSAGES[0], "registers": {"256": 100}})
        self.assertEqual(status, "rejected")
        self.assertIn("missing=['257']", read(path)["reason"])

    def test_extra_register(self):
        status, path = self.handle({**MESSAGES[0], "registers": {"256": 1, "257": 2, "258": 3}})
        self.assertEqual(status, "rejected")
        self.assertIn("extra=['258']", read(path)["reason"])

    def test_invalid_register_types(self):
        for word in (True, -1, 65536, 1.5, "23"):
            with self.subTest(word=word):
                self.assertEqual(self.handle({**MESSAGES[0], "registers": {"256": word}})[0],
                                 "rejected")

    def test_bad_json_and_payload_preservation(self):
        status, path = self.worker.handle(envelope(MESSAGES[0])[0], b"{bad")
        self.assertEqual(status, "rejected")
        self.assertEqual(base64.b64decode(read(path)["payload_base64"]), b"{bad")

    def test_duplicate_json_keys(self):
        topic, payload = envelope(MESSAGES[0])
        status, path = self.worker.handle(topic, payload.replace(b'"256": 2350', b'"256":1,"256":2'))
        self.assertEqual(status, "rejected")
        self.assertIn("Duplicate JSON key", read(path)["reason"])

    def test_qos_and_retained_messages(self):
        self.assertEqual(self.handle(MESSAGES[0], qos=0)[0], "rejected")
        self.assertEqual(self.handle(MESSAGES[0], retained=True)[0], "rejected")

    def test_bad_starlark_template(self):
        self.repo.templates["cc_environment"] = replace(TEMPLATES[0], source="def bad(")
        status, path = self.handle(MESSAGES[0])
        self.assertEqual(status, "rejected")
        self.assertIn("Starlark:", read(path)["reason"])

    def test_non_numeric_starlark_output(self):
        with self.assertRaisesRegex(Rejected, "finite numbers"):
            run_starlark('def transform(r, order):\n    return {"v": [1]}', {}, "high_first", 3)

    def test_starlark_timeout(self):
        with self.assertRaisesRegex(Rejected, "timed out"):
            run_starlark(TEMPLATES[0].source, {0: 1, 1: 1}, "high_first", 0.001)

    def test_infrastructure_errors_escape_for_redelivery(self):
        for target in ("templates.resolve", "sink.store"):
            obj, method = target.split(".")
            with self.subTest(target=target), patch.object(
                getattr(self.worker, obj), method, side_effect=OSError("unavailable")
            ):
                with self.assertRaises(OSError):
                    self.handle(MESSAGES[0])
        self.assertFalse(list((self.root / "rejected").glob("*.json")))


if __name__ == "__main__":
    unittest.main()
