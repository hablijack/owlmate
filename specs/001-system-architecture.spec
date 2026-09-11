# SPEC-001: System architecture and ownership

Status: implemented
Verified: continuously; the split has survived every subsystem added
Depends on: —

## Intent

A robot owl with expressive eyes, sensors and servos, which behaves sensibly on
its own and gains extra abilities (voice, navigation, a web UI) when an Orange
Pi is attached. The hard question is not what hardware to use, it is **which
side decides what the owl does**.

## Requirements

* **R-001.1** The owl must exhibit its core behaviour with no Orange Pi
  attached — power alone is enough.
* **R-001.2** There must be exactly one behaviour state machine in the system.
* **R-001.3** The Orange Pi may influence behaviour, but only as policy and as
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
— are both sensed on the ESP32. Putting the decision on the Orange Pi would mean a
serial round trip per frame and an owl that goes inert whenever the Pi reboots.

**The behaviour is the job; the telemetry is a by-product, and the by-product
yields.** The owl must never be hostage to the host's scheduling. This was an
unstated assumption until 2026-08-31, when it turned out to be false in the one
place it mattered: `sendTelemetry()` runs on core 1, in the same iteration that
draws the eyes, and the Arduino USB CDC layer let a single frame block that loop
for up to 2 s whenever nothing drained the port (measured `loop_max_ms` 2256 and
4023). The owl stood still because a *reader* was slow.

The rule that follows, and that any future outbound channel must obey: **a write
on the behaviour loop either fits without waiting or is dropped.** Never
truncated — a half-written NDJSON line costs the Orange Pi a parse error while a
dropped frame costs only a gap — and never silently: `tx_dropped` carries the
count, so "the owl went quiet" and "I was too slow to listen" stay
distinguishable. Telemetry is a 500 ms snapshot whose counters are cumulative,
so it is *designed* to survive gaps. See SPEC-010 for the mechanism.

This is a production requirement, not a bench nicety. The Orange Pi runs speech
recognition in the same process as the serial reader; a starved reader thread
there must not be able to freeze the owl's eyes.

**Leaving a state for IDLE is one named operation.** The three timeout paths out
of `DETECTING`, `INTERACTING` and `NAVIGATING` each repeated
`transitionTo(IDLE)` + `eyes.setGaze(0,0)` + `servos.setCenter()`, and did so in
two different orders — which reads as though the order were load-bearing
somewhere. It is not: `setGaze()` only marks the framebuffer dirty and
`setCenter()` only writes servo *targets*, which `ServoController::update()`
later acts on. They are now one `returnToIdle()` private to `behavior.cpp`.

Deliberately **not** used by `setNavTarget(active=false)`: that path recentres
only `CH_HEAD` and leaves the ears and wings wherever the supervisor put them,
whereas `setCenter()` centres all five. The two look interchangeable and are not.

**Sensor structs are value-initialised, never positionally.** `Sensors::getImu()`
opened with `ImuData data = {0, 0, 0, false, 0, 0, 0, 0, false}` — a nine-element
list that had to be kept in `ImuData`'s field order by hand. It was correct as
written and would have gone silently wrong the first time anyone inserted a field
into the struct, shifting every value after it. `ImuData data{}` is equivalent
today and cannot rot (likewise `GpsData`, `VibrationData`).

**The Orange Pi is a supervisor, not a second brain.** It logs telemetry, watches for
staleness, and sends: policy (`sleep`/`wake`), *temporary* overrides
(`expression`, `gaze` — 3 s, `EXPRESSION_OVERRIDE_MS`), and navigation aim
angles.

Reason for the time limit: an override that never expires is a way for a crashed
supervisor to freeze the owl's face. `NAVIGATING` is the one persistent override
and it carries its own escape hatch — `NAV_TIMEOUT_MS` (5 s) without a refresh
returns the head to centre.

