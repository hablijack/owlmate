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

// --- USB-CDC-Sendepuffer ----------------------------------------------------
// DER WICHTIGSTE WERT IN DIESEM BLOCK. Er entscheidet, ob ein langsamer Leser
// am anderen Ende die AUGEN einfrieren kann.
//
// Die Voreinstellung der Arduino-Portierung ist 256 Byte (HWCDC::begin()). Eine
// Telemetriezeile ist rund 900 Byte, passt also NIE hinein: sie wird in Stuecke
// zerlegt, und jedes Stueck wartet in HWCDC::write() bis zu tx_timeout_ms
// (100 ms) auf Platz - bis zu 20 Mal hintereinander, bevor aufgegeben wird.
// Das sind bis zu 2 s, in denen die Hauptschleife steht. Und sie steht dort,
// wo auch die Augen gezeichnet werden.
//
// Auf Hardware gemessen 2026-08-31, waehrend NIEMAND den Port las:
//   loop_max_ms 2256 und 4023, loop_hz 3,3-11,6 - die Augen sekundenlang tot;
//   eine Zeile im Mitschnitt exakt bei 256 Byte abgeschnitten (die Ringgroesse,
//   also die FIFO-Ersetzung in flushTXBuffer(), nicht ein zufaelliger Schnitt).
// Sobald ein Leser anhing, war der hoechste Wert 228 ms.
//
// 4096 fasst gut vier Telemetriezeilen, also rund 2 s Rueckstand bei 2 Hz. Das
// ueberbrueckt einen Leser, der kurz haengt - und der Orange Pi TUT das:
// dort laeuft die Spracherkennung im selben Prozess. Laenger als das wird
// verworfen statt gewartet, siehe sendJson() in src/protocol.cpp.
//
// Kostet 4 KB internen Heap (von ~197 KB frei). Muss VOR Serial.begin() gesetzt
// werden: begin() legt den Ring nur an, wenn es noch keinen gibt.
#define SERIAL_TX_BUFFER_SIZE 4096

// Wartezeit von HWCDC::write() auf Platz im Ring, in Millisekunden.
//
// 0 = gar nicht warten. Das ist hier RICHTIG und nicht etwa riskant, weil
// sendJson() vorher fragt, ob die ganze Zeile hineinpasst, und sonst gar nicht
// erst schreibt. Damit kann der Schreibvorgang weder blockieren NOCH eine Zeile
// halb hinausschicken - eine abgeschnittene NDJSON-Zeile kostet den Orange Pi einen
// Parse-Fehler und ist schlimmer als ein sauber verworfener Frame.
//
// Die Voreinstellung 100 ist der Wert, der oben die 2 s erzeugt.
#define SERIAL_TX_TIMEOUT_MS 0
#define I2C_SDA 1
#define I2C_SCL 2
// Ueberschreibbar per Build-Flag, damit Diagnose-Envs den Bustakt variieren
// koennen, ohne diese Datei anzufassen.
//
// 400 kHz ist hier seit dem Wechsel auf den LSM303AGR (2026-09-01) unauffaellig.
// Der BNO055 davor war es nicht: er verletzte beim Clock-Stretching die
// Setup-Zeit zwischen SDA-HIGH und SCL-HIGH und galt mit ESP32/ESP32-S3 als
// unzuverlaessig (Adafruit: "Troublesome Chips"), weshalb `imuaxis` den Takt
// auf 30 kHz herunterzog. DER LSM303AGR DEHNT DEN TAKT GAR NICHT - beide
// Haelften wurden am 2026-09-01 mit `-e i2ctest` bit-gebangt und ueber den
// Treiber bei 100 UND 400 kHz gefunden. Die Absenkung ist damit weg.
#ifndef I2C_FREQ
#define I2C_FREQ 400000
#endif
// Wie lange der Master auf einen Slave wartet, bevor er die Uebertragung
// abbricht. Die Arduino-Voreinstellung (50 ms) ist zu kurz: gibt der Controller
// auf, bricht er MITTEN in der Uebertragung ab - und laesst den Bus liegen.
// Danach ist nicht nur das eine Geraet weg, sondern auch GPS und Servotreiber,
// weil beide Leitungen unten bleiben.
//
// GEMESSEN WURDE DAS AM BNO055, der den Takt dehnte; der LSM303AGR tut das
// nicht. Der Wert bleibt trotzdem, und zwar nicht aus Traegheit: er schuetzt
// gegen JEDEN stummen Slave am Bus, und das PA1010D-GPS ist regelmaessig einer.
// Was sich geaendert hat, ist die Begruendung, nicht die Zahl.
//
// Auf Hardware gemessen 2026-08-28: mit 50 ms war der Bus nach dem ZWEITEN
// Zugriff tot (jeder Bustakt, jede Bibliothek), mit 1000 ms ueberstand er 12
// Lesezugriffe in Folge unbeschadet. Der Wert zaehlt ZEIT, nicht Takte -
// deshalb half es auch nichts, den Bustakt zu senken.
// 250 ms statt der 1000 ms des ersten Versuchs: gedehnt wurde nie um mehr als
// ~600 us, das ist immer noch das Vierhundertfache an Reserve. Der
// Wert wird bei JEDEM fehlgeschlagenen Zugriff voll bezahlt, und mit 1000 ms
// brauchte ein Telemetrieframe mit stummem GPS so lange, dass die Hauptschleife
// stehenblieb (gemessen: gar keine Telemetrie mehr).
#define I2C_TIMEOUT_MS 250

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
// back. The obvious model -- frame time = ~820 kbit / LCD_SPI_FREQ -- is WRONG,
// and an earlier version of this comment claimed "16 MHz -> ~61 ms" on its
// strength. Measured on hardware 2026-08-28 with both eyes flushing:
//   16 MHz -> 149.9 ms
//   40 MHz -> 149.9 ms   (identical, to 0.1 ms)
// The transfer is not clock-bound. Also ruled out by measurement, same figure
// every time: the PSRAM framebuffer (internal RAM is no faster) and the
// byte swap in writePixels() (writeBytes() without it is no faster). What
// remains is per-chunk overhead inside the bulk transfer itself, ~1.56 us per
// BYTE, so the fix is DMA or a dirty-rectangle flush -- not this number.
//
// Raising this therefore buys nothing today; it is left at 16 MHz because that
// keeps margin on hand-wired jumper runs. Watch for torn or speckled pixels if
// it is ever raised -- that is what an over-clocked panel looks like.
// See BACKLOG.md "Eye flush is the loop bottleneck" and SPEC-003.
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

