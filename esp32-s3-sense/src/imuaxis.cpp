// ============================================================================
// LSM303AGR: Einbaulage, Kalibrierung und Kurs (env: imuaxis, -DIMUAXIS_ACTIVE)
//
// Beantwortet der Reihe nach:
//   0. Sind beide Haelften da, und ist es wirklich ein AGR? WHO_AM_I des
//      Beschleunigungssensors (0x0F -> 0x33) und des Magnetometers
//      (0x4F -> 0x40). Das blaue LSM303DLHC hat auf 0x4F kein WHO_AM_I und
//      faellt hier auf - siehe config.h, es waere sonst ein stiller Kursfehler.
//   1. Wie ist das Board eingebaut? Der Schwerevektor im ROHEN Sensorrahmen
//      liefert IMU_REMAP_X/Y/Z. Bei waagerechter Eule muss genau eine Achse
//      etwa +/-9,81 zeigen und die anderen zwei etwa 0.
//   2. Stimmt der Umbau? Nach dem Remap muessen roll und pitch bei
//      waagerechter Eule nahe 0 liegen.
//   3. Wie weit ist die Magnetometerkalibrierung? Spanne pro Achse und die
//      Anzahl abgedeckter Achsen - der Fortschrittsbalken der Drehung.
//   4. Was meldet der Kurs? Live, sobald genug Achsen abgedeckt sind.
//
// BENUTZT ABSICHTLICH lib/OwlImu, also GENAU DIE MATHEMATIK DER FIRMWARE. Eine
// Diagnose mit eigener Kopie der Rechnung prueft nur sich selbst.
// ============================================================================
#if defined(IMUAXIS_ACTIVE)

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_LIS2MDL.h>
#include <Adafruit_LSM303_Accel.h>

#include "config.h"
#include "imu_config.h"

static OwlImu imu;
// Zweites, ROHES Paar Treiber: OwlImu gibt nur den bereits umgerechneten
// Eulenrahmen heraus, aber fuer Schritt 1 braucht man genau den Rohrahmen.
static Adafruit_LSM303_Accel_Unified rawAccel(1);
static Adafruit_LIS2MDL rawMag(2);
static bool rawOk = false;

static void probeWhoAmI() {
    struct { const char* name; uint8_t addr; uint8_t reg; uint8_t want; } P[] = {
        {"Beschleunigung", ADDR_LSM303_ACCEL, 0x0F, 0x33},
        {"Magnetometer  ", ADDR_LSM303_MAG, 0x4F, 0x40},
    };
    for (auto& p : P) {
        Wire.beginTransmission(p.addr);
        Wire.write(p.reg);
        bool ok = (Wire.endTransmission(false) == 0) &&
                  (Wire.requestFrom((int)p.addr, 1) == 1);
        Serial.print(F("    "));
        Serial.print(p.name);
        Serial.print(F(" @0x"));
        Serial.print(p.addr, HEX);
        Serial.print(F("  WHO_AM_I=0x"));
        if (!ok) {
            Serial.print(F("--   KEINE ANTWORT"));
        } else {
            uint8_t v = Wire.read();
            Serial.print(v, HEX);
            Serial.print(v == p.want ? F("   ok") : F("   FALSCH, erwartet 0x"));
            if (v != p.want) Serial.print(p.want, HEX);
        }
        Serial.println();
    }
}

