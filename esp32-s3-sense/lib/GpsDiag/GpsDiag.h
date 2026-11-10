#pragma once
// ============================================================================
// GpsDiag - was die GPS sieht, BEVOR sie einen Fix hat.
//
// Adafruit_GPS wertet GSV nicht aus (parse() gibt dafuer schlicht false zurueck),
// und GGA meldet nur Satelliten IM FIX - ohne Fix also immer 0. Damit sehen
// "es kommt gar nichts an", "sieht keinen Satelliten" und "sieht drei, braucht
// vier" in der Telemetrie identisch aus. Diese Bibliothek schliesst die Luecke:
// Pruefsumme jeder NMEA-Zeile, und aus den GSV-Saetzen die Zahl der Satelliten
// in Sicht und das beste Signal-Rausch-Verhaeltnis (dB-Hz).
//
// Bewusst ohne Arduino-Abhaengigkeit: wird nativ getestet
// (tests/gpsdiag/test.cpp, ausgefuehrt von orangepi-brain/tests/test_gps_diag.py).
// ============================================================================
#include <stdint.h>

// Pruefsumme einer NMEA-Zeile ("$...*HH", optional mit \r\n dahinter).
bool nmeaChecksumOk(const char *line);

// Ein einzelner GSV-Satz, zerlegt.
struct GsvSentence {
    char talker[3];     // "GP", "GL", "GA", "GB", "BD", "GN", ...
    uint8_t msgCount;   // Saetze in diesem Zyklus
    uint8_t msgNum;     // dieser Satz, 1-basiert
    uint8_t inView;     // Satelliten in Sicht (fuer diesen Talker)
    uint8_t maxSnr;     // bestes SNR in DIESEM Satz, 0 = keiner wird verfolgt
    uint8_t tracked;    // Satelliten in diesem Satz MIT SNR (also verfolgt)
};

// true, wenn line ein syntaktisch gueltiger GSV-Satz ist. Prueft die Pruefsumme
// NICHT - dafuer nmeaChecksumOk() vorher aufrufen.
bool parseGsv(const char *line, GsvSentence &out);

// Sammelt GSV-Zyklen pro Talker und liefert die Summe ueber alle Talker.
// Ein Talker, dessen letzter Zyklus aelter als staleMs ist, zaehlt nicht mehr.
class GsvTracker {
public:
    explicit GsvTracker(uint32_t staleMs) : _staleMs(staleMs) {}
    void feed(const GsvSentence &s, uint32_t nowMs);
    uint8_t inView(uint32_t nowMs) const;    // Summe ueber alle frischen Talker
    uint8_t tracked(uint32_t nowMs) const;   // davon mit SNR
    uint8_t maxSnr(uint32_t nowMs) const;    // bestes SNR, dB-Hz

private:
    static const int SLOTS = 6;
    struct Slot {
        char talker[3];
        uint8_t inView, tracked, maxSnr;           // letzter VOLLSTAENDIGER Zyklus
        uint8_t curTracked, curMaxSnr, nextMsg;    // laufender Zyklus
        uint32_t stamp;
        bool used, valid;
    };
    Slot _slots[SLOTS] = {};
    uint32_t _staleMs;
    bool fresh(const Slot &s, uint32_t nowMs) const;
};
