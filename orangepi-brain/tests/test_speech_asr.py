"""
Phase-2 tests: the full capture -> VAD -> whisper.cpp -> react loop,
exercised WITHOUT a real microphone, PortAudio, or whisper.cpp.

How it works:
  * `sounddevice` is stubbed with a fake InputStream whose start() runs the
    Speech worker thread and then feeds it a scripted stream of synthetic
    48 kHz STEREO float32 chunks (silence + a "voice" burst on the mic
    channel), then signals stop.
  * `whispercpp` is stubbed with a fake WhisperModel whose transcribe()
    returns an object with a canned .text (e.g. "Das ist toll!").
  * numpy is real (it's already a project dependency), so the VAD + resample
    math is real.

The OrangePi mic (ICS43434) sits on the shared I2S card, which is fixed at
48 kHz and STEREO (the mic is on one channel, the amp on the other). So the
synthetic stream here is 48 kHz / 2 ch, and the worker selects the mic channel
and resamples to 16 kHz before the VAD -- exactly the real path.

This proves the Phase-2 wiring: the worker pulls chunks, picks the mic channel,
resamples, the VAD opens on the voice burst, the window closes on the trailing
silence, whisper.cpp is called, and the owl reacts (expression + sound).
"""

import sys
import threading
import time
import types
import unittest

import numpy as np

from stubs import install_stub_modules, FakeSerial, FakeSupervisor, FakeAudio, make_config

install_stub_modules()

import brain.speech as speech_mod  # noqa: E402
from brain.speech import Speech  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic audio (48 kHz STEREO: mic on channel 0, amp on channel 1)
# ---------------------------------------------------------------------------
def silence_chunks(n, rate=48000, chunk_s=0.3, channels=2):
    block = int(chunk_s * rate)
    return [np.zeros((block, channels), dtype=np.float32) for _ in range(n)]


def voice_chunk(rate=48000, chunk_s=0.3, amp=0.5, freq=440.0, channels=2):
    """One loud (well above the 0.02 VAD threshold) 0.3 s tone on the mic
    channel (0); the amp channel (1) is silence."""
    block = int(chunk_s * rate)
    t = np.arange(block) / rate
    mono = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([mono, np.zeros(block, dtype=np.float32)], axis=1)


def build_stream(silence_before=3, voice=2, silence_after=5,
                 rate=48000, chunk_s=0.3, channels=2):
    """Scripted stream: silence, a voice burst, then enough silence to close
    the utterance (energy_floor_ms=700 needs >~2.4 silence chunks at 0.3 s)."""
    return (silence_chunks(silence_before, rate, chunk_s, channels)
            + [voice_chunk(rate, chunk_s, channels=channels) for _ in range(voice)]
            + silence_chunks(silence_after, rate, chunk_s, channels))


# ---------------------------------------------------------------------------
# Fake sounddevice + whisper.cpp
# ---------------------------------------------------------------------------
class FakeStream:
    """Mimics sounddevice.InputStream.

    On real hardware the mic streams chunks continuously from PortAudio's own
    thread, independent of the main thread. We reproduce that: start() launches
    a daemon "feed" thread that delivers the scripted chunks (the stand-in for
    the microphone) and returns immediately. The test then joins that feed
    thread (== "the mic finished the utterance") BEFORE calling
    speech.stop(), so the worker has the whole stream to work on. This mirrors
    the real shutdown order (mic done -> stop the brain) and avoids the race
    where a fast main thread stops the worker before the mic has delivered the
    voice.

    The feed thread does NOT set the Speech stop flag -- only the test's
    speech.stop() does, after the feed has completed.
    """

    def __init__(self, device, channels, samplerate, dtype, blocksize, callback, stream, speech):
        self.callback = callback
        self.stream = stream
        self.speech = speech
        self.started = False
        self.feed_thread = None

    def start(self):
        self.started = True
        self.feed_thread = threading.Thread(target=self._run, daemon=True)
        self.feed_thread.start()
        return self

    def _run(self):
        # Deliver the scripted stream (the "microphone").
        for chunk in self.stream:
            self.callback(chunk, len(chunk), None, None)
            time.sleep(0.02)

    def stop(self):
        self.started = False

    def close(self):
        pass


