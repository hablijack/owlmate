"""
Robot Owl OrangePi Brain - Emotion Classification (optional)

An OPTIONAL layer on top of the speech pipeline. Where the RPi brain mapped a
transcript to a reaction with a fixed table of German keyword clusters, the
OrangePi brain can instead hand the transcript to a small language model
(ringorsolya/Emotion_RoBERTa_german6_v7, run through llama.cpp) and let it name
the speaker's emotion. The label is then mapped to an owl eye expression (and
optionally an owl-call) via a config table.

Why an LLM and not just keywords:
  * Keyword clusters are brittle -- "das ist toll" is happy, but "das ist
    voll toll, nervt mich" is not, and no keyword list captures that. A
    sentence-level model reads the whole utterance.
  * It is a strict SUPERSET: when the model is disabled (the default) or
    returns a label we do not map, the keyword clusters still run as the
    fallback. So enabling emotion can only add reactions, never remove the
    ones that already worked.

Design (mirrors brain/speech.py's lazy-import discipline):
  * llama_cpp is imported ONLY inside start(), and only when
    emotion.enabled is true. With the default (disabled) nothing here is
    imported, so the brain still runs on a machine with no LLM runtime.
  * classify() is a pure, cheap-on-the-happy-path function: it returns ""
    (no opinion) whenever the layer is off, the model failed to load, or the
    transcript is empty. speech.py treats "" as "fall through to keywords".
  * The model is a GGUF file downloaded by setup.sh (not pulled from a hub at
    runtime), so the OrangePi works offline.
"""

import logging

logger = logging.getLogger(__name__)


class EmotionClassifier:
    """Optional sentence-level emotion tagger (llama.cpp + a GGUF model).

    Safe to construct with no model / no llama_cpp: it no-ops (classify()
    returns "") until start() has loaded a model, and stays a no-op when the
    feature is disabled.
    """

    def __init__(self, config: dict):
        cfg = (config or {}).get("emotion", {})
        self.enabled = bool(cfg.get("enabled", False))
        # GGUF path (downloaded by setup.sh); "" = feature cannot be enabled.
        self.model_path = cfg.get("model_path", "") or ""
        # The prompt the model was trained/expected to complete. The RoBERTa
        # emotion models are completion-style: feed the utterance, it emits a
        # single label token. Kept in config so a different model's prompt
        # format can be dropped in without a code change.
        self.prompt_template = cfg.get("prompt_template", "Klassifiziere die Emotion: {text}")
        self.max_tokens = int(cfg.get("max_tokens", 8))
        # model output label -> owl eye expression. Labels NOT in this map are
        # ignored (the keyword clusters remain the fallback), so a typo or a
        # model drift degrades to "no emotion reaction" rather than a crash.
        self.labels = cfg.get("labels", {}) or {}
        # optional: model output label -> owl-call played alongside the expression
        self.sounds = cfg.get("sounds", {}) or {}

        self._llama = None
        self._warned = False

    # ------------------------------------------------------------------
    # Lifecycle (called from speech.start())
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Load the GGUF model. No-op (with a log) when disabled or unconfigured."""
        if not self.enabled:
            logger.info("Emotion classification disabled (emotion.enabled: false)")
            return
        if not self.model_path:
            raise RuntimeError("emotion.enabled is true but emotion.model_path is empty")
        # Lazy heavy import: only pay for llama.cpp when the feature is on.
        import llama_cpp
        try:
            self._llama = llama_cpp.Llama(model_path=self.model_path, n_ctx=512)
        except Exception as e:
            self._llama = None
            raise RuntimeError(f"Could not load emotion model {self.model_path!r}: {e}") from e
        logger.info("Emotion classification enabled (model=%s, %d known label(s))",
                     self.model_path, len(self.labels))

    def stop(self) -> None:
        self._llama = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def classify(self, text: str) -> str:
        """Return the model's emotion label for `text`, or "" for no opinion.

        "" means: feature off, model not loaded, empty input, or the model
        produced nothing usable. speech.feed() treats "" as 'use the keyword
        clusters'. A non-empty label that is not in self.labels is also
        effectively a no-op (feed() falls through to keywords), but we return
        it so the log line can show what the model actually said.
        """
        if not self.enabled or self._llama is None:
            return ""
        text = (text or "").strip()
        if not text:
            return ""
        try:
            prompt = self.prompt_template.format(text=text)
            out = self._llama.create_completion(
                prompt, max_tokens=self.max_tokens, temperature=0.0,
            )
            label = out["choices"][0]["text"].strip()
        except Exception as e:
            if not self._warned:
                logger.warning("Emotion: classify failed (%s) - falling back to keywords", e)
                self._warned = True
            return ""
        return label
