# SPEC-006: IMU axes, mounting, calibration, heading

Status: partial — sensor replaced and axis remap measured 2026-09-01; heading offset and calibration still to be measured
Verified: 2026-09-01 — chip identity, scaling, bus behaviour and the axis remap on hardware
Depends on: 005

## Intent

Navigation ("point the head at a named place") needs a trustworthy compass
heading. Everything in this spec exists because that one number has to be right,
and because almost every layer between the chip and `geo.aim_angle()` was wrong
at least once.

## Requirements

* **R-006.1** `imu.yaw` must be a **true geographic** heading of the direction
  the beak points.
* **R-006.2** `imu.roll` / `imu.pitch` must read ~0 when the owl stands level.
* **R-006.3** The `calibrated` flag must be stable enough to gate navigation
  without flickering.
* **R-006.4** Calibration must survive a power cycle.
* **R-006.5** A wrong-but-similar sensor must fail loudly, not silently produce
  a plausible heading. (New 2026-09-01; see the two-breakouts decision below.)

## Decisions

**The sensor is an Adafruit LSM303AGR** as of 2026-09-01, replacing the BNO055.
This is **not a drop-in swap**, and the difference is the whole content of this
revision:

| | BNO055 | LSM303AGR |
|---|---|---|
| degrees of freedom | 9 (accel + gyro + mag) | **6 (accel + mag, NO GYRO)** |
| fusion | in the chip | **in our firmware** |
| orientation output | finished Euler angles | raw vectors only |
| mounting correction | `AXIS_MAP` registers, in hardware | in software |
| calibration | four 0..3 counters, in the chip | our own hard-iron min/max |
| clock stretching | yes, and it took the bus down | none observed |

Everything the BNO055 did in silicon now lives in `esp32-s3-sense/lib/OwlImu`.

**Two Adafruit breakouts look alike and are not.** The older LSM303DLHC (blue,
silkscreened `LSM303DLHC`) and the LSM303AGR (black, `LSM303AGR`) both put the
accelerometer on `0x19` and the magnetometer on `0x1E`, and they **share the
accelerometer driver** — but the **magnetometer register layouts are completely
different**. Running the DLHC magnetometer library (`Adafruit LSM303DLH Mag`)
against an AGR does not error; it returns garbage, i.e. a heading that looks
plausible and is wrong. The AGR's magnetometer is a **LIS2MDL**, so the library
is `Adafruit LIS2MDL`.
<https://learn.adafruit.com/lsm303-accelerometer-slash-compass-breakout/which-lsm303-do-i-have>

That is R-006.5, and it is enforced rather than documented: `OwlImu::begin()`
reads **both** `WHO_AM_I` registers by hand (accel `0x0F` → `0x33`, mag `0x4F` →
`0x40`) before handing over to the libraries, and names *which* half failed. The
DLHC magnetometer has no `WHO_AM_I` at `0x4F` and therefore cannot answer `0x40`,
so a swapped board is caught at boot.

**The mounting is corrected in software**, via `IMU_REMAP_X/Y/Z` in `config.h`:
each says which sensor axis (1/2/3 = x/y/z, sign = direction) feeds which owl
axis. Like the BNO055's `AXIS_MAP` before it, this can only express the 24
axis-aligned orientations — a board fitted at an odd angle needs a real rotation
matrix instead, and see **Open** below, because that is a live possibility.

**Owl body frame**, used by `lib/OwlImu`, by `imu.*` in telemetry, and by this
spec: **+X forward out of the beak, +Y to the owl's left, +Z up**. A level owl
reads accel `(0, 0, +9.81)` — an accelerometer at rest measures the reaction to
gravity, so its vector points **up**. `pitch = +90°` is the beak at the sky,
`roll = +90°` is lying on the right side.

**The compass is tilt-compensated by vector projection, not by the usual
trigonometric formula.** Project the magnetic field into the horizontal plane
(`m − (m·up)up`), project the beak direction into the same plane, and measure the
angle between them clockwise seen from above. Identical result, but **nothing in
it depends on an Euler-rotation order** — which is where the sign errors in the
classic formulation come from, and this project has already paid for one full set
of swapped orientation axes (see **Falsified**). The projection is not cosmetic:
the field here dips ~66° **downward**, so without removing the vertical component
the heading swings with every tilt of the owl.

