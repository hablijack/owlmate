# SPEC-016: Servo safety for the closed owl

Status: implemented — main image moves the servos within calibrated limits; the lock remains a build choice (`xiao_esp32s3_locked`, diagnostic envs)
Verified: 2026-11-01 — native motion/choreography tests, flash verification, hardware feel with the owner (earlier: 2026-10-18 locked-image protocol and cold boot)
Depends on: 001, 002, 010

## Intent

The PCA9685 plugs were relocated to fit the closed casing and their mapping was
initially not recorded; the owner subsequently supplied the assignment, now
recorded in WIRING and the firmware/brain mirrors. Mechanical travel is
constrained, and zero/center is not known to
be reachable. USB is soldered inside the owl and servo power cannot be switched
separately. Provisioning, serial opens and host reboots must not cause guessed
servo positions. Non-motion functionality must remain available.

## Requirements

* **R-016.1** The safety image never initializes/resets the PCA9685, changes its
  frequency, or writes PWM values, including boot, ordinary behavior, sleep,
  ERROR, OTA, direct servo commands, navigation and maintenance probes.
* **R-016.2** The lock survives software reset, power cycle and host reconnect;
  no serial command, policy action or NVS value unlocks it.
* **R-016.3** Eyes, camera, face detection, IMU/GPS, vibration, telemetry and
  explicit software OTA control remain functional independently of servo motion.
* **R-016.4** Telemetry reports the lock and ACK-only controller presence;
  rejected movement commands must not acknowledge successful actuation.
* **R-016.5** Deployment preserves bootloader, partition table, OTA metadata and
  NVS. Both OTA slots must contain the verified locked image before resetting
  into the app, so selection/rollback cannot start an old motion-enabled app.
* **R-016.6** (2026-10-31, owner decision; 2026-11-01 made the main image) Motion is a
  **build flag**: the main env `xiao_esp32s3` sets `-DSERVO_MOTION_ENABLED=1`;
  `xiao_esp32s3_locked` and every diagnostic env stay locked (`config.h` default 0), and
  there is no serial/NVS/runtime switch. 2026-11-01: owner: there is one owl we work
  towards, so the motion build is the main env, not a second image. Backed by
  `orangepi-brain/tests/test_servo_motion.py`.
* **R-016.7** The main image writes only pulses inside the per-servo table ends in
  `include/servo_profiles.h`, which are the owner-measured quiet limits **minus a margin**
  (table in that header). Targets, positions and ticks are each clamped; NaN/inf targets are
  rejected; an invalid profile is never driven; a loop stall cannot cause a jump; three bus
  errors stop all writes. Backed by `tests/servomotion/test.cpp` (mutations verified).
* **R-016.8** No servo moves before BOOT is over, and a cold channel's first pulse is its
  profile's rest position. A pulse already present and valid is adopted, never jumped.
  Frequency/sleep are only changed while every channel is off.

## Decisions

**Lock at the driver boundary, not only in the state machine.**
`SERVO_SAFETY_LOCKED=1` gates every driver entry point. A static_assert also
prevents simply flipping the flag to build an unlocked image without redesigning
for verified port assignments and individual limits. Normal methods remain for
future calibrated firmware, but the shipped image cannot actuate any channel.
`servo_step` is deliberately blocked too: a small relative step is not safe if
already against a mechanical stop, and an inactive channel has no known starting
position. No default center, sweep, return or automatic enable is allowed.

**Different servo types do not share a calibration.** The installed models are
recorded in WIRING. A travel-speed label is not a pulse-duration specification;
servo size does not establish idle, holding or stall current. The PCA9685 shares
one PWM frequency across all channels, but safe pulse bounds, start positions
and slew limits must be established individually. Existing global pulse/angle
constants are nominal parameters, not evidence of safe mechanical travel.
Electrical limits cannot substitute for checking the linkage; PWM full-off is
not power isolation.

**Isolation is per-servo unplugging; the real hazard is gear slip.**
Owner-stated 2026-10-29: servos are isolated by unplugging their connectors at
the PCA9685 with the owl fully de-energised; horns/linkages are unreachable in
the assembled owl; nothing falls when torque is lost and holding torque is
irrelevant (light loads). The remaining hazard is an arm driven into its
housing limit, where plastic gears slip. That is damage (broken teeth,
mis-indexed output), so per-servo bounds with margin remain required. Calibration
tests plug in exactly one servo, which also removes the shared-frequency
concern about other channels holding an old pulse. The owner can see and hear
the servos with the casing open; this is the abort criterion, not a hard stop.

