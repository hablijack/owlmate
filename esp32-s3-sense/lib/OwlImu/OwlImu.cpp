#include "OwlImu.h"

#include <Adafruit_LIS2MDL.h>
#include <Adafruit_LSM303_Accel.h>
#include <math.h>

// Die beiden Haelften des LSM303AGR sind zwei voellig eigenstaendige I2C-Geraete
// mit getrennten Adressen und getrennten Treibern - sie teilen nur das Stueck
// Platine. Bewusst `static` hier drin statt im Header: nur diese Datei braucht
// sie (die Regel aus AGENTS.md, die owl.h davor bewahrt, wieder zur Gott-Datei
// zu werden).
static Adafruit_LSM303_Accel_Unified accelDev(30301);
static Adafruit_LIS2MDL magDev(30302);

// --- kleine Vektorhelfer ----------------------------------------------------
static inline float vdot(const float a[3], const float b[3]) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
static inline void vcross(const float a[3], const float b[3], float o[3]) {
    o[0] = a[1] * b[2] - a[2] * b[1];
    o[1] = a[2] * b[0] - a[0] * b[2];
    o[2] = a[0] * b[1] - a[1] * b[0];
}
static inline float vnorm(const float a[3]) { return sqrtf(vdot(a, a)); }

// --- Achsenumbau ------------------------------------------------------------
// Der BNO055 konnte das in HARDWARE (AXIS_MAP-Register, Placement P7). Der
// LSM303AGR kann es nicht, also machen wir es hier.
static inline void remapAxes(const int8_t sel[3], const float in[3], float out[3]) {
    for (int i = 0; i < 3; i++) {
        const int8_t s = sel[i];
        const int idx = (s < 0 ? -s : s) - 1;
        out[i] = (s < 0) ? -in[idx] : in[idx];
    }
}

static bool readReg8(TwoWire* wire, uint8_t addr, uint8_t reg, uint8_t& val) {
    wire->beginTransmission(addr);
    wire->write(reg);
    if (wire->endTransmission(false) != 0) return false;
    if (wire->requestFrom((int)addr, 1) != 1) return false;
    val = wire->read();
    return true;
}

bool OwlImu::begin(const OwlImuConfig& cfg, TwoWire* wire) {
    _cfg = cfg;
    _wire = wire;
    _ready = false;
    _failed = false;

    // WHO_AM_I ZUERST, von Hand, BEIDE Haelften einzeln. Die Adafruit-begin()
    // pruefen das auch, melden aber nur ein gemeinsames false zurueck - und
    // "irgendwas mit dem IMU" ist am Bench die unbrauchbarste aller Meldungen.
    // Hier steht hinterher, WELCHE Haelfte fehlt: das unterscheidet ein
    // abgezogenes Kabel von einem falschen Board.
    uint8_t idA = 0, idM = 0;
    const bool gotA = readReg8(wire, _cfg.accelAddr, 0x0F, idA);
    const bool gotM = readReg8(wire, _cfg.magAddr, 0x4F, idM);

    if (!gotA || idA != 0x33) {
        Serial.print(F("IMU: Beschleunigungssensor @0x19 "));
        if (!gotA) Serial.println(F("antwortet nicht"));
        else { Serial.print(F("meldet WHO_AM_I 0x")); Serial.print(idA, HEX);
               Serial.println(F(", erwartet 0x33")); }
        return false;
    }
    if (!gotM || idM != 0x40) {
        Serial.print(F("IMU: Magnetometer @0x1E "));
        if (!gotM) {
            Serial.println(F("antwortet nicht"));
        } else {
            Serial.print(F("meldet WHO_AM_I 0x")); Serial.print(idM, HEX);
            Serial.println(F(", erwartet 0x40 (LIS2MDL)"));
            // Das ist der wahrscheinlichste Einzelfehler beim Boardtausch,
            // deshalb hier ausgeschrieben statt in der Doku versteckt.
            Serial.println(F("IMU: Das sieht nach dem BLAUEN LSM303DLHC aus, nicht"
                             " nach dem SCHWARZEN LSM303AGR - andere Registerbelegung,"
                             " der Kurs waere still falsch."));
        }
        return false;
    }

    if (!accelDev.begin(_cfg.accelAddr, wire)) {
        Serial.println(F("IMU: Beschleunigungssensor-Init fehlgeschlagen"));
        return false;
    }
    if (!magDev.begin(_cfg.magAddr, wire)) {
        Serial.println(F("IMU: Magnetometer-Init fehlgeschlagen"));
        return false;
    }

    // +/-2 g und 12 Bit. Diese Kiste misst keine Fahrdynamik, sie misst die
    // Lotrichtung - dafuer ist der kleinste Messbereich der genaueste, und die
    // Aufloesung geht direkt in die Kippkompensation des Kompasses ein.
    accelDev.setRange(LSM303_RANGE_2G);
    accelDev.setMode(LSM303_MODE_HIGH_RESOLUTION);
    // magDev.reset() in begin() stellt schon Dauermodus + Temperaturkompensation
    // + BDU + 100 Hz ein; die Rate hier nur explizit, damit sie in dieser Datei
    // nachlesbar ist und nicht in der Bibliothek.
    magDev.setDataRate(LIS2MDL_RATE_100_HZ);

    _ready = true;
    _first = true;
    _lastSample = millis() - _cfg.sampleIntervalMs;
    return true;
}

