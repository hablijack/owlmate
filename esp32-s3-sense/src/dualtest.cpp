// ============================================================================
// Dual-panel shared-SPI first-light test (env: dualtest, -DDUALTEST_ACTIVE)
//
// Purpose: prove that two GC9D01 panels on ONE SPI bus can be addressed
// independently. This is deliberately standalone and minimal -- no PSRAM, no
// framebuffer, no Eyes/state machine -- so the only thing under test is
// bus + software chip-select + init sequence.
//
// THE BUG THIS EXISTS TO PROVE: lib/GC9D01 never drives CS. In the
// single-panel path it got away with it by passing CS to SPIClass::begin() as
// the HARDWARE SS pin (the peripheral toggled it automatically). The shared-bus
// path passes SS = -1 and adds no software CS, so NEITHER panel is ever
// selected. Here CS is driven explicitly around every transaction.
//
// Pin map: taken from the harness AS PHYSICALLY BUILT (D# labels -> GPIOs;
// on the XIAO ESP32-S3 these differ: D3=4 D4=5 D6=43 D7=44 D8=7 D9=8 D10=9).
//   shared: DIN/MOSI = D8 = GPIO7,  CLK/SCK = D4 = GPIO5,  RST = D6 = GPIO43
//   left  : CS = D7 = GPIO44,  DC = D10 = GPIO9
//   right : CS = D9 = GPIO8,   DC = D3  = GPIO4
// ============================================================================
#if defined(DUALTEST_ACTIVE)

#include <Arduino.h>
#include <SPI.h>

static const int PIN_SCK  = 5;
static const int PIN_MOSI = 7;
static const int PIN_RST  = 43;
static const int PIN_CS_L = 44, PIN_DC_L = 9;
static const int PIN_CS_R = 8,  PIN_DC_R = 4;

static const int16_t W = 160, H = 160;

// Conservative for a first-light test on hand-wired jumpers. Raise later.
static const uint32_t SPI_HZ = 4000000;

static SPIClass bus(FSPI);
static SPISettings spiCfg(SPI_HZ, MSBFIRST, SPI_MODE0);

struct Panel { const char* name; int cs; int dc; };
static const Panel LEFT  = {"left",  PIN_CS_L, PIN_DC_L};
static const Panel RIGHT = {"right", PIN_CS_R, PIN_DC_R};

// RGB565
static const uint16_t BLACK = 0x0000, RED = 0xF800, GREEN = 0x07E0;
static const uint16_t BLUE = 0x001F, WHITE = 0xFFFF, YELLOW = 0xFFE0;

// ---------------------------------------------------------------------------
// One command + its parameters, as ONE transaction, with CS held low across it.
// ---------------------------------------------------------------------------
static void wr(const Panel& p, uint8_t c, const uint8_t* args, size_t n) {
    bus.beginTransaction(spiCfg);
    digitalWrite(p.cs, LOW);
    digitalWrite(p.dc, LOW);          // command phase
    bus.transfer(c);
    if (n) {
        digitalWrite(p.dc, HIGH);     // data phase
        for (size_t i = 0; i < n; i++) bus.transfer(args[i]);
    }
    digitalWrite(p.cs, HIGH);
    bus.endTransaction();
}

#define C0(p, c)          wr((p), (c), nullptr, 0)
#define CN(p, c, ...)     do { static const uint8_t _a[] = {__VA_ARGS__}; \
                               wr((p), (c), _a, sizeof(_a)); } while (0)

