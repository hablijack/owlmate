#pragma once

#include <Arduino.h>
#include <Wire.h>
#include "config.h"

// IMU data from BNO055
struct ImuData {
    float pitch;    // degrees, -90 to 90
    float roll;     // degrees, -180 to 180
    float yaw;      // degrees, 0 to 360 -- absolute magnetic heading, but only
                    // once calMag reaches 3 (before that NDOF reports 0)
    bool isCalibrated;
    // Raw BNO055 calibration counters, 0..3 each. Surfaced in telemetry so the
    // figure-8 calibration dance has visible progress instead of a single
    // opaque false. isCalibrated is all four at 3.
    uint8_t calSys;
    uint8_t calGyro;
    uint8_t calAccel;
    uint8_t calMag;
    // True when this boot restored calibration offsets from flash instead of
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
    bool _calRestored;   // offsets came back from NVS at boot
    bool _calSaved;      // offsets have been written to NVS this run
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
