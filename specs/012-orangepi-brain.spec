# SPEC-012: Orange Pi brain internals — ownership, packaging, testing

Status: implemented
Verified: 2026-08-27 — 174 tests, `python3 orangepi-brain/tests/run_tests.py`
Depends on: 001, 010

## Intent

SPEC-001 settles *what* the Orange Pi is allowed to decide (policy and temporary
overrides, never behaviour). This spec covers how the supervisor process is put
together: which module owns which resource, what ships to the Orange Pi, and what the
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
* **R-012.4** A failure that will be read from an Orange Pi log must name its likely
  cause, not only its symptom.
* **R-012.5** A test must be verified to fail against the defect it claims to
  cover. An unverified regression test is assumed to be broken.
* **R-012.6** The read loop runs on the foreground thread and must survive any
  single frame, callback or field.

## Decisions

**Explicit USB-disabled maintenance mode.** `main.py --no-serial` starts the
Web UI/supervisor without calling either `SerialHandler.connect()` or
`read_loop()` (the latter reconnects automatically). The disconnected sender
returns false, so Web UI control requests cannot move hardware. The foreground
thread only sleeps; there is no simulated telemetry presented as real data.
This allows provisioning a closed owl with unknown mechanical limits without
opening USB, which can reboot the XIAO and center servos. It does NOT make a
host reboot or the firmware's own autonomous behavior mechanically safe.
A regression test is verified against an accidental `connect()` in this mode.

**Real Flask contexts in web tests when Flask is installed.** `call_web_view`
uses `test_request_context()` and decodes the response with `get_json()`. With
no Flask it keeps the existing minimal stub. Direct view calls without context
worked only on the Mac's stubbed environment, not on a provisioned Orange Pi;
unit tests must work with either dependency environment.

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

**One supervisor test double.** `tests/stubs.py` `FakeSupervisor` is the only
one, as of 2026-08-31. There were three — it plus a local `StubSupervisor` in
each of `test_navigation.py` and `test_navigation_webui.py` — so every method
added to `Supervisor` had to be mirrored in three files, a direct R-012.2
violation inside the test suite. They had already drifted: only the web-UI copy
carried `nav_start`/`nav_stop`, and it omitted the `navigation is None` guard
the real one has, while the two `sleep()` copies logged to different places
(`self.commands` vs `serial.commands`). The merged double mirrors the real
`Supervisor` method for method, so drift shows up as an `AttributeError` rather
than as three copies quietly disagreeing.

`test_navigation_speech.py` had monkey-patched `nav_start`/`nav_stop` onto the
double for the same reason; the double provides them now, so that is gone too.

The one parameter worth care is `last`. It defaults to a frame (the speech face
gate reads `supervisor.last.face`), but the navigation tests pass `last=None`,
which is what the real `Supervisor` starts with. `FakeTelemetry`'s defaults are
a *valid* fix with a *calibrated* heading, so the default frame is one staleness
check away from making the controller aim as soon as it is started. Measured
2026-08-31: today it does not, because `timestamp=0.0` is stale against
wall-clock and the suite passes with either value — that is luck, and the
docstring says so rather than claiming the tests depend on it.

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
Orange Pi.

The risk this trades into is packaging: "the file did not deploy" replaces "a
stray backslash broke the module". `setup.sh` rsyncs the tree wholesale so a new
package directory ships automatically, and `main.py` imports `WebUI` *inside* a
try/except so a missing template degrades to "running without the web UI"
(R-012.3). `_load_template()` raises a message naming deployment and
`rsync --exclude` rather than a bare `FileNotFoundError` (R-012.4).

**The web payload is derived from the dataclass, not hand-copied.** `/api/telemetry`
built its JSON from ~50 lines of explicit field accesses; it is now
`dataclasses.asdict(t)` plus three documented deviations. This is NOT an R-010.5
obligation — that rule is about parsing what the firmware sends, and a page may
render a subset — so the justification is only R-012.2: one fact, one place.

