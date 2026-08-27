"""
Robot Owl RPi Brain - Serial Communication Handler

Handles NDJSON protocol communication with ESP32-S3 firmware.
Receives telemetry and face detection data, sends commands back.
"""

import serial
import json
import time
import logging
from typing import Callable, Optional, Dict, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Servos fitted, and therefore the length of the telemetry "servos" array.
# Mirrors NUM_SERVOS in esp32-s3-sense/include/config.h. The array order is
# fixed regardless of the physical PCA9685 channels (which are sparse -- the
# ears sit on 14 and 15): [left_ear, right_ear, head, left_wing, right_wing].
NUM_SERVOS = 5


@dataclass(frozen=True)
class FaceDetection:
    """Face detection result from the ESP32's on-device esp-dl inference."""
    detected: bool = False
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    confidence: float = 0.0
    gaze_x: float = 0.0
    gaze_y: float = 0.0
    # Cumulative hits since boot. `detected` is an INSTANTANEOUS flag sampled at
    # the 2 Hz telemetry rate, so it misses sporadic detections: the firmware
    # once reported detected:false in 39 of 39 frames while the owl was
    # demonstrably in INTERACTING. A rising `total` is how you tell "detecting
    # sporadically" from "not detecting at all".
    total: int = 0


@dataclass(frozen=True)
class IMUCalibration:
    """BNO055 per-sensor calibration counters, 0..3 each.

    Sent so the figure-8 dance can be guided from the web UI instead of a serial
    log. `sys` and `accel` are live confidence values that fall during any
    movement, which is why IMUData.calibrated deliberately excludes them (see
    Sensors::getImu() -- requiring all four made the flag false in 0 of 23
    frames on a fully calibrated sensor). `restored` means the offsets came back
    from NVS at boot, so the owl started up already calibrated.
    """
    sys: int = 0
    gyro: int = 0
    accel: int = 0
    mag: int = 0
    restored: bool = False


@dataclass(frozen=True)
class IMUData:
    """Fused orientation from the BNO055.

    `yaw` is a TRUE geographic heading of the beak: the firmware folds the
    mounting rotation and the magnetic declination into IMU_HEADING_OFFSET_DEG,
    so it shares one north reference with geo.bearing_deg(). Accurate to roughly
    +/-5..10 deg.
    """
    pitch: float = 0.0
    roll: float = 0.0
    yaw: float = 0.0
    # gyro >= 3 and mag >= 3. Navigation refuses to aim while this is false.
    calibrated: bool = False
    cal: IMUCalibration = field(default_factory=IMUCalibration)


@dataclass(frozen=True)
class GPSData:
    """PA1010D fix. Everything is zero until `valid` goes true."""
    valid: bool = False
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    satellites: int = 0


@dataclass(frozen=True)
class VibrationData:
    """SW-420 tap state, derived from an edge-counting ISR (not a level read)."""
    detected: bool = False
    count: int = 0
    # Raw ISR edge count since boot. The only way to distinguish "nobody tapped"
    # from "the sensor is dead": constant at rest, rising while the owl is
    # motionless means interference or an over-sensitive pot.
    pulses: int = 0


@dataclass(frozen=True)
class UpdateMode:
    """Firmware update mode (SoftAP + /update HTTP server).

    Populated only while the owl is in the UPDATE state (entered by a 4-tap
    vibration sequence). The owl is on an isolated SoftAP during this time, so
    the RPi can no longer reach it over the normal USB serial link.
    """
    active: bool = False
    ssid: str = ""
    password: str = ""
    ip: str = ""
    url: str = ""


@dataclass(frozen=True)
class NavigationState:
    """Navigation (compass-to-destination) status from the ESP32.

    Present only while the owl is in the NAVIGATING state. `active` is False
    otherwise; `angle` is the head angle the owl is currently holding.
    """
    active: bool = False
    angle: float = 0.0


