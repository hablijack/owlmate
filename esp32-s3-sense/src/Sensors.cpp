#include "Sensors.h"
#include <Adafruit_BNO055.h>
#include <Adafruit_GPS.h>
#include <Preferences.h>

// Calibration offsets are persisted in NVS. The BNO055 forgets its calibration
// on every power cycle, and navigation refuses to aim until imu.calibrated is
// true (rpi-brain/brain/navigation.py), so without this the figure-8 dance
// would be a prerequisite of every single boot.
// --- Vibration: Flankenzaehler im Interrupt --------------------------------
// Der SW-420 liefert Impulsbuendel mit ~1 kHz (auf Hardware gemessen, siehe
// config.h). Den Pegel im Telemetrie-Takt abzufragen kann das prinzipiell nicht
// erfassen, also zaehlen wir Flanken im Interrupt und werten spaeter nur noch
// die ANZAHL aus. CHANGE zaehlt beide Richtungen -> unabhaengig davon, ob das
// Modul im Ruhezustand HIGH oder LOW ausgibt.
static volatile uint32_t vibPulses = 0;      // seit der letzten Auswertung
static volatile uint32_t vibPulsesTotal = 0; // seit dem Boot, nur Diagnose

static void IRAM_ATTR vibrationIsr() {
    vibPulses++;
    vibPulsesTotal++;
}

static Preferences imuPrefs;
static const char* IMU_NVS_NS = "owl-imu";
static const char* IMU_NVS_KEY = "bno-offsets";

static Adafruit_BNO055 bno = Adafruit_BNO055(28, ADDR_BNO055);
static Adafruit_GPS GPS(&Wire);

bool Sensors::begin() {
    _imuReady = false;
    _gpsReady = false;
    _calRestored = false;
    _calSaved = false;

    // Initialize I2C bus (BNO055 + PA1010D GPS + PCA9685 share D0/D1)
    Wire.begin(I2C_SDA, I2C_SCL, I2C_FREQ);

    // Probe the GPS on the I2C bus (no UART pins used anymore)
    Wire.beginTransmission(ADDR_GPS);
    _gpsReady = (Wire.endTransmission() == 0);

    // PA1010D GPS in native I2C mode
    if (_gpsReady) {
        GPS.begin(ADDR_GPS);
        GPS.sendCommand(PMTK_SET_NMEA_OUTPUT_RMCGGA);
        GPS.sendCommand(PMTK_SET_NMEA_UPDATE_1HZ);
    }

    // BNO055 IMU.
    //
    // Expect a burst of ~18 `ESP_ERR_INVALID_STATE` log lines from the I2C HAL
    // here, spanning roughly half a second. They are NOT a fault and the bus is
    // NOT broken: in the IDF 5.x i2c_master driver that error code is what a
    // plain slave NACK is reported as, and Adafruit_BNO055::begin() soft-resets
    // the chip and then polls its ID (`while (read8(CHIP_ID) != BNO055_ID)`)
    // while it reboots. Every poll before the chip answers NACKs and logs.
    // A previous session read this noise as "the I2C bus is dead" -- it is not.
    if (!bno.begin()) {
        return false;
    }
    // NDOF (accel + gyro + MAGNETOMETER), not IMUPLUS. IMUPLUS fuses only accel
    // and gyro, which means (a) there is no absolute magnetic reference, so yaw
    // is a drifting relative heading rather than a compass bearing, and (b) the
    // magnetometer calibration counter stays at 0 forever, so the "calibrated"
    // flag below can never become true. Navigation treats imu.yaw as a true
    // compass heading (see rpi-brain/brain/geo.py aim_angle), so it needs NDOF.
    bno.setMode(OPERATION_MODE_NDOF);

    // Tell the chip how it is physically mounted (bottom-PCB-up) so it fuses in
    // the owl's frame rather than its own: roll/pitch then read ~0 when the owl
    // is level, and yaw becomes a rotation about true vertical. See config.h.
    bno.setAxisRemap((Adafruit_BNO055::adafruit_bno055_axis_remap_config_t)IMU_AXIS_REMAP_CONFIG);
    bno.setAxisSign((Adafruit_BNO055::adafruit_bno055_axis_remap_sign_t)IMU_AXIS_REMAP_SIGN);

    // Restore calibration offsets saved by a previous run, if any.
    {
        adafruit_bno055_offsets_t off;
        if (imuPrefs.begin(IMU_NVS_NS, /*readOnly=*/true)) {
            if (imuPrefs.getBytesLength(IMU_NVS_KEY) == sizeof(off) &&
                imuPrefs.getBytes(IMU_NVS_KEY, &off, sizeof(off)) == sizeof(off)) {
                bno.setSensorOffsets(off);   // handles the CONFIG-mode switch
                _calRestored = true;
                Serial.println(F("IMU: restored calibration offsets from flash"));
            }
            imuPrefs.end();
        }
    }

    // Give BNO055 time to initialize
    delay(100);

    sensor_t sensor;
    bno.getSensor(&sensor);
    _imuReady = true;

    // Der Ruhepegel des Moduls ist nicht zuverlaessig festgelegt (gemessen
    // wurden je nach Poti-Stellung LOW und mittlere Spannungen), deshalb ein
    // Pullup fuer einen definierten Pegel bei abgezogenem Sensor - und ein
    // Interrupt auf CHANGE, der beide Flankenrichtungen zaehlt.
    pinMode(VIBRATION_PIN, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(VIBRATION_PIN), vibrationIsr, CHANGE);
    _vibState = false;
    _vibLastPulse = 0;
    _vibBurstStart = 0;
    _vibLastEvent = 0;
    _vibCount = 0;
    _rapidTapCount = 0;
    _updateSeqPending = false;

    return true;
}

