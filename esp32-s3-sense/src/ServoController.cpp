#include "ServoController.h"

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
    if (channel >= PCA9685_NUM_CHANNELS) return;
    angle = constrain(angle, SERVO_MIN_ANGLE, SERVO_MAX_ANGLE);
    _targetAngles[channel] = angle;
    _dirty[channel] = true;
}

void ServoController::setCenter() {
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
    return _currentAngles[channel];
}

void ServoController::update() {
    if (!_pcaReady) return;  // PCA9685 not present - never touch the I2C bus
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
    if (!_pcaReady) return;  // PCA9685 not present - never touch the I2C bus
    _pca.setPWM(channel, 0, us);
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
