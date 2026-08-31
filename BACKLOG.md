## >>> NEXT SESSION — priority order <<<

Everything below is ordered by **dependency**, not by size. The order is the
useful part of this list: three of these tasks invalidate the work of another if
done in the wrong sequence.

**Why this order.** Any work that opens the owl's head comes before any
calibration, because the BNO055 *and* the camera both live in there — disturbing
the IMU invalidates both its calibration and `IMU_HEADING_OFFSET_DEG` (75.4°,
which encodes 70.8° of measured mounting rotation). Calibrating first and then
opening the head means calibrating twice. Likewise the camera extension goes in
before any detection tuning: remounting changes the framing and may change
`CAM_VFLIP`, so tuning against a camera that is about to move is wasted effort.
Navigation cannot be verified until the heading is trustworthy.

Steps 0–5 need the owl. Step 6 needs nothing but a laptop.

**Read the two orders separately.** The Step numbers are a *dependency* order and
stay fixed — renumbering them would destroy the information they carry. What is
worth doing *next* is a different question, and as of 2026-08-30 the two have
come apart:

| step | state | why |
|---|---|---|
| 0, 1, 4 | done | ticked above |
| **3** | **BLOCKED on a part** | the BNO055 must be physically replaced; nothing else unblocks it |
| 2 | ready, but cheap and low-value alone | a 2-minute confirmation, best folded into the next flash |
| 5 | blocked *through* 3 | navigation cannot be verified without a trustworthy heading |
| **4b** | **ready, needs the owl** | gaze smoothing (unblocked 2026-08-31), square crop, field of view |
| **6** | items 1–3 **done and flashed** | item 4 stays blocked (no Flask here); items 5–6 open |

So the dependency chain 0→5 is stalled at 3 until the IMU arrives. Step 6 was
called "the rainy-day list" when it was written; while the part is in the post it
became the main road, and on 2026-08-31 its two laptop-only items were done and
the firmware one was flashed and verified on the owl — supervisor test doubles
(item 2) and the `main.cpp` split (item 1). Step 2 was folded into that same
flash and is ticked.

**Step 6 item 2 — inference on the second core — is done and on the owl**
(2026-08-31). The eyes now render continuously at 28-29 Hz while tracking, with
no stall, and a gaze target arrives every 181 ms instead of every 382. **The
next real step is Step 4b's gaze smoothing**, which that unblocked: the four
filter variants rejected earlier were all sampled at 6 fps, so retry them before
inventing a fifth.

That work also overturned a number three documents asserted — the inference is
48-91 ms, not ~170-200 ms; the old figure had been divided out of `loop_hz`,
which charged the detector for the eye render. See SPEC-009 Falsified.

**4b's remaining items need the owl.** One of its four
items got a partial answer for free: the HAPPY→AWE arc was observed in
telemetry, so what remains there is a judgement call about how it *looks*, not
whether it fires. The 4-tap OTA path was the one thing the verification flash
could not reach; it was tapped by hand on 2026-08-31 and works (Step 6 item 2).

> **"Step" here, "Phase" elsewhere — they are different things.** These Steps are
> this session's task order. The `Phase 1..4` you will see in `rpi-brain/brain/`
> comments and in `specs/014-speech.spec` are the *speech feature's*
> implementation phases (VAD → ASR → reactions → auto-sleep) and have nothing to
> do with the list below. Do not renumber either to match the other.

---

### Step 0 — Get the refactored firmware onto the owl  `[x]` DONE 2026-08-28/29

**Superseded by events, ticked 2026-08-30.** This said "none of it has run on
hardware"; that stopped being true two days later. The 2026-08-27 refactor has
since been flashed and measured repeatedly on the owl — `39c44bd` (centre crop),
`8b0ec83` (dirty-row flush) and `35ece9b` (cascade thresholds) were each verified
against a real face, and Step 4's numbers below (4.93 hits/s, 55/55 frames) are
firmware readings, not simulation.

Left as a step rather than deleted because the flashing rule it carries is the
one this project has already broken once:

**NEVER `firmware.factory.bin` at 0x0** — it spans past 0x9000 and erases NVS.
That is how the IMU calibration was lost on 2026-08-26. Flash the pieces:
`bootloader.bin` @0x0, `partitions.bin` @0x8000, `boot_app0.bin` @0xf000,
`firmware.bin` @0x20000.

**Still genuinely unseen**: the web UI's diagnostics line — heading, GPS fix,
`imu.cal` counters, pulse/hit totals. Those seven fields were parsed for the
first time on 2026-08-27 and have only ever been fed synthetic frames. 30
seconds with `web.enabled: true` next time the owl is up.

### Step 1 — Fit the camera extension cable  `[x]` DONE 2026-08-28

All head-opening work happens here, before anything is calibrated.

Parts ordered: 24-pin 0.5 mm **type A** FFC cable (50 mm) plus a 24-pin 0.5 mm
female-to-female coupler. Chain: camera tail → coupler → extension → Sense
socket. Adds ~7 cm.

**Done when**: `pio run -e camtest -t upload` still reports `0x3C antwortet`,
ID `0x3660`, ~110 mean brightness with a clear drop when the lens is covered.

**Verified 2026-08-28**: `0x3C` answers, 16-bit `0x300A/B` → `0x3660`, driver
init OK, 10 frames at 320×240 in ~27 ms each, mean 132–133 with min 59 / max 244.
Covering the lens: 133 → 29, recovering to 134. No image degradation, so
`CAM_XCLK_FREQ_HZ` stays at 20 MHz. `CAM_VFLIP=1`/`CAM_HMIRROR=0` re-confirmed
from actual JPEGs (`camsnap`), unchanged by the remount.

**The extension changed the AIM, and that cost the rest of the session.** The
camera ended up pointing at the ceiling; detection scored 0 in 31.5 s while every
number above looked perfect. Re-aiming fixed it. **Add "take a snapshot and look
at it" to this step** — brightness cannot catch a mis-aimed camera. See Step 4
and SPEC-008.

**If the image degrades** (torn frames, low contrast, ID read failing): the extra
~7 cm plus two connector transitions are marginal for a 20 MHz DVP bus. Drop
`CAM_XCLK_FREQ_HZ` in `config.h` from 20 to 10 MHz — halves the data rate and is
far more tolerant of length.

**If the camera moved at all, RE-MEASURE the mounting orientation.** The models
only find upright faces and `CAM_VFLIP` was determined empirically
(normal 0 hits / vflip 57 / hmirror 2 / 180° 10). Getting this wrong makes
detection impossible at any threshold — see SPEC-008.

**Note whether the BNO055 shifted.** If it did, Step 3 also needs
`IMU_HEADING_OFFSET_DEG` re-measured, not just the calibration redone.
The head *was* opened on 2026-08-28, so treat `IMU_HEADING_OFFSET_DEG` (75.4) as
suspect until re-measured. Telemetry that day read `imu.cal
{sys:0 gyro:3 accel:1 mag:0}` with `restored:false`, consistent with the erased
NVS — Step 3 is still outstanding and now needs the offset re-derived too.

### Step 2 — Validate the reassembly  `[x]` DONE 2026-08-31

Do this *before* investing in calibration: it catches a connector disturbed in
Step 1 in seconds, rather than after twenty minutes of figure-eights.

    # build_flags: -DHARDWARE_CHECK=1, flash, read one JSON line, flash back
    pio run -e xiao_esp32s3 -t upload

One `{"type":"hardware_check",...}` line reports both LCDs, PCA9685, GPS,
BNO055, vibration and camera; `i2c_found` lists every address that answered.
Set the flag back to `0` and re-flash.

**Expect `0x10` and `0x40` only — `0x28` missing is the KNOWN fault, not a new
one.** This step used to say "expect `0x10`, `0x28`, `0x40`", which Step 3 below
makes impossible: the BNO055 holds SCL low and has to be replaced. Read a
missing `0x28` here as confirmation of Step 3, and re-diagnose only if `0x10` or
`0x40` also vanish — that would mean the bus is down for a *different* reason.

**Result 2026-08-31**, folded into the Step 6 item 1 verification flash:

    {"type":"hardware_check","vcc_mv":0,"psram":true,"lcd_left":true,
     "lcd_right":true,"servo":true,"gps":true,"imu":false,"vibration":false,
     "camera":true,"i2c_found":"[10,40]","all_ok":false}

`i2c_found` is exactly `[10,40]` as predicted — Step 3 confirmed, bus otherwise
healthy. PSRAM, both LCDs and the camera all pass, so nothing in Step 1's
reassembly was disturbed. Two fields need reading carefully rather than as
faults:

* **`vcc_mv: 0` is a bug in the check, not a dead rail.** It reads
  `analogRead(5)`, and its comment claims GPIO5 is the XIAO's 5 V sense — but
  `config.h` has `LCD_SCK 5`. GPIO5 is the shared LCD clock in this harness, so
  the reading can never mean anything. Pre-existing (the line is unchanged since
  before the split); logged as Step 6 item 5. Use `-e powerprobe` for a real
  rail reading.
* **`vibration: false`** is the crude idle-level probe
  (`digitalRead(VIBRATION_PIN) == HIGH`), which depends on where the SW-420's
  sensitivity pot sits — see the open-collector note in AGENTS.md. The pulse
  counter is the trustworthy signal, and it read a correct `0` at rest in the
  same session.

### Step 3 — The BNO055 holds SCL low and must be replaced  `[ ]`

**BLOCKED on hardware. Diagnosed 2026-08-28; recalibration cannot start until the
part is swapped.**

The chip ACKs its address after every power-up and then, on the first register
access, **holds SCL low indefinitely** — measured at over **two seconds**, against
a few hundred microseconds of legitimate clock stretching. While it is down it
drags the whole bus with it, so GPS (`0x10`) and the PCA9685 (`0x40`) disappear
too, and the owl used to boot straight into `ERROR`.

Everything else on that bus was eliminated by measurement first — wiring, ground,
supply, pull-ups, the XIAO's pins, our firmware, the bus clock, and the two other
devices. See `specs/005-i2c-bus.spec` **Falsified**, which lists five hypotheses
that each died to a measurement; do not re-run them.

**Mitigations already in the tree** (so the owl is usable meanwhile):
* `I2C_TIMEOUT_MS` 250 ms — the master no longer abandons a frame at 50 ms and
  strands the bus
* A silent IMU is a warning, not a boot failure — camera, face detection and eyes
  all work without it
* `lib/SoftI2C` + `-e softimu` — bit-banged bus that addresses all four devices
  cleanly. It cannot rescue a slave that never releases SCL, but it is the right
  foundation if the replacement also proves marginal on ESP32-S3

**When the new part arrives**: `pio run -e softimu -t upload` first — it names the
failing protocol step rather than just "not found". Then the calibration
(`kalibrieren.py`, figure-8 for `mag` BEFORE the static poses for `accel`), and
because the head has been open, `IMU_HEADING_OFFSET_DEG` (75.4) needs
**re-deriving**, not just the calibration redone.

### Step 4 — Detection rate: RESOLVED 2026-08-29  `[x]`

**The face was too small in frame. Nothing else was ever wrong.**

`run()` rescales its input to the model's resolution, so detection depends on the
face's size **relative to the frame**, not in pixels. At 1 m a head is ~40 px of
320 and is never found; at 30 cm it is ~62 px and the rate jumps. Frames are
sharp and well exposed in both cases. `CAM_DETECT_CROP_DIV` (`config.h`) now
gives the detector the centre crop, doubling relative size for free.

