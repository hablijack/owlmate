#include "protocol.h"

#include <WiFi.h>
#include <ArduinoJson.h>
#include <esp_heap_caps.h>
#include "behavior.h"
#include "config.h"

// ============================================================================
// The NDJSON wire protocol. See protocol.h for the contract this holds up.
// ============================================================================

namespace {

// Verworfene Zeilen, seit dem Boot. Wird in der Telemetrie mitgeschickt, denn
// ein stilles Verwerfen waere genau die Sorte unsichtbarer Ausfall, gegen die
// vibration.pulses und face.attempts existieren: der RPi saehe eine Luecke und
// koennte nicht unterscheiden, ob die Eule nichts gesendet hat oder ob er
// selbst zu langsam war.
static uint32_t txDropped = 0;

// One JSON object, one line. Every reply in this file ends this way; the two
// lines were copy-pasted 8 times inside handleCommand() and gained a copy with
// every command added, which is what this helper stops.
//
// SCHREIBT NIE BLOCKIEREND. Das ist der Kern und nicht eine Optimierung: diese
// Funktion laeuft auf Kern 1, in derselben Schleife, die die Augen zeichnet.
// Mit der Voreinstellung der Arduino-Portierung (256 Byte Ring, 100 ms
// tx_timeout, bis zu 20 Wiederholungen) hielt EIN Telemetrieframe die Schleife
// bis zu 2 s an, sobald der Leser am anderen Ende nicht mitkam - auf Hardware
// gemessen 2026-08-31: loop_max_ms 2256 und 4023, loop_hz 3,3. Die Eule stand
// dann still, weil der Raspberry Pi beschaeftigt war. Das darf nicht sein: die
// Telemetrie ist ein Nebenprodukt, das Verhalten ist die Aufgabe.
//
// Zwei Teile, und beide werden gebraucht:
//   1. availableForWrite() >= Laenge  ->  nur schreiben, wenn die GANZE Zeile
//      in den Ring passt. Sonst verwerfen. Eine halb geschriebene NDJSON-Zeile
//      ist schlimmer als keine: der RPi meldet dafuer einen Parse-Fehler.
//   2. SERIAL_TX_TIMEOUT_MS 0 -> selbst wenn Punkt 1 sich irrte, wartet der
//      Schreibvorgang nicht.
//
// Ein Frame zu verwerfen ist unbedenklich: Telemetrie ist eine Momentaufnahme
// alle 500 ms, keine Ereignisliste. Die kumulativen Zaehler darin (face.total,
// vibration.pulses) ueberleben eine Luecke, gerade weil sie kumulativ sind.
//
// EIN Schreibvorgang, nicht println(): println() schickt die Zeile und das
// "\r\n" als ZWEI Aufrufe, also zwei Gelegenheiten zu blockieren und zwei, an
// denen eine Zeile auseinandergerissen werden kann.
void sendJson(const JsonDocument& doc) {
    String out;
    serializeJson(doc, out);
    out += '\n';

    const size_t len = out.length();
    if ((size_t)Serial.availableForWrite() < len) {
        txDropped++;
        return;
    }
    Serial.write((const uint8_t*)out.c_str(), len);
}

// Ack carrying nothing but its type.
void sendAck(const char* type) {
    JsonDocument doc;
    doc["type"] = type;
    sendJson(doc);
}

// Map an expression name (from the RPi) to an EyeExpression. Backed by the
// single NAMES table in Eyes.cpp, so adding a mood there makes it addressable
// over the protocol automatically. An unknown name falls back to NEUTRAL rather
// than being rejected, matching the long-standing behaviour of this command.
EyeExpression parseExpression(const char* name) {
    EyeExpression e;
    return Eyes::parseName(name, e) ? e : EyeExpression::NEUTRAL;
}

}  // namespace

namespace protocol {

void handleCommand(const char* json) {
    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, json);

