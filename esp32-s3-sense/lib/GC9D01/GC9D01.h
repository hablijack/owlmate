#pragma once

#include <Arduino.h>
#include <SPI.h>

#ifndef LCD_WIDTH
#define LCD_WIDTH 160
#endif

#ifndef LCD_HEIGHT
#define LCD_HEIGHT 160
#endif

#ifndef LCD_SPI_FREQ
#define LCD_SPI_FREQ 6000000
#endif

// ============================================================================
// GC9D01 round LCD (Waveshare 0.71", 160x160), built for SEVERAL panels
// sharing ONE SPI bus.
//
// Wiring model: SCK / MOSI / RST are shared by every panel; each panel owns
// only its CS and DC. Hence the constructor takes just dc/cs/rst -- the bus
// pins belong to the SPIClass the caller sets up and injects via attachBus().
//
// TWO THINGS THAT HAVE BURNED THIS PROJECT BEFORE
//
// 1. CS MUST BE DRIVEN IN SOFTWARE, around every transaction. An earlier
//    version passed CS to SPIClass::begin() as the peripheral's hardware SS
//    pin and never touched it again. That works for ONE panel (the peripheral
//    toggles SS for you) and silently breaks for two: the shared bus is begun
//    with SS = -1, so no CS was ever asserted and BOTH panels ignored the bus.
//    Every write path here goes through startWrite()/endWrite().
//
// 2. THERE IS NO DATA-OUT LINE. The panel connector is 8 pins -- VCC, GND,
//    DIN, CLK, CS, DC, RST, BL -- with no SDO/MISO. Nothing can be read back:
//    no ID register, no status, no readback verification of any kind. A "read
//    the panel ID" probe can only ever return 0x00. Do not add one, and do not
//    read 0x00 as evidence that a panel is dead; that misreading cost this
//    project days of chasing a hardware fault that did not exist.
//
// Usage (order matters):
//     SPIClass bus(FSPI);
//     bus.begin(LCD_SCK, -1, LCD_MOSI, -1);   // SS = -1, we do CS ourselves
//     left.attachBus(&bus);                   // parks each CS high as it
//     right.attachBus(&bus);                  //   attaches -- do BOTH first
//     left.resetShared();                     // one pulse of the shared RST
//     left.begin();  right.begin();
// ============================================================================
class GC9D01 {
public:
    // dc/cs are this panel's own pins. rst is shared with every other panel
    // (pass -1 if this panel has no reset line).
    GC9D01(int8_t dc, int8_t cs, int8_t rst);
    ~GC9D01();

    // Attach the shared, already-begun bus and immediately park this panel
    // deselected. Attach EVERY panel before calling begin() on any of them:
    // a panel whose CS is still an undriven input floats selected and will
    // swallow its sibling's init stream.
    void attachBus(SPIClass* bus);

    // Pulse the shared reset line ONCE, before any begin(). Calling begin()
    // per panel must not re-pulse a shared RST -- that would wipe the sibling
    // panel's just-completed init.
    void resetShared();

    bool begin();

    // Push the framebuffer to the panel. Nothing appears on screen until this
    // runs; the drawing calls below only touch the in-memory framebuffer.
    void flush();

    void fillScreen(uint16_t color);
    void drawPixel(int16_t x, int16_t y, uint16_t color);
    void drawFastVLine(int16_t x, int16_t y, int16_t h, uint16_t color);
    void drawFastHLine(int16_t x, int16_t y, int16_t w, uint16_t color);
    void drawLine(int16_t x0, int16_t y0, int16_t x1, int16_t y1, uint16_t color);
    void fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color);
    void fillCircle(int16_t cx, int16_t cy, int16_t r, uint16_t color);

    bool ready() const { return _fb != nullptr && _bus != nullptr; }
    int width() const { return LCD_WIDTH; }
    int height() const { return LCD_HEIGHT; }

private:
    // Nestable transaction window: CS goes low on the outermost startWrite()
    // and back high on the matching endWrite(), so a compound operation
    // (flush = CASET + RASET + RAMWR + pixels) is one single chip-select.
    void startWrite();
    void endWrite();
    void writeCmd(uint8_t cmd, const uint8_t* args = nullptr, size_t n = 0);
    void setWindow(int16_t x0, int16_t y0, int16_t x1, int16_t y1);

    int8_t _dc;
    int8_t _cs;
    int8_t _rst;
    SPIClass* _bus;
    SPISettings _cfg;
    uint8_t _txDepth;
    uint16_t* _fb;
};
