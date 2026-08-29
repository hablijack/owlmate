#include "FaceDetector.h"
#include "config.h"
#include <Arduino.h>

// ============================================================================
// Gesichtserkennung mit esp-dl v3.
//
// GESCHICHTE, damit niemand den alten Weg nochmal versucht: bis Arduino-Core
// 3.0.x brachte das SDK esp-dl v1 vorkompiliert mit, und diese Datei benutzte
// HumanFaceDetectMSR01 aus "human_face_detect_msr01.hpp". Ab Core 3.1 sind
// diese Bibliotheken NICHT MEHR in den vorkompilierten Arduino-Libs. Deshalb
// laeuft das Firmware-Env als "framework = arduino, espidf" (siehe
// platformio.ini) und holt esp-dl als verwaltete IDF-Komponente
// (src/idf_component.yml). Ein Downgrade des Cores kommt nicht in Frage, weil
// nur die neueren Versionen das oktale PSRAM initialisieren.
//
// Die v3-API hat mit v1 nichts gemeinsam: HumanFaceDetect statt
// HumanFaceDetectMSR01, run() liefert std::list<dl::detect::result_t>.
//
// Auf Hardware nachgewiesen (2026-08-26): 48 ms Inferenz, ~21 Bilder/s,
// Scores 0.58-1.00.
// ============================================================================

#if FACE_DETECTION_ENABLED

#include "esp_camera.h"
#include "human_face_detect.hpp"

static HumanFaceDetect *detector = NULL;
static bool initialized = false;

// Letztes erfolgreiches Ergebnis, damit ein Treffer FACE_HOLD_MS lang stehen
// bleibt und nicht zwischen zwei Telemetrieframes durchfaellt.
static FaceResult_t lastGood = {};
static uint32_t lastGoodAt = 0;
static uint32_t totalDetections = 0;
static uint32_t totalAttempts = 0;

// esp32-camera liefert RGB565 mit dem hohen Byte zuerst. Auf Hardware
// gemessen: BE 62 Treffer gegen LE 2.
static const dl::image::pix_type_t PIX_TYPE =
    dl::image::DL_IMAGE_PIX_TYPE_RGB565BE;

static void fillCameraConfig(camera_config_t &c) {
    c = {};
    c.pin_pwdn = CAM_PIN_PWDN;
    c.pin_reset = CAM_PIN_RESET;
    c.pin_xclk = CAM_PIN_XCLK;
    c.pin_sccb_sda = CAM_PIN_SIOD;
    c.pin_sccb_scl = CAM_PIN_SIOC;
    c.pin_d7 = CAM_PIN_D7; c.pin_d6 = CAM_PIN_D6;
    c.pin_d5 = CAM_PIN_D5; c.pin_d4 = CAM_PIN_D4;
    c.pin_d3 = CAM_PIN_D3; c.pin_d2 = CAM_PIN_D2;
    c.pin_d1 = CAM_PIN_D1; c.pin_d0 = CAM_PIN_D0;
    c.pin_vsync = CAM_PIN_VSYNC;
    c.pin_href = CAM_PIN_HREF;
    c.pin_pclk = CAM_PIN_PCLK;
    c.xclk_freq_hz = CAM_XCLK_FREQ_HZ;
    c.ledc_timer = LEDC_TIMER_0;
    c.ledc_channel = LEDC_CHANNEL_0;
    c.pixel_format = PIXFORMAT_RGB565;
    c.frame_size = FRAMESIZE_QVGA;        // 320x240
    c.jpeg_quality = 12;                  // ungenutzt bei RGB565
    c.fb_count = 2;                       // doppelt gepuffert, PSRAM ist da
    c.fb_location = CAMERA_FB_IN_PSRAM;
    c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
}

bool FaceDetector_Init(void) {
    if (initialized) return true;

    camera_config_t cfg;
    fillCameraConfig(cfg);
    const esp_err_t err = esp_camera_init(&cfg);
    if (err != ESP_OK) {
        Serial.printf("FaceDetector: Kamera-Init fehlgeschlagen: 0x%x (%s)\n",
                      err, esp_err_to_name(err));
        return false;
    }

    // EINBAULAGE KORRIGIEREN. Ohne das findet die Erkennung prinzipiell nichts:
    // die Modelle erkennen nur aufrechte Gesichter, und die Kamera sitzt
    // gespiegelt im Kopf. Siehe CAM_VFLIP in config.h.
    sensor_t *s = esp_camera_sensor_get();
    if (s) {
        s->set_vflip(s, CAM_VFLIP);
        s->set_hmirror(s, CAM_HMIRROR);
        Serial.printf("FaceDetector: Sensor PID 0x%04X, vflip=%d hmirror=%d\n",
                      s->id.PID, CAM_VFLIP, CAM_HMIRROR);
    }

    // lazy_load ist Standard: das Modell wird erst beim ersten run() geladen.
    // Der Konstruktor meldet deshalb 0 ms - das ist normal, kein Fehler.
    detector = new HumanFaceDetect(HumanFaceDetect::MSRMNP_S8_V1);
    if (!detector) {
        Serial.println(F("FaceDetector: Modell konnte nicht angelegt werden"));
        esp_camera_deinit();
        return false;
    }
    detector->set_score_thr(FACE_SCORE_THRESHOLD, 0);   // MSR, grob
    detector->set_score_thr(FACE_SCORE_THRESHOLD, 1);   // MNP, fein
    detector->set_nms_thr(FACE_NMS_THRESHOLD, 0);
    detector->set_nms_thr(FACE_NMS_THRESHOLD, 1);

    initialized = true;
    return true;
}

