# SPEC-011: Diagnostic builds and host tools

Status: implemented
Verified: 2026-08-31 — every tool used in anger; trefferquote extended for loop_hz
Depends on: 002

## Intent

Almost every problem in this project was a wrong belief about the hardware, held
because the available evidence was ambiguous. The diagnostics exist to make
individual claims falsifiable in isolation, cheaply, so a wrong belief dies in
minutes instead of days.

## Requirements

* **R-011.1** Each diagnostic must isolate one subsystem, with nothing else
  running.
* **R-011.2** A diagnostic must be able to distinguish "absent" from "present but
  not working" — an ambiguous result is a failed diagnostic.
* **R-011.3** Diagnostics must build fast enough to iterate on.
* **R-011.4** Exactly one `setup()`/`loop()` pair may link per environment.

## Decisions

**Each diagnostic is its own build environment**, compiling exactly one source
file via `build_src_filter`, and every sketch is wrapped in
`#if defined(ITS_FLAG)` so it compiles to nothing in the firmware build.
They extend `[arduino_base]`, never the firmware env, to stay on the fast
Arduino-only path (SPEC-002).

| env | subject | key trick |
|---|---|---|
| `dualtest` | two LCDs on one bus | distinct colours **and a swap**, so a pass cannot be coincidence |
| `i2ctest` | I2C bus | a **bit-banged** scan that bypasses the ESP-IDF driver entirely |
| `imuaxis` | IMU mounting | prints gravity in the **raw sensor frame**, deliberately no axis remap |
| `vibtest` | vibration sensor | pull-up/pull-down test, sink impedance, edge scan over **all 11 header pins** |
| `camtest` | camera | 8-bit **and** 16-bit ID reads, plus mean brightness to catch "initialises but returns black" |
| `camsnap` | camera framing/orientation | returns the **actual JPEG** in all four vflip/hmirror combinations — the only check that catches a camera aimed at the wrong place |
| `powerprobe` | supply rail | a **genuinely** minimal image — its own `src/powerprobe.cpp`, no PSRAM/LCD/camera/I2C/servos — plus a rolling 5 V-input reading |

**Host tools are separate from firmware diagnostics**, live in
`esp32-s3-sense/tools/`, and are run by the user so that timing is in their
hands:

| tool | purpose |
|---|---|
| `kalibrieren.py` | guided BNO055 calibration; enforces figure-8 **before** static poses |
| `klopftest.py` | live tap intervals against the OTA window |
| `preview_eyes.py` | renders `SHAPES[]` to an HTML contact sheet |
| `schnappschuss.py` | decodes `camsnap`'s Base64 JPEGs to files; syncs on a sweep start so the four orientations are one comparable set |

**Tools find their own serial port** — `OWL_PORT`, else `/dev/cu.usbmodem*`,
`/dev/ttyACM*`, `/dev/ttyUSB*` — so the same script runs from the Mac or from the
Pi.

**A measurement that cannot fail is not a check.** `camtest` reports mean
brightness, which distinguishes "black frame" from "real image" — but a camera
pointed at the ceiling produces a flawless brightness reading, and on 2026-08-28
that passed every `camtest` criterion while face detection scored 0 in 31.5 s
(SPEC-008). `camsnap` exists because the only way to falsify "the camera sees
what we think it sees" is to look at the frame. Prefer a diagnostic that returns
the artefact over one that returns a statistic about it.

**`preview_eyes.py` parses the C++ table** rather than duplicating it, and
re-implements `drawBlob()`'s maths exactly. A preview that can drift from the
firmware is worse than no preview.

**Interactive tests hand timing to the user.** Several measurements failed
during the 2026-08-26 session purely because a capture window opened before the
user had read the instruction. Anything needing a human action either syncs
explicitly first or is packaged as a script the user starts themselves.

## Verified facts

