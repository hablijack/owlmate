// ============================================================================
// LSM303AGR ueber bit-gebanntes I2C (env: softimu, -DSOFTIMU_ACTIVE)
//
// WOZU DAS JETZT NOCH GUT IST - denn der Grund, aus dem es gebaut wurde, ist
// weg. Diese Umgebung entstand fuer den BNO055: der dehnt den Takt so lange,
// dass der I2C-Controller des ESP32 mit ihm nicht zuverlaessig sprechen kann,
// und sie war der Beweis, dass es bit-gebannt trotzdem geht. DER LSM303AGR
// DEHNT DEN TAKT NICHT und laeuft ueber den normalen Treiber bei 400 kHz
// einwandfrei (gemessen 2026-09-01 mit -e i2ctest).
//
// Geblieben ist der Wert, der nichts mit dem Chip zu tun hatte: dieses Programm
// umgeht den ESP-IDF-Treiber KOMPLETT und sagt bei einem Fehlschlag, WELCHER
// PROTOKOLLSCHRITT gescheitert ist (Adresse, ACK, Registerbyte, Datenphase).
// Damit trennt es "Bus/Verkabelung kaputt" von "Treiber falsch benutzt" - und
// das bleibt die Frage, die am Bench am meisten Zeit kostet.
//
// Reihenfolge:
//   0. Adressscan, danach die WHO_AM_I beider Haelften einzeln und mehrfach.
//      Beschleunigung 0x19: 0x0F muss 0x33 sein. Magnetometer 0x1E: 0x4F muss
//      0x40 sein (LIS2MDL). Ein Magnetometer, das antwortet aber nicht 0x40
//      liefert, ist das falsche Board - das blaue LSM303DLHC, siehe config.h.
//   1. Beide Sensoren von Hand konfigurieren und live auslesen, mit Rohwerten
//      UND Betrag: |a| muss ~9,8 m/s^2 sein, |m| ~25..65 uT.
// ============================================================================
#if defined(SOFTIMU_ACTIVE)

#include <Arduino.h>
#include "SoftI2C.h"
#include "config.h"

extern uint32_t SoftI2C_maxStretchUs;

static SoftI2C bus(I2C_SDA, I2C_SCL);

// --- Beschleunigungssensor (LIS2DH-artig) -----------------------------------
#define ACC ADDR_LSM303_ACCEL
#define ACC_WHO_AM_I   0x0F   // -> 0x33
#define ACC_CTRL_REG1  0x20
#define ACC_CTRL_REG4  0x23
#define ACC_OUT_X_L    0x28
// Mehrbyte-Lesen braucht beim LIS2DH das MSB der Subadresse. Ohne dieses Bit
// liest man dasselbe Register sechsmal - und bekommt einen Vektor, der
// plausibel aussieht und falsch ist.
#define ACC_AUTOINC    0x80

// --- Magnetometer (LIS2MDL) -------------------------------------------------
#define MAG ADDR_LSM303_MAG
#define MAG_WHO_AM_I   0x4F   // -> 0x40
#define MAG_CFG_REG_A  0x60
#define MAG_CFG_REG_C  0x62
#define MAG_OUTX_L     0x68

static void whoAmI(uint8_t addr, uint8_t reg, uint8_t want, const char* name) {
    Serial.printf("\n   %s @0x%02X, Register 0x%02X, erwartet 0x%02X\n",
                  name, addr, reg, want);
    for (int t = 0; t < 4; t++) {
        uint8_t id = 0;
        const SoftI2C::Step st = bus.readRegsStep(addr, reg, &id, 1);
        Serial.printf("      Versuch %d: %-28s id=0x%02X%s\n", t + 1,
                      SoftI2C::stepName(st), id,
                      (st == SoftI2C::OK && id == want) ? "  <== ok" : "");
        delay(20);
    }
}

