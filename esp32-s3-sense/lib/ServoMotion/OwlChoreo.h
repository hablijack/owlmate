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
};