// Vorzeichen, mit dem die waagerechte Blickrichtung auf die Augen abgebildet
// wird. -1 heisst: gaze_x aus dem Kamerabild wird gespiegelt, bevor die Augen
// ihm folgen.
//
// Warum das noetig ist: eine Kamera nimmt das Gegenueber seitenverkehrt auf -
// wer nach RECHTS geht, wandert im Bild nach LINKS. Zusaetzlich ist offen, ob
// die Kamera im Kopf auch waagerecht gespiegelt sitzt: CAM_VFLIP/CAM_HMIRROR
// wurden danach ausgewaehlt, welche Kombination AUFRECHTE Gesichter liefert,
// und eine Seitenspiegelung faellt bei dieser Pruefung nicht auf.
//
// Deshalb NICHT hergeleitet, sondern auf Hardware bestimmt (2026-08-29): mit
// +1 wanderten die Augen nach links, waehrend der Betrachter nach rechts ging.
// Wenn die Kamera neu eingebaut wird, hier wieder ausprobieren - genau wie
// navigation.aim_sign auf der Orange-Pi-Seite.
#define EYE_GAZE_SIGN_X (-1)

// Kein Blickfilter, und das ist gemessen so gewollt.
//
// Die Iris folgt der Gesichtsposition jedes Treffers unmittelbar. Sie zittert
// dadurch um etwa 1 px, weil der Detektor bei ruhigem Gegenueber leicht
// unterschiedliche Kaesten meldet. Am 2026-08-31 wurde genau dagegen ein Tag
// lang gefiltert - Totband, Zeitkonstante, Schwelle - und JEDE Variante wurde
// am fertigen Kopf als "laeuft hinterher" verworfen. Der Grund ist keine
// schlechte Einstellung, sondern die Abtastrate:
//
//   neue Gesichtsposition alle ~200 ms (5 Hz, durch die Inferenz begrenzt)
//   Rauschen je Abtastung          ~1 px
//   LANGSAME Kopfbewegung          ~1 px je Abtastung
//
// Bei 5 Abtastungen je Sekunde sind Rauschen und langsame Bewegung dasselbe
// Signal. Was das eine entfernt, verzoegert das andere um ein bis zwei
// Abtastungen - 200 bis 400 ms, und das faellt staerker auf als das Zittern.
// Ein lebendiger Blick war dem Besitzer wichtiger als ein ruhiger.
//
// DIESE BEGRUENDUNG IST SEIT 2026-08-31 UEBERHOLT, und zwar genau in dem
// Punkt, auf dem sie stand. Die Inferenz laeuft auf Kern 0 (include/vision.h),
// die Augen zeichnen durchgehend mit 28-29 Hz statt in Schueben, und ein
// Blickziel kommt alle 181 ms statt alle 382 ms. Die Rechnung oben ging von
// 5 Abtastungen je Sekunde aus - es sind jetzt 5,5, aber vor allem sind es
// 29 GEZEICHNETE Bilder je Sekunde statt 6. Eine kurze Interpolation zwischen
// zwei Blickzielen hat damit 5 Bilder Zeit statt einem und kann als
// Augenbewegung durchgehen. Ein Filter ist also NEU zu bewerten und nicht mehr
// durch diesen Absatz erledigt - siehe BACKLOG.md Step 4b.
//
// Gemessen und WIDERLEGT, bitte nicht wiederholen:
//   * FACE_SCORE_THRESHOLD_MSR 0.1 -> 0.3 -> 0.5: Inferenz kaum schneller
//     (5,0 -> 6,0 Hz), Trefferquote 100 -> 85 -> 31 %, nutzbares Blickziel
//     also 200 -> 207 -> 528 ms. 0,1 ist bereits richtig.
//   * "FACE_DETECT_INTERVAL_MS 100 -> 0 kostet 85 % Bildrate (40 -> 6 Hz)":
//     galt nur, solange die Inferenz in loop() lief. Der Zusammenhang
//     existiert nicht mehr; der Wert steht wieder auf 100.

