"""Fallback hotspot state machine (brain/hotspot.py), no WLAN hardware."""
import os
import stat
import tempfile
import unittest
from pathlib import Path

from stubs import install_stub_modules
install_stub_modules()
from brain.hotspot import AP, CLIENT, Hotspot, ap_mac, dnsmasq_conf, hostapd_conf, read_passphrase  # noqa: E402

PASS = "3xample!Pass#word"   # not the real one; that lives only on the Orange Pi


class FakeProc:
    def __init__(self, cmd):
        self.cmd = cmd
        self.code = None
        self.terminated = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = 0

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.code = -9


class FakeSystem:
    """wlan0 as seen through wpa_cli / ip / iw, plus every command issued."""

    def __init__(self):
        self.connected = True
        self.stations = 0
        self.commands = []
        self.procs = []

    def sh(self, cmd):
        self.commands.append(cmd)
        if cmd[0] == "wpa_cli":
            return "ssid=Olymp\nwpa_state=COMPLETED\n" if self.connected else "wpa_state=SCANNING\n"
        if cmd[0] == "ip" and cmd[1] == "-4":
            return "2: wlan0    inet 192.168.178.127/24 brd ...\n" if self.connected else ""
        if cmd[0] == "hostapd_cli":
            return "".join(f"aa:bb:cc:dd:ee:0{i}\n" for i in range(self.stations))
        if cmd[:4] == ["iw", "dev", "wlan0", "info"]:
            return "\ttype managed\n\tchannel 11 (2462 MHz), width: 20 MHz\n" if self.connected else "\ttype managed\n"
        return ""

    def spawn(self, cmd):
        p = FakeProc(cmd)
        self.procs.append(p)
        return p

    def ran(self, *prefix):
        return any(c[:len(prefix)] == list(prefix) for c in self.commands)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TestHotspot(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.psk = Path(self.dir.name) / "hotspot.psk"
        self.psk.write_text(PASS + "\n")
        self.sys = FakeSystem()
        self.clock = Clock()
        cfg = {"hotspot": {"enabled": True, "passphrase_file": str(self.psk), "wait_s": 90}}
        self.h = Hotspot(cfg, sh=self.sys.sh, spawn=self.sys.spawn, clock=self.clock,
                         rundir=str(Path(self.dir.name) / "run"))

    def tearDown(self):
        self.dir.cleanup()

    def advance(self, seconds, step=5):
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.clock.t += step
            self.h.step()

    def wlan0_touched(self):
        """Anything that could take the client link down. Never allowed."""
        return any(c[0] in ("ifdown", "ifup") or
                   (c[0] == "ip" and "wlan0" in c and c[1] in ("addr", "link")) or
                   c[:3] == ["iw", "dev", "wlan0"] and c[3:4] == ["del"]
                   for c in self.sys.commands)

    def test_working_wlan_is_never_touched(self):
        self.advance(3600)
        self.assertEqual(self.h.state, CLIENT)
        self.assertEqual(self.sys.procs, [])
        self.assertFalse(self.wlan0_touched())

    def test_short_dropout_does_not_open_the_ap(self):
        self.sys.connected = False
        self.advance(85)
        self.assertEqual(self.h.state, CLIENT)
        self.sys.connected = True
        self.advance(200)
        self.assertEqual(self.h.state, CLIENT)

    def test_no_wlan_opens_a_protected_ap_on_uap0_and_leaves_wlan0_alone(self):
        self.sys.connected = False
        self.advance(95)
        self.assertEqual(self.h.state, AP)
        self.assertTrue(self.sys.ran("iw", "dev", "wlan0", "interface", "add", "uap0", "type", "__ap"))
        self.assertTrue(self.sys.ran("ip", "addr", "add", "10.42.0.1/24", "dev", "uap0"))
        self.assertFalse(self.wlan0_touched())
        self.assertEqual([p.cmd[0] for p in self.sys.procs], ["hostapd", "dnsmasq"])
        conf = Path(self.sys.procs[0].cmd[1])
        text = conf.read_text()
        self.assertIn("interface=uap0\n", text)
        self.assertIn("ssid=Hubert\n", text)
        self.assertIn("wpa=2\n", text)
        self.assertIn(f"wpa_passphrase={PASS}\n", text)
        self.assertEqual(stat.S_IMODE(os.stat(conf).st_mode), 0o600)

    def test_associated_without_address_is_not_a_link(self):
        real = self.sys.sh

        def no_ip(cmd):
            return "" if cmd[:2] == ["ip", "-4"] else real(cmd)
        self.h._sh = no_ip
        self.advance(95)
        self.assertEqual(self.h.state, AP)

    def test_no_passphrase_means_no_ap(self):
        self.psk.write_text("short\n")
        self.sys.connected = False
        self.advance(300)
        self.assertEqual(self.h.state, CLIENT)
        self.assertEqual(self.sys.procs, [])

    def test_ap_closes_when_the_known_wlan_is_back_and_nobody_uses_it(self):
        self.sys.connected = False
        self.advance(95)
        self.sys.connected = True          # the owl came home
        self.advance(5)
        self.assertEqual(self.h.state, CLIENT)
        self.assertTrue(self.sys.ran("iw", "dev", "uap0", "del"))
        self.assertTrue(all(p.terminated for p in self.sys.procs))
        self.advance(3600)
        self.assertEqual(self.h.state, CLIENT)

    def test_ap_stays_while_the_wlan_is_still_missing(self):
        self.sys.connected = False
        self.advance(3600)
        self.assertEqual(self.h.state, AP)
        self.assertEqual(len(self.sys.procs), 2)   # opened once, not re-opened

    def test_a_connected_phone_is_never_kicked_off(self):
        self.sys.connected = False
        self.advance(95)
        self.sys.stations = 1
        self.sys.connected = True
        self.advance(3600)
        self.assertEqual(self.h.state, AP)
        self.sys.stations = 0                       # phone leaves
        self.advance(5)
        self.assertEqual(self.h.state, CLIENT)

    def test_dead_hostapd_closes_the_ap(self):
        self.sys.connected = False
        self.advance(95)
        self.sys.procs[0].code = 1
        self.advance(5)
        self.assertEqual(self.h.state, CLIENT)
        self.assertTrue(self.sys.ran("iw", "dev", "uap0", "del"))

    def test_disabled_by_default(self):
        self.assertFalse(Hotspot({}).enabled)


class TestConfigText(unittest.TestCase):
    def test_passphrase_validation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "psk"
            for value, ok in [("1234567", False), ("12345678", True), ("x" * 63, True),
                              ("x" * 64, False), ("tab\there", False), (PASS, True)]:
                p.write_text(value + "\n")
                self.assertEqual(read_passphrase(str(p)) is not None, ok, value)
            self.assertIsNone(read_passphrase(str(Path(d) / "missing")))

    def test_ap_mac_is_locally_administered_and_distinct(self):
        self.assertEqual(ap_mac("bc:8b:73:a2:d0:f3\n"), "be:8b:73:a2:d0:f3")

    def test_dnsmasq_serves_dhcp_only_without_gateway(self):
        text = dnsmasq_conf("wlan0", "10.42.0.10", "10.42.0.50", "/run/x/leases")
        self.assertIn("port=0\n", text)
        self.assertIn("dhcp-option=3\n", text)
        self.assertIn("dhcp-range=10.42.0.10,10.42.0.50,255.255.255.0,12h\n", text)

    def test_hostapd_is_wpa2_ccmp(self):
        text = hostapd_conf("uap0", "Hubert", PASS, 6, "DE", "/run/x/ctrl")
        for line in ("wpa=2", "wpa_key_mgmt=WPA-PSK", "rsn_pairwise=CCMP", "country_code=DE"):
            self.assertIn(line + "\n", text)


if __name__ == "__main__":
    unittest.main()