**Second cause, found by reading a working robot's source** (Adafruit's MEMENTO
shoulder robot): the cascade's two stages need *different* thresholds. MSR
proposes candidates, MNP refines them, and esp-dl defaults both to 0.5 — so the
proposal stage was discarding faces the refiner never got to see. MSR is now 0.1,
MNP stays 0.5.

Measured at 1 m, same spot and light, `facelab` as control:

| | full frame | + centre crop 2× | + MSR 0.1 |
|---|---|---|---|
| facelab | 0.0 % | 59.4 % | — |
| firmware, hits | 0.00/s | 1.84/s | **4.93/s** |
| firmware, hit rate | 0 % | 31 % | **100 %** |
| firmware, state | `idle` | `interacting` | `interacting`, 55/55 frames |

**False positives: zero** over 184 attempts in an empty room, state stayed
`idle` throughout. Precision holds because the reported score is still MNP's.

**A larger frame does NOT help** — VGA gives the model the same picture at the
same field of view. The cost of the crop is field of view; `CAM_DETECT_CROP_DIV 1`
restores the old behaviour.

Fixed along the way, both found by chasing this:

* **`gaze_x`/`gaze_y` overflowed to 2³²** whenever the face was left of centre —
  `fb->width` is `size_t`, so the subtraction was unsigned. The eyes snapped hard
  right instead of tracking. Present since face detection was ported.
* **The eyes barely moved even when correct.** Travel was 8 px on a 160 px panel
  against a realistic `gaze_x` of ±0.34 — two pixels. Now gain 2.5 and 18 px
  travel, with `EYE_GAZE_SIGN_X` for the mirror question, determined on hardware.

**The `facelab` baseline that framed this whole investigation was wrong.**
"Near-every-frame detection" was recorded at bring-up under unstated conditions.
Re-measured 2026-08-29 in a normal room it scored **5 %** on the full frame —
*worse* than the firmware's 21 %. Two days of "it must be the integration" rested
on a comparison that did not hold. Re-measure a control in the same sitting as
the thing it is controlling for.

**Loop performance, also done** (was the suspected cause, and was not):
partial flush plus a slower blink cadence took the main loop from **4.3 Hz to
29 Hz** while interacting, and attempts from 4.36/s to 5.93/s. Worth having, but
it moved hits/s not at all — the crop did that.

**Still open, and now low priority:** stale frames
(`CAMERA_GRAB_WHEN_EMPTY` vs `CAMERA_GRAB_LATEST`). facelab grabs in a tight loop
with the freshest frames obtainable and still scored 5 % before cropping, so this
is a weak lead.

**Thresholds were never the lever** — 0.5 is the library default and measured
right. The v1 knobs (`resize_scale`, `top_k`) do not exist in esp-dl v3.

### Step 4b — Polish the tracking, now that it actually tracks  `[ ]`

Small, laptop-plus-owl items left over from 2026-08-29. None is blocking.

**Smooth the gaze.** `[ ]` **UNBLOCKED 2026-08-31** — Step 6 item 2 has landed.
This is now the highest-value item that needs the owl. Tried on hardware
2026-08-31 in four variants — Adafruit's deadband + proportional step, a
time-constant filter, a distance-dependent snap/smooth split, and a pure
threshold — and **every one was rejected as either jittery or laggy**. The
reason was only found afterwards: the eyes rendered at **6 fps while tracking**,
so the filter itself was being sampled six times a second. No control law
produces smooth motion at that frame rate.

**What changed.** The eyes now draw continuously at **28-29 Hz while tracking**
with no stall (0 of 58 samples below 15 Hz, against 3 of 55 before), and a new
gaze target arrives every **181 ms** instead of every 382 ms. So an
interpolation between two targets now has ~5 rendered frames to work with
instead of one, which is the condition every one of those four variants was
missing. **Retry them before inventing a fifth** — the earlier rejections say
nothing about the laws themselves, only about the frame rate they ran at.

The measured signal, for whoever picks this up: the tremble is ~1.1 px of iris
travel per sample, and a slow head movement is also ~1 px per sample. At the old
5 Hz those were literally the same signal; at 5.5 targets/s they still are, so
**the separation has to come from the interpolation between targets, not from
filtering the targets themselves**. Adafruit's `SERVO_HYSTERESIS 2` is wrong
here regardless: it was chosen for a servo with a far larger range, and this
iris travels only +-18 px total, so 2 px is wider than the entire signal at a
normal seating distance.

Same law applies to the head servo when wired.

**Try a square detection crop.** The crop is 160×120, still 4:3; the model's
input is square, so some of it is wasted on rescale. A 160×160 centre crop is
square and captures more vertical extent. Five-minute experiment.

**Look at the new INTERACTING arc.** `[x]` ANSWERED 2026-08-31, and it was
wrong. The greeting read on hardware as *"a horizontal line before the happy
eyes"*: `HAPPY` carries `botRise 62` against a `halfH` of 42, so its centre
column is only **22 px** tall — a deep crescent, which renders as a shut eye.
That is the very reason sustained `HAPPY` was replaced by `AWE` the day before;
the one-second greeting kept it and so kept the defect. Now `SURPRISED`
(40x47, no botRise/topSag/slant = 94 px of open eye, the widest in the table) —
"I just noticed you" is a widening, not a squeeze. Still worth a look on the
panel, but the reported defect is fixed.* Changed 2026-08-30: a ~1 s `HAPPY` burst on
entry (`INTERACT_GREET_MS`) and then `AWE` held, instead of `HAPPY` pinned for
the whole state — sustained `HAPPY` has `botRise 62` and reads as an eye
squeezed shut while the owl is visibly tracking you. Builds clean on all 11
envs; **nobody has seen it on a panel.** `AWE`'s row was widened the same day
from `35×41` to `{42, 42, 32}` — +12 % area over `NEUTRAL` was not a visible
change, +40 % and round-instead-of-oval should be. Two judgement calls that
still need eyes on hardware rather than arithmetic: whether 1 s is the right
burst length, and whether the widened blob crowds the bezel at full gaze
deflection (it reaches 69.5 px of the 80 px disc, against 68.9 for the shipped
`SURPRISED`). `tools/preview_eyes.py` renders the whole sheet from the same
`SHAPES[]` if you want to compare before flashing. See SPEC-004.

**Decide the field of view.** `CAM_DETECT_CROP_DIV 2` means the owl sees only
the middle half of its camera view. Nobody has measured what cone the owl
actually needs — that depends on where it ends up sitting.

### Step 5 — Verify navigation on hardware  `[ ]`

Needs Step 3 done (a trustworthy heading) and a sky-view GPS fix.

`navigation.aim_sign` in `rpi-brain/config.yaml` has **never** been verified
against hardware. Start a navigation to a saved place; if the head turns the
wrong way, flip the sign. That is the one value the geodesy cannot derive on its
own — it encodes which way the head servo turns for a positive angle and whether
IMU yaw increases clockwise.

### Step 6 — Software backlog (no owl required)  `[ ]`

**Ordered by impact**, re-measured 2026-08-30 — not by dependency, because only
item 3 has a real prerequisite. Each entry states what it costs to *verify*: on
this list that varies far more than the work itself does.

1. **`src/main.cpp` split** — `[x]` DONE 2026-08-31, **flashed and verified**.

   934 lines became four files: `main.cpp` (202, wiring only), `behavior.cpp`
   (state machine + OTA mode), `protocol.cpp` (the NDJSON contract),
   `hardware_check.cpp` (built only under `-DHARDWARE_CHECK=1`), sharing
   `include/owl.h`. Both duplications went with it: the 8× ack
   serialize-and-println is now `sendJson()`/`sendAck()`, and the two I2C scan
   loops are one `i2cScan()` template taking the reporting as a callback. See
   SPEC-001 Decisions.

   The hardware check is no longer linked into the production image:
   **−3,844 bytes flash, −168 bytes RAM**.

   **Verified on the owl, 2026-08-31** (all 11 envs build; then flashed):

   | check | result |
   |---|---|
   | boot + telemetry | `{"type":"boot"}`, state settles to `idle`, eyes render |
   | all 8 commands ack | pass, key order intact (`type` first) |
   | `gaze` sends no ack | pass — still deliberately silent |
   | unknown expression | falls back to NEUTRAL and still acks |
   | malformed JSON | `{"type":"error","msg":"invalid_json"}` |
   | face → `interacting` | confidence 0.999+, gaze tracks, `face.total` climbs |
   | HAPPY → AWE arc | seen in telemetry: `eye` flips ~1 s after entry |
   | `nav active=true` | → `navigating`, `navigation` block carries the angle |
   | NAV self-timeout | back to `idle` ~5 s after the last refresh |
   | `sleep` / `wake` | → `sleeping` (eye `sleeping`) → `idle` |
   | `HARDWARE_CHECK=1` | one JSON line, `i2c_found:"[10,40]"` — see Step 2 |

   **No performance regression, measured against a control in the same
   sitting** (the `facelab` lesson): the pre-refactor firmware was rebuilt,
   flashed and measured back to back on the same owl.

   | build | cadence min/mean/max | loop_hz min/mean/max |
   |---|---|---|
   | pre-refactor (934 lines) | 510 / **581** / 657 ms | 5.5 / 31.6 / 44.2 |
   | refactored | 506 / **549** / 644 ms | 6.4 / **33.0** / 44.4 |

   The refactored build is marginally *faster*, which is within noise — the
   honest claim is "no regression", not "an improvement". An earlier 12 s sample
   read 604 ms and looked like a regression against the ~535 ms in the docs; it
   was just too short. Do not read a single short cadence sample as a signal.

   ~~**Still unseen:** the 4-tap OTA entry / 1-tap exit path.~~ Exercised with
   physical taps on 2026-08-31 while verifying item 2; it works end to end. See
   item 2.

