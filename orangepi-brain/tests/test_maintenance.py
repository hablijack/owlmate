"""Maintenance web mode must never open or reconnect USB serial."""
import unittest
from unittest.mock import patch
from stubs import install_stub_modules
install_stub_modules()

import main as brain_main  # noqa: E402


class TestMaintenance(unittest.TestCase):
    def test_no_serial_never_connects_or_reconnects(self):
        config = {'serial': {'port': '/dev/DO_NOT_OPEN', 'baudrate': 115200, 'timeout': 1},
                  'web': {'enabled': False}}
        with patch.object(brain_main, 'load_config', return_value=config), \
                patch.object(brain_main, 'SerialHandler') as handler, \
                patch.object(brain_main, 'Audio'), \
                patch.object(brain_main, 'Supervisor'), \
                patch.object(brain_main, 'Speech'), \
                patch.object(brain_main, 'print_banner'), \
                patch.object(brain_main.time, 'sleep', side_effect=KeyboardInterrupt), \
                patch('sys.argv', ['main.py', '--no-serial']):
            brain_main.main()
        handler.return_value.connect.assert_not_called()
        handler.return_value.read_loop.assert_not_called()
        handler.return_value.disconnect.assert_called_once()
