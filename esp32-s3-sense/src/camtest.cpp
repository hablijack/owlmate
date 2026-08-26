// ============================================================================
// Kamera-Inbetriebnahme OV3660 (env: camtest, -DCAMTEST_ACTIVE)
//
// Beantwortet der Reihe nach:
//   1. Laesst sich der Treiber initialisieren? (Fehlercode wird im Klartext
//      ausgegeben - ESP_ERR_NOT_FOUND heisst meist "kein Modul erkannt")
//   2. Meldet sich wirklich ein OV2640? (Sensor-PID, OV2640 = 0x26)
//   3. Kommen Bilder an, in welcher Groesse, wie schnell?
//   4. SIEHT die Kamera etwas? Aus jedem Bild wird die mittlere Helligkeit
//      berechnet. Ein initialisierter Sensor, der nur Schwarz liefert, ist ein
//      voellig anderer Fehler als einer, der gar nicht startet - und ohne
//      diese Auswertung sehen beide gleich aus.
//
// Bewusst OHNE Gesichtserkennung: esp-dl ist im Arduino-Core 3.x nicht mehr
// enthalten (siehe BACKLOG.md). Die Kamera laesst sich davon unabhaengig
// pruefen, und das ist die Voraussetzung fuer jeden Erkennungsweg.
// ============================================================================
#if defined(CAMTEST_ACTIVE)

#include <Arduino.h>
#include "esp_camera.h"
#include <Wire.h>

// Pinbelegung des XIAO ESP32-S3 Sense (Kamera sitzt am B2B-Stecker).
static void fillConfig(camera_config_t& c) {
    c = {};
    c.pin_pwdn = -1;
    c.pin_reset = -1;
    c.pin_xclk = 10;
    c.pin_sccb_sda = 40;
    c.pin_sccb_scl = 39;
    c.pin_d7 = 48; c.pin_d6 = 11; c.pin_d5 = 12; c.pin_d4 = 14;
    c.pin_d3 = 16; c.pin_d2 = 18; c.pin_d1 = 17; c.pin_d0 = 15;
    c.pin_vsync = 38;
    c.pin_href = 47;
    c.pin_pclk = 13;
    c.xclk_freq_hz = 20000000;
    c.ledc_timer = LEDC_TIMER_0;
    c.ledc_channel = LEDC_CHANNEL_0;
    c.pixel_format = PIXFORMAT_RGB565;   // sensorunabhaengig, OV3660 wie OV2640
    c.frame_size = FRAMESIZE_QVGA;       // 320x240
    c.jpeg_quality = 12;
    c.fb_count = 1;
    c.fb_location = CAMERA_FB_IN_PSRAM;
    c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
}

// Mittlere Helligkeit eines RGB565-Bildes (0..255). Bytes liegen als
// Big-Endian-Paare im Puffer.
static void frameStats(const uint8_t* buf, size_t len, int& mean, int& lo, int& hi) {
    uint64_t sum = 0; uint32_t n = 0;
    lo = 255; hi = 0;
    for (size_t i = 0; i + 1 < len; i += 2 * 8) {     // jedes 8. Pixel reicht
        const uint16_t px = (buf[i] << 8) | buf[i + 1];
        const int r = ((px >> 11) & 0x1F) << 3;
        const int g = ((px >> 5) & 0x3F) << 2;
        const int b = (px & 0x1F) << 3;
        const int y = (r * 30 + g * 59 + b * 11) / 100;
        sum += y; n++;
        if (y < lo) lo = y;
        if (y > hi) hi = y;
    }
    mean = n ? (int)(sum / n) : 0;
}

// XCLK auf GPIO10 starten. Nicht jede Kombination aus Frequenz und Aufloesung
// ist erreichbar - der LEDC-Teiler (80 MHz / 2^bits / freq) muss ganzzahlig
// bleiben, 20 MHz mit 2 Bit liegt exakt auf der Grenze und wird abgelehnt.
static bool startXclk() {
    struct XclkOpt { uint32_t hz; uint8_t bits; };
    static const XclkOpt opts[] = {
        {20000000, 1}, {10000000, 2}, {10000000, 1}, {8000000, 2}, {5000000, 3},
    };
    for (const auto& o : opts) {
        if (ledcAttach(10, o.hz, o.bits)) {
            ledcWrite(10, (1u << o.bits) / 2);
            Serial.printf("   XCLK: %lu Hz, %u Bit\n", (unsigned long)o.hz, o.bits);
            return true;
        }
    }
    Serial.println(F("   XCLK liess sich NICHT starten"));
    return false;
}

