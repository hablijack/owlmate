# Robot Owl — Embedded Firmware & Brain

A robotic owl companion with expressive LCD eyes, face detection, IMU, GPS, and servo-controlled ears/head/wings. The ESP32-S3 Sense owns the behavior state machine and runs on-device sensor fusion and eye rendering, communicating NDJSON telemetry to an Orange Pi "supervisor" for logging,
health monitoring, and policy.

---

## Hardware

### Main Board
- **Seeed Studio XIAO ESP32-S3 Sense** — ESP32-S3 dual-core @ 240 MHz, 8 MB Flash, 8 MB PSRAM (OPI), USB-CDC on boot, 24-pin FPC camera connector on the Sense expansion (fitted with an **OV3660**, verified on hardware — the docs previously said OV3660 throughout)

### Peripherals
| Component | Model | Interface | Address / Pins | Function |
|---|---|---|---|---|
| **LCD Eyes** | Waveshare 0.71" round (×2) | SPI (shared bus) | shared SCK=5 (D4), MOSI=7 (D8), RST=43 (D6); left CS=44 (D7) DC=9 (D10); right CS=8 (D9) DC=4 (D3) | Expressive eyes with GC9D01 driver. Verified on hardware 2026-08-26 |
| **Camera** | OV3660 (on-board, Sense expansion) | SCCB/I2C + parallel | XCLK=10, SIOD=40, SIOC=39, D0-D7 on GPIO 15-18,38-48, VSYNC=38, HREF=47, PCLK=13 | Face detection input |
| **IMU** | Adafruit **LSM303AGR** | I2C | SDA=1, SCL=2, addr **0x19** (accel) + **0x1E** (magnetometer) | Orientation (pitch/roll/yaw). 6-DOF: accelerometer + magnetometer, **no gyroscope and no on-chip fusion** — the firmware computes the attitude and the tilt-compensated heading itself (`lib/OwlImu`). Replaced a BNO055 on 2026-09-01. **The black `LSM303AGR` board, not the blue `LSM303DLHC`** — same addresses, incompatible magnetometer registers, see `specs/006` |
| **GPS** | Adafruit PA1010D | I2C | SDA=1, SCL=2, addr 0x10 | Position/satellites |
| **Servo Driver** | PCA9685 | I2C | SDA=1, SCL=2, addr 0x40 | 5-channel PWM for servos |
| **Vibration** | SW420 | Digital GPIO | GPIO3 (D2), input | Wake-on-vibration trigger |
| **Audio Amp** | MAX98357A (Adafruit) | I2S — **on the Orange Pi**, not the ESP32 | BCLK=PB5, LRCLK=PB6, DIN=PB7 (Orange Pi 40-pin header) | Sound effects / voice |

### Servo Channels (PCA9685)

Throughout this document and in `config.h`, `left`/`right` mean the **owl's own**
left and right — standing in front of it, its left side is the one on *your*
right. This applies to the eyes as well as the servos.

| Channel | Function | Range |
|---|---|---|
| CH15 | Left ear | -45° to +45° |
| CH14 | Right ear | -45° to +45° |
| CH2 | Head tilt | -45° to +45° |
| CH3 | Left wing | -45° to +45° |
| CH4 | Right wing | -45° to +45° |

> ⚠️ The ear channels are **15 and 14, not 0 and 1** — moved 2026-08-27 because
> the servo cables were too short to reach the low channels. The numbering is
> therefore **sparse**, which matters in code: per-channel arrays and bounds
> checks must be sized by the PCA9685's 16 channels (`PCA9685_NUM_CHANNELS`),
> not by the 5 servos fitted (`NUM_SERVOS`). Those used to be one constant, and
> with it `setAngle(15, …)` would have failed a `channel >= 5` guard and returned
> silently — the ear simply never moving, with no error anywhere.
>
> The telemetry `servos` array keeps its documented order regardless of physical
> channel: `[left_ear, right_ear, head, left_wing, right_wing]`.

### I2C Bus (GPIO1/2)
All I2C devices share the same bus: LSM303AGR @ 0x19 + 0x1E, PA1010D @ 0x10, PCA9685 @ 0x40. Clock: 400 kHz.

### Power
- LCD backlights tied directly to 3.3V (always on)
- Servos powered separately (not from ESP32 3.3V rail)
- MAX98357A amp powered from the Orange Pi 5V rail; speaker is 4Ω or 8Ω