**Calibration is hard-iron only** — per-axis min/max, offset `= (min+max)/2`.
Soft-iron (scale/skew) correction is deliberately out of scope: it needs an
ellipsoid fit and the accuracy target here is ±5–10°.

**`calibrated` answers one question and the progress counter answers another**,
and they are kept apart on purpose because conflating them is exactly what broke
the BNO055 (see **Falsified**):

* `imu.calibrated` = *hard-iron offsets exist* **and** *the geometry currently
  yields a usable heading*. This is what navigation gates on.
* `imu.cal.axes` (0..3) = how many magnetometer axes have seen at least
  `MAG_CAL_MIN_SPAN_UT` of span **this run**. A progress bar, nothing more. It
  may legitimately read **0 while `calibrated` is true** — that is a fresh boot
  with offsets restored from flash.

A level full turn reaches **2 of 3 axes**, and two is the bar: the earth field's
horizontal component (~20 µT of ~49 µT total, at ~66° inclination) only sweeps
the two horizontal axes. Only tumbling the owl reaches 3.

**The BNO055's three other counters are gone, not replaced.** `sys`, `gyro` and
`accel` had no referent on a chip with no gyro and no fusion engine. Synthesising
stand-ins was considered and rejected: a number that looks like a measurement and
is not one is this project's most expensive error class.

**Hard-iron offsets persist in NVS** (`owl-imu` / `mag-hardiron`, three floats)
and are restored at boot. A fresh turn in the current run supersedes restored
offsets automatically, so re-calibrating takes effect immediately. The stale
BNO055 key (`bno-offsets`, a different shape entirely) is deleted once if found —
a leftover blob in an NVS dump is the kind of thing that costs an hour at the
bench.

**Sampling runs at loop rate, not telemetry rate.** `Sensors::updateImu()` is
called once per main-loop iteration and self-limits to `IMU_SAMPLE_INTERVAL_MS`
(20 ms). Two reasons, both load-bearing: without a gyro the accelerometer is the
*only* attitude source and needs low-pass filtering, which needs sample rate; and
the hard-iron min/max is collected here, where 2 Hz would give a slow full turn a
few dozen points instead of several hundred. `getImu()` is a **pure getter** —
the BNO055 version wrote to flash from inside a getter the telemetry called.

**A mute sensor backs off.** Five consecutive implausible reads mark the IMU dead
for `IMU_FAIL_BACKOFF_MS`, after which one `WHO_AM_I` probe decides. Every failed
I2C access costs the full `I2C_TIMEOUT_MS` (250 ms), so polling a dead sensor at
50 Hz would stop the main loop — the defect `getGps()` already had twice
(SPEC-005). Detection is by plausibility (`|a|` outside 2–30 m/s², `|m|` outside
5–500 µT), because Adafruit's BusIO returns a zero-filled buffer on NACK and
`getEvent()` reports success anyway.

**`IMU_HEADING_OFFSET_DEG` is 356.6 — declination plus mounting.** It is
+4.594° of magnetic declination plus a mounting rotation of about −8°, measured
2026-11-10 on the assembled owl (see Verified facts) — but a later control
bearing disagreed by ~30°, so its accuracy is unproven (Open). The declination is required,
not optional, because the compass computes a *magnetic* heading while
`geo.bearing_deg()` computes a *true geographic* bearing and both sides must share
one north reference (`true = magnetic + declination`). The BNO055's 75.4 was
measured against *that* chip's remapped X axis in a different position and does
not carry over.

**Offsets are re-saved when the turn covers more axes, not once per boot.**
Changed 2026-11-10. A level turn determines only the two horizontal offsets; the
z value it leaves is the midpoint of interference plus the vertical earth field
(~45 µT here), which is invisible while the owl stands upright and costs a 40°
heading jump at 48° roll (measured). With "save once" the better 3-axis offsets
of a later tumble in the same run never reached flash. `Sensors` now remembers how
many axes the last saved set had and writes again only when that number grows,
so flash still sees at most three writes per boot. **And with offsets restored
from flash, a run replaces them only once all three axes have reached
`MAG_CAL_REPLACE_SPAN_UT`** (`lib/OwlImu/MagCalRule.h`, natively tested): before
that, carrying the owl a quarter turn and lifting it replaced a good calibration
with a half-done one and saved it — measured, +39°. *Known gap:* the save happens
the moment the third axis crosses its span threshold, so the stored z can be less
complete than the live offsets that keep improving in RAM.

