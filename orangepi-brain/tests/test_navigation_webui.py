"""
Web UI navigation-endpoint tests (real brain.WebUI, faked Flask via stubs).

The fake Flask (tests/stubs.py) records each @app.route view in
app.view_functions keyed by the view's function name, and request.get_json
returns {} -- so these tests call the view functions directly with a stubbed
request body (supervisor.request patched) and assert on the returned dict.

The serial + supervisor are fakes (FakeSerial / the shared FakeSupervisor from
stubs.py), so no hardware is needed. The locations store is a temp file.
"""

import os
import re
import shutil
import sys
import tempfile
import types
import unittest

from stubs import install_stub_modules, FakeSerial, FakeSupervisor, make_config

install_stub_modules()

import brain.web_ui as webui_mod  # noqa: E402
from brain.web_ui import WebUI  # noqa: E402

# The Flask `request` the views call is the module-level object imported into
# brain.web_ui (NOT an attribute of the supervisor). The fake's get_json returns
# {}, so to exercise a POST endpoint we patch webui_mod.request.get_json to
# return the test's body for the duration of the call.
_orig_get_json = webui_mod.request.get_json


def make_webui(nav_enabled=True, locations_file=None):
    serial = FakeSerial()
    sup = FakeSupervisor(serial, last=None)
    cfg = make_config()
    cfg["navigation"]["enabled"] = nav_enabled
    if locations_file is not None:
        cfg["navigation"]["locations_file"] = locations_file
    # Attach the real locations store + navigation controller to the supervisor,
    # exactly as brain.supervisor.Supervisor does.
    from brain.locations import LocationsStore
    from brain.navigation import Navigation
    sup.locations = LocationsStore(cfg["navigation"]["locations_file"] or None)
    sup.navigation = Navigation(serial, sup, sup.locations, cfg)
    ui = WebUI(serial, sup, host="127.0.0.1", port=0)
    return ui, serial, sup


