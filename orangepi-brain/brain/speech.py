"""
Robot Owl OrangePi Brain - Speech Recognition

The owl hears the user's voice and reacts: a short utterance is transcribed
(offline whisper.cpp on the OrangePi) and mapped to a reaction (eye expression
+ an owl-call on the MAX98357A amp). This is the OrangePi-side "behavior
pipeline" from the speech spec: cooldown -> (optional emotion) -> keyword
cluster -> action.

Design (see specs/014-speech.spec, and specs/015 for the OrangePi audio path):
  * ASR runs on the OrangePi -- the ESP32 has no mic / no ASR headroom.
  * Reactions drive the owl through the EXISTING temporary overrides
    (expression / gaze) + local amp audio. The ESP32 firmware and its own
    behavior state machine are NOT changed.
  * ASR is gated (awake + face + energy VAD) so the OrangePi is not
    transcribing 24/7 and the owl does not "hear" the TV or itself.

Two OrangePi-specific differences from the RPi brain:
  * The microphone is the ICS43434 I2S MEMS mic on the shared I2S card, which
    is fixed at 48 kHz and is STEREO (the mic is on one channel, the amp on
    the other). We therefore capture at 48 kHz / 2 ch, take the mic channel,
    and resample to 16 kHz (whisper's native rate) before the VAD + ASR.
  * The ASR engine is whisper.cpp (via pywhispercpp), not faster-whisper: a
    single self-contained C++ runtime with no torch / CTranslate2 dependency.
    The model is a local .bin/.gguf file downloaded by setup.sh (offline).
  * An OPTIONAL emotion layer (brain/emotion.py, llama.cpp) can tag the
    transcript with a sentence-level emotion that takes precedence over the
    keyword clusters. It is off by default; when on, an unmapped label simply
    falls through to the keyword clusters, so it can only add reactions.

Threading model
  A single daemon worker thread owns the whole audio path. It pulls mic chunks
  from a queue (filled by sounddevice's input callback), resamples them to the
  ASR rate, runs the energy VAD, and -- only when the gate is open --
  transcribes the accumulated window with whisper.cpp and hands the transcript
  (plus an optional emotion label) to feed(). All reactions (serial commands,
  amp audio) happen in this worker, never in the foreground serial read loop.

  whisper.cpp + PortAudio (+ llama.cpp if emotion is on) are imported lazily
  (inside start()) so that:
  * the brain still runs on a machine with no mic / no ASR engine installed,
  * the heavy ASR import stays out of the normal (speech-disabled) startup.
  With speech.enabled: false nothing here is imported or started.
"""

import logging
import math
import os
import queue
import random
import re
import threading
import time

from brain.serial_handler import SerialHandler
from brain.supervisor import Supervisor
from brain.emotion import EmotionClassifier

logger = logging.getLogger(__name__)

# States the owl must be in for speech to be allowed to react. (SLEEPING is
# deliberately excluded: a sleeping owl should not be woken by ambient words.)
# UPDATE is excluded so speech cannot interrupt firmware maintenance.
REACTIVE_STATES = {"idle", "detecting", "interacting"}

# Whisper writes non-speech sounds as a bracketed tag ("[Musik]", "* Musik *", "(Applaus)").
# That is the model saying "this was not speech": never a transcript to react to.
_NON_SPEECH_TAG = re.compile(r"[\[\(\*♪][^\]\)\*♪]{0,30}[\]\)\*♪]")


def _only_non_speech(transcript: str) -> bool:
    """True if nothing is left once the tags are removed (e.g. \"[Musik] [Musik]\")."""
    return not _NON_SPEECH_TAG.sub("", transcript).strip(" .,-")


