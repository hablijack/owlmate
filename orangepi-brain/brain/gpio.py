"""
Robot Owl OrangePi Brain - GPIO inputs (mute switch, power button)

Reads a header pin through the Linux GPIO character device (/dev/gpiochipN,
uAPI v2) with plain ioctls -- no Python package, the same approach as the OLED
driver in display.py. Both switches on the owl short their pin to GND, so a line
is requested as an input with the internal PULL-UP and ACTIVE-LOW: read() is
True while the contact is closed (switch on / button held).

A pin is named by its A733 port name ("PB0") or by a raw line offset. A name is
looked up in the chip's line names first; if the kernel does not name its lines,
it falls back to the sunxi numbering, bank * 32 + pin (PB0 = 32).

Bench check, as root on the Orange Pi (prints chips, then the live values):

    sudo .venv/bin/python -m brain.gpio PB0 PB1
"""

import fcntl
import logging
import os
import re
import struct
import sys
import time
from typing import Callable, Optional, Union

logger = logging.getLogger(__name__)

# --- uAPI v2 (include/uapi/linux/gpio.h) -----------------------------------
_LINES_MAX = 64
_NUM_ATTRS_MAX = 10
_CHIPINFO_FMT = "32s32sI"                              # struct gpiochip_info, 68 bytes
_LINEINFO_FMT = "32s32sIIQ" + "IIQ" * _NUM_ATTRS_MAX + "4I"   # struct gpio_v2_line_info, 256
_REQUEST_FMT = ("%dI32s" % _LINES_MAX                  # offsets[64], consumer[32]
                + "QI5I" + "IIQQ" * _NUM_ATTRS_MAX     # gpio_v2_line_config
                + "II5Ii")                             # num_lines, event_buffer_size, padding, fd
_VALUES_FMT = "QQ"                                     # struct gpio_v2_line_values (bits, mask)


def _iowr(nr: int, size: int) -> int:
    return (3 << 30) | (size << 16) | (0xB4 << 8) | nr


def _ior(nr: int, size: int) -> int:
    return (2 << 30) | (size << 16) | (0xB4 << 8) | nr


GPIO_GET_CHIPINFO_IOCTL = _ior(0x01, struct.calcsize(_CHIPINFO_FMT))
GPIO_V2_GET_LINEINFO_IOCTL = _iowr(0x05, struct.calcsize(_LINEINFO_FMT))
GPIO_V2_GET_LINE_IOCTL = _iowr(0x07, struct.calcsize(_REQUEST_FMT))
GPIO_V2_LINE_GET_VALUES_IOCTL = _iowr(0x0E, struct.calcsize(_VALUES_FMT))

FLAG_USED = 1 << 0
FLAG_ACTIVE_LOW = 1 << 1
FLAG_INPUT = 1 << 2
FLAG_BIAS_PULL_UP = 1 << 8

DEFAULT_CHIP = "2000000.pinctrl"     # the A733's main pin controller (ports A..K)

_PORT_NAME = re.compile(r"^P([A-Z])(\d{1,2})$")


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", "replace")


def sunxi_offset(name: str) -> int:
    """'PB0' -> 32: the sunxi pinctrl numbers 32 lines per bank, bank A first."""
    m = _PORT_NAME.match(name.upper())
    if not m:
        raise ValueError("not an A733 port name: %r" % name)
    return (ord(m.group(1)) - ord("A")) * 32 + int(m.group(2))


def chip_info(fd: int):
    """(name, label, number of lines) of an open gpiochip."""
    buf = bytearray(struct.calcsize(_CHIPINFO_FMT))
    fcntl.ioctl(fd, GPIO_GET_CHIPINFO_IOCTL, buf)
    name, label, lines = struct.unpack(_CHIPINFO_FMT, bytes(buf))
    return _cstr(name), _cstr(label), lines


def line_info(fd: int, offset: int):
    """(name, consumer, flags) of one line."""
    buf = bytearray(struct.calcsize(_LINEINFO_FMT))
    struct.pack_into("I", buf, 64, offset)
    fcntl.ioctl(fd, GPIO_V2_GET_LINEINFO_IOCTL, buf)
    fields = struct.unpack(_LINEINFO_FMT, bytes(buf))
    return _cstr(fields[0]), _cstr(fields[1]), fields[4]


