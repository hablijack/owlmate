"""Compile and execute the real C++ servo driver with observable I2C/PWM doubles."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from stubs import install_stub_modules
install_stub_modules()
from brain.serial_handler import SerialHandler  # noqa: E402


class TestServoSafety(unittest.TestCase):
    def test_locked_driver_has_no_hardware_writes(self):
        compiler = shutil.which('c++')
        if compiler is None:
            self.skipTest('native C++ compiler required for firmware safety test')
        firmware = Path(__file__).resolve().parents[2] / 'esp32-s3-sense'
        with tempfile.TemporaryDirectory(prefix='owl-servo-safety-') as directory:
            output = str(Path(directory) / 'test')
            subprocess.run([compiler, '-std=c++11', '-I'+str(firmware/'tests/servo_safety'),
                            '-I'+str(firmware/'include'), str(firmware/'src/ServoController.cpp'),
                            str(firmware/'tests/servo_safety/test.cpp'), '-o', output], check=True)
            subprocess.run([output], check=True)

    def test_owner_port_mapping_matches_firmware_web_and_wiring(self):
        from brain import web_ui
        root = Path(__file__).resolve().parents[2]
        header = (root/'esp32-s3-sense/include/config.h').read_text()
        wiring = (root/'WIRING.md').read_text()
        expected = {'CH_RIGHT_WING': (8, 'Right wing'),
                    'CH_LEFT_WING': (9, 'Left wing'), 'CH_HEAD': (10, 'Head'),
                    'CH_LEFT_EAR': (12, 'Left ear'), 'CH_RIGHT_EAR': (13, 'Right ear')}
        for name, (channel, label) in expected.items():
            with self.subTest(name=name):
                match = re.search(r'^#define\s+'+name+r'\s+(\d+)', header, re.M)
                self.assertIsNotNone(match)
                self.assertEqual(int(match.group(1)), channel)
                self.assertEqual(getattr(web_ui, name), channel)
                self.assertRegex(wiring, r'\|\s*CH'+str(channel)+r'\s*\|\s*'+label+r'\s*\|')
        self.assertRegex(header, r'#define SERVO_SAFETY_LOCKED 1')

    def test_lock_telemetry_and_fail_closed_default(self):
        handler = SerialHandler('/dev/null')
        old = handler.parse_telemetry('{"type":"telemetry","state":"idle"}')
        self.assertTrue(old.servo_safety.locked)
        current = handler.parse_telemetry(
            '{"type":"telemetry","servo_safety":{"locked":true,"ready":true}}')
        self.assertTrue(current.servo_safety.locked)
        self.assertTrue(current.servo_safety.ready)
        self.assertFalse(old.servo_safety.ready)
