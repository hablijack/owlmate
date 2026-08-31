# SPEC-009: On-device face detection

Status: works; inference on core 0 since 2026-08-31, render no longer stalls
Verified: 2026-08-26 — end to end, state machine reacts, eyes respond
Depends on: 002, 008

## Intent

The owl should notice a person and look at them, without help from the Raspberry
Pi. That is what makes `DETECTING → INTERACTING` a local decision (SPEC-001) and
lets the owl be interesting when unplugged.

## Requirements

* **R-009.1** Detection runs on the ESP32, not on the Pi.
* **R-009.2** A detected face yields a bounding box and a gaze vector in
  −1..1 relative to the frame centre.
* **R-009.3** Detection must not stall the main loop. Held only on paper until
  2026-08-31, when the inference moved to core 0 — before that it stalled the
  loop for the length of every cycle. Backed by the before/after measurement
  under Verified facts.
* **R-009.4** Intermittent detection must be observable in telemetry.

## Decisions

**esp-dl v3, model `MSRMNP_S8_V1`**, via the managed component
`espressif/human_face_detect`. This forces the firmware into
`framework = arduino, espidf` (SPEC-002) — the sole reason that build mode exists
in this project.

**The v1 API is gone.** `HumanFaceDetectMSR01` and
`human_face_detect_msr01.hpp` do not exist in v3. The API is:

    HumanFaceDetect(model_type_t, bool lazy_load = true);
    std::list<dl::detect::result_t> &run(const dl::image::img_t &img);
    Detect &set_score_thr(float thr, int idx);   // 0 = MSR coarse, 1 = MNP fine

    dl::image::img_t     { void *data; uint16_t width, height; pix_type_t pix_type; }
    dl::detect::result_t { int category; float score;
                           std::vector<int> box;      // lx, ly, rx, ry
                           std::vector<int> keypoint; }

Read the headers under `managed_components/`, not the online documentation.

**`FaceDetector`'s public interface is unchanged.** `FaceResult_t` and
`FaceDetector_Init/Detect/Deinit` are exactly as they were, so `main.cpp` needed
no modification at all when the implementation was rewritten.

**Pixel format is `DL_IMAGE_PIX_TYPE_RGB565BE`**, determined by measurement, not
by reading the camera driver's documentation (which does not commit).

**`face.total`, a cumulative detection count, is in telemetry** and
`FACE_HOLD_MS` (400 ms) keeps a hit visible across telemetry frames. Same reason
as `vibration.pulses` in SPEC-007: an intermittent event exposed as a bare
instantaneous flag is invisible at a 2 Hz sampling rate.

**Thresholds stay at the library default 0.5.** Measured scores are 0.58–1.00,
mostly above 0.9. Lowering them is useful while debugging and an invitation for
false positives in production.

**The largest face wins** when several are in frame — in doubt that is the
nearest person, and the one to follow.

## Verified facts

* **Glasses cost a third of the detections.** Measured 2026-08-31, same
  lighting, minutes apart: with photochromic glasses **65 %** hit rate, without
  them **100 %**. The reported score stayed 0.96 either way — when it finds the
  face it is confident; it simply finds it less often. These models lean on the
  eye region, and a darkened lens removes it. Because the lenses darken with
  sunlight, this makes the hit rate drift over the day with no code change, and
  it is a likely co-cause of readings previously blamed on distance alone.
* **Hit rate is dominated by face size in frame, and that is a seating
  distance.** Measured 2026-08-31 on one owl within one hour: **31 %** at 16 %
  of crop width, **100 %** at 31 %. Nothing in the firmware changed between
  those two readings. Before diagnosing detection, measure where the person
  actually is — `tools/trefferquote.py`.
* **Auto-exposure meters the whole frame.** `FaceDetector_Init()` sets only
  `vflip`/`hmirror`; everything else is an esp32-camera default. A bright window
  in frame therefore darkens the face even when the window lies outside
  `CAM_DETECT_CROP_DIV`'s crop, because the exposure decision is made on the
  full frame. Untested lever: `set_ae_level()` / `set_aec2()`.

