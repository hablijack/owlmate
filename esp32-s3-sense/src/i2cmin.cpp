// ============================================================================
// MINIMALER I2C-Scan (env: i2cmin, -DI2CMIN_ACTIVE)
//
// Absichtlich das kleinstmoegliche Programm: Wire.begin() auf den Pins aus
// config.h, dann ein Adressscan im Sekundentakt. KEIN Display, keine Kamera,
// kein PSRAM, keine Servos, kein Bit-Banging, keine Recovery-Impulse.
//
// Grund: i2ctest macht sehr viel mehr (Bit-Banging auf denselben Pins,
// 9-Takt-Bus-Recovery, Pinwechsel im Betrieb) und ist damit als Schiedsrichter
// ungeeignet, wenn die Frage lautet "ist der Bus selbst in Ordnung?". Dieses
// Programm kann den Bus nicht beeinflussen - was es findet, ist da; was es
// nicht findet, antwortet nicht.
//
// Laeuft dauerhaft, damit man beim Wackeln an der Verkabelung live zusieht.
// ============================================================================
#if defined(I2CMIN_ACTIVE)

#include <Arduino.h>
#include <Wire.h>
#include "config.h"
#include <Adafruit_GPS.h>

// Testtakt. HISTORISCH war das der interessante Regler: der BNO055 verletzte
// beim Clock-Stretching die Setup-Zeit zwischen SDA-HIGH und SCL-HIGH und galt
// mit ESP32/ESP32-S3 als unzuverlaessig (Adafruit: "Troublesome Chips"), 100 kHz
// reichte nicht, 30 kHz war der Ausweg. Der LSM303AGR dehnt den Takt nicht und
// laeuft bei 400 kHz - der Regler bleibt trotzdem, weil er jetzt eine andere
// Frage beantwortet: reagiert der Bus ueberhaupt auf den Takt (Kabellaenge,
// Pull-ups, Uebersprechen)?
#ifndef TESTHZ
#define TESTHZ 400000
#endif

static const char* nameOf(uint8_t a) {
    switch (a) {
        case 0x10: return " (PA1010D GPS)";
        case 0x19: return " (LSM303AGR accel)";
        case 0x1E: return " (LSM303AGR mag)";
        case 0x40: return " (PCA9685 Servo)";
        case 0x70: return " (PCA9685 all-call)";
        default:   return "";
    }
}

// Eine einzelne Adresse ansprechen und DANACH die Ruhepegel lesen. Der Bus
// wird zwischen den Versuchen wieder freigegeben, damit die Pegelmessung nicht
// vom Treiber stammt.
// Standard-I2C-Bus-Recovery: 9 Taktimpulse auf SCL, waehrend SDA losgelassen
// wird. Ein Slave, der mitten in einer Uebertragung haengt und SDA festhaelt,
// zaehlt die Impulse zu Ende und gibt den Bus frei. Danach eine Stop-Bedingung.
//
// Genau das trennt "Bauteil defekt" von "Bauteil haengt": kommt ein Geraet nach
// den Impulsen zurueck, ist es in Ordnung und das Problem liegt im Protokoll -
// nicht im Chip. Am BNO055 war das der Normalfall (er dehnte den Takt); beim
// LSM303AGR waere ein haengender Bus ein echter Befund.
static void busRecover() {
    pinMode(I2C_SDA, INPUT_PULLUP);
    pinMode(I2C_SCL, OUTPUT);
    for (int i = 0; i < 9; i++) {
        digitalWrite(I2C_SCL, LOW);  delayMicroseconds(5);
        digitalWrite(I2C_SCL, HIGH); delayMicroseconds(5);
    }
    // Stop: SDA steigt, waehrend SCL hoch ist.
    pinMode(I2C_SDA, OUTPUT);
    digitalWrite(I2C_SDA, LOW);  delayMicroseconds(5);
    digitalWrite(I2C_SCL, HIGH); delayMicroseconds(5);
    digitalWrite(I2C_SDA, HIGH); delayMicroseconds(5);
    pinMode(I2C_SDA, INPUT_PULLUP);
    pinMode(I2C_SCL, INPUT_PULLUP);
    delay(5);
}

static bool probeOnce(uint8_t addr, int& sdaOut, int& sclOut) {
    Wire.end();
    Wire.begin(I2C_SDA, I2C_SCL, TESTHZ);
    delay(5);
    Wire.beginTransmission(addr);
    const bool ack = (Wire.endTransmission() == 0);
    Wire.end();
    pinMode(I2C_SDA, INPUT_PULLUP);
    pinMode(I2C_SCL, INPUT_PULLUP);
    delayMicroseconds(300);
    sdaOut = digitalRead(I2C_SDA);
    sclOut = digitalRead(I2C_SCL);
    return ack;
}

