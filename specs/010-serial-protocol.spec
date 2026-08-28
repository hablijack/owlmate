# SPEC-010: NDJSON protocol between ESP32 and Raspberry Pi

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
* **R-010.5** Every field the firmware sends must be parsed by the RPi. A field
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

    {"type":"telemetry","state":…,"uptime":…,"loop_hz":…,"fw":…,
     "imu":{"pitch","roll","yaw","calibrated","cal":{"sys","gyro","accel","mag","restored"}},
     "gps":{"valid","latitude","longitude","altitude","satellites"},
     "vibration":{"detected","count","pulses"},
     "navigation":{"active","angle"},        // only while NAVIGATING
     "update":{"ssid","password","ip","url"}, // only while in UPDATE
     "servos":[5],
     "face":{"detected","x","y","w","h","confidence","gaze_x","gaze_y",
             "total","attempts"},
     "eye":…}

**A sub-object is omitted when its device is absent.** `imu` and `gps` appear
only when the boot-time probe found them, which makes telemetry a presence
indicator for free — no separate health field needed.

**Commands** (RPi → ESP32): `sleep`, `wake`, `expression`, `servo`, `gaze`,
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

**Expression names come from one table** in `Eyes.cpp` (`NAMES[]`), which backs
both directions. The RPi copy is **generated** from it
(`rpi-brain/tools/gen_expressions.py` → `brain/expressions.py`), not
hand-maintained — see SPEC-004, where the two former mirrors are recorded as
having already drifted. An unknown name renders as `neutral` rather than being
rejected, so drift shows up as an eye that refuses to change.

**The RPi side is a table, not a function.** `serial_handler.py` holds one row
per field — `(wire key, attribute, coercion)` — and the parser only handles the
frame's *shape*. It was ~60 lines of hand-written `.get()` calls until
2026-08-27, which is how seven fields came to be sent and never read (R-010.5).
One row plus one dataclass field is now the whole cost of a new field (R-010.6).

**Coercion failures degrade to the field's default** and log at debug, rather
than propagating `None` into the supervisor or raising into the read loop
(R-010.7). An explicit JSON `null` is treated as absent for the same reason.

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
* The RPi parser tolerates non-JSON lines, so bootloader output at reset does no
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
  above, and stated that the RPi's dataclasses were frozen. Neither was true:
  the fields were parsed by nobody and the dataclasses were mutable. A spec
  written retroactively can describe the design as intended and be read as
  describing the code — and the closer it is to right, the less likely anyone
  checks. The defence is R-010.5 plus an actual test, not a tidier document.
* **"The tests cover the protocol."** Seven test modules imported
  `serial_handler`, which looks like coverage in any grep or dependency graph.
  They imported the dataclasses to build fixtures. The function under discussion
  was never called. Importing a module is not exercising it.

## Acceptance

1. `python3 rpi-brain/tests/run_tests.py` — 174 tests pass, of which 39 are the
   protocol contract (`tests/test_protocol.py`).
2. A serial capture shows one JSON object per line and nothing else after boot.
3. Removing a device from the I2C bus makes its sub-object disappear from
   telemetry rather than reporting zeros.
4. Every key `sendTelemetry()` writes appears in one of the `_*_FIELDS` tables
   in `serial_handler.py`. This is the R-010.5 check and it is worth re-running
   by eye after any firmware telemetry change:

       grep -oE 'doc\["[a-z_]+"\](\["[a-z_]+"\])*' esp32-s3-sense/src/main.cpp | sort -u

## Open

* The seven newly-parsed fields have only ever been fed **synthetic** frames.
  The parsing is covered, but the web UI's rendering of the calibration
  counters, GPS fix and pulse/hit totals has never been seen against a live owl.
  Check it on the next hardware run — the calibration display exists to make the
  figure-8 dance easier, which is the whole point of the change.
* Changes here need a matching change on the RPi side. The `_*_FIELDS` tables in
  `serial_handler.py` are the other half of this contract, and R-010.5 is the
  rule that keeps them honest.
