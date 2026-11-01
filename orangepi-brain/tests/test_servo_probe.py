"""Host probe never sends absolute targets, return movements or retries."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    'servo_probe', Path(__file__).resolve().parents[2] / 'esp32-s3-sense/tools/servo_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class TestServoProbe(unittest.TestCase):
    def test_single_step_only(self):
        stream = Mock()
        stream.readline.side_effect = [b'boot noise\n', b'{"type":"telemetry"}',
                                      b'{"type":"servo_step_ack","channel":7,"ok":true}']
        probe.send_step(stream, 7, -1)
        self.assertEqual(json.loads(stream.write.call_args.args[0]),
                         {'type': 'servo_step', 'channel': 7, 'direction': -1})
        stream.write.assert_called_once()

    def test_refusal_and_timeout_never_retry(self):
        stream = Mock()
        stream.readline.return_value = b'{"type":"servo_step_ack","channel":7,"ok":false}'
        with self.assertRaises(RuntimeError):
            probe.send_step(stream, 7, 1)
        stream.write.assert_called_once()
        stream.reset_mock()
        with patch.object(probe.time, 'monotonic', side_effect=[0, 4]):
            with self.assertRaises(RuntimeError):
                probe.send_step(stream, 7, 1)
        stream.write.assert_called_once()

    def test_interrupt_closes_without_return_command(self):
        stream = Mock()
        serial = Mock()
        serial.Serial.return_value = stream
        args = Mock(channel=7, direction=1, port='/dev/test')
        with patch.dict('sys.modules', {'serial': serial}), \
                patch.object(probe, 'parse_args', return_value=args), \
                patch('builtins.input', return_value=''), \
                patch('builtins.print'), \
                patch.object(probe, 'send_step', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                probe.main()
        stream.close.assert_called_once()
        stream.write.assert_not_called()
        self.assertFalse(stream.dtr)
        self.assertFalse(stream.rts)
