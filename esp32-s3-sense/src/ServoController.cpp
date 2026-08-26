#include "ServoController.h"

ServoController::ServoController() {
    _pcaReady = false;
    for (int i = 0; i < NUM_SERVO_CHANNELS; i++) {
        _currentAngles[i] = 0;
        _targetAngles[i] = 0;
        _dirty[i] = false;
    }
}

bool ServoController::begin() {
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

void ServoController::setAngle(uint8_t channel, float angle) {
    if (channel >= NUM_SERVO_CHANNELS) return;
    angle = constrain(angle, SERVO_MIN_ANGLE, SERVO_MAX_ANGLE);
    _targetAngles[channel] = angle;
    _dirty[channel] = true;
}

void ServoController::setAngles(const float* angles, uint8_t count) {
    for (uint8_t i = 0; i < count && i < NUM_SERVO_CHANNELS; i++) {
        setAngle(i, angles[i]);
    }
}

void ServoController::setCenter() {
    for (int i = 0; i < NUM_SERVO_CHANNELS; i++) {
        _targetAngles[i] = 0;
        _currentAngles[i] = 0;
        _dirty[i] = true;
    }
}

float ServoController::getAngle(uint8_t channel) const {
    if (channel >= NUM_SERVO_CHANNELS) return 0;
    return _currentAngles[channel];
}

void ServoController::update() {
    if (!_pcaReady) return;  // PCA9685 not present - never touch the I2C bus
    for (int i = 0; i < NUM_SERVO_CHANNELS; i++) {
        if (!_dirty[i]) continue;

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
    if (!_pcaReady) return;  // PCA9685 not present - never touch the I2C bus
    _pca.setPWM(channel, 0, us);
}

float ServoController::angleToUs(float angle) {
    // Map angle (-45 to 45) to microseconds (1000 to 2000)
    return SERVO_CENTER_US + (angle / SERVO_MAX_ANGLE) * (SERVO_CENTER_US - SERVO_MIN_US);
}
