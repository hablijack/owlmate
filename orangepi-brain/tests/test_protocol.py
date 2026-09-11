"""
Protocol tests: the ESP32 -> OrangePi telemetry parser and message dispatcher.

`SerialHandler.parse_telemetry()` / `._handle_message()` / `.read_loop()` are
the OrangePi half of the NDJSON contract with `sendTelemetry()` in
esp32-s3-sense/src/protocol.cpp. Until now they had NO direct test coverage at all:
serial_handler was imported by seven test modules, but only to build Telemetry
objects by hand -- the parsing itself was never exercised.

That made the contract the least-tested and most-duplicated part of the system,
which is exactly the wrong way round. These tests pin the behaviour down so the
parser can be refactored (and new firmware fields added) without guesswork.

The firmware side of each field is cited so the two can be diffed by eye.
"""

import dataclasses
import json
import logging
import unittest

from stubs import install_stub_modules

install_stub_modules()

from brain.serial_handler import SerialHandler, Telemetry  # noqa: E402


# A complete telemetry frame, matching sendTelemetry() in protocol.cpp field for
# field. Every optional section is present here; individual tests drop parts of
# it to check the defaults.
FULL_FRAME = {
    "type": "telemetry",
    "state": "interacting",
    "uptime": 123456,
    "fw": "1.2.0",
    "eye": "happy",
    "imu": {
        "pitch": 1.5, "roll": -2.5, "yaw": 210.25, "calibrated": True,
        # Magnetometer calibration state. The firmware sends these so the
        # calibration turn can be guided; see Sensors::getImu().
        "cal": {"axes": 2, "heading_ok": True, "restored": True},
    },
    "gps": {
        "valid": True, "latitude": 49.5, "longitude": 10.75,
        "altitude": 312.5, "satellites": 9,
    },
    "vibration": {"detected": True, "count": 4, "pulses": 3252},
    "update": {
        "ssid": "RobotOwl-Update", "password": "robotowl123",
        "ip": "192.168.4.1", "url": "http://192.168.4.1/update",
    },
    "navigation": {"active": True, "angle": -12.5},
    "servos": [1.0, 2.0, 3.0, 4.0, 5.0],
    "face": {
        "detected": True, "x": 10, "y": 20, "w": 30, "h": 40,
        "confidence": 0.87, "gaze_x": -0.5, "gaze_y": 0.25, "total": 57,
        "attempts": 233, "capture_ms": 27, "infer_ms": 174, "stack_free": 3120,
    },
    "loop_hz": 10.5,
    "loop_max_ms": 34,
    "tx_dropped": 0,
    "heap": {
        "free": 142880, "min": 121344, "largest": 96256,
        "psram_free": 7340032, "psram_largest": 6291456,
    },
}


def frame(**overrides):
    """FULL_FRAME with top-level keys replaced; a value of None drops the key."""
    f = json.loads(json.dumps(FULL_FRAME))
    for k, v in overrides.items():
        if v is None:
            f.pop(k, None)
        else:
            f[k] = v
    return f


def parse(f):
    return SerialHandler("/dev/null").parse_telemetry(json.dumps(f))


class TestParseTelemetryScalars(unittest.TestCase):
    """Top-level scalars, including the firmware's abbreviated key names."""

    def test_full_frame_scalars(self):
        t = parse(FULL_FRAME)
        self.assertIsNotNone(t)
        self.assertEqual(t.state, "interacting")
        # The wire names are terse: "uptime" -> uptime_ms, "fw" -> firmware,
        # "eye" -> eye_expression. Renaming on the wire would break the ESP32.
        self.assertEqual(t.uptime_ms, 123456)
        self.assertEqual(t.firmware, "1.2.0")
        self.assertEqual(t.eye_expression, "happy")

    def test_missing_scalars_fall_back_to_defaults(self):
        t = parse(frame(state=None, uptime=None, fw=None, eye=None))
        self.assertEqual(t.state, "idle")
        self.assertEqual(t.uptime_ms, 0)
        self.assertEqual(t.firmware, "")
        self.assertEqual(t.eye_expression, "neutral")

    def test_timestamp_is_arrival_time_not_from_the_wire(self):
        # The frame carries no timestamp; the OrangePi stamps it on arrival, and
        # check_stale()/Navigation's self-timeout both depend on that.
        t = parse(FULL_FRAME)
        self.assertGreater(t.timestamp, 0)


