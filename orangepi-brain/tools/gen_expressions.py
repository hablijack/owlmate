#!/usr/bin/env python3
"""Generate brain/expressions.py from the firmware's NAMES[] table.

The firmware owns the eye-expression vocabulary: NAMES[] in
esp32-s3-sense/lib/Eyes/Eyes.cpp is the single source for the protocol strings
(Eyes::nameOf() / parseName() both read it, and it is static_assert-ed against
the EyeExpression enum). Everything on the OrangePi side is a mirror.

Mirrors drift. Measured on 2026-08-27: the firmware had 26 names,
web_ui.EXPRESSIONS had 23, and config.yaml's `expressions:` block had 24 --
`sleeping` was in one and missing from the other for no stated reason. An
unknown name is not an error either: the firmware silently falls back to
NEUTRAL, so a drifted entry shows up as "an eye that refuses to change".

So the list is generated instead, following the precedent of
esp32-s3-sense/tools/preview_eyes.py, which parses SHAPES[] out of the same file
so the contact sheet cannot drift from the firmware.

Usage (from orangepi-brain/):
    python3 tools/gen_expressions.py            # rewrite brain/expressions.py
    python3 tools/gen_expressions.py --check    # exit 1 if it is out of date

The generated file is COMMITTED, so an OrangePi deployment never needs the
firmware tree. tests/test_expressions.py runs --check as a real test, which is
what actually prevents the drift from coming back.
"""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ORANGEPI_ROOT = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(ORANGEPI_ROOT)

EYES_CPP = os.path.join(REPO_ROOT, "esp32-s3-sense", "lib", "Eyes", "Eyes.cpp")
OUT_PY = os.path.join(ORANGEPI_ROOT, "brain", "expressions.py")

# Names the firmware drives from its own state machine rather than from a mood:
# UPDATE is the OTA spinner, ERROR the fault cross. Neither is an eye shape (both
# bypass the SHAPES[] table entirely), so neither belongs on a mood palette.
SYSTEM_NAMES = ("update", "error")


def parse_names(path=EYES_CPP):
    """Return the NAMES[] entries, in firmware order."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()

    m = re.search(r"NAMES\[\]\s*=\s*\{(.*?)\};", src, re.S)
    if not m:
        raise SystemExit(f"{path}: could not find the NAMES[] table")

    names = re.findall(r'"([a-z_0-9]+)"', m.group(1))
    if not names:
        raise SystemExit(f"{path}: NAMES[] matched but contained no strings")

    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise SystemExit(f"{path}: duplicate names in NAMES[]: {sorted(dupes)}")
    return names


def render(names):
    known = set(names)
    missing = [n for n in SYSTEM_NAMES if n not in known]
    if missing:
        raise SystemExit(
            f"SYSTEM_NAMES lists {missing}, which are not in NAMES[] any more. "
            "Update gen_expressions.py.")

    selectable = [n for n in names if n not in SYSTEM_NAMES]
    quoted = lambda seq: "\n".join(f'    "{n}",' for n in seq)  # noqa: E731

    return f'''"""Eye expressions the firmware understands.

GENERATED FILE -- do not edit by hand.
Regenerate with:  python3 tools/gen_expressions.py

Source of truth: NAMES[] in esp32-s3-sense/lib/Eyes/Eyes.cpp, which the firmware
static_asserts against its EyeExpression enum. This file exists so the OrangePi does
not keep a hand-maintained second copy; it used to, and it drifted.

An expression the firmware does not recognise is NOT rejected -- it silently
renders as "neutral". That makes a typo here look like a hardware fault, which
is why this is generated and drift-checked in the test suite
(tests/test_expressions.py).
"""

# Every name in the protocol, in firmware order.
ALL = (
{quoted(names)}
)

# Driven by the firmware's own state machine, not by a mood: UPDATE is the OTA
# spinner and ERROR the fault cross. Both bypass the eye-shape table, so neither
# belongs on a palette of moods to pick from.
SYSTEM = (
{quoted(SYSTEM_NAMES)}
)

# What a user may sensibly ask for (ALL minus SYSTEM). This is what the web UI
# offers.
SELECTABLE = (
{quoted(selectable)}
)


def is_valid(name: str) -> bool:
    """True if the firmware knows this expression name."""
    return name in ALL
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the generated file is out of date")
    args = ap.parse_args()

    wanted = render(parse_names())

    if args.check:
        try:
            with open(OUT_PY, encoding="utf-8") as fh:
                current = fh.read()
        except FileNotFoundError:
            print(f"{OUT_PY} does not exist; run tools/gen_expressions.py")
            return 1
        if current != wanted:
            print(f"{OUT_PY} is out of date with Eyes.cpp NAMES[]; "
                  "run: python3 tools/gen_expressions.py")
            return 1
        print(f"{os.path.relpath(OUT_PY, ORANGEPI_ROOT)} is up to date")
        return 0

    with open(OUT_PY, "w", encoding="utf-8") as fh:
        fh.write(wanted)
    names = parse_names()
    print(f"wrote {os.path.relpath(OUT_PY, ORANGEPI_ROOT)}: "
          f"{len(names)} names, {len(names) - len(SYSTEM_NAMES)} selectable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