@dataclass(frozen=True)
class Telemetry:
    """One complete telemetry frame from the ESP32.

    Frozen: a frame is an observation of a moment, and the supervisor hands the
    same object to navigation, speech and the web UI. Nothing may edit it after
    the fact.
    """
    timestamp: float = field(default_factory=time.time)
    state: str = "idle"
    uptime_ms: int = 0
    firmware: str = ""
    imu: IMUData = field(default_factory=IMUData)
    gps: GPSData = field(default_factory=GPSData)
    vibration: VibrationData = field(default_factory=VibrationData)
    update: UpdateMode = field(default_factory=UpdateMode)
    servos: list = field(default_factory=lambda: [0.0] * NUM_SERVOS)
    face: FaceDetection = field(default_factory=FaceDetection)
    eye_expression: str = "neutral"
    navigation: NavigationState = field(default_factory=NavigationState)


# ============================================================================
# Wire format
#
# One row per field: (wire key, attribute, coercion). These tables ARE the
# contract with sendTelemetry() in esp32-s3-sense/src/main.cpp -- adding a
# firmware field means adding one row here and one field to the dataclass
# above, and nothing else.
#
# Until 2026-08-27 this was ~60 lines of hand-written .get() calls, and seven
# fields the firmware sends had simply never been transcribed: the five
# imu.cal.* counters, vibration.pulses and face.total. Every one of them exists
# specifically to make an invisible failure visible, and all seven were being
# dropped on the floor.
# ============================================================================
_IMU_CAL_FIELDS = (
    ("sys", "sys", int),
    ("gyro", "gyro", int),
    ("accel", "accel", int),
    ("mag", "mag", int),
    ("restored", "restored", bool),
)
_IMU_FIELDS = (
    ("pitch", "pitch", float),
    ("roll", "roll", float),
    ("yaw", "yaw", float),
    ("calibrated", "calibrated", bool),
)
_GPS_FIELDS = (
    ("valid", "valid", bool),
    ("latitude", "latitude", float),
    ("longitude", "longitude", float),
    ("altitude", "altitude", float),
    ("satellites", "satellites", int),
)
_VIBRATION_FIELDS = (
    ("detected", "detected", bool),
    ("count", "count", int),
    ("pulses", "pulses", int),
)
_FACE_FIELDS = (
    ("detected", "detected", bool),
    ("x", "x", int),
    ("y", "y", int),
    ("w", "w", int),
    ("h", "h", int),
    ("confidence", "confidence", float),
    ("gaze_x", "gaze_x", float),
    ("gaze_y", "gaze_y", float),
    ("total", "total", int),
)
_UPDATE_FIELDS = (
    ("ssid", "ssid", str),
    ("password", "password", str),
    ("ip", "ip", str),
    ("url", "url", str),
)
_NAVIGATION_FIELDS = (
    ("active", "active", bool),
    ("angle", "angle", float),
)


def _as_dict(value) -> dict:
    """A wire section as a dict, whatever actually arrived."""
    return value if isinstance(value, dict) else {}


def _scalar(data: dict, key: str, cast, default):
    """One coerced scalar, falling back to `default` on absent/null/bad type."""
    value = data.get(key)
    if value is None:
        return default
    try:
        return cast(value)
    except (TypeError, ValueError):
        logger.debug("telemetry: ignoring bad %s=%r", key, value)
        return default


def _section(cls, spec, data: dict, **extra):
    """Build one frozen dataclass from a wire section using `spec`.

    A field that is absent, null, or of an unusable type keeps the dataclass's
    own default rather than raising -- one malformed field must never cost the
    whole frame, because this runs on the foreground read loop.
    """
    kwargs = {}
    for wire_key, attr, cast in spec:
        value = data.get(wire_key)
        if value is None:
            continue
        try:
            kwargs[attr] = cast(value)
        except (TypeError, ValueError):
            logger.debug("telemetry: ignoring bad %s=%r", wire_key, value)
    kwargs.update(extra)
    return cls(**kwargs)



