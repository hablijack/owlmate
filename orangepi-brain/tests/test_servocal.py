"""Compile and run the real lib/ServoCal against a recording fake PCA9685."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class TestServoCal(unittest.TestCase):
    def test_calibration_tool_rules_hold_against_fake_pca9685(self):
        compiler = shutil.which('c++')
        if compiler is None:
            self.skipTest('native C++ compiler required for firmware safety test')
        firmware = Path(__file__).resolve().parents[2] / 'esp32-s3-sense'
        with tempfile.TemporaryDirectory(prefix='owl-servocal-') as directory:
            output = str(Path(directory) / 'test')
            subprocess.run([compiler, '-std=c++11', '-I' + str(firmware / 'lib/ServoCal'),
                            str(firmware / 'lib/ServoCal/ServoCal.cpp'),
                            str(firmware / 'tests/servocal/test.cpp'), '-o', output], check=True)
            result = subprocess.run([output], check=True, capture_output=True, text=True)
            self.assertIn('all checks passed', result.stdout)

    def test_servocal_env_is_isolated_from_the_locked_image(self):
        """The normal image stays locked and never links the calibration tool."""
        root = Path(__file__).resolve().parents[2] / 'esp32-s3-sense'
        ini = (root / 'platformio.ini').read_text()
        section = ini.split('[env:servocal]')[1].split('\n[env:')[0]
        self.assertIn('build_src_filter = +<servocal.cpp>', section)
        self.assertIn('-DSERVOCAL_ACTIVE', section)
        self.assertRegex((root / 'src/servocal.cpp').read_text(), r'#if defined\(SERVOCAL_ACTIVE\)')
        self.assertRegex((root / 'include/config.h').read_text(), r'#define SERVO_SAFETY_LOCKED 1')
        self.assertNotIn('ServoCal', (root / 'src/main.cpp').read_text())


if __name__ == '__main__':
    unittest.main()
