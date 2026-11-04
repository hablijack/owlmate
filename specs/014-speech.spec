# SPEC-014: Speech — hearing the user and reacting

Status: partial — name-gated Vosk + mood classifier accepted live by the owner;
        sustained ambient/TV robustness pending
Verified: 2026-11-04 — on the shoulder: "Hubert" alone, Sauwetter -> sad_down,
          Lotto -> glee, Fuss/Treppe -> scared, neutral -> gaze only
          (earlier: 2026-10-18 real mic/VAD/ASR, happy command ACK)
Depends on: 001, 012, 013

## Intent

The owl is called **Hubert** (written "Huubert"). It sits on the owner's shoulder,
so it is never looked at while spoken to: it listens only when its name is said,
transcribes offline on the Orange Pi, and shows the *mood the sentence implies*
("Heute ist wirklich ein Sauwetter" -> sad_down, "Ich habe im Lotto gewonnen" ->
glee, "Ich habe mir den Fuss angestossen" -> scared) with eyes, body pose and sound. It is a reaction engine, not a
conversation: there is no dialogue state, no intent model, no reply. German.

Absorbed from `SPEECH_RECOGNITION_PLAN.md` (deleted 2026-08-27), which was the
pre-implementation design.

## Requirements

* **R-014.1** ASR runs on the Orange Pi. The ESP32 is not involved and needs no
  firmware change (it has no mic and no headroom).
* **R-014.2** Reactions use the *existing* override protocol. Speech must not
  add a second way to drive the owl (inherits R-001.2).
* **R-014.3** The owl must not react to ambient sound — the TV, or its own amp.
* **R-014.4** A sleeping owl is not woken by ambient words, only by a clearly
  addressed wake keyword.
* **R-014.5** Nothing here may block the brain's startup or its read loop.
* **R-014.6** Keywords, clusters and reactions are configuration, not code.
* **R-014.7** Real owl playback and a configurable reverb tail must not become
  speech input. Drop overlapping/stale mic blocks, reset partial utterances and
  invalidate in-flight ASR/mood results across playback. Keep capture and the
  silent I2S TX alive; the silent stream itself must not inhibit listening.

## Decisions

**Since 2026-11-04 the pipeline is: Vosk (streaming) -> name gate -> navigation ->
mood classifier.** It replaces whisper + face gate + keyword clusters for the
default configuration (`speech.engine: vosk`, `mood.enabled`). The whisper path
and the clusters remain for `engine: whisper` / `mood.enabled: false`. Measured
on a corpus the owner recorded on the shoulder (33 utterances, 20 containing the
name, 12 without it; A733, same audio for every engine):

| engine | latency after speech | name hits /20 | false names /12 | WER |
|---|---|---|---|---|
| whisper tiny | 0.70 s | 3 | 1 | 52 % |
| whisper base | 1.29 s | 11 | 0 | 23 % |
| whisper small q8_0, 4 threads | 3.30 s | 15 | 1 | 12 % |
| whisper small, 8 threads | 3.36 s (p90 6.07) | 15 | - | 11 % |
| whisper medium q5 | ~12 s | - | - | - |
| whisper large-v3-turbo q5 | 21-80 s, repetition loops | - | - | - |
| **Vosk small-de-0.15, free text** | **~0.2 s** (RTF 0.124) | **14** | **0** | **12 %** |

Vosk is as accurate as whisper small at a fifteenth of the delay, because it
decodes while the user speaks; whisper must wait for the end and then encode.
Live on 2026-11-04 the mood reaction followed the transcript by ~10 ms.

**The name gate (`_addressed`).** A fuzzy match of any word against
`name_keywords` (`hubert`, ratio >= `name_threshold` 0.75) opens a
`name_window_s` (10 s) window; inside it the name is not needed again. Said alone
the name shows `focused` + the `hello` sound. Vosk's most frequent mishearings of
the name said *alone* ("robert", "rupert") count only when they are the whole
utterance (`name_alone_aliases`) -- in a sentence "Robert hat angerufen" must not
address the owl. The face gate is off (`require_face: false`): on the shoulder
the camera never sees the speaker. The own-voice barrier (R-014.7) still resets
the recogniser across playback.

