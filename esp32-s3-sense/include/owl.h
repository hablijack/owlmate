#pragma once

// ============================================================================
// The owl's shared surface: the behaviour state, the peripherals every module
// drives, and the last thing the camera saw.
//
// src/main.cpp was ~930 lines and had to be read in full before any firmware
// change could be made safely, because everything -- protocol, state machine,
// hardware check, wiring -- lived there as file-scope statics. It is now four
// files (main / behavior / protocol / hardware_check) and this header is the
// only thing they share.
//
// The rule that keeps it that way: anything only ONE module needs stays a
// static inside that module's .cpp. This header carries what genuinely crosses
// a boundary, and nothing else.
// ============================================================================

#include <Arduino.h>
#include "common.h"
#include "GC9D01.h"
#include "Eyes.h"
#include "Sensors.h"
#include "ServoController.h"
#include "FaceDetector.h"

// ----------------------------------------------------------------------------
// Behaviour states. The ESP32 owns this machine; the RPi only sends policy
// (sleep/wake) and temporary overrides. See behavior.h for the transitions.
// ----------------------------------------------------------------------------
enum class State {
    BOOT,
    IDLE,
    DETECTING,
    INTERACTING,
    SLEEPING,
    NAVIGATING,
    UPDATE,
    ERROR
};

// Protocol name for a state (telemetry's "state" field, heartbeat_ack).
// Defined in behavior.cpp next to the machine it names.
const char* stateToString(State state);

// ----------------------------------------------------------------------------
// Peripherals. Defined in main.cpp, which owns construction and bring-up;
// everyone else drives them through these references.
// ----------------------------------------------------------------------------
extern GC9D01 lcdLeft;
extern GC9D01 lcdRight;
extern Eyes eyes;
extern Sensors sensors;
extern ServoController servos;

// The most recent face-detection result, as a SNAPSHOT for core 1.
//
// The detection itself runs in its own task on core 0 (see include/vision.h);
// loop() copies the newest published result in here once per iteration, before
// the state machine runs. So behaviour and telemetry always agree on what the
// owl currently sees, and neither can catch a result half-written by the other
// core. Between refreshes the state machine keeps using the last one.
extern FaceResult_t faceResult;

// Measured main-loop rate, reported in telemetry. It is the RENDER rate: the
// loop draws the eyes and sleeps 16 ms, and nothing else in it is expensive
// since the inference moved off core 1 on 2026-08-31.
//
// Read it as the SEQUENCE, not the mean -- a mean cannot show a stall, and here
// it actively hid one. Measured 2026-08-31 with a face in frame, before and
// after the inference moved to core 0:
//
//   before  10.8 / 30.2 / 44.1 Hz   11 29 31 35 32 42 26 41 33 17 36 31 40 ...
//   after   15.6 / 28.3 / 61.7 Hz   29 29 29 27 27 29 29 29 29 21 29 29 25 ...
//
// The mean went DOWN. What changed is the spread: 3 of 55 samples were below
// 15 Hz before (visibly frozen), 0 of 58 after.
extern float loopHz;

// ----------------------------------------------------------------------------
// Display bring-up.
//
// Both panels sit on ONE SPI bus (shared SCK/MOSI/RST, private CS/DC), so the
// order inside is load-bearing:
//   1. begin the bus ONCE, with SS = -1 -- CS is driven in software by the
//      driver (see lib/GC9D01/GC9D01.h; relying on the peripheral's hardware
//      SS is what broke the two-panel case for so long),
//   2. attach BOTH panels before initialising either, so neither floats
//      selected while its sibling is being set up,
//   3. pulse the shared RST exactly once,
//   4. then init each panel.
//
// Shared because the hardware check brings the panels up too, before the normal
// boot path would have.
// ----------------------------------------------------------------------------
void bringUpDisplays(bool& leftOk, bool& rightOk);
