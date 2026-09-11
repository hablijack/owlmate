#!/usr/bin/env python3
"""Fail when the docs no longer match the code.

WHY THIS EXISTS, and why it is a program rather than a rule in AGENTS.md:

"Remember to update the specs" is an instruction, and instructions get skipped.
On 2026-08-27 every single doc defect found in this repo had been sitting in a
file that *told* the reader it was authoritative:

  * WIRING.md had the two eye CS lines swapped and put the vibration sensor on
    the right eye's DC pin. It is the document you follow with a soldering iron.
  * AGENTS.md said the IMU calibration was present in NVS. It had been erased
    weeks earlier, and specs/006 plus BACKLOG both said so. AGENTS.md is the
    file an agent reads first.
  * SPEC-010 listed three telemetry fields the Orange Pi never parsed, and called its
    dataclasses frozen while they were mutable.
  * README claimed Arduino core 2.0.17 / IDF 4.4 long after the move to
    pioarduino 3.3.11 / IDF 5.5, and described the SW420 as debounced when it is
    an edge-counting ISR — a documented falsified belief.

None of that was laziness; all of it was drift that nothing could detect. The
only guards that actually held this repo together are the ones that FAIL:
`gen_expressions.py --check`, the drift tests, the `static_assert` on the
expression tables. So doc consistency gets the same treatment.

The rule this encodes: **a change to the code should break the build until the
docs catch up.** Every check below either passes or names the file and the fix.

Run:
    python3 tools/check_docs.py           # from the repo root
    python3 tools/check_docs.py -v        # list every check as it runs

It also runs inside the Orange Pi suite (orangepi-brain/tests/test_docs.py), so a normal
`run_tests.py` catches drift without anyone remembering to invoke this.
"""

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPECS = os.path.join(ROOT, "specs")
CONFIG_H = os.path.join(ROOT, "esp32-s3-sense", "include", "config.h")
EYES_CPP = os.path.join(ROOT, "esp32-s3-sense", "lib", "Eyes", "Eyes.cpp")
BACKLOG = os.path.join(ROOT, "BACKLOG.md")

# Docs that are steering files: they claim to be authoritative, so they are the
# ones worth checking. Generated files and history are excluded.
STEERING = ["AGENTS.md", "README.md", "BACKLOG.md", "WIRING.md"]

failures = []
checks_run = 0


def check(name, ok, detail=""):
    global checks_run
    checks_run += 1
    if ok:
        return True
    failures.append(f"{name}\n    {detail}" if detail else name)
    return False


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def steering_texts():
    return {f: read(os.path.join(ROOT, f)) for f in STEERING
            if os.path.exists(os.path.join(ROOT, f))}


def config_macros():
    """Every `#define NAME value` in config.h, as {name: raw value string}."""
    out = {}
    for m in re.finditer(r"^#define\s+([A-Z_0-9]+)\s+(\S+)", read(CONFIG_H), re.M):
        out[m.group(1)] = m.group(2)
    return out


# ---------------------------------------------------------------------------
# 1. specs/ and its index must agree, in both directions
# ---------------------------------------------------------------------------
def check_spec_index():
    index = read(os.path.join(SPECS, "000-index.spec"))
    on_disk = sorted(f for f in os.listdir(SPECS)
                     if f.endswith(".spec") and f != "000-index.spec")

    for f in on_disk:
        check(f"spec {f} is listed in 000-index.spec", f in index,
              f"add a row for it to the Index table in specs/000-index.spec")

    for linked in re.findall(r"\]\((\d{3}-[a-z0-9-]+\.spec)\)", index):
        check(f"indexed spec {linked} exists on disk",
              os.path.exists(os.path.join(SPECS, linked)),
              f"specs/000-index.spec links {linked}, which is not there")


