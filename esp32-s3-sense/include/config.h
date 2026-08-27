#pragma once

// ============================================================================
// Firmware version (reported in telemetry so the running build is identifiable
// and OTA updates can be verified). Bump the minor on behavior changes, the
// patch on fixes.
// ============================================================================
// FW_VERSION is DERIVED from the three numbers, never written out by hand: it
// used to be a fourth #define carrying a second copy of the same string, which
// only had to be kept in sync manually.
#define FW_VERSION_MAJOR 1
#define FW_VERSION_MINOR 2
#define FW_VERSION_PATCH 0

#define FW_VERSION_STR_(x) #x
#define FW_VERSION_STR(x) FW_VERSION_STR_(x)
#define FW_VERSION FW_VERSION_STR(FW_VERSION_MAJOR) "." \
                   FW_VERSION_STR(FW_VERSION_MINOR) "." \
                   FW_VERSION_STR(FW_VERSION_PATCH)

// ============================================================================
// SIDE CONVENTION -- applies to EVERYTHING in this file
//
// "left" and "right" always mean the OWL's own left and right, as it would
// describe them looking forward. Standing in front of the owl, its left side is
// the one on YOUR right.
//
// This holds for the eyes (LCD_*_L / LCD_*_R) and for the servos
// (CH_LEFT_* / CH_RIGHT_*) alike. Confirmed with the owner 2026-08-27; the two
// subsystems were named before the convention was written down, so it was worth
// checking that they agreed. They do.
//
// If the eyes ever look mirrored, this is settled empirically in 30 seconds:
// `pio run -e dualtest -t upload` drives one eye red and the other blue.
// ============================================================================

// ============================================================================
// Board & System
// ============================================================================
#define SERIAL_BAUD 115200
#define I2C_SDA 1
#define I2C_SCL 2
#define I2C_FREQ 400000

// ============================================================================
// LCD Eyes (GC9D01, SPI) -- shared bus, per-panel CS/DC
//
// These are ACTUAL GPIO NUMBERS, not the XIAO "D#" silkscreen labels. The two
// differ on this board and confusing them has broken this project before:
//   D0=1  D1=2  D2=3  D3=4  D4=5  D5=6  D6=43  D7=44  D8=7  D9=8  D10=9
//
// LEFT/RIGHT here follow the side convention at the top of this file: the owl's
// own left and right, not the viewer's.
//
// Verified against the harness as physically built (2026-08-26), by driving
// each panel a different colour and confirming which eye lit up:
//   shared:  CLK=D4 (GPIO5)   DIN=D8 (GPIO7)   RST=D6 (GPIO43)
//   LEFT:    CS =D7 (GPIO44)  DC =D10 (GPIO9)
//   RIGHT:   CS =D9 (GPIO8)   DC =D3  (GPIO4)
// Backlights and panel VCC are tied to 3.3V (always on).
//
// NOTE: LEFT/RIGHT were previously swapped here -- config.h had the left eye on
// the right eye's CS/DC pair and vice versa. Both panels still lit up, so the
// only symptom was the two eyes being mirrored. Confirmed and corrected.
// ============================================================================
#define LCD_SCK 5
#define LCD_MOSI 7
#define LCD_RST 43
#define LCD_DC_L 9
#define LCD_CS_L 44
#define LCD_DC_R 4
#define LCD_CS_R 8

#define LCD_WIDTH 160
#define LCD_HEIGHT 160
// The earlier rationale here ("27 MHz was too fast, the panels never accepted
// pixel data") was a misdiagnosis: the panels accepted nothing at ANY clock
// because no chip-select was ever asserted (see lib/GC9D01/GC9D01.h). With that
// fixed the clock is a pure speed/margin trade-off.
//
// A full frame is 160*160*2 = 51,200 bytes per eye, and both eyes flush back to
// back, so frame time is ~820 kbit / LCD_SPI_FREQ. Measured on hardware:
//   6 MHz  -> 161 ms for both eyes (~6 fps, visibly choppy blinks)
//   16 MHz -> ~61 ms  (~16 fps)
// 16 MHz keeps comfortable margin on hand-wired jumper runs. The GC9D01 itself
// is good for far more; if the wiring is tidied up (short leads, ground return
// alongside the clock) this can go higher -- watch for torn or speckled pixels,
// which is what an over-clocked panel looks like.
#define LCD_SPI_FREQ 16000000

