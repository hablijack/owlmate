// ============================================================================
// BNO055 ueber bit-gebanntes I2C (env: softimu, -DSOFTIMU_ACTIVE)
//
// Beweisstueck fuer SoftI2C: liest die Chip-ID (muss 0xA0 sein) und danach
// laufend Euler-Winkel und Kalibrierzaehler - alles OHNE den I2C-Controller des
// ESP32, an dem dieser Chip scheitert. Laeuft das hier stabil, kann Sensors.cpp
// denselben Weg nehmen.
// ============================================================================
#if defined(SOFTIMU_ACTIVE)

#include <Arduino.h>
#include "SoftI2C.h"
#include "config.h"

extern uint32_t SoftI2C_maxStretchUs;

static SoftI2C bus(I2C_SDA, I2C_SCL);

#define BNO 0x28
#define REG_CHIP_ID      0x00
#define REG_OPR_MODE     0x3D
#define REG_PWR_MODE     0x3E
#define REG_SYS_TRIGGER  0x3F
#define REG_UNIT_SEL     0x3B
#define REG_CALIB_STAT   0x35
#define REG_EULER_H_LSB  0x1A
#define MODE_CONFIG      0x00
#define MODE_NDOF        0x0C

void setup() {
    Serial.begin(115200);
    delay(2000);
    Serial.println();
    Serial.println(F("=== BNO055 ueber SoftI2C (bit-gebannt) ==="));
    bus.begin();
    Serial.printf("Bus frei: %s\n", bus.idle() ? "ja" : "NEIN");

    Serial.println(F("\n[0] Adressscan ueber SoftI2C"));
    for (uint8_t a = 0x08; a <= 0x77; a++) {
        if (bus.ping(a)) Serial.printf("   0x%02X antwortet\n", a);
        delayMicroseconds(200);
    }
    Serial.println(F("\n[0b] Chip-ID von 0x28 UND 0x29"));
    for (uint8_t a = 0x28; a <= 0x29; a++) {
        for (int t = 0; t < 4; t++) {
            uint8_t id = 0;
            const SoftI2C::Step st = bus.readRegsStep(a, REG_CHIP_ID, &id, 1);
            Serial.printf("   0x%02X Versuch %d: %-28s id=0x%02X%s\n", a, t + 1,
                          SoftI2C::stepName(st), id,
                          (st == SoftI2C::OK && id == 0xA0) ? "  <== BNO055!" : "");
            delay(20);
        }
    }

    Serial.println(F("\n[1] Chip-ID 20x lesen (erwartet 0xA0)"));
    int ok = 0;
    for (int i = 1; i <= 20; i++) {
        uint8_t id = 0;
        const SoftI2C::Step st = bus.readRegsStep(BNO, REG_CHIP_ID, &id, 1);
        if (st == SoftI2C::OK && id == 0xA0) ok++;
        if (i <= 8 || st != SoftI2C::OK || id != 0xA0)
            Serial.printf("   %2d: %-28s id=0x%02X\n", i, SoftI2C::stepName(st), id);
        delay(20);
    }
    Serial.printf("   -> %d von 20 korrekt, Bus %s\n", ok, bus.idle() ? "frei" : "BLOCKIERT");
    if (SoftI2C_maxStretchUs == 0xFFFFFFFF)
        Serial.println(F("   SCL wurde innerhalb von 2 s NIE freigegeben."));
    else
        Serial.printf("   laengste Taktdehnung: %lu us\n",
                      (unsigned long)SoftI2C_maxStretchUs);
    if (ok < 20) { Serial.println(F("   Nicht stabil - hier nicht weitermachen.")); return; }

    Serial.println(F("\n[2] In NDOF schalten"));
    bus.writeReg(BNO, REG_OPR_MODE, MODE_CONFIG);      delay(25);
    bus.writeReg(BNO, REG_SYS_TRIGGER, 0x00);          delay(10);
    bus.writeReg(BNO, REG_PWR_MODE, 0x00);             delay(10);
    bus.writeReg(BNO, REG_UNIT_SEL, 0x00);             delay(10);
    // Einbaulage wie in config.h (Platine kopfueber).
    bus.writeReg(BNO, 0x41, IMU_AXIS_REMAP_CONFIG);    delay(10);
    bus.writeReg(BNO, 0x42, IMU_AXIS_REMAP_SIGN);      delay(10);
    bus.writeReg(BNO, REG_OPR_MODE, MODE_NDOF);        delay(30);
    Serial.println(F("   ok"));
    Serial.println(F("\n[3] Live: Euler-Winkel und Kalibrierung"));
}

void loop() {
    uint8_t cal = 0, raw[6] = {0};
    const bool a = bus.readRegs(BNO, REG_CALIB_STAT, &cal, 1);
    const bool b = bus.readRegs(BNO, REG_EULER_H_LSB, raw, 6);
    if (!a || !b) { Serial.println(F("   Lesefehler")); delay(500); return; }

    // Registerblock ist Heading, Roll, Pitch - 1/16 Grad je Bit.
    const float yaw   = (int16_t)(raw[0] | (raw[1] << 8)) / 16.0f;
    const float roll  = (int16_t)(raw[2] | (raw[3] << 8)) / 16.0f;
    const float pitch = (int16_t)(raw[4] | (raw[5] << 8)) / 16.0f;
    Serial.printf("   yaw %7.2f  roll %7.2f  pitch %7.2f   cal sys%d gyro%d acc%d mag%d   Bus %s\n",
                  yaw, roll, pitch,
                  (cal >> 6) & 3, (cal >> 4) & 3, (cal >> 2) & 3, cal & 3,
                  bus.idle() ? "frei" : "BLOCKIERT");
    delay(500);
}

#endif
