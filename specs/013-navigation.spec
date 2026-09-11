# SPEC-013: Navigation — the owl as a compass

Status: partial — implemented and unit-tested; `aim_sign` unverified on hardware
Verified: 2026-08-27 — 66 tests across geodesy, the controller, speech and web UI.
          Never run against a real GPS fix or a calibrated heading.
Depends on: 001, 006, 010, 012

## Intent

You teach the owl places (a name plus lat/lon), then ask it to guide you to one.
It enters `NAVIGATING` and holds its head pointed at the destination, re-aiming
as you walk. It is a compass with a beak, not a route planner: there is no map
matching, no turn-by-turn, no path — just "the thing you asked for is *that*
way".

Absorbed from `NAVIGATION_PLAN.md` (deleted 2026-08-27), which was the
pre-implementation design. Where that document asked open questions, the answers
are recorded below as decisions.

## Requirements

* **R-013.1** All geodesy runs on the Orange Pi. The ESP32 holds an angle it is told
  and does no math (inherits R-001.2 — one brain).
* **R-013.2** A dropped Orange Pi link must never leave the head stuck pointing.
* **R-013.3** There must be more than one way out, and no way to get stuck in.
* **R-013.4** A missing or untrustworthy sensor holds the last aim rather than
  flailing. Never aim on a guess.
* **R-013.5** Aiming must not begin until the heading is trustworthy.
* **R-013.6** A misheard place name must not send the head somewhere confidently
  wrong. Failing to understand is the better outcome.

## Decisions

**The Orange Pi computes, the ESP32 holds.** `geo.bearing_deg()` (initial great-circle
bearing) and `geo.distance_m()` (haversine) against the live fix, then
`geo.aim_angle()` reduces it to a head angle. The firmware's `nav` command
carries only that angle.

**`nav` is a PERSISTENT override, unlike expression/gaze.** Those expire after
3 s; `NAVIGATING` holds until an explicit `active:false` — or until
`NAV_TIMEOUT_MS` (5 s) passes with no refresh, at which point the firmware
recenters itself (R-013.2). The Orange Pi re-sends at `refresh_min_s` (0.5 s), so the
firmware timeout is 10× the refresh interval: comfortably long enough not to
trip on jitter, short enough that a dead link is noticed immediately.

**Four ways out, all funnelling to one command** (R-013.3): a spoken stop
keyword, the web UI's Stop button, arrival within `arrive_m`, and the firmware's
own timeout. All send `nav active:false`, so the firmware never has to know
*why* it is exiting — it recenters and returns to `IDLE`.

The stop keyword is deliberately **navigation-specific** (`stopp die
navigation`, `danke`, `reicht`) rather than a bare "stop": a generic stop word
shared with other reactions would kill the compass every time you told the owl
to stop doing something else.

**`aim_sign` is a config flag, not a code path.** The formula
`wrap_180(bearing − heading) · sign` has exactly one unknown: whether the head
servo's positive angle turns left or right, and whether IMU yaw increases
clockwise. Rather than guess, it is one config value to flip after a two-minute
hardware check — a config fix, not a re-code. **Still unverified** (Step 5).

**A destination behind the owl is clamped, not solved.** The head turns ±45°
only, and there is no body-yaw servo in the 5-channel setup. `aim_angle()` pins
to the range limit. Adding body rotation later would change this; nothing else.

**`NAVIGATING` reuses the `SEARCHING` eye** rather than introducing a glyph, so
the firmware change stayed additive.

**Fuzzy name matching lives in the store**, not in speech: exact, then prefix
(ASR truncates — "hote" → "hotel"), then Levenshtein ≤ 2. Beyond that it
refuses (R-013.6). See SPEC-012 for why it moved out of `Speech`.

**Places persist to `~/.config/robot-owl/locations.json`**, not into the repo —
they are user data, and the web UI writes them.

## Verified facts

* Aim math is unit-tested against hand-computed bearings, including the
  wrap-around cases (`wrap_180(180) == -180`, so "directly behind" reads as a
  leftward limit rather than oscillating).
* `imu.yaw` is meant to be a **true geographic** heading of the beak: the
  firmware folds the mounting rotation plus **+4.594° of magnetic declination**
  into `IMU_HEADING_OFFSET_DEG`. Both sides must share one north reference,
  because `geo.bearing_deg()` computes a true bearing while the magnetometer
  measures a magnetic one. Accuracy ±5–10° — fine for aiming a ±45° head.
  **Since the LSM303AGR replaced the BNO055 on 2026-09-01 the constant carries
  the declination term only** (`IMU_HEADING_OFFSET_DEG` = 4.594): the old 70.8°
  mounting rotation was measured against the BNO055's own remapped axes and does
  not carry over, so the aim is off by the mounting rotation until it is
  re-measured. See SPEC-006 Open.
* Navigation counts as activity for auto-sleep: the owl must not fall asleep
  while guiding someone who is walking with no face in frame.
* The controller no-ops with no fix or an uncalibrated heading (R-013.4/R-013.5),
  holding the last aim. `navigation.py` checks `imu.calibrated`, which is
  `gyro >= 3 && mag >= 3`.

## Falsified

* **"IMUPLUS mode is fine for a compass."** It drops the magnetometer, which
  means no absolute heading *and* a `calibrated` flag that can never go true —
  so navigation would refuse to aim forever. The IMU must stay in
  `OPERATION_MODE_NDOF`. See SPEC-006.
* **"`calibrated` should require all four calibration counters."** It made the
  flag false in 0 of 23 frames on a fully calibrated sensor, permanently
  blocking the aim path. `sys` and `accel` are live confidence values that fall
  during any movement — which is exactly when you are navigating. The flag uses
  `gyro` and `mag` only.
* **"A spoken 'stop' is enough."** A generic stop word is shared with other
  reactions; the navigation exit needs its own keywords or the compass dies
  every time you tell the owl to stop something else.
* **"Yaw relative to however the owl happens to be facing is good enough."**
  Considered during design as the simpler option. It is not good enough: without
  an absolute reference the head points at a bearing relative to an arbitrary
  zero, which is not a compass. The heading offset exists for this reason.

## Acceptance

1. `cd orangepi-brain && python3 tests/run_tests.py` — the navigation, geodesy, speech
   and web-UI navigation tests pass.
2. With a fix and a calibrated heading, starting a navigation makes the head
   point at the destination and track it as you turn.
3. Unplugging the Orange Pi mid-navigation recenters the head within
   `NAV_TIMEOUT_MS`.
4. Each of the four exits returns the owl to `IDLE` with the head centred.

## Open

* **`navigation.aim_sign` has never been verified on hardware** — BACKLOG Step 5.
  If the head turns the wrong way, flip it. Needs Step 3 (a calibrated IMU)
  first; until then `imu.calibrated` is false and the controller correctly
  refuses to aim, which looks like a broken feature and is not one.
* GPS accuracy is ~3–10 m at 1 Hz from the PA1010D, against a 15 m `arrive_m`.
  Untested outdoors; the threshold may need widening.
* Whether a misheard-name **confirmation turn** ("You mean Hotel?") is wanted.
  Currently it fuzzy-matches or refuses. A confirmation is safer but adds a turn.