    if (error) {
        JsonDocument err;
        err["type"] = "error";
        err["msg"] = "invalid_json";
        sendJson(err);
        return;
    }

    const char* type = doc["type"];
    if (!type) return;

    // Update mode is local-only: while the SoftAP is up the supervisor
    // can't drive the owl (heartbeat still answers for liveness).
    if (behavior::current() == State::UPDATE && strcmp(type, "heartbeat") != 0) {
        return;
    }

    if (strcmp(type, "expression") == 0) {
        // Temporary expression override from the supervisor. It takes
        // precedence over the state-driven expression until it expires, but
        // it never changes the state machine itself.
        const char* expr = doc["value"];
        behavior::overrideExpression(parseExpression(expr));

        JsonDocument resp;
        resp["type"] = "expression_ack";
        resp["value"] = expr;
        sendJson(resp);

    } else if (strcmp(type, "servo") == 0) {
        uint8_t ch = doc["channel"];
        float angle = doc["angle"];
        servos.setAngle(ch, angle);

        JsonDocument resp;
        resp["type"] = "servo_ack";
        resp["channel"] = ch;
        resp["angle"] = angle;
        sendJson(resp);

    } else if (strcmp(type, "gaze") == 0) {
        // Temporary gaze override from the supervisor (manual aim / testing).
        // Deliberately unacknowledged, as it always has been.
        behavior::overrideGaze(doc["x"], doc["y"]);

    } else if (strcmp(type, "nav") == 0) {
        // Persistent navigation command from the supervisor. Unlike the 3s
        // expression/gaze overrides, this HOLDS: active=true enters/keeps the
        // NAVIGATING state and points the head at `angle` until an active=false
        // arrives (or the firmware's own timeout fires). The RPi recomputes the
        // bearing from live GPS + heading and re-sends the angle each refresh,
        // so the head tracks the destination.
        float angle = doc["angle"];
        bool active = doc["active"] | false;
        behavior::setNavTarget(angle, active);

        JsonDocument resp;
        resp["type"] = "nav_ack";
        resp["active"] = active;
        resp["angle"] = angle;
        sendJson(resp);

    } else if (strcmp(type, "sleep") == 0) {
        // Policy command: put the owl to sleep.
        behavior::transitionTo(State::SLEEPING);
        sendAck("sleep_ack");

    } else if (strcmp(type, "wake") == 0) {
        // Policy command: wake the owl (only meaningful while sleeping).
        if (behavior::current() == State::SLEEPING) {
            behavior::transitionTo(State::IDLE);
        }
        sendAck("wake_ack");

    } else if (strcmp(type, "blink") == 0) {
        uint8_t speed = doc["speed"] | 3;
        eyes.blink(speed);
        sendAck("blink_ack");

    } else if (strcmp(type, "heartbeat") == 0) {
        JsonDocument resp;
        resp["type"] = "heartbeat_ack";
        resp["state"] = stateToString(behavior::current());
        sendJson(resp);
    }
}

