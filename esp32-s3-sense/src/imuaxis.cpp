// ============================================================================
// BNO055 mounting-orientation + calibration helper (env: imuaxis)
//
// Two jobs:
//
// 1. WORK OUT THE AXIS REMAP. The BNO055 fuses in ITS OWN frame, so if the
//    board is not sitting upright-and-forward inside the owl, `yaw` is not the
//    owl's compass bearing and roll/pitch are offset. Rather than reason about
//    "bottom-PCB-up", measure it: gravity always points down, so whichever
//    sensor axis reads ~-9.8 while the owl is held level tells you which sensor
//    axis is the owl's DOWN. Repeat with the beak pointing at the floor to find
//    the owl's FORWARD. Those two fix the remap completely.
//    IMPORTANT: this sketch deliberately leaves the chip at its DEFAULT remap
//    (P1) so the numbers printed are in the raw sensor frame.
//
// 2. LIVE CALIBRATION MONITOR. Prints the four calibration counters so the
//    figure-8 dance has visible feedback. Calibration is independent of
//    mounting -- do it in your hands, in any orientation.
//      gyro  : sits at 3 after a few seconds held still
//      accel : hold stationary in ~6 different orientations
//      mag   : slow figure-8, rotating through all axes
//      sys   : reaches 3 once the other three are good
//
// Use:  pio run -e imuaxis && pio run -e imuaxis -t upload
// ============================================================================
#if defined(IMUAXIS_ACTIVE)

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_BNO055.h>
#include "config.h"

static Adafruit_BNO055 bno = Adafruit_BNO055(55, ADDR_BNO055, &Wire);

// Name the sensor axis that gravity has settled on, e.g. "-Z".
static const char* dominantAxis(float x, float y, float z) {
    const float ax = fabsf(x), ay = fabsf(y), az = fabsf(z);
    if (ax >= ay && ax >= az) return x < 0 ? "-X" : "+X";
    if (ay >= ax && ay >= az) return y < 0 ? "-Y" : "+Y";
    return z < 0 ? "-Z" : "+Z";
}

void setup() {
    Serial.begin(115200);
    uint32_t t0 = millis();
    while (!Serial && millis() - t0 < 3000) delay(10);
    delay(300);

    Serial.println();
    Serial.println(F("=== BNO055 axis + calibration helper ==="));

    Wire.begin(I2C_SDA, I2C_SCL, I2C_FREQ);

    if (!bno.begin()) {                 // default NDOF
        Serial.println(F("FATAL: BNO055 not found at 0x28"));
        while (true) delay(1000);
    }
    // Leave the axis remap at its power-on default: we are measuring the raw
    // sensor frame in order to CHOOSE a remap.
    Serial.println(F("BNO055 up, default (P1) axis remap, NDOF"));
    Serial.println(F("grav = gravity vector m/s^2 in the SENSOR's own frame"));
    Serial.println(F("----------------------------------------------------"));
}

void loop() {
    imu::Vector<3> g = bno.getVector(Adafruit_BNO055::VECTOR_GRAVITY);
    imu::Vector<3> e = bno.getVector(Adafruit_BNO055::VECTOR_EULER);

    uint8_t sys, gyro, accel, mag;
    bno.getCalibration(&sys, &gyro, &accel, &mag);

    Serial.printf("grav x=%+6.2f y=%+6.2f z=%+6.2f  down=%s | "
                  "head=%6.1f roll=%+6.1f pitch=%+6.1f | cal sys=%u gyro=%u accel=%u mag=%u\n",
                  g.x(), g.y(), g.z(), dominantAxis(g.x(), g.y(), g.z()),
                  e.x(), e.y(), e.z(), sys, gyro, accel, mag);
    delay(250);
}

#endif // IMUAXIS_ACTIVE