### Audio (Orange Pi-side)
Sound lives **entirely on the Orange Pi** — the MAX98357A amp **and** the
ICS43434 microphone share the Orange Pi Zero 3W's I2S0 bus on one 48 kHz ALSA
card (`owl`), so the owl can both talk *and* hear, and the ESP32 has no audio
pins. The A733's I2S controller needs a small custom kernel driver (`owl_i2s` —
a one-bit data-delay fix that is not expressible in a DT overlay) rather than a
DT overlay; `setup.sh` builds the driver, installs the `owl-i2s-overlay` and the
`asound.conf`, and reboots. The brain synthesizes short effects
(beep/chirp/happy/sad/alert) in-process (`brain/audio.py`) and plays them with
`aplay` on the `owl` card; if the amp/I2S is absent it just logs and no-ops.
Full pin map + the SD-MODE-to-3.3V gotcha are in `WIRING.md`. The A733
specifics — and what is still unverified on hardware — are in
`specs/015-orangepi-audio.spec`.

---

## Software Architecture

```
┌─────────────────────────────────────────────────────────┐
│                 Orange Pi Brain (Python)                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Serial Handler│  │  Supervisor  │  │  Policy / OTA │  │
│  │ (NDJSON parse)│  │(log + health)│  │(sleep/wake)   │  │
│  └──────┬───────┘  └──────┬───────┘  └───────┬───────┘  │
│         └──────────────────┼──────────────────┘          │
│                    USB Serial (115200)                   │
└──────────────────────────┬──────────────────────────────┘
                           │ NDJSON
┌──────────────────────────▼──────────────────────────────┐
│              ESP32-S3 Firmware (Arduino)                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
  │  │ Serial Parser│  │State Machine │  │ Face Detector │  │
  │  │(NDJSON recv) │  │(8 states)    │  │ (esp-dl MSR01)│  │
│  └──────┬───────┘  └──────┬───────┘  └───────┬───────┘  │
│         └──────────────────┼──────────────────┘          │
│                    Telemetry Sender                      │
│              (500ms interval, NDJSON)                    │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────────────┐  │
│  │ GC9D0│ │ Eyes │ │Sensors│ │Servo │ │ FaceDetector │  │
│  │ LCD  │ │Render│ │IMU/GPS│ │Ctrl │ │   (optional)  │  │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### State Machine (ESP32)
Eight states. The ESP32 owns this machine and runs it autonomously from local
inputs (vibration sensor + on-device face detection):

| State | Eye Expression | Behavior |
|---|---|---|
| **BOOT** | SEARCHING | 3-second init, then → IDLE |
| **IDLE** | NEUTRAL | Monitors vibration + face; on either → DETECTING |
| **DETECTING** | DETECTING | Face confirmed → INTERACTING; no face for 10s → IDLE |
| **INTERACTING** | HAPPY for 1s, then AWE | Gaze follows face; face lost for 5s → IDLE |
| **SLEEPING** | SLEEPING (closed) | Eyes closed, servos centered; wake command → IDLE |
| **NAVIGATING** | SEARCHING | Head held at the Orange Pi's compass bearing (a *persistent* `nav` override, not the 3s gaze override); `nav active:false` or a 5s no-refresh timeout → IDLE |
| **UPDATE** | UPDATE (spinner) | SoftAP + `/update` HTTP server; 4-tap vibration enters, one tap exits |
| **ERROR** | — | Hardware init failure |

### Navigation — "Guide me home" (live compass)
The owl can point its head at a **named destination** and keep pointing there as
you walk — a live compass. All the math lives on the Orange Pi (it already parses the
GPS fix + IMU yaw from every telemetry frame); the ESP32 just holds the head at
the angle the Orange Pi sends.

- **Teach places** in the web UI **Places** card: name + lat/lon, typed directly
  or dropped on the embedded OpenStreetMap/Leaflet picker (no API key).
  Saved to `~/.config/robot-owl/locations.json`.
- **Start** by voice — *"Bring mich nach Home"*, *"Zeig mir den Weg zum Hotel"*,
  *"Wie komme ich zum Zoo?"* — or from the web UI **Navigate** card.
- **What you'll see**: the owl enters the NAVIGATING state (focussed eyes) and
  its head turns to point at the destination, re-aiming roughly every 0.5 s from
  live GPS + heading. The web UI shows `to <name> · <distance> m · bearing <deg>°
  · head at <aim>°`.
- **Exit** (any one of four, whichever comes first): a spoken stop phrase
  (*"Danke"*, *"Stopp die navigation"*), the web UI **Stop** button, **arrival**
  (within `arrive_m` of the target), or a **timeout** (the head recenters if the
  Orange Pi stops sending).
- The head only turns ±45°; a destination behind the owl is clamped to the
  nearest edge (the web UI says it's behind you).

Full design, math and open questions: `specs/013-navigation.spec`.

### Eye Renderer
- Two 160×160 LCDs with PSRAM framebuffers (2×51.2 KB each)
- Procedural eye drawing: sclera → iris → pupil → eyelids
- 26 expressions (24 user-selectable), all from ONE parametric routine driven by the `SHAPES[]` table
- Deliberately **two colours only**, black on white: no iris, no pupil, no sclera, no grey lids. The panels are 160 px behind small openings and that detail turned to mush — it was removed on purpose, see `specs/004-eye-expressions.spec`
- Auto-blink every 2–5 seconds
- Gaze tracking: iris/pupil offset based on face position or remote command

### Servo Controller
- PCA9685 at 50 Hz PWM (1000–2000 µs pulse width)
- Smooth interpolation: 2° per loop iteration (~60 Hz = ~120°/s max speed)
- Center position on idle/sleep transitions

### Sensors Module
- **LSM303AGR**: reads the acceleration and magnetic field vectors and derives roll/pitch from gravity plus a tilt-compensated heading from the magnetometer (`lib/OwlImu`). Hard-iron calibration is ours too, persisted in NVS
- **PA1010D GPS**: Native I2C mode via `Adafruit_GPS GPS(&Wire)` — full NMEA parsing (RMC+GGA), no UART pins
- **SW420**: Digital input with 100 ms debounce, event counting

### NDJSON Protocol

**ESP32 → Orange Pi (telemetry, every 500ms):**
```json
{
  "type": "telemetry",
  "state": "idle",
  "uptime": 12345,
  "loop_hz": 10.5,
  "loop_max_ms": 34,
  "tx_dropped": 0,
  "fw": "1.2.0",
  "heap": { "free": 142880, "min": 121344, "largest": 96256,
            "psram_free": 7340032, "psram_largest": 6291456 },
  "imu": { "pitch": -2.5, "roll": 0.8, "yaw": 180.3, "calibrated": true,
           "cal": { "axes": 2, "heading_ok": true, "restored": true } },
  "gps": { "valid": true, "latitude": 52.52, "longitude": 13.405, "altitude": 34.2, "satellites": 8 },
  "vibration": { "detected": false, "count": 3, "pulses": 3252 },
  "servos": [0.0, 0.0, 5.2, -1.0, 1.5],
  "face": { "detected": true, "x": 45, "y": 38, "w": 62, "h": 74, "confidence": 0.87,
            "gaze_x": -0.19, "gaze_y": -0.05, "total": 57, "attempts": 233,
            "capture_ms": 0, "infer_ms": 66, "stack_free": 5584 },
  "eye": "detecting"
}
```

**Orange Pi → ESP32 (commands):**
| Command | Payload | Description |
|---|---|---|
| `sleep` | `{"type":"sleep"}` | Policy: put the owl to sleep |
| `wake` | `{"type":"wake"}` | Policy: wake from sleep |
| `expression` | `{"type":"expression","value":"happy"}` | Temporary expression override (3s) |
| `servo` | `{"type":"servo","channel":0,"angle":15.0}` | Set servo angle (-45 to 45) |
| `gaze` | `{"type":"gaze","x":-0.5,"y":0.2}` | Temporary gaze override (3s) |
| `blink` | `{"type":"blink","speed":3}` | Trigger blink animation |
| `heartbeat` | `{"type":"heartbeat"}` | Request state ack |

The counters are diagnostics, and each exists because an instantaneous flag sampled at 2 Hz hid a
real failure: `vibration.pulses` (raw ISR edges since boot), `face.total` (cumulative hits), and
`face.attempts` with `loop_hz` — the denominator that says whether a low hit count means a poor
detector or a starved main loop. `imu.cal.*` carries the magnetometer calibration state:
`axes` (0..3 covered this run), `heading_ok`, and whether the offsets were restored from flash.

`face.capture_ms` / `face.infer_ms` split a detection cycle into waiting on the camera against
computing, and `face.stack_free` is the low-water mark of the core-0 detection task's stack. They
exist because the sum alone was misleading: the cycle had been "measured" at ~170-200 ms by dividing
into `loop_hz`, which charged the detector for the eye render. Timed directly it is 48-91 ms, and
`capture_ms` is 0 in every sample — the camera is never waited on.

`tx_dropped` counts NDJSON lines the firmware threw away rather than block on. The telemetry write
shares a loop with the eye rendering, so it refuses to wait for a host that is not draining the USB
CDC port — with the Arduino defaults (a 256-byte TX ring against a ~900-byte line, 100 ms per chunk,
20 retries) a single frame could stall that loop for **up to 2 s**. A rising `tx_dropped` means the
reader fell behind, not that the owl went quiet. See `specs/010`.

`loop_max_ms` and `heap.*` were added 2026-08-31 to answer "does something fill up over the
runtime?", after the owl was reported to render smoothly for the first ~12 minutes and then lag and
hold the eyes on their last position, recovering instantly on a power cycle. `loop_max_ms` is the
longest **single** loop iteration in the window that produced `loop_hz` — a mean cannot show a
stall, and its magnitude names the blocker (~35 ms healthy, ~250 ms one `I2C_TIMEOUT_MS`, up to
~2000 ms a USB CDC port the host stopped draining). `heap.free` and `heap.largest` must be read as a
pair: a falling `free` is a leak, a flat `free` with a falling `largest` is fragmentation, and
neither alone can tell them apart. `heap.min` is the since-boot low-water mark, which survives a
spike between two 2 Hz samples. Internal and PSRAM are separate because the small allocations that
churn are all internal and 8 MB of PSRAM would hide them.

While in **UPDATE** state the owl is on an isolated SoftAP (`RobotOwl-Update`); all commands except `heartbeat` are ignored, and telemetry carries the AP credentials under `update` (ssid / password / ip / url).

---

## Software Decisions

Every decision, the measurement behind it, and — most valuable — the hypotheses
that turned out to be **wrong** live in `specs/`. They are not repeated here:
this section used to carry ~95 lines of parallel rationale and had drifted (it
still claimed Arduino core 2.0.17 / ESP-IDF 4.4 long after the move to
pioarduino 3.3.11 / IDF 5.5).

**Read `specs/000-index.spec` first.** Before re-diagnosing anything, read that
subsystem's **Falsified** section — nearly all the time lost on this project
went into believing a measurement that could not mean what it appeared to mean.

| Decision | Spec |
|---|---|
| One behaviour state machine, on the ESP32; the Orange Pi supervises | [001](specs/001-system-architecture.spec) |
| `framework = arduino, espidf`, octal PSRAM, partitions, flashing | [002](specs/002-build-and-toolchain.spec) |
| Two LCDs on one SPI bus, software chip-select, custom GC9D01 driver | [003](specs/003-display-bus.spec) |
| Two colours, one parametric shape routine for all 26 moods | [004](specs/004-eye-expressions.spec) |
| I2C bus and its three devices | [005](specs/005-i2c-bus.spec) |
| IMU axes, mounting, calibration, true-north heading | [006](specs/006-imu-orientation.spec) |
| Vibration as a pulse source; the 4-tap OTA entry | [007](specs/007-vibration-and-ota.spec) |
| Camera module, mounting, `CAM_VFLIP`, OV3660 identification | [008](specs/008-camera.spec) |
| On-device face detection with esp-dl v3 | [009](specs/009-face-detection.spec) |
| The NDJSON wire contract | [010](specs/010-serial-protocol.spec) |
| Diagnostic builds and host tools | [011](specs/011-diagnostics.spec) |
| Orange Pi internals: ownership, packaging, testing discipline | [012](specs/012-orangepi-brain.spec) |
| Navigation — the owl as a compass | [013](specs/013-navigation.spec) |
| Speech — hearing the user and reacting | [014](specs/014-speech.spec) |

Requirements are numbered `R-NNN.n` and cited from code comments. If a spec
contradicts `esp32-s3-sense/include/config.h`, **the header wins** — fix the spec.

## Project Structure

```
esp32-s3-sense/                    # ESP32 firmware
├── platformio.ini                 # PlatformIO config (Arduino, PSRAM, USB CDC)
├── include/
│   ├── config.h                   # Pin assignments, constants, addresses
│   ├── common.h                   # Shared enums (EyeExpression), colors, geometry
│   ├── owl.h                      # State enum + the peripherals every module drives
│   ├── behavior.h                 # State machine surface (overrides, nav, update mode)
│   ├── protocol.h                 # NDJSON poll/handle/send
│   ├── hardware_check.h           # Wiring probe + the shared I2C scan
│   ├── Sensors.h                  # Sensor data structures
│   └── ServoController.h          # Servo controller interface
├── lib/
│   ├── GC9D01/                    # Custom SPI driver for round LCDs
│   │   ├── GC9D01.h/.cpp          # Init, framebuffer, flush, pixel drawing
│   │   └── common.h               # Display constants
│   ├── Eyes/                      # Eye renderer class
│   │   ├── Eyes.h/.cpp            # SHAPES[] table + drawBlob(): 26 expressions, lids, blink
│   │   └── common.h               # Eye geometry, colors
│   └── FaceDetector/              # Face detection module (esp-dl v3)
│       ├── FaceDetector.h/.cpp    # OV3660 + HumanFaceDetect (v3 API) integration
│                                    # Enabled with FACE_DETECTION_ENABLED=1
├── src/
│   ├── main.cpp                   # Wiring only: construct, bring up, run the loop
│   ├── behavior.cpp               # The state machine + OTA update mode
│   ├── protocol.cpp               # NDJSON commands + telemetry (the Orange Pi contract)
│   ├── vision.cpp                 # Face-detection task pinned to core 0; loop() only reads it
│   ├── hardware_check.cpp         # -DHARDWARE_CHECK=1 wiring probe; else compiles to nothing
│   ├── Sensors.cpp                # LSM303AGR IMU, PA1010D GPS, SW420 vibration
│   ├── ServoController.cpp        # PCA9685 smooth servo interpolation
│   ├── camtest.cpp                # -e camtest: SCCB probe, capture, mean brightness
│   ├── camsnap.cpp                # -e camsnap: returns the real JPEG, all 4 orientations
│   └── (dualtest / i2ctest / imuaxis / vibtest / powerprobe).cpp
├── tools/                         # Host-side helpers, run by you (German UI, see AGENTS.md)
│   ├── kalibrieren.py             # Guided magnetometer calibration (one slow full turn)
│   ├── klopftest.py               # Live tap intervals against the OTA window
│   ├── schnappschuss.py           # Decodes camsnap's JPEGs -- the camera-aim check
│   ├── trefferquote.py            # Hit rate + face size + gaze-target interval (--live)
│   └── preview_eyes.py            # Renders SHAPES[] to an HTML contact sheet, no flashing
└── .pio/build/xiao_esp32s3/       # Build output (firmware.bin)