class TestParseTelemetrySections(unittest.TestCase):
    """The nested sections and their defaults."""

    def test_imu(self):
        t = parse(FULL_FRAME)
        self.assertAlmostEqual(t.imu.pitch, 1.5)
        self.assertAlmostEqual(t.imu.roll, -2.5)
        self.assertAlmostEqual(t.imu.yaw, 210.25)
        self.assertTrue(t.imu.calibrated)

    def test_gps(self):
        t = parse(FULL_FRAME)
        self.assertTrue(t.gps.valid)
        self.assertAlmostEqual(t.gps.latitude, 49.5)
        self.assertAlmostEqual(t.gps.longitude, 10.75)
        self.assertAlmostEqual(t.gps.altitude, 312.5)
        self.assertEqual(t.gps.satellites, 9)

    def test_vibration(self):
        t = parse(FULL_FRAME)
        self.assertTrue(t.vibration.detected)
        self.assertEqual(t.vibration.count, 4)

    def test_face(self):
        t = parse(FULL_FRAME)
        self.assertTrue(t.face.detected)
        self.assertEqual((t.face.x, t.face.y, t.face.w, t.face.h), (10, 20, 30, 40))
        self.assertAlmostEqual(t.face.confidence, 0.87)
        self.assertAlmostEqual(t.face.gaze_x, -0.5)
        self.assertAlmostEqual(t.face.gaze_y, 0.25)

    def test_servos_pass_through(self):
        t = parse(FULL_FRAME)
        self.assertEqual(t.servos, [1.0, 2.0, 3.0, 4.0, 5.0])

    def test_absent_sections_give_zeroed_defaults(self):
        # A frame with every optional section missing must still parse. The
        # firmware genuinely omits imu/gps when those peripherals are not ready.
        t = parse(frame(imu=None, gps=None, vibration=None, face=None, servos=None))
        self.assertIsNotNone(t)
        self.assertEqual(t.imu.yaw, 0.0)
        self.assertFalse(t.imu.calibrated)
        self.assertFalse(t.gps.valid)
        self.assertEqual(t.gps.satellites, 0)
        self.assertFalse(t.vibration.detected)
        self.assertFalse(t.face.detected)
        self.assertEqual(t.servos, [0.0] * 5)


