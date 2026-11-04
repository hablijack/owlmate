"""
Robot Owl OrangePi Brain - Mute switch

A slide switch from a header pin to GND (PB1, header pin 13) mutes the owl:
while it is in the mute position, Audio.play() starts no player at all. That is
a true 0 % -- the owl_i2s card has no mixer control to turn down -- and as a
side effect speech is not held off by the own-voice barrier for sounds nobody
hears. The optional silent TX keepalive (audio.keep_playback_clock) is NOT
touched: it is not a sound, and stopping it would silence the microphone
(SPEC-015).

Polled from a daemon thread; a new position counts once two reads in a row
agree. A pin that cannot be opened logs once and leaves the owl audible.
"""

import logging
import threading
from typing import Optional

from brain.gpio import DEFAULT_CHIP, GpioInput

logger = logging.getLogger(__name__)


class MuteSwitch:
    def __init__(self, audio, config: dict, line=None):
        cfg = (config or {}).get("mute_switch", {}) or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.poll_s = float(cfg.get("poll_s", 0.2))
        # Which contact state means "mute": closed = pin at GND.
        self.mute_when_closed = str(cfg.get("muted_when", "closed")).lower() != "open"
        self._audio = audio
        self._line = line or GpioInput(cfg.get("chip", DEFAULT_CHIP), cfg.get("line", "PB1"),
                                       consumer="owl-mute")
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pending: Optional[bool] = None

    def _muted_now(self) -> bool:
        return self._line.read() == self.mute_when_closed

    def poll(self) -> None:
        """One debounced step: apply a position once it has been read twice in a row."""
        muted = self._muted_now()
        if muted == self._audio.muted:
            self._pending = None
        elif self._pending == muted:
            self._audio.set_muted(muted)
            self._pending = None
        else:
            self._pending = muted

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        try:
            self._line.open()
            # Before the first sound can play: no debounce for the boot position.
            self._audio.set_muted(self._muted_now())
        except Exception as e:
            self._line.close()
            logger.warning("Mute switch not available (%s) - the owl stays audible. "
                           "Is /dev/gpiochip* readable by this user (group gpio)?", e)
            return
        self._thread = threading.Thread(target=self._run, name="mute-switch", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.poll_s):
            try:
                self.poll()
            except Exception as e:
                logger.warning("Mute switch read failed (%s); stopping, owl stays as it is", e)
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._line.close()
