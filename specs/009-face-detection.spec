# SPEC-009: On-device face detection

Status: partial — works; detection rate in the firmware needs work
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
* **R-009.3** Detection must not stall the main loop.
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

* Inference **48 ms**, stable over 212 frames → **~21 frames/s**. (The README
  previously projected ~25 fps for the old v1 model; this is comparable.)
* Detection scores 0.58–1.00.
* End to end in the real firmware: `face.total` climbing, confidence 0.61–0.87,
  state going `idle → detecting → interacting`, eye expression turning `happy`.
* `FaceDetector: Sensor PID 0x3660, vflip=1 hmirror=0` / `Face detection enabled`
  at boot.
* `lazy_load` defaults to true, so the constructor reports 0 ms / 0 bytes and the
  model actually loads on the first `run()`. Normal, not a failure.

## Falsified

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

## Acceptance

1. Boot log: `Face detection enabled` and the sensor PID line with `vflip=1`.
2. Hold a face in front: `face.total` increases, `face.detected` appears in
   telemetry, `face.gaze_x` tracks left/right movement.
3. State goes `idle → detecting → interacting`; the eye expression becomes
   `happy`.

## Open

* **The detection rate is capped by the eye renderer, and that cap is the whole
  story.** Lead 1 below is now measured and confirmed; lead 2 was never reached.
  Measured 2026-08-28 with `loop_hz` and `face.attempts` in telemetry:
  `FACE_DETECT_INTERVAL_MS` (100 ms, i.e. 10 attempts/s) is **entirely
  non-binding** — detection runs on *every* loop iteration because the loop
  itself only manages 4.4 Hz. Of that ~227 ms iteration, ~160 ms is
  `eyes.render()` and ~47 ms is inference. So attempts sit at ~4.4/s no matter
  what the interval says, and the fix belongs in SPEC-003 (the 149.9 ms flush),
  not here. Detector accuracy is not the problem: **54 % of attempts hit**, at
  confidence 0.69–1.00, once the camera is actually pointed at a face.
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
* Stale frames (`CAMERA_GRAB_WHEN_EMPTY` vs `CAMERA_GRAB_LATEST`) — a weak lead
  now, see above.

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