// Eine Adresse acht Mal isoliert ansprechen. Faellt der Bus waehrend genau
// dieser Phase aus, ist das Bauteil gefunden.
static bool phase(const char* label, uint8_t addr) {
    Serial.printf("--- nur %s (0x%02X) ---\n", label, addr);
    for (int i = 1; i <= 12; i++) {
        int sda, scl;
        const bool ack = probeOnce(addr, sda, scl);
        Serial.printf("   %d: %-8s  danach SDA=%s SCL=%s%s\n",
                      i, ack ? "ACK" : "kein ACK",
                      sda ? "HIGH" : "LOW ", scl ? "HIGH" : "LOW ",
                      (sda && scl) ? "" : "   <-- BUS TOT");
        if (!sda || !scl) {
            Serial.printf("   >>> Bus stirbt bei %s (0x%02X) <<<\n", label, addr);

            // Ist der Chip defekt oder haengt er nur? Recovery entscheidet das.
            for (int versuch = 1; versuch <= 3; versuch++) {
                busRecover();
                pinMode(I2C_SDA, INPUT_PULLUP);
                pinMode(I2C_SCL, INPUT_PULLUP);
                delayMicroseconds(300);
                const int rs = digitalRead(I2C_SDA), rc = digitalRead(I2C_SCL);
                Serial.printf("   Recovery %d: Leitungen SDA=%s SCL=%s\n",
                              versuch, rs ? "HIGH" : "LOW ", rc ? "HIGH" : "LOW ");
                if (rs && rc) {
                    int s2, c2;
                    const bool ack2 = probeOnce(addr, s2, c2);
                    Serial.printf("   -> danach 0x%02X: %s   (SDA=%s SCL=%s)\n",
                                  addr, ack2 ? "ACK - CHIP LEBT!" : "kein ACK",
                                  s2 ? "HIGH" : "LOW ", c2 ? "HIGH" : "LOW ");
                    Serial.println(ack2
                        ? F("   ==> Bauteil ist NICHT defekt, es hing nur fest.")
                        : F("   ==> Bus frei, aber Chip antwortet nicht mehr."));
                    return false;
                }
            }
            Serial.println(F("   ==> Recovery erfolglos - Leitungen bleiben unten."));
            return false;
        }
        delay(250);
    }
    Serial.printf("   %s ueberlebt alle 12 Versuche.\n\n", label);
    return true;
}

static void scan(uint32_t hz) {
    Wire.end();
    Wire.begin(I2C_SDA, I2C_SCL, hz);
    delay(50);
    int found = 0;
    Serial.printf("  %6lu Hz: ", (unsigned long)hz);
    // NUR die echten Adressen, kein Sweep ueber 112 Adressen. Damit laesst
    // sich pruefen, ob der Bus vom Scan selbst gestoert wird.
    static const uint8_t nur[] = {0x10, 0x19, 0x1E, 0x40};
    for (uint8_t i = 0; i < sizeof(nur); i++) {
        const uint8_t a = nur[i];
        Wire.beginTransmission(a);
        if (Wire.endTransmission() == 0) {
            Serial.printf("0x%02X%s  ", a, nameOf(a));
            found++;
        } else {
            Serial.printf("0x%02X:-  ", a);
        }
    }
    if (!found) Serial.print("nichts gefunden");
    Serial.println();
}

void setup() {
    Serial.begin(115200);
    delay(2000);
    Serial.println();
    Serial.println(F("=== Minimaler I2C-Scan ==="));
    Serial.printf("SDA=GPIO%d  SCL=GPIO%d\n", I2C_SDA, I2C_SCL);
    Serial.println(F("Erwartet: 0x10 GPS, 0x19+0x1E IMU, 0x40 Servo (+0x70 all-call)"));
    Serial.println();
}

