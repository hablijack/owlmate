"""
Sentence -> mood: the classifier (brain/mood.py), its place in the speech pipeline, the
Vosk recogniser path, and the training data it is built from (assets/mood/sentences.yaml).

No onnxruntime / tokenizers / vosk needed: the classifier gets fake modules, the pipeline
gets a fake classifier, the Vosk path a scripted recogniser.
"""

import json
import re
import sys
import time
import types
import unittest
from pathlib import Path

import numpy as np

from stubs import install_stub_modules, FakeSerial, FakeSupervisor, FakeAudio, make_config

install_stub_modules()

from brain import expressions  # noqa: E402
from brain.mood import MoodClassifier  # noqa: E402
from brain.speech import Speech  # noqa: E402

SENTENCES = Path(__file__).resolve().parent.parent / "assets" / "mood" / "sentences.yaml"


def read_sentences():
    """The two-level yaml of sentences.yaml, read without PyYAML (it may be stubbed here)."""
    data, section, mood = {}, None, None
    for line in SENTENCES.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\w+:$", line):
            section = data.setdefault(line[:-1], {})
        elif re.match(r"^  \w+:$", line):
            mood = section.setdefault(line.strip()[:-1], [])
        elif line.startswith("    - "):
            mood.append(line[6:].strip().strip("'\""))
    return data


class FakeMood:
    """Stand-in for MoodClassifier with a canned answer."""
    def __init__(self, answer=("", 0.0), enabled=True, min_confidence=0.5):
        self.enabled, self.min_confidence, self.answer, self.asked = enabled, min_confidence, answer, []

    def classify(self, text):
        self.asked.append(text)
        return self.answer


def make_speech(answer, state="interacting", **cfg):
    serial, audio = FakeSerial(), FakeAudio()
    sup = FakeSupervisor(state=state, face_detected=False, audio=audio)
    base = dict(name_keywords=["hubert"], name_alone_aliases=["robert", "rupert"], require_face=False,
                cooldown_s=0.0)
    base.update(cfg)
    speech = Speech(serial, sup, make_config(**base))
    speech.mood = FakeMood(answer)
    return speech, serial, sup, audio


class TestMoodInThePipeline(unittest.TestCase):
    def test_a_confident_mood_is_shown_with_eyes_body_and_sound(self):
        speech, serial, sup, audio = make_speech(("sad_down", 0.93))
        speech.feed("hubert heute ist wirklich ein sauwetter")
        self.assertEqual(sup.moods, ["sad_down"])
        self.assertEqual(audio.played, ["sad"])
        self.assertEqual(speech.mood.asked, ["hubert heute ist wirklich ein sauwetter"])

    def test_neutral_or_unsure_only_looks_at_the_user(self):
        for answer in (("neutral", 0.99), ("glee", 0.3), ("", 0.0)):
            speech, serial, sup, audio = make_speech(answer)
            speech.feed("hubert ich gehe jetzt einkaufen")
            self.assertEqual(sup.moods, [], answer)
            self.assertIn(("gaze", (0.0, 0.0)), serial.commands, answer)

    def test_the_mood_model_replaces_the_keyword_clusters(self):
        # "toll" is in the happy cluster, but the model decides.
        speech, serial, sup, audio = make_speech(("scared", 0.8))
        speech.feed("hubert das ist toll")
        self.assertEqual(sup.moods, ["scared"])
        self.assertNotIn(("expression", "happy"), serial.commands)

    def test_unaddressed_speech_is_not_even_classified(self):
        speech, serial, sup, audio = make_speech(("glee", 0.99))
        speech.feed("ich habe gerade im lotto gewonnen")
        self.assertEqual(speech.mood.asked, [])
        self.assertEqual(sup.moods, [])

    def test_navigation_still_wins_over_the_mood(self):
        speech, serial, sup, audio = make_speech(("neutral", 0.9),
                                                 nav_triggers=["bring mich nach"])
        called = []
        speech._react_navigate = lambda t: called.append(t)
        speech.feed("hubert bring mich nach hause")
        self.assertEqual(called, ["hubert bring mich nach hause"])
        self.assertEqual(speech.mood.asked, [])


class TestNameAlone(unittest.TestCase):
    def test_the_name_alone_shows_it_is_listening(self):
        speech, serial, sup, audio = make_speech(("glee", 0.99))
        speech.feed("hubert")
        self.assertEqual(sup.moods, ["focused"])
        self.assertEqual(audio.played, ["hello"])        # focused itself stays silent
        speech.feed("ich habe gerade im lotto gewonnen")  # inside the window: no name needed
        self.assertEqual(sup.moods, ["focused", "glee"])

    def test_a_mishearing_counts_only_when_it_is_the_whole_utterance(self):
        speech, serial, sup, audio = make_speech(("glee", 0.99))
        speech.feed("robert hat vorhin angerufen")
        self.assertEqual(sup.moods, [])
        speech.feed("rupert")
        self.assertEqual(sup.moods, ["focused"])

    def test_a_mishearing_alone_wakes_a_sleeping_owl(self):
        speech, serial, sup, audio = make_speech(("glee", 0.99), state="sleeping")
        speech.feed("robert")
        self.assertIn("wake", [k for k, _ in serial.commands])