## Verified facts

Measured 2026-09-01 on hardware, `pio run -e i2ctest` and `-e imuaxis`:

* **Bus.** Both halves answer at `0x19` and `0x1E`, on the bit-banged scan and
  through the Wire driver, at **100 kHz and 400 kHz**. `0x28` is gone. GPS
  (`0x10`) and servo driver (`0x40`) unaffected. **No clock stretching observed**
  — so the 30 kHz `I2C_FREQ` override the `imuaxis` env carried for the BNO055
  has been removed.
* **Identity.** Accel `WHO_AM_I` = `0x33`, magnetometer `WHO_AM_I` = `0x40`. It
  is genuinely an AGR, not the blue DLHC.
* **Accelerometer scaling is correct**: `|a|` read **9.74–9.85 m/s²** (mean
  ~9.77) across many samples. That validates the register map, the ±2 g range and
  the high-resolution 12-bit mode in one number.
* **Magnetometer scaling is plausible**: `|m|` ≈ **38 µT** against ~49 µT
  expected for the earth field here, the difference being an uncorrected
  hard-iron offset (components read ~−19.6, −24.1, +22.3 µT).
* **The filter works**: raw `ax` wandered −4.73…−4.80 between consecutive
  samples while the filtered value held −4.76…−4.79.
* **The axis remap, measured with the board held in its mounted position.** Two
  poses, because one is not enough (see below):

  | pose | raw sensor | conclusion |
  |---|---|---|
  | level | `(0.62, −0.40, −9.98)` | gravity 100 % on sensor Z, only **4.2°** off it → cleanly axis-aligned, sensor +Z points **down** → `owl Z = −sensor z` |
  | beak up 37° | `(1.00, −6.43, −7.69)` | gravity moved within the sensor **Y–Z** plane, sensor X barely changed → sensor X is the tilt axis (owl left–right), sensor Y is fore-aft → `owl X = −sensor y` |

  Right-handedness then forces `owl Y = U × F = −sensor x`. So
  **`IMU_REMAP_X = −2, IMU_REMAP_Y = −1, IMU_REMAP_Z = −3`** (determinant +1, a
  proper rotation). **Superseded 2026-11-10: Y is `+sensor x`** — the handedness
  step was wrong (see Falsified).
* **Verified after applying it**: level → `roll −1.4°, pitch +5.3°` (**R-006.2**);
  beak-up → `pitch +35°` with roll steady at −6.5°, i.e. pitch does not leak into
  roll; a sideways tip → roll swung to −62° with pitch steady at ~1°, i.e. the two
  axes are cleanly separated.
* **Persistence round-trips (R-006.4), verified accidentally and therefore
  honestly.** The main firmware was left running while the owl was being handled;
  that motion covered enough magnetometer span to trigger the one-shot save, and
  the next boot logged `IMU: Hartmagnet-Offsets aus dem Flash: -28.9 -12.0 33.9
  uT` and came up with `restored: true`, `heading_ok: true`, `calibrated: true`
  and a live, steady `yaw` (67.5° across many frames). Nobody set out to test
  this, which makes it a better test than a scripted one.
  **Those particular offsets are worthless** — collected hand-waving an unmounted
  board next to a laptop, with the head open. They prove the mechanism, not the
  calibration. Redo the turn once the head is closed.
* `loop_hz` with the IMU sampling at 50 Hz in `loop()`: **56–57** idle, worst
  `loop_max_ms` 26–27 in the steady state. The loop-rate polling costs nothing
  measurable.
* An earlier reading with the board merely lying loose on the bench gave
  `(−4.77, +5.91, −6.15)` — a compound angle. That was the board's own resting
  position, not the mount, and is only recorded here to explain why the remap
  could not be derived before it was held in place.

