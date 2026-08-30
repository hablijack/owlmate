# SPEC-014: Speech — hearing the user and reacting

Status: partial — implemented and unit-tested; never run against a real mic
Verified: 2026-08-27 — 47 tests (pipeline, ASR path, auto-sleep, navigation
          triggers), all against synthetic audio and a stubbed Whisper
Depends on: 001, 012, 013

## Intent

The owl hears a short utterance, transcribes it offline on the RPi, and reacts
with an eye expression and an owl-call. It is a reaction engine, not a
conversation: there is no dialogue state, no intent model, no reply. German.

Absorbed from `SPEECH_RECOGNITION_PLAN.md` (deleted 2026-08-27), which was the
pre-implementation design.

## Requirements

* **R-014.1** ASR runs on the RPi. The ESP32 is not involved and needs no
  firmware change (it has no mic and no headroom).
* **R-014.2** Reactions use the *existing* override protocol. Speech must not
  add a second way to drive the owl (inherits R-001.2).
* **R-014.3** The owl must not react to ambient sound — the TV, or its own amp.
* **R-014.4** A sleeping owl is not woken by ambient words, only by a clearly
  addressed wake keyword.
* **R-014.5** Nothing here may block the brain's startup or its read loop.
* **R-014.6** Keywords, clusters and reactions are configuration, not code.

## Decisions

**faster-whisper (CTranslate2), not openai-whisper.** No torch on a Pi. The
default is the **`small` model with `compute_type="int8"`** — the combination
faster-whisper itself recommends for a Pi 4, and the reason int8 is hardcoded
in `speech.py` while the size stays configuration (R-014.6). `tiny`/`base` cut
latency and RAM and remain valid values, at a real accuracy cost on German place
names. The default was `tiny` until 2026-08-30; nothing measured on this
hardware justified it, so the vendor recommendation wins until a measurement on
the Pi says otherwise.

**A three-part gate, not always-on transcription** (R-014.3). ASR runs only when
the owl is awake *and* a face is in frame *and* the mic energy passes an RMS VAD
threshold. Reacting to the television, or to the owl's own hoot coming back
through the mic, is the failure mode this exists to prevent. `REACTIVE_STATES`
is `{idle, detecting, interacting}` — `SLEEPING` and `UPDATE` are excluded
deliberately (the latter because the owl is on an isolated SoftAP and cannot be
reached at all).

**One worker thread owns the whole audio path.** sounddevice's callback fills a
queue; the worker drains it, runs the VAD, transcribes, and performs every
reaction. Nothing touches the foreground serial read loop (R-014.5).

**Heavy imports are lazy.** `faster_whisper` and `sounddevice` are imported
inside `start()`, so the brain runs on a machine with no mic and no ASR engine
installed — and a machine with `speech.enabled: false` never imports them at
all. `main.py` wraps the whole start in try/except (R-014.5, R-012.3).

**Navigation triggers are matched BEFORE the keyword clusters.** "Wie komme ich
zum Zoo" contains "wie", which belongs to the `question` cluster; matched in the
other order, every navigation request would be answered with a puzzled face
instead of a bearing. Order is load-bearing, not incidental.

**A navigation stop keyword is exempt from both the face gate and the cooldown**,
and works even while the owl is asleep. "Stop" must always work — including from
the sofa, and including when the face has momentarily left frame.

**Whole-word keyword matching, not substring.** The design sketch used
`keyword in text`, which over-matches badly in German: "wie" fires inside
"völlig", "was" inside "irgendwas". Single words anchor on word boundaries;
multi-word phrases still match as substrings.

**Wake-on-speech is opt-in and separate.** The firmware self-wakes on a face or
a tap; the RPi adds only "wake on an explicit `wake_keywords` entry" (R-014.4).
An empty list means speech never wakes the owl.

**Everything German lives in `config.yaml`** — keywords, clusters, reactions,
nav triggers, stop words (R-014.6). Adding a reaction is a config edit.

## Verified facts

* The whole pipeline is tested on a dev machine with no mic, no PortAudio and no
  faster-whisper: `tests/stubs.py` feeds synthetic audio chunks and a mocked
  model returning canned segments. See SPEECH-adjacent detail in SPEC-012.
* Reaction expressions configured in `config.yaml` are validated against the
  firmware's `NAMES[]` by `tests/test_expressions.py` — a typo there would
  otherwise render as `neutral` and look like a broken eye (SPEC-004).
* Auto-sleep and speech interact deliberately: hearing the user is an activity
  trigger that resets the inactivity timer, and navigation being active blocks
  auto-sleep entirely.

## Falsified

* **"Substring keyword matching is fine."** Not in German. "wie" inside
  "völlig", "was" inside "irgendwas" — the owl reacts to words nobody said.
* **"The owl should react to whatever it hears."** It then reacts to the
  television, and to its own owl-call returning through the mic. Hence the
  awake + face + energy gate.
* **"Phase" is an unambiguous word in this repo.** The speech feature's
  implementation phases (1–4: VAD → ASR → reactions → auto-sleep) are cited in
  `rpi-brain/brain/` comments and are **not** the BACKLOG's Steps. Two meanings
  for one word cost time on 2026-08-27; the BACKLOG's task list was renamed to
  "Step" for this reason.

## Acceptance

1. `cd rpi-brain && python3 tests/run_tests.py` — the speech pipeline, ASR,
   auto-sleep and navigation-trigger tests pass.
2. With `speech.enabled: false` (the default), no audio module is imported and
   the brain starts normally.
3. On a machine with no mic, enabling speech logs a failure and the brain keeps
   running (R-014.5).

## Open

* **Never run against a real microphone.** Every test uses synthetic audio and a
  stubbed model. Unknown until it runs on the Pi: real VAD threshold, actual
  transcription latency for a 2.5 s window on Pi hardware, and whether the RMS
  gate needs tuning for the room.
* **Whether `small`/int8 is fast enough on the Pi 4 — unmeasured.** It is the
  vendor's recommendation for that board, not a number taken here; transcription
  latency for a 2.5 s window on real hardware is still unknown, and `cooldown_s`
  (4.5 s) may need raising if a transcription outlasts it. Drop to `base` or
  `tiny` if the owl feels sluggish, and record the measurement here. Accuracy on
  German place names interacts with SPEC-013's fuzzy name matching, which exists
  partly to absorb ASR error.
* No dialogue and no confirmation turn, by design. Revisit only if misheard
  navigation targets turn out to be common in practice.
