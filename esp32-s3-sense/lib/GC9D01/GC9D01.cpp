#include "GC9D01.h"

// GC9D01 commands actually used by this driver.
#define GC9D01_SLPOUT 0x11
#define GC9D01_DISPON 0x29
#define GC9D01_CASET  0x2A
#define GC9D01_RASET  0x2B
#define GC9D01_RAMWR  0x2C

// ============================================================================
// Waveshare's official 0.71" GC9D01 power-on sequence, as a flat
// {command, argc, args...} byte stream.
//
// The leading 0xFE / 0xEF pair is the command-page unlock. Without it every
// power / timing / gamma register below is written to the wrong page and
// silently dropped, and the panel sits there with its backlight lit and a
// black screen -- indistinguishable from a dead panel.
//
// Each entry is sent as ONE chip-select assertion (command byte with DC low,
// then its parameters with DC high). Splitting a command from its parameters
// across two CS assertions is not safe on this controller.
// ============================================================================
static const uint8_t kInitSeq[] = {
    0xFE, 0,
    0xEF, 0,

    // Unlock/enable registers 0x80..0x8F.
    0x80, 1, 0xFF,   0x81, 1, 0xFF,   0x82, 1, 0xFF,   0x83, 1, 0xFF,
    0x84, 1, 0xFF,   0x85, 1, 0xFF,   0x86, 1, 0xFF,   0x87, 1, 0xFF,
    0x88, 1, 0xFF,   0x89, 1, 0xFF,   0x8A, 1, 0xFF,   0x8B, 1, 0xFF,
    0x8C, 1, 0xFF,   0x8D, 1, 0xFF,   0x8E, 1, 0xFF,   0x8F, 1, 0xFF,

    0x3A, 1, 0x05,                                       // COLMOD: RGB565
    0xEC, 1, 0x01,
    0x74, 6, 0x02, 0x0E, 0x00, 0x00, 0x00, 0x00,
    0x98, 1, 0x3E,
    0x99, 1, 0x3E,
    0xB5, 2, 0x0D, 0x0D,

    0x60, 4, 0x38, 0x0F, 0x79, 0x67,
    0x61, 4, 0x38, 0x11, 0x79, 0x67,
    0x64, 6, 0x38, 0x17, 0x71, 0x5F, 0x79, 0x67,
    0x65, 6, 0x38, 0x13, 0x71, 0x5B, 0x79, 0x67,
    0x6A, 2, 0x00, 0x00,
    0x6C, 7, 0x22, 0x02, 0x22, 0x02, 0x22, 0x22, 0x50,
    0x6E, 32, 0x03, 0x03, 0x01, 0x01, 0x00, 0x00, 0x0F, 0x0F,
              0x0D, 0x0D, 0x0B, 0x0B, 0x09, 0x09, 0x00, 0x00,
              0x00, 0x00, 0x0A, 0x0A, 0x0C, 0x0C, 0x0E, 0x0E,
              0x10, 0x10, 0x00, 0x00, 0x02, 0x02, 0x04, 0x04,

    0xBF, 1, 0x01,
    0xF9, 1, 0x40,
    0x9B, 1, 0x3B,
    0x93, 3, 0x33, 0x7F, 0x00,
    0x7E, 1, 0x30,
    0x70, 6, 0x0D, 0x02, 0x08, 0x0D, 0x02, 0x08,
    0x71, 3, 0x0D, 0x02, 0x08,
    0x91, 2, 0x0E, 0x09,
    0xC3, 1, 0x19,
    0xC4, 1, 0x19,
    0xC9, 1, 0x3C,

    // Gamma.
    0xF0, 6, 0x53, 0x15, 0x0A, 0x04, 0x00, 0x3E,
    0xF2, 6, 0x53, 0x15, 0x0A, 0x04, 0x00, 0x3A,
    0xF1, 6, 0x56, 0xA8, 0x7F, 0x33, 0x34, 0x5F,
    0xF3, 6, 0x52, 0xA4, 0x7F, 0x33, 0x34, 0xDF,

    0x36, 1, 0x00,                                       // MADCTL
};

