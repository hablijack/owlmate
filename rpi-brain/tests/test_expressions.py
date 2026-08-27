"""Drift guards for the eye-expression vocabulary.

The firmware owns the vocabulary: NAMES[] in
esp32-s3-sense/lib/Eyes/Eyes.cpp. Everything on the RPi side is a mirror, and
mirrors drift -- measured on 2026-08-27 the firmware had 26 names,
web_ui.EXPRESSIONS had 23 and config.yaml carried a 42-line third copy (which
nothing read) with 24.

What makes drift expensive here is that it is SILENT: the firmware does not
reject an unknown expression name, it falls back to NEUTRAL. So a stale or
mistyped name presents as "the eye refuses to change" -- indistinguishable from
a rendering or hardware fault, which is exactly the kind of false lead this
project has lost days to before.

Generating the list removes the drift; these tests are what keep it removed.
"""

import os
import re
import subprocess
import sys
import unittest

from stubs import install_stub_modules

install_stub_modules()

from brain import expressions  # noqa: E402
from brain.web_ui import EXPRESSIONS  # noqa: E402

RPI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(RPI_ROOT)
EYES_CPP = os.path.join(REPO_ROOT, "esp32-s3-sense", "lib", "Eyes", "Eyes.cpp")
GENERATOR = os.path.join(RPI_ROOT, "tools", "gen_expressions.py")
CONFIG_YAML = os.path.join(RPI_ROOT, "config.yaml")

# The firmware tree is absent from a deployed Pi (setup.sh installs only
# rpi-brain/), so the firmware-comparing tests skip rather than fail there.
HAVE_FIRMWARE = os.path.exists(EYES_CPP)


class TestGeneratedFileIsCurrent(unittest.TestCase):
    """brain/expressions.py must still match the firmware it was generated from."""

    @unittest.skipUnless(HAVE_FIRMWARE, "firmware tree not present")
    def test_generator_check_passes(self):
        # The generator's own --check mode is the authoritative comparison; run
        # it so there is exactly one implementation of "are these in sync".
        r = subprocess.run([sys.executable, GENERATOR, "--check"],
                           cwd=RPI_ROOT, capture_output=True, text=True)
        self.assertEqual(
            r.returncode, 0,
            "brain/expressions.py is out of date with Eyes.cpp NAMES[].\n"
            "Run: python3 tools/gen_expressions.py\n"
            f"{r.stdout}{r.stderr}")

    @unittest.skipUnless(HAVE_FIRMWARE, "firmware tree not present")
    def test_all_matches_firmware_names_exactly(self):
        # Independent of the generator: re-parse the C++ here, so a bug in the
        # generator cannot make both sides agree on something wrong.
        with open(EYES_CPP, encoding="utf-8") as fh:
            src = fh.read()
        block = re.search(r"NAMES\[\]\s*=\s*\{(.*?)\};", src, re.S)
        self.assertIsNotNone(block, "NAMES[] not found in Eyes.cpp")
        firmware = re.findall(r'"([a-z_0-9]+)"', block.group(1))
        self.assertEqual(list(expressions.ALL), firmware)

    @unittest.skipUnless(HAVE_FIRMWARE, "firmware tree not present")
    def test_firmware_count_matches_the_enum(self):
        # Eyes.h/.cpp static_assert NAMES[] against EyeExpression::_COUNT, so the
        # table cannot be short. Assert we read the whole thing, not a prefix.
        with open(EYES_CPP, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("static_assert", src)
        self.assertGreaterEqual(len(expressions.ALL), 20)


class TestListsAgree(unittest.TestCase):
    """The derived lists must stay consistent with ALL."""

    def test_selectable_is_all_minus_system(self):
        self.assertEqual(list(expressions.SELECTABLE),
                         [n for n in expressions.ALL
                          if n not in expressions.SYSTEM])

    def test_system_names_are_real(self):
        for name in expressions.SYSTEM:
            self.assertIn(name, expressions.ALL)

    def test_web_ui_offers_exactly_the_selectable_set(self):
        # web_ui used to keep its own hand-written copy. If someone reintroduces
        # one, this fails.
        self.assertEqual(EXPRESSIONS, list(expressions.SELECTABLE))

    def test_web_ui_does_not_offer_system_faces(self):
        # UPDATE is the OTA spinner and ERROR the fault cross; neither is a mood
        # and neither goes through the eye-shape table.
        for name in expressions.SYSTEM:
            self.assertNotIn(name, EXPRESSIONS)

    def test_neutral_is_present(self):
        # The firmware's fallback. If this ever left the list, every unknown
        # name would still render it while the UI could not ask for it.
        self.assertIn("neutral", expressions.SELECTABLE)

    def test_is_valid(self):
        self.assertTrue(expressions.is_valid("happy"))
        self.assertTrue(expressions.is_valid("error"))       # real, just not selectable
        self.assertFalse(expressions.is_valid("hapy"))       # typo
        self.assertFalse(expressions.is_valid(""))
        self.assertFalse(expressions.is_valid("HAPPY"))      # names are lowercase


class TestConfigExpressionNames(unittest.TestCase):
    """Every expression name configured in config.yaml must be one the firmware knows.

    These are the names that ARE meant to be edited by hand (speech.reactions),
    so they are the ones a typo can realistically reach. Parsed with a regex
    rather than yaml: the test suite runs without pyyaml installed.
    """

    @unittest.skipUnless(os.path.exists(CONFIG_YAML), "config.yaml not present")
    def test_configured_expressions_are_known(self):
        with open(CONFIG_YAML, encoding="utf-8") as fh:
            lines = fh.readlines()

        found = []
        for lineno, line in enumerate(lines, 1):
            code = line.split("#", 1)[0]          # ignore comments
            for name in re.findall(r"\bexpression:\s*([A-Za-z_0-9]+)", code):
                found.append((lineno, name))

        self.assertTrue(found, "no `expression:` entries found in config.yaml")
        for lineno, name in found:
            self.assertTrue(
                expressions.is_valid(name),
                f"config.yaml:{lineno} uses expression {name!r}, which the "
                f"firmware does not know -- it would silently render as "
                f"'neutral'. Valid names: {', '.join(expressions.ALL)}")

    @unittest.skipUnless(os.path.exists(CONFIG_YAML), "config.yaml not present")
    def test_dead_expressions_block_is_not_reintroduced(self):
        # A 42-line `expressions:` mapping used to sit at the top level and was
        # read by nothing, while being a third copy of the vocabulary.
        with open(CONFIG_YAML, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                self.assertFalse(
                    re.match(r"^expressions:\s*$", line),
                    f"config.yaml:{lineno} reintroduces a top-level "
                    "`expressions:` block. Nothing reads it; the list is "
                    "generated into brain/expressions.py.")


if __name__ == "__main__":
    unittest.main()
