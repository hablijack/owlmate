#include "behavior.h"

#include <WiFi.h>
#include <WebServer.h>
#include <HTTPUpdateServer.h>
#include "config.h"

// ============================================================================
// The behaviour state machine. See behavior.h for the transition table.
//
// Everything below is private to this file on purpose: the override timers and
// the nav target used to be file-scope statics in main.cpp alongside the
// protocol parser and the hardware check, which meant all three had to be read
// together. The protocol reaches them only through the narrow surface in
// behavior.h.
// ============================================================================

namespace {

State currentState = State::BOOT;
uint32_t lastFaceSeen = 0;
uint32_t updateModeSince = 0;
uint32_t navSince = 0;
float navTargetAngle = 0.0f;

// Expires when the HAPPY burst on entering INTERACTING is over. Armed in
// transitionTo(), NOT in the state branch: loop() runs that branch ~60x/s, so
// arming it there would restart the burst forever and reproduce exactly the
// frozen grin it replaces.
uint32_t greetUntil = 0;

// Temporary overrides from the RPi supervisor. These take precedence over the
// state-driven expression/gaze until they expire or the state changes. They
// never change the state itself.
EyeExpression overrideExpr = EyeExpression::NEUTRAL;
uint32_t overrideExprUntil = 0;
bool overrideGazeActive = false;
float overrideGazeX = 0.0f;
float overrideGazeY = 0.0f;
uint32_t overrideGazeUntil = 0;

// OTA update mode. Only this file uses them, so only this file owns them.
WebServer webServer(80);
HTTPUpdateServer httpUpdateServer;
bool updateServerReady = false;

// Apply the expression for the current state, honoring an active supervisor
// expression override.
void applyExpression(EyeExpression stateExpr) {
    if (millis() < overrideExprUntil) {
        eyes.setExpression(overrideExpr);
    } else {
        eyes.setExpression(stateExpr);
    }
}

// Apply the gaze for the current state, honoring an active supervisor gaze
// override.
void applyGaze(float stateGx, float stateGy) {
    if (overrideGazeActive && millis() < overrideGazeUntil) {
        eyes.setGaze(overrideGazeX, overrideGazeY);
    } else {
        overrideGazeActive = false;
        eyes.setGaze(stateGx, stateGy);
    }
}

}  // namespace

const char* stateToString(State state) {
    switch (state) {
        case State::BOOT: return "boot";
        case State::IDLE: return "idle";
        case State::DETECTING: return "detecting";
        case State::INTERACTING: return "interacting";
        case State::SLEEPING: return "sleeping";
        case State::NAVIGATING: return "navigating";
        case State::UPDATE: return "update";
        case State::ERROR: return "error";
        default: return "unknown";
    }
}