ImuData Sensors::getImu() {
    ImuData data = {0, 0, 0, false, 0, 0, 0, 0, false};

    if (!_imuReady) return data;

    sensors_event_t event;
    bno.getEvent(&event);

    // AXIS ORDER MATTERS. getVector(VECTOR_EULER) reads the BNO055's Euler
    // register block, which starts at EUL_Heading_LSB and runs
    // Heading, Roll, Pitch -- so orientation.x is HEADING (yaw), .y is ROLL and
    // .z is PITCH. All three used to be wired up wrong here (pitch<-roll,
    // roll<-heading, yaw<-pitch), which is why a level owl reported
    // roll = 359.9 deg. Navigation consumes imu.yaw as a compass bearing, so
    // that mix-up silently pointed the head using a pitch angle.
    // Heading, shifted so that 0 means "the beak points at magnetic north".
    data.yaw = event.orientation.x + IMU_HEADING_OFFSET_DEG;
    if (data.yaw < 0.0f) data.yaw += 360.0f;
    else if (data.yaw >= 360.0f) data.yaw -= 360.0f;
    data.roll = event.orientation.y;
    data.pitch = event.orientation.z;

    // Check calibration status
    uint8_t sys, gyro, accel, mag;
    bno.getCalibration(&sys, &gyro, &accel, &mag);
    data.calSys = sys;
    data.calGyro = gyro;
    data.calAccel = accel;
    data.calMag = mag;
    // Two different questions here, deliberately answered differently.
    //
    // (1) "Can the fused HEADING be trusted?" -- this is what navigation needs;
    // rpi-brain/brain/navigation.py refuses to aim while it is false. Heading
    // trust comes from the magnetometer and gyro. It must NOT include `sys`,
    // which is a live fusion-confidence value that dips to 0 whenever the owl
    // is moved, nor `accel`, whose counter falls back to 0 during any sustained
    // motion (that is why the figure-8 for `mag` has to be done BEFORE the
    // static poses for `accel`, not after). Requiring all four made this flag
    // false in 0 of 23 frames on a fully calibrated, offset-restored sensor --
    // navigation could never have engaged.
    data.isCalibrated = (gyro >= 3 && mag >= 3);
    data.calRestored = _calRestored;

    // First time we reach a full calibration, persist the offsets so the next
    // boot starts calibrated. Cheap: the guard makes this a no-op afterwards.
    // First time we reach a full calibration, persist the offsets so the next
    // boot starts calibrated.
    //
    // Two subtleties:
    //  * The isCalibrated gate is REQUIRED, not just conservative:
    //    Adafruit's getSensorOffsets() returns false unless isFullyCalibrated().
    //  * Skip this entirely if we restored offsets at boot. Writing them back
    //    would be a flash write on every single boot for no gain, because
    //    setSensorOffsets() makes the chip report 3/3/3/3 immediately, so this
    //    branch would otherwise fire on every run.
    // (2) "Are these offsets worth writing to flash?" -- a stricter, one-shot
    // question, and all four must read 3. That is also exactly what Adafruit's
    // getSensorOffsets() enforces internally before it will hand them over.
    const bool offsetsWorthSaving = (sys >= 3 && gyro >= 3 && accel >= 3 && mag >= 3);
    if (!_calSaved && !_calRestored && offsetsWorthSaving) {
        adafruit_bno055_offsets_t off;
        if (bno.getSensorOffsets(off)) {
            if (imuPrefs.begin(IMU_NVS_NS, /*readOnly=*/false)) {
                if (imuPrefs.putBytes(IMU_NVS_KEY, &off, sizeof(off)) == sizeof(off)) {
                    _calSaved = true;
                    Serial.println(F("IMU: calibration complete - offsets saved to flash"));
                }
                imuPrefs.end();
            }
        }
    }

    return data;
}