// OV3660/OV5640 adressieren ihre Register mit ZWEI Bytes. Ein 8-Bit-Zugriff
// wie bei OV2640/OV7670 liefert dort nur Muell - deshalb beide Varianten.
static bool readReg16(uint8_t addr, uint16_t reg, uint8_t& out) {
    Wire1.beginTransmission(addr);
    Wire1.write((uint8_t)(reg >> 8));
    Wire1.write((uint8_t)(reg & 0xFF));
    if (Wire1.endTransmission(false) != 0) return false;
    if (Wire1.requestFrom(addr, (uint8_t)1) != 1) return false;
    out = Wire1.read();
    return true;
}

static bool readReg8(uint8_t addr, uint8_t reg, uint8_t& out) {
    Wire1.beginTransmission(addr);
    Wire1.write(reg);
    if (Wire1.endTransmission(false) != 0) return false;
    if (Wire1.requestFrom(addr, (uint8_t)1) != 1) return false;
    out = Wire1.read();
    return true;
}

// Bus abhorchen: Ruhepegel, Adressscan und gezielte ID-Lesezugriffe.
static void sccbProbe() {
    const bool xclk = startXclk();

    // IST ueberhaupt ein Modul angeschlossen? Mit eingeschaltetem internen
    // Pullup liest die Leitung immer HIGH - das sagt nichts. Aussagekraeftig
    // ist der interne PULLDOWN (~45 kOhm): ein angestecktes Kameramodul bringt
    // eigene Pullups von ~4,7 kOhm mit und haelt die Leitung trotzdem HIGH.
    // Ohne Modul zieht der Pulldown sie auf LOW.
    // NUR digital lesen: GPIO39/40 haben auf dem ESP32-S3 keinen ADC (ADC1
    // liegt auf GPIO1-10, ADC2 auf GPIO11-20). analogReadMilliVolts() scheitert
    // dort nicht nur, es reisst den HAL mit in einen LoadProhibited-Absturz.
    for (int pin : {40, 39}) {
        pinMode(pin, INPUT_PULLDOWN);
        delay(20);
        const int lvl = digitalRead(pin);
        Serial.printf("   GPIO%-2d (%s) mit PULLDOWN: %s  -> %s\n",
                      pin, pin == 40 ? "SDA" : "SCL", lvl ? "HIGH" : "LOW ",
                      lvl ? "externe Pullups vorhanden, Modul haengt dran"
                          : "KEIN externer Pullup - Modul nicht verbunden");
    }
    pinMode(40, INPUT_PULLUP); pinMode(39, INPUT_PULLUP);
    delay(5);

    if (!Wire1.begin(40, 39, 100000)) {
        Serial.println(F("   Wire1.begin() FEHLGESCHLAGEN"));
        if (xclk) ledcDetach(10);
        return;
    }
    delay(200);   // Sensor nach dem Takt-Start hochlaufen lassen

    int nf = 0;
    for (uint8_t a = 0x08; a <= 0x77; a++) {
        Wire1.beginTransmission(a);
        if (Wire1.endTransmission() == 0) {
            Serial.printf("   Adressscan: 0x%02X antwortet\n", a);
            nf++;
        }
    }
    if (!nf) Serial.println(F("   Adressscan: keine Antwort"));

    // Unabhaengig vom Adressscan die bekannten Kameraadressen direkt anlesen -
    // manche Sensoren moegen die Nulllaengen-Probe nicht.
    Serial.println(F("   gezielte ID-Abfragen:"));

    // 16-Bit-Sensoren: OV3660 (0x3660) und OV5640 (0x5640) liegen beide auf 0x3C.
    {
        uint8_t h = 0, lo = 0;
        const bool a = readReg16(0x3C, 0x300A, h);
        const bool b = readReg16(0x3C, 0x300B, lo);
        Serial.printf("     0x3C 16-Bit 0x300A/B : ");
        if (a && b) {
            Serial.printf("0x%02X%02X%s\n", h, lo,
                          (h == 0x36 && lo == 0x60) ? "   -> OV3660!"
                        : (h == 0x56 && lo == 0x40) ? "   -> OV5640!" : "");
        } else {
            Serial.println(F("keine Antwort"));
        }
    }

    // 8-Bit-Sensoren: OV2640 auf 0x30 (nach Bankwahl 0xFF=0x01), OV7670 auf 0x21.
    struct Cand { uint8_t addr; const char* who; };
    static const Cand cands[] = {
        {0x30, "OV2640"}, {0x21, "OV7670/OV7725"}, {0x42, "GC0308"},
    };
    for (const auto& c : cands) {
        Wire1.beginTransmission(c.addr); Wire1.write(0xFF); Wire1.write(0x01);
        Wire1.endTransmission();
        delay(2);
        uint8_t pid = 0, ver = 0;
        const bool ok = readReg8(c.addr, 0x0A, pid) && readReg8(c.addr, 0x0B, ver);
        Serial.printf("     0x%02X 8-Bit  0x0A/0x0B  : ", c.addr);
        if (ok) Serial.printf("PID 0x%02X VER 0x%02X%s\n", pid, ver,
                              pid == 0x26 ? "   -> OV2640!" : "");
        else    Serial.printf("keine Antwort   (%s)\n", c.who);
    }

    Wire1.end();
    if (xclk) ledcDetach(10);
    delay(20);
}

