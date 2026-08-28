// ============================================================================
// I2C-Leitungen dauerhaft auf HIGH (env: bushigh, -DBUSHIGH_ACTIVE)
//
// Messhilfe zum Aufspueren einer unterbrochenen Leitung: SDA und SCL werden
// fest auf 3,3 V getrieben. Damit laesst sich die Spannung vom XIAO-Pad
// Loetstelle fuer Loetstelle bis zum letzten Board verfolgen - dort wo aus
// 3,3 V plotzlich 0 V wird, liegt die Unterbrechung.
//
// ACHTUNG, bewusst gegen die I2C-Regeln: I2C ist Open-Drain, niemand darf die
// Leitung aktiv auf HIGH treiben. Das ist hier in Ordnung, weil KEIN Bus-
// verkehr stattfindet - kein Wire, kein Treiber, kein Slave wird angesprochen.
// Nur zum Messen benutzen, nie im Normalbetrieb.
//
// Zwei Sicherheitsmassnahmen:
//   1. schwaechste Treiberstufe (GPIO_DRIVE_CAP_0, ~5 mA), damit ein Bauteil,
//      das die Leitung doch nach GND zieht, keinen hohen Strom verursacht.
//   2. der Pegel wird zurueckgelesen. Treibt der Pin HIGH und misst trotzdem
//      LOW, zieht etwas anderes die Leitung herunter - dann ist nicht die
//      Leitung unterbrochen, sondern ein Bauteil haelt den Bus fest.
// ============================================================================
#if defined(BUSHIGH_ACTIVE)

#include <Arduino.h>
#include "driver/gpio.h"
#include "config.h"

// Vollstaendiger Gesundheitstest EINES Pins. Nur sinnvoll, wenn nichts
// angeloetet ist - sonst misst man die Beschaltung, nicht den Pin.
//
// Vier Proben, weil zwei davon allein nichts beweisen: ein nach GND
// durchlegierter Pin liefert bei "LOW erwartet" brav LOW und sieht damit halb
// gesund aus. Erst Pullup-HIGH und Ausgang-HIGH trennen heil von defekt.
struct PinResult { bool pu, pd, oh, ol; };

static PinResult probePin(int pin) {
    PinResult r;
    pinMode(pin, INPUT_PULLUP);   delay(5); r.pu = digitalRead(pin);
    pinMode(pin, INPUT_PULLDOWN); delay(5); r.pd = !digitalRead(pin);
    gpio_set_drive_capability((gpio_num_t)pin, GPIO_DRIVE_CAP_0);
    pinMode(pin, OUTPUT);
    digitalWrite(pin, HIGH);      delay(5); r.oh = digitalRead(pin);
    digitalWrite(pin, LOW);       delay(5); r.ol = !digitalRead(pin);
    pinMode(pin, INPUT);          // wieder freigeben
    return r;
}

static void reportPin(const char* label, int pin) {
    const PinResult r = probePin(pin);
    const bool ok = r.pu && r.pd && r.oh && r.ol;
    Serial.printf("  %-14s GPIO%-2d  Pullup:%s  Pulldown:%s  Aus-HIGH:%s  Aus-LOW:%s   %s\n",
                  label, pin,
                  r.pu ? "ok" : "FEHL", r.pd ? "ok" : "FEHL",
                  r.oh ? "ok" : "FEHL", r.ol ? "ok" : "FEHL",
                  ok ? "-> PIN IN ORDNUNG"
                     : (!r.pu && !r.oh) ? "-> DEFEKT: haengt fest auf LOW"
                                        : "-> DEFEKT");
}

void setup() {
    Serial.begin(115200);
    delay(2000);
    Serial.println();
    Serial.println(F("=== SDA/SCL fest auf HIGH (Messhilfe) ==="));
    Serial.printf("SDA=GPIO%d  SCL=GPIO%d, beide werden auf 3,3 V getrieben.\n",
                  I2C_SDA, I2C_SCL);
    Serial.println(F("Jetzt mit dem Messgeraet der Leitung folgen:"));
    Serial.println(F("  XIAO-Pad D0 -> Loetstelle -> BNO055 -> GPS -> PCA9685"));
    Serial.println(F("Wo aus 3,3 V ploetzlich 0 V wird, liegt die Unterbrechung."));
    Serial.println();

    // Pin-Gesundheitstest. Aussagekraeftig NUR mit abgeloeteten Leitungen.
    Serial.println(F("--- Pin-Test (nur gueltig, wenn nichts angeloetet ist) ---"));
    reportPin("SDA / D0", I2C_SDA);
    reportPin("SCL / D1", I2C_SCL);
    reportPin("D5 Kontrolle", 6);      // bekannt gut, nichts angeschlossen
    Serial.println(F("Faellt D5 ebenfalls durch, stimmt der Test nicht - nicht der Pin."));
    Serial.println();

    // Schwaechste Treiberstufe zuerst, DANN treiben.
    // KONTROLLE: GPIO6 (D5) ist der einzige freie Pin am XIAO - nichts
    // angeloetet. Treibt der HIGH und D0/D1 nicht, liegt es nicht am Programm.
    gpio_set_drive_capability((gpio_num_t)6, GPIO_DRIVE_CAP_0);
    pinMode(6, OUTPUT);
    digitalWrite(6, HIGH);

    gpio_set_drive_capability((gpio_num_t)I2C_SDA, GPIO_DRIVE_CAP_0);
    gpio_set_drive_capability((gpio_num_t)I2C_SCL, GPIO_DRIVE_CAP_0);
    pinMode(I2C_SDA, OUTPUT);
    pinMode(I2C_SCL, OUTPUT);
    digitalWrite(I2C_SDA, HIGH);
    digitalWrite(I2C_SCL, HIGH);
}

void loop() {
    // Pad-Pegel zurueeklesen: digitalRead() liefert beim ESP32 den echten
    // Pegel am Pin, auch wenn er als Ausgang konfiguriert ist.
    const int sda = digitalRead(I2C_SDA);
    const int scl = digitalRead(I2C_SCL);
    const int ctl = digitalRead(6);
    Serial.printf("getrieben HIGH -> SDA(D0)=%s  SCL(D1)=%s | KONTROLLE D5(frei)=%s  %s\n",
                  sda ? "HIGH" : "LOW ", scl ? "HIGH" : "LOW ",
                  ctl ? "HIGH" : "LOW ",
                  (ctl && !sda && !scl)
                      ? "<-- Programm ok, D0/D1 lassen sich NICHT treiben"
                      : (sda && scl) ? "alles HIGH" : "");
    delay(1000);
}

#endif
