#pragma once
#include <cstdint>
extern int driverWrites;
struct FakeWire {
    int address = -1;
    int dataWrites = 0;
    bool present = true;
    void beginTransmission(uint8_t a) { address = a; }
    void write(uint8_t) { ++dataWrites; }
    int endTransmission() { return present ? 0 : 2; }
    int requestFrom(uint8_t, uint8_t) { return 0; }
    int read() { return 0; }
};
extern FakeWire Wire;
class Adafruit_PWMServoDriver {
public:
    // Count begin/frequency changes too: not only explicit PWM writes move servos.
    bool begin() { ++driverWrites; return true; }
    void setPWMFreq(float) { ++driverWrites; }
    int setPWM(uint8_t, uint16_t, uint16_t) { ++driverWrites; return 0; }
    void writeMicroseconds(uint8_t, uint16_t) { ++driverWrites; }
};
