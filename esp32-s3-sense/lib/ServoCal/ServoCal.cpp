#include "ServoCal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

static const uint8_t REG_MODE1 = 0x00;
static const uint8_t REG_LED0_ON_L = 0x06;
static const uint8_t REG_PRESCALE = 0xFE;
static const uint8_t MODE1_SLEEP = 0x10;
static const uint8_t MODE1_AI = 0x20;
static const uint8_t MODE1_RESTART = 0x80;
static const uint16_t FULL_BIT = 0x1000;

ServoCal::ServoCal(ServoCalBus& bus, uint16_t allowedMask, uint16_t extLowMask,
                   uint16_t extHighMask)
    : _bus(bus), _allowed(allowedMask), _extLow(extLowMask & allowedMask),
      _extHigh(extHighMask & allowedMask), _armed(false), _channel(0),
      _limLo(ENV_LO_TICKS), _limHi(ENV_HI_TICKS), _hasWritten(false),
      _lastWriteMs(0), _lastCmdMs(0), _pending(false), _pendingTicks(0),
      _pendingDeadline(0) {}

uint16_t ServoCal::envLo(uint8_t ch) const {
    return (_extLow & (1u << ch)) ? EXT_LO_TICKS : ENV_LO_TICKS;
}

uint16_t ServoCal::envHi(uint8_t ch) const {
    return (_extHigh & (1u << ch)) ? EXT_HI_TICKS : ENV_HI_TICKS;
}

uint16_t ServoCal::usToTicks(long us) {
    return uint16_t((us * 100 + TICK_US_X100 / 2) / TICK_US_X100);
}

long ServoCal::ticksToUs(uint16_t ticks) {
    return (long(ticks) * TICK_US_X100 + 50) / 100;
}

bool ServoCal::readChan(uint8_t ch, Chan& c) {
    // Vier EINZELNE Lesezugriffe: ohne Auto-Increment in MODE1 (Power-on-Zustand!)
    // liefert ein 4-Byte-Read viermal dasselbe Register. Auf Hardware gesehen:
    // alle 16 Kanaele lasen sich als "unklar", bevor ARM AI setzen konnte.
    uint8_t b[4];
    for (uint8_t i = 0; i < 4; i++)
        if (!_bus.read(REG_LED0_ON_L + 4 * ch + i, &b[i], 1)) return false;
    const uint16_t on = b[0] | (uint16_t(b[1]) << 8);
    const uint16_t off = b[2] | (uint16_t(b[3]) << 8);
    c.ticks = 0;
    // Wie im Datenblatt: "full off" im OFF-Register hat Vorrang vor full on.
    if (off & FULL_BIT) c.kind = FULL_OFF;
    else if (on & FULL_BIT) c.kind = FULL_ON;
    else if (on == 0 && off > 0 && off < 4096) { c.kind = PULSE; c.ticks = off; }
    else c.kind = UNKNOWN;
    return true;
}

// Ein Burst mit vier Bytes, so wie die Herstellerbibliothek. Ob der Chip die
// vier Bytes erst am Ende uebernimmt, ist hier NICHT geprueft (SPEC-016 Open).
bool ServoCal::writePulse(uint8_t ch, uint16_t ticks) {
    const uint8_t b[4] = {0, 0, uint8_t(ticks & 0xFF), uint8_t(ticks >> 8)};
    return _bus.write(REG_LED0_ON_L + 4 * ch, b, 4);
}

bool ServoCal::writeFullOff(uint8_t ch) {
    const uint8_t b[4] = {0, 0, 0, uint8_t(FULL_BIT >> 8)};
    return _bus.write(REG_LED0_ON_L + 4 * ch, b, 4);
}

// Nach einem Busfehler ist die Position unbekannt: entwaffnen, NICHT erneut
// schreiben. ARM liest die Register neu und entscheidet dann.
void ServoCal::fail(char* out, size_t cap, const char* what) {
    _armed = false;
    _pending = false;
    snprintf(out, cap, "FEHLER %s - entwaffnet, nichts wiederholt; neu mit ARM", what);
}

void ServoCal::cmdHelp(char* out, size_t cap) {
    snprintf(out, cap,
        "Befehle: STATUS | ARM <kanal> | FIRST <us> + YES | STEP <+-us, max %u> | "
        "LIMIT <lo> <hi> | STOP. Huelle %ld..%ld us @ ~50 Hz. Nur ein Servo "
        "angesteckt!", MAX_STEP_US, ticksToUs(ENV_LO_TICKS), ticksToUs(ENV_HI_TICKS));
}