# ---------------------------------------------------------------------------
# 2. Every spec keeps the documented skeleton
# ---------------------------------------------------------------------------
def check_spec_skeleton():
    required = ["## Intent", "## Requirements", "## Decisions",
                "## Falsified", "## Open"]
    for f in sorted(os.listdir(SPECS)):
        if not f.endswith(".spec") or f == "000-index.spec":
            continue
        text = read(os.path.join(SPECS, f))
        missing = [s for s in required if s not in text]
        check(f"spec {f} has the required sections", not missing,
              f"missing: {', '.join(missing)} (see specs/000-index.spec Format)")
        check(f"spec {f} declares a Status", text.startswith("# SPEC-")
              and "\nStatus:" in text[:400],
              "the first lines must be '# SPEC-NNN: Title' then 'Status: ...'")


# ---------------------------------------------------------------------------
# 3. Every `Step N` reference resolves to a BACKLOG heading
# ---------------------------------------------------------------------------
def check_backlog_steps():
    backlog = read(BACKLOG)
    defined = set(re.findall(r"^### Step (\d+)", backlog, re.M))
    check("BACKLOG defines at least one Step", bool(defined),
          "the top section should be a '### Step N — ...' list")

    for fname, text in steering_texts().items():
        for n in sorted(set(re.findall(r"\bStep (\d+)\b", text))):
            check(f"{fname} references Step {n}, which is defined", n in defined,
                  f"BACKLOG.md has no '### Step {n}' heading")

    # The Phase/Step collision is a real trap: orangepi-brain code uses "Phase 1..4"
    # for the speech feature's implementation phases. The backlog must not
    # reintroduce "Phase N" for its task list.
    body = backlog.split("# Robot Owl — Backlog")[0]
    stray = [m for m in re.findall(r"### Phase \d+", body)]
    check("BACKLOG's task list uses Step, not Phase", not stray,
          "'Phase N' means the SPEECH feature's phases in orangepi-brain/ comments; "
          "do not reuse it for backlog tasks (see specs/014-speech.spec)")


# ---------------------------------------------------------------------------
# 4. Paths named in the steering docs must exist
# ---------------------------------------------------------------------------
def check_referenced_paths():
    # Backtick-quoted things that look like repo paths.
    pattern = re.compile(
        r"`((?:[A-Za-z0-9_.\-]+/)+[A-Za-z0-9_.\-]+\.(?:md|spec|py|cpp|h|ini|yaml|csv|json|sh|html))`")
    for fname, text in steering_texts().items():
        # BACKLOG below its top section is a HISTORY log: it legitimately names
        # files that were deleted later (src/pinmap.cpp, src/tfttest.cpp, the
        # plan docs). Only its current section makes claims about today.
        if fname == "BACKLOG.md":
            text = text.split("# Robot Owl — Backlog")[0]
        for path in sorted(set(pattern.findall(text))):
            if path.startswith(("http", "~", "/")) or "*" in path:
                continue
            # Docs write paths relative to the subproject under discussion:
            # "brain/audio.py" means orangepi-brain/brain/audio.py, "src/main.cpp"
            # means esp32-s3-sense/src/main.cpp. Accept any of the roots.
            found = any(os.path.exists(os.path.join(ROOT, base, path))
                        for base in ("", "orangepi-brain", "esp32-s3-sense"))
            check(f"{fname} references {path}, which exists", found,
                   f"{fname} names {path}; not found at the repo root, under "
                   "orangepi-brain/ or under esp32-s3-sense/ (renamed or deleted?)")


# ---------------------------------------------------------------------------
# 5. Numbers quoted in the docs must match config.h
#
# This is the "if a spec contradicts config.h, the header wins" rule made
# executable. Change a constant and the docs still claiming the old value fail.
# ---------------------------------------------------------------------------
# The macro must be NAMED in the doc, and the expected rendering must appear
# within WINDOW characters of that name. A plain "does the file contain '16 MHz'"
# test is satisfied by coincidence: AGENTS.md and specs/003 both mention "27 MHz"
# in a Falsified note about why the clock is NOT 27 MHz, so changing the macro to
# 27 MHz passed a containment check while every doc still claimed 16.
WINDOW = 240

