// ============================================================================
// Netzteil-/Versorgungsdiagnose (POWERPROBE).
//
// Zweck: unterscheiden, ob die 3,3-V-Schiene grundsaetzlich tot ist oder nur
// von einem Verbraucher (Backlights / I2C-Bus) heruntergezogen wird. Bootet die
// Platine MIT diesem Abbild stabil, aber mit der vollen Firmware in einer
// Brownout-Schleife (RTC_SW_SYS_RST), dann traegt die Schiene die leichte Last
// und das Problem ist ein Peripheriegeraet.
//
// Damit dieser Vergleich etwas aussagt, muss das Abbild wirklich LEICHT sein:
// kein PSRAM, kein LCD, keine Kamera, kein I2C, keine Servos. Genau deshalb hat
// das Env `build_src_filter = +<powerprobe.cpp>` -- es kompiliert AUSSCHLIESSLICH
// diese Datei.
//
// GESCHICHTE: dieser Code stand bis 2026-08-27 als `#if POWER_PROBE`-Zweig in
// setup()/loop() von main.cpp. Dem Env fehlte dabei ein build_src_filter, es
// baute also die komplette Firmware samt aller Peripherie-Objekte und sprang
// erst in setup() per `return` heraus -- das Gegenteil eines Minimalabbilds und
// damit als Vergleichsmessung wertlos.
//
// Benutzung:
//   pio run -e powerprobe && pio run -e powerprobe -t upload
//   pio run -e powerprobe -t monitor
// Zurueck zur echten Firmware:  pio run && pio run -t upload
// ============================================================================
#if defined(POWERPROBE_ACTIVE)

#include <Arduino.h>

// XIAO ESP32-S3: der 5-V-EINGANG wird auf D4 = GPIO5 = ADC1_CH4 ueber einen
// 2:1-Teiler abgegriffen. Eine gesunde 5-V-Versorgung liest hier also ~2500 mV.
//
// FALLE: GPIO7 ist auf dem S3 NICHT ADC-faehig (ADC1 = GPIO1-10,
// ADC2 = GPIO11-20). Eine fruehere Fassung las analogRead(7) und bekam darum
// immer 0 -- was wie eine tote Schiene aussah, aber nur der falsche Pin war.
static const int PIN_VIN_SENSE = 5;   // D4 = GPIO5 = ADC1_CH4
static const long ADC_MAX = 4095;
static const long ADC_REF_MV = 3300;

static int readVinMv(int& rawOut) {
    rawOut = analogRead(PIN_VIN_SENSE);
    // 2:1-Teiler -> Vollausschlag entspricht 2 * ADC_REF_MV.
    return (int)((rawOut * ADC_REF_MV) / (ADC_MAX / 2));
}

void setup() {
    Serial.begin(115200);
    delay(100);
    Serial.println(F("POWER-PROBE: up (no PSRAM/LCD/camera/I2C/servo)"));
    Serial.println(F("POWER-PROBE: 5Vin sense on D4=GPIO5=ADC1_CH4, 2:1 divider"));
    Serial.println(F("POWER-PROBE: healthy 5V reads ~2500 mV; watch for a sag"));
    Serial.println(F("POWER-PROBE: below ~2000-2200 mV (=> <4.0-4.4 V input),"));
    Serial.println(F("POWER-PROBE: especially in the first second after boot."));
}

void loop() {
    // Rollende Messung, 1 Hz.
    int raw = 0;
    const int vccMv = readVinMv(raw);
    Serial.printf("t=%lu  5Vin~%d mV  (raw %d)\n",
                  (unsigned long)millis(), vccMv, raw);
    delay(1000);
}

#endif  // POWERPROBE_ACTIVE