* **Assembled owl, 2026-11-10** (asleep, head centred; owl aligned by eye
  against an iPhone compass on TRUE north, which the owner rates as good to a few
  degrees). With the shipped `Y = −1`: right side down read roll **−50°/−68°**,
  and four bearings 70/161/251/340 read 303.3/213.5/120.6/30.8 — sums constant at
  ~372.5°, i.e. a mirrored heading. After `IMU_REMAP_Y = +1`: right side down
  reads **+44..+50°**, and after a 3-axis calibration in free air the bearings
  160/250/340/70 read 165.1/261.1/356.7/82.5 — a constant +11.4° (absorbed into
  `IMU_HEADING_OFFSET_DEG`) with residuals −6.3/−0.2/+5.3/+1.1, the size of the
  hand-alignment error.
* **Control bearings, 2026-11-10, same day.** With 356.6 and the offsets
  restored from flash: 163° → **163.6** ✓. Moving the owl to 244° (under the old
  replacement rule) produced **283** — a partial run had replaced the offsets.
  After a thorough tumble under the new rule (all three axes ≥ 60 µT, saved):
  206° → **163**. Restoring the earlier NVS from backup: 206° → **177**. Two
  calibrations disagree with that one bearing by 29° and 43°; not resolved.

## Falsified

The first three are from the BNO055 era. The chip is gone; the lessons are not,
and two of them shaped the replacement design directly.

* **"All three axes are fine, the numbers just look odd."** They were fully
  mislabelled: `pitch←roll, roll←heading, yaw←pitch`. That is why a level owl
  reported `roll: 359.9`. Since `navigation.py` feeds `imu.yaw` to
  `geo.aim_angle()` as a compass bearing, it was steering the head with a **pitch
  angle** — the feature could never have worked. *This is why the new compass is
  computed by vector projection: there is no axis order left to get wrong.*
* **"`calibrated` is false because the sensor is not calibrated."** It was
  calibrated. The flag required all four counters at 3, and `sys` pendulums by
  design. Measured false in 0 of 23 frames on a fully calibrated,
  offset-restored sensor, permanently blocking `navigation.py:162`. *This is why
  `calibrated` and `cal.axes` are now two separate fields answering two separate
  questions.*
* **"The figure-8 comes last."** Backwards for the BNO055: `accel` fell back to 0
  under sustained motion, so the figure-8 after the static poses destroyed the
  accel calibration just earned. Observed exactly that. **Does not apply to the
  LSM303AGR at all** — there are no such counters, and the procedure is now one
  slow full turn with no ordering constraint.
* **"The BNO055's 70.8° mounting rotation can be reused."** No. It was measured
  against that sensor's own remapped X axis, on a different breakout, in a
  different position. Only the declination term (+4.594°) survives the swap.
  Carrying the whole 75.4 across would have produced a heading wrong by ~70°
  that still looked like a plausible bearing.
* **"The board is upside down exactly like the BNO055, so reuse placement P7."**
  Measured false, and this is the most useful entry in this revision. The BNO055
  sat bottom-PCB-up with P7 = `X→−X, Y→Y, Z→−Z`. The LSM303AGR is *also* upside
  down and the two **agree on Z** — but its in-plane rotation differs by 90° and
  **swaps X with Y**: the measured map is `X→−Y, Y→−X, Z→−Z`. Copying P7 would
  have left a level owl reading a perfect 0/0 — passing the obvious check — while
  every tilt showed up on the wrong axis. "Upside down" pins only one axis; it
  says nothing about the rotation about it.
* **"One pose is enough to derive the mounting."** No. Gravity fixes which axis is
  vertical and its sign, and nothing else. The four in-plane rotations all give
  roll ≈ pitch ≈ 0 on a level owl, so the level pose cannot separate them — the
  error only appears once the owl tilts. That is why the procedure now requires a
  second, tilted pose.