// ============================================================================
// Camera (OV3660, XIAO ESP32-S3 Sense expansion)
//
// IDENTIFIED ON HARDWARE 2026-08-26: the sensor reports PID 0x3660 at SCCB
// address 0x3C, i.e. an OV3660 -- NOT the OV2640 this project assumed. Verify
// with `pio run -e camtest`. OV3660 registers are 16-BIT addressed (its ID is
// at 0x300A/0x300B); an 8-bit read as an OV2640 needs returns garbage and makes
// a working sensor look absent.
//
// These pins sit on the Sense expansion's B2B connector, not on the D-pad
// header, and do not collide with anything else we use (we hold 1-9, 43, 44).
// ============================================================================
#define CAM_PIN_PWDN -1
#define CAM_PIN_RESET -1
#define CAM_PIN_XCLK 10
#define CAM_PIN_SIOD 40
#define CAM_PIN_SIOC 39
#define CAM_PIN_D7 48
#define CAM_PIN_D6 11
#define CAM_PIN_D5 12
#define CAM_PIN_D4 14
#define CAM_PIN_D3 16
#define CAM_PIN_D2 18
#define CAM_PIN_D1 17
#define CAM_PIN_D0 15
#define CAM_PIN_VSYNC 38
#define CAM_PIN_HREF 47
#define CAM_PIN_PCLK 13
#define CAM_XCLK_FREQ_HZ 20000000

// MOUNTING: the camera sits VERTICALLY FLIPPED in the owl's head. This is not
// cosmetic -- the detection models only find UPRIGHT faces, so without the
// vflip below detection can NEVER succeed, at any threshold or lighting.
// Measured over all four orientations with a face in frame (2026-08-26):
//   normal 0 hits | vflip 57 | hmirror 2 | vflip+hmirror 10
// Re-measure if the camera is ever remounted.
#define CAM_VFLIP 1
#define CAM_HMIRROR 0

// ============================================================================
// Vibration Sensor (SW420)
// Wired to D2 = GPIO3. (The LEFT eye's DC was moved to D3 = GPIO4, so the
// vibration sensor lives on the adjacent free pin D2 to avoid a conflict.)
// ============================================================================
#define VIBRATION_PIN 3

// ============================================================================
// Firmware update mode (4-tap vibration trigger -> WiFi SoftAP + OTA)
// ============================================================================
#define UPDATE_TAP_REQUIRED 4       // consecutive taps to enter update mode
#define UPDATE_TAP_GAP_MS 1500      // max gap between taps in a sequence
#define UPDATE_EXIT_GRACE_MS 1000   // ignore exit taps right after entering
#define UPDATE_AP_SSID "RobotOwl-Update"
#define UPDATE_AP_PASSWORD "robotowl123"
#define USB_WAIT_TIMEOUT_MS 5000    // max wait for USB CDC before continuing boot

// ============================================================================
// I2C Devices
// BNO055 IMU @ 0x28
// PA1010D GPS @ 0x10
// PCA9685 Servo Driver @ 0x40
// ============================================================================
#define ADDR_BNO055 0x28

// --- BNO055 mounting orientation ------------------------------------------
// The board is fitted bottom-PCB-up in the owl's head, i.e. rotated 180 deg
// about its own Y axis, so its +Z points DOWN. Measured on hardware
// 2026-08-26 with the owl standing on a level surface (`pio run -e imuaxis`):
// gravity read (-0.53, -1.21, -9.71) -- 7.7 deg off the Z axis, so a cleanly
// axis-aligned mount -- and Euler pitch read +172.8 deg, the 180 deg Y-flip
// signature. The BNO055 corrects this in hardware via its AXIS_MAP registers;
// these values are the datasheet's "P7" placement (X->-X, Y->Y, Z->-Z), which
// Adafruit exposes as REMAP_CONFIG_P7 / REMAP_SIGN_P7.
//
// IF THE SENSOR IS EVER REMOUNTED, re-measure with `pio run -e imuaxis`:
// stand the owl level, and with the right placement roll and pitch both read
// ~0. The AXIS_MAP registers can only express the 24 axis-aligned
// orientations, so a sensor glued in at an odd angle cannot be fixed this way.
#define IMU_AXIS_REMAP_CONFIG 0x24   // Adafruit REMAP_CONFIG_P7
#define IMU_AXIS_REMAP_SIGN 0x05     // Adafruit REMAP_SIGN_P7

