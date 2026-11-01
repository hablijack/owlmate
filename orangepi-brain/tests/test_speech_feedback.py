"""Playback barriers must cover queued audio and in-flight ASR, not just VAD."""
import subprocess
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np
from stubs import install_stub_modules, FakeSerial, FakeSupervisor, make_config
install_stub_modules()
from brain.audio import Audio  # noqa: E402
from brain.speech import Speech  # noqa: E402
from brain.supervisor import Supervisor  # noqa: E402


class TestPlaybackGuard(unittest.TestCase):
    def audio(self):
        with patch('brain.audio.shutil.which', return_value='/usr/bin/aplay'), \
                patch.object(Audio, '_load_samples', return_value={}):
            return Audio({'audio': {'enabled': True, 'device': 'owl_playback',
                                    'feedback_tail_s': 0.6}})

    def test_overlapping_players_and_monotonic_tail(self):
        audio = self.audio()
        with patch('brain.audio.time.monotonic', return_value=100):
            audio._begin_playback()
            audio._begin_playback()
            self.assertEqual(audio.capture_guard()[1], float('inf'))
            audio._end_playback()
            self.assertEqual(audio.capture_guard()[1], float('inf'))
        with patch('brain.audio.time.monotonic', return_value=110):
            audio._end_playback()
            generation, until = audio.capture_guard()
            self.assertGreater(generation, 0)
            self.assertAlmostEqual(until, 110.6)

    def test_spawn_failure_releases_guard_and_device_uses_D(self):
        audio = self.audio()
        with patch('brain.audio.subprocess.Popen', side_effect=OSError('missing')) as spawn:
            self.assertFalse(audio.play('happy'))
        self.assertIn('-D', spawn.call_args[0][0])
        self.assertNotEqual(audio.capture_guard()[1], float('inf'))

    def test_timeout_kills_player_before_releasing_guard(self):
        audio = self.audio()
        proc = Mock()
        proc.communicate.side_effect = [subprocess.TimeoutExpired('aplay', 15), (None, None)]
        proc.poll.return_value = 0
        audio._begin_playback()
        audio._feed(proc, b'wav')
        proc.kill.assert_called_once()
        self.assertEqual(proc.communicate.call_count, 2)
        self.assertNotEqual(audio.capture_guard()[1], float('inf'))

    def test_supervisor_delegates_without_exposing_audio_handle(self):
        sup = Supervisor.__new__(Supervisor)
        sup.audio = None
        self.assertEqual(sup.audio_capture_guard(), (0, 0.0))
        sup.audio = Mock()
        sup.audio.capture_guard.return_value = (7, 123.5)
        self.assertEqual(sup.audio_capture_guard(), (7, 123.5))


class TestSpeechFeedback(unittest.TestCase):
    def setUp(self):
        self.sup = FakeSupervisor(face_detected=True)
        self.guard = (0, 0.0)
        self.sup.audio_capture_guard = lambda: self.guard
        self.speech = Speech(FakeSerial(), self.sup, make_config())
        self.speech.window_s = 0.3
        self.speech._whisper = Mock()
        self.speech._whisper.transcribe.return_value = [types.SimpleNamespace(text='Das ist toll.')]
        self.speech.feed = Mock()
        self.voice = np.ones(4800, dtype=np.float32) * 0.1

    def test_no_playback_allows_initial_block_before_process_clock_origin(self):
        with patch('brain.speech.time.monotonic', return_value=0.1):
            self.speech._audio_callback(np.ones((14400, 2)), 14400, None, None)
            self.assertEqual(self.speech._queue.qsize(), 1)
            self.speech._process_chunk(self.voice, captured_generation=0, captured_at=-0.2)
        self.speech.feed.assert_called_once()

    def test_callback_drops_playback_tail_and_overlapping_block(self):
        data = np.ones((14400, 2), dtype=np.float32)
        for now, guard in [(100, (1, float('inf'))), (100.4, (1, 100.2))]:
            with self.subTest(now=now):
                self.guard = guard
                with patch('brain.speech.time.monotonic', return_value=now):
                    self.speech._audio_callback(data, len(data), None, None)
                self.assertTrue(self.speech._queue.empty())
        self.guard = (1, 100.2)
        with patch('brain.speech.time.monotonic', return_value=101):
            self.speech._audio_callback(data, len(data), None, None)
        self.assertEqual(self.speech._queue.qsize(), 1)

    def test_adc_timestamp_rejects_delayed_echo_callback(self):
        self.guard = (1, 100.2)
        info = types.SimpleNamespace(currentTime=500, inputBufferAdcTime=498)
        with patch('brain.speech.time.monotonic', return_value=101):
            self.speech._audio_callback(np.ones((14400, 2)), 14400, info, None)
        self.assertTrue(self.speech._queue.empty())

    def test_unavailable_adc_timestamp_does_not_permanently_block_capture(self):
        self.guard = (1, 100.2)
        info = types.SimpleNamespace(currentTime=500, inputBufferAdcTime=0)
        with patch('brain.speech.time.monotonic', return_value=101):
            self.speech._audio_callback(np.ones((14400, 2)), 14400, info, None)
        self.assertEqual(self.speech._queue.qsize(), 1)

    def test_queued_preplayback_audio_is_not_transcribed_after_tail(self):
        self.guard = (1, 100.2)
        with patch('brain.speech.time.monotonic', return_value=101):
            self.speech._process_chunk(self.voice, captured_generation=0, captured_at=99)
        self.speech._whisper.transcribe.assert_not_called()
        self.speech.feed.assert_not_called()

    def test_generation_change_resets_partial_utterance_and_recovers(self):
        self.speech.window_s = 2.5
        self.speech._process_chunk(self.voice)
        self.guard = (1, 0.0)
        self.speech._process_chunk(self.voice)
        self.assertEqual(len(self.speech._utterance), 1)
        self.speech.window_s = .3
        self.speech._process_chunk(self.voice)
        self.speech.feed.assert_called_once_with('Das ist toll.', emotion='')

    def test_sound_during_inference_invalidates_even_after_playback_finishes(self):
        def transcribe(*args, **kwargs):
            self.guard = (2, 0.0)  # sound started AND ended while model ran
            return [types.SimpleNamespace(text='Das ist toll.')]
        self.speech._whisper.transcribe.side_effect = transcribe
        self.speech._process_chunk(self.voice)
        self.speech.feed.assert_not_called()
        self.assertEqual(self.speech._utterance, [])