void ServoCal::cmdStatus(char* out, size_t cap) {
    uint8_t mode1 = 0, pre = 0;
    if (!_bus.read(REG_MODE1, &mode1, 1) || !_bus.read(REG_PRESCALE, &pre, 1)) {
        snprintf(out, cap, "FEHLER i2c (STATUS liest nur)");
        return;
    }
    int n = snprintf(out, cap, "STATUS scharf=%d kanal=%d grenzen=%ld..%ld us mode1=0x%02X "
                     "prescale=%u |",
                     _armed, _armed ? _channel : -1, ticksToUs(_limLo), ticksToUs(_limHi),
                     mode1, pre);
    for (uint8_t ch = 0; ch < 16 && n > 0 && size_t(n) < cap; ch++) {
        Chan c;
        if (!readChan(ch, c)) { snprintf(out + n, cap - n, " k%u=i2c?", ch); return; }
        if (c.kind == FULL_OFF) continue;   // aus = Normalzustand, nicht auflisten
        if (c.kind == PULSE)
            n += snprintf(out + n, cap - n, " k%u=%ldus", ch, ticksToUs(c.ticks));
        else
            n += snprintf(out + n, cap - n, " k%u=%s", ch, c.kind == FULL_ON ? "AN" : "?");
    }
}

void ServoCal::cmdArm(const char* arg, char* out, size_t cap) {
    char* end;
    const long ch = arg ? strtol(arg, &end, 10) : -1;
    if (!arg || *end || ch < 0 || ch > 15 || !(_allowed & (1u << ch))) {
        snprintf(out, cap, "ABGELEHNT ARM: Kanal nicht freigegeben (erlaubt: Maske 0x%04X)", _allowed);
        return;
    }
    if (_armed) {
        snprintf(out, cap, "ABGELEHNT ARM: Kanal %d ist scharf, erst STOP", _channel);
        return;
    }
    uint8_t mode1 = 0, pre = 0;
    if (!_bus.read(REG_MODE1, &mode1, 1) || !_bus.read(REG_PRESCALE, &pre, 1)) {
        snprintf(out, cap, "FEHLER i2c - kein Schreibzugriff erfolgt");
        return;
    }
    Chan mine = {UNKNOWN, 0};
    for (uint8_t c = 0; c < 16; c++) {
        Chan s;
        if (!readChan(c, s)) { snprintf(out, cap, "FEHLER i2c - kein Schreibzugriff erfolgt"); return; }
        if (c == ch) { mine = s; continue; }
        if (s.kind != FULL_OFF) {
            snprintf(out, cap, "ABGELEHNT ARM: Kanal %u gibt bereits ein Signal aus. Nur den "
                     "Servo unter Test anstecken und Eule stromlos neu starten", c);
            return;
        }
    }
    const bool validPulse = mine.kind == PULSE && mine.ticks >= envLo(uint8_t(ch)) && mine.ticks <= envHi(uint8_t(ch));
    if (mine.kind != FULL_OFF && !validPulse) {
        snprintf(out, cap, "ABGELEHNT ARM: Kanal %ld hat einen Wert ausserhalb der Huelle/unklar", ch);
        return;
    }
    const bool needsConfig = pre != PRESCALE || (mode1 & MODE1_SLEEP);
    if (needsConfig && mine.kind != FULL_OFF) {
        snprintf(out, cap, "ABGELEHNT ARM: Frequenz/Sleep muesste geaendert werden, Kanal %ld ist aber aktiv", ch);
        return;
    }
    if (needsConfig) {
        // Alle 16 Kanaele sind hier nachweislich aus: nichts kann sich bewegen.
        const uint8_t base = mode1 & uint8_t(~MODE1_RESTART);
        const uint8_t sleep = base | MODE1_SLEEP;
        const uint8_t awake = uint8_t((base & ~MODE1_SLEEP) | MODE1_AI);
        const uint8_t pres = PRESCALE;
        const uint8_t restart = awake | MODE1_RESTART;
        bool ok = _bus.write(REG_MODE1, &sleep, 1) && _bus.write(REG_PRESCALE, &pres, 1)
               && _bus.write(REG_MODE1, &awake, 1);
        if (ok) _bus.delayMs(5);
        ok = ok && _bus.write(REG_MODE1, &restart, 1);
        uint8_t p2 = 0, m2 = 0;
        if (!ok || !_bus.read(REG_PRESCALE, &p2, 1) || !_bus.read(REG_MODE1, &m2, 1)
            || p2 != PRESCALE || (m2 & MODE1_SLEEP)) {
            fail(out, cap, "Frequenz-Setup");
            return;
        }
    } else if (!(mode1 & MODE1_AI)) {
        const uint8_t ai = uint8_t((mode1 & ~MODE1_RESTART) | MODE1_AI);
        if (!_bus.write(REG_MODE1, &ai, 1)) { fail(out, cap, "MODE1"); return; }
    }
    _armed = true;
    _channel = uint8_t(ch);
    _limLo = envLo(uint8_t(ch));
    _limHi = envHi(uint8_t(ch));
    _hasWritten = false;
    _pending = false;
    if (mine.kind == PULSE)
        snprintf(out, cap, "OK ARM kanal=%ld uebernimmt vorhandenen Puls %ld us (nur gelesen)",
                 ch, ticksToUs(mine.ticks));
    else
        snprintf(out, cap, "OK ARM kanal=%ld inaktiv, kein Puls. Erster Puls nur per FIRST <us> + YES", ch);
}