orangepi-brain/                    # Orange Pi Zero 3W (A733) brain (Python)
├── main.py                        # Entry point, config loading, hands the main thread to read_loop
├── config.yaml                    # Serial port + every feature block (all opt-in except audio)
├── requirements.txt               # pyserial, numpy, pyyaml, flask, sounddevice, pywhispercpp, llama-cpp-python
├── setup.sh                       # Orange Pi provisioning: kernel module + DT overlay + asound + wizard + model + reboot
├── assets/sounds/                 # Owl-call WAVs played through the shared 'owl' I2S card
├── audio/                         # The A733 'owl' sound card: custom driver + DT overlay + asound.conf
│   ├── owl_i2s.c                  # Custom I2S0 driver (one-bit data-delay fix a DT overlay can't express)
│   ├── owl-i2s-overlay.dts        # Muxes the I2S0 pins (BCLK/LRCK/DOUT/DIN) onto the 40-pin header
│   ├── asound.conf                # The shared 48 kHz 'owl' card (amp + mic)
│   └── Kbuild / Makefile          # in-tree kernel-module build
├── deploy/                        # install.sh (light redeploy) + systemd unit + udev rule
│   ├── install.sh
│   ├── robot-owl-brain.service
│   └── 99-robot-owl-serial.rules
├── tools/
│   └── gen_expressions.py         # GENERATES brain/expressions.py from the firmware's NAMES[]
├── tests/                         # 201 tests; run on a plain Mac, no Orange Pi or audio stack needed
│   ├── run_tests.py               # unittest discovery
│   ├── stubs.py                   # fakes third-party modules ONLY when not importable
│   ├── test_protocol.py           # the ESP32<->Orange Pi wire contract (48 tests)
│   ├── test_expressions.py        # drift guard: Orange Pi list vs firmware NAMES[] (11)
│   ├── test_navigation*.py        # geo + controller + speech/web triggers (90)
│   ├── test_speech_*.py           # ASR, VAD pipeline, auto-sleep (39)
│   └── test_emotion.py, test_docs.py  # emotion layer + the docs checker (13)
└── brain/
    ├── serial_handler.py          # NDJSON parse/send; the _*_FIELDS tables ARE the wire contract
    ├── supervisor.py              # Logs telemetry, sends policy; owns the amp, state and locations
    ├── navigation.py              # "Guide me home": computes the head aim, streams it to the ESP32
    ├── geo.py                     # Bearing, haversine, angle wrap, aim clamp (pure functions)
    ├── locations.py               # Named places + JSON persistence + fuzzy name matching
    ├── speech.py                  # Mic -> VAD -> whisper.cpp (pywhispercpp) -> reaction pipeline (German)
    ├── emotion.py                 # Optional: transcript -> emotion (llama.cpp GGUF) -> eye expression
    ├── audio.py                   # WAV/tone playback via aplay on the shared 'owl' I2S card
    ├── web_ui.py                  # Flask control page (LAN only, no auth, disabled by default)
    ├── templates/index.html       # The web UI page (HTML/CSS/JS; not a Python string)
    ├── expressions.py             # GENERATED from NAMES[] -- do not edit by hand
    └── banner.py                  # Startup banner