// Dauer EINES Blinzelschritts in Millisekunden. Die Lidkurve laeuft ueber
// 2 * BLINK_SPEED Schritte (schliessen, oeffnen), speed 3 also 6 Schritte =
// 150 ms - ein menschlicher Lidschlag.
//
// War bis 2026-08-31 ein reiner Zaehler je gerendertem Bild und damit an
// dieselbe schwankende Bildrate gekoppelt wie oben: bei 44 Hz dauerte der
// Lidschlag 136 ms, bei 6,3 Hz aber 952 ms, davon 159 ms mit GESCHLOSSENEM
// Auge. Das ist der "waagerechte Strich", der auf Hardware zu sehen war. Der
// Wert 25 ms ist so gewaehlt, dass das Verhalten bei 40 Hz unveraendert
// bleibt - dort wurde die Blinzelkadenz seinerzeit eingestellt.
#define BLINK_TICK_MS 25

// ============================================================================
// Vibration Sensor (SW420)
// Wired to D2 = GPIO3. (The RIGHT eye's DC sits on D3 = GPIO4 -- see LCD_DC_R
// above -- so the vibration sensor lives on the adjacent free pin D2 to avoid
// a conflict. WIRING.md used to list it on D3 itself, which would have shorted
// it to that DC line.)
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
// LSM303AGR IMU @ 0x19 (accel) + 0x1E (magnetometer)
// PA1010D GPS @ 0x10
// PCA9685 Servo Driver @ 0x40
// ============================================================================

// --- LSM303AGR: two devices on one breakout --------------------------------
// Replaced the BNO055 on 2026-09-01. This is NOT a drop-in swap and the
// difference is the whole story of this block:
//
//   * The BNO055 was a FUSION chip. It computed orientation, absolute heading,
//     the mounting axis remap and its own calibration in silicon, and handed
//     out finished Euler angles.
//   * The LSM303AGR does NONE of that, and it has NO GYROSCOPE - it is a 6-DOF
//     part (accelerometer + magnetometer), not a 9-DOF one. Roll/pitch now come
//     from the gravity vector, yaw from a tilt-compensated compass, and both
//     are computed in `lib/OwlImu`.
//
// The two halves are two independent I2C devices with their own addresses and
// their own drivers; they share only the piece of PCB.
#define ADDR_LSM303_ACCEL 0x19
#define ADDR_LSM303_MAG 0x1E

