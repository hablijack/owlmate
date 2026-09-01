# SPEC-008: Camera module, mounting, compatibility

Status: implemented
Verified: 2026-08-26 — sensor identified, frames captured, responds to light
Depends on: 002 (PSRAM for frame buffers)

## Intent

The camera is the owl's only outward sense of people. It feeds face detection
(SPEC-009) and nothing else.

## Requirements

* **R-008.1** Capture QVGA RGB565 frames into PSRAM.
* **R-008.2** The camera must not conflict with any pin used by the displays,
  the sensors or the vibration input.
* **R-008.3** A camera that initialises but returns a blank image must be
  distinguishable from one that does not initialise.

## Decisions

**The sensor is an OV3660**, not the OV2640 this project claimed everywhere
until 2026-08-26. Nothing functional depended on it — the pin map and
RGB565/QVGA settings are sensor-independent and the `esp32-camera` driver handles
the difference — but the wrong name cost real time (see Falsified).

**Camera pins live in `config.h`** as `CAM_PIN_*`, not in the .cpp. `config.h` is
this project's authority for pins; they were previously duplicated in a comment
and in code.

**`CAM_VFLIP 1`.** The camera is mounted vertically flipped in the head. This is
not cosmetic — see SPEC-009, where it is the difference between working and never
working.

**Two frame buffers in PSRAM**, `CAMERA_GRAB_WHEN_EMPTY`.

## Verified facts

* Sensor reports **PID 0x3660** at SCCB address **0x3C**. Driver log:
  `Detected OV3660 camera at address=0x3c`.
* `cam_hal: Allocating 153600 Byte frame buffer in PSRAM` ×2;
  `ov3660: SYSCLK 20000000 Hz, PCLK 10000000 Hz`.
* Image is real: mean brightness ~110 with min 7 / max 250 (full dynamic range),
  and it **dropped 122 → 53 the instant a hand covered the lens**. A blank or
  flat frame would show a narrow min/max spread.
* **`cam_hal: FB-OVF` and `cam_hal: FB-SIZE: a != b` mean the driver DISCARDED a
  frame, not that it handed us a bad one.** Both come from the DMA
  frame-assembly path in `cam_hal.c`. `FB-SIZE` means the frame was short when
  VSYNC arrived — observed once as `138240 != 153600`, i.e. 24 of 240 rows
  missing — and the driver then sets `frames[pos].en = 1`, which gates the
  `xQueueSend` that would have published it. `FB-OVF` means the DMA overran the
  buffer; the driver calls `ll_cam_stop()` and abandons it. Neither reaches
  `esp_camera_fb_get()`.
  **With `fb_count = 2` a discarded frame costs nothing measurable**: observed
  2026-08-31 alongside `capture_ms` = 0 in every one of 216 frames and
  `face.attempts` steady at 5.7–6.7/s, because the second buffer already held
  the next image. They appeared twice, immediately after an unrelated 2.2 s
  main-loop stall, and a 15-minute capture after that stall was fixed contains
  zero of them.
  They are printed with `ets_printf`, so they land on the NDJSON protocol port
  and cost the RPi a parse warning each. `CONFIG_LOG_DEFAULT_LEVEL_NONE` is what
  suppresses them — `cam_hal.c:44` guards the macro on exactly that symbol.
* **No pin conflicts.** Camera uses GPIO 10, 39, 40, 48, 11–18, 38, 47, 13; the
  rest of the owl holds 1–9, 43, 44.
* **The Sense expansion board's microSD slot shares GPIO 7, 8, 9** — which are
  the display MOSI, right-eye CS and left-eye DC. No SD card is fitted and none
  should be: an inserted card would drive its MISO line against the right eye's
  chip select. Seeed document a `J3` solder link to disconnect the slot entirely
  if it ever becomes necessary.

## Falsified

