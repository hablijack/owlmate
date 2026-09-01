#pragma once

#include <Arduino.h>
#include <Wire.h>

// ============================================================================
// Lage und Kurs aus dem Adafruit LSM303AGR.
//
// WARUM DAS EINE EIGENE BIBLIOTHEK IST UND NICHT DREI ZEILEN IN Sensors.cpp:
// Der BNO055 war ein FUSIONS-Chip. Er hat Lage, absoluten Kurs, Achsenumbau und
// Kalibrierung in Silizium gerechnet und fertige Eulerwinkel herausgegeben. Der
// LSM303AGR tut nichts davon. Er hat ausserdem KEIN GYROSKOP - er ist ein
// 6-DOF-Teil (Beschleunigung + Magnetfeld), kein 9-DOF-Teil. Alles, was der
// BNO055 intern erledigt hat, muss hier von Hand passieren.
//
// Die Diagnose-Umgebung `imuaxis` benutzt GENAU DIESE Klasse. Das ist der Grund
// fuer die Bibliothek: eine Diagnose, die ihre eigene Kopie der Mathematik
// mitbringt, kann die Firmware nicht pruefen - sie prueft dann sich selbst.
//
// ZWEI ADAFRUIT-PLATINEN, DIE GLEICH AUSSEHEN UND ES NICHT SIND. Adafruit hat
// zwei LSM303-Breakouts: das aeltere LSM303DLHC (BLAU, Aufdruck "LSM303DLHC")
// und das LSM303AGR (SCHWARZ, Aufdruck "LSM303AGR"). Beide haben den
// Beschleunigungssensor auf 0x19 und das Magnetometer auf 0x1E, und der
// Beschleunigungstreiber ist derselbe - aber die REGISTERBELEGUNG DER
// MAGNETOMETER IST VOELLIG VERSCHIEDEN. Wer das DLHC-Magnetometer (Bibliothek
// "Adafruit LSM303DLH Mag") gegen ein AGR laufen laesst, bekommt keinen Fehler,
// sondern MUELL - also einen Kurs, der plausibel aussieht und falsch ist. Das
// AGR-Magnetometer ist ein LIS2MDL; die richtige Bibliothek heisst deshalb
// "Adafruit LIS2MDL". Siehe
// https://learn.adafruit.com/lsm303-accelerometer-slash-compass-breakout/which-lsm303-do-i-have
//
// Dagegen ist hier absichtlich eine Sicherung eingebaut: begin() prueft BEIDE
// WHO_AM_I-Register (Beschleunigung 0x0F -> 0x33, Magnetometer 0x4F -> 0x40).
// Das DLHC-Magnetometer hat auf 0x4F gar kein WHO_AM_I, es kann die 0x40 also
// nicht liefern. Ein vertauschtes Board fliegt damit beim Start auf und
// erzeugt keinen stillen Kursfehler.
// ============================================================================

// --- Koerperfestes Koordinatensystem der Eule -------------------------------
// Diese Konvention gilt fuer die ganze Datei UND fuer imu.* in der Telemetrie:
//
//   +X = nach vorn, aus dem Schnabel heraus
//   +Y = nach links (aus Sicht der Eule)
//   +Z = nach oben
//
// A LEVEL OWL THEREFORE READS ACCEL (0, 0, +9.81): an accelerometer at rest
// measures the reaction to gravity, so its vector points UP, not down.
//
//   pitch = +90 deg  <=>  the beak points at the sky   (a = (+1g, 0, 0))
//   roll  = +90 deg  <=>  the owl lies on its right side (a = (0, +1g, 0))
//   yaw            = compass bearing of the beak, 0..360, clockwise from north
//
// yaw is a TRUE GEOGRAPHIC bearing, not a magnetic one: IMU_HEADING_OFFSET_DEG
// folds the magnetic declination in, because geo.bearing_deg() on the RPi
// computes a true bearing and both sides must share one north reference.

// --- Konfiguration ----------------------------------------------------------
// WIRD HEREINGEGEBEN, NICHT AUS config.h GELESEN. Die Bibliotheken unter lib/
// sind in diesem Projekt in sich geschlossen (lib/Eyes bringt sein eigenes
// common.h mit) und sehen include/ nicht. Das ist hier kein Umweg, sondern
// richtig: config.h bleibt die eine Wahrheit fuer Pins und Zahlen, und
// include/imu_config.h fuellt diese Struktur an EINER Stelle, aus der sich
// sowohl die Firmware als auch die Diagnose bedienen.
struct OwlImuConfig {
    uint8_t accelAddr = 0x19;
    uint8_t magAddr = 0x1E;
    // Welche SENSOR-Achse auf welche EULEN-Achse geht: 1/2/3 = x/y/z,
    // Vorzeichen = Richtung. {1,2,3} ist die Identitaet.
    int8_t remapX = 1, remapY = 2, remapZ = 3;
    float headingOffsetDeg = 0.0f;
    uint16_t sampleIntervalMs = 20;
    float filterAlpha = 0.15f;
    float magMinSpanUt = 25.0f;
    uint8_t maxReadFails = 5;
    uint16_t failBackoffMs = 5000;
};

struct OwlImuSample {
    float pitch = 0.0f;      // deg, -90..+90
    float roll  = 0.0f;      // deg, -180..+180
    float yaw   = 0.0f;      // deg, 0..360, true geographic bearing of the beak