def _install_sounddevice_stub(stream_chunks, speech):
    """Install a fake sounddevice whose InputStream feeds `stream_chunks` from
    a daemon thread. Returns the FakeStream so the test can join its feed
    thread before stopping the Speech worker"""
    sd = types.ModuleType("sounddevice")
    holder = {}

    class _InputStream:
        def __init__(self, device=None, channels=2, samplerate=48000,
                     dtype="float32", blocksize=None, callback=None):
            holder["fake"] = FakeStream(device, channels, samplerate, dtype, blocksize,
                                         callback, stream_chunks, speech)

        def start(self):
            return holder["fake"].start()

        def stop(self):
            holder["fake"].stop()

        def close(self):
            holder["fake"].close()

    sd.InputStream = _InputStream
    sys.modules["sounddevice"] = sd
    return holder  # test reads holder["fake"].feed_thread


def _install_whispercpp_stub(transcript):
    # brain/speech.py does:  import whispercpp
    #   self._whisper = whispercpp.WhisperModel(model_path=..., language=...)
    # and then:  result = self._whisper.transcribe(utterance, language=...)
    #   text = getattr(result, "text", None)   (else it joins result.segments)
    # So the stub's WhisperModel.transcribe() must return an object with a
    # .text attribute set to the canned transcript.
    wc = types.ModuleType("whispercpp")

    class _Model:
        def __init__(self, model_path=None, language=None, **kw):
            self.model_path = model_path
            self.language = language
            self.transcribed = []

        def transcribe(self, audio, language=None, **kw):
            self.transcribed.append(audio)
            return types.SimpleNamespace(text=transcript, segments=[])

    wc.WhisperModel = _Model
    sys.modules["whispercpp"] = wc
    return wc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestASRLoop(unittest.TestCase):
    def _setup(self, transcript, state="interacting", face=True, stream=None, **cfg_over):
        serial = FakeSerial()
        audio = FakeAudio()
        sup = FakeSupervisor(state=state, face_detected=face, audio=audio)
        cfg = make_config(**cfg_over)
        speech = Speech(serial, sup, cfg)
        holder = _install_sounddevice_stub(stream if stream is not None else build_stream(), speech)
        _install_whispercpp_stub(transcript)
        return speech, serial, sup, audio, holder

    def _run(self, speech, holder, settle=0.4):
        """Start the pipeline, let the (fake) mic deliver the whole stream,
        then stop. Mirrors real hardware: the mic streams the utterance, and
        only after it has finished do we shut the brain down."""
        speech.start()
        feed = holder["fake"].feed_thread
        if feed is not None:
            feed.join(timeout=5.0)
        time.sleep(settle)  # let the worker finish the close + (mocked) ASR
        speech.stop()

    def test_voice_triggers_reaction(self):
        speech, serial, sup, audio, holder = self._setup("Das ist toll!")
        self._run(speech, holder)
        # The owl should have reacted to the transcribed "toll" (happy).
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["happy"])
        self.assertEqual(audio.played, ["happy"])
        self.assertEqual(speech.last_heard, "Das ist toll!")

    def test_question_transcript(self):
        speech, serial, sup, audio, holder = self._setup("Wer bist du?")
        self._run(speech, holder)
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["surprised"])
        self.assertEqual(audio.played, ["alert"])

    def test_no_voice_no_reaction(self):
        # Pure silence: the VAD never opens, so nothing is transcribed.
        silence_only = silence_chunks(12)
        speech, serial, sup, audio, holder = self._setup("Das ist toll!", stream=silence_only)
        self._run(speech, holder)
        self.assertEqual(serial.commands, [])
        self.assertEqual(audio.played, [])
        self.assertEqual(speech.last_heard, "")

    def test_asleep_gate_blocks_reaction(self):
        # Owl is SLEEPING: the gate is closed, so even a voice burst is dropped
        # and never transcribed / reacted to.
        speech, serial, sup, audio, holder = self._setup("Das ist toll!", state="sleeping")
        self._run(speech, holder)
        self.assertEqual(serial.commands, [])
        self.assertEqual(audio.played, [])
        self.assertEqual(speech.last_heard, "")

    def test_no_face_gate_blocks_reaction(self):
        # require_face=True (default) and no face in frame -> dropped.
        speech, serial, sup, audio, holder = self._setup("Das ist toll!", face=False)
        self._run(speech, holder)
        self.assertEqual(serial.commands, [])
        self.assertEqual(audio.played, [])

    def test_mic_channel_is_selected_not_amp(self):
        # The card is stereo: the mic is on channel 0, the amp on channel 1. Put the voice
        # on the AMP channel only (channel 0 silent) -> the worker selects
        # channel 0, sees silence, and must NOT transcribe. This guards against
        # the worker accidentally reading the amp channel as the mic.
        block = int(0.3 * 48000)
        amp_only = []
        for _ in range(3):
            amp_only.append(np.zeros((block, 2), dtype=np.float32))
        for _ in range(2):
            amp = (0.5 * np.sin(2 * np.pi * 440.0 * np.arange(block) / 48000)).astype(np.float32)
            amp_only.append(np.stack([np.zeros(block, dtype=np.float32), amp], axis=1))
        for _ in range(5):
            amp_only.append(np.zeros((block, 2), dtype=np.float32))
        speech, serial, sup, audio, holder = self._setup("Das ist toll!", stream=amp_only)
        self._run(speech, holder)
        self.assertEqual(serial.commands, [])
        self.assertEqual(speech.last_heard, "")