```

```
> The Orange Pi mirrors nothing by hand. `brain/expressions.py` is generated from the
> firmware's `NAMES[]`, and the telemetry field tables in `serial_handler.py` are
> the single place a new field is added. Both are drift-guarded by tests — see
> `specs/010-serial-protocol.spec` and `specs/012-orangepi-brain.spec`.

---

## Build & Flash

### ESP32 Firmware
```bash
cd esp32-s3-sense
pio run                    # Build firmware.bin
pio run --target upload    # Flash to device
pio run --target monitor   # Serial monitor (115200 baud)
```

**Build size:** RAM 11.2% (36,644 / 327,680 bytes), Flash 25.5% (851,585 / 3,342,336 bytes)

---

### Orange Pi Brain — one-command setup

On a fresh DietPi install on an Orange Pi Zero 3W, the whole side is set up
with a single script:

```bash
cd orangepi-brain
sudo ./setup.sh
```

`setup.sh` (idempotent — safe to re-run) does, in order:

1. **apt** — the Python/ALSA packages plus the kernel-module build tools and `dtc`.
2. **Kernel module** — builds + installs `owl_i2s` (the A733 I2S sound card) and loads it at boot.
3. **DT overlay** — compiles + installs + enables `owl-i2s-overlay` (muxes the I2S0 pins); needs a reboot.
4. **asound.conf** — installs the shared 48 kHz `owl` card config.
5. **Install + wizard** — copies the brain to `/opt/robot-owl/orangepi-brain`, builds a `.venv`, and runs a config wizard (serial port, web UI, speech + whisper.cpp model, auto-sleep).
6. **Model** — pre-downloads the whisper.cpp model so the first transcription is instant.
7. **User + udev + systemd** — creates the `robotowl` user, installs the serial-port udev rule, and enables `robot-owl-brain.service`.
8. **Reboot** — applies the DT overlay; the owl auto-starts on boot.

