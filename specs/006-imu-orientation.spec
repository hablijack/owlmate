# SPEC-006: IMU axes, mounting, calibration, heading

Status: partial — calibration must be redone; heading offset unverified in use
Verified: 2026-08-26 — axes, mounting correction and persistence all on hardware
Depends on: 005

## Intent

Navigation ("point the head at a named place") needs a trustworthy compass
heading. Everything in this spec exists because that one number has to be right,
and because almost every layer between the chip and `geo.aim_angle()` was wrong.

## Requirements

* **R-006.1** `imu.yaw` must be a **true geographic** heading of the direction
  the beak points.
* **R-006.2** `imu.roll` / `imu.pitch` must read ~0 when the owl stands level.
* **R-006.3** The `calibrated` flag must be stable enough to gate navigation
  without flickering.
* **R-006.4** Calibration must survive a power cycle.

## Decisions

**`OPERATION_MODE_NDOF`**, not IMUPLUS. IMUPLUS fuses accelerometer and gyro
only — no magnetometer, therefore no absolute north, and a magnetometer
calibration counter that stays at 0 forever.

**Axis mapping follows the register layout, which is Heading, Roll, Pitch.**
`getVector(VECTOR_EULER)` reads from `BNO055_EULER_H_LSB_ADDR`, so
`orientation.x` is **yaw**, `.y` is **roll**, `.z` is **pitch**.

**The mounting is corrected in the chip**, via the BNO055's own `AXIS_MAP`
registers — placement **P7** (`config 0x24 / sign 0x05`, i.e. X→−X, Y→Y, Z→−Z).
The sensor is fitted bottom-PCB-up. `AXIS_MAP` can only express the 24
axis-aligned orientations, so a sensor glued in at an odd angle would need a
software rotation instead.

**`imu.calibrated` deliberately excludes `sys` and `accel`.** It is
`gyro >= 3 && mag >= 3` — "the heading can be trusted". `sys` is a live fusion
confidence value that dips to 0 whenever the owl moves; `accel` falls back under
sustained motion. The stricter all-four test is used only for the one-shot
decision to write offsets to flash (which is also what Adafruit's
`getSensorOffsets()` enforces internally).

**Calibration offsets persist in NVS** (`owl-imu` / `bno-offsets`) and are
restored at boot. Writing is skipped if offsets were restored this boot —
otherwise `setSensorOffsets()` makes the chip report 3/3/3/3 immediately and the
save branch would fire on every single boot, wearing flash for nothing.

**`IMU_HEADING_OFFSET_DEG` = 75.4** folds two corrections into one constant:
mounting rotation 70.8° plus magnetic declination **+4.594° E**. The declination
is not optional — the BNO055 reports a *magnetic* heading while
`geo.bearing_deg()` computes a *true geographic* bearing, and the two must share
a north reference (`true = magnetic + declination`).

## Verified facts

Measured 2026-08-26, owl standing level on a flat surface:

* Mounting: gravity read `(-0.53, -1.21, -9.71)`, 7.7° off the Z axis — a cleanly
  axis-aligned mount. Euler pitch read **+172.8°**, the 180° Y-flip signature.
* After applying P7: pitch **+172.8° → +8.1°**, roll −3.1° → +2.9°. The ~8°
  residual is physical mounting slop, not firmware.
* Heading offset: beak aimed at a known bearing of 341° **magnetic** (phone
  compass with True North *off*) while the sensor reported yaw 270.2° → 70.8°.
  Declination **+4.594° E** at that time and place, from the WMM via the British
  Geological Survey service. Total 75.4°. The declination is site-specific —
  re-derive it rather than reusing this number elsewhere.
* Verification of the offset: expected 345.6°, measured 343.0° — a −2.6°
  residual, which is sensor drift between measurements (the raw reading wandered
  270.2 → 267.6 over minutes), not an arithmetic error.
* Accuracy: **roughly ±5–10°**. The owl sat at ~15° pitch during the measurement
  (its level resting pitch is ~8°) and `accel` read 1/3; both weaken the
  compass's tilt compensation.
* `calibrated` true in **26/26** frames after the flag definition was fixed
  (it was 0/23 before).
* Persistence: save and restore round-trip verified. A reboot logs
  `IMU: restored calibration offsets from flash` and comes up with a live heading
  (yaw ~269 instead of pinned at 0).

## Falsified

* **"All three axes are fine, the numbers just look odd."** They were fully
  mislabelled: `pitch←roll, roll←heading, yaw←pitch`. That is why a level owl
  reported `roll: 359.9`. Since `navigation.py` feeds `imu.yaw` to
  `geo.aim_angle()` as a compass bearing, it was steering the head with a **pitch
  angle** — the feature could never have worked.
* **"`calibrated` is false because the sensor is not calibrated."** It was
  calibrated. The flag required all four counters at 3, and `sys` pendulums by
  design. Measured false in 0 of 23 frames on a fully calibrated,
  offset-restored sensor, permanently blocking `navigation.py:162`.
* **"The figure-8 comes last."** Backwards. `accel` falls back to 0 under
  sustained motion, so doing the figure-8 after the static poses destroys the
  accel calibration just earned. Observed exactly that: accel back to 0/3 after
  ~200 s of figure-8. Correct order is **mag (motion) before accel (stillness)**.
  `tools/kalibrieren.py` enforces it.

## Acceptance

1. `pio run -e imuaxis -t upload`, owl level: roll and pitch near 0.
2. Run `tools/kalibrieren.py` — all four counters reach 3/3 and it reports
   offsets saved.
3. Reboot: log shows offsets restored, `cal.restored: true`, `calibrated: true`
   in essentially every frame.
4. Point the beak at a known bearing; `imu.yaw` matches within ~10°.

## Open

* **Calibration must be redone** — it was wiped on 2026-08-26 by flashing a
  combined image over NVS (SPEC-002).
* `IMU_HEADING_OFFSET_DEG` has never been checked *in use*, only against the
  single measurement it was derived from.
* Declination drifts ~0.1°/year and is location dependent. Re-check if the owl
  changes region: <https://geomag.bgs.ac.uk/data_service/models_compass/wmm_calc.html>