GpsData Sensors::getGps() {
    GpsData data = {0, 0, 0, 0, false};

    if (!_gpsReady) return data;

    // Pump the I2C GPS reader and parse whatever complete sentences arrive.
    //
    // BOUNDED ON PURPOSE. Adafruit_GPS::available() is hardcoded to `return 1`
    // in I2C mode ("I2C doesn't have 'availability' so always has a byte at
    // least to read"), so the obvious `while (GPS.available()) GPS.read();`
    // NEVER terminates -- it hung the whole main loop and froze the eyes. A
    // 512-iteration bound merely turned that into ~512 I2C transfers per call,
    // which cost ~700 ms per telemetry tick.
    //
    // GPS.read() serves bytes from a 32-byte buffer and only touches the bus
    // when it runs dry, so this budget is ~GPS_READ_BUDGET/32 transfers. The
    // PA1010D emits RMC+GGA at 1 Hz (~150 byte/s) and telemetry runs at 2 Hz,
    // so 128 bytes/call (~256 byte/s) keeps up with margin.
    static const int GPS_READ_BUDGET = 128;
    for (int i = 0; i < GPS_READ_BUDGET; i++) {
        GPS.read();
        if (GPS.newNMEAreceived()) {
            GPS.parse(GPS.lastNMEA());   // parse() clears the flag
        }
    }

    if (GPS.fix) {
        data.latitude = GPS.latitudeDegrees;
        data.longitude = GPS.longitudeDegrees;
        data.altitude = GPS.altitude;
        data.satellites = GPS.satellites;
        data.valid = true;
    }

    return data;
}

// Flanken abholen und Zustand fortschreiben. MUSS genau einmal pro
// Hauptschleifendurchlauf laufen (~60 Hz) - siehe Kommentar in Sensors.h.
void Sensors::updateVibration() {
    // Zaehler kurz gesperrt auslesen und nullen, damit zwischen Lesen und
    // Zuruecksetzen keine Flanke verloren geht.
    noInterrupts();
    const uint32_t pulses = vibPulses;
    vibPulses = 0;
    interrupts();

    const uint32_t now = millis();

    // Genug Flanken in diesem ~16-ms-Fenster -> gerade jetzt Erschuetterung.
    // Die Schwelle liegt weit unter einem echten Buendel (~1 kHz Prellen, also
    // etwa 16 Flanken pro Fenster) und weit ueber der Ruhe (gemessen: 0
    // Flanken in 20 s).
    if (pulses >= VIBRATION_PULSE_MIN) {
        // Ein NEUER Klopfer nur, wenn davor lange genug Ruhe war. Weil diese
        // Funktion mit 60 Hz laeuft, schreitet _vibLastPulse waehrend eines
        // Buendels alle ~16 ms mit - der Abstand bleibt also klein und ein
        // durchprellendes Buendel wird nicht mehrfach gezaehlt.
        if (_vibLastPulse == 0 || now - _vibLastPulse > VIBRATION_BURST_GAP_MS) {
            _vibBurstStart = now;
            _vibCount++;

            // Schnellfolge fuer den 4-Tipp-Einstieg in den Update-Modus.
            if (_vibLastEvent != 0 && now - _vibLastEvent <= UPDATE_TAP_GAP_MS) {
                _rapidTapCount++;
            } else {
                _rapidTapCount = 1;
            }
            _vibLastEvent = now;
            if (_rapidTapCount >= UPDATE_TAP_REQUIRED) {
                _rapidTapCount = 0;
                _updateSeqPending = true;
            }
        }
        _vibLastPulse = now;
    }

    // detected haelt nach der letzten Flanke noch nach, damit ein kurzer
    // Klopfer nicht zwischen zwei Telemetrie-Frames durchfaellt.
    _vibState = (_vibLastPulse != 0) && (now - _vibLastPulse <= VIBRATION_HOLD_MS);
}

uint32_t Sensors::vibrationPulseTotal() const {
    return vibPulsesTotal;
}

// Reiner Lesezugriff - keine Nebenwirkungen, mehrfach pro Runde unbedenklich.
VibrationData Sensors::getVibration() {
    VibrationData data;
    data.detected = _vibState;
    data.lastDetected = _vibLastEvent;
    data.count = _vibCount;
    data.updateSequence = _updateSeqPending;
    return data;
}


