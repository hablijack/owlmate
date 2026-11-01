#include "motion.h"

#include <Arduino.h>
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
#endif

}  // namespace

namespace motion {

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