namespace behavior {

State current() {
    return currentState;
}

float navAngle() {
    return navTargetAngle;
}

// Change state and clear any active supervisor overrides so the new state's
// expression/gaze take effect immediately.
void transitionTo(State newState) {
    if (newState == currentState) return;
    currentState = newState;
    overrideExprUntil = 0;
    overrideGazeActive = false;
    // Entering INTERACTING is the event "I have seen you". The early return
    // above (newState == currentState) is what keeps the burst to one per
    // genuine entry rather than one per frame.
    if (newState == State::INTERACTING) {
        greetUntil = millis() + INTERACT_GREET_MS;
    }
}

void overrideExpression(EyeExpression e) {
    overrideExpr = e;
    overrideExprUntil = millis() + EXPRESSION_OVERRIDE_MS;
    eyes.setExpression(e);
}

void overrideGaze(float x, float y) {
    overrideGazeX = x;
    overrideGazeY = y;
    overrideGazeActive = true;
    overrideGazeUntil = millis() + GAZE_OVERRIDE_MS;
    eyes.setGaze(x, y);
}

void setNavTarget(float angle, bool active) {
    if (active) {
        navTargetAngle = angle;
        if (currentState != State::NAVIGATING) {
            transitionTo(State::NAVIGATING);
        }
        navSince = millis();  // (re)arm the self-timeout on every update
        eyes.setGaze(0, 0);
        servos.setAngle(CH_HEAD, angle);
    } else {
        navTargetAngle = 0.0f;
        if (currentState == State::NAVIGATING) {
            transitionTo(State::IDLE);
        }
        eyes.setGaze(0, 0);
        servos.setAngle(CH_HEAD, 0);
    }
}

// Bring up the SoftAP + /update HTTP server for a firmware update.
void enterUpdateMode() {
    transitionTo(State::UPDATE);
    updateModeSince = millis();
    servos.setCenter();
    eyes.setGaze(0, 0);
    eyes.setExpression(EyeExpression::UPDATE);

    WiFi.mode(WIFI_AP);
    WiFi.softAP(UPDATE_AP_SSID, UPDATE_AP_PASSWORD);
    delay(100); // let the AP interface come up

    // The SoftAP password is the only authentication; the /update page is
    // only reachable on the isolated update network. Configure the handlers
    // only once: webServer.stop() does not remove them.
    if (!updateServerReady) {
        httpUpdateServer.setup(&webServer, "/update");
        updateServerReady = true;
    }
    webServer.begin();

    String out = "{\"type\":\"update_mode\",\"ssid\":\"";
    out += UPDATE_AP_SSID;
    out += "\",\"password\":\"";
    out += UPDATE_AP_PASSWORD;
    out += "\",\"url\":\"http://";
    out += WiFi.softAPIP().toString();
    out += "/update\"}";
    Serial.println(out);
}

// Tear down the SoftAP and resume normal behavior.
void exitUpdateMode() {
    webServer.stop();
    WiFi.mode(WIFI_OFF);
    transitionTo(State::IDLE);
    eyes.setGaze(0, 0);
    // faceResult is NOT cleared here any more, and must not be: it is a
    // snapshot that loop() refreshes from the core-0 task every iteration, so
    // a write here would be overwritten a few milliseconds later. Clearing on
    // the way OUT of detection is vision::setEnabled(false)'s job, and that
    // already happened when update mode was entered.
    Serial.println("{\"type\":\"update_mode_end\"}");
}

void serveUpdateClient() {
    if (currentState == State::UPDATE) {
        webServer.handleClient();
    }
}

void update() {
    // 4-tap vibration sequence enters update mode from any state.
    VibrationData vib = sensors.getVibration();
    if (vib.updateSequence) {
        sensors.clearUpdateSequence();
        if (currentState != State::UPDATE) {
            enterUpdateMode();
            return;
        }
    }

    if (currentState == State::UPDATE) {
        // A new tap after the grace period exits update mode. The 4th tap
        // that entered the mode (or a still-pressed contact) does not.
        if (vib.detected && vib.lastDetected > updateModeSince + UPDATE_EXIT_GRACE_MS) {
            exitUpdateMode();
            return;
        }
        applyExpression(EyeExpression::UPDATE);
        servos.setCenter();
        return;
    }

    switch (currentState) {
        case State::BOOT: {
            applyExpression(EyeExpression::SEARCHING);
            if (millis() > BOOT_TIMEOUT_MS) {
                transitionTo(State::IDLE);
            }
            break;
        }

        case State::IDLE: {
            applyExpression(EyeExpression::NEUTRAL);
            if (vib.detected) {
                // Vibration wakes us up to look for a face.
                lastFaceSeen = millis();
                transitionTo(State::DETECTING);
                servos.setAngle(CH_HEAD, 0); // Look forward
            } else if (faceResult.detected) {
                // A face nearby wakes us up.
                lastFaceSeen = millis();
                transitionTo(State::DETECTING);
                servos.setAngle(CH_HEAD, 0);
            }
            break;
        }

        case State::DETECTING: {
            applyExpression(EyeExpression::DETECTING);
            if (faceResult.detected) {
                // Face confirmed: start interacting and follow it.
                lastFaceSeen = millis();
                applyGaze(faceResult.gaze_x, faceResult.gaze_y);
                transitionTo(State::INTERACTING);
            } else if (millis() - lastFaceSeen > DETECT_TIMEOUT_MS) {
                // Nothing found: give up and go back to idle.
                transitionTo(State::IDLE);
                eyes.setGaze(0, 0);
                servos.setCenter();
            }
            break;
        }

        case State::INTERACTING: {
            // Two beats instead of one frozen face: a brief SURPRISED in
            // reaction to noticing someone, then AWE for as long as we keep
            // tracking.
            //
            // The greeting was HAPPY until 2026-08-31, and that was the same
            // mistake twice. HAPPY carries botRise 62 against a halfH of 42,
            // so its centre column is only 22 px tall -- a deep crescent, which
            // reads as a SHUT eye. That is exactly why sustained HAPPY was
            // replaced by AWE the day before; the one-second greeting kept it
            // and therefore kept the defect. Seen on hardware and reported as
            // "a horizontal line before the happy eyes".
            //
            // SURPRISED is the right beat anyway: 40x47 with no botRise, no
            // topSag and no slant -- 94 px of open eye, the widest in the
            // table. "I just noticed you" is a widening, not a squeeze. It
            // also contrasts with AWE (42x42, round) so the two beats stay
            // distinguishable.
            //
            // AWE for the sustained state: no botRise, no topSag, no slant,
            // widened to 42x42 at roundness 3.2 -- +40% area over NEUTRAL and
            // ROUND where every other mood is a taller oval, so the change is
            // visible across a room rather than only side by side.
            applyExpression(millis() < greetUntil ? EyeExpression::SURPRISED
                                                  : EyeExpression::AWE);
            if (faceResult.detected) {
                lastFaceSeen = millis();
                applyGaze(faceResult.gaze_x, faceResult.gaze_y);
            } else if (millis() - lastFaceSeen > INTERACT_TIMEOUT_MS) {
                // Face lost: return to idle.
                transitionTo(State::IDLE);
                eyes.setGaze(0, 0);
                servos.setCenter();
            }
            break;
        }

        case State::SLEEPING: {
            applyExpression(EyeExpression::SLEEPING);
            servos.setCenter();
            break;
        }

        case State::NAVIGATING: {
            // "Guide me home": the head is pinned to the RPi-computed compass
            // bearing (navTargetAngle) and ignores face-following. The RPi
            // re-sends the angle on each refresh, so the head tracks the
            // destination. If the RPi stops sending (link dropped / it crashed),
            // the self-timeout recenters the head and returns to idle so it is
            // never left stuck pointing somewhere.
            applyExpression(EyeExpression::SEARCHING);
            servos.setAngle(CH_HEAD, navTargetAngle);
            if (millis() - navSince > NAV_TIMEOUT_MS) {
                transitionTo(State::IDLE);
                servos.setCenter();
                eyes.setGaze(0, 0);
            }
            break;
        }

        case State::UPDATE:
            // Handled before the switch (SoftAP + OTA mode).
            break;

        case State::ERROR: {
            // Hardware fault: show the red X error face and hold servos
            // centered. The owl cannot recover from a boot-time init failure,
            // so it stays here until power-cycled.
            applyExpression(EyeExpression::ERROR);
            servos.setCenter();
            break;
        }
    }
}

}  // namespace behavior
