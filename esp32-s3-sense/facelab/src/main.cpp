// ============================================================================
// facelab Meilenstein 2: Gesichtserkennung mit esp-dl v3
//
// Die API von esp-dl v3 hat mit der alten (HumanFaceDetectMSR01 aus Core 2.x)
// nichts mehr zu tun. Aus den echten Headern in managed_components/ abgelesen:
//
//   HumanFaceDetect(model_type_t, bool lazy_load)
//   std::list<dl::detect::result_t>& run(const dl::image::img_t&)
//
//   dl::image::img_t   { void* data; uint16_t width, height; pix_type_t pix_type; }
//   dl::detect::result_t { int category; float score;
//                          std::vector<int> box;      // lx, ly, rx, ry
//                          std::vector<int> keypoint; }
//
// Modell wird explizit gewaehlt (MSRMNP_S8_V1) statt ueber den Kconfig-Default,
// damit der Build nicht von einer Konfiguration abhaengt, die wir nicht setzen.
// ============================================================================
#include <Arduino.h>
#include <esp_heap_caps.h>
#include <esp_psram.h>

#include "esp_camera.h"
#include "human_face_detect.hpp"

// AUF HARDWARE ERMITTELT (2026-08-26), nicht geraten:
//
// Byte-Reihenfolge: BE 62 Treffer gegen LE 2 -> Big Endian ist richtig.
//
// ORIENTIERUNG: die Kamera ist VERTIKAL GESPIEGELT eingebaut. Ueber alle vier
// Lagen gemessen: normal 0 Treffer, vflip 57, hmirror 2, 180 Grad 10. Ohne
// vflip kann diese Erkennung PRINZIPIELL nicht funktionieren - die Modelle
// finden nur aufrechte Gesichter, ein gedrehtes Gesicht ist fuer sie keines.
// Das war die ganze Ursache; Beleuchtung und Schwellwerte waren nie das
// Problem (die Scores liegen bei 0.58-1.00, meist ueber 0.9).
static const dl::image::pix_type_t PIX = dl::image::DL_IMAGE_PIX_TYPE_RGB565BE;
static const int CAM_VFLIP = 1;
static const int CAM_HMIRROR = 0;

static HumanFaceDetect *detector = nullptr;

static void fillCameraConfig(camera_config_t &c) {
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
    c.pixel_format = PIXFORMAT_RGB565;
    c.frame_size = FRAMESIZE_QVGA;      // 320x240
    c.jpeg_quality = 12;
    c.fb_count = 2;                     // doppelt gepuffert, PSRAM ist da
    c.fb_location = CAMERA_FB_IN_PSRAM;
    c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
}

void setup() {
    Serial.begin(115200);
    uint32_t t0 = millis();
    while (!Serial && millis() - t0 < 3000) delay(10);
    delay(300);

    Serial.println();
    Serial.println(F("=== facelab M2: Gesichtserkennung mit esp-dl v3 ==="));
    Serial.printf(" PSRAM: %s, %u Byte frei\n",
                  esp_psram_is_initialized() ? "OK" : "FEHLT",
                  (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));

    Serial.println(F("\n[1] Kamera"));
    camera_config_t cfg;
    fillCameraConfig(cfg);
    const esp_err_t err = esp_camera_init(&cfg);
    if (err != ESP_OK) {
        Serial.printf("   FEHLGESCHLAGEN: 0x%x (%s)\n", err, esp_err_to_name(err));
        while (true) delay(1000);
    }
    sensor_t *s = esp_camera_sensor_get();
    Serial.printf("   OK, Sensor-PID 0x%04X%s\n", s ? s->id.PID : 0,
                  (s && s->id.PID == 0x3660) ? " (OV3660)" : "");

    Serial.println(F("\n[2] Modell laden (MSRMNP_S8_V1)"));
    const uint32_t heapBefore = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    const uint32_t tLoad = millis();
    detector = new HumanFaceDetect(HumanFaceDetect::MSRMNP_S8_V1);
    if (!detector) {
        Serial.println(F("   Allokation fehlgeschlagen"));
        while (true) delay(1000);
    }
    Serial.printf("   geladen in %lu ms, PSRAM-Verbrauch %ld Byte\n",
                  (unsigned long)(millis() - tLoad),
                  (long)heapBefore - (long)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    Serial.println(F("   (0 ms/0 Byte ist normal: lazy_load laedt beim ersten run())"));

    // Schwellwerte bleiben auf dem Standard 0.5: die gemessenen Scores liegen
    // bei 0.58-1.00. Ein Absenken war beim Suchen hilfreich, ist im Betrieb
    // aber nur eine Einladung fuer Fehlerkennungen.

    // Einbaulage der Kamera korrigieren - siehe Kommentar oben bei CAM_VFLIP.
    if (s) {
        s->set_vflip(s, CAM_VFLIP);
        s->set_hmirror(s, CAM_HMIRROR);
        delay(120);
        Serial.printf("\n[3] Bildlage korrigiert: vflip=%d hmirror=%d\n",
                      CAM_VFLIP, CAM_HMIRROR);
    }
    Serial.println(F("    Erkennung laeuft. Halte ein Gesicht vor die Kamera."));
    Serial.println(F("--------------------------------------------------------"));
}

void loop() {
    static uint32_t frames = 0, withFace = 0, sumInfer = 0, lastReport = 0;

    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) { Serial.println(F("   kein Bildpuffer")); delay(200); return; }

    dl::image::img_t img = {};
    img.data = fb->buf;
    img.width = fb->width;
    img.height = fb->height;
    img.pix_type = PIX;

    const uint32_t t0 = millis();
    auto &results = detector->run(img);
    sumInfer += millis() - t0;
    frames++;

    if (!results.empty()) {
        withFace++;
        // Das groesste Gesicht gewinnt - so macht es auch die echte Firmware.
        const dl::detect::result_t *best = nullptr;
        int bestArea = 0;
        for (auto &r : results) {
            const int a = (r.box[2] - r.box[0]) * (r.box[3] - r.box[1]);
            if (a > bestArea) { bestArea = a; best = &r; }
        }
        const int cx = (best->box[0] + best->box[2]) / 2;
        const int cy = (best->box[1] + best->box[3]) / 2;
        // Blickrichtung -1..1 relativ zur Bildmitte, wie FaceResult_t sie fuehrt.
        const float gazeX = (float)(cx - fb->width / 2) / (fb->width / 2.0f);
        const float gazeY = (float)(cy - fb->height / 2) / (fb->height / 2.0f);
        Serial.printf("   Gesicht  Score %.2f  %dx%d @ %d,%d  Blick %+.2f/%+.2f"
                      "  (%d im Bild)\n",
                      best->score, best->box[2] - best->box[0],
                      best->box[3] - best->box[1], cx, cy, gazeX, gazeY,
                      (int)results.size());
    }

    if (millis() - lastReport > 5000) {
        lastReport = millis();
        Serial.printf("   [Bilanz] %lu Bilder, %lu mit Gesicht (%.0f %%), "
                      "Inferenz %.0f ms -> %.1f Bilder/s\n",
                      (unsigned long)frames, (unsigned long)withFace,
                      100.0 * withFace / frames, (float)sumInfer / frames,
                      1000.0f * frames / (float)sumInfer);
    }

    esp_camera_fb_return(fb);
}
