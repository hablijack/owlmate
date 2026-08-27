# SPEC-012: RPi brain internals — ownership, packaging, testing

Status: implemented
Verified: 2026-08-27 — 174 tests, `python3 rpi-brain/tests/run_tests.py`
Depends on: 001, 010

## Intent

SPEC-001 settles *what* the RPi is allowed to decide (policy and temporary
overrides, never behaviour). This spec covers how the supervisor process is put
together: which module owns which resource, what ships to the Pi, and what the
test suite is actually allowed to claim.

Written after a refactoring pass on 2026-08-27 which found that most defects on
this side were not logic errors but **ownership errors** — two modules reaching
for the same resource, or a fact written in three places.

## Requirements

* **R-012.1** Every resource has exactly one owning module. Other modules go
  through the owner, not around it.
* **R-012.2** A fact that exists in more than one place must be generated or
  test-guarded. "It is documented as a mirror" is neither.
* **R-012.3** Every optional subsystem degrades to "running without it"
  (inherits R-001.4). A missing dependency must not stop the brain.
* **R-012.4** A failure that will be read from a Pi log must name its likely
  cause, not only its symptom.
* **R-012.5** A test must be verified to fail against the defect it claims to
  cover. An unverified regression test is assumed to be broken.
* **R-012.6** The read loop runs on the foreground thread and must survive any
  single frame, callback or field.

## Decisions

**Ownership, one line each.** The pass that produced this spec fixed four
violations of R-012.1:

| Resource | Owner | Everyone else |
|---|---|---|
| The I2S amp (`Audio`) | `supervisor.py` | `supervisor.play_sound()` |
| "What state is the owl in?" | `supervisor.py` | `supervisor.current_state()` |
| Place names + matching | `locations.py` | `LocationsStore.match()` |
| The NDJSON wire format | `serial_handler.py` | the typed senders |

Before: `navigation.py` and `speech.py` each lifted an `audio` handle off the
supervisor with `getattr` and called `audio.play()` directly — three independent
paths to one amp. `speech.py` rebuilt the supervisor's state expression from two
of its attributes, making the supervisor's internal bookkeeping part of Speech's
contract. Fuzzy place-name matching (~45 lines plus a Levenshtein) sat private
inside `Speech`, so the web UI could not fuzzy-match at all although it works
with the same store. `Supervisor.sleep()` hand-rolled a raw NDJSON dict because
`SerialHandler` had `wake()` but no `sleep()`.

**The page is a file, not a string.** `brain/templates/index.html`, ~430 lines,
rendered with `render_template_string` from a module-level load. It was a Python
raw-string literal in `web_ui.py` until 2026-08-27 (which was 746 lines; it is
now ~360).

Not `render_template`: that depends on Flask's template-folder resolution, and
there is no Flask or Jinja2 on the machine the split was done on, so the path
could not be exercised even once. Feeding the file's source to the renderer
already proven in this deployment keeps the change to *where the bytes live*
rather than *how rendering works*. The Jinja syntax in the file is already
compatible — switching is a two-line change for whoever can verify it on a real
Pi.

The risk this trades into is packaging: "the file did not deploy" replaces "a
stray backslash broke the module". `setup.sh` rsyncs the tree wholesale so a new
package directory ships automatically, and `main.py` imports `WebUI` *inside* a
try/except so a missing template degrades to "running without the web UI"
(R-012.3). `_load_template()` raises a message naming deployment and
`rsync --exclude` rather than a bare `FileNotFoundError` (R-012.4).

**Test doubles are built from the real types where possible.** `FakeTelemetry`
in `tests/stubs.py` constructs the real frozen dataclasses. It used to be
hand-rolled `SimpleNamespace`s, i.e. a fourth mirror of the wire format, and it
had drifted: it broke the moment the seven dropped fields were parsed. A double
assembled from the real type cannot drift.

## Verified facts

* 174 tests, up from 104 before the pass. `tests/test_protocol.py` (39) and
  `tests/test_expressions.py` (11) are new; neither area had any coverage.
* `web_ui.py` 746 → ~360 lines; `brain/templates/index.html` 429 lines, verified
  byte-for-byte identical to the extracted literal (20,257 characters).
* The whole suite runs on a plain Mac with no Pi, mic, PortAudio, faster-whisper,
  **Flask, Jinja2 or PyYAML**. `tests/stubs.py` substitutes a module only when
  the real one is not importable. numpy is the single hard dependency.
* Consequence of the above worth knowing: `config.yaml` cannot be parsed by the
  test suite — the yaml stub returns `{}`. Validating it needs a real parser;
  `~/.platformio/penv/bin/python` has PyYAML 6.0.3 and was used for exactly that
  after editing the file.

## Falsified

* **"A regression test that passes proves the fix."** It proves nothing until it
  has been seen to fail. Three attempts at one small check were written during
  this pass and **two passed against the live bug**: a per-line scan for an HTML
  entity in a `textContent` assignment never matched, because the property and
  its string literal are on separate lines; the next version treated any `;` as
  the statement terminator, and `&mdash;` *contains* one, so the match stopped
  inside the entity and the captured text no longer held an entity to find. The
  third bit correctly but over-captured into a neighbouring `innerHTML` call and
  produced a false positive. Hence R-012.5, and hence the narrow final check.
  This is SPEC-011's "a diagnostic whose output cannot differ between the healthy
  and broken case is worse than none", one floor up: it applies to tests as much
  as to hardware probes.
* **"Importing a module in a test means the module is tested."** Seven test
  modules imported `serial_handler`, which reads as coverage in any grep or
  dependency graph. They imported the dataclasses to build fixtures;
  `parse_telemetry()` was never called. See SPEC-010.
* **"The stubs are just test scaffolding, they cannot hide a defect."**
  `FakeSerial` defined `sleep()` while the real `SerialHandler` did not — the
  double modelled the interface better than the class, and the gap survived
  because the supervisor worked around it with a raw dict. A double that is
  *ahead* of the real type is a design note nobody read.
* **"Nothing reads it, so it is harmless."** `config.yaml`'s 42-line
  `expressions:` block was dead configuration — and being dead did not make it
  inert, because it was also one of two copies of the expression vocabulary that
  had drifted from the firmware. Dead weight participates in duplication.

## Acceptance

1. `cd rpi-brain && python3 tests/run_tests.py` — 174 tests pass.
2. `grep -rn 'getattr(supervisor, "audio"' brain/` returns nothing, and
   `supervisor.py` is the only module touching `Audio` (R-012.1).
3. Deleting `brain/templates/index.html` makes the brain log
   "Web UI failed to start (continuing without it): web UI template missing …"
   and keep running (R-012.3, R-012.4).
4. Any new regression test has been run against the unfixed code and observed to
   fail (R-012.5).

## Open

* `[ ]` **Three supervisor test doubles.** `tests/stubs.py` `FakeSupervisor`
  plus a local `StubSupervisor` in each of `test_navigation.py` and
  `test_navigation_webui.py`. Adding `current_state()` and `play_sound()` meant
  editing all three, which is how it was noticed — a direct R-012.2 violation in
  the test suite itself. Consolidate into one.
* `[ ]` `render_template` instead of `render_template_string`, once someone can
  verify it on a deployment that has Flask installed.
* `[ ]` The diagnostic panel added to the web UI (calibration counters, GPS fix,
  pulse/hit totals) has only ever been fed synthetic frames. See SPEC-010 Open.
