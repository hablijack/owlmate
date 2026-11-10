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