2. **Inference on the second core.** `[x]` DONE 2026-08-31, **flashed and
   measured on the owl**, owner in front of the camera for both readings.

   `FaceDetector_Detect()` now runs in a FreeRTOS task pinned to **core 0**
   (`src/vision.cpp` + `include/vision.h`); the Arduino loop keeps core 1
   (`CONFIG_ARDUINO_RUNNING_CORE=1`) and only copies the latest result.
   `FACE_DETECT_INTERVAL_MS` is back to **100**.

   **Before and after, one sitting, same distance, face in frame:**

   | | before (blocking, interval 300) | after (core 0, interval 100) |
   |---|---|---|
   | `loop_hz` min / mean / max | 10.8 / 30.2 / 44.1 | 15.6 / **28.3** / 61.7 |
   | samples below 15 Hz | **3 of 55** | **0 of 58** |
   | new gaze target | every 382 ms | every **181 ms** |
   | attempts | 2.6 Hz | 6.0 Hz |
   | hit rate | 100 % | 92 % |

   At idle the mean moves too: 40.8 → 55.8 Hz (the loop's `delay(16)` ceiling is
   62.5).

   **Read the sequence, not the mean — the mean went DOWN.** Before:
   `11 29 31 35 32 42 26 41 33 17 36 31 40 26 42 33 …`. After:
   `29 29 29 27 27 29 29 29 29 21 29 29 25 28 28 …`. The stall is what was
   removed, and a mean cannot show a stall. The small drop in the mean is
   expected: the iris now moves twice as often, so the dirty-row flush has more
   to do every frame. That is the trade working, not a regression.

   **The unknowns are settled, and one of them overturned a documented number.**

   * **Capture vs CPU: it is 100 % CPU.** `face.capture_ms` is **0 in every
     sample** — `fb_count=2` with `CAMERA_GRAB_WHEN_EMPTY` always has a frame
     ready, so `esp_camera_fb_get()` never waits.
   * **The inference is 48-91 ms, not ~170-200 ms.** Timed directly via
     `face.infer_ms`: 48 ms with nobody in frame, 48-91 ms (mean 66) with a
     face. A control build — the old blocking call on core 1, same
     instrumentation, nobody in frame — read 47-50 ms (mean 48). **The
     `facelab` 48 ms was right all along.** The ~170-200 ms figure was never
     timed; it was divided out of `loop_hz`, which also contains
     `eyes.render()`, so the detector was charged for the eyes. See SPEC-009
     Falsified — a subtraction is not a measurement.
   * **Cost of the move: ~3 ms of inference** (48 → 51 ms, cache and PSRAM
     contention with the renderer). Cheap for what it buys.
   * **Stack: 5,584 bytes free of 8,192** (`VISION_TASK_STACK`), reported
     continuously as `face.stack_free` because the failure mode is a silent
     overflow mid-inference. PSRAM needed nothing new — the crop buffer and
     esp-dl's own allocations are unchanged, just made from another task.
   * **Watchdog: no trigger.** The task always yields at least one tick, so
     IDLE0 is fed. No `Task watchdog` line appeared in any capture, which
     matters doubly here: it would land in the NDJSON stream the RPi parses.
   * **Core 0 contention during OTA:** none, as expected — `vision::setEnabled`
     pauses the task on entry to `UPDATE` and clears the published result, so
     WiFi has core 0 to itself.

   **Verified on the owl after flashing:** boot line + `Face detection enabled`;
   all 8 commands ack with `gaze` still silent; unknown expression falls back to
   NEUTRAL and still acks; malformed JSON → `invalid_json`; `nav` →
   `navigating` with the angle, self-timing-out to `idle` at 5.3 s; `sleep` /
   `wake`; `HARDWARE_CHECK=1` still emits its one JSON line
   (`i2c_found:"[10,40]"`); all 11 envs build.

   **The 4-tap OTA path was exercised too, with physical taps** — the one path
   this change could plausibly disturb, since WiFi and the vision task share
   core 0, and the path BACKLOG had carried as unseen since the `main.cpp`
   split. It works: 4 taps → `state=update`, eye `update`,
   `{"type":"update_mode","ssid":"RobotOwl-Update",…,"url":"http://192.168.4.1/update"}`,
   SoftAP up with `wifi driver task … core=0`; 1 tap → `{"type":"update_mode_end"}`
   and detection resumes (`face.detected` true again 7 s later). `face.detected`
   went false for the whole update session, which is `vision::setEnabled(false)`
   clearing the published result as intended.

   **Two things fell out of that first run, both pre-existing, neither caused
   by this change — see the new item 6 below.**

   **Owner's verdict on the panel: "it feels really *alive* now."** Worth
   recording next to the numbers, because the numbers alone were ambiguous —
   the mean render rate went *down*. What was bought is the absence of the
   stall, and it reads on the finished head exactly as the measurement predicted.

   **Follow-on:** Step 4b's gaze smoothing is unblocked by this and is now the
   best next thing to do with the owl.

3. **Consolidate the three supervisor test doubles.** `[x]` DONE 2026-08-31.
   One `FakeSupervisor` in `tests/stubs.py`; the local `StubSupervisor` in
   `test_navigation.py` and `test_navigation_webui.py` are gone, along with the
   `nav_start`/`nav_stop` monkey-patch in `test_navigation_speech.py`. All 179 then-existing
   tests still pass. They had already drifted — only the web-UI copy carried the nav
   delegation, and it lacked the `navigation is None` guard the real supervisor
   has. See SPEC-012 Decisions for the one parameter (`last`) that needed care.

4. **`render_template` instead of `render_template_string`** in `web_ui.py:159`.
   **Still blocked, and the block is the entire point.** It has to be done on a
   machine that actually has Flask; this one does not (`import flask` fails),
   which is precisely the condition under which the split was originally made
   and why the template-folder path has never once been executed. Doing it here
   would repeat the original mistake with more confidence behind it. The Jinja
   in `brain/templates/index.html` is already compatible, and the comment at
   `web_ui.py:97` records the state.

   Lowest impact of the three: it changes no behaviour anyone can observe, it
   only retires a documented oddity.

5. **The hardware check's `vcc_mv` can never be right.** Found 2026-08-31 while
   verifying item 1. `runHardwareCheck()` opens with
   `analogRead(5)` and a comment claiming *"XIAO S3 senses the 5 V input on
   D4 = GPIO5 = ADC1_CH4"* — but `config.h` has `#define LCD_SCK 5`. GPIO5 is
   the **shared LCD clock** in this harness, so the reading is meaningless; it
   returned `0` on hardware.

   This is the *second* time this line has been wrong in the same way: the
   comment records that it used to read GPIO7, "which is NOT ADC-capable on the
   S3, so it always returned 0". It was moved off GPIO7 onto a pin that was
   already spoken for, and the symptom (a constant 0) is identical, which is
   why the fix looked like it had worked.

   Cheap and low-risk to fix, but **decide what it should read first** — on the
   XIAO S3 the ADC1 pins are GPIO1–10 and this harness has most of them
   committed. `-e powerprobe` already gives a trustworthy rail reading from a
   minimal image, so the honest options are to point this at a genuinely free
   ADC1 pin, or to drop the field and let the JSON say nothing rather than say
   `0`. Reporting a wrong number is worse than reporting none — a `vcc_mv` of
   `0` reads as a dead rail.

6. **Entering update mode dumps ~40 non-JSON lines onto the protocol port.**
   `[ ]` FOUND 2026-08-31, on the first run of the 4-tap path. Pre-existing and
   unrelated to the core-0 work, but nobody had ever watched this transition.

   `-DCORE_DEBUG_LEVEL=0` silences the *Arduino* core's logging. It does not
   gate ESP-IDF component logs, and `WiFi.softAP()` emits about forty of them
   (`I (346384) wifi:wifi driver task…`, `phy_init`, `esp_netif_lwip`, …)
   straight onto the USB CDC port that carries the NDJSON protocol. The RPi's
   `_handle_message` logs `Failed to parse message` for each one. Nothing
   breaks, but this is exactly the noise `CORE_DEBUG_LEVEL=0` exists to
   prevent, and the port is the machine-to-machine link.

   Fix is a one-liner in `sdkconfig.defaults`
   (`CONFIG_LOG_DEFAULT_LEVEL_NONE=y`, or `esp_log_level_set("*", ESP_LOG_NONE)`
   at boot) — but check first whether any diagnostic env depends on IDF logs;
   they run in plain Arduino mode, so probably not. Remember
   `sdkconfig.defaults` is only read when no `sdkconfig.<env>` exists.

   One more line in the same capture: `cam_hal: EV-VSYNC-OVF`, once, as the AP
   came up. Expected rather than alarming — the camera keeps filling its ring
   buffer while the paused vision task stops calling `esp_camera_fb_get()`, so
   with `CAMERA_GRAB_WHEN_EMPTY` it overflows. It recovered by itself and
   detection resumed normally.


6. **Mechanical assembly** — enclosure, servo attachment for ears/head/wings,
   LCD bezels. Not blocked by anything here, and not really comparable to the
   above: it is the only item that changes what the owl physically is.

---

### Reference: per-subsystem verification

Not a phase — the lookup table for "is this subsystem still good?". Every tool
already exists. Reach for the specific one when a phase above says something is
wrong; run the lot only after mechanical work.

| what | how | expected |
|---|---|---|
| eyes | `pio run -e dualtest -t upload` | both panels, distinct colours, they swap |
| I2C bus | `pio run -e i2ctest -t upload` | 0x10, 0x28, 0x40 all answer |
| IMU | `pio run -e imuaxis -t upload` | level owl → roll/pitch near 0; counters 3/3 |
| vibration | `tools/klopftest.py` | quiet = 0 edges; 4 taps enter OTA mode |
| camera | `pio run -e camtest -t upload` | OV3660 at 0x3C, brightness responds |
| supply rail | `pio run -e powerprobe -t upload` | ~2500 mV steady; watch for a sag under 2000 |
| firmware | flash `xiao_esp32s3`, watch telemetry | no LCD errors, ~535 ms cadence, `idle` at rest |
| face | hold a face in front | `face.total` climbing, state → `interacting`, eyes `happy` |
| eye designs | `esp32-s3-sense/tools/preview_eyes.py` | all 26 expressions render, no flashing needed |
| detection health | `tools/trefferquote.py` — **owner IN FRONT of the camera** | ~100 % hit rate at a normal seat, gaze target every ~190 ms, face >= 40 px |
| RPi brain | `cd rpi-brain && python3 tests/run_tests.py` | 184 tests pass |
| firmware/RPi drift | `cd rpi-brain && python3 tools/gen_expressions.py --check` | "up to date" |

**Reminder for every flash**: never `firmware.factory.bin` at 0x0 — it wipes NVS
and with it the IMU calibration. `bootloader.bin` @0x0, `partitions.bin` @0x8000,
`boot_app0.bin` @0xf000, `firmware.bin` @0x20000.

---

# Robot Owl — Backlog

Cross-session working backlog. Update the status as items get done so any
session can pick up where the last one left off.

Status legend: `[ ]` open · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Refactoring pass — 2026-08-31 (code smells)

Six small cleanups, no behaviour change intended. All 11 PlatformIO envs build,
the `-DHARDWARE_CHECK=1` variant builds, 184 RPi tests pass, `check_docs.py`
passes.

**Flashed and verified on the owl, 2026-08-31** (four pieces separately, never
`firmware.factory.bin` at 0x0; `partitions.bin` ends at 0x8c00, clear of NVS):

| check | result |
|---|---|
| boot, no LCD error | `System ready`, state settles to `idle`, eye `neutral` |
| missing BNO055 is non-fatal | warns and continues; **not** `ERROR` (Step 3 part still absent) |
| `imu` block absent, not zeroed | R-010.4 working — absence is visible, not guessed |
| `loop_hz` at idle | min/mean/max **29.2 / 58.2 / 61.8** against the `delay(16)` ceiling of 62.5; sequence is flat 62s with a few dips, no stall |
| `face.infer_ms` | **48 ms** with no face — corroborates the figure this pass propagated, on the same owl |
| `face.capture_ms` | 0, as documented: the camera is never waited on |
| `face.attempts` | 198 in 32 s ≈ 6.2 Hz, matching the documented 6.0 |
| `face.stack_free` | 5584 of 8192 bytes — ~2.6 KB used, healthy margin |
| camera | PID `0x3660`, `vflip=1`, detection enabled |
| **`returnToIdle()` on the NAV timeout** | `nav active=true` → `navigating` + eye `searching` + `navigation{active,angle:30}`; refresh stopped → back to `idle` + eye `neutral` after ~5 s (`NAV_TIMEOUT_MS`) |
| all 8 commands ack | pass, incl. unknown expression → `neutral` and still acked |
| `gaze` sends no ack | pass — still deliberately silent |
| `sleep` / `wake` | → `sleeping` (eye `sleeping`) → `idle` |
| malformed JSON | `{"type":"error","msg":"invalid_json"}` |