It had drifted in **both** directions, which is what a hand-copied mirror does:
it omitted `timestamp`, `uptime`, `loop_hz`, the whole `update` block and five
face diagnostics (`attempts`, `capture_ms`, `infer_ms`, `stack_free`, and the
firmware's own `navigation`), while publishing `face.confidence`/`gaze_x`/`gaze_y`
that no part of the page reads.

The three deviations, each for a different reason:
* `eye` and `uptime` keep their **wire** names. `asdict` emits the attribute
  names `eye_expression`/`uptime_ms`, and the page has read the wire names since
  it existed — the failure mode is a blank span, not an error.
* `update.password` is **dropped**. The route has no authentication (R-012.3's
  LAN-only premise), and a password-shaped field is not something to publish as a
  side effect of a refactor. `UPDATE_AP_SSID`/`PASSWORD` are compile-time
  constants if the page ever wants them deliberately.
* the firmware's `navigation` becomes `navigation_esp32`, because **the name
  meant two different things and asdict made them collide.** On the wire it is
  the firmware's echo (`active`, `angle` — is the head really held where we told
  it?); in this payload it is the `Navigation` controller's status (`target`,
  `bearing`, `distance_m`, `aim`), which is what the Navigate card renders. The
  hand-written version only ever carried the controller's copy, so assignment
  order concealed the clash rather than resolving it.

**Test doubles are built from the real types where possible.** `FakeTelemetry`
in `tests/stubs.py` constructs the real frozen dataclasses. It used to be
hand-rolled `SimpleNamespace`s, i.e. a fourth mirror of the wire format, and it
had drifted: it broke the moment the seven dropped fields were parsed. A double
assembled from the real type cannot drift.

**Status OLED (`brain/display.py`, 2026-11-01, owner request).** A 0.91" 128x32 SSD1306
on one of the Orange Pi's own I2C buses shows the owl's mode (`IDLE`, `DETECTING`, ...) and,
small, the firmware loop rate and whether a face is seen. Decisions: (1) **no new package** -
`/dev/i2c-N` with the `I2C_SLAVE` ioctl and a built-in 5x7 font (upper case only), so
`requirements.txt` is unchanged; (2) **a pure `render()`** returns the 512-byte page buffer, so
the layout is tested without hardware (`tests/test_display.py`), and the device gets a frame
only when the picture changed; (3) it follows R-012.3: a missing bus or panel logs once, the
thread retries every `retry_s`, a write error closes the device and never reaches the read
loop; (4) it reads the supervisor (`last`, `last_state`) and never talks to the serial port;
(5) two states exist only on the brain side: `STARTING` (no frame yet; the firmware's own
`boot` also shows as `STARTING`) and `NO LINK` (telemetry went stale), plus `BRAIN OFF` left on
the panel at shutdown; (6) dims to minimum contrast while the owl sleeps. The service user
needs the `i2c` group (`setup.sh`, `deploy/install.sh` and the unit add it).
**Do not read bus 20 as the panel:** `i2cdetect -l` names `i2c-20` "SUNXI HDMI" - the HDMI DDC
channel. A device at `0x30` there is the monitor link (E-DDC segment address), not an OLED;
the panel answers at `0x3C`/`0x3D` on the bus of the header pins it is wired to.
**Installed 2026-11-01:** SDA on PE2, SCL on PE1 = TWI2, which is `disabled` in the stock DT, so
the panel is invisible until it is enabled. **The `overlays=` line in `/boot/dietpiEnv.txt` does
nothing on this image** (`overlay_path=amlogic` points at a directory that does not exist, so
`i2c3-ph` was never applied); only `user_overlays=` works. TWI2 comes from
`/boot/overlay-user/owl-i2c2.dtbo` (`status = "okay"` on `/soc@3000000/twi@2512000`, compiled
with `dtc -@`), and appears as `/dev/i2c-2`, panel at `0x3C`. Config: `i2c_bus: 2`.
**Portrait mount (2026-11-01):** the panel stands in the owl's backpack with its long side
vertical, so `display.portrait: true` draws the mode bold and centred, upright letters one under
the other, first letter on top (`render_portrait()`, 32x128 canvas): 3x for up to 4 letters, 2x up
to 7. Long names are abbreviated (`PORTRAIT_LABELS`: DETECT, CHAT for interacting, NAVI, ...).
Below a rule come the metrics as two small horizontal lines (`28HZ`, `FACE`/`NONE`). The first
version put the mode in a 1x column and was too small to read. The canvas is turned onto the panel by a quarter turn;
if it comes out upside down, set `rotate: true` (hardware 180 degree flip).

