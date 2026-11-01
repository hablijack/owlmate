"""Native tests for the motion image: ServoMotion + OwlChoreo + the shipped profile table."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

FIRMWARE = Path(__file__).resolve().parents[2] / 'esp32-s3-sense'


class TestServoMotion(unittest.TestCase):
    def test_motion_layer_holds_against_fake_pca9685(self):
        compiler = shutil.which('c++')
        if compiler is None:
            self.skipTest('native C++ compiler required for firmware safety test')
        with tempfile.TemporaryDirectory(prefix='owl-servomotion-') as directory:
            output = str(Path(directory) / 'test')
            subprocess.run([compiler, '-std=c++11', '-I' + str(FIRMWARE / 'lib/ServoMotion'),
                            '-I' + str(FIRMWARE / 'include'), '-I' + str(FIRMWARE / 'tests/servo_safety'),
                            str(FIRMWARE / 'lib/ServoMotion/ServoMotion.cpp'),
                            str(FIRMWARE / 'lib/ServoMotion/OwlChoreo.cpp'),
                            str(FIRMWARE / 'tests/servomotion/test.cpp'), '-o', output], check=True)
            result = subprocess.run([output], check=True, capture_output=True, text=True)
            self.assertIn('all checks passed', result.stdout)

    def test_main_image_moves_and_locked_env_does_not(self):
        """Motion is a BUILD choice: the main env has it, xiao_esp32s3_locked and config.h do not."""
        ini = '\n'.join(line for line in (FIRMWARE / 'platformio.ini').read_text().splitlines()
                        if not line.lstrip().startswith(';'))
        main_env = ini.split('[env:xiao_esp32s3]')[1].split('\n[env:')[0].split('\n[')[0]
        self.assertIn('-DSERVO_MOTION_ENABLED=1', main_env)
        locked_env = ini.split('[env:xiao_esp32s3_locked]')[1].split('\n[env:')[0].split('\n[')[0]
        self.assertNotIn('SERVO_MOTION_ENABLED', locked_env)
        self.assertIn('extends = env:xiao_esp32s3', locked_env)
        header = (FIRMWARE / 'include/config.h').read_text()
        self.assertRegex(header, r'#ifndef SERVO_MOTION_ENABLED\s+#define SERVO_MOTION_ENABLED 0')
        self.assertRegex(header, r'#else\s+#define SERVO_SAFETY_LOCKED 1')
        # no other env (diagnostics, locked) may switch motion on; the default stays 0
        for name, body in re.findall(r'\[env:(\w+)\](.*?)(?=\n\[env:|\Z)', ini, re.S):
            if name != 'xiao_esp32s3':
                self.assertNotIn('SERVO_MOTION_ENABLED=1', body, name)
        # no runtime switch: the serial protocol must not mention motion enabling
        self.assertNotIn('SERVO_MOTION_ENABLED', (FIRMWARE / 'src/protocol.cpp').read_text()
                         .replace('!SERVO_MOTION_ENABLED', ''))


if __name__ == '__main__':
    unittest.main()