// Added to the fused heading so that yaw is the direction the BEAK points,
// which is what navigation treats it as. The axis remap above makes yaw a
// rotation about true vertical, but its zero sits wherever the sensor's
// remapped X axis happens to face -- that is what this corrects.
//
// This folds TWO corrections into one constant:
//
//  1. Mounting rotation, 70.8 deg. Measured on hardware 2026-08-26: the beak
//     was aimed at a known MAGNETIC bearing of 341 deg (phone compass with
//     True North switched OFF) while the sensor reported yaw 270.2 deg
//     => (341 - 270.2) mod 360 = 70.8 deg.
//
//  2. Magnetic declination, +4.594 deg EAST at the time and place of that
//     measurement (WMM, via the British Geological Survey web service).
//     Re-derive it for your own location, it is not a universal constant.
//     REQUIRED, not
//     optional: the BNO055 reports a MAGNETIC heading, while geo.bearing_deg()
//     on the RPi computes a TRUE geographic bearing from lat/lon. Both sides
//     must share one north reference or navigation aims consistently wrong.
//     true = magnetic + declination.
//
//  => 70.8 + 4.594 = 75.4, so yaw is now a TRUE geographic heading of the beak.
//
// Declination drifts ~0.1 deg/year and is location dependent, so re-check it if
// the owl changes region or after a few years:
//   https://geomag.bgs.ac.uk/data_service/models_compass/wmm_calc.html
//
// ACCURACY: roughly +/-5..10 deg. The owl was pitched ~15 deg during the
// measurement (its level resting pitch is ~8 deg) and `accel` read only 1/3,
// both of which degrade tilt compensation of the compass. Fine for aiming a head
// with a +/-45 deg range. To re-measure more precisely: stand the owl level, get
// accel to 3/3 (six static poses), point the beak at a known bearing, then set
// this to (true bearing - reported yaw) mod 360.
#define IMU_HEADING_OFFSET_DEG 75.4f
#define ADDR_GPS 0x10
#define ADDR_PCA9685 0x40

// ============================================================================
// Servo Channels (PCA9685)
//
// Sides follow the convention at the top of this file: the owl's own left and
// right, not the viewer's.
//
// The ears are NOT on channels 0 and 1. They were moved to 15 and 14 on
// 2026-08-27 because the servo cables were too short to reach the low channels.
// The numbers are therefore SPARSE - see the note on array sizing below, this
// is not merely cosmetic.
// ============================================================================
#define CH_LEFT_EAR 15
#define CH_RIGHT_EAR 14
#define CH_HEAD 2
#define CH_LEFT_WING 3
#define CH_RIGHT_WING 4

// Number of servos actually fitted. Used for the telemetry "servos" array and
// for iterating the populated channels.
#define NUM_SERVOS 5

// Channel count of the PCA9685 itself. This - NOT NUM_SERVOS - is what bounds
// checks and per-channel arrays must use, because the channel numbers above are
// sparse. The two used to be the same constant (NUM_SERVO_CHANNELS = 5), which
// would have made setAngle(15, ...) fail the `channel >= 5` guard and return
// silently: the ear would simply never move, with no error anywhere.
#define PCA9685_NUM_CHANNELS 16

// Servo center positions (pulse width in microseconds, 50Hz)
#define SERVO_CENTER_US 1500
#define SERVO_MIN_US 1000
#define SERVO_MAX_US 2000
#define SERVO_MIN_ANGLE -45
#define SERVO_MAX_ANGLE 45

// ============================================================================
// Face Detection (optional, disabled by default)
// ============================================================================
#ifndef FACE_DETECTION_ENABLED
#define FACE_DETECTION_ENABLED 1
#endif
#define FACE_DETECT_INTERVAL_MS 100   // min ms between detection runs

// esp-dl v3 knobs. NOTE the old MSR01 parameters (top_k, resize_scale) do not
// exist in v3 -- the model handles its own preprocessing. What remains are the
// two stage thresholds of the MSRMNP model (stage 0 = MSR coarse, 1 = MNP fine).
//
// 0.5 is the library default and measured right: real detections score
// 0.58-1.00, mostly above 0.9. Lowering these is only useful while debugging
// and invites false positives in production.
#define FACE_SCORE_THRESHOLD 0.5f
#define FACE_NMS_THRESHOLD 0.5f
// Post-filter on top of the model: only treat a face as "detected" (and drive
// gaze/state) above this, so low-confidence flicker cannot flip the state
// machine.
#define FACE_MIN_CONFIDENCE 0.5f
// Nachhaltezeit fuer die Telemetrie. Die Erkennung laeuft alle
// FACE_DETECT_INTERVAL_MS, die State-Machine sieht mit ~60 Hz jeden Treffer -
// die Telemetrie tastet aber nur alle 500 ms einen Momentanwert ab und greift
// bei sporadischen Treffern ins Leere. Gemessen: die Eule wechselte nach
// INTERACTING (was nur bei erkanntem Gesicht passiert), waehrend 39 von 39
// Telemetrieframes "detected: false" meldeten. Dasselbe Muster wie beim
// Vibrationssensor; dieselbe Loesung.
#define FACE_HOLD_MS 400