**Servo variants and what is known (retail listings, no manufacturer
datasheets).** Neck: Longruner KY66 9 g (owner; first thought to be an SG90, no datasheet found);
SG90 reference values, NOT verified for the KY66: 50 Hz, 1000–2000 µs usable
(500–2400 µs theoretical). Others: 3.7 g nano servos, label missing, probably
D-Power AS-106 (analog, 4.8–6.0 V). The owner judged the AS-106 to match
visually; a photo does not identify it, so it is a working assumption. Owner-
supplied retail text gives it 1000–2000 µs full travel, 1500 µs neutral, 50 Hz
typical, extendable to ~900–2100 µs with care for the plastic gears. SG90 and
AS-106 therefore share 50 Hz and 1000–2000 µs, used as an outer safety
envelope; real bounds still come from observed steps, and 1500 µs is not a
safe start pose. The owner ruled out the GH-S37D (digital, 3.6–4.8 V), which
would not have tolerated 5 V. The owner
states the rail is 5 V and that the servos were checked earlier to tolerate it;
that is an owner statement, consistent with the AS-106's range, not a
measurement recorded here. The owner also confirmed common ground (all to
battery "−") and correct servo plug polarity. These are leads, not calibration.

**Calibration tool: a separate env, never the locked image.** `pio run -e servocal`
builds `lib/ServoCal` + `src/servocal.cpp` (raw PCA9685 registers, not Adafruit
begin/setPWMFreq). `setup()` only ACK-probes. Serial commands: `ARM <ch>`,
`FIRST <us>` then `YES`, `STEP <+-us>`, `LIMIT <lo> <hi>`, `STOP`, `STATUS`.
Rules (each has a native test against a recording fake PCA9685, and each was
seen to fail when deliberately removed): nothing writes before ARM; ARM refuses
if any other channel outputs a pulse, if the armed channel holds a value outside
the 1000–2000 µs envelope, and changes PRE_SCALE (->121, ~50 Hz, 4.88 µs/tick)
only while all 16 channels are full-off; the first pulse needs FIRST+YES within
15 s and any other command cancels it; STEP is at most 15 µs, at least 150 ms
apart, starts from the register's real value, stays inside the envelope and any
LIMIT (which can only narrow); STEP on an inactive channel is rejected; STOP and
a 120 s command watchdog set the channel full-off and issue no target; a bus
error disarms and is never retried; only the armed channel is written. After a
host reboot the PCA9685 keeps its pulse, and ARM adopts it by reading, not writing.
Only the five installed channels may be armed. Microsecond values are nominal
(internal oscillator tolerance). Loss of torque on STOP/watchdog relies on the
owner's statement that it is mechanically harmless.

**Deploying the tool uses the two-phase flasher, not `pio ... -t upload`.** Both OTA
slots hold the locked image and `-t upload` writes slot 0 only, leaving otadata
alone, so the board could keep booting the old slot. `tools/flash_servo_locked.py`
(backup, then both slots written and verified, protected prefix compared, no
automatic start of an older image on failure) is given the `servocal`
`firmware.bin` and its SHA256. Returning uses the same flow with the locked
image. A failed flash therefore leaves either the tool (disarmed, no output) or
nothing, never an old motion-enabled app. This was a correction: the first
instructions for this tool said `-t upload`.

**Read-only presence check at boot.** A zero-data I2C address probe is permitted.
Adafruit's `begin()` is NOT: it resets MODE1 and changes frequency before any
explicit angle command. Warm reset leaves existing PWM settings unchanged;
a cold PCA9685 is not enabled. This prevents new drive commands; it does not
remove an existing holding force or guarantee that an unpowered mechanism cannot
fall under gravity. It is not a physical emergency stop.

**Visible fail-closed state.** `servo_safety:{locked,ready}` is always emitted.
`ready` means address ACK only, not initialized or mechanically ready. The brain
parses both fields; absent data defaults locked. The Web UI disables motion
controls and labels the lock. `servo_ack`/`servo_step_ack` carry `ok:false` and
`error:servo_locked`; active navigation is rejected rather than entering a
state which falsely implies the head moved. Expressions, blink, gaze, sleep,
wake, heartbeat and update_mode remain available.

