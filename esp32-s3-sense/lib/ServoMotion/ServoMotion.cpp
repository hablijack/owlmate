#include "ServoMotion.h"
#include <math.h>
#include <string.h>

static const uint8_t REG_MODE1 = 0x00;
static const uint8_t REG_LED0_ON_L = 0x06;
static const uint8_t REG_PRESCALE = 0xFE;
static const uint8_t MODE1_SLEEP = 0x10;
static const uint8_t MODE1_AI = 0x20;
static const uint8_t MODE1_RESTART = 0x80;
static const uint16_t FULL_BIT = 0x1000;
static const long TICK_US_X100 = (long(SERVO_PWM_PRESCALE) + 1) * 4;   // Tick in 1/100 us (122*4 = 488 -> 4,88 us)
static const float MAX_DT = 0.1f;        // laengere Aussetzer zaehlen wie 100 ms

ServoMotion::ServoMotion(ServoBus& bus, const ServoProfile* profiles, uint8_t count)
    : _bus(bus), _ready(false), _fault(false), _lastMs(0), _haveLast(false) {
    memset(_ch, 0, sizeof(_ch));
    for (uint8_t i = 0; i < count; i++) {
        const ServoProfile& p = profiles[i];
        if (p.channel >= NUM_CH || _ch[p.channel].prof || !profileValid(p)) continue;
        Chan& c = _ch[p.channel];
        c.prof = &p;
        c.valid = true;
        const uint16_t a = usToTicks(p.pts[0].us), b = usToTicks(p.pts[p.n - 1].us);
        c.tMin = a < b ? a : b;
        c.tMax = a < b ? b : a;
    }
}

uint16_t ServoMotion::usToTicks(float us) {
    return uint16_t((long(us * 100.0f + 0.5f) + TICK_US_X100 / 2) / TICK_US_X100);
}

float ServoMotion::ticksToUs(uint16_t ticks) {
    return float(long(ticks) * TICK_US_X100) / 100.0f;
}

bool ServoMotion::profileValid(const ServoProfile& p) {
    if (p.channel >= NUM_CH || p.n < 2 || p.n > MAX_CURVE_POINTS) return false;
    if (!(p.maxSpeed > 0.0f) || !(p.smoothTime >= 0.03f)) return false;
    if (!isfinite(p.maxSpeed) || !isfinite(p.smoothTime) || !isfinite(p.rest)) return false;
    int dir = 0;
    for (uint8_t i = 0; i < p.n; i++) {
        if (!isfinite(p.pts[i].pos) || p.pts[i].us < 400 || p.pts[i].us > 2600) return false;
        if (i == 0) continue;
        if (!(p.pts[i].pos > p.pts[i - 1].pos)) return false;
        const int d = p.pts[i].us > p.pts[i - 1].us ? 1 : (p.pts[i].us < p.pts[i - 1].us ? -1 : 0);
        if (d == 0 || (dir != 0 && d != dir)) return false;
        dir = d;
    }
    return p.rest >= p.pts[0].pos && p.rest <= p.pts[p.n - 1].pos;
}

float ServoMotion::posToUs(const ServoProfile& p, float pos) {
    if (pos <= p.pts[0].pos) return p.pts[0].us;
    if (pos >= p.pts[p.n - 1].pos) return p.pts[p.n - 1].us;
    for (uint8_t i = 1; i < p.n; i++) {
        if (pos <= p.pts[i].pos) {
            const float t = (pos - p.pts[i - 1].pos) / (p.pts[i].pos - p.pts[i - 1].pos);
            return p.pts[i - 1].us + t * (float(p.pts[i].us) - float(p.pts[i - 1].us));
        }
    }
    return p.pts[p.n - 1].us;
}

float ServoMotion::usToPos(const ServoProfile& p, float us) {
    for (uint8_t i = 1; i < p.n; i++) {
        const float a = p.pts[i - 1].us, b = p.pts[i].us;
        const float lo = a < b ? a : b, hi = a < b ? b : a;
        if (us >= lo && us <= hi) {
            const float t = (us - a) / (b - a);
            return p.pts[i - 1].pos + t * (p.pts[i].pos - p.pts[i - 1].pos);
        }
    }
    return p.rest;
}

bool ServoMotion::readChan(uint8_t ch, Kind& kind, uint16_t& ticks) {
    // Einzelne Lesezugriffe: ohne Auto-Increment (Power-on-Zustand) liefert ein
    // 4-Byte-Read viermal dasselbe Register (SPEC-016 Falsified).
    uint8_t b[4];
    for (uint8_t i = 0; i < 4; i++)
        if (!_bus.read(REG_LED0_ON_L + 4 * ch + i, &b[i], 1)) return false;
    const uint16_t on = b[0] | (uint16_t(b[1]) << 8);
    const uint16_t off = b[2] | (uint16_t(b[3]) << 8);
    ticks = 0;
    if (off & FULL_BIT) kind = FULL_OFF;
    else if (on & FULL_BIT) kind = FULL_ON;
    else if (on == 0 && off > 0 && off < 4096) { kind = PULSE; ticks = off; }
    else kind = UNKNOWN;
    return true;
}

