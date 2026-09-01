# AGENTS.md

Guidance for AI coding agents working in this repository. `AGENTS.md` is the
vendor-neutral convention read by OpenCode, Codex, Cursor, Zed, Aider and
others; nothing in this repo is tied to one particular tool.

**Using a tool that looks for a different filename?** Point it here with a
symlink rather than copying the content — a copy will drift:

```bash
ln -s AGENTS.md GEMINI.md      # Gemini CLI
ln -s AGENTS.md CONVENTIONS.md # Aider
```

Those symlinks and any per-tool config directories are gitignored on purpose, so
the repository itself stays tool-agnostic. Create whichever one you need after
cloning.

## A note on language — the mixed languages are INTENDED

This file, `README.md`, `BACKLOG.md` and `specs/` are English: they are the
documents a reader who does not speak German has to be able to follow, and the
protocol contract between the two halves of the machine lives in them.

**Everything else may be German, and much of it is.** The owner is a native
speaker. Two categories are deliberately German:

* **The helper scripts under `esp32-s3-sense/tools/`** and the serial output of
  the diagnostic envs in `src/` — read live, at the bench, while holding a
  soldering iron.
* **Comments inside the firmware.** `include/config.h` is 113 of its 385 comment
  lines German, `lib/FaceDetector/FaceDetector.cpp` 61 of 81, `src/vision.cpp`
  27 of 31, `lib/Eyes/Eyes.cpp` 30 of 87 — roughly 350 lines across 11 files,
  measured 2026-08-31.

**This is a decision, not drift, and it is settled.** Confirmed with the owner
on 2026-08-31 after a refactoring pass proposed translating it. Do not "fix" it:

* **Do not translate existing comments**, individually or in a sweep. The German
  is concentrated in the newest and most densely annotated code — vision,
  FaceDetector, config — which is exactly where the hard-won measurements and
  the "do not re-derive this" warnings live. Those notes are read under time
  pressure, by the person who wrote them, in their first language.
* **A mistranslated measurement note is this repo's most expensive error class,
  and nothing can catch it.** `tools/check_docs.py` compares numbers quoted in
  docs against `config.h`; it cannot compare prose meaning. A sweep would put
  ~350 unverifiable edits on the notes that exist to stop repeat diagnoses.
* **Mixed language inside one file is fine.** `src/main.cpp` and
  `src/protocol.cpp` carry a handful of German lines in otherwise-English code.
  That is not a defect and needs no cleanup.

For **new** comments, write whichever language makes the note clearest — German
is expected in firmware and bench tooling. English is required only where a
non-German reader must follow it: the four steering docs, `specs/`, and anything
describing the NDJSON wire contract.

## What this is

A two-part robot owl. `esp32-s3-sense/` is Arduino/PlatformIO firmware for a Seeed XIAO ESP32-S3
Sense (LCD eyes, camera face detection, IMU, GPS, PCA9685 servos). `rpi-brain/` is a Python
supervisor that runs on a Raspberry Pi and talks to the board over USB CDC serial.

The **ESP32 owns the behavior state machine**. The RPi is *not* a second state machine — it logs
telemetry, monitors health, and sends policy (`sleep`/`wake`) plus *temporary* overrides
(`expression`/`gaze`, 3 s) and navigation aim angles. A duplicate state machine used to exist on
both sides and fought over expressions; don't reintroduce one.

## Commands

### Firmware (`esp32-s3-sense/`)

```bash
pio run -e xiao_esp32s3              # build the main firmware
pio run -e xiao_esp32s3 -t upload    # flash it over USB-C
pio run -e xiao_esp32s3 -t monitor   # serial monitor, 115200 baud
pio run -e dualtest -t upload        # flash a diagnostic env instead
pio run                              # build ALL 11 envs (the regression check)
```

**`-e` is not optional on `-t upload`, and leaving it off is silently
destructive.** There is no `[platformio] default_envs` in `platformio.ini`, so a
bare `pio run -t upload` does not flash "the default"; it flashes **all eleven
envs in file order**, and `[env:softimu]` is last — so the owl ends up running
the bit-banged IMU probe, printing `Lesefehler` every 500 ms and no NDJSON at
all. It looks exactly like dead firmware. Cost a flash cycle and a confused
diagnosis on 2026-08-31, when this block still said `pio run -t upload` and
described it as flashing the default env.

A bare `pio run` (no target) is fine and useful: it builds all 11, which is the
"builds clean on every env" check `BACKLOG.md` refers to.

Ten diagnostic envs, each selected with `-e` and each compiling exactly one source file via
`build_src_filter`. All guard their `setup()`/`loop()` behind a `#if defined(..._ACTIVE)` flag so
they compile to nothing in the main build — keep that pattern for new sources in `src/`, otherwise
their `setup()`/`loop()` collide with `main.cpp`'s.

| env | what it does |
|---|---|
| `dualtest` | drives both eyes with distinct colours/split patterns; regression test for the shared SPI bus |
| `i2ctest` | idle-level check, **bit-banged** scan (bypasses the ESP-IDF driver), Wire scan at 100/400 kHz, every candidate pin pair, bus-recovery pulses |
| `imuaxis` | prints the gravity vector in the sensor's raw frame (to derive the BNO055 axis remap) **and** live calibration counters (to guide the figure-8 dance) |
| `vibtest` | SW-420: pull test (connected? actively driven?), sink impedance, analog level, then an edge scan across **all 11 header pins** |
| `camtest` | camera: SCCB probe with the sensor ID read both 8- and 16-bit, driver init, frame capture, and a mean-brightness readout that catches "initialises but returns black" |
| `i2cmin` | the **smallest possible** I2C check: `Wire.begin()`, a probe, and the idle line levels — nothing else. `i2ctest` bit-bangs the same pins and fires recovery pulses, so it cannot arbitrate whether the bus itself is sound; this can. It also probes one address at a time, which is how the BNO055 was isolated |
| `bushigh` | drives SDA/SCL high and tests a **bare pin** four ways (pull-up, pull-down, driven high, driven low) against `D5` as a known-good control. Use with the wires desoldered to tell a dead pin from a dead wire |
| `softimu` | talks to the BNO055 over `lib/SoftI2C` (bit-banged), reporting **which protocol step** fails. Exists because the ESP32's I2C controller cannot be shared with a clock-stretching slave |
| `camsnap` | returns the **actual JPEG** over serial (Base64), one per vflip/hmirror combination, decoded by `tools/schnappschuss.py`. The only check that catches a camera aimed at the wrong place — brightness looks perfect on a ceiling. Added 2026-08-28 after exactly that cost a session |
| `powerprobe` | rolling 5 V-input reading from a genuinely minimal image (no PSRAM/LCD/camera/I2C/servos), to tell "rail dragged down by a peripheral" from "rail dead". Was a `#if POWER_PROBE` branch inside `main.cpp` until 2026-08-27, which linked the whole firmware and so measured nothing useful |

