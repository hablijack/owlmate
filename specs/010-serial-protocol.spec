# SPEC-010: NDJSON protocol between ESP32 and Orange Pi

Status: implemented
Verified: 2026-08-27 — 39 dedicated contract tests (tests/test_protocol.py);
          before that date the parser had NO direct coverage at all
Depends on: 001

## Intent

One line of JSON per message over the USB CDC link. Human-readable, so the whole
system can be debugged with a serial monitor and nothing else.

## Requirements

* **R-010.1** Newline-delimited JSON, both directions, one object per line.
* **R-010.2** The link carries protocol only — no log noise.
* **R-010.3** Unknown fields must be ignored, so either side can be extended
  without breaking the other.
* **R-010.4** Absence of a device must be visible, not guessed.
* **R-010.5** Every field the firmware sends must be parsed by the Orange Pi. A field
  that is transmitted and discarded is worse than one that was never added: it
  reads as implemented on both sides while carrying nothing.
* **R-010.6** Adding a field must require changing exactly one place per side.
* **R-010.7** A malformed field must cost that field, not the frame. The parser
  runs on the foreground read loop; a single bad value must not drop an
  observation the supervisor needs, nor raise.
* **R-010.8** A telemetry frame is immutable once parsed. One frame is handed to
  navigation, speech and the web UI; none of them may edit what the others see.

## Decisions

**NDJSON over a binary protocol.** ~200 bytes per telemetry frame is nothing on
this link, and being able to read the traffic directly has paid for itself many
times over during hardware debugging.

**Telemetry every `TELEMETRY_INTERVAL_MS` (500 ms).** Object shape:

    {"type":"telemetry","state":…,"uptime":…,"loop_hz":…,"loop_max_ms":…,
     "tx_dropped":…,"fw":…,
     "heap":{"free","min","largest","psram_free","psram_largest"},
     "imu":{"pitch","roll","yaw","calibrated","cal":{"sys","gyro","accel","mag","restored"}},
     "gps":{"valid","latitude","longitude","altitude","satellites"},
     "vibration":{"detected","count","pulses"},
     "navigation":{"active","angle"},        // only while NAVIGATING
     "update":{"ssid","password","ip","url"}, // only while in UPDATE
     "servos":[5],
     "face":{"detected","x","y","w","h","confidence","gaze_x","gaze_y",
             "total","attempts","capture_ms","infer_ms","stack_free"},
     "eye":…}

**A sub-object is omitted when its device is absent.** `imu` and `gps` appear
only when the boot-time probe found them, which makes telemetry a presence
indicator for free — no separate health field needed.

**Commands** (Orange Pi → ESP32): `sleep`, `wake`, `expression`, `servo`, `gaze`,
`nav`, `blink`, `heartbeat`. While in `UPDATE` every command except `heartbeat`
is ignored, because the owl is on an isolated SoftAP and should not be steerable
from a link that is not there.

**Cumulative counters accompany intermittent flags.** `vibration.pulses` and
`face.total` exist because a 2 Hz sample of an instantaneous boolean is blind to
events shorter than the sampling interval. This has bitten twice
(SPEC-007, SPEC-009); treat it as a rule for any new intermittent signal.

**A counter needs its denominator.** `face.attempts` and `loop_hz` were added
2026-08-28 because `face.total` alone is unattributable: a low hit count can mean
the detector is missing faces or that the loop rarely runs it, and those need
opposite fixes. The pair settled it in one measurement — attempts at 4.4/s
against a `FACE_DETECT_INTERVAL_MS` promising 10/s, so the loop was the cause
(SPEC-009). Prefer shipping the denominator with the counter rather than adding
it during the next investigation.

**The telemetry write must never block the render loop, and by default it
did.** Fixed 2026-08-31, and this is the answer to the report that prompted the
`heap`/`loop_max_ms` fields in the first place. `sendTelemetry()` runs on core 1,
in the same iteration that draws the eyes. The Arduino USB CDC layer gives that
write a **256-byte** ring buffer and a 100 ms per-chunk timeout with up to 20
retries — and a telemetry line is ~900 bytes, so it never fits and is always
chunked. When the host stops draining, one frame stalls the loop for up to 2 s.
Measured on hardware with nothing reading the port: `loop_max_ms` **2256** and
**4023**, `loop_hz` **3.3**, and one captured line truncated at exactly 256
bytes — the ring size, i.e. the driver's FIFO-replacement path, not a random cut.

Three parts, and all three are needed:

* `SERIAL_TX_BUFFER_SIZE` 4096 (set **before** `Serial.begin()`, which only
  creates the ring if there is none) so a whole line fits and the ISR drains it
  in the background;
* `SERIAL_TX_TIMEOUT_MS` 0, so the write cannot wait;
* `sendJson()` asks `availableForWrite()` whether the **entire** line fits and
  drops the frame if not. Dropping beats truncating: a half-written NDJSON line
  costs the Orange Pi a parse error, while a dropped frame costs only a gap — telemetry
  is a 500 ms snapshot and its counters are cumulative, so nothing is lost.