void FaceDetector_Detect(FaceResult_t *result) {
    if (!result) return;
    // Vorbelegen: ein fehlgeschlagener Durchlauf darf nie ein altes Gesicht
    // stehen lassen, sonst haengt die State-Machine in INTERACTING fest.
    *result = {};

    if (!initialized || !detector) return;

    // Ab hier laeuft ein echter Durchlauf - genau das zaehlt attempts.
    totalAttempts++;
    // Zaehler SOFORT setzen, nicht erst am Ende: der Ausstieg bei fehlendem
    // Puffer liegt davor, und ohne das meldet die Telemetrie nach einem
    // misslungenen Bildabruf total=0 - was wie ein Zaehlerreset aussieht und
    // genau die Frage unbeantwortbar macht, fuer die die Zaehler da sind.
    result->attempts = totalAttempts;
    result->total = totalDetections;

    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) return;

    dl::image::img_t img = {};
    img.data = fb->buf;
    img.width = fb->width;
    img.height = fb->height;
    img.pix_type = PIX_TYPE;

    auto &results = detector->run(img);

    // Groesstes Gesicht gewinnt: das ist im Zweifel das naechste, und dem soll
    // die Eule folgen.
    const dl::detect::result_t *best = NULL;
    int bestArea = 0;
    for (auto &r : results) {
        if (r.score < FACE_MIN_CONFIDENCE) continue;
        const int a = (r.box[2] - r.box[0]) * (r.box[3] - r.box[1]);
        if (a > bestArea) { bestArea = a; best = &r; }
    }

    if (best) {
        totalDetections++;
        result->detected = true;
        result->x = best->box[0];
        result->y = best->box[1];
        result->w = best->box[2] - best->box[0];
        result->h = best->box[3] - best->box[1];
        result->confidence = best->score;

        // Blickrichtung -1..1 relativ zur Bildmitte. Das ist der Wert, dem die
        // Augen folgen und der in der Telemetrie landet.
        const int cx = (best->box[0] + best->box[2]) / 2;
        const int cy = (best->box[1] + best->box[3]) / 2;
        // fb->width/height sind size_t, also VORZEICHENLOS. Ohne die Umwandlung
        // nach int rechnet C die Differenz vorzeichenlos, und sobald das Gesicht
        // LINKS der Mitte steht, laeuft sie auf ~2^32 ueber. setGaze() begrenzt
        // das auf +1.0 - die Augen starren dann hart nach rechts unten, statt zu
        // folgen. Auf Hardware beobachtet 2026-08-29: gemeldetes gaze_x
        // 2.684355e7 bei einem Gesicht 13 px links der Bildmitte.
        const int halfW = (int)fb->width / 2;
        const int halfH = (int)fb->height / 2;
        result->gaze_x = (float)(cx - halfW) / (float)halfW;
        result->gaze_y = (float)(cy - halfH) / (float)halfH;

        lastGood = *result;
        lastGoodAt = millis();
    } else if (lastGoodAt && millis() - lastGoodAt <= FACE_HOLD_MS) {
        // Kein Treffer in DIESEM Bild, aber vor kurzem einer: den letzten
        // stehen lassen. Sonst flackert das Ergebnis mit der Erkennungsrate
        // und die Telemetrie zeigt fast immer "nichts".
        *result = lastGood;
    }
    result->total = totalDetections;
    result->attempts = totalAttempts;

    esp_camera_fb_return(fb);
}

void FaceDetector_Deinit(void) {
    if (detector) { delete detector; detector = NULL; }
    if (initialized) { esp_camera_deinit(); initialized = false; }
}

#else  // !FACE_DETECTION_ENABLED

bool FaceDetector_Init(void) { return false; }
void FaceDetector_Detect(FaceResult_t *result) { if (result) *result = {}; }
void FaceDetector_Deinit(void) {}

#endif