* **`pyserial` reboots the owl on connect; `cat` does not.** Opening the port
  with pyserial asserts DTR/RTS and resets the board, so every capture starts
  3.5 s after a fresh boot. `timeout N cat /dev/cu.usbmodem*` leaves it running —
  verified 2026-08-31 by watching `uptime` keep climbing across two connects
  (447 s -> 455 s). This matters whenever the question is about steady-state
  behaviour rather than boot: on 2026-08-31 a whole hypothesis ("the reset is
  the confound") was raised and then *disproved* only because the non-invasive
  method existed to check it. The owl's own tools should prefer `cat` for
  observation and pyserial only when a fresh boot is wanted.
* **A detection measurement with nobody in front of the camera reads as a
  fault.** Several readings on 2026-08-31 (0 hits, 31 %) were the owner being
  absent or further away, not a defect. `tools/trefferquote.py` prints the state
  and face size alongside the rate so this is visible in the output rather than
  inferred. Its `--live` mode exists because the person watching the owl and the
  person reading the terminal are often not in the same room. **Say out loud
  when you are in front of the camera and when you are not** — this cost most of
  a session on 2026-08-31, because a reading taken with nobody in view is
  indistinguishable from a broken owl, and one was mistaken for exactly that.
* **Attaching `cat` to an unread port corrupts the first `loop_hz` samples.**
  Measured 2026-08-31: a fresh attach read `4 22 40 56 50 …` while an immediate
  repeat read `47 47 47 53 …`. Likely the full USB CDC TX buffer draining once a
  reader appears. `trefferquote.py --datei` therefore discards the first two
  telemetry frames. Without that, **every** capture reports a stall that was only
  the measurement — precisely the class of artefact this project loses days to,
  and it appears in the one metric added to detect stalls.
* **Report `loop_hz` as a sequence, not a mean.** The tool prints both, plus a
  count of samples below 15 Hz (the rate at which eye motion stops reading as
  motion). The mean is actively misleading here: moving the inference to core 0
  took the mean *down* (30.2 → 28.3 Hz) while removing the stall entirely
  (3 of 55 samples below 15 Hz → 0 of 58). A tool that reported only the mean
  would have scored the fix as a regression. See SPEC-009.
* **`face.capture_ms` / `face.infer_ms` exist because a sum cannot be
  attributed.** Added 2026-08-31 and they immediately overturned a figure three
  documents asserted — the detection cycle had been "measured" at ~170-200 ms by
  dividing into `loop_hz`, which also contains the eye render. Timed directly it
  is 48-91 ms. **A subtraction is not a measurement**; if a number matters,
  instrument the thing itself.

Diagnostics that changed a conclusion, and what they cost:

* `dualtest` — ~200 lines, one flash. Proved both panels work and killed a
  "both panels are dead" diagnosis that had stood for a day.
* `i2ctest` — proved the bus healthy in seconds, against a standing belief that
  it was broken.
* `vibtest` — the pull-up/pull-down test separated "not connected" (follows the
  internal resistor) from "actively driven" (does not), which no amount of
  staring at telemetry could.
* `imuaxis` — a level owl reading pitch +172.8° identified the inverted mounting
  immediately.
* `camtest` — mean brightness dropping 122 → 53 when a hand covered the lens is
  what turned "the camera probably works" into "the camera works".

## Falsified

* **"A diagnostic that reports a number is a good diagnostic."** The most
  expensive artefact in this project was a probe that read panel IDs over a bus
  with **no data-out line**. It always returned `0x00`, and that was read as
  evidence. A diagnostic whose output cannot possibly differ between the healthy
  and broken case is worse than none — it manufactures false certainty.
  Hence R-011.2: state up front what each outcome would mean, and if two
  different states produce the same reading, the test is not finished.
* **"Idle levels prove presence."** Reading a pin HIGH with an internal pull-up
  enabled says nothing about what is attached. Twice this was read as "the device
  is connected" (the vibration pin, then the camera's SCCB lines). The pull-*down*
  variant is the one that discriminates.
* **"A compile-time flag is enough to make a minimal image."** `powerprobe`
  existed as a `#if POWER_PROBE` branch in `main.cpp`'s `setup()`/`loop()`, and
  its env carried no `build_src_filter` — so it built the **entire** firmware,
  constructed every peripheral object, and only skipped the work at runtime with
  an early `return`. Its whole purpose is to answer "does the rail hold under a
  LIGHT load?", which that image cannot answer: the load was never light. Split
  into `src/powerprobe.cpp` with a filter on 2026-08-27; the image went from the
  full firmware to 292 KB flash / 22.5 KB RAM. A diagnostic's *build* has to be
  as isolated as its claim (R-011.2 again, one level down).

## Acceptance

1. `pio run` builds all environments; the diagnostic ones in seconds.
2. Each diagnostic's output states which conclusion each outcome supports.
3. Every host tool runs unchanged on macOS and on the Raspberry Pi.

## Open

* `[ ]` **`runHardwareCheck()`'s `vcc_mv` reads a pin that is not the rail.**
  `analogRead(5)` against a comment claiming GPIO5 is the XIAO's 5 V sense,
  while `config.h` defines `LCD_SCK 5` — GPIO5 is the shared LCD clock. Measured
  0 on hardware 2026-08-31. Note the shape of this defect: the line previously
  read GPIO7, which is not ADC-capable and *also* always returned 0, so moving
  it produced no change in symptom and looked like a fix. A constant 0 here has
  now survived two different wrong pins. `-e powerprobe` is the reading to
  trust. See BACKLOG Step 6 item 5.

* `facelab/` — a standalone project used to prove esp-dl before touching the
  firmware. It has served its purpose but is kept as a reference for the
  dual-framework setup. Its build artefacts (~1.2 GB) are gitignored.