bool ServoMotion::writeTicks(uint8_t ch, uint16_t ticks) {
    const uint8_t b[4] = {0, 0, uint8_t(ticks & 0xFF), uint8_t(ticks >> 8)};
    return _bus.write(REG_LED0_ON_L + 4 * ch, b, 4);
}

bool ServoMotion::begin() {
    _ready = false;
    _fault = false;
    uint8_t mode1 = 0, pre = 0;
    if (!_bus.read(REG_MODE1, &mode1, 1) || !_bus.read(REG_PRESCALE, &pre, 1)) {
        _fault = true;
        return false;
    }
    bool anyActive = false;
    for (uint8_t ch = 0; ch < NUM_CH; ch++) {
        Kind k;
        uint16_t t;
        if (!readChan(ch, k, t)) { _fault = true; return false; }
        if (k != FULL_OFF) anyActive = true;
        Chan& c = _ch[ch];
        if (!c.valid) continue;
        // Warmstart: ein gueltiger Puls wird uebernommen (kein Sprung). Alles andere
        // ist unbekannt; der Kanal startet dann bei seinem ersten Ziel an `rest`.
        if (k == PULSE && t >= c.tMin && t <= c.tMax) {
            c.active = true;
            c.last = t;
            c.cur = c.mid = c.tgt = usToPos(*c.prof, ticksToUs(t));
        }
    }
    const bool needsConfig = pre != PRESCALE || (mode1 & MODE1_SLEEP);
    if (needsConfig && anyActive) { _fault = true; return false; }
    if (needsConfig) {
        const uint8_t base = mode1 & uint8_t(~MODE1_RESTART);
        const uint8_t sleep = base | MODE1_SLEEP;
        const uint8_t awake = uint8_t((base & ~MODE1_SLEEP) | MODE1_AI);
        const uint8_t pres = PRESCALE;
        const uint8_t restart = awake | MODE1_RESTART;
        bool ok = _bus.write(REG_MODE1, &sleep, 1) && _bus.write(REG_PRESCALE, &pres, 1)
               && _bus.write(REG_MODE1, &awake, 1);
        if (ok) _bus.delayMs(5);   // Oszillator braucht >500 us vor RESTART
        ok = ok && _bus.write(REG_MODE1, &restart, 1);
        uint8_t p2 = 0, m2 = 0;
        if (!ok || !_bus.read(REG_PRESCALE, &p2, 1) || !_bus.read(REG_MODE1, &m2, 1)
            || p2 != PRESCALE || (m2 & MODE1_SLEEP)) { _fault = true; return false; }
    } else if (!(mode1 & MODE1_AI)) {
        const uint8_t ai = uint8_t((mode1 & ~MODE1_RESTART) | MODE1_AI);
        if (!_bus.write(REG_MODE1, &ai, 1)) { _fault = true; return false; }
    }
    _ready = true;
    return true;
}

bool ServoMotion::setTarget(uint8_t ch, float pos) {
    if (ch >= NUM_CH || !_ch[ch].valid || !isfinite(pos)) return false;
    Chan& c = _ch[ch];
    const float lo = c.prof->pts[0].pos, hi = c.prof->pts[c.prof->n - 1].pos;
    c.tgt = pos < lo ? lo : (pos > hi ? hi : pos);
    c.hasTarget = true;
    return true;
}

float ServoMotion::smoothDamp(float cur, float target, float& vel, float smoothTime,
                              float maxSpeed, float dt) {
    // Kritisch gedaempfte Feder, analytisch angenaehert, fuer jedes dt stabil (Unity-SmoothDamp-Form).
    const float omega = 2.0f / smoothTime;
    const float x = omega * dt;
    const float ex = 1.0f / (1.0f + x + 0.48f * x * x + 0.235f * x * x * x);
    float change = cur - target;
    const float maxChange = maxSpeed * smoothTime;
    if (change > maxChange) change = maxChange;
    if (change < -maxChange) change = -maxChange;
    const float goal = cur - change;
    const float temp = (vel + omega * change) * dt;
    vel = (vel - omega * temp) * ex;
    float out = goal + (change + temp) * ex;
    if ((target - cur > 0.0f) == (out > target)) { out = target; vel = 0.0f; }   // kein Ueberschwingen
    return out;
}

