"""
Robot Owl OrangePi Brain - Status display

Shows the owl's current mode ("IDLE", "DETECTING", ...) on a tiny 0.91" 128x32
SSD1306 OLED that hangs on one of the OrangePi's own I2C buses (not on the
XIAO's bus: this is a Linux-side peripheral, see specs/012).

No extra Python package: the panel is driven through /dev/i2c-N with the
I2C_SLAVE ioctl, and the text comes from a built-in 5x7 font (upper case,
digits and a few symbols -- enough for the mode names). Like Audio, the module
degrades instead of failing: if the bus or the panel is missing it logs once and
every call becomes a no-op, so the brain still starts without a display.

Rendering is a pure function (render()) so it is testable without hardware; the
device only receives a frame when the picture actually changed.
"""

import fcntl
import logging
import os
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

WIDTH = 128
HEIGHT = 32
PAGES = HEIGHT // 8
I2C_SLAVE = 0x0703

# 5x7 font, one byte per column, bit 0 = top row. Upper case only: lower case is
# folded to upper case, anything unknown draws as '?'.
FONT = {
    " ": (0x00, 0x00, 0x00, 0x00, 0x00),
    "0": (0x3E, 0x51, 0x49, 0x45, 0x3E), "1": (0x00, 0x42, 0x7F, 0x40, 0x00),
    "2": (0x42, 0x61, 0x51, 0x49, 0x46), "3": (0x21, 0x41, 0x45, 0x4B, 0x31),
    "4": (0x18, 0x14, 0x12, 0x7F, 0x10), "5": (0x27, 0x45, 0x45, 0x45, 0x39),
    "6": (0x3C, 0x4A, 0x49, 0x49, 0x30), "7": (0x01, 0x71, 0x09, 0x05, 0x03),
    "8": (0x36, 0x49, 0x49, 0x49, 0x36), "9": (0x06, 0x49, 0x49, 0x29, 0x1E),
    "A": (0x7E, 0x11, 0x11, 0x11, 0x7E), "B": (0x7F, 0x49, 0x49, 0x49, 0x36),
    "C": (0x3E, 0x41, 0x41, 0x41, 0x22), "D": (0x7F, 0x41, 0x41, 0x22, 0x1C),
    "E": (0x7F, 0x49, 0x49, 0x49, 0x41), "F": (0x7F, 0x09, 0x09, 0x09, 0x01),
    "G": (0x3E, 0x41, 0x49, 0x49, 0x7A), "H": (0x7F, 0x08, 0x08, 0x08, 0x7F),
    "I": (0x00, 0x41, 0x7F, 0x41, 0x00), "J": (0x20, 0x40, 0x41, 0x3F, 0x01),
    "K": (0x7F, 0x08, 0x14, 0x22, 0x41), "L": (0x7F, 0x40, 0x40, 0x40, 0x40),
    "M": (0x7F, 0x02, 0x0C, 0x02, 0x7F), "N": (0x7F, 0x04, 0x08, 0x10, 0x7F),
    "O": (0x3E, 0x41, 0x41, 0x41, 0x3E), "P": (0x7F, 0x09, 0x09, 0x09, 0x06),
    "Q": (0x3E, 0x41, 0x51, 0x21, 0x5E), "R": (0x7F, 0x09, 0x19, 0x29, 0x46),
    "S": (0x46, 0x49, 0x49, 0x49, 0x31), "T": (0x01, 0x01, 0x7F, 0x01, 0x01),
    "U": (0x3F, 0x40, 0x40, 0x40, 0x3F), "V": (0x1F, 0x20, 0x40, 0x20, 0x1F),
    "W": (0x3F, 0x40, 0x38, 0x40, 0x3F), "X": (0x63, 0x14, 0x08, 0x14, 0x63),
    "Y": (0x07, 0x08, 0x70, 0x08, 0x07), "Z": (0x61, 0x51, 0x49, 0x45, 0x43),
    ".": (0x00, 0x60, 0x60, 0x00, 0x00), ":": (0x00, 0x36, 0x36, 0x00, 0x00),
    "-": (0x08, 0x08, 0x08, 0x08, 0x08), "%": (0x23, 0x13, 0x08, 0x64, 0x62),
    "/": (0x20, 0x10, 0x08, 0x04, 0x02), "?": (0x02, 0x01, 0x51, 0x09, 0x06),
}

