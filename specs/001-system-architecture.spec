# SPEC-001: System architecture and ownership

Status: implemented
Verified: continuously; the split has survived every subsystem added
Depends on: —

## Intent

A robot owl with expressive eyes, sensors and servos, which behaves sensibly on
its own and gains extra abilities (voice, navigation, a web UI) when a Raspberry
Pi is attached. The hard question is not what hardware to use, it is **which
side decides what the owl does**.

## Requirements

* **R-001.1** The owl must exhibit its core behaviour with no Raspberry Pi
  attached — power alone is enough.
* **R-001.2** There must be exactly one behaviour state machine in the system.
* **R-001.3** The RPi may influence behaviour, but only as policy and as
  temporary overrides; it must not be able to leave the owl in a stuck state by
  crashing or being unplugged.
* **R-001.4** Every subsystem must degrade to "running without it" rather than
  refusing to start.

## Decisions

**The ESP32 owns the behaviour state machine.** Eight states: `BOOT → IDLE →
DETECTING → INTERACTING`, plus `SLEEPING`, `NAVIGATING`, `UPDATE`, `ERROR`.
Transitions are driven by *local* inputs only: the vibration sensor, on-device
face detection, and timeouts from `config.h`.

Reason: the two inputs that drive the interesting transitions — a face and a tap
— are both sensed on the ESP32. Putting the decision on the RPi would mean a
serial round trip per frame and an owl that goes inert whenever the Pi reboots.

**The RPi is a supervisor, not a second brain.** It logs telemetry, watches for
staleness, and sends: policy (`sleep`/`wake`), *temporary* overrides
(`expression`, `gaze` — 3 s, `EXPRESSION_OVERRIDE_MS`), and navigation aim
angles.

Reason for the time limit: an override that never expires is a way for a crashed
supervisor to freeze the owl's face. `NAVIGATING` is the one persistent override
and it carries its own escape hatch — `NAV_TIMEOUT_MS` (5 s) without a refresh
returns the head to centre.

**All audio lives on the RPi.** The ESP32 has no audio pins in this build; the
MAX98357A amp hangs off the Pi's I2S bus.

**Optional features are opt-in and isolated.** Every feature block in
`rpi-brain/config.yaml` (`supervisor.auto_sleep`, `web`, `navigation`, `speech`)
defaults to `enabled: false`, and each optional thread is wrapped so a failure
logs and continues.

## Verified facts

* Standalone boot works: `setup()` waits at most `USB_WAIT_TIMEOUT_MS` (5 s) for
  a USB host, then continues. Confirmed by entering OTA update mode with no Pi
  attached.
* Telemetry cadence measured at ~535 ms against a `TELEMETRY_INTERVAL_MS` of
  500 ms; the extra ~35 ms is I2C wait time inside `sendTelemetry()`.

## Falsified

* **"Both sides can run a state machine and cooperate."** An earlier design had
  one on each side. They fought over expressions and gaze — the RPi would set a
  mood, the ESP32 would immediately overwrite it. Do not reintroduce this. It is
  the reason overrides are explicitly *temporary* rather than authoritative.

## Acceptance

1. Unplug the Pi. The owl still boots, blinks, reacts to taps and to faces.
2. Kill the Pi mid-navigation. The head returns to centre within
   `NAV_TIMEOUT_MS`.
3. Send an `expression` override and stop. The owl's own state expression
   returns within `EXPRESSION_OVERRIDE_MS`.

## Open

* `navigation.aim_sign` in the RPi config has never been verified against
  hardware. If the head turns the wrong way on the first navigation test, that
  single value is the fix.