void setup() {
    Serial.begin(115200);
    uint32_t t0 = millis();
    while (!Serial && millis() - t0 < 3000) delay(10);
    delay(300);

    Serial.println();
    Serial.println(F("=== Kamera-Test (OV3660) ==="));
    Serial.printf(" PSRAM: %s, frei %u Byte\n",
                  psramFound() ? "vorhanden" : "FEHLT (Kamera braucht ihn!)",
                  (unsigned)ESP.getFreePsram());

    // --- Phase 0a: elektrischer Fingerabdruck aller Kamerapins --------------
    // Entscheidet, ob das Modul ueberhaupt erreicht wird. Jeder Pin wird einmal
    // mit internem Pullup und einmal mit Pulldown gelesen. Ein Pin, der BEIDE
    // Male demselben internen Widerstand folgt, haengt an nichts. Weicht er ab,
    // zieht etwas Externes daran - dann besteht Kontakt zum Modul.
    // Vergleich: dieselbe Messung mit abgezogenem Flachbandkabel.
    Serial.println(F("\n[0a] Fingerabdruck der Kamerapins"));
    Serial.println(F("     PU/PD = Pegel mit Pullup / mit Pulldown"));
    {
        struct P { int gpio; const char* name; };
        static const P pins[] = {
            {10,"XCLK"}, {40,"SDA"}, {39,"SCL"}, {13,"PCLK"}, {38,"VSYNC"},
            {47,"HREF"}, {48,"D7"}, {11,"D6"}, {12,"D5"}, {14,"D4"},
            {16,"D3"}, {18,"D2"}, {17,"D1"}, {15,"D0"},
        };
        int extern_cnt = 0;
        for (const auto& p : pins) {
            pinMode(p.gpio, INPUT_PULLUP);   delay(6);
            const int up = digitalRead(p.gpio);
            pinMode(p.gpio, INPUT_PULLDOWN); delay(6);
            const int dn = digitalRead(p.gpio);
            // up==1 && dn==0 -> folgt nur dem internen Widerstand = nichts dran
            const bool ext = !(up == 1 && dn == 0);
            if (ext) extern_cnt++;
            Serial.printf("     GPIO%-2d %-5s PU=%d PD=%d  %s\n",
                          p.gpio, p.name, up, dn,
                          ext ? "<- extern getrieben/gezogen" : "frei");
        }
        Serial.printf("     => %d von %d Pins extern beeinflusst\n",
                      extern_cnt, (int)(sizeof(pins)/sizeof(pins[0])));
    }

    Serial.println(F("\n[0] SCCB-Bus direkt abfragen"));
    sccbProbe();

    camera_config_t cfg;
    fillConfig(cfg);

    Serial.println(F("\n[1] Treiber-Initialisierung"));
    const esp_err_t err = esp_camera_init(&cfg);
    if (err != ESP_OK) {
        Serial.printf("   FEHLGESCHLAGEN: 0x%x (%s)\n", err, esp_err_to_name(err));
        Serial.println(F("   ESP_ERR_NOT_FOUND / 0x105 -> kein Sensor erkannt:"));
        Serial.println(F("     Sense-Erweiterungsboard richtig aufgesteckt?"));
        Serial.println(F("     FPC-Kabel bis zum Anschlag im Sockel, Verriegelung zu?"));
        Serial.println(F("     Kontakte des Flachkabels zur richtigen Seite?"));
        Serial.println(F("\n[1b] Zweiter SCCB-Versuch NACH dem Treiber-Init."));
        Serial.println(F("     Der Treiber hat den Sensor inzwischen getaktet und"));
        Serial.println(F("     zurueckgesetzt - antwortet er jetzt, war es ein"));
        Serial.println(F("     Anlauf-/Timing-Problem meiner ersten Abfrage."));
        sccbProbe();
        while (true) delay(1000);
    }
    Serial.println(F("   OK"));

    Serial.println(F("\n[2] Sensor-Identifikation"));
    sensor_t* s = esp_camera_sensor_get();
    if (!s) {
        Serial.println(F("   kein Sensor-Handle"));
    } else {
        Serial.printf("   PID 0x%02X  VER 0x%02X  MIDH 0x%02X MIDL 0x%02X\n",
                      s->id.PID, s->id.VER, s->id.MIDH, s->id.MIDL);
        // Der OV3660 meldet eine 16-Bit-PID (0x3660), der OV2640 eine
        // 8-Bit-PID (0x26). Beide gelten, alles andere ist unerwartet.
        const char* who = (s->id.PID == 0x3660) ? "OV3660 erkannt"
                        : (s->id.PID == 0x26)   ? "OV2640 erkannt"
                                                : "unbekannter Sensor";
        Serial.printf("   -> %s\n", who);
    }

    Serial.println(F("\n[3] Bilder aufnehmen (10 Stueck)"));
    Serial.println(F("    mean = mittlere Helligkeit 0..255"));
    for (int i = 0; i < 10; i++) {
        const uint32_t t = millis();
        camera_fb_t* fb = esp_camera_fb_get();
        const uint32_t dt = millis() - t;
        if (!fb) {
            Serial.printf("   Bild %2d: FEHLER - kein Puffer\n", i + 1);
            delay(200);
            continue;
        }
        int mean, lo, hi;
        frameStats(fb->buf, fb->len, mean, lo, hi);
        Serial.printf("   Bild %2d: %ux%u  %u Byte  %3lu ms  mean %3d (min %3d max %3d)%s\n",
                      i + 1, fb->width, fb->height, (unsigned)fb->len,
                      (unsigned long)dt, mean, lo, hi,
                      (hi - lo) < 8 ? "   <- fast einfarbig!" : "");
        esp_camera_fb_return(fb);
        delay(150);
    }

    Serial.println(F("\n[4] LIVE: Helligkeit im Sekundentakt."));
    Serial.println(F("    Halte die Hand vor die Linse - der Wert muss deutlich"));
    Serial.println(F("    fallen. Passiert nichts, sieht der Sensor nichts."));
    Serial.println(F("-------------------------------------------------"));
}

void loop() {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) { Serial.println(F("   kein Puffer")); delay(1000); return; }
    int mean, lo, hi;
    frameStats(fb->buf, fb->len, mean, lo, hi);
    const int bars = mean / 8;
    Serial.printf("   Helligkeit %3d  [%.*s%*s]  min %3d max %3d\n",
                  mean, bars, "################################", 32 - bars, "", lo, hi);
    esp_camera_fb_return(fb);
    delay(1000);
}

#endif // CAMTEST_ACTIVE
