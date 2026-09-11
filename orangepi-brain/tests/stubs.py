"""
Shared test helpers for the Robot Owl OrangePi brain.

These tests run on a plain dev machine (e.g. a Mac) WITHOUT the OrangePi,
a microphone, PortAudio, whisper.cpp or llama.cpp installed. They do that by:

  * stubbing the third-party modules the brain imports (serial, flask, yaml at
    import time; whispercpp and llama_cpp lazily, when their feature starts)
    with minimal fakes, and
  * stubbing the *audio* module (sounddevice) for the Phase-2 ASR tests,
    feeding the Speech worker a stream of synthetic 48 kHz STEREO chunks (the
    I2S card's native format: mic on one channel, amp on the other) and a
    mocked whisper.cpp model that returns a canned transcript.

So the whole Phase-1 pipeline AND the Phase-2 capture->VAD->ASR->react loop
are exercised without any real hardware.

The whispercpp / llama_cpp stubs installed here are SAFE DEFAULTS: their
models return an empty transcript / empty label, so a test that forgets to
override them gets a no-op (no reaction) rather than a crash. The ASR test
overrides the whisper.cpp stub with a transcript-specific one, and the emotion
test overrides the llama.cpp stub with a label-specific one.
"""

import sys
import types


def install_stub_modules() -> None:
    """Install minimal stand-ins for third-party modules into sys.modules.

    Only replaces a module if it is not already importable, so on a machine
    that *does* have pyserial/flask/yaml the real ones are used instead.
    """
    def _has(name):
        try:
            import importlib
            importlib.import_module(name)
            return True
        except Exception:
            return False

    # pyserial: brain.serial_handler imports it.
    if not _has("serial"):
        m = types.ModuleType("serial")

        class _Serial:
            is_open = False

        m.Serial = _Serial
        m.SerialException = Exception
        sys.modules["serial"] = m

    # flask: brain.web_ui imports it (only loaded when the web UI is used).
    # The fake Flask is just enough to construct the app and reach the route
    # view functions (app.view_functions["..."]) -- WebUI._build_app registers
    # each route with @app.route, which this minimal fake records. jsonify is
    # a plain dict, render_template_string returns the raw template, and
    # request.get_json returns {} (routes read JSON bodies, which tests don't
    # exercise). This lets the Phase-3 web-UI tests run without real Flask.
    if not _has("flask"):
        m = types.ModuleType("flask")

        class _FakeFlask:
            def __init__(self, name):
                self.name = name
                self.view_functions = {}

            def route(self, rule, methods=None):
                # Real Flask keys view_functions by the view's function name
                # (not the URL rule), so tests look up e.g. "api_telemetry".
                def decorator(fn):
                    self.view_functions[fn.__name__] = fn
                    return fn
                return decorator

            def run(self, *a, **k):
                pass  # never called in tests

        m.Flask = _FakeFlask
        m.jsonify = lambda *a, **k: (a[0] if a else k)
        m.render_template_string = lambda *a, **k: (a[0] if a else "")
        m.request = types.SimpleNamespace(get_json=lambda **k: {})
        sys.modules["flask"] = m

    # yaml: main.py + config loading import it.
    if not _has("yaml"):
        m = types.ModuleType("yaml")
        m.safe_load = lambda f: {}
        sys.modules["yaml"] = m

    # whispercpp: brain/speech.py imports it lazily inside start() (ASR). The
    # default stub returns an EMPTY transcript, so a test that forgets to
    # override it gets a no-op (feed("") returns immediately) rather than a
    # crash. The ASR test installs its own transcript-specific stub on top.
    if not _has("whispercpp"):
        m = types.ModuleType("whispercpp")

        class _WhisperModel:
            def __init__(self, model_path=None, language=None, **kw):
                self.model_path = model_path
                self.language = language
                self.transcribed = []

            def transcribe(self, audio, language=None, **kw):
                self.transcribed.append(audio)
                # Default: no speech recognised (empty text -> feed() no-ops).
                return types.SimpleNamespace(text="", segments=[])

        m.WhisperModel = _WhisperModel
        sys.modules["whispercpp"] = m

    # llama_cpp: brain/emotion.py imports it lazily inside start() (only when
    # emotion.enabled). The default stub returns an EMPTY label, so classify()
    # reports "no opinion" and feed() falls through to the keyword clusters --
    # the safe no-op. The emotion test installs its own label-specific stub.
    if not _has("llama_cpp"):
        m = types.ModuleType("llama_cpp")

        class _Llama:
            def __init__(self, model_path=None, n_ctx=512, **kw):
                self.model_path = model_path
                self.n_ctx = n_ctx
                self.prompts = []

            def create_completion(self, prompt, max_tokens=8, temperature=0.0,
                                  **kw):
                self.prompts.append(prompt)
                # Default: no opinion (empty text -> classify() returns "").
                return {"choices": [{"text": ""}]}

        m.Llama = _Llama
        sys.modules["llama_cpp"] = m