* **Inference runs on core 0 since 2026-08-31, and the periodic stall is
  gone.** `FaceDetector_Detect()` used to be called from `loop()`, so the eyes
  stopped drawing for the length of every inference. It now runs in its own
  FreeRTOS task pinned to core 0 (`src/vision.cpp`); `loop()` only copies the
  latest result. Measured before and after in one sitting, face in frame at a
  normal seating distance:

  | | before (blocking, interval 300) | after (core 0, interval 100) |
  |---|---|---|
  | loop_hz min / mean / max | 10.8 / 30.2 / 44.1 | 15.6 / **28.3** / 61.7 |
  | samples below 15 Hz | **3 of 55** | **0 of 58** |
  | new gaze target | every 382 ms | every **181 ms** |
  | detection attempts | 2.6 Hz | 6.0 Hz |
  | hit rate | 100 % | 92 % |

  **The mean render rate did not improve — it fell slightly — and that is not
  a failure, it is the whole finding.** What the offload removed is the
  *spread*: the before sequence reads
  `11 29 31 35 32 42 26 41 33 17 36 31 40 26 42 33 …`, the after sequence
  `29 29 29 27 27 29 29 29 29 21 29 29 25 28 28 …`. Judge this change on the
  sequence and on the count of samples below 15 Hz, never on the mean. At idle
  the mean does move, 40.8 → 55.8 Hz, because there the loop is otherwise free.
* **A detection cycle is 48-91 ms and is 100 % compute — the camera is never
  waited on.** Measured directly 2026-08-31 via `face.capture_ms` /
  `face.infer_ms` rather than inferred from `loop_hz`. `capture_ms` was **0 in
  every sample**: `fb_count=2` with `CAMERA_GRAB_WHEN_EMPTY` always has a frame
  ready. `infer_ms` is 48 ms with no face in frame and 48-91 ms (mean 66) with
  one — a *successful* inference is the slower one, because more candidates
  reach the refinement stage.
* **Moving to core 0 costs ~3 ms of inference and buys back the whole block.**
  Control build, same instrumentation, blocking call on core 1, nobody in
  frame: `infer_ms` 47-50 (mean 48), `loop_hz` 23.4 / 40.8 / 44.2. Same
  conditions on core 0: `infer_ms` 48-63 (mean 51), `loop_hz` 28.0 / 55.8 /
  61.8. The 3 ms is cache and PSRAM contention with the renderer; it is a good
  trade.
* Detection scores 0.58–1.00.
* End to end in the real firmware: `face.total` climbing, confidence 0.61–0.87,
  state going `idle → detecting → interacting`, eye expression turning `happy`.
* `FaceDetector: Sensor PID 0x3660, vflip=1 hmirror=0` / `Face detection enabled`
  at boot.
* `lazy_load` defaults to true, so the constructor reports 0 ms / 0 bytes and the
  model actually loads on the first `run()`. Normal, not a failure.

## Falsified

* ~~**"Inference is 48 ms / ~21 fps."** That figure came from the `facelab`
  prototype and is not true of the owl; a detection cycle is ~170-200 ms.~~
  **THIS FALSIFICATION WAS ITSELF WRONG, and it is the most instructive entry
  in this file.** Written 2026-08-31; overturned the same day by direct
  measurement. The 48 ms was right all along: with `face.infer_ms` reporting
  the time around `detector->run()` itself, a cycle is **48 ms** with no face
  and **48-91 ms** with one.

  The error was in how ~170-200 ms was obtained. Nobody timed the inference.
  It was derived from `loop_hz` — 5.8 Hz while tracking, therefore 172 ms per
  iteration, therefore a 172 ms inference. But a loop iteration is `delay(16)`
  plus `eyes.render()` plus the inference, and during tracking the render is
  the expensive part, because the iris moves on every hit and the dirty-row
  flush then has real work to do. The inference was charged for the eyes.

  Two things follow, and they generalise past this bullet. **A subtraction is
  not a measurement**: `loop_hz` bounds the sum of everything in the loop and
  attributes none of it, and every number in this project that was derived
  that way has been wrong. And **a falsification deserves the same scrutiny as
  the claim it kills** — this one was believed instantly because it fit the
  `facelab`-is-not-a-control lesson learned two days earlier, and a correct
  lesson made a wrong conclusion look well-supported.

  **Still unexplained, and left open deliberately:** the recorded 5.8 Hz at
  `FACE_DETECT_INTERVAL_MS` 100 does not follow from a 66 ms inference and a
  ~35 ms iteration, which predict ~17 Hz. The interval-300 reading *was*
  reproduced exactly on 2026-08-31 (30.2 Hz mean, dips to 11), so that half of
  the record is sound; the interval-100 reading was never re-taken on the old
  firmware and should not be trusted without doing so.
* **"`FACE_DETECT_INTERVAL_MS` throttles the detection rate."** It is a floor,
  not a rate, and it is not the limit. Measured 2026-08-31: 100 -> 0 moved the
  gaze update from 190 ms to 171 ms (19 ms) while dropping the render rate from
  **40 Hz to 6 Hz**, because the inference blocks the main loop. Reverted.
