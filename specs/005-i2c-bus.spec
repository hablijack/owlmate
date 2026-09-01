# SPEC-005: I2C bus and its three devices

Status: implemented
Verified: 2026-08-26 — all three devices answer; each exercised individually
Depends on: —

## Intent

Three peripherals share one I2C bus on GPIO1/GPIO2: the IMU, the GPS and the
servo driver. The bus was believed broken for a long time. It never was.

## Requirements

* **R-005.1** LSM303AGR @ 0x19 (accel) + 0x1E (magnetometer), PA1010D GPS @
  0x10, PCA9685 @ 0x40 on SDA=GPIO1, SCL=GPIO2 at 400 kHz. (Until 2026-09-01
  the IMU was a BNO055 at 0x28 — a single device, and one that stretched the
  clock; see SPEC-006.)
* **R-005.2** A missing or mute device must not prevent the owl from running.
* **R-005.3** No peripheral read may be able to block the main loop indefinitely.

## Decisions

**One bus, `Wire`, begun once** in `Sensors::begin()` with explicit pins.

**Presence is probed, and the result gates use.** `isImuReady()` /
`isGpsReady()` come from a plain address probe at boot. Telemetry omits the
`imu` / `gps` object entirely when its device is absent — which turns telemetry
into a presence indicator for free.

**Every peripheral read loop is bounded — in BUS TRANSFERS, not in calls.**
No exceptions, and the distinction is the whole lesson: bounding the number of
`read()` calls looks like a bound and is not one, because a mute peripheral can
make every single call cost a transfer. See Falsified.

**Servo channel numbers are sparse, and that is load-bearing.** The ears sit on
PCA9685 channels **15 and 14**, not 0 and 1 — moved 2026-08-27 because the servo
cables were too short. Head and wings remain on 2, 3, 4.

Consequence: per-channel arrays and bounds checks are sized by
`PCA9685_NUM_CHANNELS` (16), never by `NUM_SERVOS` (5). Those were one constant
before the move, which would have made `setAngle(15, …)` fail a `channel >= 5`
guard and **return silently** — an ear that never moves and reports no error.
`ServoController::setCenter()` and `update()` iterate only populated channels, so
nothing writes PWM to an empty one.

"left"/"right" throughout mean the **owl's own** left and right: standing in
front of the owl, its left ear is on your right. The channel numbers are
duplicated in `rpi-brain/brain/web_ui.py`, which is a mirror — `config.h` is the
source of truth.

## Verified facts

Measured 2026-08-26 with `pio run -e vibtest`-style probing and `-e i2ctest`:

* Bit-banged scan (bypassing the ESP-IDF driver entirely) **and** the Wire
  driver both find `0x10`, `0x28`, `0x40` — at 100 kHz and at 400 kHz.
* Re-measured 2026-09-01 after the IMU swap: the same two scans both find
  `0x10`, **`0x19`, `0x1E`**, `0x40` at 100 kHz and 400 kHz, and `0x28` is
  gone. **No clock stretching from the new part** — the longest stretch the
  bit-banged probe measures is ~0, against the BNO055's ~600 µs.
* SDA and SCL idle HIGH. Wiring is sound.
* Live data from each: the BNO055 returned real Euler angles; the PA1010D produced
  43 NMEA sentences in 6 s (`$GNGGA`/`$GNRMC`, correct date field); the PCA9685
  ramped channel 0 through 0→6→12→18→24→30° and held.
* Telemetry cadence ~535 ms, of which a noticeable share is I2C wait time.

## Falsified

* **"A dead I2C bus means broken wiring."** On 2026-08-28 every device vanished and the owl booted
  into `ERROR`. Ruled out by measurement, in this order: wiring (a passive sketch that only reads the
  two pins saw them HIGH for 27 s straight), continuity (SDA/SCL 0.0 Ω end to end, ground 0.1 Ω),
  supply (3.27 V at all three boards), pull-ups (6.66 kΩ — two 3.3 kΩ networks in series), and the
  microcontroller (`-e bushigh` on bare pads: pull-up, pull-down, drive-high and drive-low all pass).
  The fault was a single slave holding the bus.
* **"Lower the bus clock to survive a clock-stretching slave."** The master's timeout counts **time,
  not clock cycles**. 400 kHz, 100 kHz and 30 kHz all failed at the *same* access. What fixed the
  lockup was `Wire.setTimeOut()`, not the clock.