// TWO ADAFRUIT BREAKOUTS LOOK ALIKE AND ARE NOT. The older LSM303DLHC (BLUE
// silkscreen, says "LSM303DLHC") and the LSM303AGR (BLACK, says "LSM303AGR")
// both put the accelerometer on 0x19 and the magnetometer on 0x1E, and they
// SHARE the accelerometer driver -- but the MAGNETOMETER REGISTER LAYOUTS ARE
// COMPLETELY DIFFERENT. Running the DLHC magnetometer library ("Adafruit
// LSM303DLH Mag") against an AGR does not error, it returns GARBAGE: a heading
// that looks plausible and is wrong. We have the AGR, whose magnetometer is a
// LIS2MDL, so the library is "Adafruit LIS2MDL".
//   https://learn.adafruit.com/lsm303-accelerometer-slash-compass-breakout/which-lsm303-do-i-have
// OwlImu::begin() checks both WHO_AM_I registers (0x0F -> 0x33 for the accel,
// 0x4F -> 0x40 for the LIS2MDL) so a swapped board fails loudly at boot instead
// of quietly producing a wrong compass.

// --- Mounting orientation, now corrected in SOFTWARE ------------------------
// The BNO055 had AXIS_MAP registers and did this in hardware (placement P7).
// The LSM303AGR has no such thing, so `lib/OwlImu` remaps in software.
//
// Each entry says which SENSOR axis feeds which OWL axis: magnitude 1/2/3 =
// sensor x/y/z, sign = direction. Identity is {1, 2, 3}.
//
// Owl body frame (also the frame of imu.* in telemetry):
//   +X = forward, out of the beak    +Y = to the owl's left    +Z = up
// A level owl reads accel (0, 0, +9.81) -- an accelerometer at rest measures
// the reaction to gravity, so its vector points UP.
//
// MEASURED ON HARDWARE 2026-09-01 with `pio run -e imuaxis -t upload`, the owl
// standing level and the board held in its mounted position. TWO poses, because
// one is not enough:
//
//   level:    raw (0.62, -0.40, -9.98) -- gravity 100 % on sensor Z, only 4.2 deg
//             off that axis, so the board is cleanly axis-aligned and its +Z
//             points DOWN. => owl Z = -sensor Z.
//   beak up:  raw (1.00, -6.43, -7.69) -- a 37 deg tilt that moved gravity within
//             the sensor's Y-Z plane while sensor X barely moved. So sensor X is
//             the tilt axis (the owl's left-right axis) and sensor Y is the
//             fore-aft one. => owl X = -sensor Y.
//   Right-handedness then forces the third: owl Y = Z x X = -sensor X.
//
// WHY THE SECOND POSE IS NOT OPTIONAL: gravity alone fixes only which axis is
// vertical and its sign. That still leaves the four in-plane rotations, and a
// level owl reads roll ~ pitch ~ 0 in ALL FOUR of them -- the error only shows up
// once the owl tilts, as pitch appearing in roll. One pose cannot see it.
//
// AND THAT IS NOT ACADEMIC HERE: the BNO055 sat "upside down" in this same head
// and its placement was P7, i.e. X->-X, Y->Y, Z->-Z. This board is ALSO upside
// down and the two agree on Z -- but its in-plane rotation differs by 90 deg and
// swaps X with Y. Copying P7 across would have left a level owl reading a
// perfect 0/0 while every tilt showed up on the wrong axis.
//
// VERIFIED after applying these, board held in the mounted position:
//   level:      roll -1.4 deg, pitch +5.3 deg  -- both near zero, so R-006.2 holds
//   beak up:    pitch +35 deg while roll stayed -6.5  -- pitch does not leak into
//               roll, so the in-plane rotation is right
//   sideways:   roll swung to -62 deg while pitch stayed ~1  -- axes cleanly
//               separated
//
// ONE LOOSE END, AND IT IS A NAMING ONE, NOT A FRAME ONE. In that third pose the
// owl was reported as tipping onto its RIGHT side, which by the convention above
// should give POSITIVE roll; the measurement was negative. Those three
// observations cannot all be true: taken together they describe a LEFT-handed
// triad, and no rigid body has one. owl Z and owl X are each pinned by an
// unambiguous pose, so right-handedness FORCES owl Y = U x F = -sensor x, which
// is what stands above. The sideways observation is the odd one out - most likely
// the board rotated in the hand holding it (undetectable from a single vector),
// or "its right side" was read as the observer's right.
//
// DELIBERATELY NOT "FIXED" BY FLIPPING IMU_REMAP_Y: that would make the map
// improper (det -1). The compass is a cross product, so a mirrored frame would
// return a MIRRORED HEADING that still looks like a plausible bearing - the exact
// failure class this whole subsystem is built to avoid. The heading depends only
// on owl X, owl Z and handedness, all three established, so yaw is unaffected
// either way; only the sign of the reported roll is in question, and nothing
// consumes roll. Re-test per specs/006 Acceptance 2 when the board is fixed in
// place.
//
// Re-measure with `-e imuaxis` if the sensor is ever remounted; a level owl must
// read roll and pitch near 0. These three constants can only express the 24
// axis-aligned orientations -- a board glued in at an odd angle needs a real
// rotation matrix instead.
#define IMU_REMAP_X -2
#define IMU_REMAP_Y -1
#define IMU_REMAP_Z -3