The main env sets `-DCORE_DEBUG_LEVEL=0`: this USB CDC port carries the NDJSON protocol the RPi
parses, so core log lines are protocol garbage. The diagnostic envs override `build_flags` and
therefore keep full logging.

**It only silences the *Arduino* core, not ESP-IDF components** — observed 2026-08-31 the first
time anyone watched the 4-tap OTA transition: `WiFi.softAP()` puts about forty `I (…) wifi:` /
`phy_init` / `esp_netif_lwip` lines straight onto the protocol port, and the RPi logs a parse
warning for each. Harmless but exactly the noise the flag exists to prevent; logged as `BACKLOG.md`
Step 6 item 6. If you need a genuinely quiet port, the lever is `CONFIG_LOG_DEFAULT_LEVEL_NONE`
in `sdkconfig.defaults`, not `CORE_DEBUG_LEVEL`.

`HARDWARE_CHECK` (in `include/config.h`, default `0`) replaces the normal boot with
`runHardwareCheck()`: probes both LCDs, PCA9685, GPS, BNO055, vibration and camera, prints one
`{"type":"hardware_check",...}` line (its `i2c_found` array lists every responding address), then
idles.

`FACE_DETECTION_ENABLED` is `1` for the main env: `[env:xiao_esp32s3]` replaces `build_flags`
wholesale and sets it there. `[arduino_base]` still sets `0`, which is what the diagnostic envs
inherit — so the value you get depends on the env, and reading only `arduino_base` is misleading.

### Flashing (USB-JTAG only — the one real host dependency)

The board exposes **only** a USB-Serial/JTAG interface; there is no second UART broken out, so esptool
cannot enter download mode via DTR/RTS. Use `--before usb-reset`, never `--before default-reset`.
That is a property of the hardware and applies on every host.

Everything else here varies by machine, so **discover it, do not hardcode it**:

```bash
# Serial port
ls /dev/cu.usbmodem*      # macOS
ls /dev/ttyACM*           # Linux / Raspberry Pi

# esptool + a python that has pyserial. With a PlatformIO install both live in
# its venv; otherwise use the system ones.
ls ~/.platformio/penv/bin/esptool ~/.platformio/penv/bin/python 2>/dev/null
```

The system `python3` on the firmware host typically does **not** have pyserial, while
`~/.platformio/penv/bin/python` does — that is why the scripts under `esp32-s3-sense/tools/` are run
with the latter. Those scripts find the port themselves (macOS and Linux patterns) and honour an
`OWL_PORT` override:

```bash
OWL_PORT=/dev/ttyACM0 ~/.platformio/penv/bin/python esp32-s3-sense/tools/kalibrieren.py
```

Opening the port resets the board into the app, so a capture always starts from a fresh boot.

**Never flash `firmware.factory.bin` at 0x0.** It spans past 0x9000 and erases the NVS partition,
taking the BNO055 calibration with it. Flash the pieces separately:

| offset | file |
|---|---|
| `0x0` | `bootloader.bin` |
| `0x8000` | `partitions.bin` (3 KB, fits its sector, does not reach NVS) |
| `0xf000` | `boot_app0.bin` |
| `0x20000` | `firmware.bin` |

### RPi brain (`rpi-brain/`)

```bash
python3 tests/run_tests.py             # whole suite (188 tests), unittest discovery
python3 tests/run_tests.py -v
PYTHONPATH=.:tests python3 -m unittest tests.test_navigation_geo          # one module
PYTHONPATH=.:tests python3 -m unittest tests.test_navigation.ClassName.test_name   # one test
python3 main.py [config.yaml]           # run the brain (default config.yaml)
sudo ./setup.sh [--non-interactive]     # full Pi provisioning (idempotent, reboots at the end)
```

The suite runs on a plain Mac with **no** Pi, mic, PortAudio or faster-whisper: `tests/stubs.py`
`install_stub_modules()` fakes `serial`/`flask`/`yaml`/`sounddevice`/`faster_whisper` *only when the
real module is not importable*. Every test file calls it before importing `brain.*`, so keep new
tests in that order (`install_stub_modules()` then `from brain... # noqa: E402`). numpy is the one
hard dependency. There is no linter configured.

`setup.sh` installs to `/opt/robot-owl/rpi-brain` with its own `.venv` under a `robotowl` system
user and enables `robot-owl-brain.service`; on the Pi, edit
`/opt/robot-owl/rpi-brain/config.yaml`, not the repo copy.

## Firmware architecture

Five source files, one job each — split out of a single ~930-line `main.cpp` on 2026-08-31, because
that file had to be read in full before any firmware change could be made safely:

| file | owns |
|---|---|
| `src/main.cpp` | **wiring only** — constructs the peripherals, `setup()`, `loop()` |
| `src/behavior.cpp` | the state machine, the supervisor overrides, OTA update mode |
| `src/protocol.cpp` | `handleCommand()` / `sendTelemetry()` / `poll()` — the NDJSON wire contract |
| `src/vision.cpp` | the face-detection task on **core 0**, and the one piece of cross-core state |
| `src/hardware_check.cpp` | `runHardwareCheck()`, built **only** under `-DHARDWARE_CHECK=1` |

