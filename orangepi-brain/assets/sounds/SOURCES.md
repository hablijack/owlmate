# Robot sounds

The owl's voice is a set of 17 short robot sounds (`alarm`, `beautiful`, `crazy`, `drill`,
`gobble`, `grunt`, `hello`, `info`, `marvel`, `no`, `ohno`, `power_sound`, `sad`, `servo`,
`whew`, `wow`, `yes`). They replaced the six owl hoots (Freesound CC0 recordings run through a
robot-voice effect chain) on 2026-11-04, because the owner found the hoots annoying.

**Origin:** the owner's own repository
[`hablijack/tiny-desk-companion`](https://github.com/hablijack/tiny-desk-companion/tree/main/voice),
folder `voice/`, as MP3. That repository does not record where the recordings originally come from or
under which licence; **if this project is ever published or redistributed, find that out first.**

## Which sound plays when

* **Per mood** -- `MOOD_SOUNDS` in `brain/audio.py` (the web UI plays it with the mood; the planned
  trigger mapping reuses it). Chosen by the sample names, **not** by ear: listen and swap freely.
* **Old names** -- `SOUND_ALIASES` in `brain/audio.py`: `detecting` -> `info` (navigation start only; no state cue), `interacting` ->
  `yes`, `happy` -> `info`, `sleeping` -> `whew`, `waking` -> `power_sound`, `alert` -> `alarm`.
  State cues (`supervisor.py`) and the navigation cues still use those words.
* `crazy` (6.5 s) is not assigned to anything; it is only a button in the web UI.

## Regenerating the WAVs

```bash
python3 tools/convert_sounds.py <folder-with-the-mp3s> assets/sounds
```

Per file: ffmpeg decodes to 22050 Hz mono, silence at both ends is trimmed, a 10 ms fade-in and a
60 ms fade-out avoid clicks, and every clip is matched to the same RMS (0.12, peak capped at 0.9) so
no sound shouts over another. Needs ffmpeg and numpy. If a file is missing at runtime,
`brain/audio.py` falls back to a synthesized tone of the same name.