To redeploy after changing brain code (without touching the audio stack), use
`sudo deploy/install.sh`. The A733 specifics — and what is still unverified on
hardware — are in `specs/015-orangepi-audio.spec`.

---

## First Run Checklist

> ⚠️ **GPIO 10 belongs to the camera.** It is the Sense camera's XCLK, so no LCD line may use it. As wired and verified on 2026-08-26 neither eye does: the **left** eye's CS is D7/GPIO 44 (`LCD_CS_L`) and the **right** eye's is D9/GPIO 8 (`LCD_CS_R`). Earlier revisions of this note named the wrong eye — `config.h` is authoritative, and `WIRING.md` has the full harness including cable colours.

### 0. Flash firmware — use USB-C first, not the Orange Pi

The XIAO's native USB D+/D− pads share the same lines as the USB-C port, so **never keep the direct solder link to the Orange Pi connected while flashing**. Flash over USB-C, then attach the native USB link afterwards.

```bash
cd esp32-s3-sense
pio run --target upload    # flash over USB-C
pio run --target monitor   # 115200 baud; opens the CDC link (firmware waits for it)
```

> ℹ️ `setup()` blocks on `while (!Serial)`, so the firmware starts only once a USB host opens the CDC link (the serial monitor counts). Powered alone, the board waits forever — always start it with a host attached.

