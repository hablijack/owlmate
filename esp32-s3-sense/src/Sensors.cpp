#include "Sensors.h"
#include "imu_config.h"
#include <Adafruit_GPS.h>
#include <Preferences.h>

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

// Hard-iron offsets are persisted in NVS. Nothing about the magnetometer
// survives a power cycle by itself, and navigation refuses to aim until
// imu.calibrated is true (rpi-brain/brain/navigation.py), so without this the
// calibration turn would be a prerequisite of every single boot.
//
// Three floats (x/y/z in microtesla), not the BNO055's opaque 22-byte offset
// blob -- so the old key is not merely stale, it is a different shape. It is
// deleted below if found, because a leftover `bno-offsets` in an NVS dump is
// exactly the sort of thing that costs an hour at the bench.
static Preferences imuPrefs;
static const char* IMU_NVS_NS = "owl-imu";
static const char* IMU_NVS_KEY = "mag-hardiron";
static const char* IMU_NVS_KEY_OLD = "bno-offsets";

static OwlImu owlImu;
static Adafruit_GPS GPS(&Wire);

// 9 Taktimpulse auf SCL, danach eine Stop-Bedingung: holt einen Slave aus einer
// abgebrochenen Uebertragung zurueck und gibt den Bus wieder frei.
//
// Gebraucht, weil ein nicht antwortender Slave den Bus mit sich reissen kann.
// Ohne diese Rettung faellt mit ihm auch GPS und Servotreiber aus - gemessen
// 2026-08-28 am damaligen BNO055: nach fehlgeschlagenem IMU-Init meldete der
// PCA9685 "not found", obwohl er nachweislich in Ordnung ist.
static void i2cBusRecover() {
    Wire.end();
    pinMode(I2C_SDA, INPUT_PULLUP);
    pinMode(I2C_SCL, OUTPUT);
    for (int i = 0; i < 9; i++) {
        digitalWrite(I2C_SCL, LOW);  delayMicroseconds(5);
        digitalWrite(I2C_SCL, HIGH); delayMicroseconds(5);
    }
    pinMode(I2C_SDA, OUTPUT);        // Stop: SDA steigt waehrend SCL hoch ist
    digitalWrite(I2C_SDA, LOW);  delayMicroseconds(5);
    digitalWrite(I2C_SCL, HIGH); delayMicroseconds(5);
    digitalWrite(I2C_SDA, HIGH); delayMicroseconds(5);
    pinMode(I2C_SDA, INPUT_PULLUP);
    pinMode(I2C_SCL, INPUT_PULLUP);
    delay(5);
    Wire.begin(I2C_SDA, I2C_SCL, I2C_FREQ);
    Wire.setTimeOut(I2C_TIMEOUT_MS);
    delay(5);
}