class TestDiagnosticFields(unittest.TestCase):
    """The fields the firmware sends to make invisible failures visible.

    Seven of them were being sent by sendTelemetry() and silently discarded
    by the OrangePi until 2026-08-27. They are the difference between "the sensor is
    quiet" and "the sensor is dead", so a dropped diagnostic field is worse than
    a missing feature -- it makes a fault look like normal operation.
    """

    def test_imu_calibration_state(self):
        # Navigation refuses to aim while imu.calibrated is false, so without
        # these the web UI cannot tell you WHY it is refusing, or guide the
        # calibration turn.
        t = parse(FULL_FRAME)
        self.assertEqual(t.imu.cal.axes, 2)
        self.assertTrue(t.imu.cal.heading_ok)
        self.assertTrue(t.imu.cal.restored)

    def test_imu_calibration_defaults_when_absent(self):
        imu = dict(FULL_FRAME["imu"])
        imu.pop("cal")
        t = parse(frame(imu=imu))
        self.assertEqual(t.imu.cal.axes, 0)
        self.assertFalse(t.imu.cal.heading_ok)
        self.assertFalse(t.imu.cal.restored)
        # The rest of the IMU section must still parse.
        self.assertAlmostEqual(t.imu.yaw, 210.25)

    def test_vibration_pulse_total(self):
        # Raw ISR edge count since boot: constant at rest, rising while the owl
        # is motionless means interference or an over-sensitive pot.
        t = parse(FULL_FRAME)
        self.assertEqual(t.vibration.pulses, 3252)

    def test_face_total(self):
        # Cumulative hits. `detected` is an instantaneous 2 Hz sample and misses
        # sporadic detections; this is what exposes them.
        t = parse(FULL_FRAME)
        self.assertEqual(t.face.total, 57)

    def test_loop_max_ms(self):
        # The mean loop_hz next to it cannot show a stall: on this owl the mean
        # went DOWN when a visible freeze was fixed (see owl.h). A single
        # 2000 ms iteration barely moves a 500 ms-windowed mean but is stated
        # outright here.
        t = parse(FULL_FRAME)
        self.assertEqual(t.loop_max_ms, 34)

    def test_heap_levels(self):
        # free AND largest, because only the pair separates a leak from
        # fragmentation. See HeapData.
        t = parse(FULL_FRAME)
        self.assertEqual(t.heap.free, 142880)
        self.assertEqual(t.heap.min, 121344)
        self.assertEqual(t.heap.largest, 96256)
        self.assertEqual(t.heap.psram_free, 7340032)
        self.assertEqual(t.heap.psram_largest, 6291456)

    def test_tx_dropped(self):
        # Lines the firmware threw away rather than block the render loop on.
        # Rising means the OrangePi reader fell behind -- not that the owl went
        # quiet, which is the reading a missing counter would invite.
        t = parse(frame(tx_dropped=17))
        self.assertEqual(t.tx_dropped, 17)

    def test_heap_and_loop_max_default_on_older_firmware(self):
        # A build from before 2026-08-31 sends neither. That must read as "not
        # reported", not as "the heap is exhausted and the loop never stalls".
        t = parse(frame(heap=None, loop_max_ms=None, tx_dropped=None))
        self.assertEqual(t.heap.free, 0)
        self.assertEqual(t.heap.largest, 0)
        self.assertEqual(t.loop_max_ms, 0)
        self.assertEqual(t.tx_dropped, 0)
        # The rest of the frame must still parse.
        self.assertEqual(t.face.total, 57)

    def test_face_attempts(self):
        # The denominator for `total`. A low hit count means either a bad
        # detector or a starved main loop, and only total/attempts tells them
        # apart -- measured 2026-08-28, attempts sat at 4.4/s against the 10/s
        # FACE_DETECT_INTERVAL_MS promises, which located the bug in the loop.
        t = parse(FULL_FRAME)
        self.assertEqual(t.face.attempts, 233)

    def test_face_cycle_timing(self):
        # capture_ms vs infer_ms: waiting on the camera against computing. The
        # sum was known (~170-200 ms) long before the split was, and without
        # the split it was not decidable whether moving the inference to the
        # ESP32's core 0 could raise the detection rate at all -- a
        # capture-bound cycle gains nothing from a free core.
        t = parse(FULL_FRAME)
        self.assertEqual(t.face.capture_ms, 27)
        self.assertEqual(t.face.infer_ms, 174)

    def test_face_stack_free(self):
        # Low-water mark of the ESP32's core-0 detection task stack. esp-dl ran
        # on the Arduino loop task's 8192-byte stack before 2026-08-31; on its
        # own task the headroom is a new unknown, and its failure mode is a
        # silent overflow in the middle of an inference.
        t = parse(FULL_FRAME)
        self.assertEqual(t.face.stack_free, 3120)

    def test_loop_hz(self):
        # Measured main-loop rate, i.e. the RENDER rate. It used to also be the
        # ceiling on the detection rate, because the inference ran from loop();
        # since 2026-08-31 it runs on core 0 and the two are independent.
        t = parse(FULL_FRAME)
        self.assertAlmostEqual(t.loop_hz, 10.5)

    def test_diagnostic_fields_default_to_zero(self):
        t = parse(frame(imu=None, vibration=None, face=None, loop_hz=None))
        self.assertEqual(t.imu.cal.axes, 0)
        self.assertEqual(t.vibration.pulses, 0)
        self.assertEqual(t.face.total, 0)
        self.assertEqual(t.face.attempts, 0)
        self.assertEqual(t.face.capture_ms, 0)
        self.assertEqual(t.face.infer_ms, 0)
        self.assertEqual(t.face.stack_free, 0)
        self.assertEqual(t.loop_hz, 0.0)


class TestMalformedFieldsAreIsolated(unittest.TestCase):
    """One bad field must not cost the whole frame.

    parse_telemetry runs on the foreground read loop, so a firmware bug or a
    corrupted byte in one field must degrade to that field's default rather
    than dropping a frame the supervisor needs.
    """

    def test_bad_scalar_falls_back_to_default(self):
        t = parse(frame(uptime="not-a-number"))
        self.assertIsNotNone(t)
        self.assertEqual(t.uptime_ms, 0)
        self.assertEqual(t.state, "interacting")   # neighbours unaffected

    def test_bad_nested_field_falls_back(self):
        imu = dict(FULL_FRAME["imu"], yaw="garbage")
        t = parse(frame(imu=imu))
        self.assertIsNotNone(t)
        self.assertEqual(t.imu.yaw, 0.0)
        self.assertAlmostEqual(t.imu.pitch, 1.5)   # neighbours unaffected

    def test_explicit_null_is_treated_as_absent(self):
        # A JSON null must fall back to the default, NOT become the string
        # "None" -- which is what a bare str(data.get(key)) would produce.
        raw = json.dumps(dict(FULL_FRAME, fw=None, state=None, uptime=None))
        t = SerialHandler("/dev/null").parse_telemetry(raw)
        self.assertEqual(t.firmware, "")
        self.assertEqual(t.state, "idle")
        self.assertEqual(t.uptime_ms, 0)

    def test_explicit_null_section_is_treated_as_absent(self):
        raw = json.dumps(dict(FULL_FRAME, imu=None, face=None))
        t = SerialHandler("/dev/null").parse_telemetry(raw)
        self.assertEqual(t.imu.yaw, 0.0)
        self.assertFalse(t.face.detected)

    def test_wrong_type_for_a_whole_section(self):
        # A section arriving as a scalar/list instead of an object.
        for bad in ("nonsense", 42, [1, 2, 3]):
            t = parse(frame(imu=bad, gps=bad, face=bad))
            self.assertIsNotNone(t, bad)
            self.assertEqual(t.imu.yaw, 0.0)
            self.assertFalse(t.gps.valid)
            self.assertFalse(t.face.detected)

    def test_servos_wrong_type_falls_back(self):
        t = parse(frame(servos="not a list"))
        self.assertEqual(t.servos, [0.0] * 5)


