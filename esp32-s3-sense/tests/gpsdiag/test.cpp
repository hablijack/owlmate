// Native test for lib/GpsDiag: NMEA checksum, GSV parsing, per-talker cycles.
// Built and run by orangepi-brain/tests/test_gps_diag.py.
#include <cassert>
#include <cstdio>
#include <cstring>
#include "GpsDiag.h"

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { printf("FAIL line %d: %s\n", __LINE__, #cond); failures++; } } while (0)

// Real sentences, checksums computed, not invented.
static const char *GP1 = "$GPGSV,3,1,11,07,79,048,42,02,51,062,43,26,36,256,42,27,27,138,42*71";
static const char *GP2 = "$GPGSV,3,2,11,09,23,313,42,04,19,159,41,15,12,041,42,30,10,180,*48";
static const char *GP3 = "$GPGSV,3,3,11,16,09,066,,05,08,305,,29,02,256,*49";
static const char *GL1 = "$GLGSV,1,1,03,65,40,100,30,66,20,200,,67,10,300,25*6A";
static const char *EMPTY = "$GPGSV,1,1,00*79";

static void withChecksum(const char *body, char (&out)[200]) {
    // body without '$' and '*'; produce "$body*HH"
    unsigned char sum = 0;
    for (const char *p = body; *p; p++) sum ^= (unsigned char)*p;
    snprintf(out, sizeof(out), "$%s*%02X", body, sum);
}

int main() {
    char buf[200];
    // Make the fixtures self-consistent rather than trusting hand-typed checksums.
    static char gp1[200], gp2[200], gp3[200], gl1[200], empty[200];
    const char *src[] = {GP1, GP2, GP3, GL1, EMPTY};
    char (*dst[])[200] = {&gp1, &gp2, &gp3, &gl1, &empty};
    for (int i = 0; i < 5; i++) {
        char body[200];
        strcpy(body, src[i] + 1);
        *strrchr(body, '*') = 0;
        withChecksum(body, *dst[i]);
    }

    // --- checksum -----------------------------------------------------------
    CHECK(nmeaChecksumOk("$PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0*28"));   // library constant
    CHECK(nmeaChecksumOk("$PMTK314,0,1,0,1,0,5,0,0,0,0,0,0,0,0,0,0,0,0,0*2D"));   // what Sensors.cpp sends
    CHECK(nmeaChecksumOk("$PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0*28\r"));
    CHECK(!nmeaChecksumOk("$PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0*29"));
    CHECK(!nmeaChecksumOk("$PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1*28"));   // corrupted payload
    CHECK(!nmeaChecksumOk("PMTK314*28"));
    CHECK(!nmeaChecksumOk("$GPGSV,1,1,00"));
    CHECK(!nmeaChecksumOk("$GPGSV,1,1,00*7"));
    CHECK(!nmeaChecksumOk(""));
    CHECK(!nmeaChecksumOk(nullptr));
    CHECK(nmeaChecksumOk(gp1) && nmeaChecksumOk(empty));

    // --- single sentences ---------------------------------------------------
    GsvSentence s;
    CHECK(parseGsv(gp1, s));
    CHECK(strcmp(s.talker, "GP") == 0 && s.msgCount == 3 && s.msgNum == 1 && s.inView == 11);
    CHECK(s.tracked == 4 && s.maxSnr == 43);
    CHECK(parseGsv(gp2, s) && s.tracked == 3 && s.maxSnr == 42);   // last SNR empty
    CHECK(parseGsv(gp3, s) && s.tracked == 0 && s.maxSnr == 0);    // three untracked
    CHECK(parseGsv(empty, s) && s.inView == 0 && s.tracked == 0);
    CHECK(parseGsv(gl1, s) && strcmp(s.talker, "GL") == 0 && s.tracked == 2 && s.maxSnr == 30);
    // NMEA 4.1 signal ID after the blocks must not be read as a fifth satellite
    withChecksum("GPGSV,1,1,01,07,79,048,42,1", buf);
    CHECK(parseGsv(buf, s) && s.tracked == 1 && s.maxSnr == 42);
    // SNR "00" is a satellite in the almanac that is not being tracked
    withChecksum("GPGSV,1,1,02,07,79,048,00,08,10,100,", buf);
    CHECK(parseGsv(buf, s) && s.inView == 2 && s.tracked == 0 && s.maxSnr == 0);
    CHECK(!parseGsv("$GPGGA,123519,4807.038,N*47", s));
    CHECK(!parseGsv("$GPGSV,0,1,00*79", s));     // zero messages
    CHECK(!parseGsv("$GPGSV,1,2,00*79", s));     // msgNum > count
    CHECK(!parseGsv("$GPGSV,,,*79", s));
    CHECK(!parseGsv("$GPG", s));

    // --- tracker: complete cycles, sum over talkers, gaps, staleness ---------
    GsvTracker t(15000);
    CHECK(t.inView(0) == 0 && t.maxSnr(0) == 0);
    GsvSentence a, b, c, g;
    parseGsv(gp1, a); parseGsv(gp2, b); parseGsv(gp3, c); parseGsv(gl1, g);
    t.feed(a, 100); t.feed(b, 110);
    CHECK(t.inView(120) == 0);                 // cycle not complete yet: nothing reported
    t.feed(c, 120);
    CHECK(t.inView(130) == 11 && t.tracked(130) == 7 && t.maxSnr(130) == 43);
    t.feed(g, 140);
    CHECK(t.inView(150) == 14 && t.tracked(150) == 9 && t.maxSnr(150) == 43);

    // A lost middle sentence must not commit a cycle; the previous one stays.
    GsvTracker t2(15000);
    t2.feed(a, 0); t2.feed(c, 10);             // gp2 lost
    CHECK(t2.inView(20) == 0);
    t2.feed(c, 30);                            // ...and a repeated last sentence still is no cycle
    CHECK(t2.inView(40) == 0);
    GsvTracker t5(15000);                      // a cycle that starts mid-way never commits
    t5.feed(b, 0); t5.feed(c, 10);
    CHECK(t5.inView(20) == 0);
    t2.feed(a, 1000); t2.feed(b, 1010); t2.feed(c, 1020);
    CHECK(t2.inView(1030) == 11);
    t2.feed(a, 2000); t2.feed(c, 2010);        // gap again: keep last good cycle
    CHECK(t2.inView(2020) == 11);

    // Stale talkers drop out; a fresh empty cycle reports 0.
    CHECK(t.inView(140 + 15001) == 0);
    GsvSentence e; parseGsv(empty, e);
    GsvTracker t3(15000);
    t3.feed(e, 5);
    CHECK(t3.inView(6) == 0 && t3.maxSnr(6) == 0);

    // millis() wrap must not make a fresh cycle look stale.
    GsvTracker t4(15000);
    t4.feed(g, 0xFFFFFF00u);
    CHECK(t4.inView(0x00000100u) == 3);

    if (failures) { printf("%d check(s) failed\n", failures); return 1; }
    printf("all checks passed\n");
    return 0;
}
