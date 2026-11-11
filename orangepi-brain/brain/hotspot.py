"""
Robot Owl OrangePi Brain - Fallback hotspot

When the Orange Pi finds no known WLAN (at home: "Olymp"), it opens its own
access point "Hubert" so a phone can still reach the control page at
http://10.42.0.1:8080.

Like the power button this is NOT part of the brain: it runs as its own root
service (deploy/robot-owl-hotspot.service), because creating an access point
needs root, and because it must work when the brain has crashed.

THE AP RUNS ON A SEPARATE VIRTUAL INTERFACE (uap0) AND wlan0 IS NEVER TOUCHED.
The AIC8800 allows one managed plus one AP interface at once (`iw list`:
"#{ managed } <= 1, #{ AP } <= 1"). So wpa_supplicant keeps looking for the
known WLAN the whole time, and the owl rejoins it on its own. The first version
(2026-11-11) instead switched wlan0 itself between client and AP with
ifdown/ifup; on the bench test the AP never appeared and wlan0 never came back
- the Orange Pi had to be power-cycled, and the journal (not persistent) lost
the reason. Probed afterwards on uap0 next to a live Olymp connection: AP up,
iPhone associated (WPA2), DHCP lease, control page loaded, Olymp never dropped.

Two states:

* CLIENT - normal. If wlan0 has had no association plus IPv4 address for
  `wait_s`, open the AP.
* AP - hostapd + dnsmasq on uap0. Close it once wlan0 is connected again AND no
  phone is associated: a phone using the control page is never cut off.

The passphrase lives in a root-only file (`passphrase_file`), never in
config.yaml: that file is in a public repository. The control page has no
login, so an open hotspot would hand the owl - servos included - to anyone in
range; without a valid passphrase (8..63 chars) the AP is not started at all.

    python -m brain.hotspot [config.yaml]
    python -m brain.hotspot --test SECONDS [config.yaml]   # bench test, see test_ap()
"""

import logging
import os
import signal
import subprocess
import sys
import time
from typing import Callable, List, Optional

import yaml

logger = logging.getLogger(__name__)

CLIENT = "client"
AP = "ap"


def read_passphrase(path: str) -> Optional[str]:
    """The WPA2 passphrase, or None when the file is missing or the value is
    not a valid WPA2 passphrase (8..63 printable ASCII)."""
    try:
        with open(path, encoding="utf-8") as f:
            value = f.read().strip("\r\n")
    except OSError:
        return None
    if not 8 <= len(value) <= 63:
        return None
    if any(not (32 <= ord(ch) <= 126) for ch in value):
        return None
    return value


def ap_mac(client_mac: str) -> str:
    """A distinct MAC for the AP interface: the client's with the
    locally-administered bit set (bc:.. -> be:..)."""
    octets = client_mac.strip().lower().split(":")
    octets[0] = f"{int(octets[0], 16) | 0x02:02x}"
    return ":".join(octets)


def hostapd_conf(iface: str, ssid: str, passphrase: str, channel: int, country: str,
                 ctrl_dir: str) -> str:
    # hostapd takes everything after '=' literally, '#' and '!' included.
    return "\n".join([
        f"interface={iface}",
        "driver=nl80211",
        f"ctrl_interface={ctrl_dir}",
        f"ssid={ssid}",
        f"country_code={country}",
        "hw_mode=g",
        f"channel={channel}",
        "ieee80211n=1",
        "auth_algs=1",
        "wpa=2",
        "wpa_key_mgmt=WPA-PSK",
        "rsn_pairwise=CCMP",
        f"wpa_passphrase={passphrase}",
        "",
    ])


def dnsmasq_conf(iface: str, first: str, last: str, leasefile: str) -> str:
    # DHCP only (port=0: no DNS) and no default gateway: the phone keeps its
    # mobile data for the internet and reaches the owl on the on-link subnet.
    return "\n".join([
        f"interface={iface}",
        "bind-interfaces",
        "port=0",
        f"dhcp-range={first},{last},255.255.255.0,12h",
        "dhcp-option=3",
        "dhcp-option=6",
        f"dhcp-leasefile={leasefile}",
        "",
    ])


