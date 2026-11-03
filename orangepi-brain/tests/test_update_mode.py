"""Explicit OTA control: Python/Web UI contract and firmware entry guard."""
from pathlib import Path
import unittest
from unittest.mock import Mock

from stubs import install_stub_modules, call_web_view
install_stub_modules()

from brain.serial_handler import SerialHandler  # noqa: E402
from brain.web_ui import WebUI  # noqa: E402
import brain.web_ui as webui  # noqa: E402


class TestUpdateMode(unittest.TestCase):
    def test_serial_command(self):
        serial = SerialHandler('/dev/null')
        serial.send_command = Mock(return_value=True)
        for active in (True, False):
            self.assertTrue(serial.set_update_mode(active))
            serial.send_command.assert_called_with({'type': 'update_mode', 'active': active})
        with self.assertRaises(ValueError):
            serial.set_update_mode('false')

    def test_web_control(self):
        serial = Mock()
        ui = WebUI(serial, Mock())
        # Direct views match the suite's Flask stub convention.
        for active in (True, False):
            for sent in (True, False):
                serial.set_update_mode.return_value = sent
                result = call_web_view(ui, 'api_update_mode', {'active': active})
                self.assertEqual(result['ok'], sent)
                serial.set_update_mode.assert_called_with(active)
        serial.reset_mock()
        for body in ({}, {'active': 'false'}, {'active': 1}, None, []):
            self.assertFalse(call_web_view(ui, 'api_update_mode', body)['ok'])
        serial.set_update_mode.assert_not_called()

    def test_expression_plays_gesture_unless_eyes_only(self):
        serial = Mock()
        ui = WebUI(serial, Mock())
        self.assertEqual(call_web_view(ui, 'api_expression', {'value': 'happy'})['gesture'], 'happy')
        serial.react.assert_called_once_with('happy')
        serial.reset_mock()
        result = call_web_view(ui, 'api_expression', {'value': 'happy', 'gesture': False})
        self.assertIsNone(result['gesture'])
        serial.react.assert_not_called()
        serial.set_expression.assert_called_once_with('happy')

    def test_vibration_cannot_control_ota(self):
        # Source guard, not a substitute for the hardware walking acceptance test.
        root = Path(__file__).resolve().parents[2] / 'esp32-s3-sense'
        behavior = (root / 'src/behavior.cpp').read_text()
        update = behavior.split('void update() {', 1)[1]
        self.assertNotIn('enterUpdateMode(', update)
        self.assertNotIn('exitUpdateMode(', update)
        sensors = (root / 'src/Sensors.cpp').read_text()
        self.assertNotIn('_updateSeqPending', sensors)
        self.assertNotIn('UPDATE_TAP', sensors)
        protocol = (root / 'src/protocol.cpp').read_text()
        self.assertIn('strcmp(type, "update_mode") != 0', protocol)
        self.assertIn('doc["active"].is<bool>()', protocol)

    def test_page_has_explicit_controls(self):
        self.assertIn('data-act="update-mode" data-active="true"', webui.TEMPLATE)
        self.assertIn('data-act="update-mode" data-active="false"', webui.TEMPLATE)
        self.assertIn("post('/api/update-mode', { active })", webui.TEMPLATE)
