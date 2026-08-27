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

A note on language: this file, `README.md` and `BACKLOG.md` are English. The
helper scripts under `esp32-s3-sense/tools/` talk to the user in **German** —
that is deliberate, the owner is a native speaker and those scripts are read
live while working on hardware. Keep new user-facing tooling German and new
code/docs English.

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
pio run                      # build the default env (xiao_esp32s3)
pio run -t upload            # flash over USB-C
pio run -t monitor           # serial monitor, 115200 baud
pio run -e dualtest -t upload  # flash a diagnostic env instead
```

Six diagnostic envs, each selected with `-e` and each compiling exactly one source file via
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
| `powerprobe` | rolling 5 V-input reading from a genuinely minimal image (no PSRAM/LCD/camera/I2C/servos), to tell "rail dragged down by a peripheral" from "rail dead". Was a `#if POWER_PROBE` branch inside `main.cpp` until 2026-08-27, which linked the whole firmware and so measured nothing useful |

The main env sets `-DCORE_DEBUG_LEVEL=0`: this USB CDC port carries the NDJSON protocol the RPi
parses, so core log lines are protocol garbage. The diagnostic envs override `build_flags` and
therefore keep full logging.

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
python3 tests/run_tests.py             # whole suite (104 tests), unittest discovery
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

`src/main.cpp` (~900 lines) is the whole application: state enum, `handleCommand()` NDJSON parser,
`sendTelemetry()`, `updateState()`, OTA update mode, `runHardwareCheck()`, `setup()`, `loop()`.
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
changed; `setExpression()`/`setGaze()` mark the frame dirty themselves. (There was a public
`markDirty()` for changes arriving by some other route; nothing ever called it, so it was removed on
2026-08-27. Re-add it if a caller genuinely appears.)

`LCD_SPI_FREQ` is 16 MHz — ~61 ms per full 160×160 frame per eye. It was 6 MHz on the theory that
27 MHz was "too fast for jumper wiring"; that was a misdiagnosis of the chip-select bug, so the
clock is now purely a speed/margin trade-off and can likely go higher.

### I2C and the IMU

Do not read a boot-time burst of `ESP_ERR_INVALID_STATE` (259) as a broken bus. In the IDF 5.x
`i2c_master` driver that code means **the slave NACKed**, and `Adafruit_BNO055::begin()` soft-resets
the chip then polls its ID while it reboots — ~18 NACKs over ~490 ms, every boot. A previous session
spent a day on this. `pio run -e i2ctest` settles the question in seconds.

Three traps in this area, all fixed, all easy to reintroduce:

- `Adafruit_GPS::available()` is hardcoded to `return 1` in I2C mode, so `while (GPS.available())`
  never terminates. `Sensors::getGps()` uses a fixed byte budget instead. Never write an unbounded
  read loop against a peripheral here — a mute device must not be able to stop the owl.
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
pull esp-dl as a managed IDF component (`src/idf_component.yml`); 48 ms inference, ~21 fps.
The diagnostic envs deliberately stay on plain Arduino via the `[arduino_base]` section — putting them
in espidf mode would take their builds from ~1.5 s to minutes.

Two build-system traps worth knowing: **`sdkconfig.defaults` is only read when no `sdkconfig.<env>`
exists**, so delete the generated one after changing defaults or nothing happens; and
`CONFIG_LIBC_NEWLIB=y` is required, because picolibc lacks `fopencookie()` which
`espressif__cbor` needs.

**Never flash `firmware.factory.bin` at 0x0** — it spans past 0x9000 and wipes the NVS partition,
taking the BNO055 calibration with it. Flash bootloader/partitions/boot_app0/firmware separately.

Four things that cost time and will again if forgotten:

- **`sensor->set_vflip(s, 1)` is mandatory.** The camera sits vertically flipped in the owl's head.
  Measured over all four orientations with a face in frame: normal **0** hits, vflip **57**,
  hmirror 2, 180° 10. These models only detect *upright* faces, so without vflip detection can never
  work — no threshold or lighting fixes it. (The BNO055 is likewise mounted inverted; the whole
  assembly is.)
- **RGB565**: use `DL_IMAGE_PIX_TYPE_RGB565BE` (62 hits vs 2 for LE).
- **Thresholds are not the problem.** Default 0.5 is right; measured scores are 0.58–1.00.
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

