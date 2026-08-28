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
  untested — it was the second lead and the first one accounted for the gap.
  Worth revisiting only after the flush is fixed.
  Use `face.total` as the metric, not `face.detected` — and read it against
  `face.attempts`, which exists precisely so a low `total` can be attributed.