**Sentence -> mood is a fine-tuned small BERT, not keywords and not an LLM.**
None of the owner's example sentences names its feeling, so keyword clusters
cannot work. `paraphrase-multilingual-MiniLM-L12-v2` is fine-tuned on
`orangepi-brain/assets/mood/sentences.yaml` (12 moods, ~570 training sentences,
106 held-out test sentences) by `orangepi-brain/tools/train_mood.py` on a dev
machine, exported as int8 ONNX (118 MB) and run by `brain/mood.py` with
onnxruntime + tokenizers -- no torch on the Pi, ~10 ms per sentence. Every
training sentence is also taught in Vosk's form (lowercase, no punctuation, with
"hubert" before or after), because that is what arrives. Held-out score: 94/106
(torch), 92/106 (int8). Below `min_confidence` (0.5) or for `neutral` the owl
only looks at the user: a wrong feeling is worse than none. Comparison on 41
unseen sentences: German-Emotions (XLM-R, GoEmotions labels) 11; Qwen2.5
0.5B/1.5B/3B by prompt 6/16/26; embedding kNN 23-32; frozen embeddings + logistic
regression 34-39; fine-tuned MiniLM 38, gbert-base 37 (MiniLM chosen: smaller,
multilingual, same score).

**Superseded 2026-11-04 for the default engine:** the two decisions below (whisper
tiny, three-part gate with a face) still describe `engine: whisper`.

**whisper.cpp (via pywhispercpp), not openai-whisper.** No torch on the Orange
Pi. The installer defaults to the multilingual **`tiny` model**, a local `.bin`
file downloaded for offline use; the model path is configuration (R-014.6).
`base`/`small` remain choices for accuracy experiments. The default was raised
to `small` on 2026-08-30 without board measurements, then returned to `tiny`
after the native comparison below: small's roughly 50 s delay is unsuitable
for the reaction loop. This is a latency decision, not evidence that tiny is
more accurate on real German speech or place names. See SPEC-015.

**A three-part gate, not always-on transcription** (R-014.3). ASR runs only when
the owl is awake *and* a face is in frame *and* the mic energy passes an RMS VAD
threshold. Reacting to the television, or to the the owl's own sound coming back
through the mic, is the failure mode this exists to prevent. `REACTIVE_STATES`
is `{idle, detecting, interacting}` — `SLEEPING` and `UPDATE` are excluded
deliberately (maintenance must not be interrupted; USB remains connected).

**Own-voice suppression is a capture barrier, not just reaction cooldown.**
Audio reserves a monotonic playback generation before starting each real player.
Its guard stays closed while any players overlap, then through
`audio.feedback_tail_s` (default 0.6 s) after actual player exit. The silent TX
keepalive never reserves this guard. Spawn failure releases it; a timed-out
player is killed/reaped, and a still-live failed player keeps the guard closed.

Speech obtains the atomic generation/safe-after snapshot only through
Supervisor. The PortAudio callback drops blocks overlapping playback/tail,
using ADC age where available and block duration as fallback. Queue entries
carry capture generation and start time: an old block cannot become safe just
because ASR dequeues it after playback ends. Generation changes reset partial
utterances. Results of ASR/mood inference are checked again before
feed(), including when a whole sound has started and ended during inference.
No mic reopen, clock stop or new firmware behavior is involved.

This is intentional half-duplex listening during real owl calls, not acoustic
echo cancellation or a solution to unrelated TV/room speech. The 0.6 s tail is
a configurable initial policy, not a measured upper bound for every room.

**One worker thread owns the whole audio path.** sounddevice's callback fills a
queue; the worker drains it, runs the VAD, transcribes, and performs every
reaction. Nothing touches the foreground serial read loop (R-014.5).

**Heavy imports are lazy.** `pywhispercpp.model` and `sounddevice` are imported
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
a tap; the Orange Pi adds only "wake on an explicit `wake_keywords` entry" (R-014.4).
An empty list means speech never wakes the owl.