class FakeAudio:
    """Stand-in for brain.audio.Audio: records play() calls."""
    def __init__(self):
        self.played = []
        self.enabled = True
        self._ready = True

    def play(self, sfx):
        self.played.append(sfx)
        return True


class FakeSerial:
    """Stand-in for SerialHandler: records commands, no real port."""
    def __init__(self):
        self.commands = []

    def set_expression(self, expression):
        self.commands.append(("expression", expression))
        return True

    def set_gaze(self, x, y):
        self.commands.append(("gaze", (x, y)))
        return True

    def nav(self, angle, active=True):
        self.commands.append(("nav", (angle, active)))
        return True

    def sleep(self):
        self.commands.append(("sleep", {"type": "sleep"}))
        return True

    def wake(self):
        self.commands.append(("wake", {"type": "wake"}))
        return True

    def send_command(self, command):
        self.commands.append((command.get("type"), command))
        return True


def FakeTelemetry(state="interacting", face_detected=True, confidence=0.9,
                  vibration_detected=False,
                  gps_valid=True, lat=48.0, lon=11.0,
                  imu_yaw=0.0, imu_calibrated=True):
    """A REAL Telemetry frame, built from the real dataclasses.

    This used to be a hand-written class of SimpleNamespaces, which made it a
    THIRD mirror of the wire format -- and it drifted exactly as you would
    expect: when imu.cal / vibration.pulses / face.total were finally parsed on
    2026-08-27, every consumer of this stub broke, because the fake lacked
    fields the real frame has.

    Building the real dataclass instead means it cannot drift again: a field
    added to Telemetry appears here for free, with the real defaults.

    Imported lazily so install_stub_modules() has already run (brain.*
    imports pyserial at module scope).
    """
    from brain.serial_handler import (Telemetry, FaceDetection, IMUData,
                                      IMUCalibration, GPSData, VibrationData,
                                      UpdateMode, NavigationState, NUM_SERVOS)
    # A calibrated IMU implies a usable heading and a completed turn. A level
    # full turn covers 2 of 3 magnetometer axes, which is the real-world value.
    cal = (IMUCalibration(axes=2, heading_ok=True) if imu_calibrated
           else IMUCalibration())
    return Telemetry(
        timestamp=0.0,
        state=state,
        uptime_ms=1000,
        firmware="test",
        eye_expression="neutral",
        face=FaceDetection(detected=face_detected, confidence=confidence),
        vibration=VibrationData(detected=vibration_detected,
                                count=1 if vibration_detected else 0),
        gps=GPSData(valid=gps_valid, latitude=lat, longitude=lon,
                    satellites=8 if gps_valid else 0),
        imu=IMUData(yaw=imu_yaw, calibrated=imu_calibrated, cal=cal),
        update=UpdateMode(active=False),
        navigation=NavigationState(),
        servos=[0.0] * NUM_SERVOS,
    )


# Sentinel for FakeSupervisor(last=...): tells "caller said nothing, build a
# default frame" apart from an explicit last=None ("no telemetry yet").
_UNSET = object()


