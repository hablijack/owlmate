"""
Robot Owl OrangePi Brain - Sentence -> mood classifier

Maps what the user says to one of the owl's moods: "Heute ist wirklich ein Sauwetter"
-> sad_down, "Ich habe gerade im Lotto gewonnen" -> glee, "Ich habe mir den Fuss
angestossen" -> scared. Keyword lists cannot do this -- none of those sentences names
its feeling -- so it is a small BERT (multilingual MiniLM, ~118 M parameters) fine-tuned
on our own moods.

  * Trained OFF the Pi by tools/train_mood.py from assets/mood/sentences.yaml; the Pi
    only runs the exported int8 ONNX model (model.onnx + tokenizer.json + labels.json in
    mood.model_dir). No torch on the Pi. ~10 ms per sentence on the Zero 3W.
  * onnxruntime + tokenizers are imported lazily in start(), so the brain still runs on a
    machine without them while the feature is off.
  * classify() never raises and returns ("", 0.0) when the layer is off or unloaded; a
    confident "neutral" or a low confidence is the caller's cue to only look at the user.

Why this model and not another is measured in specs/014 (Falsified + the comparison).
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


class MoodClassifier:
    """Sentence -> (mood, probability). Safe to construct with nothing installed."""

    def __init__(self, config: dict):
        cfg = (config or {}).get("mood", {}) or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.model_dir = cfg.get("model_dir", "") or ""
        # Below this probability the owl does not commit to a mood (it just looks at you):
        # a wrong feeling is worse than none.
        self.min_confidence = float(cfg.get("min_confidence", 0.5))
        self.max_tokens = int(cfg.get("max_tokens", 48))
        self._session = None
        self._tok = None
        self._labels = []

    def start(self) -> None:
        if not self.enabled:
            logger.info("Mood classifier disabled (mood.enabled: false)")
            return
        if not self.model_dir:
            raise RuntimeError("mood.enabled is true but mood.model_dir is empty")
        import onnxruntime as ort
        from tokenizers import Tokenizer
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2   # 1/2/4 threads measured the same (~10 ms); keep cores free
        self._session = ort.InferenceSession(os.path.join(self.model_dir, "model.onnx"), opts,
                                             providers=["CPUExecutionProvider"])
        self._tok = Tokenizer.from_file(os.path.join(self.model_dir, "tokenizer.json"))
        self._tok.enable_truncation(self.max_tokens)
        with open(os.path.join(self.model_dir, "labels.json"), encoding="utf-8") as f:
            self._labels = json.load(f)
        logger.info("Mood classifier loaded (%d moods) from %s", len(self._labels), self.model_dir)

    def classify(self, text: str):
        """Return (mood, probability), or ("", 0.0) if the layer cannot answer."""
        if self._session is None or not (text or "").strip():
            return "", 0.0
        try:
            import numpy as np
            enc = self._tok.encode(text.lower())
            logits = self._session.run(None, {
                "input_ids": np.array([enc.ids], dtype=np.int64),
                "attention_mask": np.array([enc.attention_mask], dtype=np.int64),
            })[0][0]
            p = np.exp(logits - logits.max())
            p /= p.sum()
            i = int(p.argmax())
            return self._labels[i], float(p[i])
        except Exception as e:
            logger.warning("Mood classifier failed on %r: %s", text, e)
            return "", 0.0
