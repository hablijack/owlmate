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
    uint8_t satellites;
    bool valid;
};

// Vibration state
struct VibrationData {
    bool detected;
    uint32_t lastDetected;
    uint16_t count;
    bool updateSequence; // one-shot: set for one read after a 4-tap sequence
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
    GpsData getGps();
    // Vibration in zwei Haelften: updateVibration() holt die Flanken ab und
    // fuehrt die Zustandslogik, getVibration() liest nur den Stand aus.
    // Getrennt, weil BEIDE Aufrufer (updateState mit 60 Hz und sendTelemetry
    // mit 2 Hz) sonst denselben Impulszaehler leeren und sich die Flanken
    // gegenseitig wegnehmen. updateVibration() gehoert genau einmal pro
    // Hauptschleifendurchlauf aufgerufen.
    void updateVibration();
    VibrationData getVibration();
    // Rohe Gesamtzahl der Flanken seit dem Boot, direkt aus dem Interrupt.
    // Absichtlich dauerhaft in der Telemetrie: bei einem prellenden Sensor ist
    // das der einzige Weg zu sehen, ob ueberhaupt etwas am Pin passiert -
    // unabhaengig von jeder Auswertelogik. Ruhe = konstant, Klopfen = springt
    // um Hunderte. Ein staendig steigender Wert bei stillstehender Eule heisst
    // Stoerung oder Poti zu empfindlich.
    uint32_t vibrationPulseTotal() const;

    // Consume the one-shot 4-tap update flag.
    void clearUpdateSequence() { _updateSeqPending = false; }

    bool isImuReady() const { return _imuReady; }
    bool isGpsReady() const { return _gpsReady; }

private:
    bool _imuReady;
    bool _calSaved;      // hard-iron offsets have been written to NVS this run
    bool _gpsReady;

    // Vibration debounce state
    // Vibration: Flanken werden per Interrupt gezaehlt (siehe Sensors.cpp), der
    // Pegel selbst wird NICHT ausgewertet - der SW-420 prellt mit ~1 kHz.
    bool _vibState;            // true solange das letzte Buendel noch "haelt"
    uint32_t _vibLastPulse;    // millis() der letzten gezaehlten Flanke
    uint32_t _vibLastEvent;    // millis() des letzten bestaetigten Klopfers
    uint16_t _vibCount;        // Anzahl bestaetigter Klopfer
    uint8_t _rapidTapCount;    // Klopfer in der laufenden Schnellfolge
    bool _updateSeqPending;    // Einmal-Flag, von der State-Machine abgeholt
};
