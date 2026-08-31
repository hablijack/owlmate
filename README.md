# Robot Owl — Embedded Firmware & Brain

A robotic owl companion with expressive LCD eyes, face detection, IMU, GPS, and servo-controlled ears/head/wings. The ESP32-S3 Sense owns the behavior state machine and runs on-device sensor fusion and eye rendering, communicating NDJSON telemetry to a Raspberry Pi "supervisor" for logging, health monitoring, and policy.

---

## Hardware

### Main Board
- **Seeed Studio XIAO ESP32-S3 Sense** — ESP32-S3 dual-core @ 240 MHz, 8 MB Flash, 8 MB PSRAM (OPI), USB-CDC on boot, 24-pin FPC camera connector on the Sense expansion (fitted with an **OV3660**, verified on hardware — the docs previously said OV3660 throughout)

### Peripherals
| Component | Model | Interface | Address / Pins | Function |
|---|---|---|---|---|
| **LCD Eyes** | Waveshare 0.71" round (×2) | SPI (shared bus) | shared SCK=5 (D4), MOSI=7 (D8), RST=43 (D6); left CS=44 (D7) DC=9 (D10); right CS=8 (D9) DC=4 (D3) | Expressive eyes with GC9D01 driver. Verified on hardware 2026-08-26 |
| **Camera** | OV3660 (on-board, Sense expansion) | SCCB/I2C + parallel | XCLK=10, SIOD=40, SIOC=39, D0-D7 on GPIO 15-18,38-48, VSYNC=38, HREF=47, PCLK=13 | Face detection input |
| **IMU** | Adafruit BNO055 | I2C | SDA=1, SCL=2, addr 0x28 | Orientation (pitch/roll/yaw) |
| **GPS** | Adafruit PA1010D | I2C | SDA=1, SCL=2, addr 0x10 | Position/satellites |
| **Servo Driver** | PCA9685 | I2C | SDA=1, SCL=2, addr 0x40 | 5-channel PWM for servos |
| **Vibration** | SW420 | Digital GPIO | GPIO3 (D2), input | Wake-on-vibration trigger |
| **Audio Amp** | MAX98357A (Adafruit) | I2S — **on the Raspberry Pi**, not the ESP32 | BCLK=GPIO18, LRCLK=GPIO19, DIN=GPIO21 (Pi 40-pin header) | Sound effects / voice |

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
All three I2C devices share the same bus: BNO055 @ 0x28, PA1010D @ 0x10, PCA9685 @ 0x40. Clock: 400 kHz.

### Power
- LCD backlights tied directly to 3.3V (always on)
- Servos powered separately (not from ESP32 3.3V rail)
- MAX98357A amp powered from the Pi 5V rail; speaker is 4Ω or 8Ω

### Audio (RPi-side)
Sound lives **entirely on the Raspberry Pi** — the MAX98357A amp is driven
over the Pi's I2S bus, so the ESP32 has no audio pins. Enable I2S with
`dtoverlay=hifiberry-i2s-lite` in `/boot/config.txt`. The brain synthesizes
short effects (beep/chirp/happy/sad/alert) in-process (`brain/audio.py`) and
plays them with `aplay`; if the amp/I2S is absent it just logs and no-ops.
Full pin map + the SD-MODE-to-3.3V gotcha are in `WIRING.md`.

---

## Software Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    RPi Brain (Python)                    │
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
| **NAVIGATING** | SEARCHING | Head held at the RPi's compass bearing (a *persistent* `nav` override, not the 3s gaze override); `nav active:false` or a 5s no-refresh timeout → IDLE |
| **UPDATE** | UPDATE (spinner) | SoftAP + `/update` HTTP server; 4-tap vibration enters, one tap exits |
| **ERROR** | — | Hardware init failure |

### Navigation — "Guide me home" (live compass)
The owl can point its head at a **named destination** and keep pointing there as
you walk — a live compass. All the math lives on the RPi (it already parses the
GPS fix + IMU yaw from every telemetry frame); the ESP32 just holds the head at
the angle the RPi sends.

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
  RPi stops sending).
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
- **BNO055**: Reads Euler angles (pitch/roll/yaw) from registers 0x1A–0x21, calibration status from 0x35
- **PA1010D GPS**: Native I2C mode via `Adafruit_GPS GPS(&Wire)` — full NMEA parsing (RMC+GGA), no UART pins
- **SW420**: Digital input with 100 ms debounce, event counting