class TestFramesAreImmutable(unittest.TestCase):
    """A frame is an observation of a moment, shared by several consumers.

    The supervisor hands the same object to navigation, speech and the web UI,
    so none of them may edit it. AGENTS.md described these as frozen well before
    they actually were.
    """

    def test_telemetry_cannot_be_mutated(self):
        t = parse(FULL_FRAME)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            t.state = "idle"

    def test_sections_cannot_be_mutated(self):
        t = parse(FULL_FRAME)
        for obj, attr in ((t.face, "detected"), (t.imu, "yaw"),
                          (t.gps, "valid"), (t.vibration, "count"),
                          (t.imu.cal, "axes"), (t.navigation, "angle")):
            with self.assertRaises(dataclasses.FrozenInstanceError):
                setattr(obj, attr, 0)

    def test_replace_builds_a_new_frame(self):
        # The supported way to derive a frame (used by the tests themselves).
        t = parse(FULL_FRAME)
        t2 = dataclasses.replace(t, state="idle")
        self.assertEqual(t.state, "interacting")
        self.assertEqual(t2.state, "idle")


class TestParseTelemetryPresenceFlags(unittest.TestCase):
    """`update` and `navigation` are PRESENCE-flagged, not value-flagged.

    The firmware emits each section only while in that state, so the OrangePi infers
    active=True from the section merely being there.
    """

    def test_update_absent_means_inactive(self):
        t = parse(frame(update=None))
        self.assertFalse(t.update.active)
        self.assertEqual(t.update.ssid, "")

    def test_update_present_means_active(self):
        t = parse(FULL_FRAME)
        self.assertTrue(t.update.active)
        self.assertEqual(t.update.ssid, "RobotOwl-Update")
        self.assertEqual(t.update.password, "robotowl123")
        self.assertEqual(t.update.ip, "192.168.4.1")
        self.assertEqual(t.update.url, "http://192.168.4.1/update")

    def test_navigation_absent_means_inactive(self):
        t = parse(frame(navigation=None))
        self.assertFalse(t.navigation.active)
        self.assertEqual(t.navigation.angle, 0.0)

    def test_navigation_present_is_parsed(self):
        t = parse(FULL_FRAME)
        self.assertTrue(t.navigation.active)
        self.assertAlmostEqual(t.navigation.angle, -12.5)


class TestParseTelemetryRejects(unittest.TestCase):
    """Non-telemetry input must return None rather than a half-built frame."""

    def test_other_message_types_return_none(self):
        for msg in ({"type": "boot"}, {"type": "expression_ack"},
                    {"type": "hardware_check"}, {"type": "error"}):
            self.assertIsNone(parse(msg), msg)

    def test_missing_type_returns_none(self):
        self.assertIsNone(parse(frame(type=None)))

    def test_malformed_json_returns_none(self):
        h = SerialHandler("/dev/null")
        for bad in ("", "{", "not json", '{"type":'):
            self.assertIsNone(h.parse_telemetry(bad), bad)


