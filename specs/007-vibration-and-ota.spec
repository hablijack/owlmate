# SPEC-007: Vibration sensing and OTA entry

Status: implemented
Verified: 2026-08-31 — tap counting exact; OTA sequence end to end, re-run after the core-0 split
Depends on: 002 (dual-bank partitions)

## Intent

A tap on the owl should wake it and look around. Four taps should open a
firmware update mode, because the owl has to be updatable once it is glued into
a body with no accessible USB port.

## Requirements

* **R-007.1** A tap must be detected reliably; a motionless owl must never
  register one.
* **R-007.2** Four taps in quick succession enter `UPDATE`; one tap leaves it.
* **R-007.3** Update mode must work with no Orange Pi attached.
* **R-007.4** Sensitivity must be adjustable without a firmware change.

## Decisions

**Edges are counted in an interrupt; the pin level is never sampled.** An
`IRAM_ATTR` ISR counts on `CHANGE`, and `Sensors::updateVibration()` turns counts
into state. Counting both edge directions makes the whole thing
**polarity-agnostic** — whether the module idles HIGH or LOW stops mattering,
which retired a question that could not be settled from the module's datasheet.

**The evaluation runs once per main-loop iteration (~60 Hz), from `loop()`** —
not from a telemetry tick. See Falsified; this is load-bearing.

**`getVibration()` is a pure getter.** It used to harvest the pulse counter, and
both callers (`updateState()` at 60 Hz and `sendTelemetry()` at 2 Hz) stole edges
from each other.

**`vibration.pulses` — the raw ISR count since boot — is in telemetry on
purpose.** With a chattering sensor it is the only way to distinguish "nobody
tapped" from "the sensor is dead". It earned its place twice during debugging.
Constant at rest; rising while motionless means interference or the module's
potentiometer is set too sensitive.

**Sensitivity is set in hardware**, by the potentiometer on the SW-420 module.
The firmware only counts.

## Verified facts

The sensor emits **bursts of pulses**, not a level:

* 3252 edges in 28 s of tapping, all on GPIO3. Median gap between edges
  **1 ms**; 63 % of gaps ≤ 2 ms.
* One tap = a burst of ~66 edges over ~510 ms when polled at 5 kHz, and
  200–600 edges when counted by an interrupt that catches every one.
* At rest: **0 edges in 60 s**, `detected` false in 107/107 frames, state `idle`
  throughout — with the potentiometer tuned so a real tap triggers but lifting
  the owl does not.
* Tap counting: 5 taps → counter exactly 5. A confirming run of 3 taps → counter
  5 → 8, exactly as predicted.
* OTA sequence verified end to end: four taps entered `UPDATE`, the SoftAP
  `RobotOwl-Update` came up, a phone connected and loaded the `/update` page, and
  a single tap returned the owl to normal operation.
* **Re-verified 2026-08-31, after the `main.cpp` split and after the face
  inference moved to core 0** — the two refactors most able to break it, since
  WiFi and the new detection task share core 0. Four taps → `state=update`, eye
  `update`, `{"type":"update_mode","ssid":"RobotOwl-Update",…}` with
  `wifi driver task … core=0`; one tap → `{"type":"update_mode_end"}` and
  detection resuming 7 s later. `face.detected` stayed false for the whole
  session, which is `vision::setEnabled(false)` clearing the published result as
  designed (SPEC-001). This path had been carried as "unseen" through both
  refactors; it now is not.

**OTA timing** is a per-gap rule, not a total budget. Each tap must follow the
previous within `UPDATE_TAP_GAP_MS` (1500 ms), so four taps span at most 4.5 s.
There is also an implicit *lower* bound that appears in no constant: a new tap
only counts after `VIBRATION_BURST_GAP_MS` (250 ms) of quiet measured from the
previous burst's **last pulse**, and a tap rings for ~510 ms — so gaps below
~0.75 s merge two taps into one. Usable band ~0.75–1.5 s: **tap about once per
second.** Judged tight enough to be worth widening, but it was hit first try on
hardware, so 1500 ms stays.

## Falsified

* **"The sensor or its wiring is broken."** Telemetry reported
  `detected: true, count: 1` on a motionless owl. The `count: 1` was the tell —
  one event ever, then nothing, which is a pin stuck since boot rather than a
  sensor firing. The module was fine and so was the wiring.
* **"The firmware polarity is inverted."** Both the owner and this session
  believed it. A 20-second tap test produced **zero** edges, refuting it. The
  real fault was two structural ones: `getVibration()` did a single
  `digitalRead()` at the 2 Hz telemetry rate (sampling a ~1 kHz chattering signal
  at 2 Hz is a coin flip), and the debounce required the raw level to be
  **stable for 100 ms** — a real burst chatters for ~510 ms, so the condition
  never held and `detected` froze at whatever the very first sample happened to be.
* **"The DO line has a failing solder joint."** Its measured impedance changed
  from ~989 Ω / 75 mV to ~5.6 kΩ with mid-range voltages between two runs. That
  looked like an intermittent joint; it was the **potentiometer being turned
  between the measurements**. The LM393 on the module has an open-collector
  output, so the pot moves it between saturated (~1 kΩ) and high-impedance (the
  pin then simply follows whichever internal pull is enabled). A node that tracks
  the pull direction is an output switched off, not a bad connection. Check what
  changed between two measurements before inventing an independent cause.
* **Double counting**, caught in testing: 8 counts for 5 taps. The evaluation was
  still running at the telemetry rate, and the 535 ms interval between
  evaluations always exceeded `VIBRATION_BURST_GAP_MS`, so any burst spanning two
  calls counted twice. The gap test compares against the last *pulse*, which only
  advances if the evaluation runs often enough — hence the 60 Hz requirement.

## Acceptance

1. 60 s untouched: `vibration.pulses` does not increase, `detected` stays false,
   state stays `idle`.
2. N deliberate taps at ~1 s spacing: `vibration.count` increases by exactly N.
3. `tools/klopftest.py` — four taps enter update mode; one tap leaves it.

## Open

* **Entering update mode puts ~40 non-JSON lines on the protocol port.**
  Observed 2026-08-31 on the first capture of this transition. `WiFi.softAP()`
  emits ESP-IDF component logs (`I (…) wifi:`, `phy_init`, `esp_netif_lwip`),
  and `-DCORE_DEBUG_LEVEL=0` does not gate those — it silences only the Arduino
  core. The Orange Pi logs a parse warning per line. Nothing breaks; it violates the
  spirit of the quiet-port decision in SPEC-002/SPEC-010. Lever is
  `CONFIG_LOG_DEFAULT_LEVEL_NONE` in `sdkconfig.defaults`. BACKLOG Step 6
  item 6.
* **`cam_hal: EV-VSYNC-OVF`, once, as the AP comes up.** Expected rather than
  alarming: the camera keeps filling its ring buffer while the paused vision
  task stops calling `esp_camera_fb_get()`, so with `CAMERA_GRAB_WHEN_EMPTY` it
  overflows. It recovered by itself and detection resumed normally. Worth
  knowing so it is not diagnosed as a camera fault.

* Nothing. This subsystem is closed.