// Added to the computed compass heading so that yaw is the direction the BEAK
// points, which is what navigation treats it as.
//
// THE MOUNTING TERM IS NOT YET MEASURED FOR THIS SENSOR. The BNO055's value was
// 75.4 = 70.8 deg of mounting rotation + 4.594 deg of declination. The 70.8 was
// measured against the BNO055's own remapped X axis on a different breakout in a
// different position, so it does NOT carry over. Only the declination does:
//
//  * Magnetic declination, +4.594 deg EAST at this location (WMM, via the
//    British Geological Survey service). REQUIRED, not cosmetic: the compass
//    computes a MAGNETIC heading while geo.bearing_deg() on the Orange Pi computes a
//    TRUE geographic bearing, and both sides must share one north reference
//    (true = magnetic + declination). Drifts ~0.1 deg/year and is location
//    dependent, so re-check it if the owl changes region:
//    https://geomag.bgs.ac.uk/data_service/models_compass/wmm_calc.html
//
// TO MEASURE THE MOUNTING TERM: stand the owl level, complete a magnetometer
// calibration turn (see tools/kalibrieren.py) so the heading is valid at all,
// aim the beak at a known TRUE bearing, and set this to
// (true bearing - reported yaw + 4.594) mod 360. `pio run -e imuaxis` prints
// the reported yaw live.
#define IMU_HEADING_OFFSET_DEG 4.594f

// --- Sampling and fusion tuning --------------------------------------------
// How often OwlImu::update() actually touches the bus. It is called once per
// main-loop iteration (~60 Hz) and rate-limits itself to this.
//
// NOT the telemetry rate, on purpose, and this cost real thought: without a
// gyro the accelerometer is the ONLY attitude source, so the low-pass below
// needs samples to work with -- and the hard-iron calibration collects its
// min/max here, where 2 Hz would give a slow full turn a few dozen points
// instead of several hundred.
#define IMU_SAMPLE_INTERVAL_MS 20

// Exponential low-pass coefficient for both the accel and the mag vector.
// 0.15 at 50 Hz is a time constant of ~0.12 s: enough to swallow servo
// vibration, fast enough that a head movement settles within a frame or two.
#define IMU_FILTER_ALPHA 0.15f

// Minimum observed peak-to-peak span, in microtesla, before a magnetometer axis
// counts as calibrated. The earth field here is ~49 uT total at ~66 deg
// inclination, so its HORIZONTAL component is only ~20 uT -- a full turn about
// the vertical axis therefore sweeps ~40 uT on each horizontal axis and almost
// nothing on the vertical one. That is why a level calibration turn scores 2 of
// 3 axes and only tumbling the owl scores 3, and why two is the bar for a
// usable heading.
#define MAG_CAL_MIN_SPAN_UT 25.0f

