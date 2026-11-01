"""Status OLED: rendering is pure, the device is a fake, and a missing panel must not hurt."""
import unittest
from unittest.mock import MagicMock
from stubs import install_stub_modules
install_stub_modules()

from brain import display  # noqa: E402
from brain.serial_handler import Telemetry, FaceDetection  # noqa: E402


def pixels(frame):
    """Page buffer -> rows of '#'/'.' (inverse of render's packing)."""
    rows = []
    for y in range(display.HEIGHT):
        page, bit = divmod(y, 8)
        rows.append(''.join('#' if frame[page * display.WIDTH + x] & (1 << bit) else '.'
                            for x in range(display.WIDTH)))
    return rows


class FakeDevice:
    def __init__(self, fail_open=False):
        self.frames, self.contrasts, self.opened, self.closed = [], [], 0, 0
        self.fail_open, self.fail_show = fail_open, False

    def open(self):
        self.opened += 1
        if self.fail_open:
            raise OSError('no such bus')

    def init(self): pass
    def close(self): self.closed += 1

    def show(self, frame):
        if self.fail_show:
            raise OSError('NACK')
        self.frames.append(frame)

    def contrast(self, v): self.contrasts.append(v)


def sup(last=None, state=None):
    s = MagicMock()
    s.last, s.last_state = last, state
    return s


def frame_telemetry(state='idle', face=False, hz=28.4):
    return Telemetry(state=state, loop_hz=hz, face=FaceDetection(detected=face))


class TestRender(unittest.TestCase):
    def test_buffer_size_and_every_state_fits(self):
        for label in list(display.STATE_LABELS.values()) + [display.LABEL_NO_LINK, display.LABEL_BRAIN_OFF]:
            self.assertEqual(len(display.render(label, 'X')), 512)
            self.assertLessEqual(display.text_width(label, 2), display.WIDTH, label)

    def test_glyph_I_lands_where_the_font_says(self):
        # 'I' at 2x, centred: width 2*5=10 px, so it starts at (128-10)//2 = 59; its middle
        # column (font column 2 = 0x7F) is drawn at x=59+4..5 on rows 1..14.
        rows = pixels(display.render('I'))
        self.assertEqual(rows[1][63], '#')
        self.assertEqual(rows[14][63], '#')
        self.assertEqual(rows[0][63], '.')          # one row of margin above
        self.assertEqual(rows[15][63], '.')
        self.assertEqual(rows[8][59], '.')           # left of the serif, outside the glyph's column 0 (0x00)

    def test_small_line_is_below_the_big_one(self):
        rows = pixels(display.render('IDLE', '28 HZ'))
        self.assertTrue(any('#' in r for r in rows[22:29]))
        self.assertFalse(any('#' in r for r in rows[16:22]))

    def test_unknown_character_does_not_raise(self):
        display.render('ÄÖ~', 'ü')

    def test_too_long_text_is_cut_not_overflowed(self):
        self.assertEqual(len(display.render('A' * 40)), 512)

    def test_all_state_names_have_distinct_pictures(self):
        frames = {display.render(l) for l in display.STATE_LABELS.values()}
        self.assertEqual(len(frames), len(display.STATE_LABELS))