void setup() {
    Serial.begin(115200);
    uint32_t t0 = millis();
    while (!Serial && (millis() - t0) < USB_WAIT_TIMEOUT_MS) delay(10);
    delay(300);

    Serial.println(F("\n=== LSM303AGR: Einbaulage, Kalibrierung, Kurs ==="));
    Serial.print(F("Bus: SDA=GPIO")); Serial.print(I2C_SDA);
    Serial.print(F(" SCL=GPIO")); Serial.print(I2C_SCL);
    Serial.print(F(" @ ")); Serial.print(I2C_FREQ / 1000); Serial.println(F(" kHz"));

    Wire.begin(I2C_SDA, I2C_SCL, I2C_FREQ);
    Wire.setTimeOut(I2C_TIMEOUT_MS);

    Serial.println(F("\n[0] Identitaet beider Haelften"));
    probeWhoAmI();

    Serial.println(F("\n[1] Init"));
    if (!imu.begin(owlImuConfigFromHeader(), &Wire)) {
        Serial.println(F("    OwlImu::begin() FEHLGESCHLAGEN - Grund steht oben."));
        Serial.println(F("    Ende."));
        return;
    }
    Serial.println(F("    OwlImu bereit."));

    rawOk = rawAccel.begin(ADDR_LSM303_ACCEL, &Wire) &&
            rawMag.begin(ADDR_LSM303_MAG, &Wire);
    if (rawOk) {
        rawAccel.setRange(LSM303_RANGE_2G);
        rawAccel.setMode(LSM303_MODE_HIGH_RESOLUTION);
    }

    Serial.print(F("\n    Aktueller Remap aus config.h: X<-"));
    Serial.print(IMU_REMAP_X); Serial.print(F(" Y<-")); Serial.print(IMU_REMAP_Y);
    Serial.print(F(" Z<-")); Serial.print(IMU_REMAP_Z);
    Serial.print(F("   Kursoffset: ")); Serial.println(IMU_HEADING_OFFSET_DEG);

    Serial.println(F("\nSO LIEST MAN DAS:"));
    Serial.println(F("  ROH   = Schwerevektor im Sensorrahmen. Eule WAAGERECHT"
                     " hinstellen: genau eine"));
    Serial.println(F("          Achse ~+/-9.81, die anderen ~0. Diese Achse ist"
                     " die Eulen-Z-Achse,"));
    Serial.println(F("          das Vorzeichen sagt, ob sie gedreht werden muss."));
    Serial.println(F("  EULE  = nach dem Remap. Bei waagerechter Eule muessen"
                     " roll und pitch ~0 sein."));
    Serial.println(F("  SPAN  = beobachtete Magnetfeldspanne je Achse in uT."
                     " Eule LANGSAM einmal"));
    Serial.print(F("          voll um die Hochachse drehen; ab "));
    Serial.print(MAG_CAL_MIN_SPAN_UT);
    Serial.println(F(" uT zaehlt eine Achse."));
    Serial.println(F("          Eine waagerechte Drehung erreicht 2 von 3 - das"
                     " reicht fuer den Kurs."));
    Serial.println(F("  KURS  = Schnabelrichtung, rechtweisend (Deklination ist"
                     " eingerechnet).\n"));
}

void loop() {
    if (!imu.ok()) { delay(500); return; }

    imu.update();

    static uint32_t last = 0;
    if (millis() - last < 500) return;
    last = millis();

    OwlImuSample s = imu.read();

    if (rawOk) {
        sensors_event_t ae;
        rawAccel.getEvent(&ae);
        Serial.print(F("ROH  x=")); Serial.print(ae.acceleration.x, 2);
        Serial.print(F(" y=")); Serial.print(ae.acceleration.y, 2);
        Serial.print(F(" z=")); Serial.print(ae.acceleration.z, 2);
        Serial.print(F("   |a|="));
        Serial.println(sqrtf(ae.acceleration.x * ae.acceleration.x +
                             ae.acceleration.y * ae.acceleration.y +
                             ae.acceleration.z * ae.acceleration.z), 2);
    }

    Serial.print(F("EULE ax=")); Serial.print(s.ax, 2);
    Serial.print(F(" ay=")); Serial.print(s.ay, 2);
    Serial.print(F(" az=")); Serial.print(s.az, 2);
    Serial.print(F("   roll=")); Serial.print(s.roll, 1);
    Serial.print(F(" pitch=")); Serial.println(s.pitch, 1);

    float sx, sy, sz;
    imu.magSpan(sx, sy, sz);
    Serial.print(F("MAG  mx=")); Serial.print(s.mx, 1);
    Serial.print(F(" my=")); Serial.print(s.my, 1);
    Serial.print(F(" mz=")); Serial.print(s.mz, 1);
    Serial.print(F("   SPAN ")); Serial.print(sx, 0);
    Serial.print('/'); Serial.print(sy, 0);
    Serial.print('/'); Serial.print(sz, 0);
    Serial.print(F(" uT  Achsen=")); Serial.print(s.magAxes);
    Serial.println(F("/3"));

    Serial.print(F("KURS "));
    if (s.headingOk) {
        Serial.print(s.yaw, 1);
        Serial.println(F(" deg"));
    } else if (!s.haveOffsets) {
        Serial.println(F("-- noch nicht kalibriert (drehen!)"));
    } else {
        Serial.println(F("-- Geometrie unbrauchbar (Schnabel zu steil?)"));
    }

    float hx, hy, hz;
    if (imu.getHardIron(hx, hy, hz)) {
        Serial.print(F("OFFS ")); Serial.print(hx, 1);
        Serial.print(' '); Serial.print(hy, 1);
        Serial.print(' '); Serial.print(hz, 1);
        Serial.println(F(" uT  <- diese Werte wuerde die Firmware speichern"));
    }
    Serial.println();
}

#endif // IMUAXIS_ACTIVE
