#include <Arduino.h>
#include <SPI.h>
#include "config.h"
#include "owl.h"
#include "behavior.h"
#include "protocol.h"
#include "vision.h"
#include "hardware_check.h"

// ============================================================================
// Wiring only.
//
// This file constructs the peripherals, brings them up, and runs the loop. The
// three things it used to also contain now live next to their own headers:
//
//   behavior.cpp        the state machine + the OTA update mode
//   protocol.cpp        the NDJSON wire contract with rpi-brain
//   vision.cpp          the face-detection task on core 0
//   hardware_check.cpp  the -DHARDWARE_CHECK=1 wiring probe
//
// It was ~930 lines holding all four, which meant reading all four before any
// firmware change. Keep new subsystems out of here: give them a header and a
// source file, and let this file own only their construction and their call in
// loop().
// ============================================================================

// ============================================================================
// Global instances
// ============================================================================
// Both eye displays share the SAME SPI SCK/MOSI (only DC/CS differ), so they
// must share ONE SPIClass. If each owned its own SPIClass and both called
// begin() on the same pins, the second begin() re-inits an already-configured
// SPI peripheral and hangs under ESP-IDF 5.x (pioarduino core 3.x). The shared
// bus is attached to both displays and initialised once in bringUpDisplays().
static SPIClass lcdSharedBus(FSPI);
GC9D01 lcdLeft(LCD_DC_L, LCD_CS_L, LCD_RST);
GC9D01 lcdRight(LCD_DC_R, LCD_CS_R, LCD_RST);
Eyes eyes(lcdLeft, lcdRight);
Sensors sensors;
ServoController servos;

// The last thing the camera saw. Produced by the core-0 task in vision.cpp;
// loop() copies the newest result in here once per iteration so the state
// machine and the telemetry both work from ONE coherent snapshot.
FaceResult_t faceResult = {0};

// Measured loop rate, reported in telemetry.
float loopHz = 0.0f;

// Loop-local bookkeeping: nothing outside this file reads these.
static uint32_t lastTelemetry = 0;
static uint32_t loopCount = 0;
static uint32_t lastLoopMeasure = 0;

// See owl.h for why this order is load-bearing.
void bringUpDisplays(bool& leftOk, bool& rightOk) {
    lcdSharedBus.begin(LCD_SCK, -1, LCD_MOSI, -1);
    lcdLeft.attachBus(&lcdSharedBus);
    lcdRight.attachBus(&lcdSharedBus);
    lcdLeft.resetShared();
    leftOk = lcdLeft.begin();
    rightOk = lcdRight.begin();
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
    i2cScanToSerial();
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
        behavior::transitionTo(State::ERROR);
    }
    if (!lcdRightOk) {
        Serial.println(F("ERROR: Right LCD failed"));
        behavior::transitionTo(State::ERROR);
    }

    // Initialize sensors
    // begin() faellt nur noch bei einem echten Busfehler durch. Ein nicht
    // antwortender IMU meldet sich ueber isImuReady() und ist KEIN Grund, in
    // ERROR zu gehen: der Bus traegt auch GPS und Servotreiber, und die Augen,
    // die Kamera und die Gesichtserkennung haengen gar nicht daran. Bis
    // 2026-08-28 riss ein stummer BNO055 die gesamte Eule mit in ERROR.
    if (!sensors.begin()) {
        Serial.println(F("ERROR: Sensors failed"));
        behavior::transitionTo(State::ERROR);
    } else if (!sensors.isImuReady()) {
        Serial.println(F("WARNING: running without IMU - navigation disabled"));
    }

    // Initialize servos
    servos.begin();

    // Initialize face detection (optional)
#if FACE_DETECTION_ENABLED
    // Camera + model first, then the core-0 task that drives them. Order
    // matters: vision::begin() must not start a task that would call an
    // uninitialised detector.
    if (FaceDetector_Init() && vision::begin()) {
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
    protocol::poll();

    // Pick up whatever the core-0 detection task has found. This is a ~40-byte
    // copy under a spinlock, not an inference: the 48-114 ms of esp-dl work
    // happens on the other core (see include/vision.h). Until 2026-08-31
    // FaceDetector_Detect() was called right here, which froze the eyes for the
    // whole inference -- 5.8 Hz render while tracking a face.
    //
    // Read BEFORE behavior::update() so the state machine and sendTelemetry()
    // below see the same snapshot for this whole iteration.
#if FACE_DETECTION_ENABLED
    vision::setEnabled(behavior::current() != State::UPDATE);
    vision::latest(faceResult);
#else
    faceResult.detected = false;
#endif

    // Update state machine
    behavior::update();

    // Serve the /update page while in update mode (no-op otherwise).
    behavior::serveUpdateClient();

    // Render eyes
    eyes.render();

    // Update servos (smooth motion)
    servos.update();

    // Send telemetry at interval
    if (millis() - lastTelemetry > TELEMETRY_INTERVAL_MS) {
        protocol::sendTelemetry();
        lastTelemetry = millis();
    }

    // Small delay to prevent watchdog
    delay(16); // ~60Hz loop
}