class FakeSupervisor:
    """Stand-in for Supervisor, for every test that needs one.

    Mirrors the real `Supervisor` surface the other modules actually call:
    `.last` / `.last_state` / `.serial` / `.audio` / `.locations` /
    `.navigation`, the sound cue (`play_sound`), the state query
    (`current_state`), the auto-sleep policy (`register_activity` /
    `check_auto_sleep`), the sleep/wake commands and the nav delegation
    (`nav_start` / `nav_stop`).

    There were three of these until 2026-08-31 -- this one plus a local
    `StubSupervisor` in test_navigation.py and another in
    test_navigation_webui.py -- so every method added to Supervisor had to be
    mirrored in three files, and each copy had quietly drifted (only one
    guarded `navigation is None`, and the two `sleep()` copies logged to
    different places). One double, one place to update. See SPEC-012 R-012.2.

    `last` defaults to a frame built from `state`/`face_detected`, which is
    what the speech tests need -- the face gate reads `supervisor.last.face`.
    Pass `last=None` for "no telemetry has arrived yet", which is what the real
    Supervisor starts with and what the navigation tests were written against.

    Be deliberate about which you ask for: FakeTelemetry's defaults are a VALID
    fix with a CALIBRATED heading, so the default frame is one staleness check
    away from making the controller aim the moment it is started. (Checked
    2026-08-31: today it does NOT, because `timestamp=0.0` is stale against
    wall-clock -- the suite passes either way. That is luck, not a guarantee.)
    """

    def __init__(self, serial=None, state="interacting", face_detected=True,
                 audio=None, auto_sleep_enabled=False, after_s=60.0,
                 last=_UNSET):
        self.serial = serial
        self.last = (FakeTelemetry(state=state, face_detected=face_detected)
                     if last is _UNSET else last)
        self.last_state = state
        # Commands this fake supervisor was asked to send. (These used to be
        # appended to the telemetry object itself, which nothing ever read.)
        self.commands = []
        self.audio = audio if audio is not None else FakeAudio()
        # The real Supervisor builds these in __init__; here the tests that
        # exercise navigation attach them, and the rest leave them None -- so
        # nav_start/nav_stop must survive that, exactly as the real ones do.
        self.locations = None
        self.navigation = None
        self.auto_sleep_enabled = auto_sleep_enabled
        self.auto_sleep_after_s = after_s
        self.last_activity = 0.0
        self._last_auto_sleep_sent = 0.0

    def set_state(self, state, face_detected=None, vibration_detected=None):
        # Telemetry frames are frozen, so this builds the NEXT observation
        # rather than editing the current one -- which is what the wire does:
        # the owl sends a whole new frame, it never patches the last.
        from dataclasses import replace
        changes = {}
        if face_detected is not None:
            changes["face"] = replace(self.last.face, detected=face_detected)
        if vibration_detected is not None:
            changes["vibration"] = replace(
                self.last.vibration, detected=vibration_detected,
                count=1 if vibration_detected else 0)
        self.last = replace(self.last, state=state, **changes)
        self.last_state = state

    def play_sound(self, sound):
        """Mirrors Supervisor.play_sound(): the supervisor owns the amp."""
        return self.audio.play(sound) if self.audio else False

    def current_state(self):
        """Mirrors Supervisor.current_state()."""
        if self.last_state:
            return self.last_state
        return self.last.state if self.last else None

    def register_activity(self, now=None):
        if self.auto_sleep_enabled:
            import time as _t
            self.last_activity = now if now is not None else _t.time()

    def check_auto_sleep(self, now=None):
        if not self.auto_sleep_enabled or self.last is None:
            return
        import time as _t
        now = now if now is not None else _t.time()
        if self.last_state in ("sleeping", "update"):
            return
        if self.last_activity == 0.0:
            self.last_activity = now
        if (now - self.last_activity) >= self.auto_sleep_after_s:
            if (now - self._last_auto_sleep_sent) >= self.auto_sleep_after_s:
                self._last_auto_sleep_sent = now
                self.sleep()

    def sleep(self):
        """Mirrors Supervisor.sleep(): the command goes out over the serial."""
        self.commands.append(("sleep", "sleep"))
        return self.serial.sleep() if self.serial else True

    def wake(self):
        """Mirrors Supervisor.wake()."""
        self.commands.append(("wake", "wake"))
        return self.serial.wake() if self.serial else True

    def nav_start(self, name):
        """Mirrors Supervisor.nav_start(), guard included."""
        if self.navigation is None:
            return False
        return self.navigation.start(name)

    def nav_stop(self, reason="command"):
        """Mirrors Supervisor.nav_stop(), guard included."""
        if self.navigation is None:
            return False
        return self.navigation.stop(reason)