class ScriptedRecognizer:
    """A Vosk recogniser that finishes `text` after `after` chunks."""
    def __init__(self, text, after=3):
        self.text, self.after, self.n, self.resets = text, after, 0, 0

    def AcceptWaveform(self, data):
        self.n += 1
        return self.n == self.after

    def Result(self):
        return json.dumps({"text": self.text})

    def Reset(self):
        self.resets += 1
        self.n = 0


class TestVoskPath(unittest.TestCase):
    def setUp(self):
        self.speech, self.serial, self.sup, self.audio = make_speech(("glee", 0.99), engine="vosk")
        self.heard = []
        self.speech.feed = lambda t: self.heard.append(t)
        self.chunk = np.zeros(4800, dtype=np.float32)

    def run_chunks(self, n, rec):
        self.speech._vosk = rec
        for _ in range(n):
            self.speech._process_chunk_vosk(self.chunk, captured_generation=0, captured_at=time.monotonic())

    def test_a_finished_sentence_goes_to_feed(self):
        self.run_chunks(3, ScriptedRecognizer("hubert das ist toll"))
        self.assertEqual(self.heard, ["hubert das ist toll"])

    def test_it_also_listens_while_asleep_so_the_name_can_wake_the_owl(self):
        self.sup.set_state("sleeping")
        self.run_chunks(3, ScriptedRecognizer("hubert"))
        self.assertEqual(self.heard, ["hubert"])

    def test_not_in_update_mode(self):
        self.sup.set_state("update")
        rec = ScriptedRecognizer("hubert", after=1)
        self.run_chunks(3, rec)
        self.assertEqual(self.heard, [])

    def test_the_owls_own_sound_resets_the_recogniser(self):
        rec = ScriptedRecognizer("hubert das ist toll")
        self.run_chunks(2, rec)
        self.sup.audio.capture_guard = lambda: (1, time.monotonic() + 5.0)   # playback running
        self.speech._process_chunk_vosk(self.chunk, captured_generation=1, captured_at=time.monotonic())
        self.assertEqual(rec.resets, 1)
        self.assertEqual(self.heard, [])

    def test_an_empty_result_is_ignored(self):
        self.run_chunks(3, ScriptedRecognizer(""))
        self.assertEqual(self.heard, [])


class TestMoodClassifier(unittest.TestCase):
    def install_fakes(self, logits):
        ort = types.ModuleType("onnxruntime")
        ort.SessionOptions = lambda: types.SimpleNamespace()

        class Session:
            def __init__(self, path, opts=None, providers=None):
                pass

            def run(self, outputs, feeds):
                self.feeds = feeds
                return [np.array([logits], dtype=np.float32)]
        ort.InferenceSession = Session
        tk = types.ModuleType("tokenizers")

        class Tok:
            @staticmethod
            def from_file(path):
                return Tok()

            def enable_truncation(self, n):
                pass

            def encode(self, text):
                return types.SimpleNamespace(ids=[0, 5, 2], attention_mask=[1, 1, 1])
        tk.Tokenizer = Tok
        self._saved = {k: sys.modules.get(k) for k in ("onnxruntime", "tokenizers")}
        sys.modules["onnxruntime"], sys.modules["tokenizers"] = ort, tk

    def tearDown(self):
        for k, v in getattr(self, "_saved", {}).items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def make(self, tmp, logits):
        self.install_fakes(logits)
        Path(tmp, "labels.json").write_text(json.dumps(["glee", "sad_down", "neutral"]))
        m = MoodClassifier({"mood": {"enabled": True, "model_dir": tmp}})
        m.start()
        return m

    def test_returns_the_most_likely_mood_with_its_probability(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mood, p = self.make(tmp, [0.0, 3.0, 0.0]).classify("Heute ist ein Sauwetter")
        self.assertEqual(mood, "sad_down")
        self.assertAlmostEqual(p, np.exp(3) / (np.exp(3) + 2), places=5)

    def test_off_or_empty_never_answers(self):
        self.assertEqual(MoodClassifier({}).classify("Hallo"), ("", 0.0))
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.make(tmp, [1.0, 0.0, 0.0]).classify("  "), ("", 0.0))


class TestTrainingData(unittest.TestCase):
    """assets/mood/sentences.yaml: what tools/train_mood.py learns from."""
    def setUp(self):
        self.data = read_sentences()

    def norm(self, s):
        return " ".join(re.findall(r"[a-zäöüß0-9]+", s.lower()))

    def test_every_mood_is_a_firmware_mood(self):
        for section in ("train", "test"):
            for mood in self.data[section]:
                self.assertIn(mood, expressions.ALL, f"{section}: {mood} is not a firmware mood")

    def test_train_and_test_cover_the_same_moods_with_enough_sentences(self):
        self.assertEqual(set(self.data["train"]), set(self.data["test"]))
        for mood, xs in self.data["train"].items():
            self.assertGreaterEqual(len(xs), 30, f"only {len(xs)} training sentences for {mood}")

    def test_no_test_sentence_is_trained_on(self):
        train = {self.norm(s) for xs in self.data["train"].values() for s in xs}
        for mood, xs in self.data["test"].items():
            for s in xs:
                self.assertNotIn(self.norm(s), train, f"test sentence leaks into train: {s!r}")

    def test_no_sentence_is_taught_as_two_moods(self):
        seen = {}
        for mood, xs in self.data["train"].items():
            for s in xs:
                other = seen.setdefault(self.norm(s), mood)
                self.assertEqual(other, mood, f"{s!r} is both {other} and {mood}")


if __name__ == "__main__":
    unittest.main()
