#include "Eyes.h"
#include "config.h"   // EYE_GAZE_SIGN_X
#include <math.h>

// Auto-blink cadence: a random gap in [BLINK_GAP_MIN_MS, MIN + JITTER).
// Jittery on purpose -- a metronome blink reads as a machine idling.
//
// War 700/1800 (Luecke 0,7-2,5 s, im Mittel ~1,6 s). Das ist deutlich haeufiger
// als ein Mensch blinzelt (~3-4 s) und wirkte am fertigen Kopf nervoes. Jetzt
// 2,5-6,5 s, im Mittel ~4,5 s.
//
// Hat auch eine messbare Seite: JEDES Blinzeln erzwingt einen Neuaufbau beider
// Augen, und der Flush ist der teuerste Posten der Hauptschleife (SPEC-003).
// Ein Drittel so viele Blinzler heisst ein Drittel so viele Neuaufbauten.
#define BLINK_GAP_MIN_MS 2500
#define BLINK_GAP_JITTER_MS 4000

// Eye geometry and colors come from common.h (included via Eyes.h).

Eyes::Eyes(GC9D01& left, GC9D01& right)
    : _left(left), _right(right),
      _expr(EyeExpression::NEUTRAL),
      _sleeping(false),
      _gazeX(0.0f),
      _gazeY(0.0f),
      _blinkProgress(0),
      _blinkSpeed(3),
      _blinking(false),
      _lastBlink(0),
      _nextBlinkTime(millis() + BLINK_GAP_MIN_MS),
      _dirty(true),
      _lastExpr(EyeExpression::NEUTRAL),
      _lastSleeping(false),
      _lastIrisCX(EYE_CX),
      _lastIrisCY(EYE_CY),
      _lastBlinkProgress(0),
      _lastAnimFrame(0),
      _animPhase(0) {
}

void Eyes::setExpression(EyeExpression expr) {
    // NUR bei echter Aenderung schmutzig markieren. updateState() ruft
    // applyExpression()/applyGaze() in JEDER Runde der Hauptschleife auf, meist
    // mit unveraendertem Wert; ein bedingungsloses _dirty = true hat deshalb
    // die Skip-Logik in render() vollstaendig ausgehebelt und in jeder Runde
    // einen kompletten Neuaufbau erzwungen. Gemessen 2026-08-28: 160 ms pro
    // Runde, Hauptschleife 4,4 Hz, und weil die Gesichtserkennung an der
    // Schleife haengt, dieselben 4,4 Erkennungsversuche/s statt der 10, die
    // FACE_DETECT_INTERVAL_MS zusagt.
    //
    // Sicher, weil render() den sichtbaren Zustand ohnehin selbst vergleicht
    // (_expr, _sleeping, Irisposition, _blinkProgress) und _dirty im
    // Konstruktor true ist - das erste Bild wird also in jedem Fall gezeichnet.
    if (expr == _expr) return;
    _expr = expr;
    _sleeping = (expr == EyeExpression::SLEEPING);
    _dirty = true;
}

void Eyes::setGaze(float x, float y) {
    // Wie setExpression(): nur bei echter Aenderung. Siehe dort.
    const float nx = constrain(x, -1.0f, 1.0f);
    const float ny = constrain(y, -1.0f, 1.0f);
    if (nx == _gazeX && ny == _gazeY) return;
    _gazeX = nx;
    _gazeY = ny;
    _dirty = true;
}

void Eyes::blink(uint8_t speed) {
    // speed: 1=fast ... 5=slow. Maps to the number of render ticks the eye
    // stays closed (the "dip" of the blink). Lower = snappier blink.
    _blinkSpeed = constrain(speed, 1, 5);
    _blinking = true;
    _blinkProgress = 0;
}