**Controlled two-phase USB-JTAG flash.** The flash helper opens with DTR/RTS
inactive, uses the board-specific usb-reset sequence to enter download mode,
backs up the complete flash and leaves ROM/stub running. After inspecting the
backup and actual partition table, the second phase again enters ROM directly
using usb-reset, checks the tested image's SHA256 and protected prefix,
writes/verifies both app slots, compares the protected prefix again, and only
then resets into the new image. Both phases explicitly set the S3 force-download
latch; only the success-path hard_reset clears it. This keeps download mode over
port close/warm reset, but is not a power-loss guarantee during an incomplete flash. Failures must not automatically boot or restore the old unsafe image.
No factory image, full erase, bootloader rewrite or NVS rewrite is used.

**Motion (2026-10-31): limits live in the image, not in NVS.** The measured limits are
compile-time data (`include/servo_profiles.h`), so no write path to NVS (which holds the IMU
calibration) is added and there is nothing for a corrupted store or a serial command to
loosen. Per-servo changes need a new image, deliberately. Tabelle ends = limits: wing right
1160..1990, wing left 1010..1730 (never near 1776), ear left 1985..2310, ear right 545..800,
head 520..2150 µs (head linear on the servo axis, 11.1 µs per nominal degree; the degree scale
is assumed, not measured). Speeds are deliberately low (head 40°/s, wings 300 %/s so a full-travel beat is visible, ears 220 %/s),
all eased by two cascaded critically damped springs plus a hard speed cap (a single spring jumps to
~3x the allowed speed in the first 16 ms frame, which looked "robotic"; the cascade changes speed
by <= 10 % of the cap per frame, test-backed). Face position is low-pass filtered (0.2 s) before the
head integrates it, because detections arrive only ~5x per second and are noisy.

**Servo task at a fixed 50 Hz (2026-10-31).** Owner: the head moved in "small, jerky steps".
Measured cause: the main loop stalls for 80-110 ms every second or two (GPS read, eye flush,
telemetry; `loop_max_ms` 79-111), and the servos only got a new position once per loop
iteration - so a stall became a hold followed by a jump, however smooth the curve. In the
main image `ServoController` therefore runs `ServoMotion::update()` in its own FreeRTOS task
(core 1, priority 2, every 20 ms); the loop only sets targets. `Wire` is shared with the IMU
and GPS, so every bus operation takes `I2cGuard` (`include/i2c_lock.h`; empty in the locked
image): around single operations, never around a loop. Lock order is always servo state ->
I2C bus. The last tick before a target is snapped onto it, because the spring otherwise
creeps in at one tick per few hundred ms. Not testable natively (FreeRTOS); the logic it
calls is.

**Head tracking is a camera-in-the-loop controller, so lag matters more than gain.** The camera
turns with the head: gaze_x -> filter -> integrator -> two springs -> servo -> camera -> detection
(every ~180 ms, ~70 ms old). Every lag in that chain costs stability, so "follow faster" is done by
*shortening* the lags first (gaze filter 0.08 s, head spring 0.16 s, speed cap 70 deg/s) and raising
the gain only moderately (40 deg/s per unit gaze_x). `tests/servomotion/test.cpp` section 10 simulates
that loop with the real choreography and the real springs for assumed fields of view of 10/16.5/25
degrees: it must settle without hunting and with bounded overshoot, and fails at 2x the gain. The
field of view itself is an assumption (OV3660, centre crop), not measured. Dithering between ticks
was considered and not done: on a ramp it produces the same staircase as rounding, and the servo's
own dead band (5-10 us) is wider than a tick.

**Head tracking is a saccade, not an integrator (2026-10-31/11-01).** With detections at ~5 Hz
the integrator re-aimed on every new value; each step was smooth, but the head accelerated and
braked 5 times a second and the owner saw "Stoesse" over the whole movement. (The servo and the
mechanics were innocent: with the servo off the head turns freely, and a 50 -> 100 Hz PWM
switch, see Falsified, changed nothing.) Now, once `|gaze_x|` exceeds `HEAD_TRACK_DEADBAND`
(0.28), the head sets ONE new target `sign * HEAD_TRACK_FOV_DEG * HEAD_SACCADE_GAIN * gaze_x`
from the current target; the springs make it one smooth move; then there is no re-aiming for
`HEAD_SACCADE_SETTLE_MS` + move time (`move_deg / HEAD_TRACK_DEG_PER_S`). Owner-tuned on
hardware: gain 1.3 (deliberate 30 % overshoot, "lebendiger"), settle 250 ms, deadband 0.28.
The simulation (test section 10) shows why 250 ms is about the floor: the camera image is
~180 ms apart and ~80 ms old, so below ~250 ms the head aims at a stale picture and hunts
(150 ms: 6-8 zero crossings, owner: "zu unruhig"; 300 ms: calm). Tests therefore allow more
crossings/overshoot for the smallest assumed field of view (10 deg) than for >= 12.5 deg.
`HEAD_TRACK_FOV_DEG` (16.5) is an estimate, not measured.

