// Compile the REAL lib/ServoCal against a fake PCA9685 that records every write.
#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>
#include "ServoCal.h"

struct Write { uint8_t reg; std::vector<uint8_t> data; };

struct FakePca : ServoCalBus {
    uint8_t regs[256];
    std::vector<Write> writes;
    bool failReads = false, failWrites = false;
    FakePca() { reset(); }
    void reset() {
        memset(regs, 0, sizeof regs);
        regs[0x00] = 0x11;                       // MODE1 power-on: sleep + allcall
        regs[0xFE] = 0x1E;                       // PRE_SCALE default (200 Hz)
        for (int c = 0; c < 16; c++) regs[0x06 + 4 * c + 3] = 0x10;  // full off
    }
    // Wie der Chip: ohne Auto-Increment (MODE1 bit 5) liest/schreibt ein
    // Mehrbyte-Zugriff immer dasselbe Register.
    int step() const { return (regs[0x00] & 0x20) ? 1 : 0; }
    int multiByteWithoutAI = 0;
    bool read(uint8_t reg, uint8_t* b, uint8_t n) override {
        if (failReads) return false;
        if (n > 1 && !step()) ++multiByteWithoutAI;
        for (int i = 0; i < n; i++) b[i] = regs[reg + i * step()];
        return true;
    }
    bool write(uint8_t reg, const uint8_t* b, uint8_t n) override {
        if (failWrites) return false;
        writes.push_back({reg, std::vector<uint8_t>(b, b + n)});
        if (n > 1 && !step()) ++multiByteWithoutAI;
        const uint8_t mode1 = regs[0];
        for (int i = 0; i < n; i++) {
            const int r = reg + i * step();
            if (r == 0xFE && !(mode1 & 0x10)) continue;  // only in sleep
            regs[r] = b[i];
        }
        return true;
    }
    void delayMs(uint32_t) override {}
    int channelWrites(int ch) const {
        int n = 0;
        for (auto& w : writes) if (w.reg == 0x06 + 4 * ch) ++n;
        return n;
    }
    int anyChannelWrites() const {
        int n = 0;
        for (auto& w : writes) if (w.reg >= 0x06 && w.reg < 0x46) ++n;
        return n;
    }
    void setPulse(int ch, int ticks) {
        regs[0x06 + 4 * ch] = 0; regs[0x06 + 4 * ch + 1] = 0;
        regs[0x06 + 4 * ch + 2] = ticks & 0xFF; regs[0x06 + 4 * ch + 3] = ticks >> 8;
    }
    int pulse(int ch) { return regs[0x06 + 4 * ch + 2] | (regs[0x06 + 4 * ch + 3] << 8); }
};

static char out[512];
static uint32_t T = 1000;
static const uint16_t MASK = (1u << 12) | (1u << 13) | (1u << 10) | (1u << 9) | (1u << 8);

#define SEND(cal, s) do { T += 1000; (cal).handleLine(s, T, out, sizeof out); } while (0)
#define OKAY()       assert(strncmp(out, "OK", 2) == 0)
#define REFUSED()    assert(strncmp(out, "ABGELEHNT", 9) == 0 || strncmp(out, "FEHLER", 6) == 0 || strncmp(out, "UNBEKANNT", 9) == 0)