void setup() {
    Serial.begin(115200);
    delay(2000);
    Serial.println();
    Serial.println(F("=== LSM303AGR ueber SoftI2C (bit-gebannt) ==="));
    bus.begin();
    Serial.printf("Bus frei: %s\n", bus.idle() ? "ja" : "NEIN");

    Serial.println(F("\n[0] Adressscan ueber SoftI2C"));
    for (uint8_t a = 0x08; a <= 0x77; a++) {
        if (bus.ping(a)) Serial.printf("   0x%02X antwortet\n", a);
        delayMicroseconds(200);
    }

    Serial.println(F("\n[0b] Identitaet beider Haelften"));
    whoAmI(ACC, ACC_WHO_AM_I, 0x33, "Beschleunigung");
    whoAmI(MAG, MAG_WHO_AM_I, 0x40, "Magnetometer  ");

    if (SoftI2C_maxStretchUs == 0xFFFFFFFF)
        Serial.println(F("\n   SCL wurde innerhalb von 2 s NIE freigegeben."));
    else
        Serial.printf("\n   laengste Taktdehnung: %lu us  (beim LSM303AGR"
                      " erwartet: ~0)\n",
                      (unsigned long)SoftI2C_maxStretchUs);

    Serial.println(F("\n[1] Konfigurieren"));
    // 100 Hz, XYZ an; hochaufloesend (12 Bit), +/-2 g, BDU.
    bus.writeReg(ACC, ACC_CTRL_REG1, 0x57); delay(10);
    bus.writeReg(ACC, ACC_CTRL_REG4, 0x88); delay(10);
    // Dauermodus, 100 Hz, Temperaturkompensation; BDU.
    bus.writeReg(MAG, MAG_CFG_REG_A, 0x8C); delay(10);
    bus.writeReg(MAG, MAG_CFG_REG_C, 0x10); delay(10);
    Serial.println(F("   ok"));
    Serial.println(F("\n[2] Live: Rohwerte und Betraege"));
}

void loop() {
    uint8_t ra[6] = {0}, rm[6] = {0};
    const bool okA = bus.readRegs(ACC, ACC_OUT_X_L | ACC_AUTOINC, ra, 6);
    const bool okM = bus.readRegs(MAG, MAG_OUTX_L, rm, 6);
    if (!okA || !okM) {
        Serial.printf("   Lesefehler (accel %s, mag %s)\n",
                      okA ? "ok" : "FEHLER", okM ? "ok" : "FEHLER");
        delay(500);
        return;
    }

    // Hochaufloesend: 12 Bit linksbuendig, 1 mg je Bit bei +/-2 g.
    const int16_t ax = (int16_t)(ra[0] | (ra[1] << 8)) >> 4;
    const int16_t ay = (int16_t)(ra[2] | (ra[3] << 8)) >> 4;
    const int16_t az = (int16_t)(ra[4] | (ra[5] << 8)) >> 4;
    const float axf = ax * 0.001f * 9.80665f;
    const float ayf = ay * 0.001f * 9.80665f;
    const float azf = az * 0.001f * 9.80665f;

    // LIS2MDL: 16 Bit, 1,5 mgauss je Bit; 1 mgauss = 0,1 uT.
    const int16_t mx = (int16_t)(rm[0] | (rm[1] << 8));
    const int16_t my = (int16_t)(rm[2] | (rm[3] << 8));
    const int16_t mz = (int16_t)(rm[4] | (rm[5] << 8));
    const float mxf = mx * 1.5f * 0.1f;
    const float myf = my * 1.5f * 0.1f;
    const float mzf = mz * 1.5f * 0.1f;

    Serial.printf("   a %6.2f %6.2f %6.2f |a|=%5.2f m/s2   "
                  "m %6.1f %6.1f %6.1f |m|=%5.1f uT   Bus %s\n",
                  axf, ayf, azf, sqrtf(axf * axf + ayf * ayf + azf * azf),
                  mxf, myf, mzf, sqrtf(mxf * mxf + myf * myf + mzf * mzf),
                  bus.idle() ? "frei" : "BLOCKIERT");
    delay(500);
}

#endif