class TestHandleMessage(unittest.TestCase):
    """Dispatch by `type`: only telemetry reaches the callback."""

    def setUp(self):
        self.h = SerialHandler("/dev/null")
        self.seen = []

    def _dispatch(self, obj):
        # Silence the handler's own logging for the duration.
        logging.getLogger("brain.serial_handler").setLevel(logging.CRITICAL)
        self.h._handle_message(json.dumps(obj), self.seen.append)

    def test_telemetry_reaches_the_callback(self):
        self._dispatch(FULL_FRAME)
        self.assertEqual(len(self.seen), 1)
        self.assertIsInstance(self.seen[0], Telemetry)

    def test_non_telemetry_does_not_reach_the_callback(self):
        for msg in ({"type": "boot", "msg": "ready"},
                    {"type": "update_mode", "ssid": "x", "password": "y", "url": "z"},
                    {"type": "update_mode_end"},
                    {"type": "error", "msg": "invalid_json"},
                    {"type": "servo_ack", "channel": 2, "angle": 0},
                    {"type": "something_new"}):
            self._dispatch(msg)
        self.assertEqual(self.seen, [])

    def test_malformed_line_is_swallowed(self):
        logging.getLogger("brain.serial_handler").setLevel(logging.CRITICAL)
        self.h._handle_message("{not json", self.seen.append)   # must not raise
        self.assertEqual(self.seen, [])

    def test_no_callback_is_allowed(self):
        self.h._handle_message(json.dumps(FULL_FRAME), None)    # must not raise


class TestLogRecordsAreFormattable(unittest.TestCase):
    """Every log call must actually be renderable.

    Regression test for a real bug: `logger.debug("Owl ack: %s %s", data)` had
    two placeholders and one argument, so every ack raised a logging error the
    moment debug logging was switched on. logging swallows that internally and
    prints to stderr, so it never failed a test -- the only way to catch it is
    to render each captured record's message.
    """

    def _assert_records_render(self, obj):
        h = SerialHandler("/dev/null")
        with self.assertLogs("brain.serial_handler", level="DEBUG") as cap:
            h._handle_message(json.dumps(obj), None)
        for record in cap.records:
            record.getMessage()   # raises TypeError on an arg/placeholder mismatch

    def test_ack_log_renders(self):
        for ack in ("expression_ack", "servo_ack", "nav_ack",
                    "sleep_ack", "wake_ack", "blink_ack", "heartbeat_ack"):
            self._assert_records_render({"type": ack, "value": "x"})

    def test_other_message_logs_render(self):
        for msg in ({"type": "boot", "msg": "ready"},
                    {"type": "update_mode", "ssid": "s", "password": "p", "url": "u"},
                    {"type": "update_mode_end"},
                    {"type": "error", "msg": "line_too_long"},
                    {"type": "unhandled_kind"},
                    {"type": "telemetry", "state": "idle"}):
            self._assert_records_render(msg)


class FakePort:
    """Minimal pyserial stand-in: hands out scripted byte chunks, then closes.

    read_loop() spins on `while self.serial.is_open`, so the port closes itself
    once the script is exhausted; that is what terminates the loop.
    """

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.is_open = True
        self.in_waiting = 0

    def read(self, _n):
        if not self._chunks:
            self.is_open = False
            return b""
        return self._chunks.pop(0)

    def close(self):
        self.is_open = False


class TestReadLoop(unittest.TestCase):
    """Framing and fault isolation in the read loop."""

    def _run(self, chunks, callback=None):
        h = SerialHandler("/dev/null")
        h.serial = FakePort(chunks)
        seen = []
        logging.getLogger("brain.serial_handler").setLevel(logging.CRITICAL)
        h.read_loop(callback or seen.append)
        return seen

    def test_frames_split_on_newlines(self):
        line = json.dumps(FULL_FRAME).encode()
        seen = self._run([line + b"\n", line + b"\n"])
        self.assertEqual(len(seen), 2)

    def test_frame_split_across_reads_is_reassembled(self):
        # NDJSON arrives in arbitrary chunks; a frame straddling two reads must
        # be buffered, not dropped.
        raw = json.dumps(FULL_FRAME).encode() + b"\n"
        mid = len(raw) // 2
        seen = self._run([raw[:mid], raw[mid:]])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].state, "interacting")

    def test_blank_lines_and_garbage_are_skipped(self):
        raw = json.dumps(FULL_FRAME).encode() + b"\n"
        seen = self._run([b"\n", b"   \n", b"rubbish\n", raw])
        self.assertEqual(len(seen), 1)

    def test_a_raising_callback_does_not_kill_the_loop(self):
        # read_loop runs on the FOREGROUND thread (main.py hands it the main
        # thread), so an exception escaping here used to take the whole brain
        # down over a single bad frame. The second frame must still arrive.
        calls = []

        def flaky(t):
            calls.append(t)
            if len(calls) == 1:
                raise ValueError("boom in the supervisor callback")

        raw = json.dumps(FULL_FRAME).encode() + b"\n"
        self._run([raw, raw], callback=flaky)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
