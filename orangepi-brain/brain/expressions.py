"""Eye expressions the firmware understands.

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
    "neutral",
    "blink_high",
    "blink_low",
    "happy",
    "glee",
    "sad_down",
    "sad_up",
    "worried",
    "focused",
    "annoyed",
    "surprised",
    "skeptic",
    "bored",
    "unimpressed",
    "sleepy",
    "suspicious",
    "squint",
    "angry",
    "furious",
    "scared",
    "awe",
    "sleeping",
    "searching",
    "detecting",
    "update",
    "error",
)

# Driven by the firmware's own state machine, not by a mood: UPDATE is the OTA
# spinner and ERROR the fault cross. Both bypass the eye-shape table, so neither
# belongs on a palette of moods to pick from.
SYSTEM = (
    "update",
    "error",
)

# What a user may sensibly ask for (ALL minus SYSTEM). This is what the web UI
# offers.
SELECTABLE = (
    "neutral",
    "blink_high",
    "blink_low",
    "happy",
    "glee",
    "sad_down",
    "sad_up",
    "worried",
    "focused",
    "annoyed",
    "surprised",
    "skeptic",
    "bored",
    "unimpressed",
    "sleepy",
    "suspicious",
    "squint",
    "angry",
    "furious",
    "scared",
    "awe",
    "sleeping",
    "searching",
    "detecting",
)


def is_valid(name: str) -> bool:
    """True if the firmware knows this expression name."""
    return name in ALL