**Not verified, and why.** Face *hits* need the owner in front of the camera
(`face.total` was 0 — nobody there, which is not a fault). The `DETECTING`/
`INTERACTING` timeout paths through `returnToIdle()` need a face to enter the
states at all, so only the `NAVIGATING` one was exercised — it is the same three
lines. And `Sensors::getImu()`'s `ImuData data{}` is unreachable while
`_imuReady` is false, so that one initialiser waits on the replacement BNO055;
the `GpsData`/`VibrationData` equivalents are exercised every telemetry frame and
report correctly.

**The falsified inference figure was still asserted as current in four places.**
`FaceDetector.h`/`.cpp` had been corrected when the number was overturned; these
had not, and one of them was reasoning from it: `config.h`'s note above
`FACE_DETECT_INTERVAL_MS` justified the value with "die Inferenz braucht ohnehin
~180 ms". Also `src/main.cpp`, `src/vision.cpp` and
`rpi-brain/brain/serial_handler.py` (`FaceDetection.capture_ms`). All now say
48-91 ms (mean 66), directly measured. This is the failure mode AGENTS.md warns
about by name — the figure reached *six* files, not three, and the two that were
fixed made the remaining four look fixed too.

**Every telemetry frame was JSON-parsed twice.** `SerialHandler._handle_message`
decodes the line to dispatch on `type`, then called `parse_telemetry(line)`,
which decoded the same bytes again — on the foreground read loop. Split into
`_telemetry_from_dict(data)` (the builder) with `parse_telemetry(str)` kept as
the string entry point the tests use. Verified by counting `json.loads` calls
through `_handle_message`: 2 before, 1 after, same parsed frame.

**Three timeout paths out of DETECTING / INTERACTING / NAVIGATING** each
repeated `transitionTo(IDLE)` + `setGaze(0,0)` + `setCenter()`, in two different
orders — which reads as though the order mattered somewhere. Now one
`returnToIdle()` in `behavior.cpp`'s anonymous namespace. Deliberately NOT used
by `setNavTarget(active=false)`: that path recentres only `CH_HEAD` and leaves
the ears and wings alone.

**`web_ui.py`'s `_ear_wing_angle()` returned `CENTER` 38 lines before `CENTER`
existed** — legal only because a function body is not evaluated until call time,
and it read as a bug on every pass. Moved below the constants. `api_head` used a
bare `0` where its two siblings used `CENTER`.

**`Sensors.cpp`: an orphaned comment and three fragile initialisers.** The
NVS-calibration comment sat above the *vibration* section, 15 lines from the
`imuPrefs` it describes. And `ImuData data = {0, 0, 0, false, 0, 0, 0, 0, false}`
was a positional 9-field aggregate init that had to be kept in the struct's field
order by hand: correct as written, silently wrong the moment anyone inserts a
field into `ImuData`. Now `ImuData data{}` (likewise `GpsData`, `VibrationData`).

**`api_telemetry` builds its payload from the dataclass now**, not from 50
hand-copied field accesses. `dataclasses.asdict(t)` walks the whole frozen tree,
so the page can no longer fall behind the protocol. The old version had drifted
in BOTH directions: it omitted `timestamp`, `uptime`, `loop_hz`, the `update`
block and all five face diagnostics (`attempts`, `capture_ms`, `infer_ms`,
`stack_free`, plus the firmware's `navigation` echo), while sending
`face.confidence`/`gaze_x`/`gaze_y` that no part of the page reads.

Three deliberate deviations from a plain asdict, all in `_telemetry_payload`'s
docstring: `eye`/`uptime` keep their wire names (asdict would emit
`eye_expression`/`uptime_ms` and silently blank both displays); `update.password`
is dropped, because this route has no authentication and a password-shaped field
is not something to add as a side effect of a refactor; and the firmware's
`navigation` moves to `navigation_esp32`.

**That last one was a latent name collision nobody had noticed.** `navigation`
means two different things on the two sides — the firmware's echo (`active`,
`angle`: is the head really held where we told it?) and the RPi controller's
status (`target`, `bearing`, `distance_m`, `aim`), which is what the Navigate
card renders. The hand-written payload only ever had the controller's copy, so
assignment order hid it; asdict brought both into one dict and made the clash
visible.

**Note on why this is NOT the inbound `_*_FIELDS` case.** It looks like the same
defect and it is not: R-010.5 ("every field sent must be parsed") is an INBOUND
rule, and the page is entitled to render a subset. There was no live bug — the
template read exactly what the API sent. The justification here is only that it
is less code that cannot go stale.

**The German comments in the firmware are INTENDED, and this is now written
down.** A translation pass was proposed during this refactoring and **the owner
ruled it out on 2026-08-31: it stays exactly as it is.** The policy and the
reasoning now live in `AGENTS.md` ("A note on language"), which is the single
owner; `specs/000-index.spec` points at it instead of restating it.

Recorded here only because the proposal happened and the measurement is worth
keeping: it is **~350 German comment lines across 11 files**, not the "~100
across six" first estimated — `config.h` 113 of 385, `FaceDetector.cpp` 61 of
81, `vision.cpp` 27 of 31, `Eyes.cpp` 30 of 87.

**Two documents had it backwards.** `AGENTS.md` said "keep new code/docs
English" and `specs/000-index.spec` said "documentation and code comments are
English" — both written when it was nearly true, both quietly inverted since,
and between them they made a deliberate choice read as accumulated drift. That
is the same rot pattern as the "Current state" block this file's own guidance
warns about: a convention stated once, true when written, never re-checked.
**Do not reopen this as a cleanup.** There is no partial version to do either —
the "mixed-language files" slice (`main.cpp`, `protocol.cpp`, `GC9D01.cpp`,
`Sensors.h`) was part of the same rejected proposal.

---

## Refactoring pass — 2026-08-27

The 2026-08-26 tree was committed and then refactored. Eight commits, no
behaviour change intended anywhere; all seven PlatformIO envs build and the RPi
suite went from 104 to 179 test methods.

**Dead code removed:** `Eyes::fillTriangle`, `Eyes::markDirty`,
`GC9D01::drawRect`, `GC9D01::drawCircle`, `Sensors::_vibBurstStart`, a discarded
`bno.getSensor()` call, two unused `SerialHandler` callback fields,
`LCD_SPI_HOST`. `FW_VERSION` is derived from the MAJOR/MINOR/PATCH triplet
instead of being a hand-synced fourth copy of the string.

**Bugs fixed:** an ack log line with two format placeholders and one argument
(raised a logging error on every ack at debug level); the UPDATE-mode
announcement logged twice from two layers; `read_loop` taking the whole brain
down when a telemetry callback raised; `angleToUs` ignoring `SERVO_MAX_US` and
assuming symmetric servo travel; the `powerprobe` env compiling the *entire*
firmware (297 KB now, was 3.28 MB) which made it useless as the light-load
comparison it exists to be.

**Protocol single-sourced:** seven fields the firmware had always sent were
never parsed on the RPi — `imu.cal.{sys,gyro,accel,mag,restored}`,
`vibration.pulses`, `face.total`. All of them exist to make an invisible failure
visible, and all were dropped. Now parsed, and shown in the web UI along with
heading and GPS fix (neither of which the page exposed at all). Field
transcription is a table, so a new firmware field is one row plus one dataclass
field. Frames are frozen.

**Expression list generated** from `NAMES[]` in `lib/Eyes/Eyes.cpp` via
`rpi-brain/tools/gen_expressions.py`. The two hand-kept mirrors had drifted
(26 / 23 / 24 names), and `config.yaml`'s 36-line `expressions:` block turned out
to be read by *nothing*. Drift is now a test failure.

**`WIRING.md` was actively wrong** and is the document you follow with a
soldering iron: two CS lines swapped, the left DC on an unused pin, and the
vibration sensor listed on **D3 — the right eye's DC**. Corrected against
`config.h`, which is authoritative. Nothing was ever built from the bad table.

### Deliberately NOT done

* **`src/main.cpp` split — parked 2026-08-27, unparked 2026-08-30, DONE
  2026-08-31.** It was deferred because it alters loop structure while loop
  timing was the leading suspect for sporadic detection. That suspect was
  cleared when Step 4 was resolved on 2026-08-29 (the face was too small in
  frame; loop timing was innocent), which is what released it. What was built,
  and what still has to be checked on hardware, live in **Step 6 item 1**; the
  reasoning lives in SPEC-001.
* **`facelab/` kept.** It is the known-good control for the sporadic-detection
  bug: same model, same camera, same thresholds, near-every-frame detection.
  Deleting it would destroy the A/B reference for an open investigation. (It does
  carry ~1.2 GB of gitignored build output on disk if you want the space.)
* **Leaflet not vendored.** The tiles come from a second remote host, so a
  locally-bundled Leaflet renders a working map widget full of blank grey
  squares — worse than the current honest "Map unavailable" message. Real offline
  maps need a region tile pack or a tile server on the Pi: a feature with a real
  cost, not a dependency cleanup. Rationale is in the code above the CDN tags.

### Open follow-ups from the pass

* `[ ]` **Three supervisor test doubles** — `tests/stubs.py` `FakeSupervisor`
  plus a local `StubSupervisor` in each of `test_navigation.py` and
  `test_navigation_webui.py`. Every new supervisor method has to be added in all
  three; that is how it was noticed. Consolidate into one.
* `[ ]` **The new diagnostic panel has never seen real hardware.** The parsing
  is well covered by tests, but the web UI rendering of the calibration
  counters, GPS fix and pulse/hit totals has only ever been fed synthetic
  frames. Eyeball it on the first run with a live owl — the calibration display
  is meant to make the figure-8 dance easier, which is the point of the whole
  change.

---

## Face detection PORTED into the real firmware — 2026-08-26

`[env:xiao_esp32s3]` now runs as **`framework = arduino, espidf`** with esp-dl.
Verified on hardware: camera OV3660 up, `FaceDetector: Sensor PID 0x3660,
vflip=1 hmirror=0`, `Face detection enabled`, and the full behaviour chain works
— `face.total` climbing, confidence 0.61-0.87, state going
`idle -> detecting -> interacting` and the eyes turning `happy`.

### platformio.ini restructure

The diagnostic envs must NOT be dragged into espidf mode (their build time would
go from ~1.5 s to minutes). So the common Arduino settings moved into an
`[arduino_base]` section; the diagnostics extend that, and only the firmware env
adds espidf. Verified: `pio run -e dualtest` still takes ~7 s.

### Traps hit on the way, all fixed

1. **Dead `main/CMakeLists.txt`** — a committed leftover from an ancient
   ESP-IDF attempt, registering `main.c`/`gc9d01.c`/`pca9685.c` (C files that
   never existed in this C++/Arduino project). Ignored in Arduino mode, fatal in
   espidf mode. Directory removed.
2. **`src/CMakeLists.txt` globbed `src/*.*`** — which now also swept up the new
   `idf_component.yml` as a source file. Restricted to `*.c`/`*.cpp`.
3. **picolibc vs newlib.** The build died compiling `espressif__cbor`
   ("unknown type name 'cookie_io_functions_t'") because it needs
   `fopencookie()`, which picolibc lacks. `CONFIG_LIBC_NEWLIB=y` is now explicit
   in sdkconfig.defaults.
