// ============================================================================
// I2C bus diagnostic (env: i2ctest, -DI2CTEST_ACTIVE)
//
// Every I2C read in the real firmware fails with ESP_ERR_INVALID_STATE (259).
// This sketch answers, in order:
//   0. Are SDA/SCL even idling HIGH? A line stuck LOW means a short, a missing
//      pull-up, or a slave holding the bus -- no driver can work through that.
//   1. Do the devices answer a BIT-BANGED scan? This drives the pins by hand
//      and completely bypasses the ESP-IDF i2c_master driver, so it separates
//      "the bus/devices are broken" from "the driver is being misused".
//   2. Do they answer the normal Wire driver, at 100 kHz and at 400 kHz?
//   3. Are they on the pins config.h thinks they are? Every free pin pair is
//      scanned, including SDA/SCL swapped -- a very common wiring slip.
//   4. Does a 9-clock bus-recovery pulse train change anything?
//
// Expected devices: BNO055 @ 0x28, PA1010D GPS @ 0x10, PCA9685 @ 0x40.
// ============================================================================
#if defined(I2CTEST_ACTIVE)

#include <Arduino.h>
#include <Wire.h>
#include "config.h"

// Pins free for I2C on this build. In use elsewhere: 3 (vibration), 4/8/9/44
// (LCD DC+CS), 5 (SCK), 7 (MOSI), 43 (RST).
//   GPIO1 = D0, GPIO2 = D1, GPIO6 = D5
static const int FREE_PINS[] = {1, 2, 6};

struct PinPair { int sda, scl; const char* note; };
static const PinPair PAIRS[] = {
    {1, 2, "config.h (D0/D1)"},
    {2, 1, "D0/D1 SWAPPED"},
    {1, 6, "D0/D5"},
    {6, 1, "D0/D5 swapped"},
    {2, 6, "D1/D5"},
    {6, 2, "D1/D5 swapped"},
};

static const uint8_t EXPECTED[] = {0x10, 0x28, 0x40};
static const char* EXPECTED_NAME[] = {"PA1010D GPS", "BNO055 IMU", "PCA9685 servo"};

// ---------------------------------------------------------------------------
// Bit-banged I2C. Open-drain emulation: "release" = INPUT_PULLUP (line floats
// high through the internal pull-up), "drive" = OUTPUT LOW.
// ---------------------------------------------------------------------------
static int bbSda = -1, bbScl = -1;

static inline void sdaRelease() { pinMode(bbSda, INPUT_PULLUP); }
static inline void sdaDrive()   { pinMode(bbSda, OUTPUT); digitalWrite(bbSda, LOW); }
static inline void sclRelease() { pinMode(bbScl, INPUT_PULLUP); }
static inline void sclDrive()   { pinMode(bbScl, OUTPUT); digitalWrite(bbScl, LOW); }
static inline void bbDelay()    { delayMicroseconds(5); }   // ~100 kHz

static void bbStart() {
    sdaRelease(); sclRelease(); bbDelay();
    sdaDrive(); bbDelay();
    sclDrive(); bbDelay();
}

static void bbStop() {
    sdaDrive(); bbDelay();
    sclRelease(); bbDelay();
    sdaRelease(); bbDelay();
}

static void bbWriteBit(int bit) {
    if (bit) sdaRelease(); else sdaDrive();
    bbDelay();
    sclRelease(); bbDelay();
    sclDrive(); bbDelay();
}

// Returns 0 if the slave pulled SDA low (ACK), 1 for NACK.
static int bbReadAck() {
    sdaRelease(); bbDelay();
    sclRelease(); bbDelay();
    const int ack = digitalRead(bbSda);
    sclDrive(); bbDelay();
    return ack;
}

static int bbWriteByte(uint8_t b) {
    for (int i = 7; i >= 0; i--) bbWriteBit((b >> i) & 1);
    return bbReadAck();
}

static bool bbProbe(uint8_t addr) {
    bbStart();
    const int ack = bbWriteByte((uint8_t)(addr << 1));   // write bit
    bbStop();
    return ack == 0;
}

static int bbScan(int sda, int scl, uint8_t* found, int maxFound) {
    bbSda = sda; bbScl = scl;
    sdaRelease(); sclRelease();
    delay(2);
    int n = 0;
    for (uint8_t a = 0x08; a <= 0x77; a++) {
        if (bbProbe(a) && n < maxFound) found[n++] = a;
    }
    return n;
}

// ---------------------------------------------------------------------------
// Driver-based scan
// ---------------------------------------------------------------------------
static int wireScan(int sda, int scl, uint32_t hz, uint8_t* found, int maxFound) {
    Wire.end();
    delay(20);
    if (!Wire.begin(sda, scl, hz)) {
        Serial.println(F("      Wire.begin() FAILED"));
        return -1;
    }
    delay(20);
    int n = 0;
    for (uint8_t a = 0x08; a <= 0x77; a++) {
        Wire.beginTransmission(a);
        if (Wire.endTransmission() == 0 && n < maxFound) found[n++] = a;
    }
    return n;
}