class SerialHandler:
    """Handles serial communication with ESP32-S3"""

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial: Optional[serial.Serial] = None
        self._buffer = ""

    def connect(self) -> bool:
        """Connect to ESP32 via serial"""
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout
            )
            time.sleep(2)  # Wait for ESP32 to boot
            logger.info(f"Connected to ESP32 on {self.port}")
            return True
        except serial.SerialException as e:
            logger.error(f"Failed to connect to ESP32: {e}")
            return False

    def disconnect(self):
        """Disconnect from ESP32"""
        if self.serial and self.serial.is_open:
            self.serial.close()
            logger.info("Disconnected from ESP32")

    def send_command(self, command: Dict[str, Any]) -> bool:
        """Send a JSON command to ESP32"""
        if not self.serial or not self.serial.is_open:
            logger.error("Serial not connected")
            return False

        try:
            json_str = json.dumps(command) + "\n"
            self.serial.write(json_str.encode('utf-8'))
            logger.debug(f"Sent command: {json_str.strip()}")
            return True
        except serial.SerialException as e:
            logger.error(f"Failed to send command: {e}")
            return False

    def set_expression(self, expression: str) -> bool:
        """Set eye expression on ESP32"""
        return self.send_command({"type": "expression", "value": expression})

    def set_servo(self, channel: int, angle: float) -> bool:
        """Set servo angle (channel 0-4, angle -45 to 45)"""
        return self.send_command({
            "type": "servo",
            "channel": channel,
            "angle": angle
        })

    def set_gaze(self, x: float, y: float) -> bool:
        """Set gaze direction (x, y in range -1.0 to 1.0)"""
        return self.send_command({
            "type": "gaze",
            "x": x,
            "y": y
        })

    def nav(self, angle: float, active: bool = True) -> bool:
        """Tell the ESP32 to point its head at `angle` (navigation/compass).

        active=True  -> enter/hold the NAVIGATING state and point the head at
                        `angle` (a PERSISTENT override: it holds until an
                        active=False is sent, unlike the 3s gaze override).
        active=False -> leave NAVIGATING (head recenters, back to normal).
        """
        return self.send_command({
            "type": "nav",
            "angle": angle,
            "active": active,
        })

    def wake(self) -> bool:
        """Wake up ESP32 from sleep"""
        return self.send_command({"type": "wake"})

    def blink(self, speed: int = 3) -> bool:
        """Trigger blink animation"""
        return self.send_command({"type": "blink", "speed": speed})

    def heartbeat(self) -> bool:
        """Send heartbeat request"""
        return self.send_command({"type": "heartbeat"})

    def parse_telemetry(self, json_str: str) -> Optional[Telemetry]:
        """Parse one NDJSON line into a Telemetry, or None if it is not one.

        None means "not a telemetry frame" -- an ack, a boot line, a
        hardware_check, or malformed JSON. Callers treat that as "not for me",
        not as an error.

        Field-by-field transcription lives in the _*_FIELDS tables above; this
        function only handles the frame's shape. Two sections are
        PRESENCE-flagged rather than value-flagged: the firmware emits `update`
        and `navigation` only while in that state, so the section merely being
        there is what means active.
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse telemetry: %s", e)
            return None

        if not isinstance(data, dict) or data.get("type") != "telemetry":
            return None

        imu_raw = _as_dict(data.get("imu"))
        update_raw = data.get("update")
        nav_raw = data.get("navigation")

        servos = data.get("servos")
        if not isinstance(servos, list):
            servos = [0.0] * NUM_SERVOS

        return Telemetry(
            state=_scalar(data, "state", str, "idle"),
            uptime_ms=_scalar(data, "uptime", int, 0),
            firmware=_scalar(data, "fw", str, ""),
            eye_expression=_scalar(data, "eye", str, "neutral"),
            imu=_section(
                IMUData, _IMU_FIELDS, imu_raw,
                cal=_section(IMUCalibration, _IMU_CAL_FIELDS,
                             _as_dict(imu_raw.get("cal"))),
            ),
            gps=_section(GPSData, _GPS_FIELDS, _as_dict(data.get("gps"))),
            vibration=_section(VibrationData, _VIBRATION_FIELDS,
                               _as_dict(data.get("vibration"))),
            update=(_section(UpdateMode, _UPDATE_FIELDS, _as_dict(update_raw),
                             active=True)
                    if update_raw else UpdateMode()),
            navigation=(_section(NavigationState, _NAVIGATION_FIELDS,
                                 _as_dict(nav_raw))
                        if nav_raw else NavigationState()),
            servos=servos,
            face=_section(FaceDetection, _FACE_FIELDS, _as_dict(data.get("face"))),
        )

    def _handle_message(self, line: str, callback: Optional[Callable[[Telemetry], None]] = None):
        """Dispatch a single NDJSON line by its 'type' field.

        Telemetry frames are parsed into a Telemetry object and passed to the
        callback. All other message types (boot, update_mode, *_ack) are logged
        here instead of being silently dropped.
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse message: {e}")
            return

        msg_type = data.get("type")

        if msg_type == "telemetry":
            telemetry = self.parse_telemetry(line)
            if telemetry:
                logger.debug(
                    f"Telemetry: state={telemetry.state}, "
                    f"face_detected={telemetry.face.detected}"
                )
                if callback:
                    callback(telemetry)
        elif msg_type == "boot":
            logger.info("Owl booted: %s", data.get("msg", "ready"))
        elif msg_type in ("update_mode", "update_mode_end"):
            # Transport-level: just note that the event line arrived. The
            # OPERATOR-facing announcement (with the SSID/password/URL) is the
            # supervisor's job -- it is edge-triggered on the telemetry `update`
            # field in Supervisor.on_telemetry. Both used to log the same
            # paragraph verbatim, so every entry into update mode was announced
            # twice.
            logger.debug("Owl %s: %s", msg_type, data)
        elif msg_type == "error":
            logger.error("Owl reported error: %s", data.get("msg", "unknown"))
        elif msg_type and msg_type.endswith("_ack"):
            logger.debug("Owl ack: %s %s", msg_type, data)
        else:
            logger.debug("Owl message (unhandled): %s", data)

    def read_loop(self, callback: Optional[Callable[[Telemetry], None]] = None,
                   idle_callback: Optional[Callable[[], None]] = None):
        """Main read loop - processes incoming serial data.

        callback: invoked for each parsed telemetry frame.
        idle_callback: invoked on iterations where no data arrived (used to
        run periodic checks such as telemetry-staleness detection).
        """
        if not self.serial or not self.serial.is_open:
            logger.error("Serial not connected")
            return

        logger.info("Starting serial read loop...")

        while self.serial.is_open:
            try:
                # Read available data
                data = self.serial.read(self.serial.in_waiting or 1)
                if not data:
                    if idle_callback:
                        idle_callback()
                    continue

                text = data.decode('utf-8', errors='ignore')
                self._buffer += text

                # Process complete lines
                while '\n' in self._buffer:
                    line, self._buffer = self._buffer.split('\n', 1)
                    line = line.strip()

                    if not line:
                        continue

                    # Dispatch by message type (telemetry + acks/boot/update).
                    # A failure in here is almost always a bug in the telemetry
                    # CALLBACK (supervisor / navigation / speech), not a serial
                    # fault. This loop runs on the foreground thread, so letting
                    # it escape would take the whole brain down over one bad
                    # frame -- log it and keep reading instead.
                    try:
                        self._handle_message(line, callback)
                    except Exception:
                        logger.exception(
                            "Error handling message, continuing: %.200s", line
                        )

            except serial.SerialException as e:
                logger.error(f"Serial read error: {e}")
                break
            except Exception as e:
                logger.error(f"Unexpected error in read loop: {e}")
                break
