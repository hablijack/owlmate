#include "GpsDiag.h"
#include <string.h>
#include <stdlib.h>

static int hexVal(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

bool nmeaChecksumOk(const char *line) {
    if (!line || line[0] != '$') return false;
    const char *star = strrchr(line, '*');
    if (!star) return false;
    int hi = hexVal(star[1]);
    int lo = hexVal(hi < 0 ? 0 : star[2]);
    if (hi < 0 || lo < 0) return false;
    uint8_t sum = 0;
    for (const char *p = line + 1; p < star; p++) sum ^= (uint8_t)*p;
    return sum == (uint8_t)(hi * 16 + lo);
}

// Feld i (0-basiert, Feld 0 = "$GPGSV") als Zahl; leeres Feld -> -1.
// end zeigt auf '*' (oder Stringende).
static const char *fieldStart(const char *line, const char *end, int i) {
    const char *p = line;
    for (int n = 0; n < i; n++) {
        p = (const char *)memchr(p, ',', end - p);
        if (!p) return nullptr;
        p++;
    }
    return p;
}

static int fieldInt(const char *f, const char *end) {
    if (!f || f >= end || *f == ',' || *f == '*') return -1;
    if (*f < '0' || *f > '9') return -1;
    return atoi(f);
}

bool parseGsv(const char *line, GsvSentence &out) {
    // "$GPGSV,": Talker an Stelle 1-2, "GSV" an 3-5.
    if (!line || line[0] != '$' || strlen(line) < 7) return false;
    if (strncmp(line + 3, "GSV,", 4) != 0) return false;
    const char *end = strchr(line, '*');
    if (!end) end = line + strlen(line);

    int count = fieldInt(fieldStart(line, end, 1), end);
    int num = fieldInt(fieldStart(line, end, 2), end);
    int view = fieldInt(fieldStart(line, end, 3), end);
    if (count < 1 || num < 1 || num > count || view < 0) return false;

    out.talker[0] = line[1];
    out.talker[1] = line[2];
    out.talker[2] = 0;
    out.msgCount = (uint8_t)count;
    out.msgNum = (uint8_t)num;
    out.inView = (uint8_t)(view > 255 ? 255 : view);
    out.maxSnr = 0;
    out.tracked = 0;

    // Bis zu vier Bloecke (PRN, Elevation, Azimut, SNR) ab Feld 4. NMEA 4.1
    // haengt danach noch eine Signal-ID an - daher nur VOLLSTAENDIGE Bloecke,
    // die mit einer PRN beginnen.
    for (int b = 0; b < 4; b++) {
        int base = 4 + 4 * b;
        const char *prnF = fieldStart(line, end, base);
        const char *snrF = fieldStart(line, end, base + 3);
        if (!prnF || !snrF || snrF > end) break;
        if (fieldInt(prnF, end) < 0) break;
        int snr = fieldInt(snrF, end);
        if (snr > 0) {
            out.tracked++;
            if (snr > out.maxSnr) out.maxSnr = (uint8_t)(snr > 99 ? 99 : snr);
        }
    }
    return true;
}

bool GsvTracker::fresh(const Slot &s, uint32_t nowMs) const {
    return s.used && s.valid && (uint32_t)(nowMs - s.stamp) <= _staleMs;
}

void GsvTracker::feed(const GsvSentence &g, uint32_t nowMs) {
    Slot *slot = nullptr;
    for (int i = 0; i < SLOTS; i++) {
        if (_slots[i].used && strcmp(_slots[i].talker, g.talker) == 0) { slot = &_slots[i]; break; }
    }
    if (!slot) {
        for (int i = 0; i < SLOTS; i++) {
            if (!_slots[i].used) {
                slot = &_slots[i];
                *slot = Slot{};
                slot->used = true;
                memcpy(slot->talker, g.talker, 3);
                slot->nextMsg = 1;
                break;
            }
        }
    }
    if (!slot) return;   // mehr Talker als Plaetze: ignorieren statt abstuerzen

    // Ein Zyklus zaehlt nur, wenn alle Saetze 1..count lueckenlos ankamen -
    // ein verlorener Satz (Puffer uebergelaufen) darf kein zu kleines
    // "in Sicht" als Messung ausgeben.
    if (g.msgNum == 1) {
        slot->curTracked = 0;
        slot->curMaxSnr = 0;
        slot->nextMsg = 1;
    }
    if (g.msgNum != slot->nextMsg) {
        slot->nextMsg = 0;   // Luecke: bis zum naechsten Satz 1 verwerfen
        return;
    }
    slot->curTracked += g.tracked;
    if (g.maxSnr > slot->curMaxSnr) slot->curMaxSnr = g.maxSnr;
    slot->nextMsg++;
    if (g.msgNum == g.msgCount) {
        slot->inView = g.inView;
        slot->tracked = slot->curTracked;
        slot->maxSnr = slot->curMaxSnr;
        slot->stamp = nowMs;
        slot->valid = true;
        slot->nextMsg = 1;
    }
}

uint8_t GsvTracker::inView(uint32_t nowMs) const {
    int n = 0;
    for (const Slot &s : _slots) if (fresh(s, nowMs)) n += s.inView;
    return (uint8_t)(n > 255 ? 255 : n);
}

uint8_t GsvTracker::tracked(uint32_t nowMs) const {
    int n = 0;
    for (const Slot &s : _slots) if (fresh(s, nowMs)) n += s.tracked;
    return (uint8_t)(n > 255 ? 255 : n);
}

uint8_t GsvTracker::maxSnr(uint32_t nowMs) const {
    uint8_t m = 0;
    for (const Slot &s : _slots) if (fresh(s, nowMs) && s.maxSnr > m) m = s.maxSnr;
    return m;
}
