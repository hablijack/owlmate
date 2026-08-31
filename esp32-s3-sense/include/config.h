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
// Ueberschreibbar per Build-Flag, damit Diagnose-Envs den Bustakt variieren
// koennen, ohne diese Datei anzufassen. Der BNO055 verletzt beim
// Clock-Stretching die Setup-Zeit zwischen SDA-HIGH und SCL-HIGH und gilt mit
// ESP32/ESP32-S3 als unzuverlaessig (Adafruit: "Troublesome Chips"); der
// dokumentierte Ausweg ist ein deutlich niedrigerer Takt.
#ifndef I2C_FREQ
#define I2C_FREQ 400000
#endif
// Wie lange der Master auf einen Slave wartet, bevor er die Uebertragung
// abbricht. Die Arduino-Voreinstellung (50 ms) ist zu kurz fuer den BNO055:
// er dehnt den Takt lange, der Controller gibt auf, bricht MITTEN in der
// Uebertragung ab - und laesst den Bus liegen. Danach ist nicht nur der IMU
// weg, sondern auch GPS und Servotreiber, weil beide Leitungen unten bleiben.
//
// Auf Hardware gemessen 2026-08-28: mit 50 ms war der Bus nach dem ZWEITEN
// Zugriff tot (jeder Bustakt, jede Bibliothek), mit 1000 ms ueberstand er 12
// Lesezugriffe in Folge unbeschadet. Der Wert zaehlt ZEIT, nicht Takte -
// deshalb half es auch nichts, den Bustakt zu senken.
// 250 ms statt der 1000 ms des ersten Versuchs: der BNO055 dehnt den Takt um
// hoechstens ~600 us, das ist immer noch das Vierhundertfache an Reserve. Der
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
// navigation.aim_sign auf der RPi-Seite.
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
// ACHTUNG bei einer weiteren Senkung: die Inferenz braucht ohnehin 48-91 ms
// (Mittel 66, am 2026-08-31 direkt gemessen - siehe FaceDetector.h), der
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