**Mute switch and power button (2026-11-04).** Two contacts to GND on the header: slide
switch PB1 (pin 13), push button PB0 (pin 11). (1) **Read through the GPIO character device
with raw ioctls** (`brain/gpio.py`, uAPI v2), same reasoning as the OLED: no package, the
image has neither `gpiod` tools nor bindings. Lines are requested as inputs with
`BIAS_PULL_UP | ACTIVE_LOW`, so `read()` is True while the contact is closed. (2) **The chip
is chosen by label, `2000000.pinctrl`, not by number:** the image has two chips with no
line names, and `gpiochip1` (`7025000.pinctrl`, R_PIO) holds `wlan_hostwake`/`wlan_regon`
at offsets 32/33, exactly where PB0/PB1 sit on `gpiochip0`. (3) **Mute is "start no
player"**, in `Audio.play()`: the `owl_i2s` card has no mixer control, and skipping the
player is true silence that also leaves the speech barrier idle. The silent TX keepalive is
not touched (SPEC-015). The switch position is read once before the first sound, then
polled at 5 Hz with two-read debounce. (4) **The power button runs outside the brain**, as
root, in `robot-owl-power.service`: the button exists for the moment something has gone
wrong, and a brain crash must not take the only clean way out with it. It needs a 2 s hold
(a shoulder bump must not switch the owl off), and **arms only after seeing the button
released** — a stuck contact would otherwise power off every boot with no way to log in.
DietPi runs without D-Bus/logind, which ruled out a `gpio-keys` overlay + logind's power-key
handling. (5) `main.py` turns SIGTERM into the normal shutdown path; before, `systemctl stop`
(and so any shutdown) killed the brain without its `finally`, and `BRAIN OFF` never reached
the OLED.

**Fallback hotspot (2026-11-11).** With no known WLAN (at home "Olymp") for `wait_s` (90 s),
`brain/hotspot.py` opens a WPA2 access point "Hubert" so a phone reaches the control page at
`http://10.42.0.1:8080`. (1) **Own root service**, `robot-owl-hotspot.service`, for the same
reasons as the power button: creating an AP needs root, and it is the way back in exactly
when something is wrong. (2) **The AP runs on a second, virtual interface `uap0`; `wlan0`
is never touched.** The AIC8800 allows one managed plus one AP interface at once, so
wpa_supplicant keeps searching for the known WLAN and the owl rejoins it on its own; the
AP closes once `wlan0` has a link again **and** no phone is associated (counted via
`hostapd_cli list_sta` — `iw station dump` reported 0 with an iPhone on it). While `wlan0`
is associated the AP must use its channel (one radio). (3) **The passphrase is never in
the repo**: `/etc/robot-owl/hotspot.psk`, root-only. The control page has no login, so an
open AP would hand the owl, servos included, to anyone in range; with no valid passphrase
the AP does not start. (4) DHCP only (`dnsmasq-base`, `port=0`, no gateway): the phone
keeps its mobile data and reaches the owl on the local subnet. (5) `--test SECONDS` opens
the AP immediately and closes it again, for a bench test without leaving the home WLAN.

## Verified facts

* On 2026-10-18 a native full-process smoke test used real Flask, pyserial and
  YAML with a PTY simulating the XIAO. HTTP page/telemetry, update-mode entry and
  exit forwarding, malformed requests, servo forwarding and place persistence
  passed. Every command went to the PTY, never the physical USB port. This does
  not establish live firmware behavior or mechanical correctness.

* 174 tests at the close of that pass, up from 104 before it (188 as of
  2026-08-31). `tests/test_protocol.py` (48) and
  `tests/test_expressions.py` (11) are new; neither area had any coverage.