static void report(const char* label, int n, const uint8_t* found) {
    Serial.printf("      %-22s ", label);
    if (n < 0) { Serial.println(F("(scan failed)")); return; }
    if (n == 0) { Serial.println(F("nothing responded")); return; }
    for (int i = 0; i < n; i++) {
        Serial.printf("0x%02X ", found[i]);
        for (size_t e = 0; e < sizeof(EXPECTED); e++) {
            if (found[i] == EXPECTED[e]) Serial.printf("(%s) ", EXPECTED_NAME[e]);
        }
    }
    Serial.println();
}

// 9 SCL pulses with SDA released: the standard way to shake loose a slave that
// is mid-transaction and holding SDA low.
static void busRecover(int sda, int scl) {
    bbSda = sda; bbScl = scl;
    sdaRelease();
    for (int i = 0; i < 9; i++) {
        sclRelease(); bbDelay();
        sclDrive();   bbDelay();
    }
    sclRelease();
    bbStop();
}

void setup() {
    Serial.begin(115200);
    uint32_t t0 = millis();
    while (!Serial && millis() - t0 < 3000) delay(10);
    delay(300);

    Serial.println();
    Serial.println(F("=================================================="));
    Serial.println(F(" I2C bus diagnostic"));
    Serial.printf( " config.h: SDA=GPIO%d SCL=GPIO%d @ %d Hz\n",
                   I2C_SDA, I2C_SCL, I2C_FREQ);
    Serial.println(F("=================================================="));

    // --- Phase 0: idle levels -------------------------------------------
    Serial.println(F("\n[0] Idle line levels (INPUT_PULLUP; HIGH = healthy)"));
    for (size_t i = 0; i < sizeof(FREE_PINS) / sizeof(FREE_PINS[0]); i++) {
        const int p = FREE_PINS[i];
        pinMode(p, INPUT_PULLUP);
        delay(5);
        const int lvl = digitalRead(p);
        Serial.printf("      GPIO%-2d : %s%s\n", p, lvl ? "HIGH" : "LOW ",
                      lvl ? "" : "   <-- STUCK LOW: short to GND, or a slave holding the bus");
    }

    uint8_t found[24];

    // --- Phase 1: bit-banged scan, driver bypassed ----------------------
    Serial.println(F("\n[1] BIT-BANGED scan (bypasses the ESP-IDF driver entirely)"));
    for (size_t i = 0; i < sizeof(PAIRS) / sizeof(PAIRS[0]); i++) {
        Serial.printf("    SDA=GPIO%-2d SCL=GPIO%-2d  %s\n",
                      PAIRS[i].sda, PAIRS[i].scl, PAIRS[i].note);
        const int n = bbScan(PAIRS[i].sda, PAIRS[i].scl, found, sizeof(found));
        report("bit-banged:", n, found);
    }

    // --- Phase 2: Wire driver, both clocks, configured pins -------------
    Serial.println(F("\n[2] Wire driver on the configured pins"));
    for (uint32_t hz : {(uint32_t)100000, (uint32_t)400000}) {
        Serial.printf("    %lu Hz\n", (unsigned long)hz);
        const int n = wireScan(I2C_SDA, I2C_SCL, hz, found, sizeof(found));
        report("Wire:", n, found);
    }

    // --- Phase 3: Wire driver, every candidate pair at 100 kHz ----------
    Serial.println(F("\n[3] Wire driver, every candidate pin pair @ 100 kHz"));
    for (size_t i = 0; i < sizeof(PAIRS) / sizeof(PAIRS[0]); i++) {
        Serial.printf("    SDA=GPIO%-2d SCL=GPIO%-2d  %s\n",
                      PAIRS[i].sda, PAIRS[i].scl, PAIRS[i].note);
        const int n = wireScan(PAIRS[i].sda, PAIRS[i].scl, 100000, found, sizeof(found));
        report("Wire:", n, found);
    }

    // --- Phase 4: recovery pulses, then rescan --------------------------
    Serial.println(F("\n[4] 9-clock bus recovery on the configured pins, then rescan"));
    Wire.end(); delay(20);
    busRecover(I2C_SDA, I2C_SCL);
    int n = bbScan(I2C_SDA, I2C_SCL, found, sizeof(found));
    report("bit-banged after:", n, found);
    n = wireScan(I2C_SDA, I2C_SCL, 100000, found, sizeof(found));
    report("Wire after:", n, found);

    Serial.println(F("\n[done] summary above; board now idle"));
}

void loop() { delay(1000); }

#endif // I2CTEST_ACTIVE
