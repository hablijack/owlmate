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

// The most recent face-detection result. loop() refreshes it on
// FACE_DETECT_INTERVAL_MS; behaviour and telemetry both read it, and between
// refreshes the state machine keeps using the last one.
extern FaceResult_t faceResult;

// Measured main-loop rate, reported in telemetry. The loop renders the eyes and
// sleeps 16 ms; both push this down, and with it the number of detection runs.
// FACE_DETECT_INTERVAL_MS is only an upper bound -- what actually happens is
// not visible without measuring it.
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