**The firmware is five files, and `main.cpp` is wiring.** Split 2026-08-31 from
a single ~930-line `main.cpp` that held the state machine, the NDJSON protocol,
the hardware check and the boot wiring together: `behavior.cpp`, `protocol.cpp`,
`hardware_check.cpp`, and a `main.cpp` that only constructs, boots and loops.
`vision.cpp` joined them later the same day when the face-detection inference
moved to core 0. `include/owl.h` carries the little they share (the `State`
enum, the peripheral externs, `faceResult`, `loopHz`).

**The owl uses both cores, and the split is by deadline, not by subsystem.**
Core 1 runs the Arduino loop (`CONFIG_ARDUINO_RUNNING_CORE=1`): protocol, state
machine, eyes, servos, telemetry — everything with a frame deadline. Core 0 runs
one task, `vision.cpp`, doing camera capture and esp-dl inference, which has no
frame deadline and only needs to finish eventually. That is the whole
concurrency in this firmware, and it should stay that way.

`faceResult` is the only state that crosses the two, and it crosses under one
`portMUX_TYPE` as a whole-struct copy — never field by field. A spinlock and not
a FreeRTOS mutex, deliberately: a mutex can block the renderer, and the
renderer's frame rate is the entire point of the exercise. `loop()` takes its
copy once per iteration, before `behavior::update()`, so the state machine and
`sendTelemetry()` cannot disagree about what the owl is currently looking at.

Reason: R-001.2 says there is exactly one state machine, and this file was where
you had to go to check that — but reading it meant reading the protocol parser
and a 150-line diagnostic as well. The rule that keeps the split honest is that
anything only one module needs stays a `static` in that module's `.cpp`. The
override timers, the nav target and the `WebServer` are private to
`behavior.cpp`; `protocol.cpp` reaches them only through `behavior.h`.

Two duplications went with it: the ack serialize-and-println block, which had
been copy-pasted 8 times inside `handleCommand()` and gained a copy with every
command added, is now `sendJson()`/`sendAck()`; and the two I2C address-scan
loops are one `i2cScan()` template taking the reporting as a callback. The two
scans had *genuinely different bodies* — one builds the `i2c_found` JSON array
and sets device flags, the other prints to serial — so this is a shared walk,
not a deleted copy. Do not "fix" it by making one caller print what the other
needs.

`hardware_check.cpp` is now built only under `-DHARDWARE_CHECK=1`, matching the
one-diagnostic-one-file pattern the ten diagnostic envs already follow. It was
the last diagnostic linked into every production image; removing it cut 3,844
bytes of flash and 168 bytes of RAM.

Flashed and verified on the owl the same day, including a **control measured in
the same sitting**: the pre-refactor firmware was rebuilt, flashed and measured
back to back against the refactored one. Cadence 581 ms mean before, 549 ms
after; loop rate 31.6 Hz mean before, 33.0 Hz after. The split costs nothing.
That control mattered — a 12 s sample of the refactored build alone read 604 ms
against the ~535 ms in these specs and looked like a regression, and a 20 s
sample of the same build read 549 ms. A short cadence sample is not a signal.

**All audio lives on the Orange Pi.** The ESP32 has no audio pins in this build; the
MAX98357A amp hangs off the Pi's I2S bus.

**Optional features are opt-in and isolated.** Every feature block in
`orangepi-brain/config.yaml` (`supervisor.auto_sleep`, `web`, `navigation`, `speech`)
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
  one on each side. They fought over expressions and gaze — the Orange Pi would set a
  mood, the ESP32 would immediately overwrite it. Do not reintroduce this. It is
  the reason overrides are explicitly *temporary* rather than authoritative.

## Acceptance

1. Unplug the Pi. The owl still boots, blinks, reacts to taps and to faces.
2. Kill the Pi mid-navigation. The head returns to centre within
   `NAV_TIMEOUT_MS`.
3. Send an `expression` override and stop. The owl's own state expression
   returns within `EXPRESSION_OVERRIDE_MS`.

## Open

* `navigation.aim_sign` in the Orange Pi config has never been verified against
  hardware. If the head turns the wrong way on the first navigation test, that
  single value is the fix.
