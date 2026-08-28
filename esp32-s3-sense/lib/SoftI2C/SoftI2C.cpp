#include "SoftI2C.h"

// Obergrenze fuer das Warten auf SCL. Der BNO055 dehnt hoechstens ~600 us;
// 100 ms sind das Hundertfache und trotzdem kurz genug, dass ein wirklich
// haengender Bus die Hauptschleife nicht dauerhaft blockiert.
// 250 ms. Der BNO055 darf laut Datenblatt einige hundert MIKROsekunden dehnen -
// das ist rund das Tausendfache an Reserve und trotzdem kurz genug, dass ein
// haengender Slave die Hauptschleife nicht festsetzt.
//
// Gemessen 2026-08-28: ein defekter BNO055 gibt SCL auch nach ZWEI SEKUNDEN
// nicht frei. Diese Grenze zu erhoehen hilft dagegen nicht, sie macht die
// Schleife nur langsamer.
static const uint32_t STRETCH_TIMEOUT_US = 250000;

// Laengste beobachtete Taktdehnung dieses Laufs, in Mikrosekunden. Trennt eine
// legitime Dehnung (BNO055: einige hundert us) von einem haengenden Slave, der
// SCL gar nicht mehr freigibt - von aussen sehen beide gleich aus.
uint32_t SoftI2C_maxStretchUs = 0;

void SoftI2C::begin() {
    sdaHigh();
    pinMode(_scl, INPUT_PULLUP);
    delay(2);
}

bool SoftI2C::sclRelease() {
    pinMode(_scl, INPUT_PULLUP);
    if (digitalRead(_scl)) return true;            // Normalfall, keine Dehnung
    const uint32_t t0 = micros();
    for (uint32_t i = 0; i < STRETCH_TIMEOUT_US; i++) {
        if (digitalRead(_scl)) {
            const uint32_t dt = micros() - t0;
            if (dt > SoftI2C_maxStretchUs) SoftI2C_maxStretchUs = dt;
            return true;
        }
        delayMicroseconds(1);
    }
    SoftI2C_maxStretchUs = 0xFFFFFFFF;             // nie freigegeben
    return false;
}

bool SoftI2C::idle() {
    sdaHigh();
    pinMode(_scl, INPUT_PULLUP);
    delayMicroseconds(50);
    return digitalRead(_sda) && digitalRead(_scl);
}

// START: SDA faellt, waehrend SCL hoch ist.
bool SoftI2C::start() {
    sdaHigh(); dly();
    if (!sclRelease()) return false;
    dly();
    sdaLow(); dly();
    sclLow(); dly();
    return true;
}

// Repeated START: erst SDA freigeben (SCL ist unten), dann wie ein START.
bool SoftI2C::restart() {
    sdaHigh(); dly();
    return start();
}

// STOP: SDA steigt, waehrend SCL hoch ist.
void SoftI2C::stop() {
    sdaLow(); dly();
    sclRelease(); dly();
    sdaHigh(); dly();
}

bool SoftI2C::writeByte(uint8_t v) {
    for (int i = 7; i >= 0; i--) {
        // Daten NUR bei tiefem SCL wechseln.
        (v >> i) & 1 ? sdaHigh() : sdaLow();
        dly();
        if (!sclRelease()) return false;
        dly();
        sclLow(); dly();
    }
    sdaHigh(); dly();                   // Leitung fuer das ACK des Slaves frei
    if (!sclRelease()) return false;
    dly();
    const bool ack = (digitalRead(_sda) == LOW);
    sclLow(); dly();
    return ack;
}

bool SoftI2C::readByte(uint8_t& out, bool ackIt) {
    uint8_t v = 0;
    sdaHigh();
    for (int i = 0; i < 8; i++) {
        dly();
        if (!sclRelease()) return false;
        dly();                          // Daten stabil, dann abtasten
        v = (v << 1) | (digitalRead(_sda) ? 1 : 0);
        sclLow(); dly();
    }
    ackIt ? sdaLow() : sdaHigh();       // ACK = Leitung nach unten
    dly();
    if (!sclRelease()) return false;
    dly();
    sclLow(); dly();
    sdaHigh();
    out = v;
    return true;
}

bool SoftI2C::ping(uint8_t addr) {
    if (!start()) return false;
    const bool ack = writeByte(addr << 1);
    stop();
    return ack;
}

bool SoftI2C::writeReg(uint8_t addr, uint8_t reg, uint8_t value) {
    if (!start())                       { stop(); return false; }
    if (!writeByte(addr << 1))          { stop(); return false; }
    if (!writeByte(reg))                { stop(); return false; }
    if (!writeByte(value))              { stop(); return false; }
    stop();
    return true;
}

// Repeated START zwischen Schreib- und Lesephase, wie es die I2C-Spezifikation
// fuer Registerzugriffe vorsieht.
//
// Die Variante mit vollstaendigem STOP dazwischen wurde am 2026-08-28 auf
// Hardware probiert und war SCHLECHTER: mit Repeated START quittiert der BNO055
// die Leseadresse wenigstens bei jedem zweiten Versuch, mit STOP ueberhaupt
// nicht mehr. Nicht noch einmal umstellen ohne Messung.
SoftI2C::Step SoftI2C::readRegsStep(uint8_t addr, uint8_t reg,
                                    uint8_t* buf, uint8_t len) {
    if (!len) return ERR_DATA;
    if (!start())                    { stop(); return ERR_START;   }
    if (!writeByte(addr << 1))       { stop(); return ERR_ADDR_W;  }
    if (!writeByte(reg))             { stop(); return ERR_REG;     }
    if (!restart())                  { stop(); return ERR_RESTART; }
    if (!writeByte((addr << 1) | 1)) { stop(); return ERR_ADDR_R;  }
    for (uint8_t i = 0; i < len; i++) {
        // Letztes Byte NICHT quittieren - so weiss der Slave, dass Schluss ist.
        if (!readByte(buf[i], i + 1 < len)) { stop(); return ERR_DATA; }
    }
    stop();
    return OK;
}

const char* SoftI2C::stepName(Step s) {
    switch (s) {
        case OK:          return "ok";
        case ERR_START:   return "START gescheitert";
        case ERR_ADDR_W:  return "NACK auf Schreibadresse";
        case ERR_REG:     return "NACK auf Registernummer";
        case ERR_RESTART: return "Repeated START gescheitert";
        case ERR_ADDR_R:  return "NACK auf Leseadresse";
        default:          return "Datenphase gescheitert";
    }
}

bool SoftI2C::readRegs(uint8_t addr, uint8_t reg, uint8_t* buf, uint8_t len) {
    return readRegsStep(addr, reg, buf, len) == OK;
}
