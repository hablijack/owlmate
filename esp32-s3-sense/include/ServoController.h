#pragma once

#include <Arduino.h>
#include <Adafruit_PWMServoDriver.h>
#include "config.h"

class ServoController {
public:
    ServoController();
    bool begin();

    void setAngle(uint8_t channel, float angle);
    void setCenter();
    void update();

    float getAngle(uint8_t channel) const;

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
