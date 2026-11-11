#pragma once

#include <stdint.h>

// Wann duerfen die in DIESEM Lauf eingedrehten Hartmagnet-Offsets die
// gespeicherten ersetzen (und ins Flash)? Reine Funktion, damit sie nativ
// testbar ist: esp32-s3-sense/tests/magcal/test.cpp.
//
// Bis 2026-11-10 galt "ab 2 Achsen mit MAG_CAL_MIN_SPAN_UT", auch wenn gute
// Offsets aus dem Flash da waren. Gemessen 2026-11-10: die Eule von 163 auf
// 244 Grad umgestellt (eine Vierteldrehung plus Anheben) - das reichte fuer
// 3 Achsen ueber 25 uT, die halbe Min/Max-Kalibrierung ersetzte die gute aus
// dem Flash, und der Kurs lag 39 Grad daneben. Jedes Herumtragen zerstoerte
// also die Kalibrierung.
//
// Jetzt:
//  * Ohne gespeicherte Offsets bleibt es bei >= 2 Achsen ueber minSpan -
//    eine waagerechte Drehung ist besser als gar nichts.
//  * MIT gespeicherten Offsets ersetzt ein Lauf sie erst, wenn ALLE DREI
//    Achsen replaceSpan erreicht haben, d.h. die Eule wirklich in alle
//    Richtungen gekippt wurde. Eine Vierteldrehung kommt nicht hin: das
//    Erdfeld (~49 uT) gibt pro Achse hoechstens ~98 uT Spanne her, eine
//    waagerechte Drehung auf den waagerechten Achsen ~40 uT.
static inline bool magLiveOffsetsWin(const float span[3], bool haveStored,
                                     float minSpan, float replaceSpan) {
    uint8_t enough = 0, full = 0;
    for (int i = 0; i < 3; i++) {
        if (span[i] >= minSpan) enough++;
        if (span[i] >= replaceSpan) full++;
    }
    if (!haveStored) return enough >= 2;
    return full == 3;
}

// Soll der aktuelle (gewinnende) Stand ERNEUT ins Flash? Ja, sobald eine Achse
// seit dem letzten Speichern um mindestens minGrowth gewachsen ist - aber
// hoechstens alle minIntervalMs, damit das Drehen das Flash nicht mit jeder
// Probe beschreibt.
//
// Warum: bis 2026-11-11 wurde nur gespeichert, wenn die ACHSENZAHL stieg.
// Damit landete der Stand im Moment der Schwelle im Flash, mitten im Drehen.
// Gemessen 2026-11-11: mit den Live-Offsets am Drehende lagen vier Stellungen
// auf +-16 Grad, nach einem Neustart mit den gespeicherten meldete die Eule
// gar keinen Kurs mehr (waagerechtes Feld < 5 uT) - die Mittelpunkte bei
// 60 uT Spanne liegen bis zu (98-60)/2 = 19 uT neben denen am Ende.
// Wachstum unter minGrowth geht verloren; das ist gewollt.
static inline bool magShouldResave(const float span[3], const float savedSpan[3],
                                   bool savedThisRun, uint32_t msSinceSave,
                                   float minGrowth, uint32_t minIntervalMs) {
    if (!savedThisRun) return true;
    if (msSinceSave < minIntervalMs) return false;
    for (int i = 0; i < 3; i++)
        if (span[i] >= savedSpan[i] + minGrowth) return true;
    return false;
}