### 1. Boot sequence on the serial monitor

After upload the monitor should show, in order:

```
Robot Owl ESP32-S3 starting...
Face detection enabled            ← only if FACE_DETECTION_ENABLED=1 (default)
System ready
{"type":"boot","msg":"ready"}
{"type":"telemetry","state":"idle","uptime":..., ...}   ← repeats every 500 ms
```

If it prints `ERROR: Left LCD failed` / `ERROR: Right LCD failed` / `ERROR: Sensors failed` or `Face detection failed - running without it`, stop here — see the checks below.

### 2. Verify healthy telemetry

| Field | Expected on first run |
|---|---|
| `state` | `boot` → `idle` after ~3 s |
| `imu.calibrated` | `false` until the magnetometer is calibrated — **never done on this sensor yet** (`BACKLOG.md` Step 3). It means *hard-iron offsets exist* **and** *the geometry currently yields a usable heading*. Run `esp32-s3-sense/tools/kalibrieren.py` and turn the owl slowly through one full circle; watch `imu.cal.axes` reach 2 of 3 in the web UI (a level turn cannot reach 3 — only the two horizontal axes see any span). Note that `imu.yaw` is additionally off by the unmeasured mounting rotation, see `specs/006` Open |
| `imu.cal.restored` | `true` once calibration has been saved and the owl rebooted — this is how you tell "calibration survived the last flash" from "it was wiped again" |
| `gps.valid` | `true` only with a sky-view fix; `gps.satellites` > 0 |
| `vibration.count` | `0` — increments when the SW420 is tapped |
| `face.detected` | `false` (true when a face is in frame) |
| `eye` | `searching` → `neutral` |

When wired, all I2C devices must be present on D0/D1 (addresses `0x19` + `0x1E` LSM303AGR, `0x10` PA1010D, `0x40` PCA9685). A missing device is almost always a solder/daisy-chain issue, not a code one.

### 3. Test face detection (camera)

1. Confirm the Sense camera is seated on the B2B connector and GPIO 10 is *not* wired to any LCD (it is the camera XCLK).
2. Point the camera at a face ~0.5–1.5 m away with decent lighting.
3. The telemetry should flip to `"face":{"detected":true,...}` and the state should advance `idle → detecting → interacting` while the eyes follow the face.
4. If `face.detected` stays `false`: check `pio monitor` boot messages, ensure `CAMERA_FB_IN_PSRAM` has PSRAM available (it does on the Sense), and try raising the model sensitivity note in `config.h` / `FaceDetector.cpp` (score threshold / resize scale).

### 4. Send a test command

Paste one of these into the monitor:

