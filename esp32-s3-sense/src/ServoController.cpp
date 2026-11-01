#include "ServoController.h"

// Dieses Sicherheitsabbild ist NICHT durch einen Flag-Wechsel freizugeben.
// Erst Portbelegung, individuelle Grenzen und Probe-Pulsbreiten verifizieren.
//
// Ausnahme (2026-10-31): das Bewegungs-Image (SERVO_MOTION_ENABLED=1, eigenes Env). Dort
// ist die Sperre durch den BUILD aufgehoben und die Bewegung laeuft ausschliesslich
// ueber lib/ServoMotion mit den gemessenen Grenzen aus servo_profiles.h.
static_assert(SERVO_SAFETY_LOCKED == 1 || SERVO_MOTION_ENABLED == 1,
              "Servo mapping/travel limits not verified");

#if SERVO_MOTION_ENABLED
#include <Wire.h>
#include "ServoMotion.h"
#include "servo_profiles.h"
#include "i2c_lock.h"

namespace {
class WireBus : public ServoBus {
public:
    bool read(uint8_t reg, uint8_t* buf, uint8_t n) override {
        I2cGuard i2c;
        Wire.beginTransmission(ADDR_PCA9685);
        Wire.write(reg);
        if (Wire.endTransmission() != 0) return false;
        if (Wire.requestFrom(uint8_t(ADDR_PCA9685), n) != n) return false;
        for (uint8_t i = 0; i < n; i++) buf[i] = Wire.read();
        return true;
    }
    bool write(uint8_t reg, const uint8_t* buf, uint8_t n) override {
        I2cGuard i2c;
        Wire.beginTransmission(ADDR_PCA9685);
        Wire.write(reg);
        Wire.write(buf, n);
        return Wire.endTransmission() == 0;
    }
    void delayMs(uint32_t ms) override { delay(ms); }
};
WireBus motionBus;
ServoMotion motion(motionBus, SERVO_PROFILES, NUM_SERVO_PROFILES);

// Der Servo-Task bedient den PCA9685 einmal pro PWM-Periode (20 ms bei 50 Hz, 10 ms bei 100 Hz), unabhaengig von der Hauptschleife.
// Deren Aussetzer (80-110 ms: GPS-Lesen, Augen-Flush, Telemetrie) kamen sonst als Rucke
// an den Servos an. motionMutex schuetzt den ServoMotion-Zustand (Ziele, Positionen);
// Reihenfolge der Locks immer motionMutex -> I2C-Bus, nie umgekehrt.
SemaphoreHandle_t motionMutex = nullptr;
struct MotionLock {
    MotionLock() { if (motionMutex) xSemaphoreTake(motionMutex, portMAX_DELAY); }
    ~MotionLock() { if (motionMutex) xSemaphoreGive(motionMutex); }
};
void servoTaskFn(void*) {
    TickType_t last = xTaskGetTickCount();
    for (;;) {
        vTaskDelayUntil(&last, pdMS_TO_TICKS(ServoMotion::framePeriodMs()));
        MotionLock lock;
        motion.update(millis());
    }
}
}  // namespace
#endif

// The channels actually populated, for iteration. Order matches the telemetry
// "servos" array. Everything else on the PCA9685 is left alone: update() only
// writes channels whose dirty flag is set, and nothing ever sets it for an
// unpopulated one.
static const uint8_t USED_CHANNELS[NUM_SERVOS] = {
    CH_LEFT_EAR, CH_RIGHT_EAR, CH_HEAD, CH_LEFT_WING, CH_RIGHT_WING
};

ServoController::ServoController() {
    _pcaReady = false;
    for (int i = 0; i < PCA9685_NUM_CHANNELS; i++) {
        _currentAngles[i] = 0;
        _targetAngles[i] = 0;
        _dirty[i] = false;
    }
}