**Everything German lives in `config.yaml`** — keywords, clusters, reactions,
nav triggers, stop words (R-014.6). Adding a reaction is a config edit.

**ASR speed comes from the encoder window, not the model (2026-11-01).**
whisper always encodes a 30 s window, however short the utterance. `audio_ctx`
(192 of 1500) shrinks that window to what a 2-3 s utterance needs, and four
threads use the A733's cores. Together: tiny 4.4 s -> about 0.6 s per utterance
(`speech.threads: 4`, `speech.audio_ctx: 192`). Opt-in keys, code defaults unchanged.

**An utterance keeps quiet audio around its voiced chunks (`speech.context_ms`,
300).** The VAD used to append only chunks above `vad_threshold`, so soft word
onsets, word endings and the gaps between words were cut out of what whisper
heard. Now a pre-roll before the first voiced chunk, silent gaps between voiced
chunks, and the first `context_ms` of the closing silence stay in. The
`min_utterance_s` blip filter counts *voiced* audio only, so context does not
let a music blip through. 0 = old behaviour.

**Peak normalisation and a debug dump exist but are off.** `normalize_peak`
scales each utterance to the given peak before whisper (0.7 live); it did not
visibly change recognition. `dump_dir` writes every utterance as a WAV for
offline comparison and is empty in production.

**A reaction may keep its own eyes (`own_eyes: true`).** By default the
gesture goes out last and the firmware's gesture eyes win. With `own_eyes` the
reaction's `expression` is sent after the gesture and replaces them. Used for
`negative`: `sad_down` (owner: stronger than `annoyed`), with the head shake and
ears unchanged. Reaction cooldown is 3.0 s (was 4.5; the old value swallowed a
spoken sentence ~5 s after another).

## Verified facts

* **First real-mic ASR, 2026-10-18:** tiny, two CPU threads, 15-second 16 kHz
  mono recording: inference 4.693 s, text `Hallo, eule, das ist toll.` followed
  by `Wir heißen zu. Hallo, Hallo, eule.`. The greeting/praise was recognized;
  the prompted question was not reliably recognized. No reactions were sent.
* A subsequent 20-second 48 kHz stereo capture, channel 0 resampled through
  Speech's actual helper, took 4.558 s to transcribe and returned
  `Hallo, das ist toll.` twice. Its tail became zero after playback, so it is
  not a valid continuous-capture/accuracy acceptance run (SPEC-015).
* After deploying the opt-in silent-TX fix, a 15-second stereo capture remained
  nonzero in every second, with no PortAudio status flags. Tiny inference took
  7.660 s; its text did not match the prompted phrases. The owner later
  confirmed that run contained ambient noise, not the prompted speech; it is
  not a controlled recognition-accuracy test.
* **Controlled repeat with explicit START:** an initial remotely triggered
  capture contained only the end of speech (`die hast du.`); start latency was
  suspected, not established. The repeat opened capture BEFORE signaling START.
  In 30 seconds it returned `Hallo, Eulim. Das ist toll.` and `Wie heißt du?`
  across repeated phrases. Praise and question matched; the greeting's name
  remained imperfect. Tiny/two-thread inference took 9.496 s for the full
  recording, with no PortAudio status flags and no digitally silent seconds.
  Quiet per-second RMS was approximately 0.00010–0.00012; speech-bearing seconds
  reached 0.00388. This validates controlled direct ASR, not automatic reactions
  or per-utterance pipeline latency. Automatic speech was still disabled then.
* **First live reaction test:** temporary service configuration enabled speech
  with VAD 0.0006 and require_face:true, without changing persistent config.
  Model and stereo input started successfully. During 25 seconds of observation
  the detector reported no face in any of 50 telemetry samples; no speech/reaction
  log appeared. The face gate was not bypassed. Test override was removed and
  normal speech-disabled service restored. This is evidence of startup/gating,
  not threshold calibration or end-to-end reaction acceptance.