**Core 1 runs everything with a frame deadline; core 0 runs the inference.** That is the whole
concurrency in this firmware — keep it that way. `faceResult` is the only state crossing the two,
and it crosses as a whole-struct copy under one spinlock.

`include/owl.h` is the only thing they share: the `State` enum, the peripheral externs, `faceResult`
and `loopHz`. **Anything only one module needs stays a `static` inside that module's `.cpp`** —
that is the rule that keeps the header from growing back into the old god-file. The override timers,
the nav target and the `WebServer` are private to `behavior.cpp` for exactly that reason; the
protocol reaches them only through the narrow surface in `behavior.h`.

Keep new subsystems out of `main.cpp`: give them a header and a source file, and let `main.cpp` own
only their construction and their call in `loop()`.

Peripherals are libraries under `lib/` (`GC9D01`, `Eyes`, `FaceDetector`) plus `src/Sensors.cpp` and
`src/ServoController.cpp` with headers in `include/`.

Eight states: `BOOT → IDLE → DETECTING → INTERACTING`, plus `SLEEPING`, `NAVIGATING`, `UPDATE`,
`ERROR`. Transitions are driven by local inputs only (vibration + on-device face detection +
timeouts from `config.h`). `NAVIGATING` is a *persistent* head override that expires on
`NAV_TIMEOUT_MS` without a refresh — unlike the 3 s expression/gaze overrides. `UPDATE` is entered by
4 vibration taps, brings up SoftAP `RobotOwl-Update` + an `HTTPUpdateServer` at `/update`, and
ignores every command except `heartbeat`.

### The shared SPI bus

Both eyes share SCK/MOSI/RST; each has its own CS and DC. `bringUpDisplays()` in `main.cpp` encodes
the required order: begin the bus once with **SS = -1**, `attachBus()` **both** panels (which parks
each CS high), `resetShared()` **once**, then `begin()` each.

**CS is driven in software, and must stay that way.** The long-standing "eyes don't work" bug was
that no code asserted CS at all: CS was passed to `SPIClass::begin()` as the peripheral's hardware
SS pin, which silently works for one panel and fails for two (the shared bus begins with `SS = -1`,
so neither panel was ever selected). Never reintroduce hardware SS here.

**Nothing can be read back.** The panel connector is 8 pins — VCC, GND, DIN, CLK, CS, DC, RST, BL —
with no SDO/MISO. An ID/status probe can only ever return `0x00`; a previous session read that as
"both panels are dead" and burned a day on an imaginary hardware fault. Don't write such a probe,
and don't trust one.

Drawing calls only touch the PSRAM framebuffer — `GC9D01::flush()` is what actually transmits, and
`Eyes::renderEye()` ends with it. `Eyes::render()` skips the whole redraw when nothing visible has
changed. (There was a public `markDirty()` for changes arriving by some other route; nothing ever
called it, so it was removed on 2026-08-27. Re-add it if a caller genuinely appears.)

**`setExpression()`/`setGaze()` mark the frame dirty only on an actual change**, and that is
load-bearing rather than a micro-optimisation. `updateState()` calls both every loop iteration,
almost always with an unchanged value, so an unconditional `_dirty = true` defeated the skip
entirely and forced a full redraw every single iteration — measured 2026-08-28 at 4.4 Hz, against
40+ Hz once the skip works. It is safe because `render()` re-checks the visible state itself
(`_expr`, `_sleeping`, iris position, `_blinkProgress`) and `_dirty` starts `true`, so the first
frame always draws.

**The flush was the loop's bottleneck, and `LCD_SPI_FREQ` was never the lever.** It is 16 MHz.
A full flush of both eyes took **149.9 ms**, and that number was *identical* at `LCD_SPI_FREQ`
16 MHz and 40 MHz — it is not clock-bound. Moving the framebuffer to internal RAM and dropping the
`writePixels()` byte swap each changed nothing either; what remained was per-chunk overhead in the
bulk transfer (~1.56 µs/byte). `config.h` claimed "~61 ms" until 2026-08-28; that was arithmetic
presented as a measurement.

**Fixed 2026-08-29 by a dirty-row flush** (`8b0ec83`), not by DMA: `GC9D01` tracks
`_dirtyTop`/`_dirtyBot`, `drawPixel()`/`fillScreen()` mark a row only when a pixel's *value* really
changes, and `flush()` skips the bus entirely when the range is empty — so a blink pushes the lid
band instead of 51,200 bytes per panel. If this ever regresses, do not reach for a higher
`LCD_SPI_FREQ`: that lever was measured and does nothing.

**Do not repeat the inference that this flush sets the detection rate.** It is the obvious next
thought and it is wrong — Step 4 found detection was limited by the face's size relative to the
frame and by a mis-set proposal-stage threshold, with loop timing innocent. See
`specs/009-face-detection.spec`.

### I2C and the IMU

**A clock-stretching slave can take the whole bus down, and with it two innocent devices.**
`I2C_TIMEOUT_MS` (250 ms, `config.h`) is the master's patience; the Arduino default of 50 ms is too
short. When it expires the controller abandons the transaction **mid-frame** and leaves both lines
low — so a silent BNO055 also costs you the GPS (`0x10`) and the servo driver (`0x40`). Set it after
`Wire.begin()`; `begin()` resets it. Measured 2026-08-28: at 50 ms the bus was dead after the
*second* access at every clock rate and with every library; at 1000 ms it survived 12 reads but a
missing device then cost a full second per read and stalled the main loop entirely.

**A missing IMU must never be fatal.** `Sensors::begin()` returns true with `_imuReady = false`, and
`main.cpp` logs a warning instead of entering `ERROR`. Until 2026-08-28 a silent BNO055 took the
whole owl down — eyes, camera, face detection and servos included — none of which depend on it.