bool ServoController::begin() {
#if SERVO_MOTION_ENABLED
    // Jeder benutzte Kanal braucht ein gueltiges Profil, sonst startet nichts.
    for (int i = 0; i < NUM_SERVOS; i++) {
        if (!motion.hasProfile(USED_CHANNELS[i])) {
            Serial.println(F("WARNING: servo profile missing/invalid - servos disabled"));
            return _pcaReady = false;
        }
    }
    _pcaReady = motion.begin();
    if (!_pcaReady) {
        Serial.println(F("WARNING: PCA9685 not ready - servos disabled"));
        return false;
    }
    motionMutex = xSemaphoreCreateMutex();
    // Prioritaet 2 (Arduino-loop: 1), Kern 1: kurz und periodisch, unterbricht die
    // Schleife nur fuer die I2C-Schreibzugriffe von ein, zwei Kanaelen.
    xTaskCreatePinnedToCore(servoTaskFn, "servo", 4096, nullptr, 2, nullptr, 1);
    return true;
#endif
    if (locked()) {
        // Nur ACK pruefen. Adafruit begin() setzt MODE1 zurueck und aendert
        // die Frequenz: auch OHNE setAngle waere das bereits eine Bewegung.
        // Warmstart: vorhandene PWM unveraendert lassen. Kaltstart: keinen
        // unbekannten Ausgang aktivieren. Kein General-Call/Reset an 0x00.
        Wire.beginTransmission(ADDR_PCA9685);
        _pcaReady = Wire.endTransmission() == 0;
        return _pcaReady;
    }
    // The PCA9685 is an I2C device. If it isn't reachable (unwired, no ACK, or
    // the I2C bus is down) _pca.begin() returns false and the library's internal
    // Adafruit_I2CDevice is left pointing at a non-ready bus. Driving it after
    // that (setPWM -> i2c_dev->write) dereferences a null TwoWire and panics
    // with LoadProhibited. So we record whether it came up and no-op all the
    // PWM writes when it didn't.
    _pcaReady = _pca.begin();
    if (!_pcaReady) {
        Serial.println(F("WARNING: PCA9685 servo driver not found - servos disabled"));
        return false;
    }
    _pca.setPWMFreq(50); // 50Hz for servos

    // Set all to center position
    setCenter();
    return true;
}

bool ServoController::setPos(uint8_t channel, float pos) {
#if SERVO_MOTION_ENABLED
    if (!_pcaReady) return false;
    MotionLock lock;
    return motion.setTarget(channel, pos);
#else
    (void)channel; (void)pos;
    return false;
#endif
}

bool ServoController::setAngle(uint8_t channel, float angle) {
#if SERVO_MOTION_ENABLED
    if (!isfinite(angle)) return false;
    // Kopf: Grad direkt. Alle anderen: -45..45 -> 0..100.
    const float pos = channel == CH_HEAD ? angle : 50.0f + angle * (50.0f / 45.0f);
    return setPos(channel, pos);
#endif
    if (locked() || !_pcaReady || channel >= PCA9685_NUM_CHANNELS || !isfinite(angle)) return false;
    angle = constrain(angle, SERVO_MIN_ANGLE, SERVO_MAX_ANGLE);
    _targetAngles[channel] = angle;
    _dirty[channel] = true;
    return true;
}

bool ServoController::stepOutput(uint8_t channel, int8_t direction) {
#if SERVO_MOTION_ENABLED
    (void)channel; (void)direction;
    return false;   // keine Wartungsschritte im Bewegungs-Image
#endif
    if (locked() || !_pcaReady || channel >= PCA9685_NUM_CHANNELS
        || (direction != -1 && direction != 1) || _dirty[channel]) return false;

    // Read the real output registers, not _currentAngles: a relocated plug may
    // be on a channel the firmware has never driven. Check I2C errors explicitly.
    Wire.beginTransmission(ADDR_PCA9685);
    Wire.write(uint8_t(0x06 + 4 * channel)); // LEDn_ON_L
    if (Wire.endTransmission() != 0) return false;
    if (Wire.requestFrom(uint8_t(ADDR_PCA9685), uint8_t(4)) != 4) return false;
    const uint16_t onLow = Wire.read();
    const uint16_t on = onLow | (uint16_t(Wire.read()) << 8);
    const uint16_t offLow = Wire.read();
    const uint16_t off = offLow | (uint16_t(Wire.read()) << 8);
    // Full-on/off, no pulse, nonstandard phase, or outside the configured
    // output range: no trustworthy starting point. Do NOT enable/center it.
    if (on != 0 || off < SERVO_MIN_US || off > SERVO_MAX_US) return false;
    const int next = int(off) + direction;
    if (next < SERVO_MIN_US || next > SERVO_MAX_US) return false;
    if (_pca.setPWM(channel, 0, uint16_t(next)) != 0) return false;

    // Keep the normal smoother consistent with this output; do not start it
    // from its zero-initialised angle and inadvertently sweep through center.
    const float angle = next >= SERVO_CENTER_US
        ? float(next - SERVO_CENTER_US) * SERVO_MAX_ANGLE / (SERVO_MAX_US - SERVO_CENTER_US)
        : float(SERVO_CENTER_US - next) * SERVO_MIN_ANGLE / (SERVO_CENTER_US - SERVO_MIN_US);
    _currentAngles[channel] = _targetAngles[channel] = angle;
    return true;
}

