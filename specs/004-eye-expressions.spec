# SPEC-004: Eye expressions and the shape model

Status: implemented
Verified: 2026-08-26 — all 24 expressions set over serial, drawn, and echoed back
Depends on: 003

## Intent

The eyes carry the owl's whole emotional bandwidth. They are two 160 px round
panels behind small openings in a hand-built head, viewed from a metre or two
away. The design has to survive that.

## Requirements

* **R-004.1** Two colours only: black on white.
* **R-004.2** Every mood from the reference sheet is expressible, plus the states
  the owl needs that the sheet does not cover.
* **R-004.3** Adding or retuning a mood must not mean writing a drawing routine.
* **R-004.4** Expression names, shapes and the enum cannot drift apart silently.
* **R-004.6** The Orange Pi's copy of the name list is derived from the firmware's, not
  maintained alongside it. Any hand-kept mirror is a defect.
* **R-004.5** Asymmetric shapes must mirror between the eyes, so the pair leans
  toward the beak rather than both leaning the same way.

## Decisions

**Two colours, on purpose.** An earlier version had an iris colour, a specular
highlight and grey eyelids. At this size and distance that detail turned to
mush. `COLOR_BG` / `COLOR_INK` in `include/common.h` are the whole palette.
Do not reintroduce gradients or sparkles — this was a considered removal, not an
omission.