4. **`sdkconfig.defaults` is only read when no `sdkconfig` exists.** A stale
   committed `sdkconfig.xiao_esp32s3` (with picolibc) silently overrode
   everything. After changing sdkconfig.defaults you MUST delete the generated
   `sdkconfig.<env>` or the change does nothing.
5. **Image too big**: 3.23 MB against 3.00 MB slots. The 1.875 MB `spiffs`
   partition was unused (no SPIFFS/LittleFS/FFat anywhere in the code), so it
   was folded into the OTA slots -> `0x3F0000` (4.03 MB) each, 78 % used.
   `nvs` deliberately stays at 0x9000/0x6000 so the BNO055 offsets survive.

### DO NOT flash firmware.factory.bin at 0x0

It spans 0x0..0x33FDB0 and therefore **overwrites the NVS partition at 0x9000**,
with 0xFF padding that reads as erased flash. Doing that during this session
wiped the BNO055 calibration (`restored: false`, `mag: 0`) and it had to be
redone. Flash the pieces separately instead:

    0x0      bootloader.bin
    0x8000   partitions.bin      (3072 B, fits the one sector, does not reach NVS)
    0xf000   boot_app0.bin
    0x20000  firmware.bin

### FaceDetector rewritten, interface unchanged

`lib/FaceDetector/FaceDetector.cpp` now targets esp-dl v3 (`HumanFaceDetect`,
`run(dl::image::img_t)`). `FaceResult_t` and `FaceDetector_Init/Detect/Deinit`
kept exactly as they were, so `main.cpp` needed no changes at all. Camera pins
moved out of the .cpp into `config.h` (`CAM_PIN_*`), which is this project's
authority for pins. `CAM_VFLIP 1` is mandatory — see the camera section above.

`face.total` (cumulative detections) and `FACE_HOLD_MS` (400 ms) were added for
the same reason `vibration.pulses` exists: detection is intermittent, the state
machine sees every hit at ~60 Hz, but telemetry samples an instantaneous flag at
2 Hz and missed **39 of 39** frames while the owl was demonstrably in
INTERACTING. Never expose an intermittent event as a bare instantaneous flag.

### Open

Detection is far more sporadic in the firmware than in `facelab` (6 hits in 25 s
versus near-every-frame). Suspects: the main loop exceeds
`FACE_DETECT_INTERVAL_MS` because eye rendering costs ~61 ms, so fewer attempts
happen; or the camera frame is stale under `CAMERA_GRAB_WHEN_EMPTY` with
infrequent grabs. Worth measuring during the next hardware check.

## Face detection WORKS with esp-dl v3 — 2026-08-26

Proven end to end on hardware in `esp32-s3-sense/facelab/`, an **isolated
PlatformIO project** (deliberately not another env in the main project — see
below). Measured: **48 ms inference, ~21 frames/s**, detection scores 0.58-1.00.

### How it was made to work

1. **`framework = arduino, espidf`** — Arduino compiled as an ESP-IDF component.
   This is what makes managed IDF components available; the normal Arduino
   framework mode uses prebuilt libraries and cannot take new components.
   The pioarduino platform supports it (`Frameworks: ['arduino', 'espidf']`,
   `framework-espidf` = esp-idf v5.5.5, plus a `component_manager.py` builder).
2. **esp-dl was already there.** `espressif__esp-dl` appears in the platform's
   component list — it is only missing from the *prebuilt Arduino libs*, which
   is why Core 3.x looked like it had dropped it. `espressif/human_face_detect`
   pulls it in.
3. **`CONFIG_FREERTOS_HZ=1000` is a hard requirement.** The Arduino core checks
   it in its CMakeLists and aborts: "esp32-arduino requires
   CONFIG_FREERTOS_HZ=1000 (currently 100)". The IDF default is 100.
4. **PSRAM works, and better than before.** In espidf mode the octal PSRAM is
   configured via sdkconfig (`CONFIG_SPIRAM_MODE_OCT`), not
   `board_build.memory_type`. It came up first try with full diagnostics
   ("Found 8MB PSRAM device, Speed: 80MHz, SPI SRAM memory test OK"). The old
   "PSRAM ID read error" never appeared. This was the main risk of the switch
   and it is retired.

### THE actual cause of "detects nothing"

**The camera is mounted VERTICALLY FLIPPED in the owl's head.** Measured across
all four orientations with a face held in front:

| orientation | hits |
|---|---|
| normal | **0** |
| **vflip** | **57** |
| hmirror | 2 |
| vflip+hmirror (180 deg) | 10 |

These models only find UPRIGHT faces — a flipped face is not a face to them, so
without `sensor->set_vflip(s, 1)` detection can NEVER work, at any threshold or
lighting. Same story as the BNO055 being mounted bottom-PCB-up: the whole
assembly sits inverted in the head.

Byte order was also settled by measurement, not guesswork: **RGB565BE** (62 hits
vs 2 for LE). And the score thresholds were NOT the problem — the default 0.5 is
fine, measured scores are 0.58-1.00, mostly above 0.9. Lowering them to 0.2
while hunting was useful but is only an invitation for false positives in
production.

### esp-dl v3 API (completely different from v1)

`HumanFaceDetectMSR01` does not exist any more. Read from the real headers in
`facelab/managed_components/`, not from docs:

```cpp
HumanFaceDetect(model_type_t = ..., bool lazy_load = true);   // MSRMNP_S8_V1
std::list<dl::detect::result_t> &run(const dl::image::img_t &img);
Detect &set_score_thr(float thr, int idx);   // idx 0 = MSR, 1 = MNP

dl::image::img_t     { void *data; uint16_t width, height; pix_type_t pix_type; }
dl::detect::result_t { int category; float score;
                       std::vector<int> box;      // lx, ly, rx, ry
                       std::vector<int> keypoint; }
```

`lazy_load` defaults to true, so the constructor reports 0 ms / 0 bytes and the
model actually loads on the first `run()`. That is normal, not a failure.

### Why facelab is a separate project, not an env

`framework = arduino, espidf` needs its own `sdkconfig.defaults`, and an
`idf_component.yml` in `src/` is visible to **every** env of a project. The
main project's seven working envs — including the hard-won PSRAM config — must
not be exposed to that. Build times also differ by two orders of magnitude
(~280 s cold, ~8 s incremental, versus ~4 s for the Arduino-only envs).

### Still to do — DONE, this section is history

> **Superseded.** The port happened later the same day: the main env runs
> `framework = arduino, espidf`, `FaceDetector.cpp` is written against the v3
> API, and `FACE_DETECTION_ENABLED` is **1**. See "Face detection PORTED into the
> real firmware" above, which is the current state. The stale README claims about
> the Arduino SDK shipping esp-dl were fixed on 2026-08-27.
>
> Kept because the reasoning below explains *why* the espidf migration was
> needed at all.

Port this into the real firmware. That means migrating the main env to
`framework = arduino, espidf` and rewriting `lib/FaceDetector/FaceDetector.cpp`
against the v3 API. `FACE_DETECTION_ENABLED` stays 0 until then.

## Camera brought up 2026-08-26 — and it is an OV3660, not an OV2640

The camera works: driver init OK, sensor identified, frames captured, and it
demonstrably *sees* (mean brightness dropped 122 -> 53 the instant a hand covered
the lens). `pio run -e camtest` is the bring-up tool.

**The sensor is an OV3660** (PID 0x3660 at SCCB address 0x3C), not the OV2640
this project claimed in README, config.h and FaceDetector.cpp. Corrected
throughout. Nothing functional had to change -- the pin map and
RGB565/QVGA settings are sensor-independent and the esp32-camera driver handles
the difference -- but the wrong name cost real time here, because:

**OV3660 uses 16-BIT register addresses.** Its ID lives at 0x300A/0x300B. Probing
it the OV2640 way (8-bit register 0x0A) returns garbage, so a healthy sensor
looks absent. If you ever hand-probe this camera, use 16-bit reads.

**The separately bought module does not work in the XIAO Sense socket.** Both it
and the Seeed original are labelled OV3660, but the bought one produces NO ACK on
0x3C at all while the original answers immediately and initialises first try.
Socket, FPC connector, B2B connection, SCCB bus and driver are therefore all
proven good. Cause not established -- FPC contact side or signal order are the
candidates -- but it is the module, not the owl.

Two diagnostic traps recorded so we do not fall in again:
* `ESP_ERR_NOT_SUPPORTED` + "Detected camera not supported" from esp32-camera
  does NOT mean "camera found but model unknown". `camera_probe()` leaves
  `out_camera_model = CAMERA_NONE` and returns the same error when *nothing*
  answers, so it is also the no-camera-at-all message.
* Pull-ups on SDA/SCL (GPIO40/39) say NOTHING about the camera: they sit on the
  Sense expansion board. Verified by unplugging the camera entirely -- the
  pull-ups stayed. This session briefly concluded "the module is connected" from
  that reading, which was wrong.
* GPIO39/40 have no ADC on the ESP32-S3 (ADC1 = GPIO1-10, ADC2 = GPIO11-20).
  `analogReadMilliVolts()` there does not just fail, it takes the HAL down with
  a LoadProhibited panic.

Also removed: `CAMERA_ENABLED` in config.h, which was defined but never read.

### Face detection is still blocked — RESOLVED, this section is history

> **Superseded 2026-08-26.** esp-dl *is* available, just not in the prebuilt
> Arduino libraries: pulling it in as a managed IDF component
> (`src/idf_component.yml` + `framework = arduino, espidf`) was the answer, and
> the v3 API replaced `HumanFaceDetectMSR01` entirely. Detection now runs at
> 48 ms / ~21 fps. Kept because "the library is simply gone" was the wrong
> conclusion and the next person may reach for it again.

`FaceDetector.cpp` needs `human_face_detect_msr01.hpp` from esp-dl. **esp-dl is
not present in Arduino core 3.x** (verified: nothing matching `*human_face*` or
`libdl.a` anywhere in ~/.platformio). It shipped with core 2.0.x, and this
project moved to pioarduino 3.3.11 / IDF 5.5 to fix PSRAM. README "Decision #8"
still claims the SDK provides it -- that rationale is stale.
`FACE_DETECTION_ENABLED` stays 0 until this is resolved. Options, none started:
  1. Detection on the RPi over USB CDC (OpenCV is already in requirements.txt;
     USB CDC ignores the baud setting so QVGA JPEG streaming is realistic).
     Costs the "ESP32 owns behavior" property of Decisions #7/#8.
  2. Revert to Arduino core 2.0.x to regain esp-dl -- risks the PSRAM
     regression that forced the upgrade, and PSRAM now feeds both the LCD
     framebuffers and the camera.
  3. Integrate esp-dl for IDF 5.x by hand; different API, uncertain effort.

## Vibration sensor (SW-420) — RESOLVED 2026-08-26

The sensor and the wiring were fine; the firmware could not read this class of
sensor at all. Symptom: telemetry reported `vibration: {detected: true, count: 1}`
on a motionless owl and the state machine sat permanently in DETECTING.

**What the sensor actually does.** Measured with `pio run -e vibtest`: an
AZDelivery SW-420 on D2/GPIO3 emits BURSTS OF PULSES, not a level. 3252 edges in
28 s of tapping, median gap between edges 1 ms (63 % of gaps <= 2 ms). One tap
is a burst of ~66 edges over ~510 ms when polled at 5 kHz, and 200-600 edges
when counted by an interrupt that catches every one. Idle: 0 edges in 20 s.

