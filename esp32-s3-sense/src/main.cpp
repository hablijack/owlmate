#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <WiFi.h>
#include <WebServer.h>
#include <HTTPUpdateServer.h>
#include "config.h"
#include "GC9D01.h"
#include "Eyes.h"
#include "Sensors.h"
#include "ServoController.h"
#include "FaceDetector.h"
#include "esp_camera.h"
// NOTE: psramFound() comes from esp32-hal-psram.h, which is pulled in via
// Arduino.h -> esp32-hal.h. The old core's <esp_spiram.h> does not exist in
// ESP-IDF 5.x (pioarduino / core 3.x), so we no longer include it directly.
#include <ArduinoJson.h>

// ============================================================================
// Global instances
// ============================================================================
// Both eye displays share the SAME SPI SCK/MOSI (only DC/CS differ), so they
// must share ONE SPIClass. If each owned its own SPIClass and both called
// begin() on the same pins, the second begin() re-inits an already-configured
// SPI peripheral and hangs under ESP-IDF 5.x (pioarduino core 3.x). The shared
// bus is attached to both displays and initialised once in setup().
static SPIClass lcdSharedBus(FSPI);
static GC9D01 lcdLeft(LCD_DC_L, LCD_CS_L, LCD_RST);
static GC9D01 lcdRight(LCD_DC_R, LCD_CS_R, LCD_RST);
static Eyes eyes(lcdLeft, lcdRight);
static Sensors sensors;
static ServoController servos;
static WebServer webServer(80);
static HTTPUpdateServer httpUpdateServer;
static bool updateServerReady = false;

// ============================================================================
// Face detection state
// ============================================================================
static FaceResult_t faceResult = {0};

// ============================================================================
// State machine
// ============================================================================
enum class State {
    BOOT,
    IDLE,
    DETECTING,
    INTERACTING,
    SLEEPING,
    NAVIGATING,
    UPDATE,
    ERROR
};

static State currentState = State::BOOT;
static uint32_t lastTelemetry = 0;
static uint32_t lastFaceSeen = 0;
static uint32_t lastFaceDetect = 0;
// Schleifenzaehler fuer loop_hz. Die Hauptschleife rendert die Augen und
// schlaeft 16 ms; beides drueckt ihre Rate, und mit ihr die Zahl der
// Erkennungsdurchlaeufe. FACE_DETECT_INTERVAL_MS ist nur eine Obergrenze -
// was tatsaechlich ankommt, ist ohne Messung nicht zu sehen.
static uint32_t loopCount = 0;
static uint32_t lastLoopMeasure = 0;
static float loopHz = 0.0f;
static uint32_t updateModeSince = 0;
static uint32_t navSince = 0;
static float navTargetAngle = 0.0f;

// Temporary overrides from the RPi supervisor. These take precedence over the
// state-driven expression/gaze until they expire or the state changes. They
// never change the state itself.
static EyeExpression overrideExpr = EyeExpression::NEUTRAL;
static uint32_t overrideExprUntil = 0;
static bool overrideGazeActive = false;
static float overrideGazeX = 0.0f;
static float overrideGazeY = 0.0f;
static uint32_t overrideGazeUntil = 0;

// ============================================================================
// Display bring-up
//
// Both panels sit on ONE SPI bus (shared SCK/MOSI/RST, private CS/DC), so the
// order below is load-bearing:
//   1. begin the bus ONCE, with SS = -1 -- CS is driven in software by the
//      driver (see lib/GC9D01/GC9D01.h; relying on the peripheral's hardware
//      SS is what broke the two-panel case for so long),
//   2. attach BOTH panels before initialising either, so neither floats
//      selected while its sibling is being set up,
//   3. pulse the shared RST exactly once,
//   4. then init each panel.
// ============================================================================
static void bringUpDisplays(bool& leftOk, bool& rightOk) {
    lcdSharedBus.begin(LCD_SCK, -1, LCD_MOSI, -1);
    lcdLeft.attachBus(&lcdSharedBus);
    lcdRight.attachBus(&lcdSharedBus);
    lcdLeft.resetShared();
    leftOk = lcdLeft.begin();
    rightOk = lcdRight.begin();
}

