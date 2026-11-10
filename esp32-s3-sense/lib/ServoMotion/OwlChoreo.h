#pragma once
// ============================================================================
// OwlChoreo - was Ohren, Kopf und Fluegel TUN, damit die Eule lebendig wirkt.
//
// Reine Logik ohne Hardware: bekommt den Verhaltenszustand, ob ein Gesicht da ist
// und wo, und liefert Zielpositionen in den Einheiten der ServoProfile (Ohren und
// Fluegel 0..100, Kopf in Grad, positiv = links). ServoMotion begrenzt danach
// Geschwindigkeit und Beschleunigung und schreibt nie ausserhalb der Kalibrierung;
// diese Klasse darf also grosszuegig denken, ist aber selbst auf plausible
// Bereiche begrenzt.
//
//   REST     Schlaf, Fehler, OTA, Boot: Ohren tief, Kopf geradeaus, Fluegel zu.
//   IDLE     wach ohne Gesicht: Ohren in der Mitte (50 %, tief = traurig); gelegentlich zuckt ein Ohr
//            (alle 6-14 s), gelegentlich schaut der Kopf kurz zur Seite
//            (alle 10-30 s, hoechstens glanceMaxDeg); ganz selten ein
//            Fluegel-Aufplustern (voller Hub) (alle 30-70 s).
//   ALERT    DETECTING: Ohren aufgestellt, Kopf bleibt wo er ist.
//   ENGAGED  INTERACTING: Kopf folgt dem Gesicht, Ohren oben; beim Eintritt kurz
//            Ohren hoch und zwei Fluegelschlaege bis zum Maximum der Kalibrierung ("ich habe dich gesehen").
//   NAV      Navigation: der Kopf gehoert der Navigation (headValid=false).
//
// Gesten (trigger()): kurze Koerperreaktionen, die ueber den Modus gelegt werden und nach ihrer
// Dauer von selbst enden; der Modus laeuft darunter weiter. Im REST (Schlaf, Fehler, OTA, Boot)
// wird nichts gespielt, der Kopf wird nie angefasst. Eine Geste wird hoechstens alle
// gestureCooldownMs neu gestartet, damit ein Klopf-Burst nicht dauernd zuckt.
//   STARTLE  Klopfen: Ohren schnellen auf 100, ein Fluegelschlag (0,7 s).
//   HAPPY    Lob: Ohren wippen im Wechsel, ein Fluegelschlag (1,5 s).
//   NEGATIVE Ablehnung: Ohren sinken, der Kopf schuettelt sich zweimal (1,3 s).
//   CURIOUS  Frage: Kopf kippt zu einer zufaelligen Seite, das Ohr dieser Seite geht hoch (1,8 s).
// Posen (triggerPose()): eine feste Koerperhaltung pro Stimmung (Ohren, Fluegel, Kopfneigung), die
// POSE_HOLD_MS gehalten wird, solange die Emotion gezeigt wird. Sie ueberlagert Modus und Geste wie
// eine Geste (nie im REST, Kopf nur mit headValid), ersetzt aber eine laufende Geste/Pose sofort und
// kennt keinen Cooldown: sie wird von Hand ausgewaehlt (Weboberflaeche), nicht von Sensoren ausgeloest.
// Nur NEGATIVE und CURIOUS bewegen den Kopf, als Versatz auf das Ziel von Tracking/Blick (nie in NAV).
// Manuell (manual()): EIN Teil auf einen festen Wert, MANUAL_HOLD_MS lang, ueber allem anderen
// (Modus, Geste, Pose) - die Knoepfe "Manuelle Bewegung" der Weboberflaeche. Nie im REST, der Kopf
// nie in NAV; der Kopf ABSOLUT (nicht als Versatz), begrenzt auf trackLimitDeg. Danach uebernimmt
// die Choreografie wieder. Seit 2026-11-10; davor lehnte die Firmware solche Befehle ab
// (servo_autonomous), und die Knoepfe taten im Hauptimage nichts.
//
// Kopfverfolgung als Sakkade: Das Kamerabild dreht sich mit dem Kopf, also ist gaze_x ein
// Regelfehler. Ueberschreitet er das Totband, setzt der Kopf EIN neues Ziel (sign * FOV *
// Gain * gaze_x) und faehrt es glatt an; danach wird SETTLE_MS plus Fahrzeit nicht neu gezielt
// (das Kamerabild ist dann noch alt). Ein dt-Integrator fuehlte sich wie Stoesse an. Das
// Vorzeichen ist NICHT hergeleitet (Bildspiegelung faellt beim Test mit
// vflip nicht auf, siehe EYE_GAZE_SIGN_X): ein falsches Vorzeichen waere
// Mitkopplung, der Kopf liefe an die Grenze. Dagegen der Rutschschutz: haengt das
// Ziel am Limit, waehrend das Gesicht weiter weit aussen steht (>3 s), wird das
// Tracking 30 s abgeschaltet und der Kopf zentriert.
// ============================================================================
#include <stdint.h>