### NDJSON Protocol

**ESP32 → RPi (telemetry, every 500ms):**
```json
{
  "type": "telemetry",
  "state": "idle",
  "uptime": 12345,
  "loop_hz": 10.5,
  "fw": "1.2.0",
  "imu": { "pitch": -2.5, "roll": 0.8, "yaw": 180.3, "calibrated": true,
           "cal": { "sys": 2, "gyro": 3, "accel": 1, "mag": 3, "restored": true } },
  "gps": { "valid": true, "latitude": 52.52, "longitude": 13.405, "altitude": 34.2, "satellites": 8 },
  "vibration": { "detected": false, "count": 3, "pulses": 3252 },
  "servos": [0.0, 0.0, 5.2, -1.0, 1.5],
  "face": { "detected": true, "x": 45, "y": 38, "w": 62, "h": 74, "confidence": 0.87,
            "gaze_x": -0.19, "gaze_y": -0.05, "total": 57, "attempts": 233 },
  "eye": "detecting"
}
```

**RPi → ESP32 (commands):**
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
detector or a starved main loop. `imu.cal.*` carries the BNO055 calibration counters and whether
they were restored from flash.

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
| One behaviour state machine, on the ESP32; the RPi supervises | [001](specs/001-system-architecture.spec) |
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
| RPi internals: ownership, packaging, testing discipline | [012](specs/012-rpi-brain.spec) |
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
│   ├── protocol.cpp               # NDJSON commands + telemetry (the RPi contract)
│   ├── hardware_check.cpp         # -DHARDWARE_CHECK=1 wiring probe; else compiles to nothing
│   ├── Sensors.cpp                # BNO055 IMU, PA1010D GPS, SW420 vibration
│   ├── ServoController.cpp        # PCA9685 smooth servo interpolation
│   ├── camtest.cpp                # -e camtest: SCCB probe, capture, mean brightness
│   ├── camsnap.cpp                # -e camsnap: returns the real JPEG, all 4 orientations
│   └── (dualtest / i2ctest / imuaxis / vibtest / powerprobe).cpp
├── tools/                         # Host-side helpers, run by you (German UI, see AGENTS.md)
│   ├── kalibrieren.py             # Guided BNO055 calibration (figure-8 before static poses)
│   ├── klopftest.py               # Live tap intervals against the OTA window
│   ├── schnappschuss.py           # Decodes camsnap's JPEGs -- the camera-aim check
│   └── preview_eyes.py            # Renders SHAPES[] to an HTML contact sheet, no flashing
└── .pio/build/xiao_esp32s3/       # Build output (firmware.bin)

rpi-brain/                         # Raspberry Pi brain (Python)
├── main.py                        # Entry point, config loading, hands the main thread to read_loop
├── config.yaml                    # Serial port + every feature block (all opt-in, enabled: false)
├── requirements.txt               # pyserial, numpy, pyyaml, flask, sounddevice, faster-whisper
├── setup.sh                       # Pi provisioning: venv, systemd unit, udev rule (idempotent)
├── assets/sounds/                 # Owl-call WAVs played through the I2S amp
├── tools/
│   └── gen_expressions.py         # GENERATES brain/expressions.py from the firmware's NAMES[]
├── tests/                         # 177 tests; run on a plain Mac, no Pi or audio stack needed
│   ├── run_tests.py               # unittest discovery
│   ├── stubs.py                   # fakes third-party modules ONLY when not importable
│   ├── test_protocol.py           # the ESP32<->RPi wire contract (39 tests)
│   └── test_expressions.py        # drift guard: RPi list vs firmware NAMES[]
└── brain/
    ├── serial_handler.py          # NDJSON parse/send; the _*_FIELDS tables ARE the wire contract
    ├── supervisor.py              # Logs telemetry, sends policy; owns the amp, state and locations
    ├── navigation.py              # "Guide me home": computes the head aim, streams it to the ESP32
    ├── geo.py                     # Bearing, haversine, angle wrap, aim clamp (pure functions)
    ├── locations.py               # Named places + JSON persistence + fuzzy name matching
    ├── speech.py                  # Mic -> VAD -> faster-whisper -> reaction pipeline (German)
    ├── audio.py                   # WAV/tone playback via aplay on the MAX98357A amp
    ├── web_ui.py                  # Flask control page (LAN only, no auth, disabled by default)
    ├── templates/index.html       # The web UI page (HTML/CSS/JS; not a Python string)
    ├── expressions.py             # GENERATED from NAMES[] -- do not edit by hand
    └── banner.py                  # Startup banner
