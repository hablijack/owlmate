#include "OwlChoreo.h"
#include <math.h>

static float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

// Fluegelschlag: zwei Schlaege (0-450 ms und 800-1250 ms) auf `amp`, dazwischen und danach 0.
// Der volle Hub (100 = Tabellenende) braucht ~0,45 s bei 300 %/s; die Beats sind darauf
// bemessen. Unter ~30 % Hub sieht man am linken Fluegel nichts (sichtbarer Beginn ~1240 us).
static float flutter(uint32_t sinceMs, float amp) {
    return (sinceMs < 450 || (sinceMs >= 800 && sinceMs < 1250)) ? amp : 0.0f;
}

OwlChoreo::OwlChoreo(const ChoreoConfig& cfg, Rng rng)
    : _cfg(cfg), _rng(rng), _init(false), _saccadeLockUntil(0), _mode(OM_REST), _last(0), _modeSince(0),
      _headTarget(0), _nextTwitch(0), _twitchUntil(0), _twitchEar(0), _nextGlance(0),
      _glanceUntil(0), _glanceTarget(0), _stuckSince(0), _trackOffUntil(0),
      _nextRuffle(0), _ruffleStart(0), _gxFilt(0) {}

float OwlChoreo::rnd01() { return float(_rng() % 10000u) / 10000.0f; }

void OwlChoreo::update(uint32_t now, OwlMode mode, bool face, float gx, ChoreoOut& out) {
    float dt = 0.0f;
    if (_init && now > _last) dt = clampf(float(now - _last) / 1000.0f, 0.0f, 0.2f);
    if (!_init) {
        _init = true;
        _nextTwitch = now + 6000 + uint32_t(rnd01() * 8000.0f);
        _nextGlance = now + 10000 + uint32_t(rnd01() * 20000.0f);
        _nextRuffle = now + 30000 + uint32_t(rnd01() * 40000.0f);
    }
    _last = now;
    if (mode != _mode) {
        _mode = mode;
        _modeSince = now;
        _stuckSince = 0;
        _gxFilt = 0.0f;
        if (mode == OM_IDLE) {
            _nextTwitch = now + 6000 + uint32_t(rnd01() * 8000.0f);
            _nextGlance = now + 10000 + uint32_t(rnd01() * 20000.0f);
            _glanceUntil = 0;
            _nextRuffle = now + 30000 + uint32_t(rnd01() * 40000.0f);
            _ruffleStart = 0;
        }
    }
    const uint32_t since = now - _modeSince;

    out.earL = out.earR = 50.0f;   // Mitte: tiefe Ohren wirken traurig (Besitzer, 2026-10-31)
    out.wingL = out.wingR = 0.0f;
    out.headValid = true;

    switch (mode) {
    case OM_REST:
        out.earL = out.earR = 5.0f;
        _headTarget = 0;
        break;
    case OM_IDLE:
        if (now >= _nextTwitch && _twitchUntil <= now) {
            _twitchEar = uint8_t(_rng() % 3u);
            _twitchUntil = now + 450;
            _nextTwitch = now + 6000 + uint32_t(rnd01() * 8000.0f);
        }
        if (now < _twitchUntil) {
            if (_twitchEar != 1) out.earL = 80.0f;
            if (_twitchEar != 0) out.earR = 80.0f;
        }
        if (now >= _nextGlance && _glanceUntil <= now) {
            _glanceTarget = (rnd01() * 2.0f - 1.0f) * _cfg.glanceMaxDeg;
            _glanceUntil = now + 2500 + uint32_t(rnd01() * 2500.0f);
            _nextGlance = _glanceUntil + 10000 + uint32_t(rnd01() * 20000.0f);
        }
        _headTarget = now < _glanceUntil ? _glanceTarget : 0.0f;
        if (now >= _nextRuffle && _ruffleStart == 0) _ruffleStart = now ? now : 1;
        if (_ruffleStart != 0) {
            if (now - _ruffleStart < 1100) {
                out.wingL = out.wingR = (now - _ruffleStart) < 500 ? 100.0f : 0.0f;
            } else {
                _ruffleStart = 0;
                _nextRuffle = now + 30000 + uint32_t(rnd01() * 40000.0f);
            }
        }
        break;
    case OM_ALERT:
        out.earL = out.earR = 62.0f;   // Kopf bleibt, wo er ist
        break;
    case OM_ENGAGED: {
        out.earL = out.earR = since < 1200 ? 85.0f : 55.0f;
        out.wingL = out.wingR = flutter(since, 100.0f);
        if (now < _trackOffUntil) {
            _headTarget = 0;
        } else if (face) {
            // Die Gesichtsposition kommt nur ~5x pro Sekunde und rauscht: tiefpassfiltern
            // (Zeitkonstante 0,08 s), sonst folgt der Kopf dem Rauschen in kleinen Rucken. Kurz
            // gehalten, weil jede Verzoegerung in der Kamera-Regelschleife die Stabilitaet kostet.
            _gxFilt += (gx - _gxFilt) * (1.0f - expf(-dt / 0.08f));
            // Sakkade statt Dauernachfuehrung: ein Ziel setzen, der Servo faehrt es in EINER glatten
            // Bewegung an. Waehrend der Bewegung und kurz danach (Bild ist veraltet) wird nicht neu
            // gezielt. Ein Integrator, der jeden 5-Hz-Wert nachsteuert, fuehlte sich wie Stoesse an.
            if (now >= _saccadeLockUntil && fabsf(_gxFilt) >= _cfg.trackDeadband) {
                const float delta = _cfg.headSign * _cfg.trackFovDeg * _cfg.saccadeGain * _gxFilt;
                const float target = clampf(_headTarget + delta, -_cfg.trackLimitDeg, _cfg.trackLimitDeg);
                const float moveDeg = fabsf(target - _headTarget);
                _headTarget = target;
                _saccadeLockUntil = now + uint32_t(_cfg.saccadeSettleMs + 1000.0f * moveDeg / _cfg.trackDegPerSec);
            }
            const bool atLimit = fabsf(_headTarget) >= _cfg.trackLimitDeg - 0.5f;
            if (atLimit && fabsf(gx) > 0.5f) {
                if (_stuckSince == 0) _stuckSince = now ? now : 1;
                else if (now - _stuckSince > 3000) {
                    _trackOffUntil = now + 30000;
                    _headTarget = 0;
                    _stuckSince = 0;
                }
            } else {
                _stuckSince = 0;
            }
        }
        break;
    }
    case OM_NAV:
        out.earL = out.earR = 35.0f;
        out.headValid = false;
        _headTarget = 0;
        break;
    }
    out.earL = clampf(out.earL, 0.0f, 100.0f);
    out.earR = clampf(out.earR, 0.0f, 100.0f);
    out.head = _headTarget;
}