**Why the old code could never work** — two independent structural faults:
1. `getVibration()` did a single `digitalRead()` and was called once per
   telemetry tick (~2 Hz). Sampling a ~1 kHz chattering signal at 2 Hz is a coin
   flip.
2. The debounce required the raw level to be STABLE for 100 ms before accepting
   a change. A real burst chatters for ~510 ms, so the condition never held and
   `detected` stayed frozen at whatever the very first sample happened to be.

**Now**: an `IRAM_ATTR` ISR counts edges on CHANGE, and `Sensors::updateVibration()`
turns counts into state. Counting on CHANGE is polarity-agnostic, which also
retires the unanswerable active-high/active-low question for this module.

Two follow-on bugs found while fixing it, both worth remembering:
* The evaluation must run ONCE PER MAIN LOOP (~60 Hz), not per telemetry tick.
  At 2 Hz the interval between evaluations (535 ms) always exceeded
  `VIBRATION_BURST_GAP_MS`, so every burst spanning two calls was counted twice
  — measured 8 counts for 5 taps. The gap test compares against the last PULSE,
  which only advances if the evaluation runs often enough.
* Both callers (`updateState()` at 60 Hz and `sendTelemetry()` at 2 Hz) used to
  call `getVibration()`, and each drained the pulse counter — they stole edges
  from each other. `getVibration()` is now a side-effect-free getter.

`vibration.pulses` (raw ISR edge count since boot) is now in telemetry on
purpose: with a chattering sensor it is the only way to tell "nobody tapped"
from "the sensor is dead", and it earned its place twice during this debugging.
Constant at rest, jumps by hundreds per tap, continuously rising while
motionless = interference or the pot set too sensitive.

Verified on hardware: 20 s at rest -> `detected` false in 34/34 frames, counter
0, state `idle` (it used to hang in `detecting`). 5 taps -> counter exactly 5.
3 further taps -> counter 5 -> 8, exactly as predicted.

**Do not read the module's varying DO impedance as a hardware fault.** It
measured ~989 ohm / 75 mV in one run and ~5.6 kohm with mid-range voltages in a
later one, and this session first mistook that for a marginal solder joint. It
is not: the user was turning the sensitivity pot between those measurements,
which is the whole explanation. The LM393 on the module has an OPEN-COLLECTOR
output, so the pot moves it between two very different electrical states:
  * pot at maximum sensitivity -> comparator permanently triggered, output
    transistor saturated. At the ~73 uA the ESP32's internal pull-up supplies,
    that reads as ~75 mV / ~1 kohm.
  * pot toward less sensitive -> comparator off or near threshold, output high
    impedance, so the pin simply FOLLOWS whichever internal pull is enabled --
    which is exactly the 367 / 842 / 1629 mV (pullup / none / pulldown) that was
    measured. A node that tracks the pull direction is an open-collector output
    switched off, not a bad connection.
The lesson is the recurring one in this project: before inventing an independent
hardware cause, check what changed between the two measurements.

Pot is now tuned so a real tap triggers but lifting the owl does not. Verified:
60 s untouched -> 0 raw edges, 0 counter change, `detected` false in 107/107
frames, state `idle` throughout.

**The 4-tap OTA sequence is now hardware-validated end to end** (2026-08-26):
four taps entered UPDATE, the SoftAP came up, a phone connected and loaded the
`/update` page, and a single tap returned the owl to normal operation.

Timing, for the record. The window is not a total budget but a per-gap rule:
each tap must follow the previous within `UPDATE_TAP_GAP_MS` (1500 ms), so four
taps span at most 4.5 s. There is also an implicit LOWER bound that is not in
any constant: a new tap only counts after `VIBRATION_BURST_GAP_MS` (250 ms) of
quiet measured from the previous burst's LAST PULSE, and a tap rings for ~510 ms
— so gaps below ~0.75 s merge two taps into one. Usable band ~0.75-1.5 s, i.e.
tap about once per second. That was judged tight enough to be worth widening,
but it was hit first try on hardware, so 1500 ms stays.
`tools/klopftest.py` shows each tap's exact interval live if it ever needs
re-tuning.

## Eye expressions — reference-sheet set implemented 2026-08-26