CONSTANT_CLAIMS = [
    # (macro, render the expected doc text as a regex, files that must agree)
    ("LCD_SPI_FREQ", lambda v: rf"{int(v) // 1_000_000}\s*MHz",
     ["AGENTS.md", "specs/003-display-bus.spec"]),
    ("TELEMETRY_INTERVAL_MS", lambda v: rf"{int(v)}\s*ms",
     ["specs/010-serial-protocol.spec"]),
    ("IMU_HEADING_OFFSET_DEG", lambda v: re.escape(v.rstrip("f")),
     ["AGENTS.md", "specs/006-imu-orientation.spec", "specs/013-navigation.spec"]),
    ("NAV_TIMEOUT_MS", lambda v: rf"{int(v) // 1000}\s*s",
     ["specs/013-navigation.spec"]),
]


def check_constants():
    macros = config_macros()
    for macro, render, files in CONSTANT_CLAIMS:
        if macro not in macros:
            check(f"config.h still defines {macro}", False,
                  f"{macro} is referenced by tools/check_docs.py but no longer "
                  "exists in config.h; update CONSTANT_CLAIMS")
            continue
        expected = render(macros[macro])
        for f in files:
            path = os.path.join(ROOT, f)
            if not os.path.exists(path):
                continue
            text = read(path)
            mentions = [m.end() for m in re.finditer(re.escape(macro), text)]
            if not check(f"{f} mentions {macro}", bool(mentions),
                         f"{f} is expected to document {macro} and does not name it"):
                continue
            near = any(re.search(expected, text[at:at + WINDOW]) for at in mentions)
            check(f"{f} states {macro} as /{expected}/ next to its name", near,
                  f"config.h has {macro} = {macros[macro]}; {f} names the macro "
                  f"but does not state that value within {WINDOW} chars. "
                  "config.h wins — fix the doc.")


# ---------------------------------------------------------------------------
# 5b. WIRING.md's pin tables must match config.h, row by row
#
# THE check. WIRING.md is the document followed with a soldering iron, and on
# 2026-08-27 it had the two eye CS lines swapped, the left DC on a pin the
# firmware does not use, and the vibration sensor on D3 — which is the RIGHT
# eye's DC. Following it would have shorted them together. One table was
# corrected by hand and a SECOND copy further down the same file was missed,
# which is precisely why this is a program and not a proofread.
# ---------------------------------------------------------------------------
# The XIAO silkscreen labels differ from the GPIO numbers, and confusing them has
# broken this project before. Authoritative list, from config.h's own comment.
D_TO_GPIO = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 43, 7: 44, 8: 7, 9: 8, 10: 9}

# Longest first: "LCD CS — Left" must win over a bare "LCD CS".
SIGNALS = [
    ("LCD CS", "Left", "LCD_CS_L"), ("LCD CS", "Right", "LCD_CS_R"),
    ("LCD DC", "Left", "LCD_DC_L"), ("LCD DC", "Right", "LCD_DC_R"),
    ("LCD SPI SCK", None, "LCD_SCK"), ("LCD SPI MOSI", None, "LCD_MOSI"),
    ("LCD RST", None, "LCD_RST"),
    ("Vibration", None, "VIBRATION_PIN"),
    ("I2C SDA", None, "I2C_SDA"), ("I2C SCL", None, "I2C_SCL"),
    ("Camera XCLK", None, "CAM_PIN_XCLK"),
]


