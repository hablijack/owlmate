#pragma once

#include <Arduino.h>
#include "GC9D01.h"
#include "common.h"

// One row of the shape table: everything needed to draw one mood.
//
// The base form is a superellipse  |x/halfW|^n + |y/halfH|^n = 1  (n = roundness
// / 10). n = 20 is a plain ellipse; ~26 gives the softly squared "blob" of the
// reference sheet. On top of that:
//
//   topSag   pushes the TOP edge down in the middle    -> flat / dome-down moods
//                                                        (annoyed, bored, skeptic)
//   botRise  pushes the BOTTOM edge up in the middle   -> crescents
//                                                        (happy, glee, squint)
//   slantIn  drops the top edge linearly toward the INNER corner  -> angry, furious
//   slantOut drops the top edge linearly toward the OUTER corner  -> worried, sad
//   yOff     shifts the whole shape vertically
//   asymH    shortens the RIGHT eye only, for moods that are deliberately
//            lopsided (skeptic)
//
// All values are pixels on a 160x160 panel except roundness (tenths).
struct EyeShape {
    uint8_t halfW;
    uint8_t halfH;
    uint8_t roundness;
    uint8_t topSag;
    uint8_t botRise;
    int8_t yOff;
    uint8_t slantIn;
    uint8_t slantOut;
    uint8_t asymH;
};

class Eyes {
public:
    Eyes(GC9D01& left, GC9D01& right);

    void setExpression(EyeExpression expr);
    void setGaze(float x, float y); // -1.0 to 1.0
    void blink(uint8_t speed = 3); // 1=fast, 5=slow
    void render();
    // Force the next render() to redraw and push a frame. render() skips the
    // whole redraw when nothing visible has changed. setExpression() and
    // setGaze() already mark the frame dirty themselves, so this is only needed
    // when state is changed by some other route.
    void markDirty() { _dirty = true; }

    EyeExpression getCurrentExpression() const { return _expr; }

    // Name <-> enum, backed by one table so the protocol strings and the shapes
    // cannot drift apart. nameOf() is what telemetry reports; parseName()
    // returns false for an unknown name rather than silently picking NEUTRAL.
    static const char* nameOf(EyeExpression e);
    static bool parseName(const char* name, EyeExpression& out);
    static const EyeShape& shapeOf(EyeExpression e);

private:
    // One eye = one bold black shape on a white field, plus white lids that
    // close over it. mirrored flips the asymmetric shapes (the angry lid) so
    // the two eyes angle inward toward the beak instead of both leaning the
    // same way.
    void renderEye(GC9D01& lcd, int cx, int cy, bool mirrored);
    void drawShape(GC9D01& lcd, int cx, int cy, bool mirrored);
    void drawBlob(GC9D01& lcd, const EyeShape& s, int cx, int cy, bool mirrored);
    void drawLids(GC9D01& lcd, float openness);
    void drawSpinner(GC9D01& lcd);
    void drawErrorX(GC9D01& lcd);
    void drawClosed(GC9D01& lcd);
    void fillTriangle(GC9D01& lcd, int x0, int y0, int x1, int y1,
                      int x2, int y2, uint16_t color);
    float currentOpenness() const;

    GC9D01& _left;
    GC9D01& _right;

    EyeExpression _expr;
    bool _sleeping;
    float _gazeX;
    float _gazeY;
    uint8_t _blinkProgress;
    uint8_t _blinkSpeed;
    bool _blinking;
    uint32_t _lastBlink;
    uint32_t _nextBlinkTime;

    // Dirty-flag rendering: skip the full redraw when the visible
    // frame is identical to the last one sent to the LCDs.
    bool _dirty;
    EyeExpression _lastExpr;
    bool _lastSleeping;
    int _lastIrisCX;
    int _lastIrisCY;
    uint8_t _lastBlinkProgress;

    // UPDATE spinner animation timing
    uint32_t _lastAnimFrame;
    uint16_t _animPhase;
};
