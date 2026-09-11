"""
Tests for the optional OrangePi emotion layer (brain/emotion.py) and its
integration into the speech pipeline (brain/speech.py feed()).

The layer is OFF by default: when disabled, feed() never consults it and the
keyword clusters are the sole reaction source (the RPi behaviour). These tests
prove both halves of that contract:

  * EmotionClassifier.classify() returns the model's label when enabled and a
    model is loaded, and "" (no opinion) when disabled, when no model loaded,
    or on an empty transcript.
  * feed() maps a tagged emotion to the configured expression, gives it
    PRECEDENCE over the keyword clusters, and falls through to the keyword
    clusters when the tag is empty, unmapped, or the layer is disabled.

llama_cpp is stubbed (a fake Llama whose create_completion returns a canned
label), so no LLM runtime is needed. The one end-to-end test reuses the ASR
test's synthetic-stream helpers to drive the real worker: whisper.cpp
transcribes, the (stubbed) emotion layer tags it, feed() reacts.
"""

import sys
import types
import unittest

from stubs import install_stub_modules, FakeSerial, FakeSupervisor, FakeAudio, make_config

install_stub_modules()

from brain.emotion import EmotionClassifier  # noqa: E402
from brain.speech import Speech  # noqa: E402

# Reuse the ASR test's synthetic-stream + stub helpers for the one end-to-end
# test (48 kHz stereo mic stream, whisper.cpp stub). Importing the module is
# safe: its module-level install_stub_modules() is idempotent.
from test_speech_asr import (  # noqa: E402
    build_stream, _install_sounddevice_stub, _install_whispercpp_stub,
)


def _install_llama_stub(label):
    """Override the default llama_cpp stub with one whose model returns `label`."""
    m = types.ModuleType("llama_cpp")

    class _Llama:
        def __init__(self, model_path=None, n_ctx=512, **kw):
            self.model_path = model_path
            self.n_ctx = n_ctx

        def create_completion(self, prompt, max_tokens=8, temperature=0.0, **kw):
            return {"choices": [{"text": label}]}

    m.Llama = _Llama
    sys.modules["llama_cpp"] = m
    return m


# ---------------------------------------------------------------------------
# EmotionClassifier.classify()
# ---------------------------------------------------------------------------
class TestEmotionClassifier(unittest.TestCase):
    def test_classify_returns_model_label(self):
        _install_llama_stub("Freude")
        cfg = make_config()
        cfg["emotion"]["enabled"] = True
        cfg["emotion"]["model_path"] = "models/emotion/test.gguf"
        ec = EmotionClassifier(cfg)
        ec.start()
        try:
            self.assertEqual(ec.classify("Das ist toll"), "Freude")
        finally:
            ec.stop()

    def test_classify_empty_when_disabled(self):
        _install_llama_stub("Freude")
        cfg = make_config()  # emotion.enabled defaults to False
        ec = EmotionClassifier(cfg)
        self.assertFalse(ec.enabled)
        self.assertEqual(ec.classify("Das ist toll"), "")

    def test_classify_empty_when_model_not_loaded(self):
        _install_llama_stub("Freude")
        cfg = make_config()
        cfg["emotion"]["enabled"] = True
        cfg["emotion"]["model_path"] = "models/emotion/test.gguf"
        ec = EmotionClassifier(cfg)  # enabled, but start() never called
        self.assertIsNone(ec._llama)
        self.assertEqual(ec.classify("Das ist toll"), "")

    def test_classify_empty_on_empty_transcript(self):
        _install_llama_stub("Freude")
        cfg = make_config()
        cfg["emotion"]["enabled"] = True
        cfg["emotion"]["model_path"] = "models/emotion/test.gguf"
        ec = EmotionClassifier(cfg)
        ec.start()
        try:
            self.assertEqual(ec.classify(""), "")
            self.assertEqual(ec.classify("   "), "")
        finally:
            ec.stop()

    def test_start_raises_when_enabled_but_no_model(self):
        cfg = make_config()
        cfg["emotion"]["enabled"] = True
        cfg["emotion"]["model_path"] = ""  # enabled but unconfigured
        ec = EmotionClassifier(cfg)
        with self.assertRaises(RuntimeError):
            ec.start()