* **"The camera is detected but its model is unsupported."** `esp32-camera`
  returns `ESP_ERR_NOT_SUPPORTED` with the message *"Detected camera not
  supported"* **also when nothing answers at all** — `camera_probe()` leaves
  `out_camera_model = CAMERA_NONE` and returns that same error. It is not a
  "found but unknown" signal. Reading it as one produced a false all-clear on the
  wiring.
* **"Pull-ups on SDA/SCL prove the camera module is attached."** They do not.
  Those pull-ups are on the **Sense expansion board** — verified by unplugging
  the camera entirely and watching them stay HIGH. This session briefly concluded
  "the module is connected" from that reading; it was wrong.
* **"An 8-bit register read will identify the sensor."** Not for an OV3660: its
  registers are **16-bit addressed** and its ID lives at `0x300A`/`0x300B`. An
  8-bit read, as an OV2640 needs, returns garbage — which makes a perfectly
  healthy sensor look absent. This is the concrete cost of the OV2640 mislabel.
* **"The camera works, therefore detection can work."** On 2026-08-28 the
  camera passed every `camtest` check — SCCB ACK, `0x3660`, driver init, 27 ms
  frames, mean brightness 133 dropping to 29 under a hand and recovering — while
  face detection scored **0 hits in 31.5 s** against a face held deliberately
  still. The camera was aimed at the ceiling: the upper two-thirds of every
  frame was blank plaster and the face was never in shot. Every brightness
  number a mis-aimed camera produces is perfectly healthy, so **no brightness
  measurement can detect this failure** — only looking at the image can. This
  cost most of a session and sent the diagnosis toward thresholds and loop
  timing, neither of which was the problem. Take a snapshot **before** reasoning
  about detection rates. That is what `camsnap`/`schnappschuss.py` exist for.
* **A separately bought "OV3660-75mm for ESP32-CAM" module does not work in this
  socket.** No SCCB ACK at all, while the Seeed original initialises first try —
  same socket, same firmware, minutes apart. Contact side and pitch of the two
  flex tails are identical, so the remaining explanations are a different signal
  order on the cable or a defective module; neither could be distinguished
  without a second host. Socket, FPC connector, B2B connection, SCCB bus and
  driver are all *proven good* by the original working, which is the useful part.

Also worth recording: **GPIO39/40 have no ADC** on the ESP32-S3 (ADC1 is
GPIO1–10, ADC2 is GPIO11–20). `analogReadMilliVolts()` there does not merely
fail, it takes the HAL down with a `LoadProhibited` panic.

## Acceptance

`pio run -e camtest -t upload`:

1. SCCB scan finds `0x3C`; 16-bit read of `0x300A/0x300B` returns `0x3660`.
2. Driver init OK, sensor identified as OV3660.
3. Ten frames captured at 320x240, mean brightness plausible with a wide min/max.
4. Covering the lens drops the mean by tens of counts.

Brightness alone is **not** sufficient acceptance — it cannot tell a camera aimed
at a face from one aimed at the ceiling, and both look identical in every number
`camtest` prints. `pio run -e camsnap -t upload` plus
`tools/schnappschuss.py` returns the actual JPEG, in all four vflip/hmirror
combinations. Look at the picture; see Falsified.

## Open

* The original module's flex tail is too short to reach the mounting position.
  A 24-pin 0.5 mm **type A** extension plus a female-to-female coupler is the
  planned fix (~7 cm added). If the image degrades afterwards, drop
  `CAM_XCLK_FREQ_HZ` from 20 to 10 MHz — two extra connector transitions on a
  parallel DVP bus are the risk.
* **Re-measure `CAM_VFLIP` if the camera is remounted.** Confirmed still `1`
  (with `CAM_HMIRROR` `0`) on 2026-08-28 after the extension was fitted — the
  extension did not change the vertical mounting.
* **Aim is not recorded anywhere and nothing checks it.** It is currently set by
  hand and verified only by looking at a snapshot.
