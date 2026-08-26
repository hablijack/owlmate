// ============================================================================
// SW-420 Vibrationssensor-Diagnose (env: vibtest, -DVIBTEST_ACTIVE)
//
// Die Firmware meldete "detected: true, count: 1" bei stillstehender Eule --
// das Muster eines Pins, der seit dem Boot dauerhaft LOW ist. Drei Ursachen
// sind moeglich und dieses Sketch unterscheidet sie:
//
//   1. Pin gar nicht angeschlossen / Modulausgang hochohmig
//        -> mit PULLUP liest er HIGH, mit PULLDOWN liest er LOW
//           (der Pin folgt einfach dem eingeschalteten Widerstand)
//   2. Modul zieht den Pin aktiv auf LOW (Ruhepegel oder Poti zu empfindlich)
//        -> beide Messungen liefern LOW
//   3. Modul treibt HIGH
//        -> beide Messungen liefern HIGH
//
// Dazu die ANALOGE Spannung am Pin (GPIO3 = ADC1_CH2): ein sauberes 0 V oder
// 3.3 V heisst "aktiv getrieben", irgendwas in der Mitte heisst floatend oder
// ein halb durchgesteuerter Komparator.
//
// Danach laeuft ein Live-Stream mit Flankenzaehler: antippen und am Poti
// drehen, dann sieht man sofort, ob und wie der Ausgang schaltet.
// ============================================================================
#if defined(VIBTEST_ACTIVE)

#include <Arduino.h>
#include "config.h"

static const int PIN = VIBRATION_PIN;

// Alle 11 Signalpins des XIAO-Headers, mit ihrer Silkscreen-Bezeichnung.
// GPIO 4/5/7/8/9/43/44 gehen normalerweise an die Displays, 1/2 an den I2C-Bus
// - hier laufen die aber nicht, also darf alles als Eingang abgehorcht werden.
static const int SCAN[] = { 1, 2, 3, 4, 5, 6, 7, 8, 9, 43, 44 };
static const char* const SCAN_LBL[] = { "D0", "D1", "D2", "D3", "D4", "D5",
                                        "D8", "D9", "D10", "D6", "D7" };
static const size_t NPINS = sizeof(SCAN) / sizeof(SCAN[0]);
static int lastLvl[NPINS];
static uint32_t edgeCnt[NPINS];

static int readWith(int mode, const char* label) {
    pinMode(PIN, mode);
    delay(60);                       // Pull wirken lassen
    int lvl = digitalRead(PIN);
    Serial.printf("   %-26s : %s\n", label, lvl ? "HIGH" : "LOW");
    return lvl;
}