```

> The RPi mirrors nothing by hand. `brain/expressions.py` is generated from the
> firmware's `NAMES[]`, and the telemetry field tables in `serial_handler.py` are
> the single place a new field is added. Both are drift-guarded by tests — see
> `specs/010-serial-protocol.spec` and `specs/012-rpi-brain.spec`.

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

### Raspberry Pi Brain — one-command setup

On a fresh Raspberry Pi OS install, the whole RPi side is set up with a single script:

```bash
cd rpi-brain
sudo ./setup.sh
```

`setup.sh` (idempotent — safe to re-run) does, in order:

1. **apt** — installs `python3-venv`, `python3-pip`, `alsa-utils` (for `aplay`), `rsync`, `portaudio19-dev`.
2. **I2S audio** — adds `dtoverlay=hifiberry-i2s-lite` to the right `config.txt`
   (handles both `/boot` and Bookworm's `/boot/firmware`) so the MAX98357A amp works.
3. **Config wizard** — asks a few questions (ESP32 serial port, web UI, speech
   recognition + Whisper model, auto-sleep) and writes `config.yaml` with your
   choices. **Useful defaults are pre-filled — just press Enter to accept.** It
   auto-detects the ESP32 serial port and any USB mic.
4. **Install** — copies the brain to `/opt/robot-owl/rpi-brain`, builds a `.venv`,
   installs `requirements.txt` (incl. **faster-whisper** — offline ASR, no torch).
5. **Pre-download the Whisper model** — so the first live transcription is instant
   instead of a several-minute download. The default is **`small` with
   `compute_type="int8"`**, faster-whisper's recommendation for a Pi 4 (~0.5 GB);
   pick `tiny`/`base` in the wizard for lower latency and less accuracy.
6. **User + permissions** — creates the `robotowl` system user in the `dial` group
   and installs a udev rule so it can open the ESP32 USB CDC serial port.
7. **systemd** — installs + enables `robot-owl-brain.service`.
8. **Reboot** — reboots to apply the I2S overlay; the robot auto-starts on boot
   (a one-shot hook guarantees it, then clears itself).

After the reboot the owl is running. Then:

```bash
journalctl -u robot-owl-brain -f      # watch it
systemctl status robot-owl-brain       # check state
aplay -l                                # confirm the I2S amp is visible
```

> The wizard already set the web UI / speech / auto-sleep options. To change
anything later, edit `/opt/robot-owl/rpi-brain/config.yaml` (or re-run
`sudo ./setup.sh` to go through the wizard again). For an unattended install use
`sudo ./setup.sh --non-interactive` to skip the wizard and keep the bundled defaults.

---

## First Run Checklist

> ⚠️ **GPIO 10 belongs to the camera.** It is the Sense camera's XCLK, so no LCD line may use it. As wired and verified on 2026-08-26 neither eye does: the **left** eye's CS is D7/GPIO 44 (`LCD_CS_L`) and the **right** eye's is D9/GPIO 8 (`LCD_CS_R`). Earlier revisions of this note named the wrong eye — `config.h` is authoritative, and `WIRING.md` has the full harness including cable colours.

### 0. Flash firmware — use USB-C first, not the Pi

The XIAO's native USB D+/D− pads share the same lines as the USB-C port, so **never keep the direct solder link to the Pi connected while flashing**. Flash over USB-C, then attach the native USB link afterwards.

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
| `imu.calibrated` | `false` until the BNO055 is calibrated — **the stored calibration was erased and has not been redone** (`BACKLOG.md` Step 3). It is `gyro >= 3 && mag >= 3`, and `mag` only rises during a figure-8. Run `esp32-s3-sense/tools/kalibrieren.py`; figure-8 for `mag` BEFORE the static poses for `accel`, never after. Watch `imu.cal.{gyro,mag}` climb to 3/3 in the web UI |
| `imu.cal.restored` | `true` once calibration has been saved and the owl rebooted — this is how you tell "calibration survived the last flash" from "it was wiped again" |
| `gps.valid` | `true` only with a sky-view fix; `gps.satellites` > 0 |
| `vibration.count` | `0` — increments when the SW420 is tapped |
| `face.detected` | `false` (true when a face is in frame) |
| `eye` | `searching` → `neutral` |

When wired, all three I2C devices must be present on D0/D1 (addresses `0x28` BNO055, `0x10` PA1010D, `0x40` PCA9685). A missing device is almost always a solder/daisy-chain issue, not a code one.

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

### 5. Connect the native USB link to the Pi 4

1. Power down, remove the USB-C cable.
2. Solder XIAO D+/D− (backside pads) + 5V + GND to a **USB 2.0** port's underside pads on the Pi 4 (see `WIRING.md`).
3. Power the Pi — the XIAO powers from the Pi's 5V rail.
4. On the Pi check the device appeared: `ls /dev/ttyACM0` (or `ttyUSB0`).
5. `dmesg | tail` should show a USB CDC device enumerating; `lsusb` shows the ESP32-S3.

**If it does not enumerate:** re-check DP↔D+ vs DN↔D− (most common swap), then GND continuity and wire length (< 10 cm).

### 6. Run the RPi brain

```bash
cd rpi-brain
pip install -r requirements.txt
python main.py        # connects to /dev/ttyACM0 @ 115200 by default (see config.yaml)
```

Expect log lines like `Robot Owl Brain started (supervisor mode)` and `ESP32 owns behavior; waiting for telemetry...`, then state-change and face-detection log lines. The ESP32 drives expressions/servos; the RPi supervisor only observes and can send policy (sleep/wake).

### 7. Test the OTA update mode end-to-end

**Validated end to end on hardware 2026-08-26**: 4 taps entered update mode, the SoftAP came up, a phone connected and loaded the page, and a single tap returned the owl to normal operation.

1. **Enter update mode:** tap the owl's body (the SW420 vibration sensor) **4 times** within ~1.5 s. The eyes switch to the green spinner and the RPi supervisor logs the SoftAP credentials.
2. **Join the SoftAP:** on a phone or laptop, connect to WiFi **`RobotOwl-Update`** (password **`robotowl123`**). The owl is on an isolated AP — you lose normal internet while connected, which is expected.
3. **Open the update page:** go to **`http://192.168.4.1/update`** in a browser. (The exact IP is also printed in the supervisor log / telemetry `update` object.)
4. **Flash:** select the new `firmware.bin` (from `esp32-s3-sense/.pio/build/xiao_esp32s3/firmware.bin`) and upload. Watch the progress bar.
5. **Confirm the new version:** after flashing, the owl reboots into the new firmware. The RPi supervisor logs `Owl firmware changed: <old> -> <new>` — this is the confirmation the OTA took.
6. **Exit update mode:** tap the owl **once** (after the ~1 s grace period). The eyes return to normal and the owl rejoins normal operation.

