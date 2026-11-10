"""Check decoded output from a clean demo volume."""
import argparse
import json
from pathlib import Path
from time import monotonic, sleep

from service.fixtures import MESSAGES, TEMPLATES


def verify(root):
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in (root / "decoded").glob("*.json")]
    by_id = {row["event_id"]: row for row in rows}
    assert len(rows) == len(MESSAGES) == len(by_id), f"Expected 24 unique files, got {len(rows)}"
    assert set(by_id) == {event["event_id"] for event in MESSAGES}
    assert {row["profile"] for row in rows} == {t.profile for t in TEMPLATES}
    assert by_id["demo-02"]["data"]["temperature_c"] == -1.25
    assert by_id["demo-15"]["data"]["power_a_w"] == 655.38
    assert by_id["demo-17"]["data"]["pressure_mmhg"] == 760.1
    assert all(by_id["demo-24"]["data"].values())
    assert not list((root / "rejected").glob("*.json")), "Unexpected rejected messages"
    return sorted(rows, key=lambda row: row["event_id"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--wait", type=float, default=0)
    args = parser.parse_args()
    deadline = monotonic() + args.wait
    while monotonic() < deadline:
        if len(list((args.directory / "decoded").glob("*.json"))) >= len(MESSAGES):
            break
        sleep(0.2)
    print(f"Verified {len(verify(args.directory))} decoded events")