`tx_dropped` carries the count, because a silent drop is exactly the invisible
failure `vibration.pulses` and `face.attempts` exist to prevent: the Orange Pi would
otherwise see a gap and be unable to tell "the owl went quiet" from "I was too
slow". Verified on hardware the same day, same condition (port unread for 73 s,
then attached): `loop_max_ms` **23 ms**, `tx_dropped` 139. No stall.

**This is a production requirement, not a bench nicety.** The Orange Pi runs speech
recognition in the same process; if that thread starves the reader, the owl's
eyes must not freeze. The behaviour is the job and the telemetry is the
by-product, so the by-product yields.

**A level needs its low-water mark and its largest block, not just its
level.** `heap.*` and `loop_max_ms` were added 2026-08-31, prompted by a report
that the owl renders smoothly for the first ~12 minutes and then lags and holds
the eyes on their last position, recovering instantly on a power cycle. Reading
the firmware ruled out a leak (every `esp_camera_fb_get()` is returned, esp-dl
clears its result list per run and caps it at top_k, the crop buffer is
allocated once) — but it could not rule out *fragmentation*, because the Orange Pi had
no heap field at all to look at. `free` alone would not have settled it either:
a heap that is fragmented rather than leaking shows a flat `free` and a
shrinking `largest`, so the pair is the measurement and either one alone is not.
`min` is the since-boot low-water mark, which is what survives a spike between
two 2 Hz samples. Internal and PSRAM are separate rather than summed — under
`CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL` the small allocations that churn (JSON,
String, esp-dl nodes) are all internal, and 8 MB of PSRAM would hide them.

**A mean cannot show a stall, so the outlier must be sent too.** `loop_max_ms`
is the longest single loop iteration in the window that produced `loop_hz`.
`loop_hz` is a real rate (a count over a window) and this is a real duration;
neither is derived from the other, and it is deliberately NOT expressed as a
"minimum loop_hz" — the reciprocal of one iteration is a duration wearing a
rate's clothing, which is the exact mistake SPEC-009 Falsified records. Its
*magnitude* is the diagnosis: ~35 ms is healthy, ~250 ms is one
`I2C_TIMEOUT_MS` (a slave stopped answering), and up to ~2000 ms is
`HWCDC::write()` abandoning a host that stopped draining the USB CDC port (20
retries × a 100 ms tx timeout). The eyes are frozen for every millisecond of it,
and a 500 ms-windowed mean barely registers any of it.

Honest cost, recorded here so nobody rediscovers it: the five heap fields
lengthen the telemetry line by ~100 bytes. The HWCDC TX ring buffer is 256
bytes and the line was already far past that, so if USB backpressure IS the
cause, the instrument makes the symptom marginally worse. Accepted, because
without the fields the hypothesis cannot be tested at all.

**A duration must be reported split, not summed.** `face.capture_ms` and
`face.infer_ms` were added 2026-08-31 with the move of the inference to core 0,
and they immediately overturned a figure two documents asserted. The detection
cycle had been "measured" at ~170-200 ms by dividing into `loop_hz` — but a loop
iteration is the eye render *plus* the inference, so the subtraction charged the
detector for the eyes. Timed directly the cycle is 48-91 ms, and `capture_ms` is
0 in every sample: the camera is never waited on. Neither fact is recoverable
from the sum. See SPEC-009 Falsified.

**`face.stack_free`** is the low-water mark of the core-0 detection task's
stack, in bytes. esp-dl used to run on the Arduino loop task; on its own task
its stack need was a fresh unknown whose failure mode is a silent overflow
mid-inference, so the headroom is reported continuously rather than checked
once. Measured 5,584 bytes free of 8,192 (`VISION_TASK_STACK`).

**Expression names come from one table** in `Eyes.cpp` (`NAMES[]`), which backs
both directions. The Orange Pi copy is **generated** from it
(`orangepi-brain/tools/gen_expressions.py` → `brain/expressions.py`), not
hand-maintained — see SPEC-004, where the two former mirrors are recorded as
having already drifted. An unknown name renders as `neutral` rather than being
rejected, so drift shows up as an eye that refuses to change.

**The Orange Pi side is a table, not a function.** `serial_handler.py` holds one row
per field — `(wire key, attribute, coercion)` — and the parser only handles the
frame's *shape*. It was ~60 lines of hand-written `.get()` calls until
2026-08-27, which is how seven fields came to be sent and never read (R-010.5).
One row plus one dataclass field is now the whole cost of a new field (R-010.6).

**Coercion failures degrade to the field's default** and log at debug, rather
than propagating `None` into the supervisor or raising into the read loop
(R-010.7). An explicit JSON `null` is treated as absent for the same reason.

**R-010.5 governs the INBOUND direction only, and that needed saying.** It says
every field the *firmware* sends must be parsed by the Orange Pi. It says nothing about
what the Orange Pi then re-publishes on `/api/telemetry`, and the web page is entitled
to render a subset. On 2026-08-31 the hand-written web payload was mistaken for a
violation of this requirement and nearly got the inbound fix — a `_*_FIELDS`
table — applied to a problem that was not the same shape. It was replaced with
`dataclasses.asdict()` instead, on the much weaker and honest grounds of "less
code that cannot go stale". See SPEC-012.