# ---------------------------------------------------------------------------
# feed() with an emotion tag
# ---------------------------------------------------------------------------
class TestEmotionInFeed(unittest.TestCase):
    def _speech(self, **cfg_over):
        serial = FakeSerial()
        audio = FakeAudio()
        sup = FakeSupervisor(state="interacting", face_detected=True, audio=audio)
        cfg = make_config(**cfg_over)
        cfg["emotion"]["enabled"] = True
        cfg["emotion"]["model_path"] = "models/emotion/test.gguf"
        speech = Speech(serial, sup, cfg)
        return speech, serial, sup, audio

    def test_label_sets_mapped_expression(self):
        speech, serial, sup, audio = self._speech()
        # "irgendwas" matches no keyword cluster, so only the emotion can react.
        speech.feed("irgendwas", emotion="Freude")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["happy"])

    def test_emotion_takes_precedence_over_keywords(self):
        # "toll" is in the happy cluster, but the emotion tag is Wut (angry):
        # the emotion layer runs BEFORE the keyword clusters and wins.
        speech, serial, sup, audio = self._speech()
        speech.feed("Das ist toll", emotion="Wut")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["angry"])

    def test_unmapped_label_falls_through_to_keywords(self):
        # "Neutrale" is not in the labels map -> ignored, keyword cluster wins.
        speech, serial, sup, audio = self._speech()
        speech.feed("Das ist toll", emotion="Neutrale")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["happy"])

    def test_empty_label_falls_through_to_keywords(self):
        speech, serial, sup, audio = self._speech()
        speech.feed("Das ist toll", emotion="")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["happy"])

    def test_disabled_layer_ignores_label(self):
        # Layer disabled: a passed-in label must NOT react; the keyword wins.
        serial = FakeSerial()
        audio = FakeAudio()
        sup = FakeSupervisor(state="interacting", face_detected=True, audio=audio)
        cfg = make_config()  # emotion.enabled stays False
        speech = Speech(serial, sup, cfg)
        speech.feed("Das ist toll", emotion="Wut")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["happy"])

    def test_label_plays_configured_sound(self):
        serial = FakeSerial()
        audio = FakeAudio()
        sup = FakeSupervisor(state="interacting", face_detected=True, audio=audio)
        cfg = make_config()
        cfg["emotion"]["enabled"] = True
        cfg["emotion"]["model_path"] = "models/emotion/test.gguf"
        cfg["emotion"]["sounds"] = {"Freude": "happy"}
        speech = Speech(serial, sup, cfg)
        speech.feed("irgendwas", emotion="Freude")
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["happy"])
        self.assertEqual(audio.played, ["happy"])


# ---------------------------------------------------------------------------
# End-to-end: worker transcribes (whisper.cpp) -> tags (llama.cpp) -> feed()
# ---------------------------------------------------------------------------
class TestEmotionWorkerIntegration(unittest.TestCase):
    def test_worker_tags_and_reacts(self):
        # whisper.cpp hears "Das ist toll"; the emotion layer tags it Wut, so
        # the owl reacts angry (emotion beats the "toll" happy keyword).
        _install_whispercpp_stub("Das ist toll")
        _install_llama_stub("Wut")
        serial = FakeSerial()
        audio = FakeAudio()
        sup = FakeSupervisor(state="interacting", face_detected=True, audio=audio)
        cfg = make_config()
        cfg["emotion"]["enabled"] = True
        cfg["emotion"]["model_path"] = "models/emotion/test.gguf"
        speech = Speech(serial, sup, cfg)
        holder = _install_sounddevice_stub(build_stream(), speech)
        speech.start()
        feed = holder["fake"].feed_thread
        if feed is not None:
            feed.join(timeout=5.0)
        import time
        time.sleep(0.4)
        speech.stop()
        self.assertEqual([c for k, c in serial.commands if k == "expression"], ["angry"])
        self.assertEqual(speech.last_heard, "Das ist toll")


if __name__ == "__main__":
    unittest.main(verbosity=2)