> ℹ️ Tap roughly once per second. The gap between taps must be **under 1.5 s** (`UPDATE_TAP_GAP_MS`) but also over ~0.75 s — a tap's own chatter rings for ~0.5 s, and two taps closer than that merge into one. `esp32-s3-sense/tools/klopftest.py` shows each tap's exact interval live if the timing needs checking. The owl boots standalone (5 s USB wait), so update mode works without the RPi attached — you just won't get the credential log lines.

---

### Enable Face Detection
Face detection is enabled by default (`platformio.ini`: `-DFACE_DETECTION_ENABLED=1`). To disable, set it to `0` in `platformio.ini` (and `config.h`).
The esp-dl model libraries ship with the Arduino SDK — no extra component installation needed.

### RPi Brain
```bash
cd rpi-brain
pip install -r requirements.txt
python main.py [config.yaml]   # Default config path
```

---

## Current State

| Component | Status | Notes |
|---|---|---|
| **GC9D01 LCD Driver** | ✅ Complete | Custom SPI driver, PSRAM framebuffers, software chip-select, 16 MHz. Both panels verified working on one shared bus |
| **Eye Renderer** | ✅ Complete | 26 expressions (24 user-selectable) from ONE parametric routine driven by the `SHAPES[]` table — retune a mood by editing numbers, never by adding a draw function. Auto-blink, gaze, eyelids. Two colours only, black on white (see `AGENTS.md` and `specs/004-eye-expressions.spec`) |
| **BNO055 IMU** | ✅ Complete | Euler angles + calibration status via I2C |
| **PA1010D GPS** | ✅ Complete | Native I2C (Adafruit_GPS), RMC+GGA NMEA parsing |
| **SW420 Vibration** | ✅ Complete | **Edge-counting ISR**, evaluated once per main loop (~60 Hz). Explicitly *not* a level read and *not* debounced: the sensor emits ~1 kHz pulse bursts, and reading it as a stable level is what pinned the owl in DETECTING forever. `vibration.pulses` reports raw edges since boot so a dead sensor is distinguishable from a quiet one. See `specs/007-vibration-and-ota.spec` |
| **PCA9685 Servo Ctrl** | ✅ Complete | 5 channels, smooth interpolation (2°/iteration) |
| **State Machine** | ✅ Complete | 8 states on ESP32 (owns behavior): BOOT/IDLE/DETECTING/INTERACTING/SLEEPING/NAVIGATING/UPDATE/ERROR |
| **NDJSON Protocol** | ✅ Complete | Telemetry (500ms) + commands (expression/servo/gaze/nav/wake/blink/heartbeat) |
| **Navigation "guide me home"** | ✅ Implemented | RPi computes the compass bearing to a named destination and streams the head aim; ESP32 holds it in the NAVIGATING state (live compass). Start via voice ("Bring mich nach Home") or web UI; exit via spoken keyword, web UI, arrival, or timeout. See `specs/013-navigation.spec`. On-hardware `aim_sign` verification pending (BACKLOG Step 5) |
| **Face Detection (ESP32)** | ✅ Complete | esp-dl **v3** `HumanFaceDetect` (managed IDF component, *not* the old `HumanFaceDetectMSR01`), OV3660 QVGA RGB565BE, `set_vflip(1)` mandatory, 48 ms inference (~21 fps), gaze offsets + state transitions on-device. Detection is currently more sporadic in the firmware than in the isolated `facelab/` project — see `BACKLOG.md` |
| **OTA Update Mode** | ✅ Complete | 4-tap vibration → SoftAP `RobotOwl-Update` + `/update` HTTP page (HTTPUpdateServer); one tap exits; dual-bank ota_0/ota_1; standalone boot (5s USB wait) |
| **Face Detection (RPi)** | ❌ Not implemented | OpenCV/MediaPipe fallback not needed (ESP32 does it); optional future enhancement |
| **Web UI (RPi)** | ✅ Complete | Flask on :8080, **disabled by default, no authentication — LAN only**. Blink/expression/servo/sound controls, live telemetry incl. IMU heading, GPS fix and BNO055 calibration counters, and a map place-picker for navigation. Page lives in `brain/templates/index.html` |
| **Speech (RPi)** | ✅ Implemented | German. Mic → RMS VAD gate → faster-whisper (`small`, int8 on CPU — the combination recommended for a Pi 4; CTranslate2, not torch) → keyword clusters / navigation triggers. Gated on awake + face + energy so the owl does not react to the TV. Disabled by default. See `specs/014-speech.spec` |
| **RPi test suite** | ✅ 177 tests | Runs on a plain dev machine with no Pi, mic, PortAudio, faster-whisper, Flask, Jinja2 or PyYAML — `tests/stubs.py` substitutes a module only when the real one is missing. numpy is the one hard dependency. Includes the ESP32↔RPi wire contract (39) and firmware-vs-RPi drift guards (11) |
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