// ============================================================================
// Forward declarations
// ============================================================================
const char* stateToString(State state);
void transitionTo(State newState);
void applyExpression(EyeExpression stateExpr);
void applyGaze(float stateGx, float stateGy);
void runHardwareCheck();

// ============================================================================
// Protocol handlers
// ============================================================================
// Map an expression name (from the RPi) to an EyeExpression.
// Name -> expression. Backed by the single NAMES table in Eyes.cpp, so adding a
// mood there makes it addressable over the protocol automatically. An unknown
// name falls back to NEUTRAL rather than being rejected, matching the previous
// behaviour of this command.
EyeExpression parseExpression(const char* name) {
    EyeExpression e;
    return Eyes::parseName(name, e) ? e : EyeExpression::NEUTRAL;
}


void handleCommand(const char* json) {
    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, json);

    if (error) {
        JsonDocument err;
        err["type"] = "error";
        err["msg"] = "invalid_json";
        String out;
        serializeJson(err, out);
        Serial.println(out);
        return;
    }

    const char* type = doc["type"];
    if (!type) return;

    // Reusable document for building acknowledgment replies.
    JsonDocument resp;
    String out;

    // Update mode is local-only: while the SoftAP is up the supervisor
    // can't drive the owl (heartbeat still answers for liveness).
    if (currentState == State::UPDATE && strcmp(type, "heartbeat") != 0) {
        return;
    }

    if (strcmp(type, "expression") == 0) {
        // Temporary expression override from the supervisor. It takes
        // precedence over the state-driven expression until it expires, but
        // it never changes the state machine itself.
        const char* expr = doc["value"];
        EyeExpression eyeExpr = parseExpression(expr);
        overrideExpr = eyeExpr;
        overrideExprUntil = millis() + EXPRESSION_OVERRIDE_MS;
        eyes.setExpression(eyeExpr);

        resp["type"] = "expression_ack";
        resp["value"] = expr;
        serializeJson(resp, out);
        Serial.println(out);

    } else if (strcmp(type, "servo") == 0) {
        uint8_t ch = doc["channel"];
        float angle = doc["angle"];
        servos.setAngle(ch, angle);
        resp["type"] = "servo_ack";
        resp["channel"] = ch;
        resp["angle"] = angle;
        serializeJson(resp, out);
        Serial.println(out);

    } else if (strcmp(type, "gaze") == 0) {
        // Temporary gaze override from the supervisor (manual aim / testing).
        overrideGazeX = doc["x"];
        overrideGazeY = doc["y"];
        overrideGazeActive = true;
        overrideGazeUntil = millis() + GAZE_OVERRIDE_MS;
        eyes.setGaze(overrideGazeX, overrideGazeY);

    } else if (strcmp(type, "nav") == 0) {
        // Persistent navigation command from the supervisor. Unlike the 3s
        // expression/gaze overrides, this HOLDS: active=true enters/keeps the
        // NAVIGATING state and points the head at `angle` until an active=false
        // arrives (or the firmware's own timeout fires). The RPi recomputes the
        // bearing from live GPS + heading and re-sends the angle each refresh,
        // so the head tracks the destination.
        float angle = doc["angle"];
        bool active = doc["active"] | false;
        if (active) {
            navTargetAngle = angle;
            if (currentState != State::NAVIGATING) {
                transitionTo(State::NAVIGATING);
            }
            navSince = millis();  // (re)arm the self-timeout on every update
            eyes.setGaze(0, 0);
            servos.setAngle(CH_HEAD, angle);
        } else {
            navTargetAngle = 0.0f;
            if (currentState == State::NAVIGATING) {
                transitionTo(State::IDLE);
            }
            eyes.setGaze(0, 0);
            servos.setAngle(CH_HEAD, 0);
        }
        resp["type"] = "nav_ack";
        resp["active"] = active;
        resp["angle"] = angle;
        serializeJson(resp, out);
        Serial.println(out);

    } else if (strcmp(type, "sleep") == 0) {
        // Policy command: put the owl to sleep.
        transitionTo(State::SLEEPING);
        resp["type"] = "sleep_ack";
        serializeJson(resp, out);
        Serial.println(out);

    } else if (strcmp(type, "wake") == 0) {
        // Policy command: wake the owl (only meaningful while sleeping).
        if (currentState == State::SLEEPING) {
            transitionTo(State::IDLE);
        }
        resp["type"] = "wake_ack";
        serializeJson(resp, out);
        Serial.println(out);

    } else if (strcmp(type, "blink") == 0) {
        uint8_t speed = doc["speed"] | 3;
        eyes.blink(speed);
        resp["type"] = "blink_ack";
        serializeJson(resp, out);
        Serial.println(out);

    } else if (strcmp(type, "heartbeat") == 0) {
        resp["type"] = "heartbeat_ack";
        resp["state"] = stateToString(currentState);
        serializeJson(resp, out);
        Serial.println(out);
    }
}