void ServoCal::cmdFirst(const char* arg, uint32_t now, char* out, size_t cap) {
    char* end;
    const long us = arg ? strtol(arg, &end, 10) : 0;
    if (!_armed) { snprintf(out, cap, "ABGELEHNT FIRST: nichts scharf"); return; }
    if (!arg || *end) { snprintf(out, cap, "ABGELEHNT FIRST: Zahl in us erwartet"); return; }
    const uint16_t ticks = (us > 0 && us < 10000) ? usToTicks(us) : 0;
    if (ticks < _limLo || ticks > _limHi) {
        snprintf(out, cap, "ABGELEHNT FIRST: %ld us ausserhalb %ld..%ld us", us,
                 ticksToUs(_limLo), ticksToUs(_limHi));
        return;
    }
    Chan c;
    if (!readChan(_channel, c)) { fail(out, cap, "i2c"); return; }
    if (c.kind != FULL_OFF) {
        snprintf(out, cap, "ABGELEHNT FIRST: Kanal %u ist bereits aktiv, STEP benutzen", _channel);
        return;
    }
    _pending = true;
    _pendingTicks = ticks;
    _pendingDeadline = now + CONFIRM_TIMEOUT_MS;
    snprintf(out, cap, "BESTAETIGEN: Kanal %u erster Puls %ld us (%u Ticks). YES innerhalb %lu s "
             "- jeder andere Befehl bricht ab", _channel, ticksToUs(ticks), ticks,
             (unsigned long)(CONFIRM_TIMEOUT_MS / 1000));
}

void ServoCal::cmdYes(uint32_t now, char* out, size_t cap) {
    if (!_armed || !_pending || int32_t(now - _pendingDeadline) > 0) {
        _pending = false;
        snprintf(out, cap, "ABGELEHNT YES: nichts zu bestaetigen (oder abgelaufen)");
        return;
    }
    _pending = false;
    Chan c;
    if (!readChan(_channel, c)) { fail(out, cap, "i2c"); return; }
    if (c.kind != FULL_OFF) { snprintf(out, cap, "ABGELEHNT YES: Kanal inzwischen aktiv"); return; }
    if (!writePulse(_channel, _pendingTicks)) { fail(out, cap, "i2c beim Schreiben"); return; }
    _hasWritten = true;
    _lastWriteMs = now;
    snprintf(out, cap, "OK FIRST kanal=%u puls=%ld us", _channel, ticksToUs(_pendingTicks));
}

void ServoCal::cmdStep(const char* arg, uint32_t now, char* out, size_t cap) {
    char* end;
    const long d = arg ? strtol(arg, &end, 10) : 0;
    if (!_armed) { snprintf(out, cap, "ABGELEHNT STEP: nichts scharf"); return; }
    if (!arg || *end || d == 0 || d > MAX_STEP_US || d < -long(MAX_STEP_US)) {
        snprintf(out, cap, "ABGELEHNT STEP: ganze Zahl 1..%u (mit Vorzeichen) erwartet", MAX_STEP_US);
        return;
    }
    if (_hasWritten && now - _lastWriteMs < MIN_INTERVAL_MS) {
        snprintf(out, cap, "ABGELEHNT STEP: zu schnell, mindestens %lu ms Abstand",
                 (unsigned long)MIN_INTERVAL_MS);
        return;
    }
    Chan c;
    if (!readChan(_channel, c)) { fail(out, cap, "i2c"); return; }
    if (c.kind != PULSE) {
        snprintf(out, cap, "ABGELEHNT STEP: Kanal %u hat keinen Puls - kein Startwert wird erfunden, "
                 "erster Puls nur per FIRST", _channel);
        return;
    }
    long dt = (labs(d) * 100 + TICK_US_X100 / 2) / TICK_US_X100;
    if (dt < 1) dt = 1;
    const long next = long(c.ticks) + (d > 0 ? dt : -dt);
    if (next < _limLo || next > _limHi) {
        snprintf(out, cap, "ABGELEHNT STEP: %ld us laege ausserhalb %ld..%ld us (jetzt %ld us)",
                 ticksToUs(uint16_t(next < 0 ? 0 : next)), ticksToUs(_limLo), ticksToUs(_limHi),
                 ticksToUs(c.ticks));
        return;
    }
    if (!writePulse(_channel, uint16_t(next))) { fail(out, cap, "i2c beim Schreiben"); return; }
    _hasWritten = true;
    _lastWriteMs = now;
    snprintf(out, cap, "OK STEP kanal=%u %ld -> %ld us", _channel, ticksToUs(c.ticks),
             ticksToUs(uint16_t(next)));
}