// Consecutive implausible reads before the IMU is declared dead, and how long
// to wait before probing WHO_AM_I again.
//
// THE BACKOFF IS THE POINT. Every failed I2C access costs the full
// I2C_TIMEOUT_MS (250 ms). Polling a mute sensor at 50 Hz would stop the main
// loop dead -- exactly the defect getGps() already had twice (SPEC-005).
#define IMU_MAX_READ_FAILS 5
#define IMU_FAIL_BACKOFF_MS 5000
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
// Mindestabstand zwischen zwei Erkennungslaeufen, jetzt auf KERN 0.
//
// Seit 2026-08-31 laeuft die Inferenz in einer eigenen Aufgabe auf Kern 0
// (siehe include/vision.h), nicht mehr in loop(). Damit ist dieser Wert das
// erste Mal wirklich nur noch ein Mindestabstand: er verzoegert kein Bild mehr,
// sondern begrenzt nur, wie oft Kern 0 das PSRAM und den Cache mitbenutzt.
//
// Der Wert war bis dahin ein TAUSCH, weil die Inferenz die Hauptschleife
// blockierte - am 2026-08-31 auf Hardware gegeneinander bewertet:
//
//   100: neues Blickziel alle 180 ms, Bildrate 5,8 Hz  -> Augen fast dauernd
//        eingefroren, wirkt tot
//   300: neues Blickziel alle 411 ms, Bildrate 29 Hz   -> Augen laufen fluessig,
//        folgen aber traeger. Vom Besitzer als deutlich lebendiger bewertet
//
// Dieser Tausch existiert nicht mehr, deshalb steht der Wert wieder auf 100.
// ACHTUNG bei einer weiteren Senkung: die Inferenz braucht ohnehin 48-114 ms,
// je nach Gesichtsgroesse im Bild (siehe FaceDetector.h), der
// Abstand kommt oben drauf. 100 -> 0 kauft also hoechstens 100 ms Blickalter,
// laesst Kern 0 aber dauerhaft am Anschlag laufen (Cache- und PSRAM-Konkurrenz
// mit dem Rendern auf Kern 1, plus IDLE0 knapp am Task-Watchdog vorbei).
#define FACE_DETECT_INTERVAL_MS 100

// Stapel der Erkennungsaufgabe in BYTE (die ESP-IDF-Portierung von
// xTaskCreate rechnet in Byte, nicht in Worten).
//
// 8192, weil die Inferenz vorher auf dem Arduino-Loop-Stapel lief und der genau
// so gross ist (CONFIG_ARDUINO_LOOP_STACK_SIZE=8192) - dort hat esp-dl
// nachweislich funktioniert, inklusive des Modell-Ladens beim ersten run().
// Ein Stapelueberlauf hier waere ein sporadischer Absturz mitten in der
// Erkennung, deshalb meldet die Telemetrie face.stack_free (Tiefstand) dauerhaft
// mit. Verkleinern nur gegen diese Zahl, nicht nach Gefuehl.
#define VISION_TASK_STACK 8192

// Prioritaet der Erkennungsaufgabe. 1 = dieselbe wie der Arduino-Loop.
// Hoeher bringt nichts (sie ist auf Kern 0 allein), niedriger wuerde sie im
// UPDATE-Modus hinter den WLAN-Aufgaben verhungern - dort ist sie aber aus.
#define VISION_TASK_PRIORITY 1

// Wartezeit einer Runde, wenn die Erkennung abgeschaltet ist (UPDATE-Modus).
// Nur, damit die Aufgabe nicht im Leerlauf rotiert.
#define VISION_PAUSED_POLL_MS 100

// esp-dl v3 knobs. NOTE the old MSR01 parameters (top_k, resize_scale) do not
// exist in v3 -- the model handles its own preprocessing. What remains are the
// two stage thresholds of the MSRMNP model (stage 0 = MSR coarse, 1 = MNP fine).
//
// 0.5 is the library default and measured right: real detections score
// 0.58-1.00, mostly above 0.9. Lowering these is only useful while debugging
// and invites false positives in production.
// Schwelle der FEINEN Stufe (MNP). Das ist der Wert, der in den gemeldeten
// Scores landet, und 0,5 ist hier richtig.
#define FACE_SCORE_THRESHOLD 0.5f

