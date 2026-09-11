"""
Robot Owl OrangePi Brain - Web UI

A small LAN-only web page for manually poking the owl: blink, change
expression, and move the head with arrow buttons. Every action forwards the
same NDJSON command the supervisor uses, so no firmware change is needed.

The page polls /api/telemetry for live state (so you can see the result of
each action). There is NO authentication: only expose this on a trusted
local network.
"""

import dataclasses
import logging
import os
import threading

from flask import Flask, jsonify, render_template_string, request

from brain import expressions
from brain.serial_handler import SerialHandler, Telemetry
from brain.supervisor import Supervisor

logger = logging.getLogger(__name__)


# Servo channel indices (must match esp32-s3-sense/include/config.h).
# PCA9685 channel numbers. MUST match the CH_* defines in
# esp32-s3-sense/include/config.h — this is a mirror, not the source of truth.
#
# "left"/"right" are the OWL's own left and right: standing in front of it, its
# left ear is the one on YOUR right.
#
# The ears are on 15 and 14, not 0 and 1 — moved 2026-08-27 because the servo
# cables were too short to reach the low channels. The numbers are sparse on
# purpose; do not assume they are contiguous.
CH_LEFT_EAR = 15
CH_RIGHT_EAR = 14
CH_HEAD = 2
CH_LEFT_WING = 3
CH_RIGHT_WING = 4

# Absolute head servo angles for the left/right buttons (degrees, -45..45).
# The head only pans left/right (it does not tilt up/down), so there are no
# up/down angles or buttons for it.
HEAD_LEFT = -40
HEAD_RIGHT = 40

# Ears and wings pivot on a single axis, so each has an "up" and "down" button
# (and center). Angles are absolute and clamped to +-45 by the firmware.
EAR_UP = -35
EAR_DOWN = 35
WING_UP = -40
WING_DOWN = 40
CENTER = 0


def _ear_wing_angle(direction: str, up: float, down: float) -> float:
    """Map a UI direction to an absolute servo angle for a single-axis part
    (ear or wing). 'center' returns 0; unknown directions default to center.

    Defined BELOW the angle constants it reads, not above them: it referenced
    CENTER 38 lines before that name existed, which only worked because the
    body is not evaluated until call time. It read as a bug on every pass.
    """
    if direction == "up":
        return up
    if direction == "down":
        return down
    return CENTER

# Expressions offered in the UI. GENERATED from NAMES[] in
# esp32-s3-sense/lib/Eyes/Eyes.cpp -- see brain/expressions.py and
# tools/gen_expressions.py. This used to be a hand-maintained list here and had
# drifted from the firmware by three names; the firmware silently renders an
# unknown name as "neutral", so drift looks like an eye that refuses to change.
EXPRESSIONS = list(expressions.SELECTABLE)

# Sound effects the OrangePi can play through the MAX98357A amp (see brain/audio.py).
# The owl-call voices (detecting/interacting/happy/sleeping/waking/alert) play a
# real recording when present, else a synthesized tone of the same name.
SOUNDS = ["detecting", "interacting", "happy", "sleeping", "waking", "alert", "beep"]

# Friendly labels for the sound buttons (value -> display text).
SOUND_LABELS = {
    "detecting": "Hoot (spotted)",
    "interacting": "Hoot (talking)",
    "happy": "Hoot (happy)",
    "sleeping": "Hoot (sleepy)",
    "waking": "Hoot (waking)",
    "alert": "Hoot (alert)",
    "beep": "Beep",
}

# ============================================================================
# The page
#
# The HTML/CSS/JS lives in templates/index.html, not in a ~430-line string
# literal in this file. Reasons it moved (2026-08-27): an editor can highlight
# and lint it, diffs are readable, and it is no longer inside a Python raw
# string where a stray quote or backslash is a syntax error in the brain.
#
# It is still rendered with render_template_string rather than Flask's
# render_template. That is deliberate: render_template depends on Flask's
# template-folder resolution, and there is no Flask or Jinja2 on the machine
# this refactor was done on, so that path could not be tested. Feeding the
# source to the renderer that already worked keeps the change to "where the
# bytes live". Switch to render_template if you can verify it on a real
# deployment; the Jinja syntax in the file is already compatible.
#
# Read once at import. Editing the template needs a brain restart, exactly as
# it did when it was a literal.
# ============================================================================
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "templates", "index.html")