```json
{"type":"blink","speed":3}
{"type":"expression","value":"happy"}
{"type":"servo","channel":0,"angle":15}
{"type":"heartbeat"}
```

Expect an `*_ack` reply and a visible eye/servo reaction.

### 5. Connect the native USB link to the Orange Pi

1. Power down, remove the USB-C cable.
2. Terminate the XIAO D+/D− (backside pads) + 5V + GND with a USB cable whose other end is a micro-USB plug for the Orange Pi Zero 3W (see `WIRING.md`).
3. Power the Orange Pi — the XIAO powers from the Orange Pi's 5V rail.
4. On the Orange Pi check the device appeared: `ls /dev/ttyACM0` (or `ttyUSB0`).
5. `dmesg | tail` should show a USB CDC device enumerating; `lsusb` shows the ESP32-S3.

**If it does not enumerate:** re-check DP↔D+ vs DN↔D− (most common swap), then GND continuity and wire length (< 10 cm).

### 6. Run the Orange Pi brain

```bash
cd orangepi-brain
pip install -r requirements.txt
python main.py        # connects to /dev/ttyACM0 @ 115200 by default (see config.yaml)
```

Expect log lines like `Robot Owl Brain started (supervisor mode)` and `ESP32 owns behavior; waiting for telemetry...`, then state-change and face-detection log lines. The ESP32 drives expressions/servos; the Orange Pi supervisor only observes and can send policy (sleep/wake).

### 7. Test the OTA update mode end-to-end

**Validated end to end on hardware 2026-08-26**: 4 taps entered update mode, the SoftAP came up, a phone connected and loaded the page, and a single tap returned the owl to normal operation.

1. **Enter update mode:** tap the owl's body (the SW420 vibration sensor) **4 times** within ~1.5 s. The eyes switch to the green spinner and the Orange Pi supervisor logs the SoftAP credentials.
2. **Join the SoftAP:** on a phone or laptop, connect to WiFi **`RobotOwl-Update`** (password **`robotowl123`**). The owl is on an isolated AP — you lose normal internet while connected, which is expected.
3. **Open the update page:** go to **`http://192.168.4.1/update`** in a browser. (The exact IP is also printed in the supervisor log / telemetry `update` object.)
4. **Flash:** select the new `firmware.bin` (from `esp32-s3-sense/.pio/build/xiao_esp32s3/firmware.bin`) and upload. Watch the progress bar.
5. **Confirm the new version:** after flashing, the owl reboots into the new firmware. The Orange Pi supervisor logs `Owl firmware changed: <old> -> <new>` — this is the confirmation the OTA took.
6. **Exit update mode:** tap the owl **once** (after the ~1 s grace period). The eyes return to normal and the owl rejoins normal operation.

> ℹ️ Tap roughly once per second. The gap between taps must be **under 1.5 s** (`UPDATE_TAP_GAP_MS`) but also over ~0.75 s — a tap's own chatter rings for ~0.5 s, and two taps closer than that merge into one. `esp32-s3-sense/tools/klopftest.py` shows each tap's exact interval live if the timing needs checking. The owl boots standalone (5 s USB wait), so update mode works without the Orange Pi attached — you just won't get the credential log lines.

---

### Enable Face Detection
Face detection is enabled by default (`platformio.ini`: `-DFACE_DETECTION_ENABLED=1`). To disable, set it to `0` in `platformio.ini` (and `config.h`).
The esp-dl model libraries ship with the Arduino SDK — no extra component installation needed.

### Orange Pi Brain
```bash
cd orangepi-brain
pip install -r requirements.txt
python main.py [config.yaml]   # Default config path
```

---

## Current State

