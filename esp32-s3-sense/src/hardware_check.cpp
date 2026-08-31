#include "hardware_check.h"

#if HARDWARE_CHECK

#include <Wire.h>
#include <ArduinoJson.h>
#include "esp_camera.h"
#include "config.h"
// NOTE: psramFound() comes from esp32-hal-psram.h, which is pulled in via
// Arduino.h -> esp32-hal.h. The old core's <esp_spiram.h> does not exist in
// ESP-IDF 5.x (pioarduino / core 3.x), so we no longer include it directly.

// ============================================================================
// Hardware check (first-run wiring verification)
//
// Add "HARDWARE_CHECK=1" to build_flags (or the PlatformIO CLI) to run this
// instead of the normal boot. Each probe is independent, so one bad wire
// doesn't stop the rest from being reported.
//
//   LCDs        -> lcdLeft/Right.begin() (SPI; false = no display on the bus)
//   PCA9685     -> I2C scan for 0x40 (the servo driver)
//   GPS         -> I2C scan for 0x10 (PA1010D)
//   BNO055 IMU  -> I2C scan for 0x28 + ready flag
//   Vibration   -> SW420 idle level on VIBRATION_PIN (HIGH = pull-up intact)
//   OV3660 cam  -> esp_camera frame grab (null = no camera / bad ribbon)
//
// Note: the I2C scan re-begins the bus (Wire.begin is idempotent), so the
// check runs BEFORE Sensors::begin().
// ============================================================================

void i2cScanToSerial() {
    Wire.begin(I2C_SDA, I2C_SCL, I2C_FREQ);
    Serial.print(F("[i2c] answers:"));
    bool any = false;
    i2cScan([&any](uint8_t addr) {
        Serial.print(F(" 0x"));
        Serial.print(addr, HEX);
        any = true;
    });
    if (!any) Serial.print(F(" (none)"));
    Serial.println(F("  <- GPS=0x10 IMU=0x28 servo=0x40"));
}

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
    i2cScan([&](uint8_t addr) {
        if (!firstFound) foundStr += ",";
        foundStr += String(addr, HEX);
        firstFound = false;
        if (addr == ADDR_PCA9685) pcaSeen = true;
        if (addr == ADDR_GPS) gpsSeen = true;
        if (addr == ADDR_BNO055) bnoSeen = true;
    });
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
    (void)i2cOk;  // reported via the per-device fields above; kept for the note

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

#endif  // HARDWARE_CHECK
