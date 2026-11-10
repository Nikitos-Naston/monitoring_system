"""Synthetic polls based on the supplied 'Modbus RTU edge' register map."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Template:
    profile: str
    version: int
    source: str


@dataclass(frozen=True)
class Route:
    device_id: str
    poll_id: str
    asset_id: str
    profile: str
    start: int
    count: int
    word_order: str = "high_first"


# Input to transform() is keyed by offset from the configured starting address.
HELPERS = """
def signed16(value):
    return value - 65536 if value >= 32768 else value

def u32(r, offset, order):
    first, second = r[offset], r[offset + 1]
    if order == "low_first":
        first, second = second, first
    return (first << 16) | second

def flag(value, bit):
    return bool(value & (1 << bit))
"""

TEMPLATES = [
    Template("cc_environment", 1, HELPERS + """
def transform(r, order):
    return {"temperature_c": signed16(r[0]) / 100.0, "humidity_pct": r[1] / 100.0}
"""),
    Template("cc_flags", 1, HELPERS + """
def transform(r, order):
    bits = r[0]
    return {
        "door_open": flag(bits, 0),
        "leak": flag(bits, 1),
        "ups_bypass": flag(bits, 2),
        "ups_low_bat": flag(bits, 3),
    }
"""),
    Template("rc_full", 1, HELPERS + """
def transform(r, order):
    bits = r[3]
    return {
        "temperature_c": signed16(r[0]) / 100.0,
        "dust_mg_m3": u32(r, 1, order) / 100000.0,
        "door_open": flag(bits, 0),
        "leak": flag(bits, 1),
        "smoke": flag(bits, 2),
        "fan_on": flag(bits, 3),
    }
"""),
    Template("pc_full", 1, HELPERS + """
def transform(r, order):
    return {
        "temperature_c": signed16(r[0]) / 100.0,
        "humidity_pct": r[1] / 100.0,
        "power_a_w": u32(r, 2, order) / 100.0,
        "power_b_w": u32(r, 4, order) / 100.0,
        "power_c_w": u32(r, 6, order) / 100.0,
        "energy_a_kwh": u32(r, 8, order) / 100.0,
        "energy_b_kwh": u32(r, 10, order) / 100.0,
        "energy_c_kwh": u32(r, 12, order) / 100.0,
        "door_open": flag(r[14], 0),
        "fan_on": flag(r[14], 1),
    }
"""),
    Template("room_environment", 1, HELPERS + """
def transform(r, order):
    return {
        "temperature_1_c": signed16(r[0]) / 100.0,
        "temperature_2_c": signed16(r[1]) / 100.0,
        "temperature_3_c": signed16(r[2]) / 100.0,
        "temperature_4_c": signed16(r[3]) / 100.0,
        "dust_mg_m3": u32(r, 4, order) / 100000.0,
        "humidity_pct": r[6] / 100.0,
        "hygrometer_temperature_c": signed16(r[7]) / 100.0,
        "pressure_mmhg": r[8] / 10.0,
    }
"""),
    Template("room_flags", 1, HELPERS + """
def transform(r, order):
    bits = r[0]
    return {
        "room_leak": flag(bits, 0),
        "controller_cabinet_status": flag(bits, 1),
        "motion_1": flag(bits, 2),
        "motion_2": flag(bits, 3),
        "ac_1_run": flag(bits, 4),
        "ac_2_run": flag(bits, 5),
        "dehum_1_run": flag(bits, 6),
        "dehum_2_run": flag(bits, 7),
    }
"""),
]

# Poll IDs and device-to-asset bindings below are invented for the test bench.
ROUTES = [
    Route("si23-demo-a", "cc01-env", "CC01", "cc_environment", 0x0100, 2),
    Route("si23-demo-a", "cc01-flags", "CC01", "cc_flags", 0x0102, 1),
    Route("si23-demo-a", "cc14-env", "CC14", "cc_environment", 0x01D0, 2),
    Route("si23-demo-a", "cc14-flags", "CC14", "cc_flags", 0x01D2, 1),
    Route("si23-demo-b", "rc01-full", "RC01", "rc_full", 0x0200, 4),
    Route("si23-demo-b", "rc03-full", "RC03", "rc_full", 0x0220, 4),
    Route("si23-demo-b", "pc01-full", "PC01", "pc_full", 0x0300, 15),
    Route("si23-demo-b", "pc02-full", "PC02", "pc_full", 0x0320, 15, "low_first"),
    Route("si23-demo-a", "room-env", "ROOM", "room_environment", 0, 9),
    Route("si23-demo-a", "room-flags", "ROOM", "room_flags", 9, 1),
]


def words(value, order="high_first"):
    parts = [value >> 16, value & 65535]
    return parts if order == "high_first" else parts[::-1]


def power(temp, humidity, quantities, flags, order="high_first"):
    return [temp & 65535, humidity] + [
        word for value in quantities for word in words(value, order)
    ] + [flags]


SAMPLES = [
    ("cc01-env", [2350, 4567]),
    ("cc01-env", [65411, 4820]),
    ("cc14-env", [0, 0]),
    ("cc14-env", [3125, 10000]),
    ("cc01-flags", [0]),
    ("cc01-flags", [5]),
    ("cc14-flags", [10]),
    ("cc14-flags", [15]),
    ("rc01-full", [2250, *words(123456), 0]),
    ("rc01-full", [65511, *words(7), 12]),
    ("rc03-full", [3400, *words(200000), 3]),
    ("rc03-full", [2100, *words(0), 8]),
    ("pc01-full", power(2680, 4400, [123456, 234567, 345678, 678901, 789012, 890123], 1)),
    ("pc01-full", power(1900, 5000, [0, 1, 65536, 100000, 0, 125], 2)),
    ("pc02-full", power(-125, 3500, [65538, 131073, 200000, 430001, 250002, 160003], 3, "low_first")),
    ("pc02-full", power(2800, 6130, [10, 20, 30, 999999, 1000000, 2000000], 0, "low_first")),
    ("room-env", [2200, 2210, 2250, 2260, *words(400), 4550, 2240, 7601]),
    ("room-env", [65486, 100, 350, 500, *words(120000), 6500, 400, 7498]),
    ("room-env", [2450, 2460, 2470, 2480, *words(0), 5050, 2500, 7650]),
    ("room-env", [2500, 2400, 2300, 2200, *words(65538), 3350, 2320, 7517]),
    ("room-flags", [0]),
    ("room-flags", [21]),
    ("room-flags", [170]),
    ("room-flags", [255]),
]

route_by_poll = {route.poll_id: route for route in ROUTES}
MESSAGES = []
for index, (poll_id, values) in enumerate(SAMPLES, 1):
    route = route_by_poll[poll_id]
    assert len(values) == route.count
    MESSAGES.append({
        "event_id": f"demo-{index:02}",
        "device_id": route.device_id,
        "poll_id": poll_id,
        "observed_at": f"2026-11-10T12:00:{index:02}Z",
        "registers": {str(route.start + offset): value for offset, value in enumerate(values)},
    })


def get_modbus_data():
    """Stub called by the MQTT producer. A real adapter supplies this envelope."""
    return MESSAGES