int main() {
    // --- Nichts passiert ohne ARM, Lesen schreibt nie ---
    {
        FakePca pca; ServoCal cal(pca, MASK);
        SEND(cal, "STATUS"); assert(strncmp(out, "STATUS", 6) == 0);
        SEND(cal, "HELP");
        const char* cmds[] = {"STEP 5", "STEP -5", "FIRST 1500", "YES", "LIMIT 1100 1900",
                              "ARM 3", "ARM 99", "ARM x", "ARM", "bogus", "", "STEP"};
        for (auto c : cmds) { SEND(cal, c); }
        assert(pca.writes.empty());
        SEND(cal, "STOP"); assert(pca.writes.empty());   // nichts scharf: nichts schreiben
    }
    // --- ARM: nur Konfiguration, nie ein Kanalausgang; Frequenz 50 Hz ---
    {
        FakePca pca; ServoCal cal(pca, MASK);
        SEND(cal, "ARM 10"); OKAY();
        assert(cal.armed() && cal.armedChannel() == 10);
        assert(pca.anyChannelWrites() == 0);
        assert(pca.regs[0xFE] == 121);
        assert(!(pca.regs[0] & 0x10));               // aufgewacht
        assert(pca.regs[0] & 0x20);                  // Auto-Increment
        // ARM auf anderen Kanal solange scharf: abgelehnt
        SEND(cal, "ARM 12"); REFUSED();
        // STEP auf inaktivem Kanal: kein erfundener Startwert
        size_t w = pca.writes.size();
        SEND(cal, "STEP 5"); REFUSED();
        assert(pca.writes.size() == w);
    }
    // --- Anderer Kanal aktiv: ARM verweigert, nichts geschrieben ---
    {
        FakePca pca; pca.setPulse(8, 300); ServoCal cal(pca, MASK);
        SEND(cal, "ARM 10"); REFUSED();
        assert(!cal.armed() && pca.writes.empty());
        pca.reset(); pca.regs[0x06 + 4 * 2 + 3] = 0;  // unbenutzter Kanal 2: full-off weg -> unklar
        SEND(cal, "ARM 10"); REFUSED();
        assert(!cal.armed() && pca.writes.empty());
    }
    // --- FIRST braucht YES; Huelle; Abbruch durch anderen Befehl; Timeout ---
    {
        FakePca pca; ServoCal cal(pca, MASK);
        SEND(cal, "ARM 10"); OKAY();
        SEND(cal, "FIRST 900"); REFUSED();
        SEND(cal, "FIRST 2100"); REFUSED();
        SEND(cal, "FIRST abc"); REFUSED();
        SEND(cal, "FIRST 1500"); assert(strncmp(out, "BESTAETIGEN", 11) == 0);
        assert(pca.anyChannelWrites() == 0);
        SEND(cal, "STATUS");                           // bricht ab
        SEND(cal, "YES"); REFUSED();
        assert(pca.anyChannelWrites() == 0);
        SEND(cal, "FIRST 1500");
        T += 20000; cal.handleLine("YES", T, out, sizeof out); REFUSED();   // abgelaufen
        assert(pca.anyChannelWrites() == 0);
        SEND(cal, "FIRST 1500"); SEND(cal, "YES"); OKAY();
        assert(pca.anyChannelWrites() == 1 && pca.channelWrites(10) == 1);
        assert(pca.pulse(10) == 307);                  // 1500 / 4.88
        SEND(cal, "FIRST 1500"); REFUSED();            // schon aktiv
        SEND(cal, "YES"); REFUSED();
    }
    // --- STEP: klein, vom Register aus, Huelle, LIMIT nur enger, Rate ---
    {
        FakePca pca; ServoCal cal(pca, MASK);
        SEND(cal, "ARM 9"); SEND(cal, "FIRST 1500"); SEND(cal, "YES"); OKAY();
        SEND(cal, "STEP 16"); REFUSED();
        SEND(cal, "STEP -16"); REFUSED();
        SEND(cal, "STEP 0"); REFUSED();
        SEND(cal, "STEP 1.5"); REFUSED();
        const int before = pca.pulse(9);
        SEND(cal, "STEP 10"); OKAY();
        assert(pca.pulse(9) == before + 2);            // 10 us = 2 Ticks
        SEND(cal, "STEP 1"); OKAY();
        assert(pca.pulse(9) == before + 3);            // mindestens 1 Tick
        SEND(cal, "STEP -15"); OKAY();
        assert(pca.pulse(9) == before + 3 - 3);
        // Rate: zwei STEP ohne Pause
        cal.handleLine("STEP 5", T + 1000, out, sizeof out); OKAY();
        cal.handleLine("STEP 5", T + 1010, out, sizeof out); REFUSED();
        T += 1000;
        // Register von aussen veraendert: STEP geht vom echten Wert aus
        pca.setPulse(9, 350);
        SEND(cal, "STEP 5"); OKAY(); assert(pca.pulse(9) == 351);
        // LIMIT: nur enger, nicht weiter, nicht um den Puls herum
        SEND(cal, "LIMIT 900 1900"); REFUSED();
        SEND(cal, "LIMIT 1100 2100"); REFUSED();
        SEND(cal, "LIMIT 1900 1100"); REFUSED();
        SEND(cal, "LIMIT 1100 1500"); REFUSED();       // Puls (~1713 us) ausserhalb
        SEND(cal, "LIMIT 1100 1750"); OKAY();
        SEND(cal, "LIMIT 1000 1750"); REFUSED();       // Aufweiten verboten
        const int p = pca.pulse(9);
        for (int i = 0; i < 20; i++) { SEND(cal, "STEP 15"); }
        assert(pca.pulse(9) <= 358 && pca.pulse(9) >= p);   // 1750 us = 358 Ticks
        SEND(cal, "STEP 15"); REFUSED();
        // Huelle unten
        pca.setPulse(9, 207);
        SEND(cal, "LIMIT 1000 1750"); REFUSED();
        SEND(cal, "STEP -15"); REFUSED();
        assert(pca.pulse(9) == 207);
    }
    // --- Nur der scharfe Kanal wird beschrieben ---
    {
        FakePca pca; ServoCal cal(pca, MASK);
        SEND(cal, "ARM 13"); SEND(cal, "FIRST 1400"); SEND(cal, "YES");
        for (int i = 0; i < 5; i++) SEND(cal, "STEP 7");
        for (int c = 0; c < 16; c++) if (c != 13) assert(pca.channelWrites(c) == 0);
        assert(pca.channelWrites(13) == 6);
        for (auto& w : pca.writes) assert(w.reg < 0x06 || w.reg == 0xFE || w.reg == 0x00 || w.reg == 0x06 + 4 * 13);
    }
    // --- STOP: full off, kein Zielwert, entwaffnet ---
    {
        FakePca pca; ServoCal cal(pca, MASK);
        SEND(cal, "ARM 12"); SEND(cal, "FIRST 1500"); SEND(cal, "YES");
        SEND(cal, "STOP"); OKAY();
        assert(!cal.armed());
        assert(pca.regs[0x06 + 4 * 12 + 3] & 0x10);
        const Write& last = pca.writes.back();
        assert(last.reg == 0x06 + 4 * 12 && last.data[3] == 0x10);
        size_t w = pca.writes.size();
        SEND(cal, "STEP 5"); REFUSED();
        SEND(cal, "FIRST 1500"); REFUSED();
        SEND(cal, "YES"); REFUSED();
        assert(pca.writes.size() == w);
    }
    // --- Watchdog: Stille -> full off, kein Zielwert ---
    {
        FakePca pca; ServoCal cal(pca, MASK);
        SEND(cal, "ARM 12"); SEND(cal, "FIRST 1500"); SEND(cal, "YES");
        cal.tick(T + 100000, out, sizeof out); assert(out[0] == 0 && cal.armed());
        cal.tick(T + 130000, out, sizeof out); assert(strstr(out, "WATCHDOG") && !cal.armed());
        assert(pca.regs[0x06 + 4 * 12 + 3] & 0x10);
        cal.tick(T + 260000, out, sizeof out); assert(out[0] == 0);
    }
    // --- Neustart des ESP: vorhandenen Puls uebernehmen, nicht ueberschreiben ---
    {
        FakePca pca; pca.regs[0xFE] = 121; pca.regs[0] = 0x21; pca.setPulse(10, 320);
        ServoCal cal(pca, MASK);
        SEND(cal, "ARM 10"); OKAY();
        assert(pca.writes.empty());                     // alles schon richtig: nur lesen
        SEND(cal, "STEP 5"); OKAY(); assert(pca.pulse(10) == 321);
        // Puls ausserhalb Huelle: ARM verweigert
        FakePca p2; p2.regs[0xFE] = 121; p2.regs[0] = 0x21; p2.setPulse(10, 500);
        ServoCal c2(p2, MASK); SEND(c2, "ARM 10"); REFUSED(); assert(p2.writes.empty());
        // Frequenz stimmt nicht, Kanal aktiv: Frequenz wird NICHT angefasst
        FakePca p3; p3.regs[0] = 0x21; p3.regs[0xFE] = 30; p3.setPulse(10, 300);
        ServoCal c3(p3, MASK); SEND(c3, "ARM 10"); REFUSED();
        assert(p3.writes.empty() && p3.regs[0xFE] == 30);
        // full-on Kanal
        FakePca p4; p4.regs[0x06 + 4 * 10 + 1] = 0x10; p4.regs[0x06 + 4 * 10 + 3] = 0;
        ServoCal c4(p4, MASK); SEND(c4, "ARM 10"); REFUSED(); assert(p4.writes.empty());
    }
    // --- Busfehler: entwaffnen, nicht wiederholen ---
    {
        FakePca pca; ServoCal cal(pca, MASK);
        SEND(cal, "ARM 10"); SEND(cal, "FIRST 1500"); SEND(cal, "YES");
        pca.failWrites = true;
        const size_t w = pca.writes.size();
        SEND(cal, "STEP 5"); assert(strncmp(out, "FEHLER", 6) == 0);
        assert(!cal.armed() && pca.writes.size() == w);
        pca.failWrites = false;
        SEND(cal, "STEP 5"); REFUSED();                 // erst neu ARM
        FakePca p2; ServoCal c2(p2, MASK); p2.failReads = true;
        SEND(c2, "ARM 10"); assert(strncmp(out, "FEHLER", 6) == 0);
        assert(!c2.armed() && p2.writes.empty());
        // Frequenz-Setup scheitert (PRE_SCALE wirkt nicht): entwaffnet
        FakePca p3; ServoCal c3(p3, MASK); p3.failWrites = true;
        SEND(c3, "ARM 10"); assert(strncmp(out, "FEHLER", 6) == 0 && !c3.armed());
    }
    // --- Gross-/Kleinschreibung ---
    {
        FakePca pca; ServoCal cal(pca, MASK);
        SEND(cal, "arm 10"); OKAY();
        SEND(cal, "first 1500"); SEND(cal, "yes"); OKAY();
        SEND(cal, "stop"); OKAY();
    }
    // --- Kein Mehrbyte-Zugriff, solange Auto-Increment aus ist (Power-on-Zustand) ---
    {
        FakePca pca; ServoCal cal(pca, MASK);
        SEND(cal, "STATUS");
        SEND(cal, "ARM 8"); OKAY();
        assert(pca.multiByteWithoutAI == 0);
        SEND(cal, "FIRST 1500"); SEND(cal, "YES"); OKAY();
        SEND(cal, "STEP 5"); OKAY();
        assert(pca.multiByteWithoutAI == 0);
        assert(pca.pulse(8) == 307 + 1);
    }
    // --- Erweiterte untere Grenze nur fuer den freigegebenen Kanal ---
    {
        FakePca pca; ServoCal cal(pca, MASK, 1u << 9);
        SEND(cal, "ARM 9"); OKAY();
        SEND(cal, "FIRST 480"); REFUSED();              // unter ~498 us: nie
        SEND(cal, "FIRST 520"); SEND(cal, "YES"); OKAY();
        assert(pca.pulse(9) == 107);                    // 520 / 4.88
        for (int i = 0; i < 4; i++) SEND(cal, "STEP -15");
        assert(pca.pulse(9) >= 102);
        for (int i = 0; i < 4; i++) SEND(cal, "STEP -15");
        assert(pca.pulse(9) >= 102);                    // Grenze haelt
        SEND(cal, "STOP");
        // Ein anderer Kanal bleibt bei 1000 us
        SEND(cal, "ARM 8"); OKAY();
        SEND(cal, "FIRST 900"); REFUSED();
        SEND(cal, "FIRST 1000"); SEND(cal, "YES"); OKAY();
        SEND(cal, "STEP -15"); REFUSED();
        // Vorhandener Puls zwischen 500 und 1000 us: nur auf dem freigegebenen Kanal adoptieren
        FakePca p2; p2.regs[0xFE] = 121; p2.regs[0] = 0x21; p2.setPulse(9, 150);
        ServoCal c2(p2, MASK, 1u << 9); SEND(c2, "ARM 9"); OKAY();
        FakePca p3; p3.regs[0xFE] = 121; p3.regs[0] = 0x21; p3.setPulse(8, 150);
        ServoCal c3(p3, MASK, 1u << 9); SEND(c3, "ARM 8"); REFUSED();
        // Maske wirkt nur auf erlaubte Kanaele
        FakePca p4; ServoCal c4(p4, MASK, 1u << 3);
        SEND(c4, "ARM 3"); REFUSED();
    }
    // --- Erweiterte obere Grenze nur fuer den freigegebenen Kanal ---
    {
        FakePca pca; ServoCal cal(pca, MASK, 0, 1u << 12);
        SEND(cal, "ARM 12"); OKAY();
        SEND(cal, "FIRST 2600"); REFUSED();
        SEND(cal, "FIRST 2000"); SEND(cal, "YES"); OKAY();
        for (int i = 0; i < 30; i++) SEND(cal, "STEP 15");
        assert(pca.pulse(12) <= 512);                   // Grenze haelt (~2500 us)
        assert(pca.pulse(12) > 410);                    // und ging ueber 2000 hinaus
        SEND(cal, "STOP");
        SEND(cal, "ARM 8"); OKAY();
        SEND(cal, "FIRST 2000"); SEND(cal, "YES"); OKAY();
        SEND(cal, "STEP 15"); REFUSED();                // andere Kanaele bleiben bei 2000 us
        FakePca p2; p2.regs[0xFE] = 121; p2.regs[0] = 0x21; p2.setPulse(8, 450);
        ServoCal c2(p2, MASK, 0, 1u << 12); SEND(c2, "ARM 8"); REFUSED();
    }
    puts("servocal: all checks passed");
    return 0;
}
