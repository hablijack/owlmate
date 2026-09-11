#pragma once

// ============================================================================
// The behaviour state machine -- the owl's autonomy.
//
// It runs from LOCAL inputs only (vibration sensor + on-device face detection +
// timeouts from config.h). The Orange Pi supervisor is NOT a second state machine: it
// sends policy (sleep/wake) and temporary overrides, both of which arrive here
// through the functions below. A duplicate machine used to exist on both sides
// and the two fought over expressions; don't reintroduce one.
//
//   BOOT --(3s)--> IDLE
//   IDLE --(vibration OR face)--> DETECTING
//   DETECTING --(face confirmed)--> INTERACTING
//   DETECTING --(no face for 10s)--> IDLE
//   INTERACTING --(face lost for 5s)--> IDLE
//   SLEEPING --(wake command)--> IDLE
//   any --(sleep command)--> SLEEPING
//   any --(nav active=true)--> NAVIGATING
//   NAVIGATING --(nav active=false)--> IDLE
//   NAVIGATING --(no nav update for NAV_TIMEOUT_MS)--> IDLE  (self-timeout)
//   any --(4 rapid taps)--> UPDATE
//   UPDATE --(1 tap)--> IDLE
// ============================================================================

#include "owl.h"

namespace behavior {

// Where the owl is right now.
State current();

// Change state, clearing any active supervisor override so the new state's
// expression/gaze take effect immediately.
void transitionTo(State newState);

// One step of the machine. Called once per main-loop iteration.
void update();

// ---- Supervisor overrides (temporary; they expire on their own and NEVER
// ---- change the state) ------------------------------------------------------

// Show `e` for EXPRESSION_OVERRIDE_MS, in preference to the state's expression.
void overrideExpression(EyeExpression e);

// Aim the eyes at (x, y) for GAZE_OVERRIDE_MS, in preference to the state's
// gaze.
void overrideGaze(float x, float y);

// ---- Navigation (persistent, unlike the two above) --------------------------

// active=true enters/holds NAVIGATING with the head pinned to `angle`, and
// re-arms the NAV_TIMEOUT_MS self-timeout. active=false recenters and returns
// to IDLE. The Orange Pi recomputes the bearing from live GPS + heading and re-sends
// it on each refresh, so the head tracks the destination.
void setNavTarget(float angle, bool active);

// The angle currently being held (0 when not navigating). For telemetry.
float navAngle();

// ---- OTA update mode --------------------------------------------------------

// Bring up SoftAP + the /update HTTP server; entered by 4 vibration taps.
void enterUpdateMode();

// Tear the SoftAP down and resume normal behaviour.
void exitUpdateMode();

// Serve one round of /update requests. A no-op unless the owl is in UPDATE, so
// loop() can call it unconditionally.
void serveUpdateClient();

}  // namespace behavior