* **"The permissive MSR threshold costs speed."** Plausible — every candidate it
  passes must be refined by MNP — and measured false. 2026-08-31, same sitting,
  same distance:

  | `FACE_SCORE_THRESHOLD_MSR` | attempts | hit rate | usable gaze target |
  |---|---|---|---|
  | **0.1** | 5.0 Hz | **100 %** | **200 ms** |
  | 0.3 | 5.7 Hz | 85 % | 207 ms |
  | 0.5 | 6.0 Hz | 31 % | 528 ms |

  Raising it barely speeds the inference and collapses the hit rate, so the
  *usable* rate only gets worse. 0.1 is already correct.

* **"The camera orientation does not matter for detection."** It is the single
  most important fact in this spec. These models find **upright faces only**; a
  flipped face is not a face to them. Measured over all four orientations with a
  face held in frame:

  | orientation | hits |
  |---|---|
  | normal | **0** |
  | **vflip** | **57** |
  | hmirror | 2 |
  | vflip+hmirror (180°) | 10 |

  Without `sensor->set_vflip(s, 1)` detection can **never** succeed — no
  threshold, no lighting, no model change fixes it. The camera is mounted flipped
  in the head, like the IMU (SPEC-006). Both were installed inverted.
* **"Nothing is detected, so the thresholds are too strict."** They were not.
  Lowering them to 0.2 while hunting changed nothing; real scores are 0.58–1.00
  and pass 0.5 comfortably. The thresholds were a red herring the whole time.
* **"Nothing is detected, so the byte order must be wrong."** Also not it, though
  worth settling: running both orders on every frame gave BE 62 hits vs LE 2.
* **"Face detection is broken in the firmware — telemetry says
  `detected: false` in 39 of 39 frames."** It was working. The state machine had
  reached `INTERACTING`, which happens *only* on a detected face. Detection runs
  every `FACE_DETECT_INTERVAL_MS` and the state machine sees every hit at ~60 Hz,
  but telemetry sampled an instantaneous flag at 2 Hz and missed all of them.
  Hence `face.total` and `FACE_HOLD_MS`.