def _load_template() -> str:
    """Read the page source, or raise something an OrangePi log can be diagnosed from.

    main.py imports WebUI inside a try/except, so a failure here degrades to
    "running without the web UI" rather than taking the brain down (the same
    contract Speech has). That only helps if the message says what is wrong --
    the bare FileNotFoundError names a path but not the cause.
    """
    try:
        with open(TEMPLATE_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        raise RuntimeError(
            f"web UI template missing or unreadable: {TEMPLATE_PATH} ({e}). "
            "It ships in brain/templates/ and setup.sh rsyncs the tree "
            "wholesale, so this usually means an incomplete deployment or a new "
            "rsync --exclude."
        ) from e


TEMPLATE = _load_template()


# Two Telemetry attributes whose PYTHON name is not their WIRE name. The page has
# read `eye` and `uptime` since it existed; a plain asdict() emits
# `eye_expression` and `uptime_ms` and would silently blank both displays.
_PAYLOAD_RENAMES = (("eye_expression", "eye"), ("uptime_ms", "uptime"))


def _telemetry_payload(t: Telemetry) -> dict:
    """One telemetry frame as a JSON-able dict, derived from the dataclass.

    dataclasses.asdict() walks the whole frozen tree, so this CANNOT fall behind
    the protocol: a new firmware field is one row in serial_handler's _*_FIELDS
    table plus one dataclass field, and it shows up here for free.

    This was ~50 lines of hand-copied field accesses, and it had already drifted
    in BOTH directions -- omitting loop_hz, uptime and all four face diagnostics
    while sending face.confidence/gaze_x/gaze_y that no part of the page reads.
    Unlike the inbound tables this is not a protocol requirement (R-010.5 is
    about parsing what the firmware sends); it is simply less code that cannot
    go stale.

    Three deliberate deviations from a plain asdict():

    * `eye` / `uptime` keep their wire names -- see _PAYLOAD_RENAMES.
    * `update.password` is dropped. This endpoint has no authentication, and
      while the page could legitimately show the SoftAP credentials, adding a
      password-shaped field to an unauthenticated route is not a change to make
      as a side effect of a refactor. UPDATE_AP_SSID/PASSWORD are compile-time
      constants in esp32-s3-sense/include/config.h if the page ever wants them.
    * `navigation` is moved to `navigation_esp32`. The name means two different
      things on the two sides: on the wire it is the FIRMWARE's echo (is the head
      really being held at the angle we sent?), while in this payload it is the
      OrangePi controller's status, which is what the Navigate card renders (target,
      bearing, distance_m, aim). The route sets the controller's copy after
      this, so before asdict the firmware's copy was shadowed by assignment
      order alone -- nobody had noticed the two were colliding.
    """
    payload = dataclasses.asdict(t)
    for src, dst in _PAYLOAD_RENAMES:
        payload[dst] = payload.pop(src)
    payload.get("update", {}).pop("password", None)
    payload["navigation_esp32"] = payload.pop("navigation", None)
    return payload


class WebUI:
    """Flask app for manually testing owl features over the LAN."""

    def __init__(self, serial: SerialHandler, supervisor: Supervisor,
                 host: str = "0.0.0.0", port: int = 8080, speech=None):
        self.serial = serial
        self.supervisor = supervisor
        # Optional Speech recognizer: when present, the page shows the last
        # transcript the owl heard (and how recently). None when speech is
        # disabled / failed to start, in which case the field is omitted.
        self.speech = speech
        self.host = host
        self.port = port
        self._thread = None
        self.app = self._build_app()

    # ------------------------------------------------------------------
    # Flask app
    # ------------------------------------------------------------------
    def _build_app(self) -> Flask:
        app = Flask("robot-owl-web")

        @app.route("/")
        def index():
            return render_template_string(TEMPLATE, expressions=EXPRESSIONS, sounds=SOUNDS,
                                           sound_labels=SOUND_LABELS)

        @app.route("/api/telemetry")
        def api_telemetry():
            t: Telemetry = self.supervisor.last
            if t is None:
                return jsonify({"state": None})
            payload = _telemetry_payload(t)
            # Phase 3: surface what the owl last heard (and how recently) so the
            # page can display it. Omitted entirely when speech is disabled, so
            # the payload is unchanged for deployments that don't use speech.
            if self.speech is not None:
                payload["last_heard"] = {
                    "text": self.speech.last_heard,
                    "at": self.speech.last_heard_at,
                }
            # Navigation: surface the live compass state (active? target? bearing?
            # distance?) so the Navigate card can show it without a separate poll.
            nav = getattr(self.supervisor, "navigation", None)
            if nav is not None:
                payload["navigation"] = nav.status()
            return jsonify(payload)

        @app.route("/api/blink", methods=["POST"])
        def api_blink():
            body = request.get_json(silent=True) or {}
            speed = int(body.get("speed", 3))
            ok = self.serial.blink(speed)
            return jsonify({"ok": ok})

        @app.route("/api/expression", methods=["POST"])
        def api_expression():
            value = (request.get_json(silent=True) or {}).get("value", "neutral")
            ok = self.serial.set_expression(value)
            return jsonify({"ok": ok})

        @app.route("/api/head", methods=["POST"])
        def api_head():
            # The head only pans left/right (no up/down tilt), so those are the
            # only directions offered. Any other value falls back to center.
            direction = (request.get_json(silent=True) or {}).get("direction", "center")
            angle = (HEAD_LEFT if direction == "left"
                     else HEAD_RIGHT if direction == "right" else CENTER)
            ok = self.serial.set_servo(CH_HEAD, angle)
            return jsonify({"ok": ok, "channel": CH_HEAD, "angle": angle})

        @app.route("/api/ear", methods=["POST"])
        def api_ear():
            body = request.get_json(silent=True) or {}
            side = body.get("side", "left")
            direction = body.get("direction", "center")
            channel = CH_LEFT_EAR if side == "left" else CH_RIGHT_EAR
            angle = _ear_wing_angle(direction, EAR_UP, EAR_DOWN)
            ok = self.serial.set_servo(channel, angle)
            return jsonify({"ok": ok, "channel": channel, "angle": angle})

        @app.route("/api/wing", methods=["POST"])
        def api_wing():
            body = request.get_json(silent=True) or {}
            side = body.get("side", "left")
            direction = body.get("direction", "center")
            channel = CH_LEFT_WING if side == "left" else CH_RIGHT_WING
            angle = _ear_wing_angle(direction, WING_UP, WING_DOWN)
            ok = self.serial.set_servo(channel, angle)
            return jsonify({"ok": ok, "channel": channel, "angle": angle})

        @app.route("/api/sound", methods=["POST"])
        def api_sound():
            value = (request.get_json(silent=True) or {}).get("value", "beep")
            # OrangePi-only: the MAX98357A amp is on the OrangePi's I2S bus and the ESP32
            # has no audio pins at all, so there is nothing to forward.
            ok = self.supervisor.play_sound(value)
            return jsonify({"ok": ok, "sound": value})

        @app.route("/api/sleep", methods=["POST"])
        def api_sleep():
            return jsonify({"ok": self.supervisor.sleep()})

        @app.route("/api/wake", methods=["POST"])
        def api_wake():
            return jsonify({"ok": self.supervisor.wake()})

        # ------------------------------------------------------------------
        # Navigation ("guide me home"). The store + controller live on the
        # supervisor (shared with speech); the web UI just reads/writes them.
        # ------------------------------------------------------------------
        def _nav_available():
            nav = getattr(self.supervisor, "navigation", None)
            # The controller must exist AND be enabled (a disabled navigation
            # still has a controller object, so "exists" alone is not enough).
            return nav is not None and getattr(nav, "enabled", False)

        @app.route("/api/locations")
        def api_locations():
            if not _nav_available():
                return jsonify({"ok": False, "error": "navigation disabled", "locations": []})
            return jsonify({"ok": True, "locations": self.supervisor.locations.all()})

        @app.route("/api/locations", methods=["POST"])
        def api_locations_add():
            if not _nav_available():
                return jsonify({"ok": False, "error": "navigation disabled"})
            body = request.get_json(silent=True) or {}
            name = (body.get("name") or "").strip()
            lat, lon = body.get("lat"), body.get("lon")
            if not name or lat is None or lon is None:
                return jsonify({"ok": False, "error": "name, lat and lon are required"})
            ok = self.supervisor.locations.add(name, float(lat), float(lon))
            return jsonify({"ok": ok})

        @app.route("/api/locations/delete", methods=["POST"])
        def api_locations_delete():
            if not _nav_available():
                return jsonify({"ok": False, "error": "navigation disabled"})
            name = (request.get_json(silent=True) or {}).get("name", "")
            return jsonify({"ok": self.supervisor.locations.remove(name)})

        @app.route("/api/nav/start", methods=["POST"])
        def api_nav_start():
            if not _nav_available():
                return jsonify({"ok": False, "error": "navigation disabled"})
            name = (request.get_json(silent=True) or {}).get("name", "")
            ok = self.supervisor.nav_start(name)
            return jsonify({"ok": ok, "status": self.supervisor.navigation.status()})

        @app.route("/api/nav/stop", methods=["POST"])
        def api_nav_stop():
            if not _nav_available():
                return jsonify({"ok": False, "error": "navigation disabled"})
            ok = self.supervisor.nav_stop("web")
            return jsonify({"ok": ok, "status": self.supervisor.navigation.status()})

        return app

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start the Flask server in a daemon thread (non-blocking)."""
        if self._thread is not None:
            return
        logger.info("Starting web UI on %s:%s", self.host, self.port)
        self._thread = threading.Thread(
            target=self.app.run,
            kwargs={"host": self.host, "port": self.port,
                    "debug": False, "use_reloader": False},
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Best-effort shutdown. The thread is a daemon, so process exit
        cleans it up; this just logs intent."""
        if self._thread is not None:
            logger.info("Web UI thread will exit with the process")
            self._thread = None