void Eyes::render() {
    // Auto-blink every 2-5 seconds (not while showing the update spinner)
    uint32_t now = millis();
    if (!_blinking && !_sleeping && _expr != EyeExpression::UPDATE && now > _nextBlinkTime) {
        blink();
        _lastBlink = now;
        _nextBlinkTime = now + BLINK_GAP_MIN_MS + random(BLINK_GAP_JITTER_MS);
    }

    // Update blink animation. Total duration = 2 * _blinkSpeed ticks
    // (close over _blinkSpeed ticks, then reopen over _blinkSpeed ticks).
    if (_blinking) {
        _blinkProgress++;
        if (_blinkProgress > 2 * _blinkSpeed) {
            _blinking = false;
            _blinkProgress = 0;
        }
    }

    // Blickrichtung in eine Augenposition umrechnen.
    //
    // Zwei Groessen, und beide waren zu klein, um sichtbar zu sein:
    //
    // GAZE_GAIN: _gazeX ist die Gesichtsposition im Kamerabild, normiert auf
    // -1..1. Volle Auslenkung hiesse "Gesicht am aeussersten Bildrand" - dort
    // steht niemand. Auf Hardware gemessen 2026-08-29 bei jemandem direkt vor
    // der Eule: gaze_x lag zwischen -0,34 und -0,03. Ohne Verstaerkung nutzt
    // die Anzeige also nie mehr als ein Drittel ihres Weges.
    //
    // TRAVEL: 8 px auf einem 160-px-Panel sind bei realistischem gaze_x ganze
    // ZWEI Pixel - unsichtbar. Das Panel hat einen nutzbaren Radius von 65 und
    // der Blob ist halb 33 breit, also waeren rund +-30 px moeglich; 18 laesst
    // bewusst Rand, damit die Form auch bei voller Auslenkung nicht am
    // Kreisrand anschlaegt.
    //
    // Vertikal bleibt bewusst kleiner als horizontal - echte Augen wandern
    // weniger auf und ab als zur Seite. NICHT auf gleiche Werte "korrigieren".
    static const float GAZE_GAIN = 2.5f;
    static const int TRAVEL_X = 18;
    static const int TRAVEL_Y = 12;
    const float gx = constrain(_gazeX * GAZE_GAIN * EYE_GAZE_SIGN_X, -1.0f, 1.0f);
    const float gy = constrain(_gazeY * GAZE_GAIN, -1.0f, 1.0f);
    int irisCX = EYE_CX + (int)(gx * TRAVEL_X);
    int irisCY = EYE_CY + (int)(gy * TRAVEL_Y);

    // The UPDATE spinner animates continuously: force a new frame
    // every 50ms (~20fps) while the update expression is active.
    if (_expr == EyeExpression::UPDATE && now - _lastAnimFrame >= 50) {
        _dirty = true;
        _animPhase = (_animPhase + 30) % 360;
    }

    // Skip the redraw if the visible frame is unchanged
    if (!_dirty &&
        _expr == _lastExpr &&
        _sleeping == _lastSleeping &&
        irisCX == _lastIrisCX &&
        irisCY == _lastIrisCY &&
        _blinkProgress == _lastBlinkProgress) {
        return;
    }

    renderEye(_left, irisCX, irisCY, false);
    renderEye(_right, irisCX, irisCY, true);

    _dirty = false;
    _lastExpr = _expr;
    _lastSleeping = _sleeping;
    _lastIrisCX = irisCX;
    _lastIrisCY = irisCY;
    _lastBlinkProgress = _blinkProgress;
    _lastAnimFrame = now;
}