**One parametric renderer, not 21 drawing routines.** `Eyes::drawBlob()` renders
any mood from a row of the `SHAPES[]` table. The base form is a superellipse
(`|x/halfW|^n + |y/halfH|^n = 1`, `n = roundness/10`; 2.0 is a plain ellipse,
~2.6 gives the reference sheet's softly-squared blob) plus four modifiers:

| field | effect | used by |
|---|---|---|
| `topSag` | pushes the top edge down in the middle | annoyed, skeptic, bored, unimpressed |
| `botRise` | pushes the bottom edge up in the middle → crescent | happy, glee, squint, focused |
| `slantIn` | drops the top edge toward the **inner** corner | angry, furious |
| `slantOut` | drops the top edge toward the **outer** corner | worried, sad |
| `yOff` | shifts the shape vertically | blink high / low |
| `asymH` | shortens the right eye only | skeptic (deliberately lopsided) |

Retuning a mood means editing numbers in a table. That is the point.

**Rendering is column-wise.** For each x, the top and bottom edge are computed
independently and the span between them is filled. This is what makes crescents
and dome-down shapes fall out for free; a row-by-row rasteriser could not
express independent edge profiles.

**The slant convention is emotional-standard**: angry lowers the *inner* brow,
worried and sad lower the *outer* one. `drawBlob()` mirrors it via its
`mirrored` argument.

**Names come from one table.** `NAMES[]` backs both `Eyes::nameOf()` (telemetry's
`eye` field) and `Eyes::parseName()` (the `expression` command). `SHAPES[]`,
`NAMES[]` and `EyeExpression` are size-checked against `_COUNT` with
`static_assert`, so a mismatch is a build error rather than a wrong eye.

**The Orange Pi's list is generated, not mirrored** (R-004.6).
`orangepi-brain/tools/gen_expressions.py` parses `NAMES[]` out of `Eyes.cpp` and
writes `orangepi-brain/brain/expressions.py`, following the precedent of
`tools/preview_eyes.py`, which parses `SHAPES[]` from the same file so the
preview sheet cannot drift. The generated file is committed, so a deployed Pi
never needs the firmware tree; `tests/test_expressions.py` re-derives the list
independently and fails if the two diverge.

`SELECTABLE` (what the web UI offers) is every name minus `update` and `error`:
those two are driven by the firmware's own state machine, bypass the shape table
entirely, and are not moods anyone should pick from a palette.

**Three expressions bypass the table** because they are not eyes: `UPDATE` (a
rotating spinner), `ERROR` (a cross), `SLEEPING` (a single closed bar).
`SEARCHING` and `DETECTING` are aliases of `SUSPICIOUS` and `FOCUSED`.

**Blinks are background-coloured bars** closing in from top and bottom, so a
blink eats into whatever shape is underneath. Auto-blink gap is a random
2.5–6.5 s (`BLINK_GAP_MIN_MS` 2500 + `random(BLINK_GAP_JITTER_MS)` 4000, both in
`lib/Eyes/Eyes.cpp`) — irregular on purpose, because a regular blink reads as a
machine idling. This spec said "0.7–2.5 s" until 2026-08-30; that was the
original cadence, widened by `f79c96a` because it read as nervous.

**INTERACTING is two beats, not one held face** — a ~1 s `HAPPY` burst on entry
(`INTERACT_GREET_MS`), then `AWE` for as long as the owl keeps tracking.

Sustained `HAPPY` was the original choice and read as strange on hardware, for a
reason that is in the shape table rather than in taste. `HAPPY` carries
`botRise 62`, the deepest crescent of all 26 rows — next is `SQUINT` at 52, then
`GLEE` at 28 — and a deep crescent renders as a **closed** eye. INTERACTING is
precisely the state in which the owl follows a face, so the expression said "my
eyes are squeezed shut" while the behaviour said "I am watching you". Held flat
at peak amplitude for the whole state, it also stopped reading as an emotion at
all: affect is legible through *change*, and a constant maximum is a mask.

`AWE` is the sustained face because it is the only unused row that is fully
open — no `botRise`, no `topSag`, no slant.

**Its row was widened on 2026-08-30, because as drawn it was not a visible
change.** At `35×41` it covered only **+12 % more area than `NEUTRAL`** — a
difference you can find side by side and not across a room, which is the only
comparison that matters when someone is standing in front of the owl. It is now
`{42, 42, 32}`: **+40 % area**, and *round* where every other mood is a taller
oval, so it differs in shape as well as size. Roundness 3.2 is the boxiest
exponent in the table.

The size is bounded by the panel, not by taste. The farthest outline point of a
superellipse is its diagonal corner at `2^(-1/n)` of each half-axis, and the
gaze can push the whole blob `hypot(18, 12) = 21.6 px` off centre, so what has
to fit is *corner + deflection*. `NEUTRAL` reaches 61.6 px of the 80 px disc and
the shipped `SURPRISED` already reaches 68.9 px. The new `AWE` reaches **69.5 px**
— inside the envelope an owner-approved mood already occupies. Do not read the
"usable radius 65" note in `Eyes.cpp` as a hard limit; `SURPRISED` has always
exceeded it.

The burst is armed in `transitionTo()`, **not** in the INTERACTING branch of
`updateState()`. The branch runs every loop iteration (~60 Hz), so arming it
there would restart the flash forever and reproduce exactly the frozen grin it
replaces. `transitionTo()` returns early when the state is unchanged, so the
flash fires once per genuine entry.

Keeping `HAPPY` off the sustained face also keeps it **earnable**: it stays
available for the Orange Pi to send when speech recognises someone or the owl is
tapped. Spending peak joy on mere presence leaves nothing in reserve for when
something good actually happens.

## Verified facts

* All 24 expressions were set over serial, rendered, and echoed back in
  telemetry's `eye` field.
* Design confirmed by the owner against the reference sheet via a preview sheet
  generated by `tools/preview_eyes.py`, which parses `SHAPES[]` out of the C++
  and re-implements the identical maths — so the preview cannot drift from the
  firmware.
* An expression override lasts `EXPRESSION_OVERRIDE_MS` (3 s) before the state
  machine reclaims the eyes. Holding a mood means re-sending faster than that.
* **The mirrors had drifted, measured 2026-08-27**: firmware `NAMES[]` 26 names,
  `web_ui.EXPRESSIONS` 23, `config.yaml expressions:` 24. `sleeping` was present
  in one mirror and absent from the other with no stated reason. Nothing failed,
  because an unknown name renders as `neutral` and a *missing* one simply never
  appears in the UI.
* `config.yaml`'s 36-line `expressions:` block was read by **no code at all** —
  verified by grepping for every form of the key. It was pure duplication of the
  vocabulary, and it was one of the two copies that had drifted. Deleted; the
  only expression names that are genuinely configurable are those inside
  `speech.reactions`, which the test suite now validates against the firmware
  list.

## Falsified

* **The eyes never tracked a face, for three independent reasons at once**
  (all found and fixed 2026-08-29, each hidden by the others):
  1. **`gaze_x`/`gaze_y` overflowed to 2³².** `fb->width` is `size_t`, so
     `cx - fb->width / 2` was evaluated UNSIGNED and wrapped whenever the face
     sat left of centre. `setGaze()` clamped the result to +1.0, so the eyes
     snapped hard right instead of following. Present since the port. The tell
     was `gaze_y / gaze_x` being exactly 4/3 — the frame's aspect ratio — with a
     shared numerator of 4294967296.
  2. **The direction was inverted.** A camera records the person opposite
     mirrored: step right, appear left. Whether the camera in the head is *also*
     physically mirrored was never established, because `CAM_VFLIP`/`CAM_HMIRROR`
     were chosen for UPRIGHT faces and a left-right mirror does not show up in
     that test. Now `EYE_GAZE_SIGN_X`, determined on hardware, not derived.
  3. **The travel was two pixels.** `gaze_x` is normalised so that ±1 means "face
     at the very edge of frame" — where nobody stands. Measured with someone
     directly in front: ±0.34. Against 8 px of travel on a 160 px panel that is
     invisible. Now gain 2.5 and 18 px travel.

  Each of these alone would have looked like "tracking is broken"; together they
  masked each other. Fixing only the overflow still gave a 2 px twitch in the
  wrong direction.

* **`setExpression()`/`setGaze()` marking the frame dirty unconditionally**
  defeated the skip in `render()` entirely. `updateState()` calls both every loop
  iteration, almost always with an unchanged value, so every single iteration
  rebuilt both 160×160 frames — measured 2026-08-28 at 3 redraws per 3
  iterations while idle, pinning the loop at 4.4 Hz. Gating on a real change is
  safe because `render()` re-checks the visible state itself.

* **`fillCircle()` was drawing a quarter of every circle.** It walked one
  Bresenham octant and filled only the columns between `cx±r` and `cx±r/√2`, so
  every circle rendered as two crescents flanking a one-pixel centre line. This
  shipped unnoticed because the eyes had never actually been visible before.
  Replaced with a plain scanline fill — at r ≤ 80 the cost of doing it the
  obvious way is irrelevant.
* **`SLEEPY` was squashed twice**, once by its shape row and again by a
  hard-coded lid override in `currentOpenness()`. The override is gone.
* **`main.cpp` kept a second, hand-maintained name array** (`exprNames[]`)
  indexed by the enum. Every new expression would have shifted it and made
  telemetry report the wrong name. This is why names now come from one table.
* **"One source of truth in the firmware is enough."** Removing `exprNames[]`
  fixed the firmware and left two *more* copies on the Orange Pi, which then drifted
  in exactly the way the firmware fix had prevented. The lesson generalises: a
  vocabulary shared across a process boundary needs generation or a test, and
  "it is documented as a mirror" is not either of those. `NAMES[]` even carried
  a comment saying the Orange Pi mirrors lived in `web_ui.py` and `config.yaml` — the
  duplication was known, written down, and still drifted.
* **`Eyes::markDirty()` was public API nobody called.** Documented in
  `AGENTS.md` as the way to force a redraw "when state changes by some other
  route"; no such route existed. Removed 2026-08-27.

## Acceptance

1. `python3 tools/preview_eyes.py && open /tmp/eyes.html` — the shapes render and
   match the reference sheet.
2. Send each name from `NAMES[]` as an `expression` command; telemetry's `eye`
   field echoes the same name back.
3. A build with a deliberately mismatched table fails at compile time
   (`static_assert` against `EyeExpression::_COUNT`).
4. `cd orangepi-brain && python3 tools/gen_expressions.py --check` exits 0. Adding a
   name to `NAMES[]` without regenerating must fail this and two tests in
   `tests/test_expressions.py` — verified by doing exactly that.

## Open

* Blink smoothness was bounded by frame time, and the "~61 ms per eye" figure
  quoted here was arithmetic, not a measurement — it is 149.9 ms for both eyes.
  **Both suggested fixes have since been tested**: redrawing only the changed
  rows is implemented and works (SPEC-003); raising `LCD_SPI_FREQ` does nothing
  at all and must not be tried again.

* **Blink cadence is now 2.5–6.5 s** (`BLINK_GAP_MIN_MS`/`_JITTER_MS`), up from
  0.7–2.5 s. The old rate was roughly twice a human's and read as nervous on the
  assembled head. It is also the main driver of redraws, so it is a rendering
  cost as much as an aesthetic choice.

* **Gaze is unsmoothed.** The eyes follow the raw per-frame face position with no
  deadband and no filtering. That was harmless while detection was sporadic; at
  the 100 % hit rate now achieved (SPEC-009) it is likely to look twitchy.
  Adafruit's MEMENTO shoulder robot — a working face-tracking robot — uses a
  centre deadzone, a 2 px hysteresis and a proportional step of 0.4 rather than
  jumping to the target. Worth copying for both the eyes and the head servo.