class Hotspot:
    def __init__(self, config: dict, sh: Callable = None, spawn: Callable = None,
                 clock: Callable[[], float] = time.monotonic, rundir: str = "/run/robot-owl-hotspot"):
        cfg = (config or {}).get("hotspot", {}) or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.iface = str(cfg.get("interface", "wlan0"))
        self.ap_iface = str(cfg.get("ap_interface", "uap0"))
        self.ssid = str(cfg.get("ssid", "Hubert"))
        self.passphrase_file = str(cfg.get("passphrase_file", "/etc/robot-owl/hotspot.psk"))
        self.address = str(cfg.get("address", "10.42.0.1"))
        self.dhcp_first = str(cfg.get("dhcp_first", "10.42.0.10"))
        self.dhcp_last = str(cfg.get("dhcp_last", "10.42.0.50"))
        self.channel = int(cfg.get("channel", 6))
        self.country = str(cfg.get("country", "DE"))
        self.wait_s = float(cfg.get("wait_s", 90))
        self.poll_s = float(cfg.get("poll_s", 5))
        self._sh = sh or self._run
        self._spawn = spawn or self._popen
        self._clock = clock
        self.rundir = rundir

        self.state = CLIENT
        self._last_ok = self._clock()      # last time wlan0 had a link
        self._procs: List = []

    # --- system access (replaced in tests) -----------------------------------
    @staticmethod
    def _run(cmd: List[str]) -> str:
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return r.stdout

    @staticmethod
    def _popen(cmd: List[str]):
        return subprocess.Popen(cmd)

    # --- observations ----------------------------------------------------------
    def client_connected(self) -> bool:
        """Associated AND holding an IPv4 address - either alone is not a link."""
        status = self._sh(["wpa_cli", "-i", self.iface, "status"])
        if "wpa_state=COMPLETED" not in status:
            return False
        addr = self._sh(["ip", "-4", "-o", "addr", "show", "dev", self.iface, "scope", "global"])
        return " inet " in f" {addr}"

    def stations(self) -> int:
        # hostapd's own list: `iw station dump` on the AIC8800 showed 0 with an
        # iPhone associated (2026-11-11).
        out = self._sh(["hostapd_cli", "-p", self._ctrl_dir(), "-i", self.ap_iface, "list_sta"])
        return sum(1 for line in out.splitlines() if line.strip().count(":") == 5)

    def _ctrl_dir(self) -> str:
        return os.path.join(self.rundir, "ctrl")

    def _client_channel(self) -> int:
        """The AP must share the radio's channel while wlan0 is associated."""
        for line in self._sh(["iw", "dev", self.iface, "info"]).splitlines():
            parts = line.split()
            if parts[:1] == ["channel"] and len(parts) > 1 and parts[1].isdigit():
                return int(parts[1])
        return self.channel

    # --- transitions -----------------------------------------------------------
    def start_ap(self) -> bool:
        passphrase = read_passphrase(self.passphrase_file)
        if passphrase is None:
            logger.error("No valid passphrase in %s (8..63 chars) - refusing to open an "
                         "unprotected hotspot", self.passphrase_file)
            self._last_ok = self._clock()   # ask again after another wait_s
            return False
        try:
            with open(f"/sys/class/net/{self.iface}/address") as f:
                mac = ap_mac(f.read())
        except OSError:
            mac = None
        channel = self._client_channel()
        logger.warning("No WLAN for %.0f s - opening hotspot '%s' on %s (channel %d) at %s",
                       self.wait_s, self.ssid, self.ap_iface, channel, self.address)
        self._sh(["iw", "dev", self.ap_iface, "del"])          # leftover from a crash
        self._sh(["iw", "dev", self.iface, "interface", "add", self.ap_iface, "type", "__ap"])
        if mac:
            self._sh(["ip", "link", "set", self.ap_iface, "address", mac])
        self._sh(["ip", "addr", "add", f"{self.address}/24", "dev", self.ap_iface])
        os.makedirs(self.rundir, mode=0o700, exist_ok=True)
        hconf = os.path.join(self.rundir, "hostapd.conf")
        dconf = os.path.join(self.rundir, "dnsmasq.conf")
        fd = os.open(hconf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(hostapd_conf(self.ap_iface, self.ssid, passphrase, channel, self.country,
                                 self._ctrl_dir()))
        with open(dconf, "w") as f:
            f.write(dnsmasq_conf(self.ap_iface, self.dhcp_first, self.dhcp_last,
                                 os.path.join(self.rundir, "leases")))
        self._procs = [self._spawn(["hostapd", hconf]),
                       self._spawn(["dnsmasq", "--keep-in-foreground", f"--conf-file={dconf}"])]
        self.state = AP
        return True

    def stop_ap(self) -> None:
        for p in self._procs:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:  # noqa: BLE001 - the interface must go regardless
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass
        self._procs = []
        self._sh(["iw", "dev", self.ap_iface, "del"])
        self.state = CLIENT
        self._last_ok = self._clock()

    # --- the state machine -----------------------------------------------------
    def step(self) -> None:
        now = self._clock()
        connected = self.client_connected()
        if connected:
            self._last_ok = now
        if self.state == CLIENT:
            if not connected and now - self._last_ok >= self.wait_s:
                self.start_ap()
            return
        # AP
        if any(p.poll() is not None for p in self._procs):
            logger.error("hostapd/dnsmasq exited - closing the hotspot")
            self.stop_ap()
            return
        if connected and self.stations() == 0:
            logger.info("Known WLAN is back and no phone on the hotspot - closing it")
            self.stop_ap()

    def run(self) -> None:
        stop = []
        signal.signal(signal.SIGTERM, lambda *_: stop.append(True))
        self._sh(["iw", "dev", self.ap_iface, "del"])   # leftover from a crash
        try:
            while not stop:
                self.step()
                time.sleep(self.poll_s)
        finally:
            if self.state == AP:
                self.stop_ap()


def test_ap(hotspot: "Hotspot", seconds: float) -> int:
    """Open the AP NOW (next to whatever wlan0 is doing) and close it after
    `seconds`. For a bench test; stop the service first."""
    if not hotspot.start_ap():
        return 1
    try:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if any(p.poll() is not None for p in hotspot._procs):
                logger.error("hostapd/dnsmasq exited during the test")
                break
            logger.info("test: AP up, %d phone(s) connected, %.0f s left",
                        hotspot.stations(), end - time.monotonic())
            time.sleep(10)
    finally:
        hotspot.stop_ap()
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = list(sys.argv[1:] if argv is None else argv)
    test_s = None
    if args[:1] == ["--test"]:
        test_s = float(args[1])
        args = args[2:]
    with open(args[0] if args else "config.yaml") as f:
        config = yaml.safe_load(f) or {}
    hotspot = Hotspot(config)
    if test_s is not None:
        return test_ap(hotspot, test_s)
    if not hotspot.enabled:
        logger.info("hotspot.enabled is false - nothing to do")
        return 0
    hotspot.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