class TestPortrait(unittest.TestCase):
    """Long side vertical: upright letters, one under the other, first letter on top."""

    @staticmethod
    def canvas(frame):
        """Undo the quarter turn: canvas[row][col] on the 32x128 portrait canvas."""
        return [[bool(frame[((31 - u) // 8) * 128 + v] >> ((31 - u) % 8) & 1) for u in range(32)]
                for v in range(128)]

    def test_size(self):
        self.assertEqual(len(display.render_portrait("IDLE", "15 HZ")), 512)

    def test_short_mode_is_big_and_centred(self):
        c = self.canvas(display.render_portrait("IDLE", "28 HZ  FACE"))
        cols = [u for row in c[:100] for u in range(32) if row[u]]
        self.assertGreaterEqual(max(cols) - min(cols) + 1, 14)        # 3x glyphs are 15+ px wide
        self.assertAlmostEqual((min(cols) + max(cols)) / 2, 15.5, delta=1.5)
        self.assertTrue(any(any(row) for row in c[:20]))              # first letter at the top

    def test_long_names_are_shortened_and_still_fit(self):
        for name in ("DETECTING", "INTERACTING", "NAVIGATING", "SLEEPING", "NO LINK"):
            c = self.canvas(display.render_portrait(name, "28 HZ  NO FACE"))
            self.assertTrue(any(any(row) for row in c[:100]), name)
            # mode area ends above the rule at row 103, metrics below it
            self.assertFalse(any(any(row) for row in c[100:103]), name)

    def test_metrics_sit_under_the_mode(self):
        c = self.canvas(display.render_portrait("IDLE", "28 HZ  FACE"))
        self.assertTrue(any(c[103][4:28]))                            # the rule
        self.assertTrue(any(any(row) for row in c[107:114]))          # first metric line
        self.assertTrue(any(any(row) for row in c[118:125]))          # second metric line

    def test_config_selects_the_layout(self):
        dev = FakeDevice()
        sd = display.StatusDisplay(MagicMock(last=None, last_state=None),
                                   {"display": {"enabled": True, "portrait": True}}, device=dev)
        sd.update(now=1.0)
        self.assertEqual(dev.frames[-1], display.render_portrait("STARTING", "WAITING FOR OWL"))


class TestStatus(unittest.TestCase):
    def make(self, supervisor, device, **cfg):
        c = {'display': dict({'enabled': True}, **cfg)}
        return display.StatusDisplay(supervisor, c, device=device)

    def test_labels_follow_the_firmware_state(self):
        d = self.make(sup(frame_telemetry('detecting', face=True), 'detecting'), FakeDevice())
        big, small, sleeping = d.lines()
        self.assertEqual(big, 'DETECTING')
        self.assertIn('28 HZ', small)
        self.assertIn('FACE', small)
        self.assertFalse(sleeping)
        self.assertEqual(self.make(sup(frame_telemetry('boot'), 'boot'), FakeDevice()).lines()[0], 'STARTING')
        self.assertTrue(self.make(sup(frame_telemetry('sleeping'), 'sleeping'), FakeDevice()).lines()[2])

    def test_before_first_frame_it_says_starting_and_after_a_stale_link_no_link(self):
        self.assertEqual(self.make(sup(), FakeDevice()).lines()[0], 'STARTING')
        d = self.make(sup(None, 'interacting'), FakeDevice())
        self.assertEqual(d.lines()[0], 'NO LINK')
        self.assertIn('INTERACTING', d.lines()[1])

    def test_only_changed_pictures_are_sent(self):
        dev, s = FakeDevice(), sup(frame_telemetry('idle'), 'idle')
        d = self.make(s, dev)
        d.update(now=1.0)
        d.update(now=2.0)
        self.assertEqual(len(dev.frames), 1)
        s.last = frame_telemetry('detecting')
        d.update(now=3.0)
        self.assertEqual(len(dev.frames), 2)

    def test_sleeping_dims_and_waking_restores(self):
        dev, s = FakeDevice(), sup(frame_telemetry('sleeping'), 'sleeping')
        d = self.make(s, dev)
        d.update(now=1.0)
        s.last = frame_telemetry('idle')
        d.update(now=2.0)
        self.assertEqual(dev.contrasts, [0x01, 0x8F])

    def test_missing_panel_is_survivable_and_retried_later(self):
        dev = FakeDevice(fail_open=True)
        d = self.make(sup(frame_telemetry()), dev, retry_s=30)
        d.update(now=1.0)
        d.update(now=2.0)             # inside retry window: no new attempt
        self.assertEqual(dev.opened, 1)
        d.update(now=40.0)            # after it
        self.assertEqual(dev.opened, 2)
        self.assertEqual(dev.frames, [])

    def test_write_error_closes_and_recovers(self):
        dev = FakeDevice()
        d = self.make(sup(frame_telemetry()), dev, retry_s=10)
        d.update(now=1.0)
        dev.fail_show = True
        d._supervisor.last = frame_telemetry('detecting')
        d.update(now=2.0)             # fails, must not raise
        self.assertGreaterEqual(dev.closed, 1)
        dev.fail_show = False
        d.update(now=20.0)            # reopened, picture resent
        self.assertEqual(dev.opened, 2)
        self.assertEqual(len(dev.frames), 2)

    def test_disabled_never_touches_the_bus(self):
        dev = FakeDevice()
        d = display.StatusDisplay(sup(frame_telemetry()), {'display': {'enabled': False}}, device=dev)
        d.update(now=1.0)
        d.start()
        d.stop()
        self.assertEqual((dev.opened, dev.frames), (0, []))

    def test_stop_leaves_brain_off_on_the_panel(self):
        dev = FakeDevice()
        d = self.make(sup(frame_telemetry()), dev)
        d.update(now=1.0)
        d.stop()
        self.assertEqual(dev.frames[-1], display.render(display.LABEL_BRAIN_OFF, ''))


class TestDriverBytes(unittest.TestCase):
    def test_show_sends_address_window_then_16_chunks_of_32_data_bytes(self):
        sent = []
        dev = display.SSD1306(bus=1, address=0x3C)
        dev._fd = 99
        import brain.display as mod
        real = mod.os.write
        mod.os.write = lambda fd, data: sent.append(bytes(data)) or len(data)
        try:
            dev.show(bytes(512))
        finally:
            mod.os.write = real
        self.assertEqual(sent[0], bytes([0x00, 0x21, 0, 127]))
        self.assertEqual(sent[1], bytes([0x00, 0x22, 0, 3]))
        data = sent[2:]
        self.assertEqual(len(data), 16)
        self.assertTrue(all(c[0] == 0x40 and len(c) == 33 for c in data))

    def test_wrong_frame_size_is_rejected(self):
        dev = display.SSD1306(bus=1, address=0x3C)
        with self.assertRaises(ValueError):
            dev.show(b'x')


if __name__ == '__main__':
    unittest.main()
