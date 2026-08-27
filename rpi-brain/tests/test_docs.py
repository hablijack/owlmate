"""Runs tools/check_docs.py as part of the normal suite.

The point of putting it here is that nobody has to remember. `run_tests.py` is
already the thing you run before committing, so doc drift fails there rather
than being noticed months later by whoever trips over it.

Skips when the repo root is not present — setup.sh installs only rpi-brain/ onto
the Pi, so a deployed brain has no specs/ or BACKLOG.md to check.

Why a program and not a rule in AGENTS.md: on 2026-08-27 every doc defect found
in this repo was sitting in a file that declared itself authoritative, including
AGENTS.md itself (it claimed the IMU calibration was present in NVS; it had been
erased, and two other documents said so). Instructions get skipped. Checks fail.
"""

import os
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHECKER = os.path.join(REPO_ROOT, "tools", "check_docs.py")


@unittest.skipUnless(os.path.exists(CHECKER), "repo root not present (deployed Pi)")
class TestDocsMatchCode(unittest.TestCase):
    def test_check_docs_passes(self):
        r = subprocess.run([sys.executable, CHECKER],
                           cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(
            r.returncode, 0,
            "tools/check_docs.py found the docs and the code disagreeing.\n"
            "config.h and the code win — fix the doc, do not update both.\n\n"
            f"{r.stdout}\n{r.stderr}")


if __name__ == "__main__":
    unittest.main()
