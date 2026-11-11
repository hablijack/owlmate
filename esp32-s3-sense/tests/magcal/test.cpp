// Native test for lib/OwlImu/MagCalRule.h: when may this run's hard-iron
// offsets replace the stored ones? Built and run by
// orangepi-brain/tests/test_mag_cal_rule.py.
#include <cstdio>
#include "MagCalRule.h"

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { printf("FAIL line %d: %s\n", __LINE__, #cond); failures++; } } while (0)

int main() {
    const float MIN = 25.0f, REPLACE = 60.0f;

    // No stored offsets: a level turn (two horizontal axes) is enough.
    const float level[3] = {40.0f, 40.0f, 3.0f};
    const float one[3] = {40.0f, 10.0f, 3.0f};
    CHECK(magLiveOffsetsWin(level, false, MIN, REPLACE));
    CHECK(!magLiveOffsetsWin(one, false, MIN, REPLACE));

    // Stored offsets: the 2026-11-10 case - a quarter turn plus lifting the
    // owl put all three axes over 25 uT. That must NOT replace them.
    const float carried[3] = {30.0f, 28.0f, 26.0f};
    CHECK(!magLiveOffsetsWin(carried, true, MIN, REPLACE));
    CHECK(!magLiveOffsetsWin(level, true, MIN, REPLACE));
    // Two axes full, the third short: still no.
    const float twoFull[3] = {80.0f, 75.0f, 59.9f};
    CHECK(!magLiveOffsetsWin(twoFull, true, MIN, REPLACE));
    // A real tumble: all three full.
    const float tumble[3] = {85.0f, 90.0f, 70.0f};
    CHECK(magLiveOffsetsWin(tumble, true, MIN, REPLACE));
    const float edge[3] = {60.0f, 60.0f, 60.0f};
    CHECK(magLiveOffsetsWin(edge, true, MIN, REPLACE));
    // Nothing seen.
    const float none[3] = {0.0f, 0.0f, 0.0f};
    CHECK(!magLiveOffsetsWin(none, false, MIN, REPLACE));
    CHECK(!magLiveOffsetsWin(none, true, MIN, REPLACE));

    // --- re-save while the run keeps growing --------------------------------
    const float saved[3] = {60.0f, 62.0f, 60.0f};
    const float grown[3] = {60.5f, 75.0f, 60.0f};
    const float barely[3] = {61.9f, 63.9f, 61.9f};
    CHECK(magShouldResave(grown, saved, false, 0, 2.0f, 2000));      // first save of the run
    CHECK(magShouldResave(grown, saved, true, 2000, 2.0f, 2000));    // the 2026-11-11 case
    CHECK(!magShouldResave(grown, saved, true, 1999, 2.0f, 2000));   // rate limit
    CHECK(!magShouldResave(barely, saved, true, 60000, 2.0f, 2000)); // no real growth
    CHECK(!magShouldResave(saved, saved, true, 60000, 2.0f, 2000));

    if (failures) { printf("%d check(s) failed\n", failures); return 1; }
    printf("all checks passed\n");
    return 0;
}
