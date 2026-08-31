#include "protocol.h"

#include <WiFi.h>
#include <ArduinoJson.h>
#include "behavior.h"
#include "config.h"

// ============================================================================
// The NDJSON wire protocol. See protocol.h for the contract this holds up.
// ============================================================================

namespace {

// One JSON object, one line. Every reply in this file ends this way; the two
// lines were copy-pasted 8 times inside handleCommand() and gained a copy with
// every command added, which is what this helper stops.
void sendJson(const JsonDocument& doc) {
    String out;
    serializeJson(doc, out);
    Serial.println(out);
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
    doc["fw"] = FW_VERSION;

    // IMU data
    ImuData imu = sensors.getImu();
    if (sensors.isImuReady()) {
        doc["imu"]["pitch"] = imu.pitch;
        doc["imu"]["roll"] = imu.roll;
        doc["imu"]["yaw"] = imu.yaw;
        doc["imu"]["calibrated"] = imu.isCalibrated;
        // Per-sensor calibration progress (0..3 each). yaw is not a usable
        // compass bearing until mag reaches 3 -- wave the owl in a figure 8.
        doc["imu"]["cal"]["sys"] = imu.calSys;
        doc["imu"]["cal"]["gyro"] = imu.calGyro;
        doc["imu"]["cal"]["accel"] = imu.calAccel;
        doc["imu"]["cal"]["mag"] = imu.calMag;
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
