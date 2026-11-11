#pragma once

#include <Arduino.h>
#include <Wire.h>
#include "config.h"

// Orientation from the LSM303AGR (accel 0x19 + LIS2MDL magnetometer 0x1E).
// The fusion lives in lib/OwlImu -- this chip has no gyro and computes nothing
// itself, unlike the BNO055 it replaced. See lib/OwlImu/OwlImu.h for the owl
// body frame these angles are expressed in.
struct ImuData {
    float pitch;    // degrees, -90 to +90;  +90 = beak at the sky
    float roll;     // degrees, -180 to +180; +90 = lying on the right side
    float yaw;      // degrees, 0 to 360 -- TRUE geographic bearing of the beak.
                    // Holds its last trustworthy value while headingOk is false,
                    // rather than emitting a garbage number.
    // haveOffsets && headingOk. Navigation refuses to aim while this is false.
    bool isCalibrated;
    // Magnetometer axes that have seen enough span THIS RUN, 0..3. The progress
    // bar of the calibration turn -- deliberately a different question from
    // isCalibrated. Conflating "do I have usable offsets" with "how far has this
    // run got" is exactly what produced the BNO055 calibrated bug (SPEC-006).
    // A level full turn reaches 2 of 3; only tumbling the owl reaches 3.
    uint8_t magAxes;
    // The current geometry yields a usable heading (beak not near-vertical,
    // field plausible).
    bool headingOk;
    // True when this boot restored hard-iron offsets from flash instead of
    // starting from scratch.
    bool calRestored;
};

// GPS data from PA1010D
struct GpsData {
    float latitude;
    float longitude;
    float altitude; // meters
    uint8_t satellites;  // im Fix verwendet (GGA) - ohne Fix 0, daher die Felder unten
    bool valid;
    // Diagnose, auch OHNE Fix gemeldet (SPEC-010). Trennt "kommt nichts an"
    // (nmeaOk steht) von "sieht nichts" (inView 0) von "sieht zu schwach"
    // (snrMax klein, tracked < 4).
    uint8_t fixQuality;  // GGA: 0 kein Fix, 1 GPS, 2 DGPS
    float hdop;          // GGA, nur mit Fix aussagekraeftig
    uint8_t inView;      // GSV: Satelliten in Sicht, Summe aller Systeme
    uint8_t tracked;     // GSV: davon mit Signal (SNR gemeldet)
    uint8_t snrMax;      // GSV: bestes SNR in dB-Hz, 0 = keiner verfolgt
    uint32_t nmeaOk;     // Saetze mit gueltiger Pruefsumme seit Boot
    uint32_t nmeaBad;    // Saetze mit falscher Pruefsumme seit Boot
};

// Vibration state
struct VibrationData {
    bool detected;
    uint32_t lastDetected;
    uint16_t count;
    bool knock;            // Doppelklopfen bestaetigt (haelt VIBRATION_HOLD_MS); das ist, was Verhalten und Schreck brauchen
};

class Sensors {
public:
    bool begin();

    // IMU wie die Vibration in zwei Haelften, und aus demselben Grund:
    // updateImu() macht die Arbeit (Bus lesen, filtern, Kalibrierung
    // mitschreiben, Offsets bei Bedarf ins Flash), getImu() liest nur ab.
    // updateImu() gehoert einmal pro Hauptschleifendurchlauf aufgerufen; es
    // begrenzt sich selbst auf IMU_SAMPLE_INTERVAL_MS.
    //
    // Beim BNO055 stand die Flash-Schreiblogik in getImu() - also in einem
    // Getter, den die Telemetrie ruft. Das war schon damals der falsche Ort.
    void updateImu();
    ImuData getImu();
    // GPS ebenso: updateGps() holt den I2C-Puffer des PA1010D ab und parst,
    // getGps() liest nur ab. Einmal pro Runde aufrufen; begrenzt sich selbst
    // auf GPS_POLL_INTERVAL_MS. Bis 2026-11-10 las getGps() im Telemetrietakt
    // und war damit kein reiner Getter.
    void updateGps();
    GpsData getGps();
    // Vibration in zwei Haelften: updateVibration() holt die Flanken ab und
    // fuehrt die Zustandslogik, getVibration() liest nur den Stand aus.
    // Getrennt, weil BEIDE Aufrufer (updateState mit 60 Hz und sendTelemetry
    // mit 2 Hz) sonst denselben Impulszaehler leeren und sich die Flanken
    // gegenseitig wegnehmen. updateVibration() gehoert genau einmal pro
    // Hauptschleifendurchlauf aufgerufen.
    void updateVibration();
    // Meldet, ob sich die Servos gerade selbst bewegen. Dann (und VIBRATION_SELF_MOTION_TAIL_MS
    // danach) zaehlen Flanken nicht als Klopfen; vibrationPulseTotal() zaehlt weiter roh.
    // Einmal pro Runde VOR updateVibration() aufrufen.
    void setSelfMotion(bool moving);
    VibrationData getVibration();
    // Rohe Gesamtzahl der Flanken seit dem Boot, direkt aus dem Interrupt.
    // Absichtlich dauerhaft in der Telemetrie: bei einem prellenden Sensor ist
    // das der einzige Weg zu sehen, ob ueberhaupt etwas am Pin passiert -
    // unabhaengig von jeder Auswertelogik. Ruhe = konstant, Klopfen = springt
    // um Hunderte. Ein staendig steigender Wert bei stillstehender Eule heisst
    // Stoerung oder Poti zu empfindlich.
    uint32_t vibrationPulseTotal() const;

    bool isImuReady() const { return _imuReady; }
    bool isGpsReady() const { return _gpsReady; }

private:
    bool _imuReady;
    bool _calSaved;          // in diesem Lauf schon einmal gespeichert
    float _calSavedSpan[3];  // Spannen zum Zeitpunkt des letzten Speicherns (MagCalRule.h)
    uint32_t _calSavedAt;    // millis() des letzten Speicherns
    bool _gpsReady;

    // Vibration debounce state
    // Vibration: Flanken werden per Interrupt gezaehlt (siehe Sensors.cpp), der
    // Pegel selbst wird NICHT ausgewertet - der SW-420 prellt mit ~1 kHz.
    bool _vibState;            // true solange das letzte Buendel noch "haelt"
    uint32_t _vibLastPulse;    // millis() der letzten gezaehlten Flanke
    uint32_t _prevBurstStart;  // Beginn des vorigen Buendels (fuer das Doppelklopfen)
    uint32_t _knockUntil;      // bis dahin gilt ein bestaetigtes Doppelklopfen
    uint32_t _selfMotionUntil; // bis dahin zaehlen Flanken nicht (eigene Servobewegung)
    uint32_t _vibLastEvent;    // millis() des letzten bestaetigten Klopfers
    uint16_t _vibCount;        // Anzahl bestaetigter Klopfer
};