**Behaviour layer (`OwlChoreo`, called from `src/motion.cpp`).** The state machine keeps its
states; motion adds only *targets*: rest (sleep/error/OTA/boot), idle life (ear twitch every
6-14 s, glance up to 22° every 10-30 s), alert (ears up), engaged (head follows `gaze_x`,
greeting: ears up + two wing beats to the calibrated maximum; rare full-travel ruffle when idle), navigation (head belongs to navigation). Head
tracking integrates `gaze_x` (the camera turns with the head, so it is an error signal).
The **sign is not derived** (an image mirror is invisible to the vflip test, see
`EYE_GAZE_SIGN_X`); a wrong sign is positive feedback, so a stuck-at-limit guard disables
tracking for 30 s and centres the head after ~3 s. The supervisor's direct `servo` command is
refused in this image (`servo_autonomous`): the choreography owns the targets.


## Verified facts

* The native test compiles the real driver, not a Python reimplementation. It
  counts PWM-library initialization, frequency changes and output writes as well
  as I2C payload bytes. Boot and 1000 iterations across all 16 channels, direct
  targets, centering, positive/negative probes and a missing controller produce
  no writes. Reintroducing the library's begin() into locked boot was observed
  to fail the test before reverting the injected defect.
* All eleven PlatformIO environments build with the locked driver; the main
  image reports firmware 1.3.0. Build success alone is not hardware proof.
* Both OTA slots verified after deployment; the protected prefix through
  0x20000 matched the full backup byte-for-byte before boot. The complete 8 MB
  backup was also copied to the Mac and SHA256 checked. NVS calibration restores
  in telemetry after serial opens and after power restoration.
* Live telemetry reports locked:true/ready:true and fw 1.3.0. Direct targets on
  all 16 channels, both probe directions and active nav reject with servo_locked.
  Blink/expression, sleep/wake, OTA entry/exit and UPDATE heartbeat pass. Face
  inference attempts increase; cold-boot telemetry later includes successful
  detections. The normal brain service starts automatically and reads live
  serial without clearing the lock. Shaft positions were not measured remotely.

## Falsified

* **"The head stutters because the servo needs 100 Hz PWM."** Tested 2026-10-31 on the owl:
  `SERVO_PWM_PRESCALE=60` (~100 Hz, 2.44 us/tick) via `PLATFORMIO_BUILD_FLAGS`, task period
  10 ms. No change to the jerking; the cause was the 5 Hz re-aiming of the integrator (see
  Decisions). Note the PCA9685 keeps its pulses and frequency across an ESP32 reset, and
  `ServoMotion::begin()` refuses a frequency change while any channel is active, so changing
  the frequency needs the owl's power to be cut once. The default stays 50 Hz
  (`SERVO_PWM_PRESCALE 121`); the saccade tuning above was done at 100 Hz and is not
  expected to depend on it, but was not re-verified at 50 Hz.
* **"The default stub bulk-read path is reliable here."** Full and chunked
  stub reads stopped mid-transfer. The ROM-only path completed the full backup,
  both slot writes and verification; deployment used --no-stub.
* **"The second phase can simply reconnect with no-reset."** That attempt
  failed to synchronize, with an invalid packet head. Its cause was not
  established. The helper now explicitly re-enters ROM via USB-JTAG and sets
  the force-download latch rather than relying on the previous connection state.

* **"Blocking setAngle() is enough."** begin() itself resets the PWM chip and
  changes frequency; setCenter(), update() and the raw probe are independent
  write paths. All must be guarded.
* **"Returning to center is a safe cleanup."** The owner identified that the
  animatronic mechanics may not reach center. Cleanup must never invent a
  target, including on interruption or exceptions.
* **"The old microsecond helper used microseconds."** Its name and constants
  said so, but it passed values directly to setPWM(), whose unit is raw ticks.
  The dormant normal-write helper now uses the library's writeMicroseconds().
  The legacy probe's raw bounds also need a calibrated pulse/clock design before
  any unlocked implementation is allowed. Neither path is reachable in this image;
  blindly rescaling an unknown live output during deployment would itself move
  a servo, so the lock does not attempt that.

* **"Open the casing and decouple the horn to get a reference pose."**
  Horns and linkages are unreachable once assembled (owner, 2026-10-29), so no
  decoupled reference exists. Falsified plan items: access to horns, supporting
  falling parts, and holding torque as a hazard. The reference is found by
  small observed steps instead.