def resolve_chip(chip: str, opener: Callable[[str], int] = None) -> str:
    """Device path for `chip`: a /dev path as given, otherwise a chip LABEL.

    The label is the robust name. On the Zero 3W, gpiochip0 is the main
    controller ('2000000.pinctrl') and gpiochip1 the R_PIO ('7025000.pinctrl'),
    which carries the WLAN pins at the very offsets PB0/PB1 have on gpiochip0.
    """
    if chip.startswith("/"):
        return chip
    opener = opener or (lambda path: os.open(path, os.O_RDONLY))
    for name in sorted(p for p in os.listdir("/dev") if p.startswith("gpiochip")):
        fd = opener("/dev/" + name)
        try:
            if chip_info(fd)[1] == chip:
                return "/dev/" + name
        finally:
            os.close(fd)
    raise FileNotFoundError("no gpiochip labelled %r" % chip)


def resolve_offset(fd: int, line: Union[int, str]) -> int:
    """Line offset for `line` (an int, a digit string, or a port name like 'PB0')."""
    if isinstance(line, int) or str(line).isdigit():
        return int(line)
    _, _, count = chip_info(fd)
    for offset in range(count):
        if line_info(fd, offset)[0].upper() == str(line).upper():
            return offset
    return sunxi_offset(str(line))


def build_request(offset: int, consumer: str) -> bytearray:
    """A gpio_v2_line_request for one pulled-up, active-low input line."""
    values = [0] * _LINES_MAX
    values[0] = offset
    config = [FLAG_INPUT | FLAG_ACTIVE_LOW | FLAG_BIAS_PULL_UP, 0] + [0] * 5 + [0] * (4 * _NUM_ATTRS_MAX)
    tail = [1, 0] + [0] * 5 + [0]
    return bytearray(struct.pack(_REQUEST_FMT, *values, consumer.encode()[:31], *config, *tail))


class GpioInput:
    """One input line, pulled up, active low: read() is True while it is shorted to GND."""

    def __init__(self, chip: str, line: Union[int, str], consumer: str = "robot-owl",
                 opener: Callable[[str], int] = None):
        self.chip = chip
        self.line = line
        self.consumer = consumer
        self.offset: Optional[int] = None
        self._opener = opener or (lambda path: os.open(path, os.O_RDONLY))
        self._fd: Optional[int] = None

    def open(self) -> None:
        chip_fd = self._opener(resolve_chip(self.chip, self._opener))
        try:
            self.offset = resolve_offset(chip_fd, self.line)
            req = build_request(self.offset, self.consumer)
            fcntl.ioctl(chip_fd, GPIO_V2_GET_LINE_IOCTL, req)
            self._fd = struct.unpack(_REQUEST_FMT, bytes(req))[-1]
        finally:
            os.close(chip_fd)

    def read(self) -> bool:
        buf = bytearray(struct.pack(_VALUES_FMT, 0, 1))
        fcntl.ioctl(self._fd, GPIO_V2_LINE_GET_VALUES_IOCTL, buf)
        return bool(struct.unpack(_VALUES_FMT, bytes(buf))[0] & 1)

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None


def _bench(names) -> None:
    for path in sorted(p for p in os.listdir("/dev") if p.startswith("gpiochip")):
        fd = os.open("/dev/" + path, os.O_RDONLY)
        try:
            name, label, count = chip_info(fd)
            named = sum(1 for o in range(count) if line_info(fd, o)[0])
            print("/dev/%s: %s label=%r lines=%d (%d named)" % (path, name, label, count, named))
            for n in names:
                try:
                    off = resolve_offset(fd, n)
                    if off < count:
                        lname, consumer, flags = line_info(fd, off)
                        print("   %s -> offset %d, name=%r, used=%s %s"
                              % (n, off, lname, bool(flags & FLAG_USED), consumer))
                except ValueError:
                    pass
        finally:
            os.close(fd)
    chip = os.environ.get("OWL_GPIOCHIP", DEFAULT_CHIP)
    lines = [GpioInput(chip, n, "owl-bench") for n in names]
    for g in lines:
        g.open()
    print("Live (%s), 1 = Kontakt geschlossen. Strg-C beendet." % chip)
    last = None
    try:
        while True:
            now = tuple(g.read() for g in lines)
            if now != last:
                print("  " + "  ".join("%s=%d" % (n, v) for n, v in zip(names, now)), flush=True)
                last = now
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        for g in lines:
            g.close()


if __name__ == "__main__":
    _bench(sys.argv[1:] or ["PB0", "PB1"])
