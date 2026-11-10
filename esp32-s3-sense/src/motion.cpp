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

#if SERVO_MOTION_ENABLED
namespace {
// Jede Stimmung bringt ihre Augen UND ihren Koerper mit: entweder eine Geste (bewegte Abfolge,
// nur 4 Stueck) oder eine gehaltene Pose (Ohren, Fluegel, Kopfneigung). Die Posen sind die
// Wuensche des Besitzers aus der Emotionen-Durchsicht (2026-11-03, specs/004): Ohren 0 = tief,
// 100 = ganz oben; Fluegel 100 = ganz auf; Kopf positiv = links. skeptic: das groessere Auge
// (links, SHAPES[].asymH kuerzt nur das rechte) bekommt das aufgestellte Ohr.
struct Mood {
    const char* name;
    EyeExpression eyes;
    OwlGesture gesture;   // OG_NONE: Pose
    OwlPose pose;
};
const Mood MOODS[] = {
    // Alte Gestennamen (Sprachreaktionen im Brain): Augen wie bisher.
    {"startle",     EyeExpression::SURPRISED,   OG_STARTLE,  {0, 0, 0, 0, 0}},
    {"negative",    EyeExpression::ANNOYED,     OG_NEGATIVE, {0, 0, 0, 0, 0}},
    {"curious",     EyeExpression::SKEPTIC,     OG_CURIOUS,  {0, 0, 0, 0, 0}},
    // Stimmungen
    {"neutral",     EyeExpression::NEUTRAL,     OG_NONE,     {50, 50, 0, 0, 0}},
    {"happy",       EyeExpression::HAPPY,       OG_HAPPY,    {0, 0, 0, 0, 0}},
    {"glee",        EyeExpression::GLEE,        OG_NONE,     {100, 100, 100, 100, 0}},
    {"squint",      EyeExpression::SQUINT,      OG_NONE,     {65, 65, 0, 0, 0}},
    {"sad_down",    EyeExpression::SAD_DOWN,    OG_NONE,     {0, 0, 0, 0, 0}},
    {"focused",     EyeExpression::FOCUSED,     OG_NONE,     {100, 100, 0, 0, 0}},
    {"sleepy",      EyeExpression::SLEEPY,      OG_NONE,     {0, 0, 0, 0, 0}},
    {"unimpressed", EyeExpression::UNIMPRESSED, OG_NONE,     {0, 0, 0, 0, 0}},
    {"skeptic",     EyeExpression::SKEPTIC,     OG_NONE,     {100, 15, 0, 0, 15}},
    {"suspicious",  EyeExpression::SUSPICIOUS,  OG_CURIOUS,  {0, 0, 0, 0, 0}},
    {"surprised",   EyeExpression::SURPRISED,   OG_STARTLE,  {0, 0, 0, 0, 0}},
    {"awe",         EyeExpression::AWE,         OG_NONE,     {100, 100, 55, 55, 0}},
    {"annoyed",     EyeExpression::ANNOYED,     OG_NEGATIVE, {0, 0, 0, 0, 0}},
    {"angry",       EyeExpression::ANGRY,       OG_NONE,     {100, 100, 100, 100, 0}},
    {"furious",     EyeExpression::FURIOUS,     OG_NONE,     {100, 100, 100, 100, 0, 3}},
    {"scared",      EyeExpression::SCARED,      OG_NONE,     {0, 0, 100, 100, 0}},
};
}  // namespace
#endif

bool react(const char* name, bool force) {
#if SERVO_MOTION_ENABLED
    if (!name || servos.locked() || !servos.ready()) return false;
    switch (behavior::current()) {
        case State::BOOT: case State::SLEEPING: case State::UPDATE: case State::ERROR: return false;
        default: break;
    }
    for (const Mood& m : MOODS) {
        if (strcmp(name, m.name) != 0) continue;
        if (m.gesture != OG_NONE) {
            if (!choreo.trigger(m.gesture, millis(), force)) return false;
        } else {
            choreo.triggerPose(m.pose, millis());
        }
        // Koerper und Gesicht gehoeren zusammen, sonst wirkt es leer.
        behavior::overrideExpression(m.eyes);
        return true;
    }
    return false;
#else
    (void)name; (void)force;
    return false;
#endif
}

bool manual(uint8_t channel, float value) {
#if SERVO_MOTION_ENABLED
    if (servos.locked() || !servos.ready()) return false;
    switch (behavior::current()) {
        case State::BOOT: case State::SLEEPING: case State::UPDATE: case State::ERROR: return false;
        default: break;
    }
    OwlPart part;
    switch (channel) {
        case CH_LEFT_EAR: part = OP_EAR_L; break;
        case CH_RIGHT_EAR: part = OP_EAR_R; break;
        case CH_LEFT_WING: part = OP_WING_L; break;
        case CH_RIGHT_WING: part = OP_WING_R; break;
        case CH_HEAD:
            if (behavior::current() == State::NAVIGATING) return false;   // der Kopf gehoert der Navigation
            part = OP_HEAD;
            break;
        default: return false;
    }
    choreo.manual(part, value, millis());
    return true;
#else
    // Ohne Bewegungs-Image der alte Weg; im gesperrten Image lehnt der Treiber ab.
    return servos.setAngle(channel, value);
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
    if (knock && !knockSeen && mode != OM_REST) react("startle", false);
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