* **"A stuck bus can be freed with nine clock pulses."** The standard recovery was tried three times
  per attempt and never worked here, because the slave was holding **SCL**, not SDA — there is no
  way to clock a device out of that.
* **"A stronger pull-up fixes the ESP32/BNO055 timing violation."** A 2.2 kΩ was fitted on SDA
  (verified in circuit: SDA 1.3 kΩ vs SCL 3.3 kΩ) and changed nothing. The documented setup-time
  violation is real, but it was not this failure.
* **"Bit-banging sidesteps a clock-stretching slave."** It sidesteps the *controller's* impatience —
  `lib/SoftI2C` addresses all four devices cleanly where the hardware driver could not. It cannot
  help when the slave never releases SCL at all: measured, the line stayed low for more than **two
  seconds**, against a few hundred microseconds of legitimate stretch.

* **"The I2C bus is dead."** The evidence was a burst of ~18
  `ESP_ERR_INVALID_STATE` (259) errors at every boot, spanning ~490 ms. In the
  IDF 5.x `i2c_master` driver that code is **how a plain slave NACK is
  reported**, not a bus fault. `Adafruit_BNO055::begin()` soft-resets the chip
  and then polls its ID (`while (read8(CHIP_ID) != BNO055_ID)`); every poll
  before the chip finishes rebooting NACKs and logs two lines. Expected, benign,
  and it happens on every boot. This misreading cost a previous session a day.
  `CORE_DEBUG_LEVEL=0` now keeps the noise off the protocol stream.
* **"~700 ms telemetry ticks mean the bus is slow/broken."** They were a runaway
  read loop, not the bus. `Adafruit_GPS::available()` is hardcoded to
  `return 1` in I2C mode — *"I2C doesn't have 'availability' so always has a byte
  at least to read"* — so `while (GPS.available()) GPS.read();` **never
  terminates**. It hung the whole main loop on the first telemetry tick, freezing
  the eyes on their first frame. A first fix bounded it at 512 iterations, which
  merely converted an infinite loop into ~512 I2C transfers per tick — that was
  the ~700 ms. A second fix made it a 128-byte budget per call, parsing
  sentences as they complete, and the cadence became 535 ms.
* **"A 128-read budget bounds the bus traffic at ~4 transfers."** Third time in
  the same eight lines of code, found 2026-08-31. That claim was written as a
  comment directly above the loop and holds only while the GPS has something to
  say. When it does not, it sends `0x0A` padding; `Adafruit_GPS::read()` drops
  duplicate `0x0A`, ends up setting `_buff_max = -1`, and then `_buff_idx <=
  _buff_max` is false on *every* subsequent call — so each of the 128 reads
  issues its own 32-byte I2C transfer. At 400 kHz that is **~128 ms inside one
  main-loop iteration**, twice a second, and the eyes are frozen for all of it.
  Measured on hardware: `loop_max_ms` alternated 53 / 127 ms at idle, the
  expensive value landing whenever no NMEA sentence had arrived between two
  telemetry frames. Now bounded by consecutive *empty* reads
  (`GPS_DRY_READS = 3`), which caps the dry case at 3 transfers and leaves the
  data-flowing case untouched. Re-measured the same day: idle `loop_max_ms`
  median **104 ms -> 25 ms**, and the alternation is gone.

The general lesson, which cost time three times here: **a mute peripheral must
not be able to stop the owl.** Never write an unbounded read loop against one —
and note that all three defects were in the *same loop*, each time because the
bound was placed on the wrong quantity. Bound the thing that costs time (bus
transfers), not the thing that is easy to count (iterations). The tell is that a
mute device is the expensive case, which is the opposite of the intuition the
code was written with.

## Acceptance

1. `pio run -e i2ctest -t upload` — all three addresses answer, on the
   configured pins, at both clock rates.
2. Firmware boot prints no `ERROR: Sensors failed` and no PCA9685 warning.
3. Telemetry contains both an `imu` and a `gps` object.
4. Telemetry cadence stays near 535 ms; a jump toward 700 ms+ means a read loop
   has regressed.

## Open

* The boot-time NACK burst is suppressed rather than avoided. It could be
  reduced by not letting `Adafruit_BNO055::begin()` reset the chip, but that
  means forking the library for a cosmetic gain.