const char* stateToString(State state) {
    switch (state) {
        case State::BOOT: return "boot";
        case State::IDLE: return "idle";
        case State::DETECTING: return "detecting";
        case State::INTERACTING: return "interacting";
        case State::SLEEPING: return "sleeping";
        case State::NAVIGATING: return "navigating";
        case State::UPDATE: return "update";
        case State::ERROR: return "error";
        default: return "unknown";
    }
}

// ============================================================================
// Telemetry sender
// ============================================================================
void sendTelemetry() {
    JsonDocument doc;

    doc["type"] = "telemetry";
    doc["state"] = stateToString(currentState);
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
    if (currentState == State::NAVIGATING) {
        doc["navigation"]["active"] = true;
        doc["navigation"]["angle"] = navTargetAngle;
    }

    // Update mode (SoftAP + OTA)
    if (currentState == State::UPDATE) {
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

    String output;
    serializeJson(doc, output);
    Serial.println(output);
}

// ============================================================================
// State machine helpers
// ============================================================================
// Change state and clear any active supervisor overrides so the new state's
// expression/gaze take effect immediately.
void transitionTo(State newState) {
    if (newState == currentState) return;
    currentState = newState;
    overrideExprUntil = 0;
    overrideGazeActive = false;
}

// Apply the expression for the current state, honoring an active supervisor
// expression override.
void applyExpression(EyeExpression stateExpr) {
    if (millis() < overrideExprUntil) {
        eyes.setExpression(overrideExpr);
    } else {
        eyes.setExpression(stateExpr);
    }
}

// Apply the gaze for the current state, honoring an active supervisor gaze
// override.
void applyGaze(float stateGx, float stateGy) {
    if (overrideGazeActive && millis() < overrideGazeUntil) {
        eyes.setGaze(overrideGazeX, overrideGazeY);
    } else {
        overrideGazeActive = false;
        eyes.setGaze(stateGx, stateGy);
    }
}

// Bring up the SoftAP + /update HTTP server for a firmware update.
void enterUpdateMode() {
    transitionTo(State::UPDATE);
    updateModeSince = millis();
    servos.setCenter();
    eyes.setGaze(0, 0);
    eyes.setExpression(EyeExpression::UPDATE);

    WiFi.mode(WIFI_AP);
    WiFi.softAP(UPDATE_AP_SSID, UPDATE_AP_PASSWORD);
    delay(100); // let the AP interface come up

    // The SoftAP password is the only authentication; the /update page is
    // only reachable on the isolated update network. Configure the handlers
    // only once: webServer.stop() does not remove them.
    if (!updateServerReady) {
        httpUpdateServer.setup(&webServer, "/update");
        updateServerReady = true;
    }
    webServer.begin();

    String out = "{\"type\":\"update_mode\",\"ssid\":\"";
    out += UPDATE_AP_SSID;
    out += "\",\"password\":\"";
    out += UPDATE_AP_PASSWORD;
    out += "\",\"url\":\"http://";
    out += WiFi.softAPIP().toString();
    out += "/update\"}";
    Serial.println(out);
}

// Tear down the SoftAP and resume normal behavior.
void exitUpdateMode() {
    webServer.stop();
    WiFi.mode(WIFI_OFF);
    transitionTo(State::IDLE);
    eyes.setGaze(0, 0);
    faceResult.detected = false;
    Serial.println("{\"type\":\"update_mode_end\"}");
}

// ============================================================================
// State machine update
// ============================================================================
// The ESP32 owns the behavior state machine. It runs autonomously from local
// inputs (vibration sensor + on-device face detection) and drives the eyes,
// gaze, and servos. The RPi is a supervisor that can only send policy
// commands (sleep/wake) and temporary overrides (expression/gaze).
//
//   BOOT --(3s)--> IDLE
//   IDLE --(vibration OR face)--> DETECTING
//   DETECTING --(face confirmed)--> INTERACTING
//   DETECTING --(no face for 10s)--> IDLE
//   INTERACTING --(face lost for 5s)--> IDLE
//   SLEEPING --(wake command)--> IDLE
//   any --(sleep command)--> SLEEPING
//   any --(nav active=true)--> NAVIGATING
//   NAVIGATING --(nav active=false)--> IDLE
//   NAVIGATING --(no nav update for NAV_TIMEOUT_MS)--> IDLE  (self-timeout)
//   any --(4 rapid taps)--> UPDATE
//   UPDATE --(1 tap)--> IDLE
void updateState() {
    // 4-tap vibration sequence enters update mode from any state.
    VibrationData vib = sensors.getVibration();
    if (vib.updateSequence) {
        sensors.clearUpdateSequence();
        if (currentState != State::UPDATE) {
            enterUpdateMode();
            return;
        }
    }

    if (currentState == State::UPDATE) {
        // A new tap after the grace period exits update mode. The 4th tap
        // that entered the mode (or a still-pressed contact) does not.
        if (vib.detected && vib.lastDetected > updateModeSince + UPDATE_EXIT_GRACE_MS) {
            exitUpdateMode();
            return;
        }
        applyExpression(EyeExpression::UPDATE);
        servos.setCenter();
        return;
    }

    switch (currentState) {
        case State::BOOT: {
            applyExpression(EyeExpression::SEARCHING);
            if (millis() > BOOT_TIMEOUT_MS) {
                transitionTo(State::IDLE);
            }
            break;
        }

        case State::IDLE: {
            applyExpression(EyeExpression::NEUTRAL);
            if (vib.detected) {
                // Vibration wakes us up to look for a face.
                lastFaceSeen = millis();
                transitionTo(State::DETECTING);
                servos.setAngle(CH_HEAD, 0); // Look forward
            } else if (faceResult.detected) {
                // A face nearby wakes us up.
                lastFaceSeen = millis();
                transitionTo(State::DETECTING);
                servos.setAngle(CH_HEAD, 0);
            }
            break;
        }

        case State::DETECTING: {
            applyExpression(EyeExpression::DETECTING);
            if (faceResult.detected) {
                // Face confirmed: start interacting and follow it.
                lastFaceSeen = millis();
                applyGaze(faceResult.gaze_x, faceResult.gaze_y);
                transitionTo(State::INTERACTING);
            } else if (millis() - lastFaceSeen > DETECT_TIMEOUT_MS) {
                // Nothing found: give up and go back to idle.
                transitionTo(State::IDLE);
                eyes.setGaze(0, 0);
                servos.setCenter();
            }
            break;
        }

        case State::INTERACTING: {
            applyExpression(EyeExpression::HAPPY);
            if (faceResult.detected) {
                lastFaceSeen = millis();
                applyGaze(faceResult.gaze_x, faceResult.gaze_y);
            } else if (millis() - lastFaceSeen > INTERACT_TIMEOUT_MS) {
                // Face lost: return to idle.
                transitionTo(State::IDLE);
                eyes.setGaze(0, 0);
                servos.setCenter();
            }
            break;
        }

        case State::SLEEPING: {
            applyExpression(EyeExpression::SLEEPING);
            servos.setCenter();
            break;
        }

        case State::NAVIGATING: {
            // "Guide me home": the head is pinned to the RPi-computed compass
            // bearing (navTargetAngle) and ignores face-following. The RPi
            // re-sends the angle on each refresh, so the head tracks the
            // destination. If the RPi stops sending (link dropped / it crashed),
            // the self-timeout recenters the head and returns to idle so it is
            // never left stuck pointing somewhere.
            applyExpression(EyeExpression::SEARCHING);
            servos.setAngle(CH_HEAD, navTargetAngle);
            if (millis() - navSince > NAV_TIMEOUT_MS) {
                transitionTo(State::IDLE);
                servos.setCenter();
                eyes.setGaze(0, 0);
            }
            break;
        }

        case State::UPDATE:
            // Handled before the switch (SoftAP + OTA mode).
            break;

        case State::ERROR: {
            // Hardware fault: show the red X error face and hold servos
            // centered. The owl cannot recover from a boot-time init failure,
            // so it stays here until power-cycled.
            applyExpression(EyeExpression::ERROR);
            servos.setCenter();
            break;
        }
    }
}

// ============================================================================
// Serial command parser
// ============================================================================
void parseSerialCommands() {
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

// ============================================================================
// Hardware check (first-run wiring verification)
// ============================================================================
// Probes every peripheral the owl depends on and reports each one's status over
// serial (and on the eye displays) so a fresh wiring can be verified in seconds
// without reading a log. Add "HARDWARE_CHECK=1" to build_flags (or the
// PlatformIO CLI) to run this instead of the normal boot. Each probe is
// independent, so one bad wire doesn't stop the rest from being reported.
//
//   LCDs        -> lcdLeft/Right.begin() (SPI; false = no display on the bus)
//   PCA9685     -> I2C scan for 0x40 (the servo driver)
//   GPS         -> I2C scan for 0x10 (PA1010D)
//   BNO055 IMU  -> I2C scan for 0x28 + ready flag
//   Vibration   -> SW420 idle level on VIBRATION_PIN (HIGH = pull-up intact)
//   OV3660 cam  -> esp_camera frame grab (null = no camera / bad ribbon)
//
// Note: the I2C scan re-begins the bus (Wire.begin is idempotent), so the
// check runs BEFORE Sensors::begin() below.
void runHardwareCheck() {
    // XIAO S3 senses the 5 V input on D4 = GPIO5 = ADC1_CH4 through a 2:1
    // divider. (The old code read GPIO7, which is NOT ADC-capable on the S3, so
    // it always returned 0.) Read it once up front: if the board is still
    // brownout-looping, this line is the last thing we'll ever see, which tells
    // us the power supply - not a peripheral - is the problem.
    int vccMv = (int)((analogRead(5) * 3300) / (4095 / 2));
    Serial.print(F("[check] VCC = "));
    Serial.print(vccMv);
    Serial.println(F(" mV"));

    // PSRAM gates the LCD framebuffers (and the camera). If the XIAO's octal
    // PSRAM failed to init, lcdLeft/Right.begin() return false and the eyes
    // render nothing -> black screens even though the panels are wired. Report
    // whether PSRAM is present so we can tell "PSRAM down" from "panels down".
    // NOTE: use psramFound() (Arduino HAL) - it safely returns false when PSRAM
    // is absent. Do NOT call esp_spiram_get_size() here: that IDF API ABORTS on
    // core 1 when PSRAM is missing, which crashed this board into a reset loop.
    bool psramOk = psramFound();
    Serial.print(F("[check] PSRAM = "));
    Serial.println(psramOk ? "present" : "NOT FOUND (octal PSRAM down)");

    // Bring up both displays. Eyes::render() writes into a framebuffer that
    // does not exist until begin() has allocated it in PSRAM, so nothing may
    // render before this point.
    bool lcdL = false, lcdR = false;
    bringUpDisplays(lcdL, lcdR);
    Serial.print(F("[check] lcd_left.begin()="));
    Serial.print(lcdL ? "true" : "false");
    Serial.print(F("  lcd_right.begin()="));
    Serial.println(lcdR ? "true" : "false");

    // Confirm both are up: hold LEFT=RED, RIGHT=GREEN steady.
    Serial.println(F("[diag] both up: left=RED right=GREEN (steady)"));
    if (lcdL) { lcdLeft.fillScreen(0xF800);  lcdLeft.flush(); }
    if (lcdR) { lcdRight.fillScreen(0x07E0); lcdRight.flush(); }
    delay(4000);

    // Only draw the "probing" face if at least one display came up; otherwise
    // rendering would be a no-op anyway and we skip the work.
    if (lcdL || lcdR) {
        eyes.setExpression(EyeExpression::DETECTING);
        eyes.setGaze(0, 0);
        eyes.render();
    }

    // I2C bus scan: report every address that answers, then flag the peripherals
    // we specifically expect.
    Wire.begin(I2C_SDA, I2C_SCL, I2C_FREQ);
    bool pcaSeen = false, gpsSeen = false, bnoSeen = false;
    // Build a compact "[28,40,10]" string of every I2C address that answers.
    String foundStr = "[";
    bool firstFound = true;
    for (uint8_t addr = 1; addr < 127; addr++) {
        Wire.beginTransmission(addr);
        if (Wire.endTransmission() == 0) {
            if (!firstFound) foundStr += ",";
            foundStr += String(addr, HEX);
            firstFound = false;
            if (addr == ADDR_PCA9685) pcaSeen = true;
            if (addr == ADDR_GPS) gpsSeen = true;
            if (addr == ADDR_BNO055) bnoSeen = true;
        }
    }
    foundStr += "]";

    bool imuReady = sensors.isImuReady();
    bool vibOk = (digitalRead(VIBRATION_PIN) == HIGH);  // idle: pulled up
    bool camOk = false;
#if FACE_DETECTION_ENABLED
    if (FaceDetector_Init()) {
        camera_fb_t* fb = esp_camera_fb_get();
        if (fb) { camOk = true; esp_camera_fb_return(fb); }
    }
#endif

    bool allOk = lcdL && lcdR && pcaSeen && gpsSeen && bnoSeen && imuReady && vibOk && camOk;

    JsonDocument doc;
    doc["type"] = "hardware_check";
    doc["vcc_mv"] = vccMv;
    doc["psram"] = psramOk;
    doc["lcd_left"] = lcdL;
    doc["lcd_right"] = lcdR;
    doc["servo"] = pcaSeen;
    doc["gps"] = gpsSeen;
    doc["imu"] = bnoSeen && imuReady;
    doc["vibration"] = vibOk;
    doc["camera"] = camOk;
    doc["i2c_found"] = foundStr;
    doc["all_ok"] = allOk;
    String out;
    serializeJson(doc, out);
    Serial.println(out);

    // ---- Unmissable diagnostic: flood each eye SOLID white or SOLID black ---
    // The Mac can't power the board, so we can't rely on serial. A fully-lit
    // panel is impossible to miss (unlike a small glyph).
    //
    //   LEFT eye  = PSRAM      (WHITE = PSRAM up / BLACK = PSRAM down)
    //   RIGHT eye = LCD + I2C  (WHITE = panel wired AND all I2C devices answer
    //                                 / BLACK = panel not wired or I2C missing)
    //
    // (psramOk is already resolved above via psramFound().)
    // NOTE: the right-eye "OK" gate is the I2C *bus* (all three expected
    // devices answer), NOT the IMU's software "ready" flag. The BNO055 can be
    // present on the bus (answers 0x28) yet still not report ready in this
    // check window; we don't want a healthy panel painted black over that.
    bool i2cOk = pcaSeen && gpsSeen && bnoSeen;
    bool lcdOk = lcdL && lcdR;

    // DIAGNOSTIC (eyes show only backlight, no image, at 27 MHz): now at 6 MHz, alternate
    // each eye between BLACK and a bright color. If a panel is receiving SPI
    // data at all, it will FLASH - impossible to miss. Per-eye colors keep the
    // two panels distinguishable.
    //   LEFT  flashes RED    (0xF800)
    //   RIGHT flashes GREEN  (0x07E0)
    // If a panel stays solidly black through all flashes, its data path is dead.
    Serial.println(F("[diag] flashing eyes 5x (left=RED, right=GREEN) at 6 MHz..."));
    for (int i = 0; i < 5; i++) {
        if (lcdL) {
            lcdLeft.fillScreen(0x0000);  lcdLeft.flush();
            lcdLeft.fillScreen(0xF800);  lcdLeft.flush();  // RED
        }
        if (lcdR) {
            lcdRight.fillScreen(0x0000); lcdRight.flush();
            lcdRight.fillScreen(0x07E0); lcdRight.flush();  // GREEN
        }
        delay(600);
    }
    // Hold the final colors a moment longer so they can be seen.
    if (lcdL) { lcdLeft.fillScreen(0xF800);  lcdLeft.flush(); }
    if (lcdR) { lcdRight.fillScreen(0x07E0); lcdRight.flush(); }
    delay(2000);

    Serial.print(F("[eyes] PSRAM="));
    Serial.print(psramOk ? "OK" : "FAIL");
    Serial.print(F(" LCD="));
    Serial.print(lcdOk ? "OK" : "FAIL");
    Serial.print(F(" servo="));
    Serial.print(pcaSeen ? "OK" : "--");
    Serial.print(F(" gps="));
    Serial.print(gpsSeen ? "OK" : "--");
    Serial.print(F(" imu="));
    Serial.println(bnoSeen ? "OK" : "--");

    Serial.println(allOk
        ? F("HARDWARE CHECK: all peripherals detected")
        : F("HARDWARE CHECK: one or more peripherals missing (see JSON above)"));
}

// ============================================================================
// Setup
// ============================================================================
void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(100);

#if HARDWARE_CHECK
    // I2C-only probe, run FIRST (before PSRAM/LCD/camera) so it needs minimal
    // current and can boot even on a marginal supply. Tells us which devices
    // actually ANSWER on the bus (vs. just having their power LED lit).
    Wire.begin(I2C_SDA, I2C_SCL, I2C_FREQ);
    Serial.print(F("[i2c] answers:"));
    bool any = false;
    for (uint8_t a = 1; a < 127; a++) {
        Wire.beginTransmission(a);
        if (Wire.endTransmission() == 0) {
            Serial.print(F(" 0x"));
            Serial.print(a, HEX);
            any = true;
        }
    }
    if (!any) Serial.print(F(" (none)"));
    Serial.println(F("  <- GPS=0x10 IMU=0x28 servo=0x40"));
#endif

#if !HARDWARE_CHECK
    // Wait for USB CDC to connect, but only briefly: the owl must boot
    // standalone (for the 4-tap update mode) even without the RPi.
    // Skipped in HARDWARE_CHECK mode: the check must run headless (power
    // adapter only, no host), so we must not block on a USB connection that
    // never comes.
    uint32_t usbWaitStart = millis();
    while (!Serial && millis() - usbWaitStart < USB_WAIT_TIMEOUT_MS) {
        delay(10);
    }
#endif

    Serial.println(F("Robot Owl ESP32-S3 starting..."));

    // Optional wiring-verification boot: build with -DHARDWARE_CHECK=1 (see
    // platformio.ini) to probe every peripheral and report the results instead
    // of entering the normal state machine. The owl then idles (eyes show the
    // pass/fail face); re-flash without the flag to resume normal operation.
#if HARDWARE_CHECK
    runHardwareCheck();
    return;
#endif

    bool lcdLeftOk = false, lcdRightOk = false;
    bringUpDisplays(lcdLeftOk, lcdRightOk);
    if (!lcdLeftOk) {
        Serial.println(F("ERROR: Left LCD failed"));
        currentState = State::ERROR;
    }
    if (!lcdRightOk) {
        Serial.println(F("ERROR: Right LCD failed"));
        currentState = State::ERROR;
    }

    // Initialize sensors
    // begin() faellt nur noch bei einem echten Busfehler durch. Ein nicht
    // antwortender IMU meldet sich ueber isImuReady() und ist KEIN Grund, in
    // ERROR zu gehen: der Bus traegt auch GPS und Servotreiber, und die Augen,
    // die Kamera und die Gesichtserkennung haengen gar nicht daran. Bis
    // 2026-08-28 riss ein stummer BNO055 die gesamte Eule mit in ERROR.
    if (!sensors.begin()) {
        Serial.println(F("ERROR: Sensors failed"));
        currentState = State::ERROR;
    } else if (!sensors.isImuReady()) {
        Serial.println(F("WARNING: running without IMU - navigation disabled"));
    }

    // Initialize servos
    servos.begin();

    // Initialize face detection (optional)
#if FACE_DETECTION_ENABLED
    if (FaceDetector_Init()) {
        Serial.println(F("Face detection enabled"));
    } else {
        Serial.println(F("Face detection failed - running without it"));
    }
#else
    Serial.println(F("Face detection disabled (compile with FACE_DETECTION_ENABLED=1 to enable)"));
#endif

    // Initial expression
    eyes.setExpression(EyeExpression::SEARCHING);

    Serial.println(F("System ready"));
    Serial.println(F("{\"type\":\"boot\",\"msg\":\"ready\"}"));
}

// ============================================================================
// Main loop
// ============================================================================
void loop() {
    // Gemessene Schleifenrate, Fenster = Telemetrieintervall.
    loopCount++;
    {
        const uint32_t now = millis();
        const uint32_t span = now - lastLoopMeasure;
        if (span >= TELEMETRY_INTERVAL_MS) {
            loopHz = (span > 0) ? (loopCount * 1000.0f / span) : 0.0f;
            loopCount = 0;
            lastLoopMeasure = now;
        }
    }

    // Vibration auswerten: genau einmal pro Runde, VOR allen Lesern. Der
    // Interrupt zaehlt Flanken, diese Funktion macht daraus Zustand und
    // Klopfzaehler. Wuerde stattdessen jeder Leser selbst abholen, nehmen sich
    // updateState() (60 Hz) und sendTelemetry() (2 Hz) die Flanken gegenseitig
    // weg.
    sensors.updateVibration();

    // Parse incoming commands
    parseSerialCommands();

    // Update state machine
    updateState();

    // Serve the /update page while in update mode.
    if (currentState == State::UPDATE) {
        webServer.handleClient();
    }

    // Run face detection (if enabled), rate-limited so the expensive
    // inference doesn't starve the rest of the loop. Between runs the
    // state machine keeps using the last result in faceResult.
#if FACE_DETECTION_ENABLED
    if (currentState != State::UPDATE && millis() - lastFaceDetect >= FACE_DETECT_INTERVAL_MS) {
        FaceDetector_Detect(&faceResult);
        lastFaceDetect = millis();
    }
#else
    faceResult.detected = false;
#endif

    // Render eyes
    eyes.render();

    // Update servos (smooth motion)
    servos.update();

    // Send telemetry at interval
    if (millis() - lastTelemetry > TELEMETRY_INTERVAL_MS) {
        sendTelemetry();
        lastTelemetry = millis();
    }

    // Small delay to prevent watchdog
    delay(16); // ~60Hz loop
}