// ===========================================================================
// Shape table -- one row per mood, in EyeExpression order.
//
// Tuned against the reference sheet. Numbers, not code: retune a mood by
// editing its row. tools/preview_eyes.py renders this same table to an HTML
// sheet using identical maths, so shapes can be judged without flashing.
//
//                     halfW halfH round tSag bRise yOff slIn slOut asym
// ===========================================================================
static const EyeShape SHAPES[] = {
    /* NEUTRAL     */ { 33, 40, 26,  0,  0,   0,  0,  0, 0 },
    /* BLINK_HIGH  */ { 32,  8, 22,  0,  0, -14,  0,  0, 0 },
    /* BLINK_LOW   */ { 32,  8, 22,  0,  0,  14,  0,  0, 0 },
    /* HAPPY       */ { 36, 42, 24,  0, 62,   6,  0,  0, 0 },
    /* GLEE        */ { 30, 30, 24,  0, 28,   4,  0,  0, 0 },
    /* SAD_DOWN    */ { 32, 34, 26,  0,  0,   8,  0, 30, 0 },
    /* SAD_UP      */ { 31, 34, 29,  0,  0,  -2,  0,  0, 0 },
    /* WORRIED     */ { 33, 38, 26,  0,  0,   2,  0, 34, 0 },
    /* FOCUSED     */ { 34, 30, 26,  0, 12,   2,  0, 10, 0 },
    /* ANNOYED     */ { 34, 32, 26, 26,  0,  -2,  0,  0, 0 },
    /* SURPRISED   */ { 40, 47, 26,  0,  0,   0,  0,  0, 0 },
    /* SKEPTIC     */ { 32, 27, 26, 20,  0,   0,  0,  0, 7 },
    /* BORED       */ { 34, 21, 24, 15,  0,   0,  0,  0, 0 },
    /* UNIMPRESSED */ { 34, 18, 22, 12,  0,   0,  0,  0, 0 },
    /* SLEEPY      */ { 33, 30, 29,  6,  0,   6,  0,  0, 0 },
    /* SUSPICIOUS  */ { 33, 32, 26,  0, 14,   0,  0,  0, 0 },
    /* SQUINT      */ { 33, 34, 22,  0, 52,   4,  0,  0, 0 },
    /* ANGRY       */ { 36, 40, 24,  0,  0,   2, 36,  0, 0 },
    /* FURIOUS     */ { 36, 42, 22,  0,  0,   2, 52,  0, 0 },
    /* SCARED      */ { 31, 45, 26,  0,  0,   0,  0,  0, 0 },
    /* AWE         */ { 35, 41, 30,  0,  0,   0,  0,  0, 0 },
    /* SLEEPING    */ { 32,  8, 22,  0,  0,  14,  0,  0, 0 },  // drawn as a bar
    /* SEARCHING   */ { 33, 32, 26,  0, 14,   0,  0,  0, 0 },  // = SUSPICIOUS
    /* DETECTING   */ { 34, 30, 26,  0, 12,   2,  0, 10, 0 },  // = FOCUSED
    /* UPDATE      */ { 33, 40, 26,  0,  0,   0,  0,  0, 0 },  // spinner instead
    /* ERROR       */ { 33, 40, 26,  0,  0,   0,  0,  0, 0 },  // cross instead
};

// Protocol names, same order. These are the strings the RPi sends in
// {"type":"expression","value":"..."} and that telemetry reports back as "eye".
static const char* const NAMES[] = {
    "neutral", "blink_high", "blink_low", "happy", "glee",
    "sad_down", "sad_up", "worried", "focused", "annoyed",
    "surprised", "skeptic", "bored", "unimpressed", "sleepy",
    "suspicious", "squint", "angry", "furious", "scared",
    "awe", "sleeping", "searching", "detecting", "update", "error",
};

static_assert(sizeof(SHAPES) / sizeof(SHAPES[0]) == (size_t)EyeExpression::_COUNT,
              "SHAPES is out of sync with EyeExpression");
static_assert(sizeof(NAMES) / sizeof(NAMES[0]) == (size_t)EyeExpression::_COUNT,
              "NAMES is out of sync with EyeExpression");

const EyeShape& Eyes::shapeOf(EyeExpression e) {
    const size_t i = (size_t)e;
    return SHAPES[i < (size_t)EyeExpression::_COUNT ? i : 0];
}

const char* Eyes::nameOf(EyeExpression e) {
    const size_t i = (size_t)e;
    return NAMES[i < (size_t)EyeExpression::_COUNT ? i : 0];
}

bool Eyes::parseName(const char* name, EyeExpression& out) {
    if (!name) return false;
    for (size_t i = 0; i < (size_t)EyeExpression::_COUNT; i++) {
        if (strcmp(name, NAMES[i]) == 0) {
            out = (EyeExpression)i;
            return true;
        }
    }
    return false;
}

float Eyes::currentOpenness() const {
    float o = 1.0f;
    if (_blinking) {
        const float half = (float)_blinkSpeed;
        o = (_blinkProgress <= _blinkSpeed)
                ? 1.0f - (float)_blinkProgress / half
                : (float)(_blinkProgress - _blinkSpeed) / half;
    }
    return constrain(o, 0.0f, 1.0f);
}

void Eyes::renderEye(GC9D01& lcd, int cx, int cy, bool mirrored) {
    lcd.fillScreen(COLOR_BG);

    if (_sleeping) {
        drawClosed(lcd);
        lcd.flush();
        return;
    }

    drawShape(lcd, cx, cy, mirrored);

    // The spinner and the error cross are status indicators, not eyes -- they
    // must not be clipped by a blink.
    if (_expr != EyeExpression::UPDATE && _expr != EyeExpression::ERROR) {
        drawLids(lcd, currentOpenness());
    }

    // Everything above only wrote to the in-memory framebuffer. Without this
    // the panel never receives a single pixel -- which is exactly what used to
    // happen: renderEye() drew a complete frame and then dropped it.
    lcd.flush();
}