* **The reader thread's latency is a firmware-visible quantity.** `tx_dropped`
  counts NDJSON lines the ESP32 threw away rather than block its render loop on
  (SPEC-001, SPEC-010). It is therefore a measurement *of this process*: a rising
  value means the read loop fell behind, and the likeliest cause is the speech
  thread — `whisper.cpp` inference holding the GIL — starving it. Verified
  2026-08-31 from the firmware side: 2838 lines dropped across 22 minutes with no
  reader, then **not one** across the following 15 minutes with `cat` attached.
  A gap in telemetry is therefore diagnosable rather than ambiguous, which is the
  point: telemetry counters are cumulative precisely so a gap costs nothing but
  the gap.
* `web_ui.py` 746 → ~360 lines; `brain/templates/index.html` 429 lines, verified
  byte-for-byte identical to the extracted literal (20,257 characters).
* The whole suite runs on a plain Mac with no Orange Pi, mic, PortAudio, whisper.cpp,
  **Flask, Jinja2 or PyYAML**. `tests/stubs.py` substitutes a module only when
  the real one is not importable. numpy is the single hard dependency.
* Consequence of the above worth knowing: `config.yaml` cannot be parsed by the
  test suite — the yaml stub returns `{}`. Validating it needs a real parser;
  `~/.platformio/penv/bin/python` has PyYAML 6.0.3 and was used for exactly that
  after editing the file.

* **Power button, end to end on the owl, 2026-11-04:** a 2 s hold on PB0 halted the board
  quickly; the OLED showed `OFF` (portrait form of `BRAIN OFF`, i.e. the brain's SIGTERM path
  ran). On the next boot `dmesg` showed the root ext4 mounted with **no journal recovery**, so
  the halt was clean. The journal is RAM-only on this image, so the shutdown itself cannot be
  read back afterwards; the clean mount is the evidence. Mute switch confirmed by ear by the
  owner, polarity `muted_when: open`.

* **Fallback hotspot on hardware, 2026-11-11:** on `uap0` next to a live Olymp
  connection, an iPhone saw "Hubert", associated with WPA2, got `10.42.0.40` by DHCP and
  loaded the control page; the module counted 1 station; on close `uap0` was removed;
  Olymp never dropped. Not yet exercised: the automatic path with Olymp genuinely
  missing (state machine unit-tested in `tests/test_hotspot.py`, incl. mutation checks).

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
* **"The test failed against the old code, so it is a real guard." A second
  instance of R-012.5, 2026-08-31 — and it failed for the wrong reason.** Five
  new tests were written to pin the telemetry payload against exactly the drift
  described above. Run against the previous implementation, all five "failed" —
  with `ImportError: cannot import name '_telemetry_payload'`, because they
  reached for the new private helper. Not one assertion was ever evaluated, so
  they demonstrated only that the helper was new. **Watching a test go red is not
  enough; the red has to come from the assertion.** Rewritten to go through
  `app.view_functions["api_telemetry"]` — the seam the HTTP layer actually
  calls — they fail on the assertion and name the gap:
  `['timestamp', 'uptime_ms', 'loop_hz', 'update', 'navigation']` absent, and
  `face` missing `attempts`. Then they pass. A guard aimed at a private helper
  can only ever test code that already exists.
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

* **"The hotspot can take over wlan0 itself (ifdown, AP, ifup back)."** First version,
  2026-11-11. On the bench test the AP never appeared and wlan0 never came back: the
  Orange Pi was unreachable until power-cycled, and the reason was lost with the
  non-persistent journal. Replaced by the `uap0` design, which cannot lock the owl out
  of its home WLAN. *Bench-test anything that touches the only network link with a
  path back that does not depend on that link.*

## Acceptance

1. `cd orangepi-brain && python3 tests/run_tests.py` — 184 tests pass.
2. `grep -rn 'getattr(supervisor, "audio"' brain/` returns nothing, and
   `supervisor.py` is the only module touching `Audio` (R-012.1).
3. Deleting `brain/templates/index.html` makes the brain log
   "Web UI failed to start (continuing without it): web UI template missing …"
   and keep running (R-012.3, R-012.4).
4. Any new regression test has been run against the unfixed code and observed to
   fail (R-012.5).

## Open

* `[ ]` `render_template` instead of `render_template_string`, once someone can
  verify it on a deployment that has Flask installed.
* `[ ]` The diagnostic panel added to the web UI (calibration counters, GPS fix,
  pulse/hit totals) has only ever been fed synthetic frames. See SPEC-010 Open.
