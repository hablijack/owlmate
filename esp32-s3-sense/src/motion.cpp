#include "motion.h"

#include <Arduino.h>
#include <string.h>
#include "config.h"
#include "owl.h"
#include "behavior.h"
#include "OwlChoreo.h"

namespace {

#if SERVO_MOTION_ENABLED
uint32_t randomWord() { return esp_random(); }

const ChoreoConfig CHOREO_CFG = {
    HEAD_TRACK_SIGN, HEAD_TRACK_DEG_PER_S, HEAD_TRACK_DEADBAND,
    HEAD_TRACK_LIMIT_DEG, HEAD_GLANCE_MAX_DEG,
    HEAD_TRACK_FOV_DEG, HEAD_SACCADE_GAIN, HEAD_SACCADE_SETTLE_MS,
};
OwlChoreo choreo(CHOREO_CFG, randomWord);
bool knockSeen = false;   // vorheriger Zustand von vibration.detected, fuer die Anstiegsflanke
#endif

}  // namespace

namespace motion {

bool react(const char* name) {
#if SERVO_MOTION_ENABLED
    if (!name || servos.locked() || !servos.ready()) return false;
    switch (behavior::current()) {
        case State::BOOT: case State::SLEEPING: case State::UPDATE: case State::ERROR: return false;
        default: break;
    }
    // Jede Geste bringt ihre Augen mit: Koerper und Gesicht gehoeren zusammen, sonst wirkt es leer.
    OwlGesture g = OG_NONE;
    EyeExpression eyes = EyeExpression::NEUTRAL;
    if (strcmp(name, "startle") == 0) { g = OG_STARTLE; eyes = EyeExpression::SURPRISED; }
    else if (strcmp(name, "happy") == 0) { g = OG_HAPPY; eyes = EyeExpression::GLEE; }
    else if (strcmp(name, "negative") == 0) { g = OG_NEGATIVE; eyes = EyeExpression::ANNOYED; }
    else if (strcmp(name, "curious") == 0) { g = OG_CURIOUS; eyes = EyeExpression::SKEPTIC; }
    if (!choreo.trigger(g, millis())) return false;
    behavior::overrideExpression(eyes);
    return true;
#else
    (void)name;
    return false;
#endif
}

void update() {
#if SERVO_MOTION_ENABLED
    if (servos.locked() || !servos.ready()) return;

    OwlMode mode;
    switch (behavior::current()) {
        case State::BOOT: return;   // nichts bewegen, bevor die Eule wach ist
        case State::IDLE: mode = OM_IDLE; break;
        case State::DETECTING: mode = OM_ALERT; break;
        case State::INTERACTING: mode = OM_ENGAGED; break;
        case State::NAVIGATING: mode = OM_NAV; break;
        case State::SLEEPING:
        case State::UPDATE:
        case State::ERROR:
        default: mode = OM_REST; break;
    }

    // Doppelklopfen = erschrecken: Ohren hoch, ein Fluegelschlag, grosse Augen.
    // Nach behavior::update() aufgerufen, damit der Zustandswechsel IDLE->DETECTING den
    // Ausdruck nicht wieder loescht. Im Schlaf (REST) spielt die Choreografie nichts.
    const bool knock = sensors.getVibration().knock;
    if (knock && !knockSeen && mode != OM_REST) react("startle");
    knockSeen = knock;

    ChoreoOut out;
    choreo.update(millis(), mode, faceResult.detected, faceResult.gaze_x, out);
    servos.setPos(CH_LEFT_EAR, out.earL);
    servos.setPos(CH_RIGHT_EAR, out.earR);
    servos.setPos(CH_LEFT_WING, out.wingL);
    servos.setPos(CH_RIGHT_WING, out.wingR);
    if (out.headValid) servos.setPos(CH_HEAD, out.head);
#endif
}

}  // namespace motion