* **"Two poses plus right-handedness fix the third axis."** Measured false
  2026-11-10, and the error was the one the rule was meant to prevent. X and Z
  were pinned by poses, Y was *derived* from handedness as `−sensor x`, and the
  assembled owl then read the wrong roll sign **and a mirrored compass** (four
  bearings whose true+reported sums were all ~372.5°). `Y = +sensor x` fixes
  both. The driver's axes are evidently not a right-handed triad, so a
  determinant check cannot validate the map. The 2026-09-01 "Open" entry that
  blamed the sideways observation on a rotating hand was wrong — the sideways
  pose was right and the handedness argument was not. *Measure all three axes,
  and check the compass at two bearings ~90° apart: a single bearing cannot
  tell a mirror from an offset.*
* **"A heading that is right on a level owl is right."** A level calibration
  turn leaves z uncalibrated; 48° of roll then moved the heading by 40°. Tumble
  the owl for the calibration.
* **"`I2C_TIMEOUT_MS` = 250 exists for the BNO055, so it can go."** The
  *measurement* behind it was made on the BNO055, but the value protects against
  any mute slave on the bus — and the PA1010D GPS is regularly one. What changed
  is the justification, not the number.

## Acceptance

1. `pio run -e imuaxis -t upload` — both `WHO_AM_I` correct, `|a|` ≈ 9.8 m/s².
2. **Two** poses, not one. Owl standing level: one raw axis reads ~±9.81 and the
   other two ~0, which gives the vertical axis and its sign. Then tip the owl
   beak-up ~30°: the axis that barely moves is the owl's left–right axis, and the
   one that gains the most is fore-aft. Then tip it onto its right side: that is
   `+roll` and pins Y. **Do not take Y from right-handedness** (Falsified).
   Re-flash; roll and pitch must read ~0 when level, and a beak-up tilt must show
   up as **pitch**, not roll (**R-006.2**). *Done 2026-11-10 on the assembled
   owl, Y corrected.*
3. Run `tools/kalibrieren.py` and turn the owl slowly through a full circle —
   `cal.axes` reaches 2, and the firmware logs the offsets it saved (**R-006.4**).
4. Reboot: the log shows offsets restored, `cal.restored: true`, and
   `calibrated: true` in essentially every frame without any turning
   (**R-006.3**).
5. Aim the beak at **at least two** known **true** bearings ~90° apart (four
   around the circle is better) and set `IMU_HEADING_OFFSET_DEG` to
   `(current value + mean(true bearing − reported yaw)) mod 360`; re-flash;
   `imu.yaw` then matches within ~10° (**R-006.1**). If `true + reported` is
   constant instead of `true − reported`, the frame is mirrored — fix the remap
   first. *Attempted 2026-11-10; one control bearing off by ~30° (Open).*
6. Unplug the magnetometer's SDA while running: telemetry drops the `imu` object
   and `loop_hz` does **not** collapse (the backoff works).

## Open

* **Heading accuracy is unproven** (Verified facts, control bearings). Suspects:
  min/max offsets off by ~10 µT (one outlier spoils a run; the horizontal field
  is only ~20 µT here), or the reference itself — a phone compass held beside an
  owl full of servo magnets. Next attempt: a map-derived landmark bearing instead
  of the phone, and a calibration quality figure from the firmware (BACKLOG
  Step 3, 4d).
* **The tilted heading after a full 3-axis calibration is not yet verified.**
  After an earlier, poorer tumble the heading still moved +14° at 60° roll and
  −21° at 67° pitch. Repeat with the 2026-11-10 free-air calibration; if it stays
  that large, the stored z offset is the suspect (saved at the moment the third
  axis crossed its threshold — see Decisions).
* **Min/max hard-iron calibration has no defence against a disturbance during
  the turn.** One pass near a laptop or a steel table skews an extreme for the
  rest of the run. The first tilt calibration on 2026-11-10 was visibly spoiled
  that way (constant error 26.5°, sinusoidal component 18°).
* **The mute-sensor backoff (Acceptance 6) has never been exercised.**
* Head-opening work must come **before** mounting and calibrating this sensor —
  it lives in the same head as the camera (see `BACKLOG.md`).
* Declination drifts ~0.1°/year and is location dependent. Re-check if the owl
  changes region: <https://geomag.bgs.ac.uk/data_service/models_compass/wmm_calc.html>
