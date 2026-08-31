#pragma once

// ============================================================================
// The NDJSON wire protocol: one JSON object per line, over the USB CDC port.
//
// This is the ESP32 half of the contract with rpi-brain/brain/serial_handler.py
// -- a change on one side needs the matching change on the other, and SPEC-010
// R-010.5 requires that every field sent here is parsed there. It lives in its
// own file so it is as findable as its Python counterpart.
//
// The main env builds with -DCORE_DEBUG_LEVEL=0 because this same port carries
// the protocol: core log lines would be protocol garbage.
// ============================================================================

#include "owl.h"

namespace protocol {

// Drain the serial input and dispatch every complete line. Called once per
// main-loop iteration.
void poll();

// Handle one received NDJSON command line.
void handleCommand(const char* json);

// Emit one telemetry line (the owl's whole observable state).
void sendTelemetry();

}  // namespace protocol
