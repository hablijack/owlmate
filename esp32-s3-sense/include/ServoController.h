#pragma once

#include <Arduino.h>
#include <Adafruit_PWMServoDriver.h>
#include "config.h"

class ServoController {
public:
    ServoController();
    bool begin();

    // Legacy-Schnittstelle (Supervisor, Navigation): im Bewegungs-Image wird `angle`
    // in die Positionseinheit des Kanals uebersetzt (Kopf: Grad, links positiv; Ohren
    // und Fluegel: -45..45 -> 0..100).
    bool setAngle(uint8_t channel, float angle);
    // Zielposition in der Einheit des Profils (nur Bewegungs-Image, sonst false).
    bool setPos(uint8_t channel, float pos);
    bool locked() const { return SERVO_SAFETY_LOCKED != 0; }
    bool ready() const { return _pcaReady; }
    // One raw PWM tick from the current output; never guess an initial position.
    bool stepOutput(uint8_t channel, int8_t direction);
    void setCenter();
    void update();

    float getAngle(uint8_t channel) const;
    // true, solange irgendein Servo noch auf dem Weg zu seinem Ziel ist (Bewegungs-Image).
    bool moving() const;

private:
    void writeMicroseconds(uint8_t channel, uint16_t us);
    float angleToUs(float angle);

    Adafruit_PWMServoDriver _pca;
    bool _pcaReady;
    // Indexed by PHYSICAL PCA9685 channel, so these must be sized by the chip's
    // channel count, not by how many servos are fitted - the channel numbers in
    // config.h are sparse (the ears sit on 14 and 15).
    float _currentAngles[PCA9685_NUM_CHANNELS];
    float _targetAngles[PCA9685_NUM_CHANNELS];
    bool _dirty[PCA9685_NUM_CHANNELS];
};