// ============================================================================
// BIT-BANG-I2C: der letzte saubere Unterscheidungstest.
//
// Umgeht den I2C-Controller des ESP32 vollstaendig. Entscheidend ist, dass wir
// das Clock-Stretching KORREKT behandeln: nach jeder steigenden Flanke wird
// gewartet, bis der Slave SCL tatsaechlich freigibt. Genau das macht der
// Hardware-Controller nur bis zu seinem Timeout.
//
// Geprueft wird das WHO_AM_I des Beschleunigungssensors (0x19, Register 0x0F).
// Liest 0x33 -> der Chip ist in Ordnung, das Problem liegt im Controller.
// Liest nichts -> der Chip antwortet wirklich nicht mehr.
// ============================================================================
static inline void sdaHigh() { pinMode(I2C_SDA, INPUT_PULLUP); }
static inline void sdaLow()  { pinMode(I2C_SDA, OUTPUT); digitalWrite(I2C_SDA, LOW); }
static inline void sclLow()  { pinMode(I2C_SCL, OUTPUT); digitalWrite(I2C_SCL, LOW); }
static inline void qdel()    { delayMicroseconds(30); }   // ~15 kHz, bewusst langsam

// SCL freigeben und WARTEN, bis sie wirklich hoch ist. Das ist die
// Clock-Stretch-Behandlung, an der der Hardware-Controller scheitert.
static bool sclHighWait() {
    pinMode(I2C_SCL, INPUT_PULLUP);
    for (uint32_t i = 0; i < 200000; i++) {       // grosszuegig: ~200 ms
        if (digitalRead(I2C_SCL)) return true;
        delayMicroseconds(1);
    }
    return false;                                  // Slave gibt SCL nicht frei
}

static bool bbStart() { sdaHigh(); if (!sclHighWait()) return false; qdel();
                        sdaLow(); qdel(); sclLow(); qdel(); return true; }
static void bbStop()  { sdaLow(); qdel(); sclHighWait(); qdel(); sdaHigh(); qdel(); }

// true = ACK vom Slave
static bool bbWrite(uint8_t v) {
    for (int i = 7; i >= 0; i--) {
        (v >> i) & 1 ? sdaHigh() : sdaLow();
        qdel();
        if (!sclHighWait()) return false;
        qdel(); sclLow(); qdel();
    }
    sdaHigh(); qdel();                              // ACK-Takt
    if (!sclHighWait()) return false;
    const bool ack = (digitalRead(I2C_SDA) == LOW);
    qdel(); sclLow(); qdel();
    return ack;
}

static uint8_t bbRead(bool ackIt) {
    uint8_t v = 0;
    sdaHigh();
    for (int i = 7; i >= 0; i--) {
        qdel();
        if (!sclHighWait()) return 0xFF;
        delayMicroseconds(8);                       // Daten stabilisieren lassen
        v = (v << 1) | (digitalRead(I2C_SDA) ? 1 : 0);
        qdel(); sclLow(); qdel();
    }
    ackIt ? sdaLow() : sdaHigh();
    qdel(); sclHighWait(); qdel(); sclLow(); qdel();
    sdaHigh();
    return v;
}

static void bitbangTest() {
    Serial.println(F("=== BIT-BANG (eigener Takt, echtes Clock-Stretch-Warten) ==="));
    sdaHigh(); pinMode(I2C_SCL, INPUT_PULLUP); delay(5);
    Serial.printf("  Ruhepegel vorher: SDA=%s SCL=%s\n",
                  digitalRead(I2C_SDA) ? "HIGH" : "LOW ",
                  digitalRead(I2C_SCL) ? "HIGH" : "LOW ");

    for (int runde = 1; runde <= 8; runde++) {
        if (!bbStart()) { Serial.printf("  %d: START gescheitert (SCL bleibt unten)\n", runde); break; }
        bool ok = bbWrite(ADDR_LSM303_ACCEL << 1);  // Adresse + Write
        if (!ok) { Serial.printf("  %d: kein ACK auf die Adresse\n", runde); bbStop(); delay(150); continue; }
        ok = bbWrite(0x0F);                         // Register WHO_AM_I
        if (!ok) { Serial.printf("  %d: kein ACK auf das Register\n", runde); bbStop(); delay(150); continue; }
        if (!bbStart()) { Serial.printf("  %d: Repeated START gescheitert\n", runde); break; }
        ok = bbWrite((ADDR_LSM303_ACCEL << 1) | 1); // Adresse + Read
        if (!ok) { Serial.printf("  %d: kein ACK auf Read-Adresse\n", runde); bbStop(); delay(150); continue; }
        const uint8_t id = bbRead(false);
        bbStop();
        Serial.printf("  %d: WHO_AM_I = 0x%02X %s\n", runde, id,
                      id == 0x33 ? "<== KORREKT, CHIP LEBT!" : "");
        delay(150);
    }
    delay(5);
    Serial.printf("  Ruhepegel danach: SDA=%s SCL=%s\n",
                  digitalRead(I2C_SDA) ? "HIGH" : "LOW ",
                  digitalRead(I2C_SCL) ? "HIGH" : "LOW ");
}