All 21 moods from the reference sheet plus the 3 owl-only states now render, and
all 24 were verified on hardware (set over serial, drawn, and echoed back in
telemetry's `eye` field).

They are NOT 21 hand-drawn shapes: `Eyes::drawBlob()` renders any of them from a
row of `SHAPES[]` in `lib/Eyes/Eyes.cpp` — a superellipse plus `topSag`,
`botRise`, `slantIn`, `slantOut`, `yOff`, `asymH`. Retune a mood by editing its
row. `tools/preview_eyes.py` parses that same table and renders an HTML contact
sheet with identical maths, so shapes can be judged without a flash cycle.

Two hazards removed along the way:
* `main.cpp` kept a second, hand-maintained `exprNames[]` array indexed by the
  enum. Every new expression would have shifted it and made telemetry report the
  wrong name. Names now come from `NAMES[]` via `Eyes::nameOf()`, and both
  tables are `static_assert`-checked against `EyeExpression::_COUNT`.
* `SLEEPY` was squashed twice — once by its shape and again by a hard-coded
  lid override in `currentOpenness()`. The lid override is gone.

Open question for the user: the slant direction on angry/furious vs
worried/sad_down could not be read reliably off the reference image, so the
emotional-standard convention was used (angry lowers the INNER brow, worried and
sad lower the OUTER one). Swapping `slantIn`/`slantOut` in those four rows flips
it if the reference actually differs.

RPi side updated to match: `web_ui.py` `EXPRESSIONS` and `config.yaml`
`expressions:`. Note an override only lasts `EXPRESSION_OVERRIDE_MS` (3 s)
before the firmware's own state machine reclaims the eyes — re-send faster than
that to hold a mood.

## I2C bus / sensors — RESOLVED 2026-08-26

The bus was never broken. A bit-banged scan (bypassing the ESP-IDF driver
entirely) plus a Wire scan both found all three devices — 0x10 PA1010D,
0x28 BNO055, 0x40 PCA9685 — on the configured pins GPIO1/GPIO2, at 100 kHz and
at 400 kHz, with SDA/SCL idling HIGH. `pio run -e i2ctest` re-runs that check.

What the symptoms actually were:

1. **The `ESP_ERR_INVALID_STATE` (259) burst at boot is benign.** In the IDF 5.x
   `i2c_master` driver that code is how a plain slave NACK is reported.
   `Adafruit_BNO055::begin()` soft-resets the chip and then polls its ID
   (`while (read8(CHIP_ID) != BNO055_ID)`); every poll before the chip finishes
   rebooting NACKs and logs two lines. ~18 of them over ~490 ms, then it works.
   A previous session read this as a dead bus. `CORE_DEBUG_LEVEL=0` now keeps
   this noise off the NDJSON stream the RPi parses (diagnostic envs keep logs).
2. **~700 ms telemetry ticks were a runaway GPS read loop.**
   `Adafruit_GPS::available()` is hardcoded to `return 1` in I2C mode, so
   `while (GPS.available()) GPS.read();` never exits. Now a fixed 128-byte
   budget per call, parsing sentences as they complete. Cadence: ~535 ms.
3. **All three IMU axes were mislabelled.** `getVector(VECTOR_EULER)` reads from
   `BNO055_EULER_H_LSB_ADDR`, so the block is Heading, Roll, Pitch — i.e.
   `orientation.x` is YAW, `.y` is ROLL, `.z` is PITCH. The code had
   pitch←roll, roll←heading, yaw←pitch, which is why a level owl reported
   `roll: 359.9`. Navigation consumes `imu.yaw` as a compass bearing
   (`geo.aim_angle`), so it was steering the head with a pitch angle.
4. **Fusion mode was IMUPLUS (accel+gyro, no magnetometer).** No absolute north,
   and `calibrated` requires `mag >= 3` so it could never become true — while
   `navigation.py:162` refuses to aim until it is. Now `OPERATION_MODE_NDOF`.
5. **The sensor is mounted bottom-PCB-up**, 180 deg about its Y axis. Measured
   with `pio run -e imuaxis` (owl standing level: gravity (-0.53,-1.21,-9.71),
   7.7 deg off-axis, Euler pitch +172.8). Corrected in hardware via the
   BNO055 AXIS_MAP registers, placement P7 — verified: pitch +172.8 -> +8.1,
   roll -3.1 -> +2.9. The ~8 deg residual is physical mounting slop.
6. **Calibration offsets now persist** in NVS (`owl-imu`/`bno-offsets`) and are
   restored at boot, so the figure-8 dance is a one-off instead of a
   per-boot prerequisite. Save/restore round-trip verified on hardware.
   Note `getSensorOffsets()` only works when fully calibrated, and restoring
   offsets makes the chip report 3/3/3/3 immediately — so never store bogus
   offsets, and clear them with
   `esptool --before usb-reset erase-region 0x9000 0x6000` if you do.

Telemetry gained `imu.cal.{sys,gyro,accel,mag,restored}` so calibration progress
is visible. Additive only; the RPi parser ignores unknown fields.

**Still to do (needs the user, not code):** run the calibration dance once —
hold still for gyro, hold stationary in ~6 orientations for accel, slow figure-8
for mag. Until `mag` reaches 3, `yaw` is not a usable compass bearing and
navigation will not engage. `IMU_HEADING_OFFSET_DEG` in config.h is still 0 and
unverified: it aligns yaw with the beak direction and needs one measurement
against a known bearing.

## Eyes / shared SPI bus — RESOLVED 2026-08-26

Both eyes now run on the shared SPI bus and display independent images. The
fault was never in the hardware. Four separate bugs, in the order they bit:

1. **No chip-select was ever asserted.** `GC9D01` drove no CS in any write path.
   A single panel still worked because CS was handed to `SPIClass::begin()` as
   the peripheral's hardware SS pin and toggled automatically; the shared-bus
   path begins with `SS = -1`, so with two panels *neither* was ever selected
   and both ignored the bus. Fixed: software CS around every transaction
   (`startWrite`/`endWrite` in `lib/GC9D01`).
2. **`Eyes::renderEye()` never called `flush()`.** Frames were drawn into the
   PSRAM framebuffer and dropped. Fixed.
3. **`GC9D01::fillCircle()` only filled a quarter of each circle.** It walked
   one Bresenham octant and painted columns from `cx±r` inward to `cx±r/√2`
   only, so every circle rendered as two crescents flanking a 1 px centre line.
   Replaced with a scanline fill.
4. **`Sensors::getGps()` spun forever.** `while (GPS.available()) GPS.read();`
   never terminates when the I2C bus is wedged — `available()` keeps reporting
   data that `read()` cannot consume. This hung the whole main loop on the first
   telemetry tick, freezing the eyes on their first frame. Now bounded.

Also corrected: `config.h` had the left and right eyes' CS/DC pairs swapped
(verified on hardware by colouring each panel differently).

**Do not re-run "read the panel ID" diagnostics.** The panel connector is 8 pins
(VCC, GND, DIN, CLK, CS, DC, RST, BL) with no SDO/MISO — nothing can ever be
read back, so an ID probe always returns `0x00`. A previous session read that as
"both panels dead" and spent a day chasing a hardware fault that did not exist;
`EYES_DEBUG_HANDOFF.md` documented that wrong conclusion and has been deleted.

Removed with it: `src/pinmap.cpp` + `[env:pinmap]` (the ID-probe sweep),
`src/tfttest.cpp` + `[env:tfttest]` + vendored `lib/TFT_eSPI/` (an A/B test
against Waveshare's driver, question now answered), and the `*.bak` files.
`[env:dualtest]` (`src/dualtest.cpp`) is kept as the shared-bus regression test:
it drives both panels with distinct colours and patterns from a minimal sketch.

Open, unrelated: the I2C bus (BNO055 / PA1010D / PCA9685) reports
`ESP_ERR_INVALID_STATE` on every read, and a telemetry tick costs ~700 ms
waiting on it. Out of scope for the eyes session.

## Eyes rendering

- [x] **UPDATE spinner eyelid clipping** — the green spinner ring (radius
  `SCLERA_R+5`) was drawn *after* the eyelids, so the full-width eyelid
  `fillRect` overdraw the top/bottom of the ring. Fixed: `renderEye()` now
  draws the UPDATE overlay *before* the eyelids (other overlays still after).
  `lib/Eyes/Eyes.cpp`
- [x] **ERROR red-eyes state** — `State::ERROR` was an empty stub ("Flash red
  eyes (not implemented yet)"). Added `EyeExpression::ERROR` + `COLOR_ERROR`
  (red) and a red-X face in `drawExpressionOverlay()`; `main.cpp` ERROR case
  now calls `applyExpression(ERROR)` + `servos.setCenter()`.
- [x] **Dead iris gradient** — `drawIris()` drew concentric outline rings that
  were immediately covered by the flat `fillCircle`, so the "gradient" never
  showed. Removed the dead loop.
- [x] **Asymmetric gaze range** — RESOLVED: intentional. Vertical travel is
  deliberately smaller than horizontal (6 vs 8 px) for a "natural" look (real
  eyes move less up/down). Documented in `Eyes.cpp`; do NOT make it symmetric.
- [x] **Blink speed param unused** — wired up. `blink(speed)` (1=fast…5=slow)
  now sets `_blinkSpeed`, which paces both the animation duration and the
  eyelid openness curve in `renderEye()`. The RPi `blink` command's `speed`
  field is now honored.

## Module integration

- [x] **Firmware version in telemetry** — added `FW_VERSION` to `config.h` and
  `doc["fw"]` to `sendTelemetry()`. RPi `Telemetry.firmware` parses it and the
  supervisor logs it once (and on change, to confirm an OTA took).
- [x] **RPi-side update tooling** — DROPPED (not needed). Updates are done by
  joining the owl's SoftAP from a phone/laptop hotspot and flashing via the
  browser `/update` page. No RPi push tooling required.
- [ ] **`webServer.stop()` doesn't unregister `/update`** — handled with the
  `updateServerReady` one-shot guard (`main.cpp`), so it works, but the handler
  stays registered forever. Low priority; revisit if a second `WebServer` or a
  path change is ever added.
- [x] **4-tap trigger hardware-validated 2026-08-26** (entry, SoftAP, web page,
      exit all confirmed) — SW420 debounce / tap-counting
  in `Sensors.cpp` is written but untested on the real sensor. First thing to
  verify once the mechanical build is wired.

## Face detection

- [~] **Tune esp-dl MSR01** — first-pass tuning done: model knobs moved to
  `config.h` (`FACE_SCORE_THRESHOLD 0.5`, `FACE_NMS_THRESHOLD 0.3`,
  `FACE_TOP_K 5`, `FACE_RESIZE_SCALE 0.3`) plus a post-filter
  `FACE_MIN_CONFIDENCE 0.5` gate that stops low-confidence flicker from
  flipping the state machine. A `config.h` shim was added in `lib/FaceDetector/`
  so the lib sees the project `config.h`. **Still needs on-hardware
  validation**: if the owl under-detects (ignores you), lower the thresholds
  toward 0.3; if it false-triggers on hands/pictures, raise them.
- [ ] **RPi face-detection fallback** — explicitly NOT needed (ESP32 does it).
  Only revisit if on-device detection is later disabled.

## Speech recognition (RPi-side)

Step 1 (skeleton), Step 2 (mic + VAD + ASR), Step 3 ("last heard" in web
UI) and Step 4 (autonomous sleep + wake-on-speech) code is **done and
unit-tested on the Mac** (38 tests, stubbed serial/supervisor/mic/Whisper).
See `specs/014-speech.spec`. The RPi `Speech` class captures a USB mic,
runs an energy VAD, and on a gated utterance transcribes with **faster-whisper**
(`small`, int8 on CPU — the size/precision pair faster-whisper recommends for a
Pi 4; the CTranslate2 engine, no torch, ~4× faster than openai-whisper) and drives a *temporary* reaction (expression/gaze/audio) via
the existing override path — no firmware change.

**Setup is now a single interactive command:** `sudo ./setup.sh` runs a config
wizard (serial port, web UI, speech, auto-sleep — useful defaults, Enter to
accept) and pre-downloads the Whisper model, so the first live transcription is
instant. `sudo ./setup.sh --non-interactive` keeps the bundled defaults for
unattended installs.

- [~] **Verify Step 2 + 3 + 4 on hardware** — enable `speech.enabled: true` (+
  `web.enabled: true` for the "last heard" line, + `supervisor.auto_sleep.enabled: true`
  to test autonomous sleep), plug the USB mic, and confirm:
  - Step 2/3: speak "toll" / "wer bist du" / "lass das" with a face in frame → correct
    eyes + owl call and the web UI's Live status card shows the transcript + "Ns ago"; the
    4.5 s cooldown is respected; 10 min of TV/room noise with no face → zero reactions;
    `journalctl -u robot-owl-brain` shows no serial stall. Tune `speech.vad_threshold`
    against the real mic's noise floor (default 0.02) and `speech.window_s`.
  - Step 4: leave the owl alone (no face / no taps / no speech) → after
    `supervisor.auto_sleep.after_s` it goes to sleep on its own; then tap it or show a
    face → it wakes immediately; say the wake keyword → it wakes.
- [x] **Step 3 — "last heard" in web UI** — `Speech` records `last_heard_at`;
  `WebUI` takes an optional `speech=` and `/api/telemetry` exposes
  `last_heard: {text, at}` (omitted when speech is off, so the payload is
  unchanged otherwise). The page renders a "heard *…* (Ns ago)" line. 4 unit
  tests cover the seam; all 25 tests pass. (Hardware check folded into the item above.)
- [x] **Step 4 (revised) — autonomous sleep on inactivity + wake on speech** —
  *No command forces sleep; no firmware change.* The RPi watches the existing
  telemetry (face / vibration) + its own speech events; after `supervisor.auto_sleep.after_s`
  with **no** interaction trigger it sends the **existing** `sleep` command (disabled by
  default). The owl's *wake* is mostly already in the firmware (it self-wakes on a
  face or a vibration); the one new thing is **wake-on-speech** — the RPi sends the
  existing `wake` command when it hears the user while the owl is asleep. All RPi-brain:
  `supervisor.py` (inactivity timer → `sleep`), `speech.py` (wake-exception gate → `wake`),
  `config.yaml` (`auto_sleep.*`). **Code done + 13 unit tests** drive the real
  `Supervisor`/`Speech` against stubs (see `specs/014-speech.spec` for the
  full design + the conservative wake-gate decision). Hardware check folded into the
  "Verify on hardware" item above (add: leave the owl alone → it sleeps on its own after
  `after_s`; say the wake keyword → it wakes).

## Navigation — "guide me home" (live compass)

The owl points its head at a **named destination** and keeps re-aiming from live
GPS + IMU heading — a live compass. All the math is on the RPi (it already parses
the GPS fix + IMU yaw); the ESP32 just holds the head at the angle it's sent.
Full design + math + open questions: `specs/013-navigation.spec`.

**Implemented + tested on the Mac (no hardware):**
- [x] **RPi core** — `brain/geo.py` (bearing/haversine/wrap/aim, pure),
  `brain/locations.py` (name→{lat,lon} JSON store), `brain/navigation.py`
  (controller: start/stop/on_telemetry, rate-limited re-aim, four exit paths).
- [x] **Speech start/stop** — `nav_triggers` ("bring mich nach …") checked
  *before* the reaction clusters so a nav sentence isn't stolen by "wie"; a
  dedicated stop keyword ("danke", "stopp die navigation") that is **exempt
  from the cooldown and the face-gate** and works even while the owl is asleep.
- [x] **Web UI** — Places card (add/remove/list + OpenStreetMap/Leaflet map
  picker, no API key) + Navigate card (start/stop + live bearing/distance/aim)
  + `/api/locations*` and `/api/nav/start|stop` endpoints.
- [x] **Firmware** — 8th state `NAVIGATING` (holds the head at the RPi's
  compass angle; `SEARCHING` eyes; 5 s no-refresh self-timeout → IDLE) + the
  `nav {angle, active}` command + `nav_ack` + a `navigation` telemetry field.
  `FW_VERSION` 1.1.0 → 1.2.0. Compiles clean under PlatformIO.
- [x] **Tests** — 104 pass (geo math, controller state machine incl. the four
  exit paths, speech start/stop precedence, web UI endpoints).
- [x] **Docs** — `README.md` (8-state table + a Navigation section) and this
  entry; the design is now `specs/013-navigation.spec`.

**On-hardware (needs the Pi/ESP32) — the test procedure:**
  0. [x] **RESOLVED 2026-08-24 — the "brownout loop" was a corrupt flash, not a
    power fault.** (First hardware connect.) The XIAO rebooted in a tight loop
    (`rst:0x3 RTC_SW_SYS_RST`, ~30×/sec) and never reached `setup()`. We first
    suspected a 3.3 V rail brownout (the minimal probe firmware also looped),
    but reading flash over the powered-hub port revealed the **real** cause:
    the flash was **corrupt** — the app image at `0x20000` was truncated
    (`TE 45 00 45 … "ESP_ERR_WIFI"`, not valid code) and the OTA select at
    `0xf000` was erased (`00 00 00 00`). The ROM aborted on the invalid image
    → the reset storm. **Fix: `esptool erase-flash` + a clean re-write** (this
    also clears the bad otadata). After that the board booted cleanly every
    time. Note the board's real partition layout (from `partitions.csv`): app
    `ota_0` is at **`0x20000`**, otadata at **`0xf000`** — NOT the stock
    `0x10000`/`0x9000` (the earlier reads of those offsets were misleading).
    **Flashing over the hub:** `esptool --chip esp32s3 --port
    /dev/cu.usbmodem12101 --baud 460800 erase-flash` then `write-flash 0x0
    bootloader.bin 0x8000 partitions.bin 0xE000 boot_app0.bin 0x20000
    firmware.bin` (use the framework's `boot_app0.bin`; the build dir has no
    separate `otadata.bin`).
  0a. [x] **Two real firmware bugs fixed (were crash-looping after the flash
    was repaired):** (1) `runHardwareCheck()` called `esp_spiram_get_size()`,
    which **aborts on core 1 when PSRAM is absent** → replaced with
    `psramFound()` (main.cpp). (2) `ServoController::update()` drove the
    PCA9685 over I2C through a **non-ready `Adafruit_I2CDevice`** after
    `_pca.begin()` failed → `LoadProhibited` null-deref (offset 0xC) at
    `Adafruit_I2CDevice::write`. Added a `_pcaReady` guard: `begin()` records
    the result, and `update()`/`writeMicroseconds()` no-op when the PCA isn't
    present (ServoController.cpp/h). Also: `GC9D01::drawPixel` now guards a
    null PSRAM framebuffer, and the 5 V sense read moved to **D4=GPIO5=ADC1_CH4**
    (the old `analogRead(7)`/GPIO3 was a non-ADC pin → always read 0).
  0b. [!] **The XIAO's octal PSRAM is down (hardware fault) — eyes will be
    blank.** Every valid PSRAM mode was tried: stock `qio_opi` and `qio_qspi`
    both report `psram: PSRAM ID read error: 0x00ffffff` (chip not found), and
    `opi_qspi` aborts at the ROM (`Octal Flash option selected, but EFUSE not configured`) because the flash efuses aren't set for octal. The firmware now **degrades gracefully**:
    it boots, runs the full state machine, streams telemetry, and the I2C bus is
    healthy (GPS 0x10, IMU 0x28, servo 0x40 all answer) — only the two LCD
    framebuffers are unavailable, so the eyes stay black. **This is not a wiring
    mistake on your side.** To restore the eyes the module's PSRAM needs to be
    repaired/replaced (reseat/reflow the octal part, or swap the XIAO). Face
    detection + navigation + servos do NOT depend on PSRAM and will work once
    the RPi brain is connected.
  1. [ ] **First-run wiring check** — set `HARDWARE_CHECK 1` (in
   `include/config.h`, or add `-DHARDWARE_CHECK=1` to `build_flags` in
   `platformio.ini`), `pio run`, and flash. On boot the owl probes **every**
   peripheral — both LCDs, the PCA9685 servo driver, the PA1010D GPS, the BNO055
   IMU, the SW420 vibration pin, and the OV2640 camera — and prints one JSON line:
   `{"type":"hardware_check","lcd_left":..,"lcd_right":..,"servo":..,"gps":..,
   "imu":..,"vibration":..,"camera":..,"i2c_found":"[..]","all_ok":..}`.
   The eyes show **happy** (all present) or **red X** (something missing);
   `i2c_found` lists every address that answers, so a wrong-address or dead-bus
   wire is obvious. Flip `HARDWARE_CHECK` back to `0` and re-flash to return to
   normal operation. (The check runs before the normal init, so it doesn't depend
   on the RPi being attached.)
 2. [ ] **Flash 1.2.0** (normal build) — 4-tap OTA (join `RobotOwl-Update` AP →
   `/update`) or USB; confirm `journalctl -u robot-owl-brain` shows `fw 1.2.0`.
 3. [ ] **Nav command moves the head** — from the web UI Navigate card, pick a
   place and hit **Start**; the head should turn and *hold* (NAVIGATING, not
   snap back to center). **Stop** should recenter it.
 4. [ ] **Verify `aim_sign`** (the one unknown, §10.1): stand the owl facing a
   known direction, read its BNO055 yaw from the web UI, and Start navigation to
   a place whose bearing you know (e.g. due north). If the head points the
   *opposite* way, set `navigation.aim_sign: -1` in `config.yaml` and restart — a
   config fix, not a re-code.
 5. [ ] **Live end-to-end** — teach a place in the web UI (map picker or manual
   lat/lon), say *"Bring mich nach <name>"*, and walk toward it: the head should
   keep re-aiming (a live compass). Exercise all four exits — spoken stop phrase,
   web UI Stop, arrival (within `arrive_m`), and the 5 s no-refresh timeout —
   and confirm each recenters the head cleanly.
 6. [ ] **Confirm yaw ≈ compass heading** and that the ~3–10 m GPS accuracy is
   acceptable for the 15 m `arrive_m` threshold (see `specs/013-navigation.spec` Open).

## Hardware

- [ ] **Mechanical assembly** — 3D print / enclosure, servo mounting for
   ears/head/wings, LCD bezels. (Wiring is documented in `WIRING.md`.)
- [ ] **First-run checklist** — no step yet for testing update mode end-to-end
   (4-tap → join AP → flash → tap to exit). Add one once hardware is assembled.

## Code review (2026-08-18)

- [x] **`fillScreen` dead + wrong `memset`** — `GC9D01.cpp` did a
  `memset(_fb, color>>8, ...)` then immediately overwrote every pixel with a
  full `for` loop. The `memset` was both wasted work (ran on every eye
  redraw) and the wrong value (only the high byte). Removed the `memset`;
  the loop alone is correct.
- [x] **`ServoController` bounds mismatch** — `setAngle` guarded with
  `channel >= 5` but `getAngle` used `channel >= 6`, and the arrays were
  `[6]` while only 5 channels are real. Added `NUM_SERVO_CHANNELS 5` to
  `config.h` and used it for the array sizes and all bounds checks.
- [x] **Duplicated `#ifndef` blocks in `Eyes.cpp`** — the whole
  `EYE_*`/`SCLERA_R`/`COLOR_*` guard block already existed in `common.h`
  (included via `Eyes.h`). Removed the redundant block from the `.cpp`.
- [x] **Hand-rolled JSON acks in `main.cpp`** — `handleCommand` built
  `expression_ack`/`servo_ack`/`sleep_ack`/`wake_ack`/`blink_ack`/
  `heartbeat_ack` with `String` concatenation while telemetry used
  ArduinoJson. Switched all acks (and the `invalid_json` error) to
  `JsonDocument` + `serializeJson` for consistency.
- [x] **Dead `Eyes::sleep()`** — declared in `Eyes.h` but never called; the
  `SLEEPING` state drives `_sleeping` via `setExpression`. Removed it.
- [x] **RPi drops non-telemetry messages** — `serial_handler.py::read_loop`
  only parsed `type == "telemetry"` and silently discarded everything else.
  Added a `_handle_message()` dispatcher: telemetry still goes to the
  callback, while `boot`, `update_mode`/`update_mode_end`, `error`, and any
  `*_ack` are now logged (acks at debug level).
- [x] **`parseSerialCommands` silent buffer drop** — `main.cpp` dropped any
  inbound line longer than 255 chars with no log. Now emits
  `{"type":"error","msg":"line_too_long"}` before clearing, so a
  truncated/malformed frame is diagnosable instead of silent.

## RPi-brain UX (2026-08-18)

Ideas to make the RPi supervisor nicer to run and debug.

### Service & startup

- [x] **One-command setup script** — `rpi-brain/setup.sh` is the single entry
  point for a fresh Raspberry Pi OS install: `sudo ./setup.sh`. It runs apt
  (python3-venv/alsa-utils/rsync/portaudio), adds the I2S `dtoverlay` to the
  correct `config.txt` (`/boot` or `/boot/firmware`), runs an **interactive
  config wizard** (serial port / web UI / speech / auto-sleep — useful
  defaults, Enter to accept; `--non-interactive` skips it), installs the tree
  to `/opt/robot-owl` + venv + requirements (faster-whisper, no torch),
  **pre-downloads the Whisper model**, creates the `robotowl` user in the
  `dial` group, installs the udev rule, enables the systemd unit, then reboots
  to apply I2S — with a one-shot boot hook that starts the robot and clears
  itself. Idempotent. `deploy/install.sh` remains as the no-reboot/no-apt
  variant for manual installs.
- [x] **systemd service** — `deploy/robot-owl-brain.service` + `deploy/install.sh`
  (copies the tree to `/opt/robot-owl/rpi-brain`, builds a `.venv`, creates a
  `robotowl` system user in the `dial` group, installs a udev rule for the
  ESP32 USB CDC port, then `enable`s the unit). Re-runnable. **Not yet run on
  real hardware** — verify `install.sh` + `systemctl start` on the RPi.
- [x] **Startup banner** — `brain/banner.py` prints an ASCII owl + firmware
  version / serial port / config path / web-UI status once at launch (stdout,
  so it shows in `journalctl`).
- [x] **Nicer debug loglines** — `Supervisor` now keeps the latest frame in
  `.last`, emits a 30s one-line liveness status (state, uptime, face, fw,
  servo angles), and `check_stale()` (run from the read-loop idle path) warns
  when no telemetry arrives for >10s (suppressed while in UPDATE mode, where
  silence is expected).

### Web UI (manual feature tester)

A LAN-only Flask page (`brain/web_ui.py`, started from `main.py` when
`web.enabled: true` in `config.yaml`) to poke the owl manually. Every action
forwards the same NDJSON command the supervisor uses, so no firmware change is
needed. The page polls `/api/telemetry` (1s) to show live state/face/servos.

- [x] **Web UI scaffold** — Flask app in a daemon thread; `/` (control page)
  + `/api/telemetry`, `/api/blink`, `/api/expression`, `/api/head`,
  `/api/sleep`, `/api/wake`. Routes verified with a stub serial (all 200,
  correct NDJSON forwarded).
- [x] **"Blink once"** — button + speed selector (fast…very slow) →
  `{"type":"blink","speed":N}`.
- [x] **"Look <expression>"** — button grid (neutral/happy/sleepy/surprised/
  angry/searching) → `{"type":"expression","value":...}` (3s overrides).
- [x] **"Make a sound"** — audio is **RPi-side only**: the MAX98357A I2S amp
  is wired to the Pi (see the new **Audio** section in `WIRING.md`), so no
  firmware change is needed. `brain/audio.py` synthesizes short effects
  (beep/chirp/happy/sad/alert) in-process as 16-bit mono WAV and plays them via
  `aplay` in a daemon thread (never blocks the serial read loop); it degrades
  to a logged no-op if `aplay`/I2S is unavailable. `Supervisor.play_sound()`
  wraps it, `/api/sound` exposes it to the web UI (button grid), and the
  supervisor auto-cues a matching effect on state transitions
  (`detecting`→chirp, `interacting`→happy, `sleeping`→sad, `update`/`error`
  →alert). Config: `audio.enabled` / `audio.device` / `audio.volume` in
  `config.yaml`. **On-hardware TODO:** enable I2S
  (`dtoverlay=hifiberry-i2s-lite` in `config.txt`), wire the amp (SD MODE →
  3.3V!), then confirm `aplay -l` lists a bcm2835 device and the buttons are
  audible.
- [x] **"Move the head (arrow buttons)"** — 3×3 pad (up/left/center/right/
  down) driving the head servo (`CH_HEAD`) with absolute angles
  (±30° L/R, ±20° up/down, 0 center) → `{"type":"servo","channel":2,"angle":...}`.
- [x] **Web UI safety/scope** — policy buttons (sleep/wake) exposed as
  `/api/sleep` + `/api/wake` (endpoints ready; page buttons optional). No
  auth (LAN only) — do not expose the port beyond the local network.

**On-hardware TODO for the web UI:** enable `web.enabled: true`, then confirm
the page loads and each button visibly drives the owl (blink/expression/head).

---

## Recently completed (context for future sessions)

- [x] **OTA update mode** — 4-tap vibration → SoftAP `RobotOwl-Update` +
  `/update` HTTP page (HTTPUpdateServer); one tap exits; dual-bank
  `ota_0`/`ota_1` partitions; standalone boot (5s USB wait, no RPi needed).
- [x] **RPi supervisor update-mode handling** — `Telemetry.update` dataclass
  parses the `update` object; `Supervisor` logs the AP ssid/password/url on
  entry and a confirmation on exit.
- [x] **README corrected** — state machine documented as 7 states (was 6),
   OTA line flipped from "not implemented" to implemented, protocol + status
   tables updated. (Since then: an 8th state, NAVIGATING, was added for the
   "guide me home" compass — see the Navigation section above.)