* **"A STEP-on-inactive test proves the guard."** The first test passed with the
  guard removed, because the envelope check happened to reject the same input.
  It only counted as covered once a mutation that disabled both showed the test failing.

* **"The fake PCA9685 proves the register reads."** The first `servocal` image
  read each channel as one 4-byte burst. On the real chip, MODE1 auto-increment
  is off at power-on, so the burst returned the same register four times and all
  16 channels read as unclear; ARM would have refused forever. The fake returned
  consecutive bytes regardless. It now models auto-increment, counts any
  multi-byte access made without it, and the old 4-byte read was seen to fail the
  test. Found on hardware 2026-10-30, fixed with four single-byte reads.

* **"No visible motion for the first 150-250 µs means the horn is mounted wrong."**
  Near both ends of the crank the wing angle barely changes with the servo angle;
  ch 9 showed it after both mounts. Mounting only matters when closed or open
  cannot be reached inside the envelope (it could not, with the first mount).
* **"The envelope is the same for every servo."** One fitted servo (ch 9) hunts at
  ~1776 µs; the per-servo limit has to be tighter than the common envelope.
* **"1000-2000 µs covers every servo."** The ears (ch 12/13) and the head (ch 10) have
  their travel outside it (left ear 1972..2323 µs, right ear 532..800 µs, head left
  limit ~2201 µs). The horns cannot be re-seated (the head is one unit), so `servocal`
  allows 500..2500 µs on exactly those three channels (owner decision, owner at the
  mechanism, steps with feedback). Do not widen the shared envelope instead. The
  right ear first looked dead at every pulse: it was snagged at its lower stop and was
  freed by a plain retest, not by a different value.
* **"Head curve is non-linear."** Owner's estimated degrees during the right-side sweep
  (1249 µs ~10°, 1098 µs ~45°, ... 551 µs ~80°) looked like a curve. The head sits directly
  on the servo axis and is linear (owner, 2026-10-31); the numbers were rough visual
  estimates, not data. Do not build a lookup from them.

## Acceptance

1. Native driver safety test fails against an injected write and passes against
   the locked implementation; all firmware builds and brain tests pass.
2. Before flash, retain a full backup off-board; inspect the actual partition
   table and verify both app slots fit. After flash, verify both slot contents
   and byte-for-byte preservation of the protected prefix before starting.
3. Live telemetry reports fw 1.3.0 and locked:true after reset/reconnect. Direct
   servo/probe/active-nav commands are rejected. Sleep/wake/OTA and normal local
   behavior cannot drive servos. Confirm non-motion functionality on telemetry
   and visually where an observer is available.
4. A controlled Orange Pi reboot keeps the XIAO in the locked image, and the
   brain reconnects without enabling outputs. Audio activation can then proceed.

## Hardware record 2026-10-30 (servocal on the owl, channel 8 only plugged)

Flashed with the two-phase flasher after a fresh read-only backup (bootloader,
partition table and NVS pages compared before/after: unchanged; NVS had differed
from the 2026-10-18 backup, which is why a fresh backup was taken). Verified:
power-on state reads mode1=0x11, prescale=30 and all channels full-off; `ARM 8`
sets prescale 121 and mode1=0x21 with no channel output; `STOP` writes full-off.
First pulses followed with the owner at the mechanism: the right wing (ch 8)
moved smoothly from its closed stop to ~90° open over 1498 -> 1147 µs with no
buzzing, fighting or clicking (owner observation; values nominal). Per-servo
numbers are recorded in BACKLOG, not here. Later the same day channels 9, 12, 13 and
10 were calibrated the same way (ears and head with the extended 500..2500 µs
envelope), and the locked image was restored to both slots (verified; protected
prefix and NVS unchanged). Serial note: the board sometimes stayed silent after the
port was opened (with the 120 s watchdog as the only safety net); a read-only esptool
reset (`chip_id --before usb-reset --after hard-reset`) always restored it.

## Open

Also unverified: whether the PCA9685 applies a 4-byte LEDn write atomically (a step
across a tick-count byte boundary, 255->256 = about 1250 µs, could glitch for one
20 ms frame), and the real oscillator tolerance behind the microsecond figures.

Individually reachable pulse/angle ranges, safe initial positions and an
explicit, reviewed arming/probe procedure are prerequisites for
any later motion-enabled image. They cannot be inferred from software values or
an acknowledgement. Also open: confirmation of the nano servo model (assumed
AS-106) beyond retail text. Open work and actual deployment results live in BACKLOG.