Do not read a boot-time burst of `ESP_ERR_INVALID_STATE` (259) as a broken bus. In the IDF 5.x
`i2c_master` driver that code means **the slave NACKed**, and `Adafruit_BNO055::begin()` soft-resets
the chip then polls its ID while it reboots — ~18 NACKs over ~490 ms, every boot. A previous session
spent a day on this. `pio run -e i2ctest` settles the question in seconds.

Three traps in this area, all fixed, all easy to reintroduce:

- `Adafruit_GPS::available()` is hardcoded to `return 1` in I2C mode, so `while (GPS.available())`
  never terminates. Never write an unbounded read loop against a peripheral here — a mute device
  must not be able to stop the owl. **And bound the BUS TRANSFERS, not the calls:** `getGps()` had
  a 128-*read* budget that looked like a bound and was not one. A GPS with nothing to say sends
  `0x0A` padding, the library then refills on every single call, and all 128 reads become separate
  32-byte transfers — ~128 ms inside one loop iteration, twice a second, with the eyes frozen for
  it. It now also breaks after 3 consecutive empty reads. Third defect in that same loop; see
  `specs/005-i2c-bus.spec` Falsified.
- `getVector(VECTOR_EULER)` reads from `BNO055_EULER_H_LSB_ADDR`, so the register block is
  **Heading, Roll, Pitch**: `orientation.x` is yaw, `.y` is roll, `.z` is pitch. All three were once
  wired up wrong here.
- The IMU must stay in `OPERATION_MODE_NDOF`. IMUPLUS drops the magnetometer, which means no
  absolute heading and a `calibrated` flag that can never go true.
- `imu.calibrated` is `gyro >= 3 && mag >= 3`, deliberately **excluding** `sys` and `accel`. Those
  two are live confidence values — `sys` dips to 0 on any movement, `accel` falls back under
  sustained motion — and including them made the flag false in 0 of 23 frames on a fully calibrated
  sensor, permanently blocking `navigation.py:162`. The strict all-four test is only used for the
  one-shot decision to write offsets to flash.
- When calibrating, do the **figure-8 for `mag` before** the static poses for `accel`, never after:
  sustained motion resets the `accel` counter to 0.

The BNO055 is mounted bottom-PCB-up; `IMU_AXIS_REMAP_CONFIG`/`_SIGN` in `config.h` (placement P7)
correct that in hardware. Re-measure with `-e imuaxis` if it is ever remounted — a level owl should
read roll and pitch near 0. `AXIS_MAP` can only express the 24 axis-aligned orientations, so a
sensor glued in at an odd angle needs a software rotation instead.

`IMU_HEADING_OFFSET_DEG` is 75.4, measured on hardware: 70.8° of mounting rotation plus **+4.594° of
magnetic declination**. That second term is required, not cosmetic — the BNO055 reports a *magnetic*
heading while `geo.bearing_deg()` computes a *true geographic* bearing, so both sides must share one
north reference (`true = magnetic + declination`). `imu.yaw` is therefore a true geographic heading
of the beak, accurate to roughly ±5–10°. Re-check the declination if the owl changes region.

### The vibration sensor is a pulse source, not a level

The SW-420 on D2/GPIO3 emits **bursts of pulses** — measured 3252 edges in 28 s of tapping, median
gap 1 ms, 0 edges at rest. Never read it with `digitalRead()` and never debounce it by waiting for a
stable level: the original code did both (sampled at the 2 Hz telemetry rate, required 100 ms of
stability) and consequently reported `detected: true` forever on a motionless owl, pinning the state
machine in DETECTING.

An `IRAM_ATTR` ISR counts edges on `CHANGE`; `Sensors::updateVibration()` turns counts into state.
Counting both edge directions makes it polarity-agnostic, so the module's idle level does not matter.

Two rules that are easy to break again:
- `updateVibration()` must run **once per main-loop iteration** (~60 Hz), from `loop()`. At the 2 Hz
  telemetry rate the gap between evaluations exceeds `VIBRATION_BURST_GAP_MS`, so a burst spanning
  two calls counts as two taps (measured: 8 counts for 5 taps).
- `getVibration()` is a **pure getter**. It used to drain the pulse counter, and both callers
  (`updateState()` and `sendTelemetry()`) stole edges from one another.

`vibration.pulses` in telemetry is the raw ISR count since boot — the only way to distinguish "nobody
tapped" from "the sensor is dead". Constant at rest; rising while motionless means interference or
the module's pot is set too sensitive.

The module's DO is **open-collector**, so its measured impedance changes drastically with the
sensitivity pot: saturated (~1 kΩ, ~75 mV) when the pot has it permanently triggered, high-impedance
(the pin then just follows whichever internal pull is enabled) when it is off or near threshold.
Both were observed here and briefly misread as a failing solder joint. It is normal behaviour — check
what was changed between two measurements before positing a hardware fault.

### Face detection: esp-dl v3, and the camera is mounted upside down

**Ported into the firmware.** `[env:xiao_esp32s3]` builds as `framework = arduino, espidf` so it can
pull esp-dl as a managed IDF component (`src/idf_component.yml`).
The diagnostic envs deliberately stay on plain Arduino via the `[arduino_base]` section — putting them
in espidf mode would take their builds from ~1.5 s to minutes.

Two build-system traps worth knowing: **`sdkconfig.defaults` is only read when no `sdkconfig.<env>`
exists**, so delete the generated one after changing defaults or nothing happens; and
`CONFIG_LIBC_NEWLIB=y` is required, because picolibc lacks `fopencookie()` which
`espressif__cbor` needs.

**Never flash `firmware.factory.bin` at 0x0** — it spans past 0x9000 and wipes the NVS partition,
taking the BNO055 calibration with it. Flash bootloader/partitions/boot_app0/firmware separately.