* **Controlled end-to-end reaction:** after the owner repositioned, all 16
  pre-test telemetry samples detected a face. Temporary VAD 0.0006 with face
  gating recognized `Das ist toll!` and `Das ist toll.`, logged the happy
  expression/sound dispatch, received expression_ack for happy and exposed
  eye:happy in live telemetry. The owner confirmed **one visible happy-eye
  reaction**. This verifies a controlled mic→VAD→ASR→keyword→ESP32 eye reaction;
  the speech-triggered sound was dispatched but not separately confirmed audible.
  In a subsequent 35-second observation, 81/115 samples detected a face and
  additional transcripts included repeated praise, `*Gelächter*` and `* Musik *`.
  Cooldown and face gating suppressed some; the ambient fallback sent a gaze
  command. The owner suspects the laughter transcript came from the owl's own
  sound. That is a plausible feedback explanation, not an isolated measurement;
  environment, owl playback, queued audio and model behavior must be separated.
  The temporary override was removed; persistent speech was restored to disabled
  at 0.02 for that test.
* **Feedback barrier acceptance:** new regression tests were observed failing
  against the old implementation (including an ASR result delivered despite
  intervening playback). Tests now cover player overlap/exit/tail, spawn failure,
  bounded timeout cleanup, silent-TX exclusion, delayed/unknown ADC timestamps,
  queue age/generation, partial utterance reset, stale inference and recovery.
  A fresh-process monotonic clock may have a block start before zero; no-playback
  input must still be accepted. The corresponding regression was observed failing.
* **Native isolated echo probe:** real A733 mic, real happy WAV/player, real
  Speech callback/queue/resample/VAD; synthetic face and ASR spy isolate the
  guard without sending serial actions. The face gate stayed open beyond the
  VAD trailing-silence timeout. Bypassing only the capture guard caused one ASR
  call and failed the zero-calls assertion. With the deployed guard, six blocks
  were blocked, three exceeding VAD 0.0006 (max RMS 0.00463), and ASR calls were
  zero. Guard recovery was verified after actual exit plus tail. This validates
  suppression at the ASR boundary, not a new transcription-accuracy benchmark
  or automatic full-system ambient-noise acceptance. The probe did not enable
  production speech. The owner subsequently requested persistent activation:
  deployment uses VAD 0.0006 with face gating and the 0.6 s feedback tail;
  repository defaults remain disabled. Sustained ambient rejection is not proven
  by that opt-in deployment.
* In the second real capture, 300 ms chunk RMS peaked at 0.01051, below the
  deployed/default VAD threshold 0.02. Offline replay of the first mono clip
  through `_process_chunk` with a deliberately forced-open face gate produced
  no windows at 0.02; 0.0005 admitted speech but also 0.3-second noise fragments.
  That is isolated VAD evidence, not live face-gated pipeline acceptance.
  No production threshold was changed at that stage.

* **Native CPU ASR smoke test, 2026-10-18:** A733, pywhispercpp 1.5.1, two
  inference threads, same 2.1914375 s German espeak-ng clip, model load and
  inference timed separately with `time.monotonic()`. `small`: load 0.441 s,
  inference **49.683 s**, text `Hallo ihr Passistoll!`; `tiny`: load 0.137 s,
  inference **4.480 s**, text `Hallo, Eure, das ist toll.`. Both ran through
  `Speech._transcribe_and_feed()` with mock command/audio outputs. The tiny
  result matches a happy keyword; the small result correctly does not (no
  whole-word match). A separate known transcript confirms the happy reaction.
  These are individual synthetic-clip runs, not an accuracy benchmark or a
  microphone test; scheduling, window length and real speech may differ.

* The whole pipeline is tested on a dev machine with no mic, no PortAudio and no
  whisper.cpp: `tests/stubs.py` feeds synthetic audio chunks and a mocked
  model returning canned segments. See SPEECH-adjacent detail in SPEC-012.
* Reaction expressions configured in `config.yaml` are validated against the
  firmware's `NAMES[]` by `tests/test_expressions.py` — a typo there would
  otherwise render as `neutral` and look like a broken eye (SPEC-004).
* Auto-sleep and speech interact deliberately: hearing the user is an activity
  trigger that resets the inactivity timer, and navigation being active blocks
  auto-sleep entirely.