**One decode per line.** `_handle_message` decodes the line to dispatch on its
`type`, so it builds the frame from the already-decoded dict
(`_telemetry_from_dict`) rather than handing the string back to
`parse_telemetry()`, which decoded the same bytes a second time — on the
foreground read loop, for every telemetry frame since the dispatcher was added.
`parse_telemetry(str)` remains as the string entry point the contract tests use.
Verified by counting `json.loads` calls through `_handle_message`: 2 before, 1
after, identical parsed frame.

**Parsed frames are frozen dataclasses** (R-010.8). This was documented here and
in `AGENTS.md` well before it was true; it became true on 2026-08-27. Deriving a
modified frame is `dataclasses.replace`, which is also what the wire does — the
owl sends a whole new frame, it never patches the last one.

**Core logging is off in the firmware build.** Stray `[E][esp32-hal-i2c-ng]`
lines are protocol garbage on a machine-to-machine link.

## Verified facts

* Cadence measured 535–577 ms against the 500 ms target; the excess is I2C wait
  time inside `sendTelemetry()`.
* USB CDC ignores the nominal baud rate; real throughput is far above 115200,
  which is why image streaming to the Pi would have been viable as an
  alternative to on-device detection.
* The Orange Pi parser tolerates non-JSON lines, so bootloader output at reset does no
  harm. Four ROM/bootloader `E (...)` lines appear at every boot (unflashed
  `ota_1`, no coredump partition) and are harmless.
* **Seven fields were transmitted and discarded** until 2026-08-27:
  `imu.cal.{sys,gyro,accel,mag,restored}`, `vibration.pulses`, `face.total`.
  Every one exists to make an invisible failure visible; the calibration
  counters in particular gate `navigation.py`, so the web UI could not say why
  navigation was refusing to aim. Found by diffing the keys `sendTelemetry()`
  writes against the keys the parser reads — a two-line comparison that had
  never been run.
* Before 2026-08-27 `parse_telemetry()` and `_handle_message()` had **zero**
  direct test coverage. `serial_handler` was imported by seven test modules, but
  only to construct `Telemetry` objects by hand; the parsing itself was never
  executed by a test.

## Falsified

* **"A boolean flag is enough to report an event."** Twice wrong, in two
  subsystems, for the same reason. See the cumulative-counter decision above.
* **"Specifying the wire format means both sides implement it."** This spec
  listed `imu.cal`, `vibration.pulses` and `face.total` in the telemetry object
  above, and stated that the Orange Pi's dataclasses were frozen. Neither was true:
  the fields were parsed by nobody and the dataclasses were mutable. A spec
  written retroactively can describe the design as intended and be read as
  describing the code — and the closer it is to right, the less likely anyone
  checks. The defence is R-010.5 plus an actual test, not a tidier document.
* **"The tests cover the protocol."** Seven test modules imported
  `serial_handler`, which looks like coverage in any grep or dependency graph.
  They imported the dataclasses to build fixtures. The function under discussion
  was never called. Importing a module is not exercising it.

## Acceptance

1. `python3 orangepi-brain/tests/run_tests.py` — 174 tests pass, of which 39 are the
   protocol contract (`tests/test_protocol.py`).
2. A serial capture shows one JSON object per line and nothing else after boot.
3. Removing a device from the I2C bus makes its sub-object disappear from
   telemetry rather than reporting zeros.
4. Every key `sendTelemetry()` writes appears in one of the `_*_FIELDS` tables
   in `serial_handler.py`. This is the R-010.5 check and it is worth re-running
   by eye after any firmware telemetry change:

       grep -oE 'doc\["[a-z_]+"\](\["[a-z_]+"\])*' esp32-s3-sense/src/protocol.cpp | sort -u

## Open

* The seven newly-parsed fields have only ever been fed **synthetic** frames.
  The parsing is covered, but the web UI's rendering of the calibration state,
  GPS fix and pulse/hit totals has never been seen against a live owl.
  Check it on the next hardware run — the calibration display exists to make the
  calibration turn easier, which is the whole point of the change.
* **The `imu.cal` shape changed on 2026-09-01** with the LSM303AGR swap:
  `sys`/`gyro`/`accel`/`mag` (four 0..3 counters from the BNO055's fusion engine)
  became `axes` (0..3), `heading_ok` (bool) and `restored` (bool). The three
  dropped counters had no referent on a chip with no gyro and no fusion, and were
  deliberately **not** replaced by synthesised stand-ins. Both halves of the
  contract moved together — `_IMU_CAL_FIELDS` in `serial_handler.py`, the
  `IMUCalibration` dataclass, the web UI and `tests/test_protocol.py` — which is
  R-010.5 working as intended. See SPEC-006.
* Changes here need a matching change on the Orange Pi side. The `_*_FIELDS` tables in
  `serial_handler.py` are the other half of this contract, and R-010.5 is the
  rule that keeps them honest.