def make_config(**overrides):
    """Return a full speech config dict (enabled), with overrides applied."""
    cfg = {
        "speech": {
            "enabled": True,
            "language": "de",
            "model": "tiny",
            "mic_device": "",
            "window_s": 2.5,
            "chunk_s": 0.3,
            "sample_rate": 16000,
            # The I2S card's native format (fixed by the owl_i2s kernel module):
            # 48 kHz, stereo (mic on one slot, amp on the other). The worker
            # selects the mic channel and resamples down to sample_rate (16 kHz)
            # before the VAD, so the VAD math is identical to the RPi's.
            "capture_rate": 48000,
            "capture_channels": 2,
            "mic_channel": 0,
            "capture_dtype": "float32",
            "vad_threshold": 0.02,
            "energy_floor_ms": 700,
            "cooldown_s": 4.5,
            "require_face": True,
            "wake_keywords": ["eule", "wacht auf"],
            # Navigation: triggers (the words after the trigger are the place
            # name) + stop keywords. In feed() these are checked BEFORE the
            # keyword clusters, so a nav sentence isn't stolen by a single
            # question word ("wie komm ich zu ..." would otherwise match "wie").
            "nav_triggers": ["bring mich nach", "bringe mich nach",
                             "zeig mir den weg nach",
                             "wie komme ich nach", "wie komme ich zum",
                             "wie komme ich zu"],
            "nav_stop_keywords": ["stopp die navigation", "stopp navigieren",
                                  "lass die navigation", "reicht", "danke"],
            "clusters": {
                "happy": ["fein", "brav", "toll", "super", "gute eule", "mag dich", "schön"],
                "negative": ["nein", "lass das", "aufhören", "böse", "ach", "huch"],
                "question": ["wer", "wie", "was", "warum"],
            },
            "reactions": {
                "happy": {"expression": "happy", "sound": "happy"},
                "negative": {"expression": "surprised", "sound": "alert"},
                "question": {"expression": "surprised", "sound": "alert"},
            },
        },
        # Phase 4: autonomous sleep-on-inactivity (the Supervisor reads config["supervisor"]).
        # disabled by default here, matching the real config.yaml default.
        "supervisor": {
            "auto_sleep": {"enabled": False, "after_s": 60.0},
        },
        # Navigation ("guide me home"). Enabled by default here so the nav
        # tests pass with a plain make_config(); the real config.yaml default is
        # disabled. locations_file="" means "use the default path".
        "navigation": {
            "enabled": True,
            "locations_file": "",
            "head_min": -45,
            "head_max": 45,
            "refresh_min_s": 0.5,
            "arrive_m": 15.0,
            "timeout_s": 120,
            "aim_sign": 1,
            "sound_start": "detecting",
            "sound_stop": "waking",
        },
        # Optional sentence-level emotion layer (llama.cpp). DISABLED by default
        # here (matching the real config.yaml default) so the existing
        # keyword-cluster tests are unaffected: with it off, feed() never calls
        # EmotionClassifier.classify() and the llama.cpp stub is never invoked.
        # The emotion test overrides enabled=True + the label map.
        "emotion": {
            "enabled": False,
            "model_path": "",
            "prompt_template": "Klassifiziere die Emotion: {text}",
            "max_tokens": 8,
            "labels": {
                "Freude": "happy",
                "Wut": "angry",
                "Trauer": "sad_down",
                "Angst": "worried",
                "Ueberraschung": "surprised",
                "Verachtung": "unimpressed",
            },
            "sounds": {},
        },
    }
    for k, v in overrides.items():
        cfg["speech"][k] = v
    return cfg