GC9D01::GC9D01(int8_t dc, int8_t cs, int8_t rst)
    : _dc(dc), _cs(cs), _rst(rst), _bus(nullptr),
      _cfg(LCD_SPI_FREQ, MSBFIRST, SPI_MODE0), _txDepth(0), _fb(nullptr) {
}

GC9D01::~GC9D01() {
    if (_fb) free(_fb);
}

void GC9D01::attachBus(SPIClass* bus) {
    _bus = bus;
    // Park this panel deselected the moment it is attached, so that attaching
    // every panel before initialising any of them is enough to guarantee no
    // panel is listening while a sibling is being set up.
    pinMode(_cs, OUTPUT);
    digitalWrite(_cs, HIGH);
    pinMode(_dc, OUTPUT);
    digitalWrite(_dc, HIGH);
}

void GC9D01::resetShared() {
    if (_rst < 0) return;
    pinMode(_rst, OUTPUT);
    digitalWrite(_rst, HIGH);
    delay(20);
    digitalWrite(_rst, LOW);
    delay(20);
    digitalWrite(_rst, HIGH);
    delay(150);
}

bool GC9D01::begin() {
    if (!_bus) return false;              // attachBus() was never called

    _fb = (uint16_t*)ps_calloc((size_t)LCD_WIDTH * LCD_HEIGHT, sizeof(uint16_t));
    if (!_fb) return false;               // no PSRAM framebuffer

    // The shared RST has already been pulsed once by resetShared(); just make
    // sure it stays released for the whole init.
    if (_rst >= 0) {
        pinMode(_rst, OUTPUT);
        digitalWrite(_rst, HIGH);
    }

    for (size_t i = 0; i < sizeof(kInitSeq); ) {
        const uint8_t cmd = kInitSeq[i++];
        const uint8_t argc = kInitSeq[i++];
        writeCmd(cmd, &kInitSeq[i], argc);
        i += argc;
    }

    writeCmd(GC9D01_SLPOUT);
    delay(200);
    writeCmd(GC9D01_DISPON);
    delay(20);

    fillScreen(0x0000);
    flush();
    return true;
}

// ----------------------------------------------------------------------------
// Transport
// ----------------------------------------------------------------------------
void GC9D01::startWrite() {
    if (_txDepth++ == 0) {
        _bus->beginTransaction(_cfg);
        digitalWrite(_cs, LOW);
    }
}

void GC9D01::endWrite() {
    if (_txDepth == 0) return;            // unbalanced call; ignore
    if (--_txDepth == 0) {
        digitalWrite(_cs, HIGH);
        _bus->endTransaction();
    }
}

void GC9D01::writeCmd(uint8_t cmd, const uint8_t* args, size_t n) {
    startWrite();
    digitalWrite(_dc, LOW);               // command phase
    _bus->transfer(cmd);
    if (n) {
        digitalWrite(_dc, HIGH);          // parameter phase
        for (size_t i = 0; i < n; i++) _bus->transfer(args[i]);
    }
    endWrite();
}

void GC9D01::setWindow(int16_t x0, int16_t y0, int16_t x1, int16_t y1) {
    uint8_t a[4];
    startWrite();
    a[0] = x0 >> 8; a[1] = x0 & 0xFF; a[2] = x1 >> 8; a[3] = x1 & 0xFF;
    writeCmd(GC9D01_CASET, a, 4);
    a[0] = y0 >> 8; a[1] = y0 & 0xFF; a[2] = y1 >> 8; a[3] = y1 & 0xFF;
    writeCmd(GC9D01_RASET, a, 4);
    writeCmd(GC9D01_RAMWR);
    endWrite();
}

void GC9D01::flush() {
    if (!_fb || !_bus) return;
    startWrite();                         // one chip-select for the whole frame
    setWindow(0, 0, LCD_WIDTH - 1, LCD_HEIGHT - 1);
    digitalWrite(_dc, HIGH);              // pixel data
    // writePixels() byte-swaps each 16-bit word, which is what the panel wants
    // (RGB565, high byte first on the wire).
    _bus->writePixels(_fb, (size_t)LCD_WIDTH * LCD_HEIGHT * sizeof(uint16_t));
    endWrite();
}