- `serial_handler.py` — pyserial + NDJSON. Frozen dataclasses (`Telemetry`, `IMUData`, `GPSData`,
  `FaceDetection`, `VibrationData`, `NavigationState`, `UpdateMode`) are the parsed telemetry shape;
  command senders (`set_expression`, `set_servo`, `set_gaze`, `nav`, `wake`, `blink`, `heartbeat`)
  are the outbound protocol. This is the contract with `handleCommand()`/`sendTelemetry()` in
  `main.cpp` — a change on one side needs the matching change on the other.
- `supervisor.py` — owns `self.last` (latest frame), `LocationsStore` and the `Navigation`
  controller; logs state changes, cues state sounds, tracks `last_activity` for auto-sleep.
- `navigation.py` + `geo.py` + `locations.py` — all the "guide me home" math is here; the ESP32 only
  holds the angle it is told. `geo.aim_angle()`'s `sign` comes from `navigation.aim_sign` in
  config — **flip that config value** if the head points the wrong way on hardware (this is still
  unverified against real hardware). Places persist to `~/.config/robot-owl/locations.json`.
- `speech.py` — sounddevice capture → RMS VAD gate → faster-whisper (CTranslate2, **not**
  openai-whisper/torch) → `feed(transcript)`. In `feed()`, nav triggers are matched *before* the
  keyword clusters so "wie komme ich zum Zoo" isn't stolen by the `question` cluster. German
  keywords/clusters/reactions all live in `config.yaml`, not in code. Heavy imports are lazy so the
  brain starts on a machine with no mic.
- `web_ui.py` — Flask, port 8080, disabled by default, **no authentication** (LAN only). The HTML is
  a `render_template_string` literal in the same file. `/api/*` endpoints forward the same NDJSON
  commands the supervisor uses.
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
after the subsystems worked.

Read `specs/000-index.spec` first; it explains the format and indexes the rest.
Before re-diagnosing anything, read that subsystem's **Falsified** section.
Nearly all the time lost on this project went into believing a measurement that
could not mean what it appeared to mean, and each of those traps is recorded
where you will trip over it.

Requirements are numbered `R-NNN.n` and can be cited from code comments.
If a spec contradicts `esp32-s3-sense/include/config.h`, the header wins — fix
the spec.

## Docs in this repo

`README.md` (hardware tables, protocol reference, numbered "Software Decisions" with rationale,
first-run checklist), `WIRING.md` (solder links, the SD-MODE-to-3.3V gotcha, the XIAO→Pi native USB
link), `BACKLOG.md` (open work per subsystem + a "Recently completed" section for session context),
`NAVIGATION_PLAN.md` and `SPEECH_RECOGNITION_PLAN.md` (full designs; the navigation plan's §10 and
§13 are referenced from code comments).

`BACKLOG.md`'s top section records how the shared-SPI eye bug was actually resolved (four separate
firmware bugs, no hardware fault) — worth reading before touching the eyes or the driver.

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
The RPi mirrors of this list live in `web_ui.py` `EXPRESSIONS` and `config.yaml` `expressions:`.

**Preview without flashing**: `python3 esp32-s3-sense/tools/preview_eyes.py && open /tmp/eyes.html`
parses `SHAPES[]` straight out of the C++ and re-implements the identical maths, so the contact sheet
cannot drift from the firmware.

## Current state

Both eyes work on the shared bus and render independently. The wiring as physically built —
including cable colours — is in `WIRING.md`, corrected against `config.h` on 2026-08-27.
`config.h` remains authoritative if the two ever disagree.

All three I2C devices work (verified: live BNO055 Euler angles, 43 NMEA sentences in 6 s from the
PA1010D, a PCA9685 servo ramp). Telemetry runs at ~535 ms.

The BNO055 was calibrated on hardware 2026-08-26 and the offsets persist in NVS, so a boot logs
`IMU: restored calibration offsets from flash` and comes up with a live heading. Re-run
`esp32-s3-sense/tools/kalibrieren.py` (guided, German) only if the sensor is replaced/remounted or
NVS is erased.

`IMU_HEADING_OFFSET_DEG` is 75.4, measured on hardware: 70.8° of mounting rotation plus **+4.594° of
magnetic declination**. That second term is required, not cosmetic — the BNO055 reports a *magnetic*
heading while `geo.bearing_deg()` computes a *true geographic* bearing, so both sides must share one
north reference (`true = magnetic + declination`). `imu.yaw` is therefore a true geographic heading
of the beak, accurate to roughly ±5–10°. Re-check the declination if the owl changes region.

Outstanding for navigation: `navigation.aim_sign` in the RPi `config.yaml` is still unverified
against hardware — flip it if the head turns the wrong way.