// Schwelle der GROBEN Stufe (MSR). Bewusst NIEDRIG.
//
// Die Erkennung laeuft zweistufig: MSR schlaegt Kandidatenbereiche vor, MNP
// bewertet sie fein. Wer die grobe Stufe streng einstellt, wirft Gesichter weg,
// bevor die feine Stufe sie ueberhaupt zu sehen bekommt - und die Genauigkeit
// haengt trotzdem an MNP, weil von dort der gemeldete Score stammt.
//
// esp-dl setzt beide Stufen per Voreinstellung auf 0,5. Adafruits
// funktionierender "MEMENTO Shoulder Robot" fuehrt die grobe Stufe dagegen mit
// 0,1 und nur die feine mit 0,5:
//   HumanFaceDetectMSR01 s1(0.1F, 0.5F, 10, 0.2F);
//   HumanFaceDetectMNP01 s2(0.5F, 0.3F, 5);
//
// ACHTUNG: der frueher hier notierte Satz "Schwellwerte sind nicht der Hebel"
// bezog sich auf den ENDSCORE. Fuer die grobe Stufe galt er nie.
#define FACE_SCORE_THRESHOLD_MSR 0.1f
#define FACE_NMS_THRESHOLD 0.5f
// Post-filter on top of the model: only treat a face as "detected" (and drive
// gaze/state) above this, so low-confidence flicker cannot flip the state
// machine.
#define FACE_MIN_CONFIDENCE 0.5f

// Der Detektor bekommt nur den MITTIGEN AUSSCHNITT des Kamerabildes, mit
// diesem Teiler: 2 = halbe Breite und Hoehe, also ein Viertel der Flaeche.
//
// Warum das der entscheidende Hebel ist: run() skaliert sein Eingangsbild
// ohnehin auf die Modellaufloesung, also zaehlt die RELATIVE Groesse des
// Gesichts, nicht die in Pixeln. Ein groesseres Kamerabild (VGA statt QVGA)
// bringt deshalb GAR NICHTS - das Modell sieht bei gleichem Blickwinkel exakt
// dasselbe. Ein Ausschnitt verdoppelt die relative Groesse dagegen sofort.
//
// Auf Hardware gemessen 2026-08-29, Person auf 1 m Abstand, gleiches Licht,
// gleiche Stelle, mit facelab als Vergleich:
//     ganzes Bild   0,0 % der Bilder mit Gesicht  (Kopf ~40 px von 320)
//     Ausschnitt 2  59,4 %                        (Score im Mittel 0,82)
// Auf 30 cm lag das ganze Bild bei 22 %. Das Bild war in beiden Faellen scharf
// und gut belichtet - es war nie eine Qualitaets-, immer eine Groessenfrage.
//
// Preis: kleineres Blickfeld. Wer weiter aussen steht, wird nicht mehr gesehen.
// 1 schaltet den Ausschnitt ab und liefert das alte Verhalten.
#define CAM_DETECT_CROP_DIV 2
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

// Short HAPPY burst on entering INTERACTING, then the calm AWE face.
// Affect is legible through change: an expression pinned at full amplitude
// stops reading as an expression and becomes a mask. So: a spike on the
// event, a plateau for the state.
#define INTERACT_GREET_MS 1000      // how long HAPPY flashes on noticing you

// Temporary overrides sent by the Orange Pi supervisor (do not change state)
#define EXPRESSION_OVERRIDE_MS 3000 // how long an Orange Pi expression override lasts
#define GAZE_OVERRIDE_MS 3000       // how long an Orange Pi gaze override lasts

// Navigation: the Orange Pi re-sends the nav angle on each refresh. If it stops
// (serial link dropped, Orange Pi crashed/rebooted) for this long, the owl recenters
// its head and leaves NAVIGATING so it is never left stuck pointing somewhere.
// Must be comfortably larger than the Orange Pi's refresh interval (see
// orangepi-brain config.yaml navigation.refresh_min_s) but small enough that a dead
// link is noticed quickly.
#define NAV_TIMEOUT_MS 5000

// Wiring-verification boot. 0 (default) = normal boot into the state machine.
// Build with -DHARDWARE_CHECK=1 (platformio.ini build_flags, or `pio run
// --project-option="build_flags=... -DHARDWARE_CHECK=1"`) to instead probe
// every peripheral (LCDs, PCA9685, GPS, LSM303AGR, vibration, camera) and report
// each one's status over serial + on the eyes, then idle. See runHardwareCheck()
// in src/hardware_check.cpp, which compiles to nothing unless this is 1.
// Re-flash without the flag to return to normal operation.
#ifndef HARDWARE_CHECK
#define HARDWARE_CHECK 0
#endif

// The power-rail probe is no longer a flag on this firmware: it lives in its
// own minimal sketch, src/powerprobe.cpp, built by `pio run -e powerprobe`.
// As a `#if POWER_PROBE` branch inside main.cpp it still linked the entire
// firmware and only skipped it at runtime, which made it useless as the
// light-load comparison it exists to be.
