// ============================================================================
// Kamera-Schnappschuss OV3660 (env: camsnap, -DCAMSNAP_ACTIVE)
//
// Beantwortet die eine Frage, die camtest offen laesst: WAS sieht die Kamera?
// camtest misst nur die mittlere Helligkeit - damit erkennt man "schwarzes
// Bild", aber nicht "Kamera zeigt an der Nase vorbei" oder "Bild steht auf dem
// Kopf". Genau das ist nach jedem Aus- und Einbau des Kopfes offen, und die
// Gesichtserkennung findet prinzipiell nichts, wenn das Bild verdreht ist
// (die Modelle erkennen nur aufrechte Gesichter, siehe SPEC-008).
//
// Ausgabe: JPEG in Base64 ueber die serielle Schnittstelle, ein Durchlauf je
// Orientierung (vflip/hmirror in allen vier Kombinationen). Dekodiert wird mit
// tools/schnappschuss.py. QVGA wie bei der Erkennung, damit das Bild zeigt was
// der Detektor wirklich bekommt - nicht ein schoeneres Bild in anderer Groesse.
// ============================================================================
#if defined(CAMSNAP_ACTIVE)

#include <Arduino.h>
#include "esp_camera.h"

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
    c.pixel_format = PIXFORMAT_JPEG;     // nur zum Anschauen, nicht zum Erkennen
    c.frame_size = FRAMESIZE_QVGA;       // 320x240, wie die Erkennung
    c.jpeg_quality = 10;
    c.fb_count = 2;
    c.fb_location = CAMERA_FB_IN_PSRAM;
    c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
}

static const char B64[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static void printBase64(const uint8_t* d, size_t n) {
    char line[77];
    int col = 0;
    for (size_t i = 0; i < n; i += 3) {
        const uint32_t b0 = d[i];
        const uint32_t b1 = (i + 1 < n) ? d[i + 1] : 0;
        const uint32_t b2 = (i + 2 < n) ? d[i + 2] : 0;
        const uint32_t v = (b0 << 16) | (b1 << 8) | b2;
        line[col++] = B64[(v >> 18) & 0x3F];
        line[col++] = B64[(v >> 12) & 0x3F];
        line[col++] = (i + 1 < n) ? B64[(v >> 6) & 0x3F] : '=';
        line[col++] = (i + 2 < n) ? B64[v & 0x3F] : '=';
        if (col >= 76) { line[col] = 0; Serial.println(line); col = 0; }
    }
    if (col) { line[col] = 0; Serial.println(line); }
}

// Ein Bild in einer bestimmten Orientierung. Nach dem Umschalten brauchen
// Sensor und Puffer ein paar Bilder, bis die Aenderung wirklich drin steckt -
// die ersten werden deshalb verworfen. Ohne das zeigt Bild N noch die
// Orientierung von Bild N-1, und man vergleicht Aepfel mit Birnen.
static void snap(int vflip, int hmirror) {
    sensor_t* s = esp_camera_sensor_get();
    if (!s) { Serial.println(F("--- kein Sensor-Handle ---")); return; }
    s->set_vflip(s, vflip);
    s->set_hmirror(s, hmirror);
    delay(300);
    for (int i = 0; i < 3; i++) {
        camera_fb_t* junk = esp_camera_fb_get();
        if (junk) esp_camera_fb_return(junk);
    }
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) { Serial.println(F("--- kein Puffer ---")); return; }
    Serial.printf("---SNAP vflip=%d hmirror=%d len=%u---\n",
                  vflip, hmirror, (unsigned)fb->len);
    printBase64(fb->buf, fb->len);
    Serial.println(F("---END---"));
    esp_camera_fb_return(fb);
}

void setup() {
    Serial.begin(115200);
    delay(2000);
    Serial.println();
    Serial.println(F("=== Kamera-Schnappschuss ==="));
    camera_config_t cfg;
    fillConfig(cfg);
    const esp_err_t err = esp_camera_init(&cfg);
    if (err != ESP_OK) {
        Serial.printf("Kamera-Init fehlgeschlagen: 0x%x (%s)\n",
                      err, esp_err_to_name(err));
        return;
    }
    Serial.println(F("Kamera bereit."));
}

void loop() {
    Serial.println(F("---SWEEP---"));
    snap(1, 0);   // CAM_VFLIP=1, CAM_HMIRROR=0 -> die aktuelle Firmware
    snap(0, 0);
    snap(0, 1);
    snap(1, 1);
    Serial.println(F("---SWEEPEND---"));
    delay(8000);
}

#endif