class Speech:
    """Hears the user (I2S mic) and reacts through the owl's overrides.

    All reaction + ASR state lives on the instance (no module-level globals).
    The class is safe to construct even with no audio/mic: it simply logs and
    no-ops when audio is unavailable or when speech is disabled.
    """

    def __init__(self, serial: SerialHandler, supervisor: Supervisor, config: dict):
        self.serial = serial
        self.supervisor = supervisor

        cfg = (config or {}).get("speech", {})
        self.enabled = bool(cfg.get("enabled", False))
        self.language = cfg.get("language", "de")
        # whisper.cpp CPU threads. 2 was the original guess; the Zero 3W has 8 cores.
        self.threads = int(cfg.get("threads", 2))
        # whisper always encodes a 30 s window (1500 context frames, 50 per second) however short the
        # clip. audio_ctx cuts the window: our clips are <= window_s + one chunk (~3 s = 150 frames), so
        # 192 is ample. Measured on the Zero 3W (tiny, 2.5 s clip): 4.37 s -> 0.80 s at 2 threads. 0 = whisper's default.
        self.audio_ctx = int(cfg.get("audio_ctx", 0))
        # Peak-normalise each utterance before whisper. The ICS43434 arrives very quiet (speech peaks
        # of 0.002-0.005) and whisper-tiny does not cope: it hallucinates words. 0 = off, otherwise the
        # target peak (0.7), with the gain capped so a click cannot be blown up into a "word".
        self.normalize_peak = float(cfg.get("normalize_peak", 0.0))
        # Debug: write every utterance that reaches whisper (as 16-bit WAV, after normalisation) to
        # this directory, so recognition can be judged offline against other models. "" = off.
        self.dump_dir = str(cfg.get("dump_dir", "") or "")
        # whisper.cpp model: a LOCAL path to a .bin/.gguf (downloaded by
        # setup.sh), not a hub name -- the OrangePi runs offline.
        self.model = cfg.get("model", "") or ""
        self.mic_device = cfg.get("mic_device", "") or ""
        self.window_s = float(cfg.get("window_s", 2.5))
        self.chunk_s = float(cfg.get("chunk_s", 0.3))
        # The ASR/VAD rate. whisper.cpp expects 16 kHz; the mic is captured at
        # capture_rate and resampled down to this in the worker.
        self.sample_rate = int(cfg.get("sample_rate", 16000))
        # The I2S card's native rate (fixed by the owl_i2s kernel module) and
        # its channel count. The card is stereo: the mic is on mic_channel, the
        # amp occupies the other. We capture the whole card and pick the mic.
        self.capture_rate = int(cfg.get("capture_rate", 48000))
        self.capture_channels = int(cfg.get("capture_channels", 2))
        self.mic_channel = int(cfg.get("mic_channel", 0))
        # PortAudio sample format the card delivers. float32 is requested by
        # default (ALSA's plug layer converts the card's native int format);
        # set "int32" if a given card cannot deliver float.
        self.capture_dtype = cfg.get("capture_dtype", "float32") or "float32"
        self.vad_threshold = float(cfg.get("vad_threshold", 0.02))
        # Voiced audio shorter than this is never sent to whisper: music and room noise
        # produce 0.3-0.6 s blips at speech level, real sentences are >= 0.9 s. 0 = off.
        self.min_utterance_s = float(cfg.get("min_utterance_s", 0.0))
        self.energy_floor_ms = float(cfg.get("energy_floor_ms", 700))
        # Quiet audio kept around the voiced chunks: pre-roll before the first voiced chunk and
        # silent chunks inside/after the utterance (up to this many ms). 0 = old behaviour
        # (voiced chunks only, which chops soft word onsets and endings).
        self.context_ms = float(cfg.get("context_ms", 0))
        self.cooldown_s = float(cfg.get("cooldown_s", 4.5))
        self.require_face = bool(cfg.get("require_face", True))

        # cluster name -> list of keywords (lowercased German, per the spec)
        self.clusters = cfg.get("clusters", {}) or {}
        # cluster name -> {"expression": ..., "sound": ...}
        self.reactions = cfg.get("reactions", {}) or {}
        # Phase 4: words that wake the owl from SLEEPING (the firmware does not
        # wake on speech, so the OrangePi sends the existing "wake" command when
        # one of these is heard while the owl is asleep). Kept strict/short so
        # the owl isn't woken by the TV. Empty = speech never wakes it.
        self.wake_keywords = [str(k).lower() for k in (cfg.get("wake_keywords") or [])]

        # Navigation ("guide me home"): phrases that START guiding toward a
        # place (the words after the trigger are the place name, fuzzy-matched
        # against the saved locations) and phrases that STOP it. See
        # specs/013-navigation.spec. Longest trigger first so a short
        # trigger can't shadow a longer one.
        self.nav_triggers = [str(t).lower() for t in (cfg.get("nav_triggers") or [])]
        self.nav_stop_keywords = [str(k).lower() for k in (cfg.get("nav_stop_keywords") or [])]

        # Cooldown state (instance-level, per the spec's last_interaction_time)
        self._last_reaction = 0.0
        self.last_heard: str = ""
        # When the most recent transcript was captured (epoch seconds). Exposed
        # to the web UI so it can show "heard 4s ago"; 0.0 until the first
        # utterance is transcribed.
        self.last_heard_at: float = 0.0

        # Optional sentence-level emotion layer (llama.cpp). Always constructed
        # (so feed() can always ask it); it no-ops unless emotion.enabled.
        self.emotion = EmotionClassifier(config)

        # Worker-thread plumbing (populated in start()).
        self._queue: "queue.Queue" = queue.Queue(maxsize=64)
        self._worker = None
        self._stop = threading.Event()
        # VAD / utterance-buffer state (owned by the worker thread).
        self._utterance = []
        self._voice_started = 0.0
        self._silence_frames = 0
        self._utterance_generation = None
        # Lazy-loaded whisper.cpp model (loaded on the worker thread in start()).
        self._whisper = None

    # ==================================================================
    # Lifecycle (called from main.py, mirroring the web UI pattern)
    # ==================================================================
    def start(self) -> None:
        """Start the speech pipeline (mic capture + VAD + whisper.cpp worker).

        Raises if speech is enabled but the mic/whisper.cpp cannot be brought
        up; main.py catches that and continues without speech. Calling start()
        while already running is a no-op with a warning (a double-start would
        open a second mic and a second worker); main.py starts it exactly once.
        """
        if not self.enabled:
            logger.info("Speech recognition disabled (speech.enabled: false)")
            return
        if self._worker is not None:
            logger.warning("Speech.start() called while already running; ignoring")
            return

        logger.info(
            "Speech recognition starting (lang=%s, model=%s, mic=%s, "
            "capture=%d Hz/%dch, asr=%d Hz, window=%.1fs, cooldown=%.1fs, "
            "require_face=%s, emotion=%s)",
            self.language, self.model or "default", self.mic_device or "default",
            self.capture_rate, self.capture_channels, self.sample_rate,
            self.window_s, self.cooldown_s, self.require_face,
            "on" if self.emotion.enabled else "off",
        )

        # Lazy heavy imports: only pay for whisper.cpp + portaudio (+ llama.cpp
        # if the emotion layer is on) when the feature is actually enabled.
        import numpy as np  # noqa: F401  (ensures numpy is present early)
        import sounddevice as sd
        from pywhispercpp.model import Model

        # Load the whisper.cpp model once on the worker thread. It is a local
        # file (setup.sh downloads it), so there is no first-run hub fetch and
        # the OrangePi works fully offline.
        if not self.model:
            raise RuntimeError("speech is enabled but speech.model (whisper.cpp model path) is empty")
        try:
            self._whisper = Model(self.model, language=self.language, n_threads=self.threads,
                                  print_realtime=False, print_progress=False)
        except Exception as e:
            raise RuntimeError(f"Could not load whisper.cpp model {self.model!r}: {e}") from e

        # Optional emotion layer: load the LLM only when enabled.
        if self.emotion.enabled:
            self.emotion.start()

        # Open the mic. The I2S card is fixed at capture_rate and is stereo
        # (mic on mic_channel, amp on the other), so we capture the whole card
        # and select the mic channel in the worker. The input callback runs in
        # PortAudio's own thread and only enqueues chunks (non-blocking); the
        # worker thread does the heavy lifting. blocksize is in capture-rate
        # frames (the card's native rate, not the ASR rate).
        blocksize = max(1, int(self.chunk_s * self.capture_rate))
        try:
            self._stream = sd.InputStream(
                device=self.mic_device or None,
                channels=self.capture_channels,
                samplerate=self.capture_rate,
                dtype=self.capture_dtype,
                blocksize=blocksize,
                callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as e:
            self._whisper = None
            raise RuntimeError(f"Could not open microphone {self.mic_device or 'default'}: {e}") from e

        self._stop.clear()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name="robot-owl-speech")
        self._worker.start()
        logger.info("Speech recognition started (mic=%s, %d Hz capture -> %d Hz ASR)",
                     self.mic_device or "default", self.capture_rate, self.sample_rate)

    def stop(self) -> None:
        """Stop the worker, then the mic.

        We set the stop flag and then wait for the worker thread to actually
        exit. The worker may be blocked in queue.get(timeout=0.5), so it wakes
        at most ~0.5 s after the flag is set; waiting until it is no longer
        alive (or a generous timeout) guarantees the worker has stopped
        consuming the queue before a new instance is started. The thread is a
        daemon, so any straggler is reaped at process exit.
        """
        if not self.enabled:
            return
        if self._worker is not None:
            self._stop.set()
            deadline = time.time() + 5.0
            while self._worker.is_alive() and time.time() < deadline:
                time.sleep(0.02)
            if self._worker.is_alive():
                logger.warning("Speech worker did not exit cleanly; dropping (daemon thread)")
            self._worker = None
        stream = getattr(self, "_stream", None)
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            self._stream = None
        if self.emotion.enabled:
            self.emotion.stop()
        logger.info("Speech recognition stopped")

    # ==================================================================
    # Worker: mic callback -> resample -> VAD gate -> whisper.cpp -> feed()
    # ==================================================================
    def _audio_callback(self, indata, frames, time_info, status) -> None:
        """PortAudio input callback. Non-blocking: just enqueue the chunk."""
        if status:
            logger.debug("Mic status: %s", status)
        generation, safe_after = self.supervisor.audio_capture_guard()
        now = time.monotonic()
        age = frames / self.capture_rate
        if time_info is not None:
            adc_age = time_info.currentTime - time_info.inputBufferAdcTime
            if time_info.inputBufferAdcTime > 0 and math.isfinite(adc_age):
                age = max(age, adc_age)
        captured_at = now - age
        # Drop whole overlapping blocks, including delayed PortAudio delivery.
        # Do not close the mic or the silent TX stream to suppress feedback.
        if safe_after > 0 and captured_at < safe_after:
            return
        packet = (indata.copy(), generation, captured_at)
        try:
            self._queue.put_nowait(packet)
        except queue.Full:
            # Drop the oldest chunk if we're falling behind (ASR is slow).
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(packet)
            except queue.Empty:
                pass

    def _worker_loop(self) -> None:
        """Runs on the worker thread: pull chunks, select the mic channel,
        resample to the ASR rate, run the VAD, transcribe."""
        import numpy as np

        while not self._stop.is_set():
            try:
                chunk, generation, captured_at = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            except Exception:
                continue

            raw = np.asarray(chunk)
            # The card is stereo (mic + amp). Take the mic channel; if a mono
            # stream was delivered (e.g. a test stub) use it as-is.
            if raw.ndim == 2:
                mono = raw[:, self.mic_channel]
            else:
                mono = raw
            # Normalize to float32 in [-1, 1] (the card may deliver int32),
            # then resample from the card rate down to the ASR rate. The VAD
            # below is written for the ASR rate, so it sees 16 kHz mono.
            self._process_chunk(self._resample(self._to_float(mono),
                                                self.capture_rate, self.sample_rate),
                                captured_generation=generation, captured_at=captured_at)

    def _to_float(self, raw):
        """Normalize captured samples to float32 in [-1, 1].

        PortAudio is asked for float32 by default, in which case this is a
        no-op (the ALSA plug layer already converted the card's native int
        format). If the card could only deliver a signed int format, scale by
        its full-scale value. Dividing a 24-bit mic signal that is
        left-justified in a 32-bit slot by 2^31 still lands in [-1, 1)
        correctly (the low 8 bits are zero), so this is robust to either
        justification.
        """
        import numpy as np
        if raw.dtype.kind == "f":
            return raw.astype(np.float32, copy=False)
        if raw.dtype.kind in ("i", "u"):
            scale = float(2 ** (raw.dtype.itemsize * 8 - 1))
            return (raw / scale).astype(np.float32)
        return raw.astype(np.float32)

    def _resample(self, x, src_rate: int, dst_rate: int):
        """Linear-interpolation resample of a float32 mono signal."""
        import numpy as np
        if src_rate == dst_rate or x.size == 0:
            return x.astype(np.float32, copy=False)
        ratio = src_rate / dst_rate
        n_out = int(x.size / ratio)
        if n_out == 0:
            return np.zeros(0, dtype=np.float32)
        pos = np.arange(n_out, dtype=np.float64) * ratio
        i0 = np.floor(pos).astype(np.int64)
        i1 = np.minimum(i0 + 1, x.size - 1)
        frac = (pos - i0).astype(np.float32)
        return (x[i0] * (1.0 - frac) + x[i1] * frac).astype(np.float32)

    def _reset_utterance(self):
        self._utterance = []
        self._voice_started = 0.0
        self._silence_frames = 0
        self._utterance_generation = None
        self._utterance_peak = 0.0
        self._preroll = []
        self._pending_quiet = []
        self._voiced_frames = 0

    def _process_chunk(self, chunk, captured_generation=None, captured_at=None) -> None:
        """VAD state machine over one (ASR-rate, mono) chunk; transcribe a
        closed utterance.

        Gate: the owl must be awake and (if require_face) a face must be in
        frame. While open, voiced chunks accumulate into an utterance window;
        sustained silence (or the window filling) closes it and we transcribe.
        Everything runs on the worker thread.

        NOTE on timing: silence is measured in *audio duration* (frames /
        sample_rate), not wall-clock time. The worker may process chunks faster
        or slower than real time (e.g. a burst, or a slow CPU), so wall-clock
        timing would mis-measure the silence gap. Audio duration is independent
        of processing speed, which keeps the VAD correct and testable.
        """
        generation, safe_after = self.supervisor.audio_capture_guard()
        if (time.monotonic() < safe_after
                or (captured_generation is not None and captured_generation != generation)
                or (captured_at is not None and safe_after > 0 and captured_at < safe_after)):
            self._reset_utterance()
            return
        if self._utterance_generation not in (None, generation):
            self._reset_utterance()
        # --- Gate: is the owl in a state where it may react? ---
        if not self._gate_open():
            self._reset_utterance()
            return

        energy = self._rms(chunk)
        voiced = energy >= self.vad_threshold
        chunk_frames = len(chunk)

        if voiced:
            self._utterance_peak = max(getattr(self, "_utterance_peak", 0.0), energy)
            # A voiced chunk resets the trailing-silence counter.
            self._silence_frames = 0
            if not self._utterance:
                self._voice_started = time.time()
                self._utterance_generation = generation
                self._utterance.extend(getattr(self, "_preroll", []))
                self._preroll = []
            self._utterance.extend(getattr(self, "_pending_quiet", []))
            self._pending_quiet = []
            self._utterance.append(chunk)
            self._voiced_frames = getattr(self, "_voiced_frames", 0) + chunk_frames

            total = sum(len(c) for c in self._utterance)
            max_frames = int(self.window_s * self.sample_rate)
            if total >= max_frames:
                self._transcribe_and_feed()
            # else: keep accumulating until silence or the window fills
        else:
            keep = int(self.context_ms * self.sample_rate / 1000.0)
            if keep > 0:
                if self._utterance:
                    # Quiet audio after voice: joins the utterance when voice resumes (a gap
                    # between words) or, at the close, only its first context_ms (the tail).
                    self._pending_quiet = getattr(self, "_pending_quiet", []) + [chunk]
                else:
                    pr = getattr(self, "_preroll", [])
                    pr.append(chunk)
                    while len(pr) > 1 and sum(len(c) for c in pr) > keep:
                        pr.pop(0)
                    self._preroll = pr
            if self._utterance:
                self._silence_frames += chunk_frames
                if self._silence_frames >= self.energy_floor_ms * self.sample_rate / 1000.0:
                    # Sustained silence (in audio time): the utterance is over.
                    self._transcribe_and_feed()
            # else: still in silence, nothing to do

    def _gate_open(self) -> bool:
        """True if the owl is in a reactive state and (optionally) a face is
        in frame. Cheap: reads the supervisor's cached last telemetry."""
        last = self.supervisor.last
        if last is None or last.state not in REACTIVE_STATES:
            return False
        if self.require_face and not last.face.detected:
            return False
        return True

    @staticmethod
    def _rms(chunk) -> float:
        """RMS energy of a float32 chunk (normalized to [-1, 1])."""
        if chunk.size == 0:
            return 0.0
        return float(math.sqrt((chunk ** 2).mean()))

    def _dump_wav(self, audio) -> None:
        """Debug aid (speech.dump_dir): save the utterance as a mono 16-bit WAV. Never raises."""
        try:
            import wave
            import numpy as np
            os.makedirs(self.dump_dir, exist_ok=True)
            path = os.path.join(self.dump_dir, time.strftime("utt-%H%M%S.wav"))
            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(self.sample_rate)
                w.writeframes(pcm.tobytes())
        except Exception as e:
            logger.debug("Speech: could not dump utterance: %s", e)

    def _transcribe_and_feed(self) -> None:
        """Transcribe the accumulated window (if worth it) and run the pipeline.

        pywhispercpp.Model.transcribe() returns a list of Segment objects.
        Join their text before passing the transcript to the behavior pipeline.
        If the optional emotion layer is on, we also tag the transcript with a
        sentence-level emotion and hand both to feed().
        """
        import numpy as np

        if not self._utterance:
            return
        generation, safe_after = self.supervisor.audio_capture_guard()
        captured_generation = self._utterance_generation
        tail, keep = [], int(self.context_ms * self.sample_rate / 1000.0)
        for c in getattr(self, "_pending_quiet", []):
            if keep <= 0:
                break
            tail.append(c[:keep])
            keep -= len(tail[-1])
        utterance = np.concatenate(self._utterance + tail)
        voiced_frames = getattr(self, "_voiced_frames", 0) or utterance.size
        peak = getattr(self, "_utterance_peak", 0.0)
        self._reset_utterance()
        if (time.monotonic() < safe_after
                or captured_generation not in (None, generation)):
            return

        # Ignore fragments shorter than a tenth of a second (noise clicks).
        if utterance.size < int(0.1 * self.sample_rate):
            return
        if voiced_frames < int(self.min_utterance_s * self.sample_rate):
            logger.debug("Speech: %.1fs of voiced audio is below min_utterance_s, not transcribed",
                         voiced_frames / self.sample_rate)
            return
        if self._whisper is None:
            return

        if self.normalize_peak > 0:
            top = float(np.max(np.abs(utterance)))
            if top > 1e-6:
                utterance = (utterance * min(self.normalize_peak / top, 100.0)).astype(np.float32)
        if self.dump_dir:
            self._dump_wav(utterance)
        try:
            t0 = time.monotonic()
            kwargs = {"language": self.language}
            if self.audio_ctx:
                kwargs["audio_ctx"] = self.audio_ctx
            result = self._whisper.transcribe(utterance, **kwargs)
            transcript = " ".join(seg.text.strip() for seg in result).strip()
            asr_s = time.monotonic() - t0
        except Exception as e:
            logger.warning("Speech: transcription failed: %s", e)
            return

        # Peak chunk RMS per utterance: the number to set vad_threshold from (music vs speech).
        logger.info("Speech: utterance %.1fs peak_rms=%.4f asr=%.2fs (%d threads, audio_ctx %d) -> %r",
                    utterance.size / self.sample_rate, peak, asr_s, self.threads, self.audio_ctx, transcript)
        if not transcript or _only_non_speech(transcript):
            return
        # Optional sentence-level emotion tag (llama.cpp). Empty when the layer
        # is off or has no opinion; feed() then just falls through to keywords.
        emotion = self.emotion.classify(transcript) if self.emotion.enabled else ""
        latest_generation, safe_after = self.supervisor.audio_capture_guard()
        if latest_generation != generation or time.monotonic() < safe_after:
            logger.debug("Speech: discarding ASR result invalidated by playback")
            return
        logger.debug("Speech: heard %r", transcript)
        self.feed(transcript, emotion=emotion)

    # ==================================================================
    # Behavior pipeline (Phase 1). Called by the worker with a transcript.
    # ==================================================================
    def feed(self, transcript: str, emotion: str = "") -> None:
        """Run the behavior pipeline on a transcript.

        Mirrors the spec's handle_behavior_pipeline(), plus the Phase-4
        wake-on-speech exception, the navigation override, and the OrangePi
        optional emotion layer:
          0. (Phase 4) the user is an interaction trigger -> reset the
              auto-sleep inactivity timer; if the owl is asleep, a wake keyword
              wakes it, a navigation STOP keyword ends an in-progress navigation
              (so "stop" always works, even from the sofa while the owl sleeps),
              and any other speech is ignored
          1. face-gate (if require_face). A navigation STOP keyword is EXEMPT:
              "stop it" must end navigation even if the face momentarily left
              frame. (The STOP is acted on after the face-gate, before the
              cooldown, so it is never suppressed by either.)
          2. navigation STOP keyword -> end navigation (EXEMPT from the
              cooldown: it must work even right after the owl reacted to the
              phrase that started it)
          3. enforce the cooldown
          4. navigation START trigger -> start guiding toward the named place
              (checked BEFORE the emotion layer and the keyword clusters, so a
              nav sentence is not stolen by a single question word, e.g.
              "wie komm ich zu hotel")
          4.5. (OrangePi, optional) a tagged sentence-level emotion -> set the
              matching expression (+ optional owl-call) and return. An empty
              tag or a label not in the mapping falls through to the keyword
              clusters, so enabling emotion can only ADD reactions.
          5. keyword clusters (first match wins, in cluster definition order)
          6) else stochastic ambient fallback (gaze at user / idle)
        """
        if not self.enabled:
            return
        transcript = (transcript or "").strip()
        if not transcript:
            return
        emotion = (emotion or "").strip()

        self.last_heard = transcript
        now = time.time()
        self.last_heard_at = now
        normalized = transcript.lower()

        # 0) Phase 4: hearing the user is an interaction trigger.
        self.supervisor.register_activity(now)
        if self.supervisor.current_state() == "sleeping":
            # The normal pipeline below is gated off while asleep. The
            # exceptions are a clearly addressed wake keyword (wake the owl)
            # and a navigation STOP keyword (end an in-progress navigation so
            # "stop" always works, even from the sofa while the owl sleeps).
            # Any other speech is ignored so the sleeping owl is not startled
            # by the TV / ambient words.
            if any(self._keyword_hit(normalized, kw) for kw in self.wake_keywords):
                logger.info("Speech: wake keyword %r heard while asleep -> waking", transcript)
                self.serial.wake()
                self.supervisor.last_state = "idle"  # optimistic; telemetry confirms
                return
            if self._nav_stop_hit(normalized):
                logger.info("Speech: nav stop %r while asleep -> ending navigation", transcript)
                self.nav_stop(transcript)
                return
            logger.debug("Speech: %r heard while asleep (not wake/stop) -> ignored", transcript)
            return

        # 1) Face-gate: only react while a face is in frame (prevents the owl
        #    reacting to ambient TV / the user's own amp). A navigation STOP
        #    keyword is exempt (handled in step 2, before the gate applies).
        is_stop = self._nav_stop_hit(normalized)
        if self.require_face and not is_stop:
            last = self.supervisor.last
            if last is None or not last.face.detected:
                logger.debug("Speech: no face in frame, ignoring %r", transcript)
                return

        # 2) Navigation STOP keyword (exempt from the cooldown and face-gate:
        #    "stop it" must always work).
        if is_stop:
            self.nav_stop(transcript)
            return

        # 3) Cooldown: ignore reactions closer than cooldown_s apart.
        if now - self._last_reaction < self.cooldown_s:
            logger.debug("Speech: in cooldown, ignoring %r", transcript)
            return

        # 4) Navigation START trigger (checked before the emotion layer and the
        #    keyword clusters so a nav sentence isn't claimed by a single word).
        if self.nav_triggers and self._nav_trigger_hit(normalized):
            self._react_navigate(transcript)
            return

        # 4.5) Optional sentence-level emotion (llama.cpp). Only honoured when
        #      the layer is enabled AND the tag maps to an expression; then it
        #      takes precedence over the keyword clusters. An empty tag (no
        #      opinion), a disabled layer, or an unmapped label falls through
        #      to the keyword clusters below.
        if emotion and self.emotion.enabled:
            expression = self.emotion.labels.get(emotion)
            if expression:
                sound = self.emotion.sounds.get(emotion)
                logger.info("Speech: emotion %r in %r -> %s", emotion, transcript, expression)
                self.serial.set_expression(expression)
                if sound:
                    self.supervisor.play_sound(sound)
                self._last_reaction = now
                return
            # else: unmapped label -> fall through to the keyword clusters

        # 5) Keyword clusters (first match wins, in cluster definition order).
        for cluster, keywords in self.clusters.items():
            if any(self._keyword_hit(normalized, kw) for kw in keywords):
                self._react(cluster, transcript)
                return

        # 6) No keyword matched: stochastic ambient fallback (spec's else-branch).
        #    Default 0.8 act / 0.2 idle -> we act (gaze at user) most of the time.
        if random.random() < 0.8:
            logger.info("Speech: ambient %r -> gaze at user", transcript)
            self.serial.set_gaze(0.0, 0.0)
            self._last_reaction = now
        # else: remain idle (organic resting behavior)

    # ------------------------------------------------------------------
    # Keyword matching
    # ------------------------------------------------------------------
    @staticmethod
    def _keyword_hit(text: str, keyword: str) -> bool:
        """True if `keyword` occurs in `text` as a whole word / phrase.

        The spec sketch used a plain substring test (`keyword in text`), which
        over-matches on German: the question word "wie" would fire inside
        "völlig", and "was" inside "irgendwas". We instead anchor on word
        boundaries (letters/digits on either side), so single words match whole
        words while multi-word phrases ("gute eule") still match as substrings.
        """
        if " " in keyword:
            return keyword in text
        pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None

    # ------------------------------------------------------------------
    # Reactions
    # ------------------------------------------------------------------
    def _react(self, cluster: str, transcript: str) -> None:
        """Apply a cluster's reaction: expression override + (optional) sound."""
        reaction = self.reactions.get(cluster)
        if not reaction:
            logger.warning("Speech: cluster %r matched %r but has no reaction",
                           cluster, transcript)
            return

        expression = reaction.get("expression")
        sound = reaction.get("sound")
        gesture = reaction.get("gesture")

        # Default: the gesture goes out last and its own eyes win. With `own_eyes: true` the
        # reaction's expression goes out last instead, so it replaces the gesture's eyes.
        expression_last = bool(reaction.get("own_eyes")) and bool(gesture)

        def send_expression():
            if expression and not self.serial.set_expression(expression):
                logger.warning("Speech: failed to send expression %r", expression)

        if not expression_last:
            send_expression()
        if gesture:
            # Body language (ears, wings, head) plus matching eyes from the firmware; the
            # firmware drops it while asleep.
            if not self.serial.react(gesture):
                logger.warning("Speech: failed to send gesture %r", gesture)
        if expression_last:
            send_expression()
        if sound:
            # The supervisor owns the amp; it no-ops cleanly when the I2S amp
            # is absent, so there is nothing to check here.
            self.supervisor.play_sound(sound)

        self._last_reaction = time.time()
        logger.info(
            "Heard %r -> %s (expression=%s, sound=%s, gesture=%s)",
            transcript, cluster, expression or "-", sound or "-", gesture or "-",
        )

    # ------------------------------------------------------------------
    # Navigation ("guide me home")
    # ------------------------------------------------------------------
    def _nav_stop_hit(self, normalized: str) -> bool:
        """True if the transcript is a navigation STOP keyword (see §13)."""
        return any(self._keyword_hit(normalized, kw) for kw in self.nav_stop_keywords)

    def _nav_trigger_hit(self, normalized: str) -> bool:
        """True if the transcript contains a navigation START trigger.

        A trigger is a multi-word phrase, so this is a plain substring test
        (see `_keyword_hit`). Checked before the keyword clusters in feed().
        """
        return any(t in normalized for t in self.nav_triggers)

    def nav_stop(self, transcript: str = "") -> None:
        """End the current navigation (idempotent; no-op if not navigating)."""
        if not self.supervisor.navigation or not self.supervisor.navigation.is_active():
            return
        self.supervisor.nav_stop("speech")
        self._last_reaction = time.time()
        if transcript:
            logger.info("Heard %r -> stop navigation", transcript)

    def _react_navigate(self, transcript: str) -> None:
        """Match a nav trigger, extract the place name, fuzzy-match it, start.

        The trigger phrases are lowercased (config), so we search the normalized
        (lowercased) transcript and slice the raw transcript at the same index
        (the index is case-independent). The extracted name is normalized again
        in _match_place, so the raw capitalization of the spoken name is fine.
        """
        normalized = transcript.lower()
        for trigger in sorted(self.nav_triggers, key=len, reverse=True):
            idx = normalized.find(trigger)
            if idx == -1:
                continue
            # The words after the trigger are the (fuzzy) place name.
            raw_name = transcript[idx + len(trigger):].strip(" .,!?")
            name = self._match_place(raw_name)
            if not name:
                logger.info("Speech: nav trigger %r but no place matches %r", trigger, raw_name)
                self.supervisor.play_sound("alert")
                self._last_reaction = time.time()
                return
            self.supervisor.nav_start(name)
            self._last_reaction = time.time()
            return

    def _match_place(self, raw_name: str):
        """Fuzzy-match a heard place name against the saved places.

        The matching itself belongs to the store (it owns the names and the
        normalization), so this is just the None-guard for a supervisor without
        one. It used to be ~45 lines of matching plus a Levenshtein
        implementation here, which kept it out of reach of the web UI.
        """
        store = getattr(self.supervisor, "locations", None)
        if store is None:
            return None
        return store.match(raw_name)