// ============================================================================
// Telemetry
// ============================================================================
#define TELEMETRY_INTERVAL_MS 500
// --- SW-420 Vibrationssensor ------------------------------------------------
// Dieser Sensor liefert KEINEN saubern Pegel, sondern Impulsbuendel. Auf der
// Hardware gemessen (2026-08-26, `pio run -e vibtest`): 3252 Flanken in 28 s
// Klopfen, medianer Flankenabstand 1 ms, 63 % der Abstaende <= 2 ms; ein
// Klopfer ist ein Buendel von median 66 Flanken ueber 510 ms, das kleinste
// hatte 10 Flanken in 64 ms.
//
// Darum wird nicht der PEGEL abgefragt, sondern es werden FLANKEN per Interrupt
// gezaehlt (siehe Sensors.cpp). Nebeneffekt: die Zaehlung auf CHANGE ist
// polaritaetsunabhaengig, die Frage active-high/active-low entfaellt komplett.
//
// Die alte Logik las den Pin einmal pro Telemetrie-Tick (2 Hz) und verlangte
// 100 ms Pegelstabilitaet - bei einem 1-kHz-Prellsignal ist beides aussichtslos,
// weshalb "detected" auf dem Zufallswert vom Boot klebte.
// Die Auswertung laeuft EINMAL PRO HAUPTSCHLEIFE (~60 Hz, Sensors::update-
// Vibration), nicht im Telemetrie-Takt. Das ist wesentlich: bei 2 Hz war der
// Abstand zwischen zwei Auswertungen (535 ms) immer groesser als der Buendel-
// Abstand unten, weshalb jedes Klopfen, das ueber zwei Aufrufe reichte, doppelt
// gezaehlt wurde (gemessen: 8 Zaehlungen fuer 5 Klopfer).
#define VIBRATION_PULSE_MIN 2        // Flanken pro Auswertung (~16 ms) ab denen es zaehlt
#define VIBRATION_HOLD_MS 500        // so lange bleibt detected nach der letzten Flanke true
#define VIBRATION_BURST_GAP_MS 250   // Ruhe, die zwei Klopfer voneinander trennt
#define SERVO_SMOOTH_SPEED 2  // degrees per loop iteration

// ============================================================================
// Behavior state machine (owned by ESP32)
// ============================================================================
#define BOOT_TIMEOUT_MS 3000        // BOOT -> IDLE
#define DETECT_TIMEOUT_MS 10000     // DETECTING -> IDLE when no face seen
#define INTERACT_TIMEOUT_MS 5000    // INTERACTING -> IDLE when face lost

// Temporary overrides sent by the RPi supervisor (do not change state)
#define EXPRESSION_OVERRIDE_MS 3000 // how long an RPi expression override lasts
#define GAZE_OVERRIDE_MS 3000       // how long an RPi gaze override lasts

// Navigation: the RPi re-sends the nav angle on each refresh. If it stops
// (serial link dropped, RPi crashed/rebooted) for this long, the owl recenters
// its head and leaves NAVIGATING so it is never left stuck pointing somewhere.
// Must be comfortably larger than the RPi's refresh interval (see
// rpi-brain config.yaml navigation.refresh_min_s) but small enough that a dead
// link is noticed quickly.
#define NAV_TIMEOUT_MS 5000

// Wiring-verification boot. 0 (default) = normal boot into the state machine.
// Build with -DHARDWARE_CHECK=1 (platformio.ini build_flags, or `pio run
// --project-option="build_flags=... -DHARDWARE_CHECK=1"`) to instead probe
// every peripheral (LCDs, PCA9685, GPS, BNO055, vibration, camera) and report
// each one's status over serial + on the eyes, then idle. See runHardwareCheck()
// in main.cpp. Re-flash without the flag to return to normal operation.
#ifndef HARDWARE_CHECK
#define HARDWARE_CHECK 0
#endif

// Power-rail probe. When 1, the firmware does the absolute minimum at boot:
// no PSRAM init, no LCD, no camera, no I2C, no servos. It only opens serial
// and prints a rolling VCC reading (5 V input sense on ADC1_CH7) + uptime once
// per second. Purpose: if the board brownout-loops with the full firmware but
// boots clean with this, the 3.3 V rail is being dragged down by a peripheral
// load (backlights / I2C) rather than being fundamentally dead.
// Build with: pio run --project-option="build_flags=-DPOWER_PROBE=1 -DHARDWARE_CHECK=0"
#ifndef POWER_PROBE
#define POWER_PROBE 0
#endif
