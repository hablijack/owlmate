"""Native test for the firmware's hard-iron replacement rule (lib/OwlImu/MagCalRule.h).

Compiled with the host compiler against esp32-s3-sense/tests/magcal/test.cpp.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

FIRMWARE = Path(__file__).resolve().parents[2] / 'esp32-s3-sense'


class TestMagCalRule(unittest.TestCase):
    def test_partial_run_does_not_replace_stored_offsets(self):
        compiler = shutil.which('c++')
        if compiler is None:
            self.skipTest('native C++ compiler required for the MagCalRule test')
        with tempfile.TemporaryDirectory(prefix='owl-magcal-') as directory:
            output = str(Path(directory) / 'test')
            subprocess.run([compiler, '-std=c++11', '-I' + str(FIRMWARE / 'lib/OwlImu'),
                            str(FIRMWARE / 'tests/magcal/test.cpp'), '-o', output], check=True)
            result = subprocess.run([output], check=True, capture_output=True, text=True)
            self.assertIn('all checks passed', result.stdout)

    def test_owlimu_uses_the_rule(self):
        # The rule is only worth testing if OwlImu actually decides with it.
        source = (FIRMWARE / 'lib/OwlImu/OwlImu.cpp').read_text()
        self.assertIn('magLiveOffsetsWin(', source)
        self.assertNotIn('if (axes >= 2) off = liveOff', source)


if __name__ == '__main__':
    unittest.main()