// ---------------------------------------------------------------------------
// Waveshare's official 0.71" GC9D01 init sequence. The 0xFE/0xEF pair at the
// top is the command-page unlock -- without it every power/timing/gamma write
// below lands on the wrong page and is silently dropped.
// ---------------------------------------------------------------------------
static void initPanel(const Panel& p) {
    C0(p, 0xFE); C0(p, 0xEF);

    for (uint8_t c = 0x80; c <= 0x8F; c++) { uint8_t v = 0xFF; wr(p, c, &v, 1); }

    CN(p, 0x3A, 0x05);                                    // COLMOD: 16bpp
    CN(p, 0xEC, 0x01);
    CN(p, 0x74, 0x02, 0x0E, 0x00, 0x00, 0x00, 0x00);
    CN(p, 0x98, 0x3E);
    CN(p, 0x99, 0x3E);
    CN(p, 0xB5, 0x0D, 0x0D);
    CN(p, 0x60, 0x38, 0x0F, 0x79, 0x67);
    CN(p, 0x61, 0x38, 0x11, 0x79, 0x67);
    CN(p, 0x64, 0x38, 0x17, 0x71, 0x5F, 0x79, 0x67);
    CN(p, 0x65, 0x38, 0x13, 0x71, 0x5B, 0x79, 0x67);
    CN(p, 0x6A, 0x00, 0x00);
    CN(p, 0x6C, 0x22, 0x02, 0x22, 0x02, 0x22, 0x22, 0x50);
    CN(p, 0x6E, 0x03, 0x03, 0x01, 0x01, 0x00, 0x00, 0x0F, 0x0F,
               0x0D, 0x0D, 0x0B, 0x0B, 0x09, 0x09, 0x00, 0x00,
               0x00, 0x00, 0x0A, 0x0A, 0x0C, 0x0C, 0x0E, 0x0E,
               0x10, 0x10, 0x00, 0x00, 0x02, 0x02, 0x04, 0x04);
    CN(p, 0xBF, 0x01);
    CN(p, 0xF9, 0x40);
    CN(p, 0x9B, 0x3B);
    CN(p, 0x93, 0x33, 0x7F, 0x00);
    CN(p, 0x7E, 0x30);
    CN(p, 0x70, 0x0D, 0x02, 0x08, 0x0D, 0x02, 0x08);
    CN(p, 0x71, 0x0D, 0x02, 0x08);
    CN(p, 0x91, 0x0E, 0x09);
    CN(p, 0xC3, 0x19);
    CN(p, 0xC4, 0x19);
    CN(p, 0xC9, 0x3C);
    CN(p, 0xF0, 0x53, 0x15, 0x0A, 0x04, 0x00, 0x3E);
    CN(p, 0xF2, 0x53, 0x15, 0x0A, 0x04, 0x00, 0x3A);
    CN(p, 0xF1, 0x56, 0xA8, 0x7F, 0x33, 0x34, 0x5F);
    CN(p, 0xF3, 0x52, 0xA4, 0x7F, 0x33, 0x34, 0xDF);
    CN(p, 0x36, 0x00);                                    // MADCTL

    C0(p, 0x11); delay(200);                              // SLPOUT
    C0(p, 0x29); delay(20);                               // DISPON
}

static void setWindow(const Panel& p, int16_t x0, int16_t y0, int16_t x1, int16_t y1) {
    uint8_t a[4];
    a[0] = x0 >> 8; a[1] = x0 & 0xFF; a[2] = x1 >> 8; a[3] = x1 & 0xFF;
    wr(p, 0x2A, a, 4);                                    // CASET
    a[0] = y0 >> 8; a[1] = y0 & 0xFF; a[2] = y1 >> 8; a[3] = y1 & 0xFF;
    wr(p, 0x2B, a, 4);                                    // RASET
}