| Component | Status | Notes |
|---|---|---|
| **GC9D01 LCD Driver** | ✅ Complete | Custom SPI driver, PSRAM framebuffers, software chip-select, 16 MHz. Both panels verified working on one shared bus |
| **Eye Renderer** | ✅ Complete | 26 expressions (24 user-selectable) from ONE parametric routine driven by the `SHAPES[]` table — retune a mood by editing numbers, never by adding a draw function. Auto-blink, gaze, eyelids. Two colours only, black on white (see `AGENTS.md` and `specs/004-eye-expressions.spec`) |
| **LSM303AGR IMU** | ⚠️ Ported, unmeasured | Driver, software fusion and hard-iron calibration are in and validated on hardware (`\|a\|` = 9.77 m/s², both `WHO_AM_I` correct). **Not yet mounted, so the axis remap and the heading offset's mounting term are both unmeasured** — `imu.yaw` is not trustworthy until then (`specs/006` Open) |
| **PA1010D GPS** | ✅ Complete | Native I2C (Adafruit_GPS), RMC+GGA NMEA parsing |
| **SW420 Vibration** | ✅ Complete | **Edge-counting ISR**, evaluated once per main loop (~60 Hz). Explicitly *not* a level read and *not* debounced: the sensor emits ~1 kHz pulse bursts, and reading it as a stable level is what pinned the owl in DETECTING forever. `vibration.pulses` reports raw edges since boot so a dead sensor is distinguishable from a quiet one. See `specs/007-vibration-and-ota.spec` |
| **PCA9685 Servo Ctrl** | ✅ Complete | 5 channels, smooth interpolation (2°/iteration) |
| **State Machine** | ✅ Complete | 8 states on ESP32 (owns behavior): BOOT/IDLE/DETECTING/INTERACTING/SLEEPING/NAVIGATING/UPDATE/ERROR |
| **NDJSON Protocol** | ✅ Complete | Telemetry (500ms) + commands (expression/servo/gaze/nav/wake/blink/heartbeat) |
| **Navigation "guide me home"** | ✅ Implemented | Orange Pi computes the compass bearing to a named destination and streams the head aim; ESP32 holds it in the NAVIGATING state (live compass). Start via voice ("Bring mich nach Home") or web UI; exit via spoken keyword, web UI, arrival, or timeout. See `specs/013-navigation.spec`. On-hardware `aim_sign` verification pending (BACKLOG Step 5) |
| **Face Detection (ESP32)** | ✅ Complete | esp-dl **v3** `HumanFaceDetect` (managed IDF component, *not* the old `HumanFaceDetectMSR01`), OV3660 QVGA RGB565BE, `set_vflip(1)` mandatory, 48-91 ms inference **on core 0** since 2026-08-31 so it no longer stalls the eye render, gaze offsets + state transitions on-device. Hit rate is 100 % at a normal seating distance; it is dominated by face size in frame, and glasses cost about a third of it — see `specs/009-face-detection.spec` |
| **OTA Update Mode** | ✅ Complete | 4-tap vibration → SoftAP `RobotOwl-Update` + `/update` HTTP page (HTTPUpdateServer); one tap exits; dual-bank ota_0/ota_1; standalone boot (5s USB wait) |
| **Face Detection (Orange Pi)** | ❌ Not implemented | OpenCV/MediaPipe fallback not needed (ESP32 does it); optional future enhancement |
| **Web UI (Orange Pi)** | ✅ Complete | Flask on :8080, **disabled by default, no authentication — LAN only**. Blink/expression/servo/sound controls, live telemetry incl. IMU heading, GPS fix and magnetometer calibration state, and a map place-picker for navigation. Page lives in `brain/templates/index.html`; the `/api/telemetry` payload is derived from the parsed telemetry dataclass, so it cannot fall behind the firmware (the SoftAP password is the one field deliberately withheld) |
| **Speech (Orange Pi)** | ✅ Implemented | German. Mic → RMS VAD gate → whisper.cpp (via the `pywhispercpp` binding, an offline `.bin`/`.gguf` model — no torch/CTranslate2) → keyword clusters / navigation triggers. Gated on awake + face + energy so the owl does not react to the TV. Disabled by default. See `specs/014-speech.spec` |
| **Orange Pi test suite** | ✅ 201 tests | Runs on a plain dev machine with no Orange Pi, mic, PortAudio, whisper.cpp, llama.cpp, Flask or PyYAML — `tests/stubs.py` substitutes a module only when the real one is missing. numpy is the one hard dependency. Includes the ESP32↔Orange Pi wire contract (48, `test_protocol.py`) and firmware-vs-Orange Pi drift guards (11, `test_expressions.py`) |
| **Hardware Assembly** | 🚧 Wiring done/ongoing | Solder links documented in `WIRING.md`; mechanical build (ears/head/wings, enclosure) pending |

---

## What's next

**There is exactly one backlog: [`BACKLOG.md`](BACKLOG.md).** Open work is not
tracked here — this section used to duplicate it, with eight items that drifted
out of step with the real list.

Its top section is a dependency-ordered Step list, and the order matters: Steps
0–5 need the owl (flash → mechanical → validate → calibrate → the detection bug
→ navigation), Step 6 needs only a laptop. Two orderings there are easy to get
wrong and expensive — head-opening work before any IMU calibration, and the
camera extension before any detection tuning.

The one genuinely open bug: **detection is more sporadic in the firmware than in
the isolated `facelab/` project** — same model, same camera, same thresholds, so
it is the integration. `BACKLOG.md` Step 4; measure the loop period first.

## References

- XIAO ESP32-S3 Sense pinout: https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/
- GC9D01 init sequence (TFT_eSPI PR #3783): https://github.com/Bodmer/TFT_eSPI/pull/3783
- ESP-DL (formerly esp-face): https://github.com/espressif/esp-dl
- OV3660 camera pinout (XIAO Sense): PWDN=-1, RESET=-1, XCLK=10, SIOD=40, SIOC=39, D7-D0=48,11,12,14,16,18,17,15, VSYNC=38, HREF=47, PCLK=13
- PlatformIO ESP-IDF framework: https://docs.platformio.org/en/latest/platforms/espressif32.html