// ----------------------------------------------------------------------------
// Framebuffer drawing (no SPI traffic -- call flush() to make it visible)
// ----------------------------------------------------------------------------
void GC9D01::fillScreen(uint16_t color) {
    if (!_fb) return;
    for (size_t i = 0; i < (size_t)LCD_WIDTH * LCD_HEIGHT; i++) _fb[i] = color;
}

void GC9D01::drawPixel(int16_t x, int16_t y, uint16_t color) {
    if (!_fb) return;
    if (x < 0 || x >= LCD_WIDTH || y < 0 || y >= LCD_HEIGHT) return;
    _fb[y * LCD_WIDTH + x] = color;
}

void GC9D01::drawFastVLine(int16_t x, int16_t y, int16_t h, uint16_t color) {
    for (int16_t i = 0; i < h; i++) drawPixel(x, y + i, color);
}

void GC9D01::drawFastHLine(int16_t x, int16_t y, int16_t w, uint16_t color) {
    for (int16_t i = 0; i < w; i++) drawPixel(x + i, y, color);
}

void GC9D01::drawLine(int16_t x0, int16_t y0, int16_t x1, int16_t y1, uint16_t color) {
    int16_t dx = abs(x1 - x0);
    int16_t dy = abs(y1 - y0);
    int16_t sx = x0 < x1 ? 1 : -1;
    int16_t sy = y0 < y1 ? 1 : -1;
    int16_t err = dx - dy;

    while (true) {
        drawPixel(x0, y0, color);
        if (x0 == x1 && y0 == y1) break;
        int16_t e2 = 2 * err;
        if (e2 > -dy) { err -= dy; x0 += sx; }
        if (e2 < dx) { err += dx; y0 += sy; }
    }
}

void GC9D01::drawRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color) {
    drawFastHLine(x, y, w, color);
    drawFastHLine(x, y + h - 1, w, color);
    drawFastVLine(x, y, h, color);
    drawFastVLine(x + w - 1, y, h, color);
}

void GC9D01::fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color) {
    for (int16_t i = 0; i < h; i++) drawFastHLine(x, y + i, w, color);
}

void GC9D01::drawCircle(int16_t cx, int16_t cy, int16_t r, uint16_t color) {
    int16_t x = r, y = 0, err = 0;

    auto plot = [this, cx, cy, color](int16_t dx, int16_t dy) {
        drawPixel(cx + dx, cy + dy, color);
        drawPixel(cx + dy, cy + dx, color);
        drawPixel(cx - dy, cy + dx, color);
        drawPixel(cx - dx, cy + dy, color);
        drawPixel(cx - dx, cy - dy, color);
        drawPixel(cx - dy, cy - dx, color);
        drawPixel(cx + dy, cy - dx, color);
        drawPixel(cx + dx, cy - dy, color);
    };

    plot(x, y);
    while (x > y) {
        y++;
        err += 1 + 2 * y;
        if (err + 2 * (-x - 1) + 1 > 0) { x--; err += 1 + 2 * (-x); }
        plot(x, y);
    }
}

void GC9D01::fillCircle(int16_t cx, int16_t cy, int16_t r, uint16_t color) {
    // Plain scanline fill: for each row, solve the circle for its half-width.
    // The previous implementation walked a single Bresenham octant and only
    // filled the columns between cx +/- r and cx +/- r/sqrt(2), leaving the
    // whole middle band of every circle unpainted -- circles came out as two
    // crescents either side of a one-pixel centre line. At r <= 80 the cost of
    // doing this the obvious way is irrelevant.
    if (r < 0) return;
    for (int16_t dy = -r; dy <= r; dy++) {
        const int32_t w2 = (int32_t)r * r - (int32_t)dy * dy;
        if (w2 < 0) continue;
        const int16_t dx = (int16_t)(sqrtf((float)w2) + 0.5f);
        drawFastHLine(cx - dx, cy + dy, 2 * dx + 1, color);
    }
}