// EIN EINZIGES Wire.begin(), danach nie wieder end()/begin(). Genau das ist der
// Unterschied zwischen den beiden vorherigen Laeufen: mit einmaliger Init hielt
// der Bus 12 Lesezugriffe durch, mit Neuinitialisierung vor jedem Versuch war er
// sofort tot. Verdacht: nicht der IMU legte den Bus lahm, sondern das
// wiederholte Neuaufsetzen des I2C-Treibers - und mein erster Bisektions-Test
// hat genau das getan.
static void levels(int& sda, int& scl) {
    // Pegel lesen OHNE den Treiber anzufassen ist nicht moeglich; deshalb nur
    // am Ende einer Serie messen, nicht zwischen den Zugriffen.
    pinMode(I2C_SDA, INPUT_PULLUP); pinMode(I2C_SCL, INPUT_PULLUP);
    delayMicroseconds(300);
    sda = digitalRead(I2C_SDA); scl = digitalRead(I2C_SCL);
}

// Gesundheitspruefung fuer ALLES AUSSER dem IMU.
//
// Beruehrt die IMU-Adressen bewusst nie. Beim BNO055 war das zwingend - er hielt
// SCL fest, sobald man ihn ansprach, und riss GPS und Servotreiber mit sich; die
// Frage lautete, ob die beiden Schaden genommen hatten oder nur
// Kollateralopfer waren. Beim LSM303AGR ist es nicht mehr zwingend, aber immer
// noch nuetzlich: es ist der einzige Lauf, der GPS und Servotreiber OHNE
// jeglichen IMU-Verkehr am Bus beurteilt.
static Adafruit_GPS GPS(&Wire);

void loop() {
    static bool fertig = false;
    if (fertig) { delay(1000); return; }
    fertig = true;

    Serial.println(F("=== Bus ohne IMU: GPS und Servotreiber ==="));
    Wire.begin(I2C_SDA, I2C_SCL, I2C_FREQ);
    Wire.setTimeOut(I2C_TIMEOUT_MS);
    delay(50);

    // ---- PCA9685: schreiben und zurueeklesen ----
    Serial.println(F("\n[1] PCA9685 (0x40): MODE1 schreiben und zurueeklesen"));
    Wire.beginTransmission(0x40);
    Wire.write(0x00); Wire.write(0x20);            // MODE1 = AI aktiv
    const uint8_t w = Wire.endTransmission();
    Wire.beginTransmission(0x40);
    Wire.write(0x00);
    Wire.endTransmission(false);
    const uint8_t got = Wire.requestFrom((uint8_t)0x40, (uint8_t)1) ? Wire.read() : 0xFF;
    Serial.printf("   schreiben: %s   zurueeklesen: 0x%02X %s\n",
                  w == 0 ? "ok" : "FEHLER", got,
                  got == 0x20 ? "<== stimmt ueberein, Chip arbeitet"
                              : "<== weicht ab");

    // ---- GPS: echte NMEA-Saetze ----
    Serial.println(F("\n[2] PA1010D (0x10): 8 s NMEA mitlesen"));
    GPS.begin(0x10);
    GPS.sendCommand(PMTK_SET_NMEA_OUTPUT_RMCGGA);
    GPS.sendCommand(PMTK_SET_NMEA_UPDATE_1HZ);
    delay(200);

    uint32_t ende = millis() + 8000;
    int saetze = 0, zeichen = 0;
    String letzte;
    while (millis() < ende) {
        const char c = GPS.read();
        if (c) zeichen++;
        if (GPS.newNMEAreceived()) {
            saetze++;
            letzte = String(GPS.lastNMEA());
            letzte.trim();
            if (saetze <= 3) Serial.printf("   %s\n", letzte.c_str());
            GPS.parse(GPS.lastNMEA());
        }
    }
    Serial.printf("   -> %d Zeichen, %d vollstaendige Saetze in 8 s\n", zeichen, saetze);
    Serial.printf("   Fix: %s, Satelliten: %d\n",
                  GPS.fix ? "ja" : "nein (drinnen normal)", (int)GPS.satellites);
    Serial.println(saetze > 0
        ? F("   ==> GPS sendet Daten - unbeschaedigt.")
        : F("   ==> Keine Saetze. Antwortet zwar, liefert aber nichts."));

    Serial.println(F("\n0x19/0x1E wurden in diesem Test nie angesprochen."));
}

#endif