**Inference runs on core 0, and it must stay there.** It used to be called
straight from `loop()`, which froze the eyes for the length of every cycle —
the long-unexplained "sometimes realtime, sometimes it lags a lot". Since
2026-08-31 `src/vision.cpp` owns a FreeRTOS task pinned to core 0 (the Arduino
loop has core 1, `CONFIG_ARDUINO_RUNNING_CORE=1`) and `loop()` only copies the
latest result. Measured before and after in one sitting with a face in frame:

| | before | after |
|---|---|---|
| `loop_hz` min / mean / max | 10.8 / 30.2 / 44.1 | 15.6 / **28.3** / 61.7 |
| samples below 15 Hz | **3 of 55** | **0 of 58** |
| new gaze target | every 382 ms | every **181 ms** |

**Judge this on the sequence, never on the mean — the mean went down.** What
changed is the spread: `11 29 31 35 32 42 26 41 33 17 …` became
`29 29 29 27 27 29 29 29 29 21 …`. A mean cannot show a stall, and here it hid
one for weeks.

`faceResult` is now cross-core state. It is copied whole under one
`portMUX_TYPE` spinlock — never field by field, and deliberately not a FreeRTOS
mutex, which could block the renderer.

**A detection cycle is 48-91 ms and 100 % compute; the camera is never waited
on.** `face.capture_ms` is 0 in every sample (`fb_count=2` always has a frame
ready) and `face.infer_ms` is 48 ms with no face, 48-91 ms (mean 66) with one —
a *successful* inference is the slow one, because more candidates reach the
refinement stage.

- **"A detection cycle is ~170-200 ms" was wrong, and how it was wrong matters
  more than the number.** Nobody timed the inference; it was divided out of
  `loop_hz`. But an iteration is `delay(16)` + `eyes.render()` + inference, and
  during tracking the render is the expensive part — so the detector was
  charged for the eyes. **A subtraction is not a measurement.** The `facelab`
  48 ms that this "falsified" had been right all along. See
  `specs/009-face-detection.spec` Falsified; it is the most instructive entry
  in that file.
- **Do not tune eye animation before checking `loop_hz` during tracking.** A
  whole session was spent adjusting a gaze filter that was itself being sampled
  at 6 fps. That block is now clear — at 28-29 Hz an interpolation has ~5
  frames between gaze targets — but check the rate first, every time.

**Measuring the owl changes it, in two ways that have both produced wrong
conclusions.** `pyserial` asserts DTR/RTS on open and **reboots the board**, so
every capture starts from a fresh boot; plain `cat /dev/cu.usbmodem*` does
**not** and lets you watch a running owl (verified 2026-08-31 — uptime kept
climbing across two connects). But **discard the first two telemetry frames of a
`cat` capture**: attaching to a port nobody has been reading collapses the first
`loop_hz` sample (measured `4 22 40 56 50 …`, with an immediate repeat reading
`47 47 47 53 …`). `trefferquote.py --datei` and `--folge` do this for you.

**That collapse was explained as a guess — "presumably the full USB CDC buffer
only clears once a reader attaches" — until it was measured on 2026-08-31, and
the guess was pointing at a real firmware defect.** The loop really was stopping,
for `loop_max_ms` **2256** and **4023** ms at a time, because `Serial.println()`
of a ~900-byte telemetry line blocked on a 256-byte TX ring whenever nobody
drained the port. Fixed the same day (see `specs/010`); the artifact is now a
mild one, but the general lesson is sharper than the specific fix: **an artifact
you have explained but not measured is an unread bug report.** And the owner has to be *in front of the camera*
for any detection number to mean anything: several readings of "0 hits" and
"31 %" were nobody standing there, or standing further away, not a fault.
`tools/trefferquote.py` reports hit rate, face size and the gaze-target interval
in one go, and `--live` prints a running view.

Seven things that cost time and will again if forgotten:

- **Look at a frame before you diagnose anything.** On 2026-08-28 the camera passed every
  `camtest` check — `0x3660`, driver init, 27 ms frames, brightness 133 dropping to 29 under a
  hand — while detection scored **0 hits in 31.5 s** against a face held still. It was aimed at the
  ceiling. A mis-aimed camera produces perfectly healthy numbers, so no brightness reading can catch
  it; the diagnosis went to thresholds and loop timing, and both were innocent. Run
  `pio run -e camsnap -t upload` and `tools/schnappschuss.py`, and look. After re-aiming, the same
  build scored 39 hits in 28.8 s.
- **`sensor->set_vflip(s, 1)` is mandatory.** The camera sits vertically flipped in the owl's head.
  Measured over all four orientations with a face in frame: normal **0** hits, vflip **57**,
  hmirror 2, 180° 10. These models only detect *upright* faces, so without vflip detection can never
  work — no threshold or lighting fixes it. (The BNO055 is likewise mounted inverted; the whole
  assembly is.) Re-confirmed `1` on 2026-08-28 after the ribbon extension was fitted — that remount
  did not change the vertical mounting, but the check is cheap and the failure is total.
- **RGB565**: use `DL_IMAGE_PIX_TYPE_RGB565BE` (62 hits vs 2 for LE).
- **Size in frame is the whole game.** `run()` rescales its input to the model's resolution, so what
  decides detection is the face's size **relative to the frame**, not in pixels. At 1 m a head is
  ~40 px of 320 and is *never* found; at 30 cm it is ~62 px and the rate jumps. `CAM_DETECT_CROP_DIV`
  feeds the detector the centre crop, which doubles relative size for free — measured 2026-08-29 at
  1 m: 0.0 % → 59.4 % of frames. **A bigger frame size does NOT help**: VGA gives the model the same
  picture at the same field of view. This was the real cause of "detection is too sporadic".
- **Do not trust `facelab` as a control without re-measuring it.** Its "near-every-frame detection"
  was recorded at bring-up under unstated conditions; re-measured in a normal room it scored **5 %**
  on the full frame, *worse* than the firmware. Two days of "it must be the integration" rested on
  that comparison. Measure the control in the same sitting as the thing it controls for.
