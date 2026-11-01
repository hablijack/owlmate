# SPEC-007: Vibration sensing and OTA entry

Status: implemented
Verified: 2026-08-31 — tap counting exact; OTA sequence end to end, re-run after the core-0 split
Depends on: 002 (dual-bank partitions)

## Intent

Vibration lets the owl look around, but must never control firmware updates.
Walking produced accidental OTA entries with the former four-tap gesture.
Only explicit Orange Pi software commands may enter or leave update mode;
the Web UI exposes these commands without needing access to the XIAO USB port.

## Requirements

* **R-007.1** A tap must be detected reliably; a motionless owl must never
  register one.
* **R-007.2** `{"type":"update_mode","active":true}` enters `UPDATE`;
  `active:false` leaves it. Vibration never enters or leaves this state.
* **R-007.3** The Orange Pi Python API and Web UI must offer explicit entry and
  exit. USB serial remains connected while the SoftAP is active. Repeating a
  command is a no-op; `active` must be a JSON boolean.
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

These OTA measurements describe the retired gesture, not the new trigger.
The SoftAP/upload path is unchanged; entry and exit now come from USB commands.
The Web UI POST endpoint is `/api/update-mode` with a boolean `active`; its
`ok` reports serial send success, not a firmware acknowledgement. Live telemetry
confirms the actual state. Entry requires browser confirmation.

## Falsified

* **"The owl wakes itself from IDLE" (2026-11-01).** The SW-420 sits in the same body as the
  servos, so the idle animation (glance, ear twitch, wing ruffle) shakes the sensor, the burst
  moves IDLE to DETECTING, and the owl has woken itself with its own idle life: an endless loop,
  found by the owner, not by a test. Fix: while any driven servo is still on its way to its
  target, and `VIBRATION_SELF_MOTION_TAIL_MS` (600) after, `Sensors::updateVibration()` discards
  the edges (`ServoController::moving()` -> `Sensors::setSelfMotion()` in `loop()`).
  `vibration.pulses` still counts raw, so a sensor that fires with the servos at rest remains
  visible. Cost: a knock during the owl's own movement is not noticed. Verify on the owl: leave it
  in IDLE for several minutes with the idle animation running.

* **"A rapid tap sequence distinguishes maintenance intent from movement."**
  Walking accidentally entered OTA mode. The gesture and its sequence counters
  were removed rather than retuned. Exit is explicit too, so movement cannot
  interrupt an update session.

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
3. Walk and tap repeatedly: no OTA entry. Press Enter update mode in the Web UI:
   telemetry becomes `update`, SoftAP and `/update` are available. Further taps
   do not exit; Exit update mode returns to normal operation. Repeat entry/exit
   commands to confirm idempotence. Upload and confirm reboot into the new image.
4. `tests/test_update_mode.py` covers the Python command, Web UI forwarding and
   source guards against reintroducing vibration-driven OTA.

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
