"""Native test for the firmware's GPS diagnostics parser (lib/GpsDiag).

GSV parsing and the NMEA checksum run on the owl; they are compiled here with
the host compiler against esp32-s3-sense/tests/gpsdiag/test.cpp.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

FIRMWARE = Path(__file__).resolve().parents[2] / 'esp32-s3-sense'


class TestGpsDiag(unittest.TestCase):
    def test_gsv_parser_and_checksum(self):
        compiler = shutil.which('c++')
        if compiler is None:
            self.skipTest('native C++ compiler required for the GpsDiag test')
        with tempfile.TemporaryDirectory(prefix='owl-gpsdiag-') as directory:
            output = str(Path(directory) / 'test')
            subprocess.run([compiler, '-std=c++11', '-I' + str(FIRMWARE / 'lib/GpsDiag'),
                            str(FIRMWARE / 'lib/GpsDiag/GpsDiag.cpp'),
                            str(FIRMWARE / 'tests/gpsdiag/test.cpp'), '-o', output], check=True)
            result = subprocess.run([output], check=True, capture_output=True, text=True)
            self.assertIn('all checks passed', result.stdout)

    def test_firmware_sends_the_tested_pmtk314(self):
        # The PMTK314 string in Sensors.cpp is the one whose checksum the native
        # test verifies; a typo there makes the module silently ignore it.
        source = (FIRMWARE / 'src/Sensors.cpp').read_text()
        self.assertIn('"$PMTK314,0,1,0,1,0,5,0,0,0,0,0,0,0,0,0,0,0,0,0*2D"', source)
        self.assertIn('$PMTK314,0,1,0,1,0,5,0,0,0,0,0,0,0,0,0,0,0,0,0*2D',
                      (FIRMWARE / 'tests/gpsdiag/test.cpp').read_text())


if __name__ == '__main__':
    unittest.main()
