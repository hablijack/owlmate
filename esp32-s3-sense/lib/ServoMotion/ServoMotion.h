#pragma once
// ============================================================================
// ServoMotion - die Bewegungsschicht des Bewegungs-Images (SPEC-016, Schritt 6).
//
// Das ist NICHT das Kalibrierwerkzeug (ServoCal) und nicht der gesperrte
// ServoController-Pfad. Es wird nur gebaut, wenn SERVO_MOTION_ENABLED=1 (Env
// xiao_esp32s3; xiao_esp32s3_locked und die Diagnose-Envs bleiben gesperrt).
//
// Jeder Servo hat ein ServoProfile: eine Kennlinie Position -> Mikrosekunden aus
// den am 2026-10-30/31 mit dem Besitzer gemessenen Werten, MIT Reserve zum
// Anschlag schon in der Tabelle. Die Enden der Tabelle sind die Grenzen. Ein Puls
// ausserhalb dieser Grenzen wird von diesem Modul nie geschrieben - auch nicht
// bei NaN, bei absurden Zielen oder nach einem Aussetzer der Schleife.
//
// Garantien, jede durch tests/servomotion/test.cpp gegen ein Fake-PCA9685 belegt:
//   * Es wird nur auf Kanaele mit gueltigem Profil geschrieben, und erst nach dem
//     ersten setTarget() fuer diesen Kanal.
//   * Frequenz/Sleep werden nur geaendert, solange KEIN Kanal einen Puls ausgibt.
//   * Jeder geschriebene Tick liegt in den Grenzen des Profils.
//   * Die Bewegung ist eine kritisch gedaempfte Feder (weich an- und auslaufend) mit
//     Geschwindigkeitsgrenze; ein langer
//     Schleifenaussetzer (dt) fuehrt nie zu einem Sprung.
//   * Nach drei Busfehlern in Folge wird nichts mehr geschrieben (faulted).
// Der erste Puls eines kalten Kanals ist die Ruheposition des Profils; die
// physische Position davor ist nicht lesbar. Ein vorhandener, gueltiger Puls
// (Warmstart) wird uebernommen, dann gibt es keinen Sprung.
// ============================================================================
#include <stdint.h>
#include <stddef.h>

// PWM-Frequenz: PRE_SCALE des PCA9685. 121 = ~50 Hz (4,88 us/Tick), 60 = ~100 Hz (2,44 us/Tick).
// Per Build-Flag -DSERVO_PWM_PRESCALE=<n> umstellbar; Standard 50 Hz.
#ifndef SERVO_PWM_PRESCALE
#define SERVO_PWM_PRESCALE 121
#endif

class ServoBus {
public:
    virtual ~ServoBus() {}
    virtual bool read(uint8_t reg, uint8_t* buf, uint8_t n) = 0;
    virtual bool write(uint8_t reg, const uint8_t* buf, uint8_t n) = 0;
    virtual void delayMs(uint32_t ms) = 0;
};

struct CurvePoint {
    float pos;      // Positionseinheit des Kanals (Grad bzw. Prozent), aufsteigend
    uint16_t us;    // nominale Pulsbreite, streng monoton (auf- oder absteigend)
};

static const uint8_t MAX_CURVE_POINTS = 8;

struct ServoProfile {
    uint8_t channel;
    uint8_t n;
    CurvePoint pts[MAX_CURVE_POINTS];
    float maxSpeed;   // Positionseinheiten pro Sekunde
    float smoothTime; // Sekunden: Zeitkonstante der gedaempften Feder (kleiner = straffer)
    float rest;       // Position des allerersten Pulses eines kalten Kanals
};

class ServoMotion {
public:
    static const uint8_t NUM_CH = 16;
    static const uint8_t PRESCALE = SERVO_PWM_PRESCALE;
    // Ein Tick = (PRESCALE+1)/25 MHz; eine Periode = 4096 Ticks.
    static float tickUs() { return float(PRESCALE + 1) / 25.0f; }
    static uint32_t framePeriodMs() { return (4096UL * (PRESCALE + 1) + 12500UL) / 25000UL; }

    ServoMotion(ServoBus& bus, const ServoProfile* profiles, uint8_t count);

    bool begin();
    bool ready() const { return _ready && !_fault; }
    bool faulted() const { return _fault; }

    // false: unbekannter/ungueltiger Kanal oder nicht-endlicher Wert. Ein Ziel
    // ausserhalb der Grenzen wird auf die Grenze geklemmt.
    bool setTarget(uint8_t ch, float pos);
    void update(uint32_t nowMs);

    float position(uint8_t ch) const;
    float target(uint8_t ch) const;
    bool driven(uint8_t ch) const;
    bool hasProfile(uint8_t ch) const;
    float minPos(uint8_t ch) const;
    float maxPos(uint8_t ch) const;
    uint16_t minTicks(uint8_t ch) const;
    uint16_t maxTicks(uint8_t ch) const;

    static uint16_t usToTicks(float us);
    static float ticksToUs(uint16_t ticks);
    static bool profileValid(const ServoProfile& p);
    static float posToUs(const ServoProfile& p, float pos);
    static float usToPos(const ServoProfile& p, float us);

private:
    struct Chan {
        const ServoProfile* prof;
        bool valid;
        bool hasTarget;
        bool active;
        float cur, vel, tgt;
        float mid, midVel;   // erste Feder: geformtes Ziel (weicher Anlauf)
        uint16_t last;
        uint16_t tMin, tMax;
        uint8_t errs;
    };
    enum Kind { FULL_OFF, FULL_ON, PULSE, UNKNOWN };
    bool readChan(uint8_t ch, Kind& kind, uint16_t& ticks);
    bool writeTicks(uint8_t ch, uint16_t ticks);
    void drive(Chan& c, uint8_t ch, float dt);
    static float smoothDamp(float cur, float target, float& vel, float smoothTime,
                            float maxSpeed, float dt);

    ServoBus& _bus;
    Chan _ch[NUM_CH];
    bool _ready;
    bool _fault;
    uint32_t _lastMs;
    bool _haveLast;
};