    // Der Kurs ist im Moment geometrisch brauchbar (siehe headingValid() in
    // der .cpp: Schnabel nicht senkrecht, Magnetfeld plausibel).
    bool headingOk = false;
    // Es liegen ueberhaupt Hartmagnet-Offsets vor - aus dem Flash oder in
    // diesem Lauf eingedreht. Ohne die ist jeder Kurs Zufall.
    bool haveOffsets = false;
    // Wie viele Magnetometer-Achsen in DIESEM Lauf genug Spanne gesehen haben,
    // 0..3. Das ist der Fortschrittsbalken der Kalibrierdrehung, nicht die
    // Vertrauensfrage - die beantwortet haveOffsets.
    uint8_t magAxes = 0;
    // Offsets kamen beim Booten aus dem NVS.
    bool calRestored = false;

    // Gefiltert und in Eulenkoordinaten umgerechnet. Fuer die Diagnose.
    float ax = 0.0f, ay = 0.0f, az = 0.0f;   // m/s^2
    float mx = 0.0f, my = 0.0f, mz = 0.0f;   // uT, hartmagnetkorrigiert
};

class OwlImu {
public:
    // Prueft beide WHO_AM_I-Register und konfiguriert die Sensoren.
    // false = kein LSM303AGR am Bus (oder das falsche Board, siehe oben).
    bool begin(const OwlImuConfig& cfg, TwoWire* wire = &Wire);

    // Sensoren antworten und liefern plausible Werte.
    bool ok() const { return _ready && !_failed; }

    // GEHOERT EINMAL PRO HAUPTSCHLEIFENDURCHLAUF AUFGERUFEN, aus loop().
    // Begrenzt sich selbst auf IMU_SAMPLE_INTERVAL_MS, filtert, und sammelt
    // dabei die Min/Max-Werte fuer die Hartmagnetkalibrierung ein.
    //
    // WARUM NICHT ERST IM TELEMETRIETAKT (2 Hz): zwei Gruende, beide bezahlt.
    // Erstens hat dieser Chip kein Gyroskop und keine interne Fusion - die
    // Lage kommt allein aus der Beschleunigung, und die ist waehrend einer
    // Servobewegung verrauscht. Filtern braucht Abtastrate. Zweitens sammelt
    // die Kalibrierung ihre Min/Max-Werte hier ein: bei 2 Hz bekaeme eine
    // langsame Volldrehung ein paar Dutzend Punkte statt einiger Hundert.
    // Genau dasselbe Muster wie Sensors::updateVibration() - und aus einem
    // verwandten Grund (siehe dort).
    // true = ein frischer Messwert wurde genommen.
    bool update();

    // REINER GETTER. Aendert nichts, verbraucht nichts, kann beliebig oft
    // gerufen werden. Absichtlich so: getVibration() war einmal ein Getter,
    // der den Impulszaehler geleert hat, und die zwei Aufrufer haben sich
    // gegenseitig die Flanken gestohlen (siehe Sensors.h).
    OwlImuSample read() const { return _sample; }

    // --- Hartmagnetkalibrierung --------------------------------------------
    // Offsets aus dem Flash einspielen. Setzt haveOffsets, OHNE die
    // Achsenzaehlung dieses Laufs zu faelschen: magAxes bleibt, was in diesem
    // Lauf wirklich beobachtet wurde. Die zwei Fragen "habe ich brauchbare
    // Offsets?" und "wie weit ist die Drehung diesmal?" sind verschieden, und
    // beim BNO055 hat genau ihre Vermischung den calibrated-Fehler erzeugt
    // (SPEC-006 Falsified).
    void setHardIron(float x, float y, float z);
    // Die in diesem Lauf eingedrehten Offsets, nur wenn sie es wert sind
    // (>= 2 Achsen). false = noch nichts zu speichern.
    bool getHardIron(float& x, float& y, float& z) const;
    // Beobachtete Spanne pro Achse in uT, fuer die Anzeige am Bench.
    void magSpan(float& x, float& y, float& z) const;
    void resetMagCal();

private:
    bool readRaw(float a[3], float m[3]);
    void fuse();
    // Offsets und Achsenabdeckung aus den in DIESEM Lauf beobachteten
    // Min/Max-Werten. Rueckgabe: Anzahl Achsen mit genug Spanne (0..3).
    uint8_t liveAxes(float off[3]) const;

    OwlImuConfig _cfg;
    TwoWire* _wire = nullptr;
    bool _ready = false;
    bool _failed = false;
    uint8_t _failCount = 0;
    uint32_t _lastSample = 0;
    uint32_t _backoffUntil = 0;
    bool _first = true;

    float _fa[3] = {0, 0, 0};    // gefilterte Beschleunigung, Eulenrahmen
    float _fm[3] = {0, 0, 0};    // gefiltertes Magnetfeld, Eulenrahmen, ROH

    float _hard[3] = {0, 0, 0};  // Hartmagnet-Offsets
    bool _haveOffsets = false;
    bool _restored = false;
    float _magMin[3] = {0, 0, 0};
    float _magMax[3] = {0, 0, 0};
    bool _magSeen = false;

    float _lastYaw = 0.0f;
    bool _haveYaw = false;

    OwlImuSample _sample;
};