void ServoCal::cmdLimit(const char* a, const char* b, char* out, size_t cap) {
    char *e1, *e2;
    const long lo = a ? strtol(a, &e1, 10) : 0;
    const long hi = (a && b) ? strtol(b, &e2, 10) : 0;
    if (!_armed) { snprintf(out, cap, "ABGELEHNT LIMIT: nichts scharf"); return; }
    if (!a || !b || *e1 || *e2 || lo <= 0 || hi <= 0 || hi > 10000) {
        snprintf(out, cap, "ABGELEHNT LIMIT: LIMIT <lo_us> <hi_us>");
        return;
    }
    const uint16_t l = usToTicks(lo), h = usToTicks(hi);
    if (l >= h || l < _limLo || h > _limHi) {
        snprintf(out, cap, "ABGELEHNT LIMIT: nur ENGER als %ld..%ld us, lo<hi",
                 ticksToUs(_limLo), ticksToUs(_limHi));
        return;
    }
    Chan c;
    if (!readChan(_channel, c)) { fail(out, cap, "i2c"); return; }
    if (c.kind == PULSE && (c.ticks < l || c.ticks > h)) {
        snprintf(out, cap, "ABGELEHNT LIMIT: aktueller Puls %ld us laege ausserhalb", ticksToUs(c.ticks));
        return;
    }
    _limLo = l;
    _limHi = h;
    snprintf(out, cap, "OK LIMIT %ld..%ld us", ticksToUs(l), ticksToUs(h));
}

// STOP gibt keinen Zielwert aus: der Kanal wird auf "full off" gesetzt.
void ServoCal::cmdStop(const char* why, char* out, size_t cap) {
    _pending = false;
    if (!_armed) { snprintf(out, cap, "OK %s: nichts scharf, nichts geschrieben", why); return; }
    const uint8_t ch = _channel;
    _armed = false;
    if (!writeFullOff(ch)) {
        snprintf(out, cap, "FEHLER %s: i2c beim Abschalten von Kanal %u - nicht wiederholt", why, ch);
        return;
    }
    snprintf(out, cap, "OK %s: Kanal %u aus (full off), entwaffnet, kein Zielwert", why, ch);
}

void ServoCal::handleLine(const char* line, uint32_t nowMs, char* out, size_t cap) {
    out[0] = 0;
    char buf[48];
    strncpy(buf, line, sizeof buf - 1);
    buf[sizeof buf - 1] = 0;
    char* tok[3] = {0, 0, 0};
    int n = 0;
    for (char* p = strtok(buf, " \t\r\n"); p && n < 3; p = strtok(NULL, " \t\r\n")) tok[n++] = p;
    if (n == 0) return;                       // leere Zeile: kein Befehl
    for (char* p = tok[0]; *p; p++) *p = char(toupper((unsigned char)*p));
    _lastCmdMs = nowMs;
    if (strcmp(tok[0], "YES") != 0) _pending = false;   // alles ausser YES bricht ab
    if (!strcmp(tok[0], "HELP")) cmdHelp(out, cap);
    else if (!strcmp(tok[0], "STATUS")) cmdStatus(out, cap);
    else if (!strcmp(tok[0], "ARM")) cmdArm(tok[1], out, cap);
    else if (!strcmp(tok[0], "FIRST")) cmdFirst(tok[1], nowMs, out, cap);
    else if (!strcmp(tok[0], "YES")) cmdYes(nowMs, out, cap);
    else if (!strcmp(tok[0], "STEP")) cmdStep(tok[1], nowMs, out, cap);
    else if (!strcmp(tok[0], "LIMIT")) cmdLimit(tok[1], tok[2], out, cap);
    else if (!strcmp(tok[0], "STOP")) cmdStop("STOP", out, cap);
    else snprintf(out, cap, "UNBEKANNT '%s' - HELP", tok[0]);
}

void ServoCal::tick(uint32_t nowMs, char* out, size_t cap) {
    out[0] = 0;
    if (_armed && nowMs - _lastCmdMs > WATCHDOG_MS) cmdStop("WATCHDOG", out, cap);
}