void setup() {
    Serial.begin(115200);
    uint32_t t0 = millis();
    while (!Serial && millis() - t0 < 3000) delay(10);
    delay(300);

    Serial.println();
    Serial.println(F("=== SW-420 Vibrationssensor-Diagnose ==="));
    Serial.printf(" Pin: GPIO%d  (config.h VIBRATION_PIN)\n", PIN);
    Serial.println(F("========================================"));

    Serial.println(F("\n[1] Pull-Test (Eule bitte STILL halten)"));
    int up = readWith(INPUT_PULLUP, "INPUT_PULLUP");
    int dn = readWith(INPUT_PULLDOWN, "INPUT_PULLDOWN");

    // Analogpegel in drei Konfigurationen. Der Wert MIT eingeschaltetem
    // Pullup verraet, wie hart der Pin nach Masse gezogen wird: der interne
    // Pullup (~45 kOhm) bildet mit dem Senkpfad einen Spannungsteiler, also
    // laesst sich dessen Widerstand ausrechnen. Ein Loetschluss liegt bei
    // wenigen Ohm (Millivolt), ein leitender Transistor bei einigen hundert
    // Ohm, ein floatender Pin waere ~3.3 V.
    Serial.println(F("\n[2] Analogpegel in drei Konfigurationen"));
    const int RPU_OHM = 45000;
    int mvNone = 0, mvUp = 0, mvDown = 0;
    struct { int mode; const char* label; int* out; } probes[] = {
        { INPUT,           "INPUT (kein Pull)", &mvNone },
        { INPUT_PULLUP,    "INPUT_PULLUP",      &mvUp   },
        { INPUT_PULLDOWN,  "INPUT_PULLDOWN",    &mvDown },
    };
    for (auto& p : probes) {
        pinMode(PIN, p.mode);
        delay(80);
        long sum = 0;
        for (int i = 0; i < 32; i++) { sum += analogReadMilliVolts(PIN); delay(2); }
        *p.out = (int)(sum / 32);
        Serial.printf("   %-20s : %4d mV\n", p.label, *p.out);
    }
    int mv = mvNone;
    if (mvUp < 3200) {
        long r = (long)mvUp * RPU_OHM / (3300 - mvUp);
        Serial.printf("   %-20s : ~%ld Ohm\n", "-> Senkpfad nach GND", r);
        if (r < 50)        Serial.println(F("      = praktisch KURZSCHLUSS gegen Masse"));
        else if (r < 3000) Serial.println(F("      = niederohmig; aktiver Transistor ODER Loetbruecke"));
        else               Serial.println(F("      = hochohmig"));
    }

    Serial.println(F("\n[3] BEFUND"));
    if (up == HIGH && dn == LOW) {
        Serial.println(F("   Pin FOLGT dem Pull -> Ausgang hochohmig oder NICHT"));
        Serial.println(F("   ANGESCHLOSSEN. Kein Poti-Problem: erst die Verkabelung"));
        Serial.println(F("   pruefen (DO des Moduls -> D2/GPIO3, VCC, GND)."));
    } else if (up == LOW && dn == LOW) {
        Serial.println(F("   Pin wird AKTIV auf LOW gezogen, auch gegen den"));
        Serial.println(F("   internen Pullup. Das Modul treibt seinen Ruhepegel"));
        Serial.println(F("   also LOW -> entweder ist das Poti zu empfindlich"));
        Serial.println(F("   (Komparator dauerhaft ausgeloest), oder der Ruhepegel"));
        Serial.println(F("   dieses Moduls ist LOW und die Firmware-Polaritaet"));
        Serial.println(F("   (active-low) ist verkehrt. Phase 4 klaert das."));
    } else if (up == HIGH && dn == HIGH) {
        Serial.println(F("   Pin wird AKTIV auf HIGH getrieben -> Ruhepegel HIGH."));
        Serial.println(F("   Dann ist die Firmware-Polaritaet richtig (active-low)"));
        Serial.println(F("   und der Sensor war schlicht ausgeloest."));
    } else {
        Serial.println(F("   Unerwartete Kombination - siehe Rohwerte oben."));
    }

    Serial.println(F("\n[4] PIN-SUCHE: alle Header-Pins gleichzeitig ueberwachen."));
    Serial.println(F("    Das Modul arbeitet (LEDs), also muss das Signal IRGENDWO"));
    Serial.println(F("    ankommen. Jetzt klopfen - der Pin der zuckt ist der echte."));
    for (size_t k = 0; k < NPINS; k++) {
        pinMode(SCAN[k], INPUT_PULLUP);
    }
    delay(80);
    Serial.println(F("\n    Ruhepegel aller Pins (mit Pullup):"));
    for (size_t k = 0; k < NPINS; k++) {
        lastLvl[k] = digitalRead(SCAN[k]);
        Serial.printf("      GPIO%-2d (%-3s) : %s%s\n", SCAN[k], SCAN_LBL[k],
                      lastLvl[k] ? "HIGH" : "LOW ",
                      lastLvl[k] ? "" : "   <- wird nach Masse gezogen");
    }
    Serial.println(F("\n    --- ab jetzt klopfen ---"));
}

void loop() {
    static uint32_t lastPrint = 0;
    static uint32_t total = 0;

    for (size_t k = 0; k < NPINS; k++) {
        const int lvl = digitalRead(SCAN[k]);
        if (lvl != lastLvl[k]) {
            lastLvl[k] = lvl;
            edgeCnt[k]++;
            total++;
            Serial.printf("%8lu  GPIO%-2d (%-3s) -> %-4s   Flanke #%lu\n",
                          (unsigned long)millis(), SCAN[k], SCAN_LBL[k],
                          lvl ? "HIGH" : "LOW", (unsigned long)edgeCnt[k]);
        }
    }

    if (millis() - lastPrint > 2000) {
        lastPrint = millis();
        Serial.printf("   ... %lu Flanken insgesamt |", (unsigned long)total);
        for (size_t k = 0; k < NPINS; k++) {
            if (edgeCnt[k]) Serial.printf(" %s:%lu", SCAN_LBL[k], (unsigned long)edgeCnt[k]);
        }
        Serial.println();
    }
    delayMicroseconds(200);   // ~5 kHz: erwischt auch kurze Impulse
}

#endif // VIBTEST_ACTIVE