bool Sensors::begin() {
    _imuReady = false;
    _gpsReady = false;
    _calSaved = false;

    // Initialize I2C bus (LSM303AGR + PA1010D GPS + PCA9685 share D0/D1)
    Wire.begin(I2C_SDA, I2C_SCL, I2C_FREQ);
    // Grosszuegiger Timeout, sonst reisst ein taktdehnender Slave den GANZEN
    // Bus mit sich (siehe I2C_TIMEOUT_MS in config.h). Muss nach begin()
    // stehen - begin() setzt den Wert zurueck.
    Wire.setTimeOut(I2C_TIMEOUT_MS);

    // LSM303AGR IMU: Beschleunigung @0x19 + LIS2MDL-Magnetometer @0x1E.
    //
    // Kein Fehlerburst mehr an dieser Stelle. Der BNO055 davor hat sich beim
    // Init selbst zurueckgesetzt und dann seine ID gepollt, waehrend er neu
    // startete - ~18 NACKs ueber ~490 ms, bei JEDEM Boot, die eine Sitzung
    // lang fuer einen defekten Bus gehalten wurden. Der LSM303AGR antwortet
    // sofort. Ein NACK-Burst hier ist jetzt also ein echter Fehler.
    if (!owlImu.begin(owlImuConfigFromHeader(), &Wire)) {
        // NICHT fatal. Der IMU haengt am selben Bus wie GPS und Servotreiber;
        // ihn zum Boot-Abbruch zu machen heisst, wegen eines fehlenden
        // Kompasses auch Kamera, Gesichtserkennung, Servos und GPS aufzugeben.
        // Ohne IMU faellt genau eine Faehigkeit aus: die Navigation - und die
        // ist ohnehin blockiert, solange keine Kalibrierung vorliegt
        // (navigation.py verweigert dann die Zielansage).
        //
        // Die Telemetrie laesst das Objekt "imu" dann weg, was auf der RPi-Seite
        // bereits als "Geraet nicht vorhanden" gilt.
        //
        // OwlImu::begin() hat oben schon gesagt, WELCHE Haelfte fehlt und ob es
        // nach dem falschen (blauen) LSM303DLHC aussieht.
        Serial.println(F("WARNING: LSM303AGR not responding - continuing without IMU"));
        _imuReady = false;
        // Den Bus freiraeumen, den der gescheiterte Init hinterlassen hat -
        // sonst sind GPS und Servotreiber gleich mit verloren.
        i2cBusRecover();
    } else {
        // Hartmagnet-Offsets aus einem frueheren Lauf einspielen, falls da.
        {
            float off[3];
            if (imuPrefs.begin(IMU_NVS_NS, /*readOnly=*/false)) {
                if (imuPrefs.getBytesLength(IMU_NVS_KEY) == sizeof(off) &&
                    imuPrefs.getBytes(IMU_NVS_KEY, off, sizeof(off)) == sizeof(off)) {
                    owlImu.setHardIron(off[0], off[1], off[2]);
                    Serial.print(F("IMU: Hartmagnet-Offsets aus dem Flash: "));
                    Serial.print(off[0], 1); Serial.print(' ');
                    Serial.print(off[1], 1); Serial.print(' ');
                    Serial.print(off[2], 1); Serial.println(F(" uT"));
                }
                // Einmalige Altlast: der BNO055-Offsetblock hat eine andere
                // Form und wuerde am Bench nur verwirren.
                if (imuPrefs.isKey(IMU_NVS_KEY_OLD)) {
                    imuPrefs.remove(IMU_NVS_KEY_OLD);
                    Serial.println(F("IMU: alten BNO055-Offsetschluessel entfernt"));
                }
                imuPrefs.end();
            }
        }
        _imuReady = true;
    }

    // GPS ERST JETZT pruefen, nach dem IMU. Ein gescheiterter IMU-Init reisst
    // den Bus kurzzeitig mit; wer vorher probt, merkt sich ein Ergebnis, das
    // danach nicht mehr stimmt - und pollt dann bei jedem Telemetrieframe ein
    // Geraet, das gar nicht antwortet. Das kostet pro Lesevorgang den vollen
    // I2C_TIMEOUT_MS und legt die Hauptschleife lahm.
    Wire.beginTransmission(ADDR_GPS);
    _gpsReady = (Wire.endTransmission() == 0);

    // PA1010D GPS in native I2C mode
    if (_gpsReady) {
        GPS.begin(ADDR_GPS);
        GPS.sendCommand(PMTK_SET_NMEA_OUTPUT_RMCGGA);
        GPS.sendCommand(PMTK_SET_NMEA_UPDATE_1HZ);
    }

    // Der Ruhepegel des Moduls ist nicht zuverlaessig festgelegt (gemessen
    // wurden je nach Poti-Stellung LOW und mittlere Spannungen), deshalb ein
    // Pullup fuer einen definierten Pegel bei abgezogenem Sensor - und ein
    // Interrupt auf CHANGE, der beide Flankenrichtungen zaehlt.
    pinMode(VIBRATION_PIN, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(VIBRATION_PIN), vibrationIsr, CHANGE);
    _vibState = false;
    _vibLastPulse = 0;
    _vibLastEvent = 0;
    _vibCount = 0;
    _rapidTapCount = 0;
    _updateSeqPending = false;

    return true;
}

void Sensors::updateImu() {
    if (!_imuReady) return;

    // Bus lesen, filtern, Min/Max fuer die Kalibrierung mitschreiben. Begrenzt
    // sich selbst auf IMU_SAMPLE_INTERVAL_MS, darf also im Schleifentakt
    // gerufen werden.
    owlImu.update();

    // Beim ERSTEN Mal, dass die Drehung genug Achsen abgedeckt hat, die
    // Offsets ins Flash schreiben - dann startet der naechste Boot kalibriert.
    //
    // Zwei Feinheiten, beide beim BNO055 gelernt:
    //  * Der Wachposten macht das danach zu einem No-op, sonst schreibt jede
    //    Sekunde ins Flash.
    //  * Wurde in DIESEM Boot aus dem Flash restauriert, wird trotzdem
    //    geschrieben, sobald eine frische Drehung bessere Offsets liefert -
    //    aber nur einmal. Beim BNO055 musste dieser Fall komplett gesperrt
    //    werden, weil setSensorOffsets() den Chip sofort 3/3/3/3 melden liess
    //    und der Zweig dann bei jedem Boot feuerte. Hier gibt es dieses Problem
    //    nicht: getHardIron() liefert nur etwas, wenn die Spanne in DIESEM Lauf
    //    wirklich beobachtet wurde.
    if (_calSaved) return;
    float x, y, z;
    if (!owlImu.getHardIron(x, y, z)) return;

    float off[3] = {x, y, z};
    if (imuPrefs.begin(IMU_NVS_NS, /*readOnly=*/false)) {
        if (imuPrefs.putBytes(IMU_NVS_KEY, off, sizeof(off)) == sizeof(off)) {
            _calSaved = true;
            Serial.print(F("IMU: Kalibrierung gespeichert: "));
            Serial.print(x, 1); Serial.print(' ');
            Serial.print(y, 1); Serial.print(' ');
            Serial.print(z, 1); Serial.println(F(" uT"));
        }
        imuPrefs.end();
    }
}