void sendTelemetry() {
    JsonDocument doc;

    doc["type"] = "telemetry";
    doc["state"] = stateToString(behavior::current());
    doc["uptime"] = millis();
    doc["loop_hz"] = loopHz;
    // Laengste EINZELNE Runde des letzten Fensters (ms). Siehe owl.h: der
    // Mittelwert daneben kann ein Stocken nicht zeigen, diese Zahl schon - und
    // ihre GROESSE benennt den Verursacher (~250 ms = ein I2C-Timeout, bis
    // ~2000 ms = ein nicht mehr leergelesener USB-CDC-Port).
    doc["loop_max_ms"] = loopMaxMs;
    // Seit dem Boot verworfene Zeilen, weil der Leser am USB-Port nicht mitkam.
    // Steigt = der RPi (oder das Terminal) haengt; die Eule laeuft weiter, was
    // der ganze Zweck ist. Bleibt es 0, war der Port nie der Engpass.
    doc["tx_dropped"] = txDropped;
    doc["fw"] = FW_VERSION;

    // Haldenstand. Gegenstueck zu face.stack_free: der Stapel der
    // Erkennungsaufgabe wird seit dem Umzug auf Kern 0 dauerhaft gemeldet, die
    // Halde bisher gar nicht - und damit war "wird ueber die Laufzeit etwas
    // voll?" auf der Hardware schlicht nicht pruefbar.
    //
    // free UND largest, denn erst das PAAR beantwortet die Frage:
    //   free faellt                  -> ein Leck
    //   free bleibt, largest faellt  -> FRAGMENTIERUNG, und nur so zu sehen
    //   min faellt, free erholt sich -> es gab eine Spitze, sie ging vorbei
    //
    // INTERNAL getrennt vom PSRAM, weil beide unterschiedlich beansprucht
    // werden: JSON, String und die Knoten von esp-dl landen intern (alles unter
    // CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL = 16 KB), die Bildpuffer, der
    // Ausschnittpuffer und die Augen-Framebuffer im PSRAM. Ein gemeinsamer Wert
    // wuerde die interne Halde im Rauschen der 8 MB PSRAM verstecken.
    //
    // ACHTUNG, ehrlicher Preis: diese fuenf Felder verlaengern die
    // Telemetriezeile um gut 100 Byte. Falls der USB-CDC-Gegendruck die Ursache
    // ist, macht die Messung ihn also minimal SCHLIMMER - der Ringpuffer fasst
    // 256 Byte, die Zeile lag schon vorher weit darueber. Trotzdem richtig
    // herum: ohne die Felder ist die Vermutung gar nicht pruefbar.
    doc["heap"]["free"] = (uint32_t)heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    doc["heap"]["min"] = (uint32_t)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL);
    doc["heap"]["largest"] = (uint32_t)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL);
    doc["heap"]["psram_free"] = (uint32_t)heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    doc["heap"]["psram_largest"] = (uint32_t)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM);

    // IMU data
    ImuData imu = sensors.getImu();
    if (sensors.isImuReady()) {
        doc["imu"]["pitch"] = imu.pitch;
        doc["imu"]["roll"] = imu.roll;
        doc["imu"]["yaw"] = imu.yaw;
        doc["imu"]["calibrated"] = imu.isCalibrated;
        // Calibration progress and the two reasons `calibrated` can be false.
        // The three 0..3 counters the BNO055 sent (sys/gyro/accel) are GONE:
        // the LSM303AGR has no gyro and no fusion engine, so those numbers had
        // no referent. Do not synthesise replacements -- a number that looks
        // like a measurement and is not one is this project's most expensive
        // error class.
        //
        //   axes    0..3, magnetometer axes with enough span THIS RUN. The
        //           progress bar of the calibration turn. A level full turn
        //           reaches 2, only tumbling the owl reaches 3. May legitimately
        //           be 0 while `calibrated` is true -- that is a fresh boot with
        //           offsets restored from flash.
        //   heading_ok  the current geometry yields a usable heading at all
        //           (beak not near-vertical, field magnitude plausible).
        //   restored  hard-iron offsets came back from NVS at boot.
        doc["imu"]["cal"]["axes"] = imu.magAxes;
        doc["imu"]["cal"]["heading_ok"] = imu.headingOk;
        doc["imu"]["cal"]["restored"] = imu.calRestored;
    }

    // GPS data
    GpsData gps = sensors.getGps();
    if (sensors.isGpsReady()) {
        doc["gps"]["valid"] = gps.valid;
        doc["gps"]["latitude"] = gps.latitude;
        doc["gps"]["longitude"] = gps.longitude;
        doc["gps"]["altitude"] = gps.altitude;
        doc["gps"]["satellites"] = gps.satellites;
    }

    // Vibration
    VibrationData vib = sensors.getVibration();
    doc["vibration"]["detected"] = vib.detected;
    doc["vibration"]["count"] = vib.count;
    // Rohe Flankenzahl seit Boot (siehe Sensors.h): macht sichtbar, ob am
    // Sensorpin ueberhaupt etwas passiert, unabhaengig von der Auswertung.
    doc["vibration"]["pulses"] = sensors.vibrationPulseTotal();

    // Navigation status (present only while the owl is NAVIGATING). Lets the
    // RPi confirm the head is actually being held at the requested angle.
    if (behavior::current() == State::NAVIGATING) {
        doc["navigation"]["active"] = true;
        doc["navigation"]["angle"] = behavior::navAngle();
    }

    // Update mode (SoftAP + OTA)
    if (behavior::current() == State::UPDATE) {
        doc["update"]["ssid"] = UPDATE_AP_SSID;
        doc["update"]["password"] = UPDATE_AP_PASSWORD;
        doc["update"]["ip"] = WiFi.softAPIP().toString();
        doc["update"]["url"] = String("http://") + WiFi.softAPIP().toString() + "/update";
    }

    // Servo positions (array: left_ear, right_ear, head, left_wing, right_wing)
    doc["servos"][0] = servos.getAngle(CH_LEFT_EAR);
    doc["servos"][1] = servos.getAngle(CH_RIGHT_EAR);
    doc["servos"][2] = servos.getAngle(CH_HEAD);
    doc["servos"][3] = servos.getAngle(CH_LEFT_WING);
    doc["servos"][4] = servos.getAngle(CH_RIGHT_WING);

    // Face detection state
    doc["face"]["detected"] = faceResult.detected;
    doc["face"]["x"] = faceResult.x;
    doc["face"]["y"] = faceResult.y;
    doc["face"]["w"] = faceResult.w;
    doc["face"]["h"] = faceResult.h;
    doc["face"]["confidence"] = faceResult.confidence;
    doc["face"]["gaze_x"] = faceResult.gaze_x;
    doc["face"]["gaze_y"] = faceResult.gaze_y;
    // Kumulativ (siehe FaceDetector.h): macht sporadische Erkennung sichtbar,
    // die ein Momentanwert von "detected" verschluckt.
    doc["face"]["total"] = faceResult.total;
    doc["face"]["attempts"] = faceResult.attempts;
    // Laufzeit des letzten Durchlaufs, in zwei Posten: Warten auf die Kamera
    // gegen Rechnen. Ohne die Aufteilung ist nicht entscheidbar, ob eine
    // hoehere Erkennungsrate ueberhaupt erreichbar ist.
    doc["face"]["capture_ms"] = faceResult.capture_ms;
    doc["face"]["infer_ms"] = faceResult.infer_ms;
    // Stapel-Tiefstand der Erkennungsaufgabe auf Kern 0 (Byte). Ein Ueberlauf
    // dort waere ein sporadischer Absturz mitten in der Inferenz; siehe
    // VISION_TASK_STACK.
    doc["face"]["stack_free"] = faceResult.stack_free;

    // Eye expression (name comes from the same table parseExpression() uses)
    doc["eye"] = Eyes::nameOf(eyes.getCurrentExpression());

    sendJson(doc);
}

void poll() {
    static String inputBuffer;

    while (Serial.available()) {
        char c = Serial.read();

        if (c == '\n' || c == '\r') {
            if (inputBuffer.length() > 0) {
                handleCommand(inputBuffer.c_str());
                inputBuffer.clear();
            }
        } else {
            inputBuffer += c;
            // Safety: prevent buffer overflow. Log the drop so a truncated
            // or malformed frame is diagnosable instead of failing silently.
            if (inputBuffer.length() > 255) {
                Serial.println(F("{\"type\":\"error\",\"msg\":\"line_too_long\"}"));
                inputBuffer.clear();
            }
        }
    }
}

}  // namespace protocol
