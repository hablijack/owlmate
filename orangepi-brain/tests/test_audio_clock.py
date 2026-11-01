"""Silent TX keepalive for the A733 shared I2S capture path."""
import subprocess
import unittest
from unittest.mock import Mock, patch

from stubs import install_stub_modules
install_stub_modules()
from brain.audio import Audio  # noqa: E402


class TestAudioClock(unittest.TestCase):
    def make_audio(self, enabled=True, keep=False):
        with patch('brain.audio.shutil.which', return_value='/usr/bin/aplay'), \
                patch.object(Audio, '_load_samples', return_value={}):
            return Audio({'audio': {'enabled': enabled, 'keep_playback_clock': keep}})

    def test_clock_is_opt_in_and_requires_enabled_audio(self):
        with patch('brain.audio.subprocess.Popen') as spawn:
            for enabled, keep in [(True, False), (False, True)]:
                with self.subTest(enabled=enabled, keep=keep):
                    audio = self.make_audio(enabled, keep)
                    audio.close()
            spawn.assert_not_called()

    def test_silent_shared_stream_and_idempotent_shutdown(self):
        process = Mock()
        process.poll.return_value = None
        with patch('brain.audio.subprocess.Popen', return_value=process) as spawn:
            audio = self.make_audio(keep=True)
            self.assertEqual(audio.capture_guard(), (0, 0.0))  # silence is not an owl call
            cmd = spawn.call_args[0][0]
            self.assertEqual(cmd, ['aplay', '-q', '-D', 'owl_playback', '-t', 'raw',
                                   '-f', 'S16_LE', '-r', '48000', '-c', '2', '/dev/zero'])
            audio.close()
            audio.close()
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=2)

    def test_spawn_failure_degrades_and_stuck_process_is_reaped(self):
        with patch('brain.audio.subprocess.Popen', side_effect=OSError('missing')):
            audio = self.make_audio(keep=True)
            self.assertTrue(audio._ready)  # one-shot audio remains available
            audio.close()
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('aplay', 2), 0]
        with patch('brain.audio.subprocess.Popen', return_value=process):
            audio = self.make_audio(keep=True)
            audio.close()
            process.kill.assert_called_once()
            self.assertEqual(process.wait.call_count, 2)
