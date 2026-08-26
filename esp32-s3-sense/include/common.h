#pragma once

// Eye expressions.
//
// The first block mirrors the reference sheet the owl's look is based on. Every
// one of those is drawn by the SAME parametric routine (Eyes::drawBlob) from a
// row in the SHAPES table in Eyes.cpp -- a superellipse "blob" plus four
// modifiers (sag the top edge, raise the bottom edge, slant the top toward the
// inner or the outer corner). To retune a mood, change numbers in that table;
// do not add a bespoke drawing function.
//
// KEEP IN SYNC with SHAPES[] and NAMES[] in Eyes.cpp -- both are size-checked
// against _COUNT at compile time, so a mismatch is a build error, not a
// silently wrong eye.
enum class EyeExpression {
    // --- from the reference sheet ---
    NEUTRAL,
    BLINK_HIGH,
    BLINK_LOW,
    HAPPY,
    GLEE,
    SAD_DOWN,        // sad, looking down
    SAD_UP,          // sad, looking up at the user
    WORRIED,
    FOCUSED,         // focused / determined
    ANNOYED,
    SURPRISED,
    SKEPTIC,
    BORED,           // frustrated / bored
    UNIMPRESSED,
    SLEEPY,
    SUSPICIOUS,
    SQUINT,
    ANGRY,
    FURIOUS,
    SCARED,
    AWE,
    // --- states the owl needs that the sheet does not cover ---
    SLEEPING,        // fully closed lash bar
    SEARCHING,       // scanning; drawn as SUSPICIOUS
    DETECTING,       // locked on; drawn as FOCUSED
    UPDATE,          // OTA spinner, not an eye
    ERROR,           // cross, not an eye
    _COUNT
};

// LCD dimensions
#ifndef LCD_WIDTH
#define LCD_WIDTH 160
#endif

#ifndef LCD_HEIGHT
#define LCD_HEIGHT 160
#endif

// Eye geometry
#define EYE_CX (LCD_WIDTH / 2)
#define EYE_CY (LCD_HEIGHT / 2)
#define EYE_R 65               // usable radius of the round panel

// Deliberately two colours only. These panels are 160 px across and physically
// tiny behind the owl's eye openings, so fine detail (iris gradients, specular
// sparkles, grey lids) turns to mush at arm's length. The eye is drawn as one
// bold black shape on a white field -- manga style -- which stays readable.
#define COLOR_BG 0xFFFF        // white field
#define COLOR_INK 0x0000       // black shape

// Closed-eye / lash bar
#define LASH_HALF_W 46
#define LASH_HALF_H 3