void Eyes::drawShape(GC9D01& lcd, int cx, int cy, bool mirrored) {
    switch (_expr) {
        case EyeExpression::UPDATE:   drawSpinner(lcd); return;   // not an eye
        case EyeExpression::ERROR:    drawErrorX(lcd);  return;   // not an eye
        case EyeExpression::SLEEPING: drawClosed(lcd);  return;   // fully shut
        default: break;
    }
    drawBlob(lcd, shapeOf(_expr), cx, cy, mirrored);
}

// Draw one mood from its table row, column by column: for each x, work out
// where the top and the bottom edge sit and fill the span between them. Doing
// it per column (rather than per row) is what makes independent top/bottom edge
// profiles -- and therefore crescents and dome-down shapes -- fall out for free.
void Eyes::drawBlob(GC9D01& lcd, const EyeShape& s, int cx, int cy, bool mirrored) {
    const float n = s.roundness / 10.0f;
    const int halfH = (int)s.halfH - (mirrored ? (int)s.asymH : 0);
    if (halfH <= 0 || s.halfW == 0) return;
    const float yc = (float)cy + s.yOff;

    for (int dx = -(int)s.halfW; dx <= (int)s.halfW; dx++) {
        const float t = fabsf((float)dx) / (float)s.halfW;
        const float inner = 1.0f - powf(t, n);
        if (inner <= 0.0f) continue;
        const float ext = halfH * powf(inner, 1.0f / n);   // superellipse envelope
        const float bell = 1.0f - t * t;                   // 1 in the middle, 0 at the edges

        float yTop = yc - ext + s.topSag * bell;
        float yBot = yc + ext - s.botRise * bell;

        // Slants cut the top edge down along a straight line. uIn runs 0 at the
        // OUTER edge of this eye to 1 at the INNER edge (toward the beak), which
        // is why it depends on which eye we are drawing -- otherwise both eyes
        // would lean the same way instead of mirroring.
        const float uIn = mirrored ? (0.5f - (float)dx / (2.0f * s.halfW))
                                   : (0.5f + (float)dx / (2.0f * s.halfW));
        if (s.slantIn)  yTop = fmaxf(yTop, yc - halfH + s.slantIn * uIn);
        if (s.slantOut) yTop = fmaxf(yTop, yc - halfH + s.slantOut * (1.0f - uIn));

        const int y0 = (int)lroundf(yTop);
        const int y1 = (int)lroundf(yBot);
        if (y1 >= y0) lcd.drawFastVLine(cx + dx, y0, y1 - y0 + 1, COLOR_INK);
    }
}

// A single bold bar: a closed eye, and what a blink bottoms out on. Reads far
// better at this size than a blank white disc.
void Eyes::drawClosed(GC9D01& lcd) {
    lcd.fillRect(EYE_CX - LASH_HALF_W, EYE_CY - LASH_HALF_H,
                 LASH_HALF_W * 2, LASH_HALF_H * 2, COLOR_INK);
}

// Lids are background-coloured bars closing in from the top and bottom, so a
// blink simply eats into whatever shape is underneath.
void Eyes::drawLids(GC9D01& lcd, float openness) {
    if (openness >= 0.999f) return;

    const int half = (int)(EYE_R * openness);
    const int top = EYE_CY - half;
    const int bot = EYE_CY + half;

    if (top > 0) lcd.fillRect(0, 0, LCD_WIDTH, top, COLOR_BG);
    if (bot < LCD_HEIGHT) lcd.fillRect(0, bot, LCD_WIDTH, LCD_HEIGHT - bot, COLOR_BG);

    if (openness < 0.06f) drawClosed(lcd);
}

void Eyes::drawSpinner(GC9D01& lcd) {
    const int R = 50;
    for (int a = 0; a < 90; a += 3) {
        const float rad = (float)(_animPhase + a) * (float)DEG_TO_RAD;
        lcd.fillCircle(EYE_CX + (int)(cosf(rad) * R),
                       EYE_CY + (int)(sinf(rad) * R), 6, COLOR_INK);
    }
}

void Eyes::drawErrorX(GC9D01& lcd) {
    const int a = 34;
    for (int t = -4; t <= 4; t++) {
        lcd.drawLine(EYE_CX - a + t, EYE_CY - a, EYE_CX + a + t, EYE_CY + a, COLOR_INK);
        lcd.drawLine(EYE_CX + a + t, EYE_CY - a, EYE_CX - a + t, EYE_CY + a, COLOR_INK);
    }
}


