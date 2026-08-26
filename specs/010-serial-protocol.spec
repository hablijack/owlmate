# SPEC-010: NDJSON protocol between ESP32 and Raspberry Pi

Status: implemented
Verified: continuously — the RPi test suite exercises the parser
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

## Decisions

**NDJSON over a binary protocol.** ~200 bytes per telemetry frame is nothing on
this link, and being able to read the traffic directly has paid for itself many
times over during hardware debugging.

**Telemetry every `TELEMETRY_INTERVAL_MS` (500 ms).** Object shape:

    {"type":"telemetry","state":…,"uptime":…,"fw":…,
     "imu":{"pitch","roll","yaw","calibrated","cal":{"sys","gyro","accel","mag","restored"}},
     "gps":{"valid","latitude","longitude","altitude","satellites"},
     "vibration":{"detected","count","pulses"},
     "navigation":{"active","angle"},        // only while NAVIGATING
     "update":{"ssid","password","ip","url"}, // only while in UPDATE
     "servos":[5],
     "face":{"detected","x","y","w","h","confidence","gaze_x","gaze_y","total"},
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

**Expression names come from one table** in `Eyes.cpp` (`NAMES[]`), which backs
both directions. Mirrored on the RPi in `web_ui.py` `EXPRESSIONS` and
`config.yaml` `expressions:`; an unknown name renders as `neutral`, so a typo
looks like an eye that refuses to change.

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

## Falsified

* **"A boolean flag is enough to report an event."** Twice wrong, in two
  subsystems, for the same reason. See the cumulative-counter decision above.

## Acceptance

1. `python3 rpi-brain/tests/run_tests.py` — 104 tests pass.
2. A serial capture shows one JSON object per line and nothing else after boot.
3. Removing a device from the I2C bus makes its sub-object disappear from
   telemetry rather than reporting zeros.

## Open

* Nothing outstanding. Changes here need a matching change on the RPi side —
  `serial_handler.py`'s frozen dataclasses are the other half of this contract.