bool OwlImu::readRaw(float a[3], float m[3]) {
    sensors_event_t ae, me;
    if (!accelDev.getEvent(&ae)) return false;
    if (!magDev.getEvent(&me)) return false;

    const float ar[3] = {ae.acceleration.x, ae.acceleration.y, ae.acceleration.z};
    const float mr[3] = {me.magnetic.x, me.magnetic.y, me.magnetic.z};

    // PLAUSIBILITAET IST HIER DIE EINZIGE FEHLERERKENNUNG. Ein NACK liefert bei
    // Adafruit-BusIO einen mit Nullen gefuellten Puffer, und getEvent() gibt
    // trotzdem true zurueck - der Rueckgabewert sagt also nichts. Ein ruhender
    // Beschleunigungssensor misst aber IMMER die Erdbeschleunigung; genau
    // (0,0,0) kann er physikalisch nicht liefern. Ohne diese Pruefung wuerde
    // ein abgezogenes Kabel als "Eule liegt im freien Fall" durchgehen und
    // einen Kurs aus Nullen erzeugen.
    const float an = vnorm(ar);
    if (an < 2.0f || an > 30.0f) return false;
    const float mn = vnorm(mr);
    if (mn < 5.0f || mn > 500.0f) return false;

    const int8_t sel[3] = {_cfg.remapX, _cfg.remapY, _cfg.remapZ};
    remapAxes(sel, ar, a);
    remapAxes(sel, mr, m);
    return true;
}

bool OwlImu::update() {
    if (!_ready) return false;
    const uint32_t now = millis();

    // Ausgefallen: NICHT weiter im Schleifentakt anklopfen. Jeder Fehlzugriff
    // kostet den vollen I2C_TIMEOUT_MS (250 ms). Bei 50 Hz Abtastung waere das
    // eine stehende Hauptschleife - genau der Fehler, den getGps() schon
    // einmal hatte (siehe Sensors.cpp und SPEC-005).
    if (_failed) {
        if ((int32_t)(now - _backoffUntil) < 0) return false;
        uint8_t v = 0;
        if (readReg8(_wire, _cfg.accelAddr, 0x0F, v) && v == 0x33) {
            _failed = false;
            _failCount = 0;
            _first = true;
            Serial.println(F("IMU: LSM303AGR ist zurueck"));
        } else {
            _backoffUntil = now + _cfg.failBackoffMs;
            return false;
        }
    }

    if ((uint32_t)(now - _lastSample) < _cfg.sampleIntervalMs) return false;
    _lastSample = now;

    float a[3], m[3];
    if (!readRaw(a, m)) {
        if (++_failCount >= _cfg.maxReadFails) {
            _failed = true;
            _backoffUntil = now + _cfg.failBackoffMs;
            Serial.println(F("IMU: LSM303AGR liefert keine plausiblen Werte -"
                             " Abtastung pausiert"));
        }
        return false;
    }
    _failCount = 0;

    // Tiefpass. Ohne Gyroskop ist die Beschleunigung die EINZIGE Lagequelle,
    // und waehrend einer Servobewegung wackelt sie deutlich.
    if (_first) {
        for (int i = 0; i < 3; i++) { _fa[i] = a[i]; _fm[i] = m[i]; }
        _first = false;
    } else {
        for (int i = 0; i < 3; i++) {
            _fa[i] += _cfg.filterAlpha * (a[i] - _fa[i]);
            _fm[i] += _cfg.filterAlpha * (m[i] - _fm[i]);
        }
    }

    // Hartmagnet-Min/Max mitschreiben. Auf dem GEFILTERTEN Vektor, damit ein
    // einzelner Ausreisser die Spanne nicht dauerhaft aufblaeht - Min/Max
    // vergessen nie, ein Tiefpass schon.
    if (!_magSeen) {
        for (int i = 0; i < 3; i++) { _magMin[i] = _magMax[i] = _fm[i]; }
        _magSeen = true;
    } else {
        for (int i = 0; i < 3; i++) {
            if (_fm[i] < _magMin[i]) _magMin[i] = _fm[i];
            if (_fm[i] > _magMax[i]) _magMax[i] = _fm[i];
        }
    }

    fuse();
    return true;
}

// Die gerade gueltigen Offsets. Eine frische Drehung in DIESEM Lauf schlaegt
// die aus dem Flash: wer neu kalibriert, will das Ergebnis auch sofort sehen,
// und alte Offsets stillschweigend weiterzubenutzen ist der teurere Fehler.
uint8_t OwlImu::liveAxes(float off[3]) const {
    uint8_t n = 0;
    for (int i = 0; i < 3; i++) {
        const float span = _magMax[i] - _magMin[i];
        off[i] = 0.5f * (_magMax[i] + _magMin[i]);
        if (span >= _cfg.magMinSpanUt) n++;
    }
    return n;
}