# ---------------------------------------------------------------------------
# Phase 3: the web UI surfaces what the owl last heard
# ---------------------------------------------------------------------------
class TestLastHeardInWebUI(unittest.TestCase):
    """The /api/telemetry endpoint exposes the last transcript (Phase 3).

    Flask is stubbed, so we test the seam directly: the route's view function
    (app.view_functions['api_telemetry']) is what the HTTP layer calls, and
    jsonify is stubbed to return a plain dict. This proves the WebUI reads
    Speech.last_heard / last_heard_at and that the field is present iff speech
    is wired in and has heard something.
    """

    def _telemetry(self, serial, sup, speech):
        from brain.web_ui import WebUI
        ui = WebUI(serial, sup, speech=speech)
        return ui.app.view_functions["api_telemetry"]()

    def test_telemetry_exposes_last_heard_when_heard(self):
        serial = FakeSerial()
        sup = FakeSupervisor(state="interacting", face_detected=True)
        speech = Speech(serial, sup, make_config())
        speech.last_heard = "Das ist toll!"
        speech.last_heard_at = 1_700_000_000.0
        payload = self._telemetry(serial, sup, speech)
        self.assertEqual(payload["last_heard"]["text"], "Das ist toll!")
        self.assertEqual(payload["last_heard"]["at"], 1_700_000_000.0)

    def test_telemetry_last_heard_empty_before_any_utterance(self):
        serial = FakeSerial()
        sup = FakeSupervisor(state="interacting", face_detected=True)
        speech = Speech(serial, sup, make_config())  # nothing heard yet
        payload = self._telemetry(serial, sup, speech)
        self.assertEqual(payload["last_heard"]["text"], "")
        self.assertEqual(payload["last_heard"]["at"], 0.0)

    def test_telemetry_omits_last_heard_when_no_speech(self):
        serial = FakeSerial()
        sup = FakeSupervisor(state="interacting", face_detected=True)
        payload = self._telemetry(serial, sup, None)  # speech disabled
        self.assertNotIn("last_heard", payload)

    def test_feed_stamps_last_heard_at(self):
        # The timestamp is set when a transcript is fed (the worker's path).
        serial = FakeSerial()
        sup = FakeSupervisor(state="interacting", face_detected=True)
        speech = Speech(serial, sup, make_config())
        before = time.time()
        speech.feed("Das ist toll!")
        self.assertGreaterEqual(speech.last_heard_at, before)
        self.assertLessEqual(speech.last_heard_at, time.time())


if __name__ == "__main__":
    unittest.main(verbosity=2)