def check_wiring_tables():
    path = os.path.join(ROOT, "WIRING.md")
    if not os.path.exists(path):
        return
    macros = config_macros()
    rows = 0
    for lineno, line in enumerate(read(path).splitlines(), 1):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        pad = re.match(r"\*\*D(\d+)\*\*$", cells[0])
        if not pad:
            continue
        gpio_nums = re.findall(r"\d+", cells[1])
        if not gpio_nums:
            continue
        rows += 1
        pad_n, gpio = int(pad.group(1)), int(gpio_nums[0])
        where = f"WIRING.md:{lineno} (D{pad_n})"

        # 1. The pad -> GPIO translation itself.
        check(f"{where} pad/GPIO pair is right",
              D_TO_GPIO.get(pad_n) == gpio,
              f"D{pad_n} is GPIO {D_TO_GPIO.get(pad_n)}, the table says GPIO {gpio}")

        # 2. The signal on that pad, against config.h.
        purpose = " ".join(cells[2:])
        plain = purpose.replace("**", "")
        for name, side, macro in SIGNALS:
            if name not in plain:
                continue
            if side and side not in plain:
                continue
            if macro not in macros:
                continue
            expected = int(macros[macro])
            check(f"{where} {name}{' ' + side if side else ''} -> GPIO {expected}",
                  gpio == expected,
                  f"config.h has {macro} = {expected}, but WIRING.md puts "
                  f"{name}{' ' + side if side else ''} on GPIO {gpio} "
                  f"(D{pad_n}). config.h is authoritative — fix WIRING.md. "
                  "Following this table with a soldering iron would mis-wire "
                  "the owl.")
            break
    check("WIRING.md has pin-table rows to check", rows > 0,
          "no '| **D<n>** | GPIO <n> | ...' rows found; did the table format change?")


# ---------------------------------------------------------------------------
# 6. The expression count quoted in the docs must match NAMES[]
# ---------------------------------------------------------------------------
def check_expression_count():
    block = re.search(r"NAMES\[\]\s*=\s*\{(.*?)\};", read(EYES_CPP), re.S)
    if not check("Eyes.cpp still has a NAMES[] table", block is not None):
        return
    names = re.findall(r'"([a-z_0-9]+)"', block.group(1))
    n = len(names)
    # "update" and "error" are system faces the UI does not offer, so the
    # selectable count is a legitimate thing for a doc to quote as well.
    allowed = {n, n - 2}
    for f in ("AGENTS.md", "README.md", "specs/004-eye-expressions.spec"):
        path = os.path.join(ROOT, f)
        if not os.path.exists(path):
            continue
        text = read(path)
        quoted = set(int(x) for x in re.findall(r"(\d+) expressions", text))
        bad = {q for q in quoted if q not in allowed}
        check(f"{f} quotes the expression count as {n} (or {n - 2} selectable)", not bad,
              f"NAMES[] has {n} entries ({n - 2} selectable) but {f} says "
              f"{sorted(bad)}. "
               "Regenerate with orangepi-brain/tools/gen_expressions.py and fix the prose.")


# ---------------------------------------------------------------------------
# 7. The test count quoted in the docs must match the real suite
# ---------------------------------------------------------------------------
def check_test_count():
    total = 0
    tests_dir = os.path.join(ROOT, "orangepi-brain", "tests")
    for f in os.listdir(tests_dir):
        if f.startswith("test_") and f.endswith(".py"):
            total += len(re.findall(r"^\s+def test_", read(os.path.join(tests_dir, f)), re.M))
    check("the Orange Pi suite has tests to count", total > 0)

    for fname, text in steering_texts().items():
        quoted = set(int(x) for x in re.findall(r"(\d+) tests\b", text))
        # Historical statements ("was 104", "104 tests at the time") are fine;
        # only flag a number presented as the current total.
        bad = {q for q in quoted if q != total and q > 50}
        check(f"{fname} quotes the test count as {total}", not bad,
              f"the suite has {total} tests; {fname} says {sorted(bad)}. "
              "Either update it or phrase it as historical.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    for fn in (check_spec_index, check_spec_skeleton, check_backlog_steps,
               check_referenced_paths, check_constants,
               check_wiring_tables,
               check_expression_count, check_test_count):
        fn()

    if args.verbose:
        print(f"ran {checks_run} checks")

    if failures:
        print(f"\ncheck_docs: {len(failures)} of {checks_run} checks FAILED\n")
        for f in failures:
            print(f"  ✗ {f}")
        print("\nThe docs and the code disagree. config.h and the code win; "
              "fix the doc.\n")
        return 1

    print(f"check_docs: {checks_run} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