ImuData Sensors::getImu() {
    // REINER GETTER (siehe Sensors.h). Value-initialised, NOT a positional
    // {0,0,0,false,...} list: that list had to be kept in the struct's field
    // order by hand, and inserting a field would have silently shifted every
    // value after it.
    ImuData data{};

    if (!_imuReady || !owlImu.ok()) return data;

    const OwlImuSample s = owlImu.read();
    data.pitch = s.pitch;
    data.roll = s.roll;
    data.yaw = s.yaw;
    // Zwei verschiedene Fragen, absichtlich verschieden beantwortet - genau die
    // Unterscheidung, deren Fehlen beim BNO055 die Navigation dauerhaft
    // blockiert hat (SPEC-006 Falsified).
    //
    // (1) "Kann man dem Kurs trauen?" -> isCalibrated. Das braucht die
    //     Navigation. Es sind ZWEI Bedingungen: es liegen Hartmagnet-Offsets
    //     vor UND die Geometrie taugt gerade.
    data.isCalibrated = s.haveOffsets && s.headingOk;
    // (2) "Wie weit ist die Drehung?" -> magAxes, ein Fortschrittsbalken.
    //     Der darf ruhig 0 sein, waehrend isCalibrated true ist: naemlich genau
    //     dann, wenn die Offsets aus dem Flash kamen und in diesem Lauf noch
    //     niemand gedreht hat. Das ist der Normalfall nach einem Neustart.
    data.magAxes = s.magAxes;
    data.headingOk = s.headingOk;
    data.calRestored = s.calRestored;

    return data;
}

GpsData Sensors::getGps() {
    GpsData data{};

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
    // GPS.read() serves bytes from a 32-byte buffer. Die PA1010D sendet RMC+GGA
    // mit 1 Hz (~150 Byte/s), die Telemetrie laeuft mit 2 Hz, also reichen
    // 128 Byte je Aufruf (~256 Byte/s) mit Reserve.
    //
    // WIDERLEGT 2026-08-31, und der Satz stand vorher genau hier: "read()
    // beruehrt den Bus nur, wenn der Puffer leer laeuft, also kostet dieses
    // Budget ~GPS_READ_BUDGET/32 Transfers". Das gilt NUR, solange die GPS
    // etwas zu sagen hat. Hat sie nichts, schickt sie 0x0A-Fuellbytes; die
    // Bibliothek wirft doppelte 0x0A weg, setzt _buff_max auf -1, und dann ist
    // `_buff_idx <= _buff_max` bei JEDEM Aufruf falsch - also macht JEDER der
    // 128 read()-Aufrufe einen eigenen 32-Byte-Transfer. Bei 400 kHz sind das
    // rund 1 ms pro Stueck, macht ~128 ms in EINER Runde der Hauptschleife.
    //
    // Auf Hardware gemessen 2026-08-31: loop_max_ms wechselte im Leerlauf
    // regelmaessig zwischen 53 und 127 ms - der teure Wert immer dann, wenn
    // zwischen zwei Telemetrieframes kein NMEA-Satz kam. Das ist der Rest des
    // "die Augen ruckeln", der nach der Reparatur des USB-Schreibens uebrig
    // blieb.
    //
    // Deshalb wird jetzt nach GPS_DRY_READS leeren Lesungen abgebrochen. Solange
    // Daten fliessen, liefert read() Zeichen und der Zaehler faellt auf 0 zurueck
    // - der Durchsatz aendert sich also nicht. Ein direkt nach einem Nachfuellen
    // zurueckgegebenes 0 ist normal (read() liefert dann kein Zeichen), darum 3
    // und nicht 1.
    static const int GPS_READ_BUDGET = 128;
    static const int GPS_DRY_READS = 3;
    int dry = 0;
    for (int i = 0; i < GPS_READ_BUDGET; i++) {
        const char c = GPS.read();
        if (GPS.newNMEAreceived()) {
            GPS.parse(GPS.lastNMEA());   // parse() clears the flag
        }
        if (c == 0) {
            if (++dry >= GPS_DRY_READS) break;
        } else {
            dry = 0;
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
    VibrationData data{};
    data.detected = _vibState;
    data.lastDetected = _vibLastEvent;
    data.count = _vibCount;
    data.updateSequence = _updateSeqPending;
    return data;
}


