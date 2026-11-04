"""
Robot Owl OrangePi Brain - Power button (safe shutdown)

A push button from a header pin to GND (PB0, header pin 11) shuts the Orange Pi
down cleanly, so switching the power bank off no longer cuts power under a
writing SD card. Hold it for `hold_s` (2 s): a bump on the shoulder must not
switch the owl off.

This is NOT part of the brain. It runs as its own root service
(deploy/robot-owl-power.service), because the button has to work exactly when
the brain has crashed or hangs -- otherwise pulling the power is the only way
out again.

It only arms after it has seen the button RELEASED once: a stuck or shorted
button would otherwise power the owl off on every boot, with no way to log in
and fix it.

    python -m brain.power_button [config.yaml]
"""

import logging
import subprocess
import sys
import time
from typing import Callable, Optional

import yaml

from brain.gpio import DEFAULT_CHIP, GpioInput

logger = logging.getLogger(__name__)


class PowerButton:
    def __init__(self, config: dict, line=None, run: Callable = None,
                 clock: Callable[[], float] = time.monotonic):
        cfg = (config or {}).get("power_button", {}) or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.hold_s = float(cfg.get("hold_s", 2.0))
        self.poll_s = float(cfg.get("poll_s", 0.05))
        self.command = list(cfg.get("command") or ["shutdown", "-h", "now"])
        self._line = line or GpioInput(cfg.get("chip", DEFAULT_CHIP), cfg.get("line", "PB0"),
                                       consumer="owl-power")
        self._run_cmd = run or subprocess.run
        self._clock = clock
        self.armed = False
        self.fired = False
        self._pressed_since: Optional[float] = None

    def poll(self) -> bool:
        """One step. Returns True once the shutdown has been triggered."""
        if self.fired:
            return True
        pressed = self._line.read()
        now = self._clock()
        if not pressed:
            if not self.armed:
                logger.info("Power button armed (hold %.1f s to shut down)", self.hold_s)
            self.armed = True
            self._pressed_since = None
            return False
        if not self.armed:
            return False
        if self._pressed_since is None:
            self._pressed_since = now
        elif now - self._pressed_since >= self.hold_s:
            logger.warning("Power button held %.1f s - shutting down: %s",
                           now - self._pressed_since, " ".join(self.command))
            self.fired = True
            self._run_cmd(self.command, check=False)
        return self.fired

    def run(self) -> None:
        self._line.open()
        try:
            while not self.poll():
                time.sleep(self.poll_s)
        finally:
            self._line.close()


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = sys.argv[1:] if argv is None else argv
    with open(args[0] if args else "config.yaml") as f:
        config = yaml.safe_load(f) or {}
    button = PowerButton(config)
    if not button.enabled:
        logger.info("power_button.enabled is false - nothing to do")
        return 0
    button.run()
    # Stay alive until systemd stops us during the shutdown: exiting early would let
    # Restart= start a second copy while the shutdown is running.
    while True:
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