void ServoController::setCenter() {
#if SERVO_MOTION_ENABLED
    // "Ruhe" = die Ruheposition jedes Profils, nicht "Mitte" (Ohren/Fluegel).
    if (!_pcaReady) return;
    MotionLock lock;
    for (int i = 0; i < NUM_SERVO_PROFILES; i++)
        motion.setTarget(SERVO_PROFILES[i].channel, SERVO_PROFILES[i].rest);
    return;
#endif
    if (locked() || !_pcaReady) return;
    // Only the populated channels - centering an empty channel would write PWM
    // to nothing.
    for (int i = 0; i < NUM_SERVOS; i++) {
        const uint8_t ch = USED_CHANNELS[i];
        _targetAngles[ch] = 0;
        _currentAngles[ch] = 0;
        _dirty[ch] = true;
    }
}

float ServoController::getAngle(uint8_t channel) const {
    if (channel >= PCA9685_NUM_CHANNELS) return 0;
#if SERVO_MOTION_ENABLED
    MotionLock lock;
    return motion.position(channel);
#endif
    return _currentAngles[channel];
}

bool ServoController::moving() const {
#if SERVO_MOTION_ENABLED
    if (!_pcaReady) return false;
    MotionLock lock;
    for (uint8_t i = 0; i < NUM_SERVO_PROFILES; i++) {
        const uint8_t ch = SERVO_PROFILES[i].channel;
        // nur angesteuerte Kanaele: position() liefert fuer ruhende 0, das meldete ewig "in Bewegung"
        if (motion.driven(ch) && fabsf(motion.position(ch) - motion.target(ch)) > 0.5f) return true;
    }
#endif
    return false;
}

void ServoController::update() {
#if SERVO_MOTION_ENABLED
    return;   // der Servo-Task (50 Hz) fuehrt update() aus, nicht die Hauptschleife
#endif
    if (locked() || !_pcaReady) return;  // never advance a locked target
    for (int i = 0; i < PCA9685_NUM_CHANNELS; i++) {
        if (!_dirty[i]) continue;   // unpopulated channels are never dirty

        float diff = _targetAngles[i] - _currentAngles[i];

        if (abs(diff) <= SERVO_SMOOTH_SPEED) {
            _currentAngles[i] = _targetAngles[i];
            _dirty[i] = false;
        } else {
            _currentAngles[i] += (diff > 0 ? SERVO_SMOOTH_SPEED : -SERVO_SMOOTH_SPEED);
        }

        uint16_t us = (uint16_t)angleToUs(_currentAngles[i]);
        writeMicroseconds(i, us);
    }
}

void ServoController::writeMicroseconds(uint8_t channel, uint16_t us) {
    if (locked() || !_pcaReady) return;
    _pca.writeMicroseconds(channel, us);
}

float ServoController::angleToUs(float angle) {
    // Map the angle range onto the pulse range, each half derived from its own
    // endpoint. The previous form computed the upper half as
    // CENTER + (CENTER - MIN), i.e. it silently ASSUMED the travel is symmetric
    // about center and ignored SERVO_MAX_US entirely -- so a servo with an
    // off-center pulse range (1000/2400 is common) would have been driven wrong
    // no matter what config.h said. Identical output for the symmetric
    // 1000/1500/2000 values currently configured.
    if (angle >= 0.0f) {
        return SERVO_CENTER_US + (angle / SERVO_MAX_ANGLE) * (SERVO_MAX_US - SERVO_CENTER_US);
    }
    return SERVO_CENTER_US - (angle / SERVO_MIN_ANGLE) * (SERVO_CENTER_US - SERVO_MIN_US);
}