class TestWebUINavEndpoints(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="owl-webui-nav-")
        self.path = os.path.join(self._dir, "locations.json")

    def _call(self, ui, sup, view_name, body):
        # Point the module-level Flask request at this test's body for the call.
        webui_mod.request.get_json = lambda **k: body
        try:
            return ui.app.view_functions[view_name]()
        finally:
            webui_mod.request.get_json = _orig_get_json

    # ------------------------------------------------------------------
    # /api/locations
    # ------------------------------------------------------------------
    def test_locations_list_empty_then_add(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        # Empty at first.
        d = self._call(ui, sup, "api_locations", {})
        self.assertTrue(d["ok"])
        self.assertEqual(d["locations"], [])
        # Add a place.
        d = self._call(ui, sup, "api_locations_add", {"name": "home", "lat": 48.0, "lon": 11.0})
        self.assertTrue(d["ok"])
        d = self._call(ui, sup, "api_locations", {})
        self.assertEqual(len(d["locations"]), 1)
        self.assertEqual(d["locations"][0]["name"], "home")

    def test_locations_add_requires_fields(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        self.assertFalse(self._call(ui, sup, "api_locations_add", {"name": "x"})["ok"])
        self.assertFalse(self._call(ui, sup, "api_locations_add", {"lat": 48.0, "lon": 11.0})["ok"])
        self.assertFalse(self._call(ui, sup, "api_locations_add", {})["ok"])

    def test_locations_add_rejects_bad_coords(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        self.assertFalse(self._call(ui, sup, "api_locations_add",
                                     {"name": "x", "lat": 999.0, "lon": 11.0})["ok"])

    def test_locations_delete(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        self._call(ui, sup, "api_locations_add", {"name": "home", "lat": 48.0, "lon": 11.0})
        self.assertTrue(self._call(ui, sup, "api_locations_delete", {"name": "home"})["ok"])
        self.assertEqual(self._call(ui, sup, "api_locations", {})["locations"], [])

    def test_locations_disabled(self):
        ui, serial, sup = make_webui(nav_enabled=False, locations_file=self.path)
        d = self._call(ui, sup, "api_locations", {})
        self.assertFalse(d["ok"])
        self.assertIn("error", d)

    # ------------------------------------------------------------------
    # /api/nav/start + /api/nav/stop
    # ------------------------------------------------------------------
    def test_nav_start_unknown_place(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        d = self._call(ui, sup, "api_nav_start", {"name": "nowhere"})
        self.assertFalse(d["ok"])
        self.assertFalse(d["status"]["active"])

    def test_nav_start_known_place_activates(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        # A place to the north of the (48, 11) owl, and a fresh fix, so the
        # controller aims immediately and goes active.
        sup.locations.add("home", 49.0, 11.0)
        from brain.serial_handler import (Telemetry, FaceDetection, VibrationData,
                                          IMUData, GPSData, UpdateMode)
        sup.last = Telemetry(timestamp=100.0, state="interacting", firmware="test",
                             face=FaceDetection(detected=True),
                             vibration=VibrationData(detected=False),
                             imu=IMUData(yaw=0.0, calibrated=True),
                             gps=GPSData(valid=True, latitude=48.0, longitude=11.0, satellites=8),
                             update=UpdateMode(active=False), servos=[0.0] * 5)
        d = self._call(ui, sup, "api_nav_start", {"name": "home"})
        self.assertTrue(d["ok"])
        self.assertTrue(d["status"]["active"])
        self.assertEqual(d["status"]["target"], "home")

    def test_nav_stop(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        sup.locations.add("home", 49.0, 11.0)
        from brain.serial_handler import (Telemetry, FaceDetection, VibrationData,
                                          IMUData, GPSData, UpdateMode)
        sup.last = Telemetry(timestamp=100.0, state="interacting", firmware="test",
                             face=FaceDetection(detected=True),
                             vibration=VibrationData(detected=False),
                             imu=IMUData(yaw=0.0, calibrated=True),
                             gps=GPSData(valid=True, latitude=48.0, longitude=11.0, satellites=8),
                             update=UpdateMode(active=False), servos=[0.0] * 5)
        self._call(ui, sup, "api_nav_start", {"name": "home"})
        self.assertTrue(sup.navigation.is_active())
        d = self._call(ui, sup, "api_nav_stop", {})
        self.assertTrue(d["ok"])
        self.assertFalse(d["status"]["active"])

    def test_nav_disabled(self):
        ui, serial, sup = make_webui(nav_enabled=False, locations_file=self.path)
        self.assertFalse(self._call(ui, sup, "api_nav_start", {"name": "x"})["ok"])
        self.assertFalse(self._call(ui, sup, "api_nav_stop", {})["ok"])

    # ------------------------------------------------------------------
    # telemetry payload exposes the nav status
    # ------------------------------------------------------------------
    def test_telemetry_includes_navigation(self):
        ui, serial, sup = make_webui(locations_file=self.path)
        from brain.serial_handler import (Telemetry, FaceDetection, VibrationData,
                                          IMUData, GPSData, UpdateMode)
        sup.last = Telemetry(timestamp=100.0, state="interacting", firmware="test",
                             face=FaceDetection(detected=True),
                             vibration=VibrationData(detected=False),
                             imu=IMUData(yaw=0.0, calibrated=True),
                             gps=GPSData(valid=True, latitude=48.0, longitude=11.0, satellites=8),
                             update=UpdateMode(active=False), servos=[0.0] * 5)
        d = ui.app.view_functions["api_telemetry"]()
        self.assertIn("navigation", d)
        self.assertFalse(d["navigation"]["active"])

    def test_telemetry_exposes_the_diagnostic_fields(self):
        """The web UI must surface the fields that explain a refusal to aim.

        Navigation silently declines to aim while imu.calibrated is false. Until
        2026-08-27 the OrangePi did not even parse the per-sensor counters, so the
        page could not say WHY -- and the figure-8 that fixes it cannot be
        performed while staring at a serial log.
        """
        ui, serial, sup = make_webui(locations_file=self.path)
        from brain.serial_handler import (Telemetry, FaceDetection, VibrationData,
                                          IMUData, IMUCalibration, GPSData,
                                          UpdateMode)
        sup.last = Telemetry(
            timestamp=100.0, state="idle", firmware="test",
            face=FaceDetection(detected=True, total=57),
            vibration=VibrationData(detected=False, count=2, pulses=3252),
            imu=IMUData(yaw=210.0, calibrated=False,
                        cal=IMUCalibration(axes=1, heading_ok=False,
                                           restored=True)),
            gps=GPSData(valid=True, latitude=48.0, longitude=11.0, satellites=8),
            update=UpdateMode(active=False), servos=[0.0] * 5)
        d = ui.app.view_functions["api_telemetry"]()

        self.assertEqual(d["imu"]["cal"], {"axes": 1, "heading_ok": False,
                                          "restored": True})
        self.assertFalse(d["imu"]["calibrated"])
        self.assertAlmostEqual(d["imu"]["yaw"], 210.0)
        self.assertTrue(d["gps"]["valid"])
        self.assertEqual(d["gps"]["satellites"], 8)
        self.assertEqual(d["vibration"]["pulses"], 3252)
        self.assertEqual(d["face"]["total"], 57)


class TestTemplateIsPackaged(unittest.TestCase):
    """The page source lives in brain/templates/index.html, not in web_ui.py.

    It moved out of a ~430-line Python raw string on 2026-08-27. The risk that
    replaced "a stray backslash breaks the brain" is "the file is not deployed":
    setup.sh rsyncs the tree wholesale so it works today, but an added --exclude
    or a packaging change would turn the web UI into an ImportError at startup,
    on the OrangePi, where nobody is watching.
    """

    def test_template_file_exists_next_to_the_module(self):
        from brain.web_ui import TEMPLATE_PATH
        self.assertTrue(os.path.isfile(TEMPLATE_PATH),
                        f"template not found at {TEMPLATE_PATH}")

    def test_template_path_is_inside_the_package(self):
        # Must be resolved relative to the module, not the process CWD: the brain
        # runs from a systemd unit whose working directory is not the repo.
        import brain.web_ui as w
        pkg_dir = os.path.dirname(os.path.abspath(w.__file__))
        self.assertTrue(os.path.abspath(w.TEMPLATE_PATH).startswith(pkg_dir),
                        f"{w.TEMPLATE_PATH} is outside {pkg_dir}")

    def test_loaded_template_looks_like_the_page(self):
        from brain.web_ui import TEMPLATE
        self.assertTrue(TEMPLATE.startswith("<!doctype html>"))
        self.assertIn("</html>", TEMPLATE)
        self.assertGreater(len(TEMPLATE), 10000)

    def test_jinja_placeholders_survived_the_move(self):
        # The view passes expressions/sounds/sound_labels; if the {{ }} markers
        # were mangled the buttons would silently render empty.
        from brain.web_ui import TEMPLATE
        for var in ("expressions", "sounds", "sound_labels"):
            self.assertRegex(TEMPLATE, r"\{\{\s*" + var,
                             f"missing Jinja placeholder for {var}")

    def test_missing_template_raises_a_diagnosable_error(self):
        """A packaging miss must be readable in an OrangePi log.

        main.py imports WebUI inside a try/except so this degrades to "running
        without the web UI" instead of killing the brain -- which only helps if
        the message explains itself.
        """
        import brain.web_ui as w
        real = w.TEMPLATE_PATH
        w.TEMPLATE_PATH = os.path.join(os.path.dirname(real), "no-such-file.html")
        try:
            with self.assertRaises(RuntimeError) as cm:
                w._load_template()
        finally:
            w.TEMPLATE_PATH = real
        msg = str(cm.exception)
        self.assertIn("no-such-file.html", msg)
        self.assertIn("rsync", msg)          # names the likely cause

    def test_no_leftover_template_literal_in_the_module(self):
        # Guards against someone pasting the HTML back into the .py.
        import brain.web_ui as w
        with open(w.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("<!doctype html>", src)


class TestMapPickerDegradesOffline(unittest.TestCase):
    """The place picker must stay usable with no internet.

    Leaflet and its tiles are fetched by the BROWSER from two remote hosts
    (unpkg.com for the library, tile.openstreetmap.org for the tiles). The owl is
    a mobile, battery-powered device, so "no internet on the viewing phone" is a
    normal condition, not an edge case -- and saving destinations is how
    navigation gets anywhere.

    These are template assertions, not DOM tests: the project has no JS test
    harness, and a cheap check that the guard exists is worth more than none.
    """

    def test_template_guards_on_leaflet_being_absent(self):
        from brain.web_ui import TEMPLATE
        self.assertIn("typeof L === 'undefined'", TEMPLATE)

    def test_offline_message_names_the_manual_path(self):
        from brain.web_ui import TEMPLATE
        self.assertIn("Map unavailable", TEMPLATE)
        # It must tell the user what to do instead, not just that it failed.
        self.assertIn("type the lat/lon", TEMPLATE)

    def test_manual_lat_lon_inputs_exist_independently_of_the_map(self):
        # The fallback is only real if these fields are always present.
        from brain.web_ui import TEMPLATE
        self.assertIn('id="place-lat"', TEMPLATE)
        self.assertIn('id="place-lon"', TEMPLATE)

    def test_offline_message_contains_no_html_entity(self):
        """textContent does not decode entities -- one there renders literally.

        The offline message used to read "... (offline?) &mdash; type the ...",
        putting those eight characters on screen at the exact moment the user was
        already wondering why the map was missing.

        Deliberately narrow: it checks THIS string, not every textContent
        assignment in the template. A general version needs a JS statement
        parser to be correct -- two attempts here were wrong in instructive
        ways. The first scanned line by line, but the property and its string
        literal sit on separate lines, so it never saw them together and passed
        against the live bug. The second treated any ";" as the statement end --
        and "&mdash;" contains one, so the match stopped inside the entity and
        the captured text no longer held a complete entity to find. The third
        anchored on end-of-line semicolons and then over-captured across
        statements into a neighbouring innerHTML call. Entities are legitimate
        in markup and in innerHTML, so distinguishing them properly is a real
        parsing job, and not one worth doing in this test.
        """
        from brain.web_ui import TEMPLATE
        line = next(l for l in TEMPLATE.splitlines()
                    if "Map unavailable (offline?)" in l)
        self.assertIsNone(
            re.search(r"&[a-zA-Z]+;|&#\d+;", line),
            "HTML entity in a textContent string; it will render literally:\n"
            f"{line.strip()}")
        # And it must still contain the dash it is meant to have.
        self.assertIn("\\u2014", line)

    def test_cdn_tags_pin_integrity_hashes(self):
        # The library is third-party code executing in the operator's browser on
        # a page with no authentication. SRI is what stops a compromised CDN
        # substituting it. Matched per TAG, not per line: these tags wrap, so the
        # integrity attribute sits on the continuation line.
        from brain.web_ui import TEMPLATE
        tags = re.findall(r"<(?:script|link)\b[^>]*>", TEMPLATE, re.S)
        remote = [t for t in tags if "//" in t and "unpkg.com" in t]
        self.assertTrue(remote, "expected the Leaflet CDN tags to be present")
        for tag in remote:
            self.assertIn("integrity=", tag,
                          f"unpinned CDN asset:\n{tag}")
            self.assertIn("crossorigin", tag,
                          f"SRI needs crossorigin to be enforced:\n{tag}")


class TestTelemetryPayloadCannotDrift(unittest.TestCase):
    """Every Telemetry field must reach /api/telemetry.

    Not a protocol requirement -- R-010.5 governs PARSING what the firmware
    sends, and the page is entitled to render a subset. This guards the weaker
    property that made the hand-written payload worth replacing: whatever the
    OrangePi has parsed is offered to the page, so adding a firmware field never
    needs a third edit here. The old 50-line version had already drifted, and it
    drifted silently -- an absent key is `undefined` in JS, which renders as an
    empty span rather than an error.
    """

    def _payload(self, telemetry=None):
        """The payload as the HTTP layer sees it.

        Deliberately through app.view_functions, NOT through the private
        _telemetry_payload helper: against the previous hand-written
        implementation, importing that helper raised ImportError, so all five of
        these "failed" without ever evaluating an assertion. A guard that cannot
        run against the defect it describes is not a guard (SPEC-012 R-012.5).
        Going through the route makes them meaningful for either implementation.
        """
        from brain.serial_handler import Telemetry
        ui, serial, sup = make_webui(locations_file=self.path)
        sup.last = Telemetry() if telemetry is None else telemetry
        return ui.app.view_functions["api_telemetry"]()

    def test_every_top_level_telemetry_field_is_present(self):
        import dataclasses
        from brain.serial_handler import Telemetry

        payload = self._payload()
        # The two fields whose wire name differs from the attribute name, and
        # the one deliberately renamed to resolve a collision with the OrangePi
        # controller's own `navigation` status. See _telemetry_payload.
        aliases = {"eye_expression": "eye", "uptime_ms": "uptime",
                   "navigation": "navigation_esp32"}
        missing = [f.name for f in dataclasses.fields(Telemetry)
                   if aliases.get(f.name, f.name) not in payload]
        self.assertEqual([], missing,
                         "Telemetry fields absent from /api/telemetry: %s" % missing)

    def test_the_face_diagnostics_reach_the_page(self):
        """The four fields that exist to make an invisible failure visible.

        total/attempts separate "detects badly" from "rarely gets to run";
        capture_ms/infer_ms separate waiting on the camera from computing. All
        four were parsed by the OrangePi and then dropped on the way to the page.
        """
        face = self._payload()["face"]
        for key in ("total", "attempts", "capture_ms", "infer_ms", "stack_free"):
            self.assertIn(key, face, key)

    def test_loop_hz_and_uptime_reach_the_page(self):
        payload = self._payload()
        self.assertIn("loop_hz", payload)
        self.assertIn("uptime", payload)
        self.assertNotIn("uptime_ms", payload)   # wire name, not the attribute

    def test_softap_password_is_not_published(self):
        """The endpoint has no authentication; don't hand out the AP password.

        Everything else in `update` is fine to show -- it is what the operator
        needs to reach /update -- but the password is not added to an
        unauthenticated route as a side effect of a refactor.
        """
        payload = self._payload()
        self.assertIn("update", payload)
        self.assertNotIn("password", payload["update"])
        self.assertIn("ssid", payload["update"])

    def test_controller_navigation_wins_the_navigation_key(self):
        """`navigation` must stay the OrangePi controller's status, not the firmware's.

        The page's Navigate card reads target/bearing/distance_m/aim off
        t.navigation. The firmware sends a NavigationState of its own under the
        same name; it is published as `navigation_esp32` so the two cannot be
        confused for one another.
        """
        from brain.serial_handler import Telemetry, NavigationState
        d = self._payload(Telemetry(
            state="navigating",
            navigation=NavigationState(active=True, angle=-12.5)))
        self.assertIn("target", d["navigation"])          # controller's shape
        self.assertEqual(-12.5, d["navigation_esp32"]["angle"])  # firmware's echo

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="owl-webui-payload-")
        self.path = os.path.join(self.dir, "locations.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