# What the panel says for each firmware state. "boot" is the firmware's own start-up,
# the owner calls it "starting"; STARTING / NO LINK are the brain's own states
# (no frame yet / telemetry went stale), not something the firmware reports.
STATE_LABELS = {
    "boot": "STARTING",
    "idle": "IDLE",
    "detecting": "DETECTING",
    "interacting": "INTERACTING",
    "sleeping": "SLEEPING",
    "navigating": "NAVIGATING",
    "update": "UPDATE",
    "error": "ERROR",
}
LABEL_STARTING = "STARTING"
LABEL_NO_LINK = "NO LINK"
LABEL_BRAIN_OFF = "BRAIN OFF"


def text_width(text: str, scale: int) -> int:
    """Pixel width of `text` at `scale` (5 columns per glyph plus a 1 px gap, whatever the scale)."""
    return len(text) * (5 * scale + 1) - 1 if text else 0


def _draw_text(pixels, text: str, y: int, scale: int) -> None:
    """Draw `text` centred horizontally with its top row at `y` into pixels[row][col]."""
    x = max(0, (WIDTH - text_width(text, scale)) // 2)
    for ch in text.upper():
        glyph = FONT.get(ch, FONT["?"])
        for col, bits in enumerate(glyph):
            for row in range(7):
                if not bits & (1 << row):
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        px, py = x + col * scale + dx, y + row * scale + dy
                        if 0 <= px < WIDTH and 0 <= py < HEIGHT:
                            pixels[py][px] = True
        x += 5 * scale + 1


def render(big: str, small: str = "") -> bytes:
    """Return the 512-byte SSD1306 page buffer for a big line and a small line.

    The big line is drawn at 2x (it must fit: INTERACTING is 11 glyphs = 120 px at
    an 11 px advance; longer text is cut to fit). The small
    line sits underneath at 1x. Bit 0 of each byte is the top pixel of its 8-row page.
    """
    pixels = [[False] * WIDTH for _ in range(HEIGHT)]
    scale = 2
    while len(big) > 1 and text_width(big, scale) > WIDTH:
        big = big[:-1]
    _draw_text(pixels, big, 1, scale)
    small = small[: (WIDTH + 1) // 6]
    _draw_text(pixels, small, 22, 1)
    return _pack(pixels)


# Short forms for the portrait layout, where the mode gets one letter per row
# (at most 4 letters at 3x, 7 at 2x). Anything not listed is used unchanged.
PORTRAIT_LABELS = {
    "STARTING": "START",
    "DETECTING": "DETECT",
    "INTERACTING": "CHAT",
    "NAVIGATING": "NAVI",
    "SLEEPING": "SLEEP",
    "NO LINK": "NOLINK",
    "BRAIN OFF": "OFF",
}
PORTRAIT_METRICS = {"NO FACE": "NONE", "WAITING FOR OWL": "WAIT"}
PORTRAIT_METRIC_ROWS = 2
PORTRAIT_METRIC_TOP = 107          # 2 rows of 7 px, 4 px apart, end at row 125


def render_portrait(big: str, small: str = "") -> bytes:
    """Page buffer for a panel mounted with its long side vertical (upright, 32 px wide).

    The canvas is 32 wide and 128 tall. The mode is written downwards, one upright
    letter per row, bold and centred: 3x for up to 4 letters, 2x up to 7, else 1x
    (long names are shortened by PORTRAIT_LABELS). Underneath, below a thin rule, the
    metrics follow as two small horizontal lines. The canvas is turned onto the panel by
    a quarter turn: panel x = canvas row, panel y = 31 - canvas column (the other
    mounting is `rotate`, which flips 180 degrees).
    """
    cw, ch = HEIGHT, WIDTH
    canvas = [[False] * cw for _ in range(ch)]

    def glyph(c: str, x: int, y: int, scale: int, bold: bool) -> None:
        for col, bits in enumerate(FONT.get(c, FONT["?"])):
            for row in range(7):
                if not bits & (1 << row):
                    continue
                for dy in range(scale):
                    for dx in range(scale + (1 if bold else 0)):
                        px, py = x + col * scale + dx, y + row * scale + dy
                        if 0 <= px < cw and 0 <= py < ch:
                            canvas[py][px] = True

    big = PORTRAIT_LABELS.get(big, big).upper()
    scale = 3 if len(big) <= 4 else 2 if len(big) <= 7 else 1
    pitch = 7 * scale + (1 if scale > 1 else 1)
    avail = PORTRAIT_METRIC_TOP - 4
    big = big[: max(1, (avail + 1) // pitch)]
    height = len(big) * pitch - 1
    gw = 5 * scale + 1
    top = max(0, (avail - height) // 2)
    for i, c in enumerate(big):
        if c != " ":
            glyph(c, (cw - gw) // 2, top + i * pitch, scale, True)

    for u in range(4, cw - 4):
        canvas[PORTRAIT_METRIC_TOP - 4][u] = True
    items = []
    for part in small.upper().split("  "):
        part = PORTRAIT_METRICS.get(part.strip(), part.strip())
        items.extend(w[:5] for w in part.split())
    # "15 HZ" arrives as two words: glue a number to its unit again
    merged = []
    for w in items:
        if merged and merged[-1].isdigit() and w.isalpha():
            merged[-1] += w
        else:
            merged.append(w)
    for n, text in enumerate(merged[:PORTRAIT_METRIC_ROWS]):
        x = (cw - text_width(text, 1)) // 2
        y = PORTRAIT_METRIC_TOP + n * 11
        for i, c in enumerate(text):
            glyph(c, x + i * 6, y, 1, False)

    pixels = [[False] * WIDTH for _ in range(HEIGHT)]
    for v in range(ch):
        for u in range(cw):
            if canvas[v][u]:
                pixels[HEIGHT - 1 - u][v] = True
    return _pack(pixels)


def _pack(pixels) -> bytes:
    out = bytearray(WIDTH * PAGES)
    for page in range(PAGES):
        for col in range(WIDTH):
            value = 0
            for bit in range(8):
                if pixels[page * 8 + bit][col]:
                    value |= 1 << bit
            out[page * WIDTH + col] = value
    return bytes(out)


class SSD1306:
    """Minimal 128x32 SSD1306 driver over /dev/i2c-N (no smbus dependency)."""

    def __init__(self, bus: int, address: int, rotate: bool = False,
                 opener: Callable[[str], int] = None):
        self.bus = bus
        self.address = address
        self.rotate = rotate
        self._opener = opener or (lambda path: os.open(path, os.O_RDWR))
        self._fd: Optional[int] = None

    def open(self) -> None:
        fd = self._opener("/dev/i2c-%d" % self.bus)
        try:
            fcntl.ioctl(fd, I2C_SLAVE, self.address)
        except Exception:
            os.close(fd)
            raise
        self._fd = fd

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def _write(self, control: int, payload: bytes) -> None:
        os.write(self._fd, bytes([control]) + payload)

    def command(self, *cmds: int) -> None:
        self._write(0x00, bytes(cmds))

    def init(self) -> None:
        # Standard 128x32 sequence: mux ratio 32, COM pins alternative (0x02),
        # internal charge pump, horizontal addressing. 0xA1/0xC8 vs 0xA0/0xC0 is the
        # mounting orientation (config display.rotate).
        flip = (0xA0, 0xC0) if self.rotate else (0xA1, 0xC8)
        self.command(0xAE)
        self.command(0xD5, 0x80)
        self.command(0xA8, HEIGHT - 1)
        self.command(0xD3, 0x00)
        self.command(0x40)
        self.command(0x8D, 0x14)
        self.command(0x20, 0x00)
        self.command(flip[0])
        self.command(flip[1])
        self.command(0xDA, 0x02)
        self.command(0x81, 0x8F)
        self.command(0xD9, 0xF1)
        self.command(0xDB, 0x40)
        self.command(0xA4)
        self.command(0xA6)
        self.command(0xAF)

    def contrast(self, value: int) -> None:
        self.command(0x81, value & 0xFF)

    def show(self, frame: bytes) -> None:
        if len(frame) != WIDTH * PAGES:
            raise ValueError("frame must be %d bytes" % (WIDTH * PAGES))
        self.command(0x21, 0, WIDTH - 1)
        self.command(0x22, 0, PAGES - 1)
        for i in range(0, len(frame), 32):   # small transfers: one long write is not worth the risk
            self._write(0x40, frame[i:i + 32])


class StatusDisplay:
    """Shows supervisor state on the OLED from a daemon thread.

    Disabled by default (display.enabled: false), like every feature block. When the
    panel cannot be opened it logs once and does nothing; a later write error closes
    the device and retries every `retry_s`, so unplugging the panel never takes the
    brain down.
    """

    def __init__(self, supervisor, config: dict, device: SSD1306 = None):
        cfg = (config or {}).get("display", {}) or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.interval_s = float(cfg.get("interval_s", 0.5))
        self.retry_s = float(cfg.get("retry_s", 30.0))
        self.dim_when_sleeping = bool(cfg.get("dim_when_sleeping", True))
        self._render = render_portrait if cfg.get("portrait", False) else render
        self._supervisor = supervisor
        self._device = device or SSD1306(
            bus=int(cfg.get("i2c_bus", 1)),
            address=int(cfg.get("address", 0x3C)),
            rotate=bool(cfg.get("rotate", False)),
        )
        self._ready = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_frame: Optional[bytes] = None
        self._last_contrast: Optional[int] = None
        self._next_retry = 0.0
        self._warned = False

    # ------------------------------------------------------------------
    def lines(self, now: float = None):
        """(big, small, sleeping) for the current supervisor state."""
        sup = self._supervisor
        last = getattr(sup, "last", None)
        state = getattr(sup, "last_state", None)
        if last is None and not state:
            return LABEL_STARTING, "WAITING FOR OWL", False
        if last is None:
            # Frames stopped coming (check_stale clears `last`).
            return LABEL_NO_LINK, "LAST " + STATE_LABELS.get(state, str(state).upper()), False
        state = last.state
        big = STATE_LABELS.get(state, str(state).upper())
        face = "FACE" if getattr(last.face, "detected", False) else "NO FACE"
        hz = getattr(last, "loop_hz", 0) or 0
        return big, "%d HZ  %s" % (round(hz), face), state == "sleeping"

    def _try_open(self, now: float) -> bool:
        if self._ready:
            return True
        if now < self._next_retry:
            return False
        try:
            self._device.open()
            self._device.init()
            self._ready = True
            self._last_frame = None
            self._last_contrast = None
            self._warned = False
            logger.info("Status display ready")
        except Exception as e:
            self._device.close()
            self._next_retry = now + self.retry_s
            if not self._warned:
                logger.warning("Status display not available (%s); will retry every %.0fs", e, self.retry_s)
                self._warned = True
        return self._ready

    def update(self, now: float = None) -> None:
        """Draw once if the picture changed. Never raises."""
        now = time.time() if now is None else now
        if not self.enabled or not self._try_open(now):
            return
        try:
            big, small, sleeping = self.lines(now)
            frame = self._render(big, small)
            if frame != self._last_frame:
                self._device.show(frame)
                self._last_frame = frame
            contrast = 0x01 if (sleeping and self.dim_when_sleeping) else 0x8F
            if contrast != self._last_contrast:
                self._device.contrast(contrast)
                self._last_contrast = contrast
        except Exception as e:
            logger.warning("Status display write failed (%s); closing, will retry", e)
            self._device.close()
            self._ready = False
            self._next_retry = now + self.retry_s

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="status-display", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.update()
            self._stop.wait(self.interval_s)

    def stop(self) -> None:
        """Stop the thread and leave "BRAIN OFF" on the panel (the owl keeps running without us)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self.enabled and self._ready:
            try:
                self._device.show(self._render(LABEL_BRAIN_OFF, ""))
                self._device.contrast(0x8F)
            except Exception:
                pass
        self._device.close()
        self._ready = False