enum OwlMode { OM_REST, OM_IDLE, OM_ALERT, OM_ENGAGED, OM_NAV };
enum OwlGesture { OG_NONE, OG_STARTLE, OG_HAPPY, OG_NEGATIVE, OG_CURIOUS };
enum OwlPart { OP_EAR_L, OP_EAR_R, OP_WING_L, OP_WING_R, OP_HEAD, OP_COUNT };

// Eine feste Koerperhaltung. Ohren/Fluegel 0..100, Kopf in Grad als Versatz auf das Ziel von
// Tracking/Blick (positiv = links).
struct OwlPose {
    float earL, earR, wingL, wingR;
    float headDeg;
    uint8_t flaps;   // so oft schlagen beide Fluegel zu Beginn der Pose runter und wieder hoch (0 = nie)
};

struct ChoreoConfig {
    float headSign;          // +1/-1: Drehrichtung der Kopfverfolgung
    float trackDegPerSec;    // erwartete Kopfgeschwindigkeit einer Sakkade (nur fuer die Sperrzeit)
    float trackDeadband;     // |gaze_x| darunter: keine Bewegung
    float trackLimitDeg;     // Kopf-Trackinggrenze (symmetrisch)
    float glanceMaxDeg;      // Idle-Blick zur Seite, maximal
    float trackFovDeg;       // geschaetzter halber Kamerasichtwinkel: gaze_x = 1 entspricht so vielen Grad
    float saccadeGain;       // Anteil des gemessenen Versatzes, den eine Sakkade ausfuehrt (<1: lieber zu wenig)
    float saccadeSettleMs;   // Pause nach einer Sakkade, bis das Kamerabild die neue Lage zeigt
};

struct ChoreoOut {
    float earL, earR, wingL, wingR;   // 0..100
    float head;                        // Grad
    bool headValid;                    // false: Kopf nicht anfassen (Navigation)
};

class OwlChoreo {
public:
    typedef uint32_t (*Rng)();
    OwlChoreo(const ChoreoConfig& cfg, Rng rng);
    void update(uint32_t nowMs, OwlMode mode, bool face, float gazeX, ChoreoOut& out);
    // Startet eine Geste (ignoriert, wenn gerade eine laeuft oder der Cooldown nicht um ist).
    // Gibt zurueck, ob sie angenommen wurde.
    // force=true ueberspringt Cooldown und laufende Geste (Aufruf von Hand/Weboberflaeche).
    bool trigger(OwlGesture g, uint32_t nowMs, bool force = false);
    // Haelt `p` fuer POSE_HOLD_MS; ersetzt jede laufende Geste/Pose. Immer angenommen.
    void triggerPose(const OwlPose& p, uint32_t nowMs);
    bool poseActive() const { return _poseUntil != 0; }
    // Haelt einen Teil MANUAL_HOLD_MS auf `value` (Ohren/Fluegel 0..100, Kopf Grad, positiv = links).
    void manual(OwlPart part, float value, uint32_t nowMs);
    bool manualActive(OwlPart part) const { return part < OP_COUNT && _manualUntil[part] != 0; }
    OwlGesture gesture() const { return _gesture; }
    bool trackingSuspended(uint32_t nowMs) const { return nowMs < _trackOffUntil; }

private:
    float rnd01();
    ChoreoConfig _cfg;
    Rng _rng;
    bool _init;
    uint32_t _saccadeLockUntil;
    OwlMode _mode;
    uint32_t _last, _modeSince;
    float _headTarget;
    uint32_t _nextTwitch, _twitchUntil;
    uint8_t _twitchEar;
    uint32_t _nextGlance, _glanceUntil;
    float _glanceTarget;
    uint32_t _stuckSince, _trackOffUntil;
    uint32_t _nextRuffle, _ruffleStart;
    float _gxFilt;
    OwlGesture _gesture;
    OwlPose _pose;
    uint32_t _poseUntil, _poseStart;
    uint32_t _gestureStart, _gestureReadyAt;
    float _gestureSide, _headOffset;
    float _manual[OP_COUNT];
    uint32_t _manualUntil[OP_COUNT];
};