* **Speed measurements, 2026-11-01** (A733, 2.5 s clip, CPU governor
  `performance`, ~56 C): tiny 2 threads 4.37 s; tiny + `audio_ctx` 192 0.80 s;
  tiny 4 threads 0.64-0.75 s, 8 threads 0.36-0.43 s; small 4 threads +
  `audio_ctx` 192 4.90 s, 8 threads 3.08 s. Live with the config above:
  `asr=` 0.4-0.8 s in the log line (one 3.65 s outlier while other work ran).
  Before: 4.4-13.9 s per ~2 s utterance.
* **Microphone level and placement decide recognition, 2026-11-01.** The ICS43434
  signal is quiet (`peak_rms` 0.002-0.005 across the room; speech at ~15 dB
  above a noise floor of ~0.02 after normalisation, no clipping, no
  quantisation problem). Across the room tiny and small both garbled the same
  word identically ("lass das" -> "Laptos/Lostos/lastos"), so it is the signal,
  not the model. With the owl **on the shoulder** (its normal place) `peak_rms`
  roughly doubles to ~0.006 and tiny recognised "Das ist toll.", "Wie geht es dir
  heute?" and "Nein, lasst das." correctly. "Du bist eine gute Eule" still
  arrives as "gute Eure/Eume" -- covered by extra keywords, not fixed.
* **Offline model comparison on the same WAVs, 2026-11-01:** `small` was not
  better than `tiny` on short utterances (it also hallucinated a sentence from
  a 1.2 s clip) and costs 5-7 s. Do not switch models to fix recognition.
* Whisper non-speech tags (`[Musik]`, `* Musik *`, `(Applaus)`, `[Klicken]`)
  are dropped by `_only_non_speech`; music blips are 0.3-0.6 s with peaks <=
  0.0029, speech 0.0028-0.0056 (`vad_threshold` 0.002, `min_utterance_s` 0.8).


## Falsified

* **"Whisper base/small just needs tuning to hear 'Hubert'."** Base produced
  sound tags or nothing for the name; small hears it in sentences (15/20) but
  spells it alone as "Hupiert", "Uppet", "Hulbett", "Uppert" -- and costs 3-6 s.
  medium (~12 s) and large-v3-turbo (21-80 s, repetition hallucinations) are not
  usable on the A733. Measured 2026-11-04, table in Decisions.
* **"Pinning whisper to the two A76 cores is faster."** small on the A76 pair
  with 2 threads: 7.65 s, against 3.36 s with 8 threads on all cores.
* **"Vosk's grammar mode (`["hubert", "[unk]"]`) makes a good wake word."** With
  only one real word to choose from, the decoder maps much ordinary speech onto
  it -- massive false positives. Free-text mode plus a fuzzy match is used.
* **"A ready-made German emotion model maps sentences to our moods."**
  German-Emotions scored 11/41: it recognises *named* feelings, not situations
  ("Ich bin die Treppe runtergefallen"), and its GoEmotions labels are not ours.
* **"A local LLM classifies moods by prompt."** Qwen2.5 3B, the largest that
  fits, reached 26/41 and needs seconds per sentence; 0.5B was near chance.
* **"Speech needs a face in frame."** On the shoulder the owner never faces the
  owl; the face gate blocked every real use. Replaced by the name gate.

* **"The generic 0.02 energy threshold suits the installed mic."** The measured
  real speech chunks were all below it. A gate can be perfectly unit-tested
  against loud synthetic tones and still reject every real utterance.
* **"Reaction cooldown or a flag checked only at ASR time stops feedback."**
  Cooldown left own audio queued/transcribed; a delayed block or result can be
  consumed after the flag clears. Capture-time barriers and generation checks
  are needed, including a post-inference check.
* **"Successful direct transcription validates the reaction pipeline."** These
  captures bypassed live face gating and window assembly. Threshold tuning,
  transient/noise rejection, live face timing and feedback from the owl's own
  sounds still need acceptance before automatic operation.

