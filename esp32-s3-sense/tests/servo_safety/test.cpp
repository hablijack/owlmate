// Compile the REAL ServoController.cpp against observable hardware doubles.
#include <cassert>
#include "ServoController.h"
FakeWire Wire;
FakeSerial Serial;
int driverWrites = 0;

int main() {
    static_assert(SERVO_SAFETY_LOCKED == 1, "Deployment safety lock must be on");
    ServoController servos;
    assert(servos.locked());
    assert(servos.begin());
    assert(servos.ready());
    assert(Wire.address == ADDR_PCA9685);
    assert(driverWrites == 0); // begin() must not call the PWM library at all
    for (int iteration = 0; iteration < 1000; ++iteration) {
        for (int channel = 0; channel < PCA9685_NUM_CHANNELS; ++channel) {
            assert(!servos.setAngle(channel, iteration % 91 - 45));
            assert(!servos.stepOutput(channel, 1));
            assert(!servos.stepOutput(channel, -1));
            assert(servos.getAngle(channel) == 0);
        }
        servos.setCenter(); // BOOT / IDLE return / SLEEPING / UPDATE / ERROR
        servos.update();
    }
    assert(driverWrites == 0);
    assert(Wire.dataWrites == 0); // not even a register pointer/prescaler write
    Wire.present = false;
    assert(!servos.begin());
    assert(!servos.ready());
    servos.setCenter();
    assert(!servos.setAngle(2, 0));
    servos.update();
    assert(driverWrites == 0 && Wire.dataWrites == 0);
}
