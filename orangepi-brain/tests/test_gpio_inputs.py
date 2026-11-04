"""Mute switch, power button and the raw GPIO uAPI layout (no hardware)."""
import struct
import unittest
from unittest.mock import Mock, patch

from stubs import install_stub_modules
install_stub_modules()
from brain import gpio  # noqa: E402
from brain.audio import Audio  # noqa: E402
from brain.mute_switch import MuteSwitch  # noqa: E402
from brain.power_button import PowerButton  # noqa: E402


class FakeLine:
    def __init__(self, values):
        self.values = list(values)
        self.opened = self.closed = False

    def open(self):
        self.opened = True

    def read(self):
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]

    def close(self):
        self.closed = True


class TestUapiLayout(unittest.TestCase):
    def test_struct_sizes_and_ioctl_numbers_match_the_kernel_header(self):
        # sizeof() from include/uapi/linux/gpio.h; the ioctl number encodes the size,
        # so a wrong layout is EINVAL on hardware, not a wrong reading.
        self.assertEqual(struct.calcsize(gpio._CHIPINFO_FMT), 68)
        self.assertEqual(struct.calcsize(gpio._LINEINFO_FMT), 256)
        self.assertEqual(struct.calcsize(gpio._REQUEST_FMT), 592)
        self.assertEqual(gpio.GPIO_V2_GET_LINE_IOCTL, 0xC250B407)
        self.assertEqual(gpio.GPIO_V2_LINE_GET_VALUES_IOCTL, 0xC010B40E)

    def test_request_is_one_pulled_up_active_low_input(self):
        fields = struct.unpack(gpio._REQUEST_FMT, bytes(gpio.build_request(33, "owl-mute")))
        self.assertEqual(fields[0], 33)
        self.assertEqual(fields[64].rstrip(b"\0"), b"owl-mute")
        self.assertEqual(fields[65], gpio.FLAG_INPUT | gpio.FLAG_ACTIVE_LOW | gpio.FLAG_BIAS_PULL_UP)
        self.assertEqual(fields[-8], 1)          # num_lines

    def test_port_names_use_the_sunxi_numbering(self):
        # Measured 2026-11-04: PB0/PB1 are offsets 32/33 of gpiochip0 ('2000000.pinctrl').
        self.assertEqual(gpio.sunxi_offset("PB0"), 32)
        self.assertEqual(gpio.sunxi_offset("pb1"), 33)
        with self.assertRaises(ValueError):
            gpio.sunxi_offset("GPIO5")


class TestMuteSwitch(unittest.TestCase):
    def make(self, values, **cfg):
        with patch('brain.audio.shutil.which', return_value='/usr/bin/aplay'), \
                patch.object(Audio, '_load_samples', return_value={}):
            audio = Audio({'audio': {'enabled': True}})
        line = FakeLine(values)
        sw = MuteSwitch(audio, {'mute_switch': dict({'enabled': True}, **cfg)}, line=line)
        return audio, sw

    def test_boot_position_applies_before_the_first_sound(self):
        audio, sw = self.make([True])
        with patch.object(MuteSwitch, '_run'):
            sw.start()
        self.assertTrue(audio.muted)
        with patch('brain.audio.subprocess.Popen') as spawn:
            self.assertFalse(audio.play("beep"))
            spawn.assert_not_called()          # no player at all, not a quiet one
        sw.stop()

    def test_a_single_odd_read_is_ignored(self):
        audio, sw = self.make([True, False, False])
        sw.poll()
        self.assertFalse(audio.muted)
        sw.poll()                              # glitch gone: no change
        self.assertFalse(audio.muted)
        audio2, sw2 = self.make([True, True])
        sw2.poll()
        self.assertFalse(audio2.muted)
        sw2.poll()
        self.assertTrue(audio2.muted)

    def test_open_position_can_be_the_mute_one(self):
        audio, sw = self.make([False], muted_when="open")
        sw.poll()
        sw.poll()
        self.assertTrue(audio.muted)

    def test_missing_pin_leaves_the_owl_audible(self):
        audio, sw = self.make([True])
        sw._line.open = Mock(side_effect=PermissionError("denied"))
        sw.start()
        self.assertFalse(audio.muted)
        self.assertIsNone(sw._thread)


class TestPowerButton(unittest.TestCase):
    def make(self, values, step=0.05):
        t = [0.0]

        def clock():
            t[0] += step
            return t[0]
        run = Mock()
        btn = PowerButton({'power_button': {'enabled': True, 'hold_s': 2.0}},
                          line=FakeLine(values), run=run, clock=clock)
        return btn, run

    def test_hold_for_two_seconds_shuts_down_once(self):
        btn, run = self.make([False] + [True] * 100)
        for _ in range(60):
            btn.poll()
        run.assert_called_once_with(["shutdown", "-h", "now"], check=False)

    def test_short_presses_do_nothing(self):
        btn, run = self.make(([False] + [True] * 20) * 10)   # ten 1 s presses
        for _ in range(210):
            btn.poll()
        run.assert_not_called()

    def test_a_button_stuck_at_boot_never_shuts_down(self):
        btn, run = self.make([True] * 500)
        for _ in range(500):
            btn.poll()
        self.assertFalse(btn.armed)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
