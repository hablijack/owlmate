#pragma once
// ============================================================================
// Bit-gebanntes I2C fuer den BNO055.
//
// WARUM: der I2C-Controller des ESP32-S3 kommt mit dem Clock-Stretching des
// BNO055 nicht zurecht. Auf Hardware gemessen 2026-08-28: der Chip quittiert
// GENAU EINEN Zugriff nach dem Einschalten, danach bricht der Controller die
// Uebertragung ab und laesst den Bus liegen - beide Leitungen bleiben unten.
// Damit sind dann auch GPS (0x10) und Servotreiber (0x40) weg, obwohl beide
// nachweislich in Ordnung sind. Neun Recovery-Takte holen den Bus nicht zurueck;
// nur das Trennen der Stromversorgung hilft.
//
// Derselbe Chip, dieselben Leitungen, dasselbe Netzteil, bit-gebannt gelesen:
// der Bus bleibt oben und der Chip antwortet. Der Unterschied ist allein, dass
// hier nach JEDER steigenden Flanke gewartet wird, bis der Slave SCL wirklich
// freigibt - beliebig lange, statt bis zu einem festen Timeout.
//
// Bewusst NICHT von TwoWire abgeleitet: Adafruit_BNO055 verlangt ein TwoWire*,
// und eine unvollstaendige Ableitung waere eine Falle. Stattdessen liest
// Sensors.cpp die wenigen Register, die die Eule braucht, direkt.
// ============================================================================
#include <Arduino.h>

class SoftI2C {
public:
    SoftI2C(uint8_t sda, uint8_t scl, uint32_t usDelay = 5)
        : _sda(sda), _scl(scl), _us(usDelay) {}

    void begin();

    // Registerzugriffe. Alle geben false zurueck, sobald irgendein Schritt
    // nicht quittiert wird oder der Slave SCL nicht mehr freigibt.
    bool writeReg(uint8_t addr, uint8_t reg, uint8_t value);
    bool readRegs(uint8_t addr, uint8_t reg, uint8_t* buf, uint8_t len);

    // Wie readRegs, meldet aber WELCHER Schritt gescheitert ist. Ohne das
    // bleibt "Lesefehler" eine Sackgasse: Adress-NACK, Register-NACK und ein
    // Slave, der den Takt nicht freigibt, sehen von aussen gleich aus.
    enum Step : uint8_t {
        OK = 0, ERR_START, ERR_ADDR_W, ERR_REG, ERR_RESTART, ERR_ADDR_R, ERR_DATA
    };
    Step readRegsStep(uint8_t addr, uint8_t reg, uint8_t* buf, uint8_t len);
    static const char* stepName(Step s);
    bool ping(uint8_t addr);

    // Ruhepegel beider Leitungen - beide HIGH heisst "Bus frei".
    bool idle();

private:
    uint8_t _sda, _scl;
    uint32_t _us;

    void sdaHigh() { pinMode(_sda, INPUT_PULLUP); }
    void sdaLow()  { pinMode(_sda, OUTPUT); digitalWrite(_sda, LOW); }
    void sclLow()  { pinMode(_scl, OUTPUT); digitalWrite(_scl, LOW); }
    bool sclRelease();          // freigeben UND auf den Slave warten
    void dly() { delayMicroseconds(_us); }

    bool start();
    bool restart();
    void stop();
    bool writeByte(uint8_t v);  // true = ACK
    bool readByte(uint8_t& out, bool ackIt);
};
