#pragma once
// ============================================================================
// ServoCal - Kalibrierwerkzeug fuer EINEN Servo auf einmal (SPEC-016, BACKLOG
// "staged servo calibration plan", Stufe 3).
//
// Das ist NICHT das normale Servo-Treibermodul und es gibt keinen Weg dorthin:
// ServoController bleibt gesperrt (SERVO_SAFETY_LOCKED=1). Dieses Modul wird
// nur vom Diagnose-Env `servocal` gebaut und spricht das PCA9685 ueber rohe
// Register an - nie ueber Adafruits begin()/setPWMFreq()/writeMicroseconds().
//
// Garantien, jede davon durch tests/servocal/test.cpp gegen ein Fake-PCA9685
// geprueft:
//   * Nichts schreibt ohne ARM; ARM schreibt nur MODE1/PRE_SCALE, nie einen
//     Kanalausgang.
//   * ARM verweigert, wenn irgendein ANDERER Kanal einen Puls ausgibt (der
//     Besitzer steckt nur den Servo unter Test an) oder der Kanal unter Test
//     einen Wert ausserhalb der Huelle hat.
//   * Frequenz wird nur geaendert, solange ALLE Kanaele aus sind - dann kann
//     sich kein Servo bewegen.
//   * Der erste Puls ist ein absoluter Wert mit Bestaetigung (FIRST + YES);
//     kleine Schritte machen den ersten Puls nicht sicher. STEP auf einem
//     inaktiven Kanal wird abgelehnt - es gibt keinen erfundenen Startwert.
//   * STEP ist klein (<= MAX_STEP_US), zeitlich begrenzt, geht von dem aus,
//     was das Register WIRKLICH enthaelt, und bleibt in der Huelle UND in den
//     per LIMIT enger gesetzten Grenzen (nie weiter).
//   * Kein automatischer Rueckweg, keine Wiederholung, kein Zentrieren.
//     STOP schaltet den Kanal auf "full off" - er gibt KEINEN Zielwert aus.
//   * Es wird nur auf den Kanal geschrieben, der scharf ist.
//
// Einheiten: PCA9685-Ticks. Mit PRE_SCALE=121 ist ein Tick (121+1)/25 MHz =
// 4,88 us. Mikrosekunden sind NOMINAL (+- Toleranz des internen Oszillators).
// ============================================================================
#include <stdint.h>
#include <stddef.h>

class ServoCalBus {
public:
    virtual ~ServoCalBus() {}
    virtual bool read(uint8_t reg, uint8_t* buf, uint8_t n) = 0;
    virtual bool write(uint8_t reg, const uint8_t* buf, uint8_t n) = 0;
    virtual void delayMs(uint32_t ms) = 0;
};

class ServoCal {
public:
    // allowedMask: Bit n gesetzt = PCA9685-Kanal n darf scharf gemacht werden.
    // extLowMask: Kanaele, deren untere Huellgrenze per Besitzerentscheidung auf
    // EXT_LO_TICKS (~500 us) gesenkt ist. Alle anderen bleiben bei ENV_LO_TICKS.
    // extHighMask: dito fuer die obere Grenze (EXT_HI_TICKS, ~2500 us).
    ServoCal(ServoCalBus& bus, uint16_t allowedMask, uint16_t extLowMask = 0,
             uint16_t extHighMask = 0);

    // Eine Textzeile verarbeiten (ohne Zeilenende). Antwort nach out.
    void handleLine(const char* line, uint32_t nowMs, char* out, size_t cap);
    // Regelmaessig aufrufen (Watchdog). out bleibt leer, wenn nichts passiert.
    void tick(uint32_t nowMs, char* out, size_t cap);

    bool armed() const { return _armed; }
    int armedChannel() const { return _armed ? _channel : -1; }

    static const uint8_t  PRESCALE = 121;       // ~50 Hz
    static const uint16_t TICK_US_X100 = 488;   // 122 / 25 MHz
    static const uint16_t ENV_LO_TICKS = 205;   // 1000 us
    static const uint16_t ENV_HI_TICKS = 410;   // 2000 us
    static const uint16_t EXT_LO_TICKS = 102;   // ~498 us, nur fuer extLowMask
    static const uint16_t EXT_HI_TICKS = 512;   // ~2499 us, nur fuer extHighMask
    static const uint8_t  MAX_STEP_US = 15;
    static const uint32_t MIN_INTERVAL_MS = 150;
    static const uint32_t CONFIRM_TIMEOUT_MS = 15000;
    static const uint32_t WATCHDOG_MS = 120000;

private:
    enum Kind { FULL_OFF, FULL_ON, PULSE, UNKNOWN };
    struct Chan { Kind kind; uint16_t ticks; };

    bool readChan(uint8_t ch, Chan& c);
    bool writePulse(uint8_t ch, uint16_t ticks);
    bool writeFullOff(uint8_t ch);
    void fail(char* out, size_t cap, const char* what);
    void cmdHelp(char* out, size_t cap);
    void cmdStatus(char* out, size_t cap);
    void cmdArm(const char* arg, char* out, size_t cap);
    void cmdFirst(const char* arg, uint32_t now, char* out, size_t cap);
    void cmdYes(uint32_t now, char* out, size_t cap);
    void cmdStep(const char* arg, uint32_t now, char* out, size_t cap);
    void cmdLimit(const char* a, const char* b, char* out, size_t cap);
    void cmdStop(const char* why, char* out, size_t cap);

    uint16_t envLo(uint8_t ch) const;
    uint16_t envHi(uint8_t ch) const;
    static uint16_t usToTicks(long us);
    static long ticksToUs(uint16_t ticks);

    ServoCalBus& _bus;
    uint16_t _allowed;
    uint16_t _extLow;
    uint16_t _extHigh;
    bool _armed;
    uint8_t _channel;
    uint16_t _limLo, _limHi;
    bool _hasWritten;
    uint32_t _lastWriteMs;
    uint32_t _lastCmdMs;
    bool _pending;
    uint16_t _pendingTicks;
    uint32_t _pendingDeadline;
};
