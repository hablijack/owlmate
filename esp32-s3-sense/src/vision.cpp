#include "vision.h"
#include "config.h"

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

// Siehe include/vision.h fuer das Warum. Hier steht nur, wie.

#if FACE_DETECTION_ENABLED

namespace vision {
namespace {

// Das veroeffentlichte Ergebnis. Geschrieben ausschliesslich von task()
// (Kern 0), gelesen ausschliesslich von latest() (Kern 1) - beides unter mux.
FaceResult_t published = {};
bool running = true;

// portMUX ist die kernuebergreifende Sperre der ESP32-Portierung: sie nimmt
// einen Spinlock UND sperrt die Interrupts des eigenen Kerns. Deshalb darf
// darin nur kopiert werden, nichts gerechnet und erst recht nichts gedruckt.
// Eine FreeRTOS-Mutex waere hier falsch: sie kann den Renderkern blockieren,
// und genau dessen Bildrate ist der Zweck der ganzen Uebung.
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;

TaskHandle_t taskHandle = nullptr;

void task(void*) {
    for (;;) {
        portENTER_CRITICAL(&mux);
        const bool enabled = running;
        portEXIT_CRITICAL(&mux);

        if (!enabled) {
            vTaskDelay(pdMS_TO_TICKS(VISION_PAUSED_POLL_MS));
            continue;
        }

        FaceResult_t result;
        FaceDetector_Detect(&result);

        // Was von diesem Durchlauf noch fehlt: wie viel Stapel die Aufgabe
        // wirklich gebraucht hat. Das ist die einzige NEUE Ressourcenfrage,
        // die dieser Umzug aufwirft - esp-dl lief vorher auf dem Loop-Stapel
        // (CONFIG_ARDUINO_LOOP_STACK_SIZE=8192) und niemand weiss, wie knapp
        // das war. Deshalb steht der Wert dauerhaft in der Telemetrie und
        // nicht nur einmal in einem Sitzungsprotokoll.
        //
        // ACHTUNG: die ESP-IDF-Portierung rechnet hier in BYTE, nicht in
        // Worten wie das originale FreeRTOS. Nicht mit sizeof(StackType_t)
        // multiplizieren.
        result.stack_free = (uint32_t)uxTaskGetStackHighWaterMark(nullptr);

        // Unter der Sperre nochmal pruefen: waehrend der 48-114 ms Inferenz kann
        // der UPDATE-Modus begonnen haben. Ohne diese zweite Pruefung landete
        // ein Treffer von VOR dem Abschalten noch danach im Ergebnis.
        portENTER_CRITICAL(&mux);
        if (running) published = result;
        portEXIT_CRITICAL(&mux);

        // Mindestabstand zwischen zwei Durchlaeufen (FACE_DETECT_INTERVAL_MS).
        // MINDESTENS ein Tick, auch wenn der Wert 0 ist: vTaskDelay(0) ist nur
        // ein Yield an gleich hohe Prioritaeten und laesst die Idle-Aufgabe
        // NICHT laufen - dann verhungert IDLE0 und der Task-Watchdog schreibt
        // seine Klage in den NDJSON-Strom, den der RPi parst.
        const TickType_t gap = pdMS_TO_TICKS(FACE_DETECT_INTERVAL_MS);
        vTaskDelay(gap > 0 ? gap : 1);
    }
}

}  // namespace

bool begin() {
    if (taskHandle) return true;
    // Kern 0. Der Arduino-Loop laeuft auf Kern 1
    // (CONFIG_ARDUINO_RUNNING_CORE=1, siehe sdkconfig.xiao_esp32s3), also ist
    // 0 der freie. Prioritaet 1 = dieselbe wie der Loop: hoeher braucht es
    // nicht, und niedriger wuerde von den WLAN-Aufgaben (22/23) im
    // UPDATE-Modus ohnehin verdraengt - dort ist die Erkennung aber aus.
    const BaseType_t ok = xTaskCreatePinnedToCore(
        task, "vision", VISION_TASK_STACK, nullptr, VISION_TASK_PRIORITY,
        &taskHandle, 0);
    if (ok != pdPASS) {
        taskHandle = nullptr;
        return false;
    }
    return true;
}

void setEnabled(bool enabled) {
    portENTER_CRITICAL(&mux);
    running = enabled;
    if (!enabled) published = FaceResult_t{};
    portEXIT_CRITICAL(&mux);
}

void latest(FaceResult_t& out) {
    portENTER_CRITICAL(&mux);
    out = published;
    portEXIT_CRITICAL(&mux);
}

}  // namespace vision

#else  // !FACE_DETECTION_ENABLED

namespace vision {
bool begin() { return false; }
void setEnabled(bool) {}
void latest(FaceResult_t& out) { out = FaceResult_t{}; }
}  // namespace vision

#endif