// Two horizontal bands, so this also proves the address window works (a panel
// echoing its sibling's stream would not land the split in the right place).
static void fillSplit(const Panel& p, uint16_t top, uint16_t bottom) {
    setWindow(p, 0, 0, W - 1, H - 1);

    uint8_t line[W * 2];
    bus.beginTransaction(spiCfg);
    digitalWrite(p.cs, LOW);
    digitalWrite(p.dc, LOW);
    bus.transfer(0x2C);                                   // RAMWR
    digitalWrite(p.dc, HIGH);
    for (int16_t y = 0; y < H; y++) {
        const uint16_t c = (y < H / 2) ? top : bottom;
        for (int16_t x = 0; x < W; x++) {
            line[x * 2]     = c >> 8;                     // panel wants MSB first
            line[x * 2 + 1] = c & 0xFF;
        }
        bus.writeBytes(line, sizeof(line));
    }
    digitalWrite(p.cs, HIGH);
    bus.endTransaction();
}

static void fillSolid(const Panel& p, uint16_t c) { fillSplit(p, c, c); }

void setup() {
    Serial.begin(115200);
    uint32_t t0 = millis();
    while (!Serial && millis() - t0 < 3000) delay(10);
    delay(200);

    Serial.println();
    Serial.println(F("=== GC9D01 dual-panel shared-SPI test ==="));
    Serial.printf("SCK=%d MOSI=%d RST=%d | LEFT cs=%d dc=%d | RIGHT cs=%d dc=%d | %lu Hz\n",
                  PIN_SCK, PIN_MOSI, PIN_RST, PIN_CS_L, PIN_DC_L,
                  PIN_CS_R, PIN_DC_R, (unsigned long)SPI_HZ);

    // BOTH chip-selects must be OUTPUT+HIGH before either panel is touched,
    // or the un-initialised panel floats selected and eats its sibling's
    // init stream.
    pinMode(PIN_CS_L, OUTPUT); digitalWrite(PIN_CS_L, HIGH);
    pinMode(PIN_CS_R, OUTPUT); digitalWrite(PIN_CS_R, HIGH);
    pinMode(PIN_DC_L, OUTPUT); digitalWrite(PIN_DC_L, HIGH);
    pinMode(PIN_DC_R, OUTPUT); digitalWrite(PIN_DC_R, HIGH);
    Serial.println(F("[1] both CS parked HIGH (deselected)"));

    bus.begin(PIN_SCK, -1, PIN_MOSI, -1);   // SS = -1: we do CS in software
    Serial.println(F("[2] SPI bus up (software chip-select)"));

    // Shared RST: pulse ONCE for both panels, before either init.
    pinMode(PIN_RST, OUTPUT);
    digitalWrite(PIN_RST, HIGH); delay(20);
    digitalWrite(PIN_RST, LOW);  delay(20);
    digitalWrite(PIN_RST, HIGH); delay(150);
    Serial.println(F("[3] shared RST pulsed once"));

    initPanel(LEFT);
    Serial.println(F("[4] LEFT initialised"));
    initPanel(RIGHT);
    Serial.println(F("[5] RIGHT initialised"));

    Serial.println(F("[6] entering pattern loop"));
    Serial.println(F("--------------------------------------------------"));
}

void loop() {
    Serial.println(F("A: LEFT=solid RED     RIGHT=solid BLUE"));
    fillSolid(LEFT, RED);   fillSolid(RIGHT, BLUE);   delay(4000);

    Serial.println(F("B: LEFT=solid BLUE    RIGHT=solid RED   (swapped)"));
    fillSolid(LEFT, BLUE);  fillSolid(RIGHT, RED);    delay(4000);

    Serial.println(F("C: LEFT=GREEN         RIGHT=BLACK       (one at a time)"));
    fillSolid(LEFT, GREEN); fillSolid(RIGHT, BLACK); delay(4000);

    Serial.println(F("D: LEFT=BLACK         RIGHT=GREEN"));
    fillSolid(LEFT, BLACK); fillSolid(RIGHT, GREEN); delay(4000);

    Serial.println(F("E: LEFT=WHITE/RED split  RIGHT=YELLOW/BLUE split"));
    fillSplit(LEFT, WHITE, RED); fillSplit(RIGHT, YELLOW, BLUE); delay(5000);
}

#endif // DUALTEST_ACTIVE
