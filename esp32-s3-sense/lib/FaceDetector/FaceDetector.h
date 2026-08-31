#pragma once

#include <stdint.h>
#include <stdbool.h>

// Face detection result structure
typedef struct {
    bool detected;
    int16_t x;        // Top-left X position (0-319 for QVGA)
    int16_t y;        // Top-left Y position (0-239 for QVGA)
    uint16_t w;       // Width of face bounding box
    uint16_t h;       // Height of face bounding box
    float confidence; // Detection confidence (0.0-1.0)
    float gaze_x;     // Gaze offset X (-1.0 to 1.0)
    float gaze_y;     // Gaze offset Y (-1.0 to 1.0)
    // Kumulative Zahl erkannter Gesichter seit dem Boot. Absichtlich in der
    // Telemetrie: bei sporadischer Erkennung ist ein Momentanwert von
    // "detected" wertlos, ein steigender Zaehler dagegen eindeutig. Konstant
    // bei einem Gesicht vor der Kamera = Erkennung arbeitet nicht.
    uint32_t total;
    // Kumulative Zahl der DURCHLAEUFE seit dem Boot, Gegenstueck zu total.
    // total allein sagt nicht, ob eine niedrige Trefferzahl an der Erkennung
    // liegt oder daran, dass sie schlicht selten laeuft: die Hauptschleife
    // rendert die Augen und schlaeft, und beides drueckt die Rate unter das,
    // was FACE_DETECT_INTERVAL_MS verspricht. Erst total/attempts trennt
    // "erkennt schlecht" von "kommt kaum dran". Siehe SPEC-008.
    uint32_t attempts;
    // Wie lange dieser Durchlauf gedauert hat, aufgeteilt in die zwei Posten,
    // die man nicht gegeneinander abwaegen kann, solange man nur die Summe
    // kennt: capture_ms ist die WARTEZEIT auf ein Kamerabild, infer_ms die
    // Rechenzeit (Ausschnitt kopieren + run()).
    //
    // Auf Hardware gemessen 2026-08-31, und beide Zahlen waren eine
    // Ueberraschung: capture_ms ist in JEDER Abtastung 0 - der Treiber haelt
    // mit fb_count=2 immer ein Bild bereit, gewartet wird nie. Der Durchlauf
    // ist also zu 100 % Rechenzeit, und die betraegt 48 ms ohne Gesicht.
    //
    // MIT Gesicht haengt sie an der Gesichtsgroesse IM BILD, nicht an einer
    // festen Zahl: je groesser, desto mehr Kandidaten ueberleben die
    // Vorschlagsstufe und erreichen die Verfeinerung. Zwei Messungen am
    // 2026-08-31, dieselbe Eule: 48-91 ms (Mittel 66) bei kleinerem Gesicht,
    // und 58-114 ms (Mittel 81) bei 31 % Bildanteil mit 100 % Trefferquote.
    // 48-114 ms ist der beobachtete RAHMEN, keine Obergrenze - die erste
    // Messung wurde als Grenze notiert und war es nicht.
    //
    // Die frueher notierten ~170-200 ms waren
    // nie direkt gemessen, sondern aus loop_hz erschlossen - und darin steckte
    // das Augenzeichnen mit drin. Siehe specs/009-face-detection.spec.
    uint16_t capture_ms;
    uint16_t infer_ms;
    // Freier Stapel der Erkennungsaufgabe (Byte, Tiefstand seit dem Start).
    // Die Inferenz lief bis 2026-08-31 auf dem Arduino-Loop-Stapel; seit sie
    // eine eigene Aufgabe auf Kern 0 hat, ist ihr Stapelbedarf eine offene
    // Frage, die still mit einem Ueberlauf endet. Siehe VISION_TASK_STACK.
    // 0, solange noch kein Durchlauf fertig ist.
    uint32_t stack_free;
} FaceResult_t;

// Initialize face detection module
// Returns true if initialization successful
bool FaceDetector_Init(void);

// Run face detection on current camera frame
// Populates result with detection data
void FaceDetector_Detect(FaceResult_t *result);

// Deinitialize face detection module
void FaceDetector_Deinit(void);