- **The two detection stages need DIFFERENT thresholds.** Detection is a cascade: MSR proposes
  candidate regions, MNP refines and scores them. esp-dl defaults both to 0.5, and so did we — which
  throws faces away at the proposal stage before the refiner can ever look at them. `FACE_SCORE_THRESHOLD_MSR`
  is now **0.1** (permissive) while `FACE_SCORE_THRESHOLD` stays 0.5 (strict), matching Adafruit's
  working MEMENTO shoulder robot. Measured 2026-08-29 at 1 m: hit rate **31 % → 100 %**, hits
  1.84/s → 4.93/s, and **zero** false positives over 184 attempts in an empty room. Precision is
  protected because the reported score still comes from MNP.
- **"Thresholds are not the lever" was wrong, and cost time.** That claim was about the FINAL score,
  where 0.5 is indeed right and measured scores are 0.58–1.00. It was never true of the coarse stage.
- **`CONFIG_FREERTOS_HZ=1000`** is a hard Arduino-core requirement in espidf mode (IDF defaults to
  100 and the core's CMakeLists aborts).

esp-dl is *available* in the pioarduino platform — it is only absent from the **prebuilt Arduino
libs**, which is why Core 3.x looks like it dropped it. `espressif/human_face_detect` pulls it in.
The v3 API bears no resemblance to v1: `HumanFaceDetect` + `run(dl::image::img_t)` returning
`std::list<dl::detect::result_t>`, not `HumanFaceDetectMSR01`. Read the headers in
`facelab/managed_components/`, not the docs.

Octal PSRAM comes up **cleanly** in espidf mode via `CONFIG_SPIRAM_MODE_OCT` (not
`board_build.memory_type`), with far better diagnostics than Arduino mode gives.

### The camera is an OV3660

Verified on hardware: PID **0x3660** at SCCB address **0x3C**. README, `config.h` and
`FaceDetector.cpp` all claimed OV2640 until 2026-08-26. Nothing functional depended on it (pin map
and RGB565/QVGA are sensor-independent), but two things follow:

- **OV3660 registers are 16-bit addressed** — its ID is at `0x300A`/`0x300B`. Probing it the OV2640
  way (8-bit register `0x0A`) returns garbage, making a working sensor look absent.
- `ESP_ERR_NOT_SUPPORTED` + *"Detected camera not supported"* from esp32-camera is **also** what you
  get when nothing answers at all — `camera_probe()` returns it with `CAMERA_NONE`. Don't read it as
  "found but unknown model".

Pull-ups on SDA/SCL (GPIO40/39) are on the **Sense expansion board**, not the camera, so they say
nothing about whether a camera is attached. And GPIO39/40 have no ADC (ADC1 = GPIO1–10,
ADC2 = GPIO11–20); `analogReadMilliVolts()` there panics the HAL.

A separately bought OV3660 module for ESP32-CAM does **not** work in this socket (no SCCB ACK at
all) while the Seeed original initialises first try — so socket, FPC, B2B and driver are all proven
good if the camera ever goes quiet again.

### Pin numbers

`include/config.h` uses **real GPIO numbers, never the XIAO `D#` silkscreen labels** — they differ
(D0=1, D1=2, D2=3, D3=4, D4=5, D5=6, D6=43, D7=44, D8=7, D9=8, D10=9). A past session "fixed"
config.h to D-numbers and broke everything. `config.h` is authoritative and its LCD pins were
verified against the physical harness on 2026-08-26 by colouring each panel differently:
shared CLK=D4/GPIO5, DIN=D8/GPIO7, RST=D6/GPIO43; left CS=D7/GPIO44 DC=D10/GPIO9; right
CS=D9/GPIO8 DC=D3/GPIO4. (They had been swapped left-for-right.)

`README.md`'s hardware table and `WIRING.md` both agree with `config.h` as of 2026-08-27.
`WIRING.md` was the dangerous one until then: it had the two CS lines swapped, put the left DC on an
unused pin, and listed the vibration sensor on **D3 — the right eye's DC**. It is the document you
follow with a soldering iron, so check it against `config.h` whenever pins move.

## RPi brain architecture

`main.py` wires everything and then hands the foreground thread to
`SerialHandler.read_loop(supervisor.on_telemetry, idle_cb)`. Everything optional runs in daemon
threads (`Speech`, `WebUI`) and each is wrapped in try/except so a failure degrades to "running
without it" rather than exiting. The idle callback runs `supervisor.check_stale()` and
`check_auto_sleep()` on quiet iterations.

- `serial_handler.py` — pyserial + NDJSON. Frozen dataclasses (`Telemetry`, `IMUData`,
  `IMUCalibration`, `GPSData`, `FaceDetection`, `VibrationData`, `NavigationState`, `UpdateMode`)
  are the parsed telemetry shape; command senders (`set_expression`, `set_servo`, `set_gaze`, `nav`,
  `sleep`, `wake`, `blink`, `heartbeat`) are the outbound protocol. This is the contract with
  `handleCommand()`/`sendTelemetry()` in `src/protocol.cpp` — a change on one side needs the matching
  change on the other.
  **Adding a telemetry field is one row in a `_*_FIELDS` table plus one dataclass field**, nothing
  else. Do not hand-write `.get()` calls: that is how seven fields the firmware sends came to be
  silently discarded until 2026-08-27 (`imu.cal.*`, `vibration.pulses`, `face.total` — all of them
  diagnostics that exist to make an invisible failure visible). A bad or null field falls back to its
  default and logs at debug; this runs on the foreground thread, so it must never raise. Frames are
  frozen — derive with `dataclasses.replace`. See `specs/010-serial-protocol.spec` R-010.5.
- `supervisor.py` — owns `self.last` (latest frame), `LocationsStore`, the `Navigation` controller
  **and the audio amp**; logs state changes, cues state sounds, tracks `last_activity` for
  auto-sleep. It is the only module that touches `Audio`: play sounds via `supervisor.play_sound()`,
  never by lifting an `audio` handle off it. Ask it what state the owl is in via
  `supervisor.current_state()` rather than rebuilding `last_state or last.state` at the call site.
- `navigation.py` + `geo.py` + `locations.py` — all the "guide me home" math is here; the ESP32 only
  holds the angle it is told. `geo.aim_angle()`'s `sign` comes from `navigation.aim_sign` in
  config — **flip that config value** if the head points the wrong way on hardware (this is still
  unverified against real hardware). Places persist to `~/.config/robot-owl/locations.json`.
- `speech.py` — sounddevice capture → RMS VAD gate → faster-whisper (CTranslate2, **not**
  openai-whisper/torch) → `feed(transcript)`. In `feed()`, nav triggers are matched *before* the
  keyword clusters so "wie komme ich zum Zoo" isn't stolen by the `question` cluster. German
  keywords/clusters/reactions all live in `config.yaml`, not in code. Heavy imports are lazy so the
  brain starts on a machine with no mic.
- `web_ui.py` — Flask, port 8080, disabled by default, **no authentication** (LAN only). The page
  lives in `brain/templates/index.html` (~430 lines) and is read once at import; it is still
  rendered with `render_template_string`, deliberately *not* Flask's `render_template` — see
  `specs/012-rpi-brain.spec`. `/api/*` endpoints forward the same NDJSON commands the supervisor
  uses. `EXPRESSIONS` is generated, not written here. The map picker loads Leaflet **and its tiles**
  from two remote hosts and degrades to "type the lat/lon" offline; do not "fix" that by vendoring
  leaflet.js — the comment above the CDN tags explains why it would not help.
- `audio.py` — plays WAVs from `assets/sounds/` (or synthesizes tones) through `aplay` on the Pi's
  I2S MAX98357A amp. No-ops with a log line when the amp/I2S is absent. **All audio is on the Pi;
  the ESP32 has no audio pins.**

Every feature block in `config.yaml` (`supervisor.auto_sleep`, `web`, `navigation`, `speech`) is
`enabled: false` by default so existing deployments are unaffected — keep new features opt-in the
same way. `config.yaml`'s comments are the reference documentation for each key.

## Specs — start here for "why"

`specs/` holds the decision record: what this machine is meant to do, why each
choice was made, the measurements that justify it, and — most valuable — the
**hypotheses that turned out to be wrong**. Written retroactively on 2026-08-26,
after the subsystems worked; revised 2026-08-27 after a refactoring pass.

Read `specs/000-index.spec` first; it explains the format and indexes the rest.
Before re-diagnosing anything, read that subsystem's **Falsified** section.
Nearly all the time lost on this project went into believing a measurement that
could not mean what it appeared to mean, and each of those traps is recorded
where you will trip over it.

Requirements are numbered `R-NNN.n` and can be cited from code comments.
If a spec contradicts `esp32-s3-sense/include/config.h`, the header wins — fix
the spec.

**A retroactive spec can describe intent and read as description.** SPEC-010
listed three telemetry fields the RPi never parsed, and called its dataclasses
frozen while they were mutable — and the closer a document is to right, the less
likely anyone checks. Where a claim is backed by a test the spec now says so;
treat an unbacked claim as intent. Two specs are especially worth reading before
touching the RPi: `010-serial-protocol` (the wire contract, R-010.5 "every field
sent must be parsed") and `012-rpi-brain` (which module owns what, and why a
regression test is assumed broken until it has been seen to fail).

## Definition of done — the docs are part of the change

**A change is not finished until the docs that describe it are true again.** This
is not politeness; it is the single biggest source of lost time in this project.
Every doc defect found on 2026-08-27 was sitting in a file that declared itself
authoritative — including this one, which claimed the IMU calibration was present
in NVS while two other documents correctly said it had been erased. An agent
trusting that would have read navigation's correct refusal to aim as a bug.

So it is enforced, not requested:

```bash
python3 tools/check_docs.py        # or just run the RPi suite, which includes it
```

187 checks: specs indexed both ways, every spec keeps its skeleton, every
`Step N` reference resolves, every path named in a steering doc exists, numbers
quoted in docs match `config.h`, **every `**D<n>**` row in `WIRING.md` matches
the firmware pin map**, and quoted expression/test counts are real.

**That total moves on its own, so do not read a change in it as a regression.**
Checks are *generated* — one per file path cited in a steering doc, one per
constant quoted from `config.h` — so merely mentioning a new file in this file
adds one. What matters is that it reports 0 failures. (It said 172 until
2026-08-31, when nobody had re-run it against the prose in a while.)

Before finishing any change, ask:

| If you changed… | then update… |
|---|---|
| a constant in `config.h` | every doc quoting it — the checker names them |
| a pin | `WIRING.md` (both tables) — the checker diffs them against `config.h` |
| the telemetry format | the `_*_FIELDS` table **and** `specs/010`; R-010.5 says every field sent must be parsed |
| anything about timing | measure it **directly**; a number divided out of `loop_hz` is a subtraction, not a measurement, and that is how "~170-200 ms inference" got into three files |
| `NAMES[]` | run `rpi-brain/tools/gen_expressions.py`; never hand-edit the generated list |
| a design decision, or falsified a belief | the owning spec — **especially its `Falsified` section**, which is the most valuable part of `specs/` |
| what is left to do | `BACKLOG.md`, and only there |
| anything with a test count in a doc | the number, or phrase it as historical |

Two rules that matter more than the table:

1. **If a fact lives in two places, one of them is a bug.** Fix the duplication;
   do not update both. That is how the expression list came to exist in three
   versions with three different lengths.
2. **A guard you have not seen fail is not a guard.** Run a new check against the
   defect it claims to catch, and watch it fail, before keeping it. Three
   attempts at one small check were written on 2026-08-27 and two of them passed
   against the live bug (SPEC-012 R-012.5).

## Docs in this repo — four files, one job each

Restructured 2026-08-27 so that no fact has two homes. If you find the same claim in two places,
one of them is a bug; fix it rather than updating both.

| File | Owns | Does NOT contain |
|---|---|---|
| `BACKLOG.md` | **What to do next.** The only place open work lives. Top section is the dependency-ordered Step list; below it, per-subsystem history newest-first | rationale (that is `specs/`) |
| `specs/*.spec` | **Why.** Every decision, the measurement behind it, and the **Falsified** hypotheses. The decision record | task lists, status prose |
| `README.md` | **What this is,** for a human meeting the project: hardware tables, protocol reference, project layout, first-run checklist | open work, or duplicated rationale — it links to specs instead |
| `WIRING.md` | **The harness as physically built**, read at the bench with a soldering iron | anything not about wiring |

`config.h` is authoritative over all four for pins and timing. A doc that contradicts it is the bug.

**`BACKLOG.md`'s top section is the priority order, and the order is the point.** Steps 0–5 need
the owl, Step 6 needs only a laptop. Two dependencies there are easy to get wrong and expensive:
head-opening work (the camera extension) comes **before** any IMU calibration, because the BNO055
lives in the same head and disturbing it invalidates both the calibration and
`IMU_HEADING_OFFSET_DEG`; and the camera extension comes **before** any detection tuning, because
remounting can change `CAM_VFLIP`. Further down, `## Eyes / shared SPI bus — RESOLVED 2026-08-26`
records how that saga actually ended (four separate firmware bugs, no hardware fault) — worth reading
before touching the eyes or the driver.

## Eye rendering

Deliberately **two colours only**, black on white (`COLOR_BG`/`COLOR_INK` in
`include/common.h`). The panels are 160 px and sit behind small openings, so iris
gradients, specular highlights and grey lids turned to mush — that detail was removed on purpose,
don't reintroduce it.

**All 21 moods come from one parametric routine**, `Eyes::drawBlob()`, driven by a row of the
`SHAPES[]` table in `lib/Eyes/Eyes.cpp`. To retune a mood, edit numbers in that table — do not add
a bespoke drawing function. The base form is a superellipse (`roundness/10` is the exponent; 2.0 is
a plain ellipse, ~2.6 gives the reference sheet's soft-squared blob) plus four modifiers:

| field | effect | used by |
|---|---|---|
| `topSag` | pushes the top edge down in the middle | annoyed, skeptic, bored, unimpressed |
| `botRise` | pushes the bottom edge up in the middle → crescent | happy, glee, squint, focused |
| `slantIn` | drops the top edge toward the **inner** corner | angry, furious |
| `slantOut` | drops the top edge toward the **outer** corner | worried, sad_down |
| `yOff`, `asymH` | vertical shift; shorten the right eye only (deliberately lopsided) | blink high/low, skeptic |

The slant convention is emotional-standard: **angry lowers the inner brow, worried/sad lowers the
outer one**, and `drawBlob()` mirrors it per eye via its `mirrored` argument so the two eyes lean
toward each other rather than the same way.

`drawBlob()` renders **column by column**, computing a top and a bottom edge per x and filling
between them. That is what makes independent edge profiles — and therefore crescents and dome-down
shapes — fall out for free; a row-by-row rasteriser could not do it.

`UPDATE` (spinner), `ERROR` (cross) and `SLEEPING` (lash bar) are not eyes and bypass the table.
`SEARCHING`/`DETECTING` are aliases of `SUSPICIOUS`/`FOCUSED` so the state machine keeps working.

`EyeExpression`, `SHAPES[]` and `NAMES[]` are size-checked against `_COUNT` with `static_assert`, so
a mismatch is a build error rather than a silently wrong eye. `NAMES[]` is the single source for the
protocol strings — `Eyes::nameOf()`/`parseName()` back both telemetry's `eye` field and the
`expression` command. `main.cpp` used to keep a second hand-maintained list; it doesn't any more.
The RPi copy is **generated** from `NAMES[]` by `rpi-brain/tools/gen_expressions.py` into
`rpi-brain/brain/expressions.py` — never hand-edit it, and regenerate after adding a mood
(`cd rpi-brain && python3 tools/gen_expressions.py`). `tests/test_expressions.py` fails if the two
diverge. Two hand-kept mirrors existed until 2026-08-27 and had already drifted (26 / 23 / 24 names);
`config.yaml`'s `expressions:` block turned out to be read by nothing and is gone.

**Preview without flashing**: `python3 esp32-s3-sense/tools/preview_eyes.py && open /tmp/eyes.html`
parses `SHAPES[]` straight out of the C++ and re-implements the identical maths, so the contact sheet
cannot drift from the firmware.

## Current state — deliberately NOT here

**This file does not track status, and must not start again.** What is built,
what is broken and what to do next live in exactly one place each:

| question | file |
|---|---|
| What do I do next? What is still open? | `BACKLOG.md` — the dependency-ordered Step list at the top |
| Why is it like this? What did we already try and falsify? | `specs/` — start at `000-index.spec` |
| What is this project, and does subsystem X work? | `README.md`'s status table |

A section here duplicating any of that is a bug, not a convenience. It was one:
this file carried a "Current state" block until 2026-08-30 that claimed the IMU
calibration was present in NVS while `specs/006` and `BACKLOG.md` both correctly
said it had been erased — the exact defect that caused `tools/check_docs.py` to
be written, quoted in that program's own header. The block also still described
the `main.cpp` split as parked on a detection bug that had been resolved the day
before, and named the eye flush as the cap on the detection rate after that had
been falsified.

The pattern is why: a status paragraph is true when written and silently rots,
while the surrounding technical guidance stays valid — so the file keeps looking
trustworthy. Durable facts (pin maps, traps, measured constants, the reasoning
behind a design) belong here. Anything with a date, a checkbox or a "still
outstanding" belongs in `BACKLOG.md`.