void OwlImu::fuse() {
    float liveOff[3];
    const uint8_t axes = _magSeen ? liveAxes(liveOff) : 0;

    const float* off = _hard;
    if (axes >= 2) off = liveOff;
    const bool haveOffsets = (axes >= 2) || _haveOffsets;

    // Lotrichtung. Ein ruhender Beschleunigungssensor zeigt nach OBEN.
    const float amag = vnorm(_fa);
    float up[3] = {_fa[0] / amag, _fa[1] / amag, _fa[2] / amag};

    _sample.pitch = atan2f(_fa[0], sqrtf(_fa[1] * _fa[1] + _fa[2] * _fa[2])) * RAD_TO_DEG;
    _sample.roll = atan2f(_fa[1], _fa[2]) * RAD_TO_DEG;

    float m[3] = {_fm[0] - off[0], _fm[1] - off[1], _fm[2] - off[2]};

    // KIPPKOMPENSIERTER KOMPASS, als Vektorrechnung statt als die uebliche
    // Trigonometrieformel: Magnetfeld in die Horizontalebene projizieren,
    // Schnabelrichtung in dieselbe Ebene projizieren, und den Winkel dazwischen
    // im Uhrzeigersinn von oben gesehen messen.
    //
    // Dasselbe Ergebnis, aber NICHTS DARIN HAENGT AN EINER
    // EULERWINKEL-REIHENFOLGE - und genau da entstehen die Vorzeichenfehler der
    // klassischen Formel. Dieses Projekt hat einen kompletten Satz vertauschter
    // Lageachsen schon einmal bezahlt (SPEC-006 Falsified: ein waagerechter Vogel
    // meldete roll 359,9 und die Navigation steuerte den Kopf mit einem
    // Nickwinkel).
    //
    // Die Projektion ist nicht Kosmetik: das Erdfeld zeigt in Deutschland etwa
    // 66 Grad NACH UNTEN. Ohne Herausrechnen des Vertikalanteils dreht sich der
    // Kurs mit jeder Kippung der Eule mit.
    const float mdotup = vdot(m, up);
    float mh[3] = {m[0] - mdotup * up[0], m[1] - mdotup * up[1], m[2] - mdotup * up[2]};
    // Schnabel (+X) in die Horizontalebene: X - (X.up)*up
    float fwd[3] = {1.0f - up[0] * up[0], -up[0] * up[1], -up[0] * up[2]};

    const float nmh = vnorm(mh);
    const float nfwd = vnorm(fwd);

    // nfwd faellt gegen 0, wenn der Schnabel senkrecht steht - dann hat "wohin
    // schaut die Eule" in der Horizontalebene keine Antwort mehr. 0.15
    // entspricht etwa 81 Grad Nickwinkel, viel mehr als die Eule je einnimmt.
    const bool geometryOk = (nmh > 5.0f) && (nfwd > 0.15f);
    _sample.headingOk = haveOffsets && geometryOk;

    if (_sample.headingOk) {
        float c[3];
        vcross(fwd, mh, c);
        float h = atan2f(vdot(c, up), vdot(fwd, mh)) * RAD_TO_DEG;
        h += _cfg.headingOffsetDeg;
        while (h < 0.0f) h += 360.0f;
        while (h >= 360.0f) h -= 360.0f;
        _lastYaw = h;
        _haveYaw = true;
    }
    // Letzten guten Kurs HALTEN statt Muell zu senden. Ein kurzer Ausfall der
    // Geometrie (jemand hebt die Eule an) darf keinen falschen Zahlenwert in
    // die Navigation schieben; headingOk sagt daneben die Wahrheit.
    _sample.yaw = _haveYaw ? _lastYaw : 0.0f;

    _sample.haveOffsets = haveOffsets;
    _sample.magAxes = axes;
    _sample.calRestored = _restored;
    _sample.ax = _fa[0]; _sample.ay = _fa[1]; _sample.az = _fa[2];
    _sample.mx = m[0]; _sample.my = m[1]; _sample.mz = m[2];
}

void OwlImu::setHardIron(float x, float y, float z) {
    _hard[0] = x; _hard[1] = y; _hard[2] = z;
    _haveOffsets = true;
    _restored = true;
    _sample.haveOffsets = true;
    _sample.calRestored = true;
}

bool OwlImu::getHardIron(float& x, float& y, float& z) const {
    if (!_magSeen) return false;
    float off[3];
    if (liveAxes(off) < 2) return false;
    x = off[0]; y = off[1]; z = off[2];
    return true;
}

void OwlImu::magSpan(float& x, float& y, float& z) const {
    x = _magSeen ? (_magMax[0] - _magMin[0]) : 0.0f;
    y = _magSeen ? (_magMax[1] - _magMin[1]) : 0.0f;
    z = _magSeen ? (_magMax[2] - _magMin[2]) : 0.0f;
}

void OwlImu::resetMagCal() {
    _magSeen = false;
    for (int i = 0; i < 3; i++) { _magMin[i] = _magMax[i] = 0.0f; }
}