* **"The binding API was already covered by the ASR tests."** Initial deployment
  inspection found `requirements.txt` installs `pywhispercpp`, while code and
  tests invented `whispercpp.WhisperModel` and a Transcription object. The actual
  1.5.1 API is `pywhispercpp.model.Model(path, **params)` returning a list of
  segments from `transcribe()`. Tests now mirror that API; the old import was
  reproduced and observed to fail. CPU inference uses two threads to leave
  capacity for the serial reader. The installer model URL also named a
  nonexistent repository (HTTP 401); the official `ggerganov/whisper.cpp`
  repository serves the requested model.

* **"Substring keyword matching is fine."** Not in German. "wie" inside
  "völlig", "was" inside "irgendwas" — the owl reacts to words nobody said.
* **"The owl should react to whatever it hears."** It then reacts to the
  television, and to its own sound returning through the mic. Hence the
  awake + face + energy gate.
* **"Phase" is an unambiguous word in this repo.** The speech feature's
  implementation phases (1–4: VAD → ASR → reactions → auto-sleep) are cited in
  `orangepi-brain/brain/` comments and are **not** the BACKLOG's Steps. Two meanings
  for one word cost time on 2026-08-27; the BACKLOG's task list was renamed to
  "Step" for this reason.

* **"A bigger model gives better recognition."** Measured 2026-11-01 on the
  same utterances: `small` was no better than `tiny` on 1-2 s clips, hallucinated
  a full sentence from one, and was 5-7x slower. The limit was the mic signal.
* **"Appending only the voiced chunks is a fine VAD."** It removes soft onsets,
  endings and inter-word pauses from what whisper hears, which produced
  fragmentary transcripts ("Laptos"). The utterance needs context
  (`context_ms`), and the blip filter must count voiced audio, not total.
* **"Whisper time scales with the model alone."** It is dominated by the fixed
  30 s encoder window; `audio_ctx` removed ~85 % of it for a 2-3 s utterance.
* **"Recognition quality can be judged with the owl across the room."** The owl
  sits on a shoulder, with the mouth much closer; the same sentences that
  failed at a distance were recognised there.

## Acceptance

1. `cd orangepi-brain && python3 tests/run_tests.py` — the speech pipeline, ASR,
   auto-sleep and navigation-trigger tests pass.
2. With `speech.enabled: false` (the default), no audio module is imported and
   the brain starts normally.
3. On a machine with no mic, enabling speech logs a failure and the brain keeps
   running (R-014.5).

## Open

* **The name inside a sentence is still sometimes misheard** (live 2026-11-04:
  "wird", "gerd", "gucke", "robert ich hab mir ..."), so the owner occasionally
  repeats the sentence. Collect these from the log; if it stays frequent, a
  custom wake-word model (~50 recordings of the owner) is the next lever, not a
  bigger ASR.
* **Misclassified real sentences are training data.** The log line
  `'<text>' -> <mood> (<p>)` shows each decision; add wrong ones to
  `assets/mood/sentences.yaml` (train) and retrain.

* **Mic level.** Peak levels of 0.002-0.006 are low. Software gain before the
  VAD, or the ICS43434's placement/window in the case, may help; sentences below
  `vad_threshold` (0.002) are never heard. Lowering it toward 0.0015 trades
  against more music/ambient blips -- measure both.
* **Keyword robustness** (whisper path only). Superseded for the default
  configuration by the mood classifier (Decisions, 2026-11-04).

* **Production VAD calibration and ambient rejection.** The default threshold
  rejects measured speech; temporary 0.0006 admitted a confirmed live reaction
  but also extra transcripts. Playback suppression now passes an isolated native
  negative/positive probe. Validate sustained real listening with the deployed
  barrier, startup/transient rejection, room/TV speech and adequacy of the tail
  under sustained operation. Neither a single happy-eye
  reaction nor a playback barrier proves general R-014.3.
* **Real-mic recognition accuracy and short-window latency.** Full-recording
  runs are measured above, but they do not measure the real 2.5 s pipeline
  window. Confirm the owner's actual spoken phrases; test multiple utterances,
  German place names and navigation stop commands. Accuracy interacts with
  SPEC-013's fuzzy matching; a faster model is not automatically adequate.
* No dialogue and no confirmation turn, by design. Revisit only if misheard
  navigation targets turn out to be common in practice.