void ServoMotion::drive(Chan& c, uint8_t ch, float dt) {
    const ServoProfile& p = *c.prof;
    const float lo = p.pts[0].pos, hi = p.pts[p.n - 1].pos;
    if (!c.active) {
        c.cur = c.mid = p.rest;
        c.vel = c.midVel = 0;
        const uint16_t t = usToTicks(posToUs(p, c.cur));
        if (!writeTicks(ch, t)) { if (++c.errs >= 3) _fault = true; return; }
        c.errs = 0;
        c.active = true;
        c.last = t;
        return;
    }
    // Zwei kritisch gedaempfte Federn hintereinander: die erste formt das Ziel (mid),
    // die zweite folgt ihm. Eine einzelne Feder springt bei einem Zielsprung sofort auf
    // hohe Geschwindigkeit (unendlicher Ruck); zwei laufen weich an UND aus - der
    // Unterschied zwischen "roboterhaft" und organisch. Danach eine harte
    // Geschwindigkeitsgrenze, damit nichts schneller laeuft als im Profil erlaubt.
    if (dt > 0.0f) {
        c.mid = smoothDamp(c.mid, c.tgt, c.midVel, p.smoothTime * 0.7f, p.maxSpeed, dt);
        float out = smoothDamp(c.cur, c.mid, c.vel, p.smoothTime, p.maxSpeed, dt);
        const float step = p.maxSpeed * dt;
        if (out - c.cur > step) out = c.cur + step;
        if (out - c.cur < -step) out = c.cur - step;
        if (c.vel > p.maxSpeed) c.vel = p.maxSpeed;
        if (c.vel < -p.maxSpeed) c.vel = -p.maxSpeed;
        c.cur = out;
    }
    if (c.cur < lo) { c.cur = lo; c.vel = 0; }
    if (c.cur > hi) { c.cur = hi; c.vel = 0; }
    // Das letzte Tick-Stueck NICHT auslaufen lassen: die Feder naehert sich dem Ziel
    // exponentiell, schiebt also im Endbereich nur noch alle paar hundert ms um einen
    // Tick - das sah als Stolpern aus. Unter einem Tick Restweg wird gesetzt.
    {
        const float usSpan = fabsf(float(p.pts[p.n - 1].us) - float(p.pts[0].us));
        const float unitsPerTick = tickUs() * (hi - lo) / usSpan;
        if (fabsf(c.tgt - c.cur) < unitsPerTick && fabsf(c.vel) < p.maxSpeed * 0.05f
            && fabsf(c.mid - c.tgt) < unitsPerTick) {
            c.cur = c.mid = c.tgt; c.vel = c.midVel = 0;
        }
    }
    uint16_t t = usToTicks(posToUs(p, c.cur));
    if (t < c.tMin) t = c.tMin;
    if (t > c.tMax) t = c.tMax;
    if (t == c.last) return;
    if (!writeTicks(ch, t)) { if (++c.errs >= 3) _fault = true; return; }
    c.errs = 0;
    c.last = t;
}

void ServoMotion::update(uint32_t nowMs) {
    if (!_ready || _fault) return;
    float dt = 0.0f;
    if (_haveLast && nowMs > _lastMs) {
        dt = float(nowMs - _lastMs) / 1000.0f;
        if (dt > MAX_DT) dt = MAX_DT;
    }
    _lastMs = nowMs;
    _haveLast = true;
    for (uint8_t ch = 0; ch < NUM_CH && !_fault; ch++) {
        Chan& c = _ch[ch];
        if (c.valid && c.hasTarget) drive(c, ch, dt);
    }
}

float ServoMotion::position(uint8_t ch) const {
    return ch < NUM_CH && _ch[ch].valid && _ch[ch].active ? _ch[ch].cur : 0.0f;
}
float ServoMotion::target(uint8_t ch) const { return ch < NUM_CH && _ch[ch].valid ? _ch[ch].tgt : 0.0f; }
bool ServoMotion::driven(uint8_t ch) const { return ch < NUM_CH && _ch[ch].valid && _ch[ch].active; }
bool ServoMotion::hasProfile(uint8_t ch) const { return ch < NUM_CH && _ch[ch].valid; }
float ServoMotion::minPos(uint8_t ch) const { return hasProfile(ch) ? _ch[ch].prof->pts[0].pos : 0.0f; }
float ServoMotion::maxPos(uint8_t ch) const {
    return hasProfile(ch) ? _ch[ch].prof->pts[_ch[ch].prof->n - 1].pos : 0.0f;
}
uint16_t ServoMotion::minTicks(uint8_t ch) const { return hasProfile(ch) ? _ch[ch].tMin : 0; }
uint16_t ServoMotion::maxTicks(uint8_t ch) const { return hasProfile(ch) ? _ch[ch].tMax : 0; }