**The owner's verdict on the finished thing: "it feels really *alive* now."**
That is the acceptance criterion this subsystem is actually held to, and it is
recorded because the numbers alone would not have settled it — the mean render
rate *fell*. The judgement that liveliness is made of render rate rather than
tracking latency was made on the panel earlier the same day (interval 300 at
29 Hz beat interval 100 at 5.8 Hz, "smoother, more alive — even though it
lags"); the offload removed the need to choose, and the verdict above is on
having both.

## Acceptance

1. Boot log: `Face detection enabled` and the sensor PID line with `vflip=1`.
2. Hold a face in front: `face.total` increases, `face.detected` appears in
   telemetry, `face.gaze_x` tracks left/right movement.
3. State goes `idle → detecting → interacting`; the eye expression becomes
   `happy`.

## Open

* ~~**The detection rate is capped by the eye renderer.**~~ RESOLVED
  2026-08-31 by moving the inference to core 0; the two are now independent.
  The 2026-08-28 reading behind this — a ~227 ms iteration of which ~160 ms was
  `eyes.render()` and ~47 ms inference — is worth keeping for one reason: that
  47 ms was the *correct* inference cost, sitting in this file three days
  before it was wrongly declared falsified. Detector accuracy was never the
  problem: **54 % of attempts hit**, at confidence 0.69–1.00, once the camera
  is actually pointed at a face.
* **The 6-hits-in-25-s baseline is not trustworthy.** It was recorded 2026-08-26,
  before anyone had looked at what the camera sees. On 2026-08-28 the same
  firmware scored **0 hits in 31.5 s** with a face deliberately held still —
  because the camera was aimed at the ceiling (see SPEC-008). After re-aiming,
  the same build scored 39 hits in 28.8 s. Any detection-rate number taken
  without confirming framing first measures the mounting, not the detector.
* Stale frames (`CAMERA_GRAB_WHEN_EMPTY` vs `CAMERA_GRAB_LATEST`) remains
  untested, and is now a **much weaker** lead: `facelab` grabs in a tight loop
  with the freshest frames obtainable and still scored 5 % before cropping.
  Use `face.total` as the metric, not `face.detected` — and read it against
  `face.attempts`, which exists precisely so a low `total` can be attributed.

**Two causes, both resolved 2026-08-29. The second was hiding behind a claim
this spec made.**

**(a) The coarse stage was rejecting faces before the fine stage saw them.**
Detection is a cascade: MSR proposes candidate regions, MNP refines and scores.
esp-dl defaults *both* to 0.5 and the firmware set both explicitly to 0.5.
Adafruit's working MEMENTO shoulder robot instead runs the coarse stage at 0.1:

    HumanFaceDetectMSR01 s1(0.1F, 0.5F, 10, 0.2F);
    HumanFaceDetectMNP01 s2(0.5F, 0.3F, 5);

Measured at 1 m with the crop already in place: hit rate **31 % → 100 %**, hits
1.84/s → 4.93/s, confidence 0.96 avg. **Zero** false positives over 184 attempts
in an empty room — precision survives because the reported score is still MNP's.

This spec previously stated "Thresholds are NOT the lever". That was true of the
FINAL score and false of the coarse stage, and the phrasing stopped anyone
looking. Distinguish the two stages before repeating it.

**RESOLVED 2026-08-29 — the face was simply too small in frame.** `run()`
rescales its input to the model's own resolution, so detection depends on the
face's size **relative to the frame**, not in pixels. At 1 m a head is ~40 px of
320 and is never found; at 30 cm it is ~62 px and the rate jumps. The frames are
sharp and well exposed in both cases — this was never image quality. Measured
with `facelab` as the control, same spot and light:

    full frame        0.0 % of frames had a face
    centre crop 2x   59.4 %   (score 0.82 avg)

`CAM_DETECT_CROP_DIV` (`config.h`) now feeds the detector the centre crop. In the
firmware at 1 m that took hits from 0.00/s to 1.84/s and held `interacting` for
30 s without once dropping out. **A larger frame size does not help** — VGA gives
the model the same picture at the same field of view, just resampled. The cost of
the crop is field of view.

**The `facelab` baseline quoted above was wrong.** "Near-every-frame detection"
was recorded at bring-up under unstated conditions. Re-measured 2026-08-29 in a
normal room, facelab scored **5 %** on the full frame — *worse* than the
firmware's 21 %. Every inference of the form "facelab is fine, so it must be the
integration" rested on a comparison that did not hold. If you use facelab as a
control, measure it in the same sitting as the thing you are comparing.

Detection is also **bursty** rather than uniformly sporadic: facelab logged 24
hits in one 56-frame window and almost nothing either side — the signature of a
face sitting at the model's size limit, not of a threshold or a race.

## Still open

* **A square detection crop.** The crop is currently 160×120, still 4:3.
  Adafruit's MEMENTO shoulder robot uses `FRAMESIZE_240X240` — square. If the
  model's input is square, a 4:3 image is distorted or letterboxed on rescale and
  some of it is wasted. A 160×160 centre crop would be square *and* capture more
  vertical extent, which suits faces. Untested; expected to be a modest gain.
* **Field of view is the price of the crop.** `CAM_DETECT_CROP_DIV 2` means the
  owl only sees the middle half of its camera view. `3` would extend range
  further at more cost. Nobody has measured what the owl's useful cone actually
  needs to be.
* ~~Stale frames (`CAMERA_GRAB_WHEN_EMPTY` vs `CAMERA_GRAB_LATEST`).~~ Closed
  2026-08-31: `face.capture_ms` is 0 in every sample, so `esp_camera_fb_get()`
  always has a frame waiting. Switching grab modes cannot buy time that is not
  being spent. It could still change frame *age*, but nothing points there.
* **The render is now the larger cost during tracking, not the inference.**
  48-91 ms of inference on core 0 against a ~35 ms loop iteration on core 1,
  and it is the latter that sets the 28-29 Hz the eyes draw at. Anything that
  wants a higher render rate belongs in SPEC-003, not here.
* **`FACE_DETECT_INTERVAL_MS` has not been re-tuned since the offload.** It is
  back to 100 as a floor between cycles, which with a 48-91 ms inference puts
  attempts at ~6 Hz. Lowering it would let core 0 run flat out for a gaze
  target maybe 30 % sooner, at the cost of permanent cache and PSRAM
  contention with the renderer — the 3 ms already measured is what that costs
  at a 100 ms duty cycle. Untested; measure `loop_hz` spread, not the mean.

## Reference

Adafruit's **MEMENTO Shoulder Robot** is a working face-tracking robot on
ESP32-S3, and reading it produced the cascade-threshold fix that took the hit
rate from 31 % to 100 %. Worth consulting again before inventing anything here:

    Adafruit_Learning_System_Guides/MEMENTO/Memento_Shoulder_Robot/
        platformio_memento_shoulder_camera/src/main.cpp

It is esp-dl **v1** (`HumanFaceDetectMSR01`/`MNP01`) on Arduino core 2.x, so the
API differs from ours — but the *structure* of the problem is identical, and the
constants it chose were arrived at on working hardware. Three worth knowing:

    HumanFaceDetectMSR01 s1(0.1F, 0.5F, 10, 0.2F);   // coarse: permissive
    HumanFaceDetectMNP01 s2(0.5F, 0.3F, 5);          // fine: strict
    config.frame_size = FRAMESIZE_240X240;            // square, not 4:3
    SERVO_HYSTERESIS 2 / SERVO_MOVEMENT_FACTOR 0.4    // deadband + proportional
