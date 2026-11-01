// ============================================================================
// SERVO-KALIBRIERUNG, EIN SERVO AUF EINMAL (env: servocal, -DSERVOCAL_ACTIVE)
//
// Eigenes Image, ohne das normale Verhalten: kein Boot-Zentrieren, keine
// Zustandsmaschine, kein Brain. setup() prueft nur per Adress-ACK, ob das
// PCA9685 da ist, und SCHREIBT NICHTS. Alle Regeln stehen in lib/ServoCal und
// sind in tests/servocal/test.cpp gegen ein Fake-PCA9685 belegt.
//
// Vor dem Flashen: Eule stromlos, NUR den Servo unter Test am PCA9685 stecken.
// Bedienung ueber den seriellen Monitor, Zeilenende LF/CRLF:
// Flashen NICHT per `pio run -t upload`: beide OTA-Slots tragen das gesperrte
// Abbild, und ein Upload nach 0x20000 allein laesst otadata unberuehrt - gebootet
// wird dann womoeglich weiter der alte Slot. Stattdessen tools/flash_servo_locked.py
// (Backup, dann beide Slots schreiben/verifizieren) mit .pio/build/servocal/firmware.bin;
// Rueckweg derselbe Ablauf mit .pio/build/xiao_esp32s3/firmware.bin.
// Danach seriellen Monitor oeffnen (115200 Baud).
// ============================================================================
#if defined(SERVOCAL_ACTIVE)

#include <Arduino.h>
#include <Wire.h>
#include "config.h"
#include "ServoCal.h"

class WireBus : public ServoCalBus {
public:
    bool read(uint8_t reg, uint8_t* buf, uint8_t n) override {
        Wire.beginTransmission(ADDR_PCA9685);
        Wire.write(reg);
        if (Wire.endTransmission() != 0) return false;
        if (Wire.requestFrom(uint8_t(ADDR_PCA9685), n) != n) return false;
        for (uint8_t i = 0; i < n; i++) buf[i] = Wire.read();
        return true;
    }
    bool write(uint8_t reg, const uint8_t* buf, uint8_t n) override {
        Wire.beginTransmission(ADDR_PCA9685);
        Wire.write(reg);
        Wire.write(buf, n);
        return Wire.endTransmission() == 0;
    }
    void delayMs(uint32_t ms) override { delay(ms); }
};

static WireBus bus;
// Nur die fuenf eingebauten Servos duerfen scharf werden.
static ServoCal cal(bus, (1u << CH_LEFT_EAR) | (1u << CH_RIGHT_EAR) | (1u << CH_HEAD) |
                         (1u << CH_LEFT_WING) | (1u << CH_RIGHT_WING),
                    (1u << CH_LEFT_EAR) | (1u << CH_RIGHT_EAR) | (1u << CH_HEAD),
                    (1u << CH_LEFT_EAR) | (1u << CH_RIGHT_EAR) | (1u << CH_HEAD));
// Huelle: offizielle 1000..2000 us fuer alle Kanaele. AUSNAHME OHREN (Kanal 12/13),
// Besitzerentscheidung 2026-10-30: das Horn laesst sich nicht umsetzen (der Kopf ist
// eine Einheit) und der Ohrweg liegt bei ~1950 us und darueber, also ausserhalb.
// Nur dort 500..2500 us. Dito Kopf (Kanal 10), Besitzerentscheidung 2026-10-30: der
// KY66 hat von Hand deutlich mehr Weg als 1000..2000 us, Kabelzug im Kopf beobachtet der Besitzer.
// Der linke Fluegel (Kanal 9) darf nur 1000..1747 us.
static char line[48];
static size_t lineLen = 0;
static bool lineOverflow = false;
static char reply[400];

void setup() {
    Serial.begin(115200);
    delay(1500);
    Wire.begin(I2C_SDA, I2C_SCL, I2C_FREQ);
    Wire.setTimeOut(I2C_TIMEOUT_MS);
    Wire.beginTransmission(ADDR_PCA9685);
    const bool ack = Wire.endTransmission() == 0;
    Serial.println();
    Serial.println(F("=== servocal: Kalibrierwerkzeug, ein Servo auf einmal ==="));
    Serial.println(ack ? F("PCA9685 antwortet (nur ACK, nichts geschrieben).")
                       : F("PCA9685 antwortet NICHT - Verkabelung pruefen."));
    cal.handleLine("HELP", millis(), reply, sizeof reply);
    Serial.println(reply);
}

void loop() {
    while (Serial.available()) {
        const char ch = char(Serial.read());
        if (ch == '\n' || ch == '\r') {
            if (lineOverflow) {
                Serial.println(F("ABGELEHNT: Zeile zu lang, ignoriert"));
            } else if (lineLen) {
                line[lineLen] = 0;
                cal.handleLine(line, millis(), reply, sizeof reply);
                if (reply[0]) Serial.println(reply);
            }
            lineLen = 0;
            lineOverflow = false;
        } else if (lineLen < sizeof line - 1) {
            line[lineLen++] = ch;
        } else {
            lineOverflow = true;
        }
    }
    cal.tick(millis(), reply, sizeof reply);
    if (reply[0]) Serial.println(reply);
    delay(5);
}

#endif  // SERVOCAL_ACTIVE
