# SPEC-015: Orange Pi audio — the A733 I2S stack

Status: implemented with opt-in silent-TX workaround — boot, WAV, mic and
        post-playback capture continuity verified; speech acceptance in SPEC-014
Verified: 2026-10-18 — DietPi Debian 13, kernel 6.6.98-vendor-sun60iw2;
          cold boot, owner-confirmed WAV, ALSA/sounddevice capture and duplex
Depends on: 001, 010, 012, 014

## Intent

Move the brain from the Raspberry Pi to an **Orange Pi Zero 3W** (Allwinner
A733, DietPi) so that a real I2S microphone and a real I2S amplifier live on the
same board as the CPU — the Pi had no convenient I2S and the mic/amp were an
afterthought. On the A733 the I2S0 controller is a first-class peripheral, so
`orangepi-brain` pairs the **MAX98357A** amplifier (playback) and the
**ICS43434** MEMS microphone (capture) on one shared 48 kHz ALSA card, and
replaces faster-whisper with **whisper.cpp** (SPEC-014) on that card.

This spec records the A733-specific traps that forced a **custom kernel driver**
instead of the "just add a DT overlay" path that works on a Raspberry Pi, and the
decisions that flow from them.

## Requirements

* **R-015.1** I2S0 drives both the MAX98357A (TX) and the ICS43434 (RX) on one
  shared 48 kHz card, exposed to userspace as the ALSA card **`owl`**.
* **R-015.2** The A733 one-bit **data-delay** quirk is corrected on **both** TX
  and RX, and re-applied after **every** `set_fmt()` — otherwise the amp plays
  one bit off and the mic returns garbage (samples alternating 0 / full-scale).
* **R-015.3** `set_pll()` precedes `set_sysclk()`. The vendor kernel otherwise
  computes `pllclk_freq = 0` and fails `hw_params` in a tight userspace retry
  loop that can crash the kernel.
* **R-015.4** `asound.conf` makes `owl` the default card and exposes
  `dmix`/`dsnoop` (S16_LE / 48000 / 2 ch) with a `plug` layer on top.
* **R-015.5** The microphone is the **left** I2S slot (LR pin tied to GND); the
  amp reads `(L+R)/2`, so mono playback is duplicated across both slots.
* **R-015.6** The audio **driver** is always built and loaded; the **speech**
  feature that consumes the mic remains opt-in (`speech.enabled: false` default,
  SPEC-014). A heavy RX path must not be a reason the amp does not work.
* **R-015.7** MCLK (PB4) is **unconnected** and neither codec uses it; the
  driver must not require an MCLK.
* **R-015.8** The DT overlay muxes the I2S0 pins and disables the stock
  `i2s0_mach`; it does **not** reference `ac101`.

## Decisions

**A custom kernel driver (`owl_i2s`), not a DT overlay alone.** On a Raspberry
Pi the MAX98357A is brought up with the vendor `hifiberry-i2s-lite` overlay on
the clean `snd-soc-bcm2835` driver. The A733's vendor "BSP" I2S controller is
different: the one-bit data-delay fix (R-015.2) lives behind a **private
notifier inside the stock driver that an external driver cannot reach and that is
not DT-overlay-configurable**. It cannot be expressed in a `.dts`, so it can only
be fixed in a driver. (The alternative — patching the stock vendor driver — was
rejected: it would fork the vendor kernel and break on every DietPi kernel
update.)

**The hook is `dai_link->ops`, replicating the Whisplay reference.** The A733
sequence is driven from the CPU DAI link's ops (`.startup` sets the 48 kHz
constraint, `.hw_params` runs the full sequence), exactly as the Whisplay
reference driver does — not from a component callback.

**A dummy codec.** The MAX98357A and ICS43434 are not ASoC-managed (no codec
driver, no registers to program), so the card is paired with
`snd-soc-dummy` rather than a real codec DAI.

**Silent TX keepalive is an opt-in runtime workaround.** On the installed
kernel, ending TX makes an already-running RX stream become digitally silent.
It reproduces with both direct hw and dmix/dsnoop, so changing ALSA topology is
not the fix. CTL still has GLOBAL_EN/RXEN set after TX ends; the exact underlying
hardware/BSP cause has not been established. Do not describe it as a measured
PLL shutdown. Keeping a zero-valued dmix TX stream open prevents the failure.
`audio.keep_playback_clock` starts that stream in Audio, using the shared output
PCM, before capture; default false. Audio owns/reaps the child, and main closes
capture before closing Audio. It consumes resources throughout the brain's
lifetime, including when speech is disabled; enabling it is an explicit choice.
No patch to the vendor driver or firmware is required.

**Continuous-clock format requested by default, with a DT opt-out.** `SND_SOC_DAIFMT_CONT`
requests continuous operation but did not prevent the measured TX-close/RX
failure on this kernel. The request is on by default and can be
disabled with the `robotowl,no-continuous-clock` DT property if the A733
misbehaves — a one-line overlay edit, no recompile.

**48 kHz, S16_LE, 2 slots × 32-bit, fixed.** The mic is a 32-bit left-justified
part; the upper 16 bits carry the audio, so the hw slaves are S16_LE and the
`plug` layer converts for applications. 48 kHz is the one rate the PLL/BCLK
math is cleanest at (see Verified facts).

**asound.conf: `dmix`/`dsnoop` with `plug` on top — and a documented fallback.**
The shared-card topology is `asym` (playback→`dmix`, capture→`dsnoop`) over
S16_LE/48k/2ch slaves, with a `plug` on top. The A733 **a7z** reference instead
uses a plain `plug` directly on `hw:` because "the A733 vendor I2S controller
does not advance dmix/dsnoop timing reliably." That is unverified on the 3W, so
the `a7z`-style config is kept as a documented fallback in `asound.conf`'s header
rather than guessed away.

## Verified facts

* Native `owl_i2s.ko` build succeeded on DietPi kernel 6.6.98-vendor-sun60iw2
  with matching `linux-headers-vendor-sun60iw2=26.08.0-trunk-dietpi2`.
  The compiler-version warning (GCC 14 vs kernel GCC 13) did not prevent the
  build. After power restoration, the module loads and registers card `owl`.
* This DietPi boot script loads `/boot/dtb/${fdtfile}` and overlays from
  `/boot/overlay-user/` named by `user_overlays=` in `dietpiEnv.txt`. An offline
  merge of the owl overlay into the actual base DTB succeeds. The running
  kernel has no CONFIG_OF_OVERLAY, so activation requires a reboot.
* ALSA helper names are `owl_playback`, `owl_capture`, `owl_dmixed`, `owl_array`;
  default and `owl` select the asymmetric shared card. Name resolution now
  reaches the registered physical card. Playback is audible (owner confirmation).
* Run ALSA diagnostics as `robotowl` or root. The SSH user `dietpi` is not in
  audio: its `aplay -l`/`arecord -l` reports no sound cards despite the card being
  registered. Service user `robotowl` has the required audio group; do not weaken
  device permissions to fix a diagnostic invocation.
* Real `owl_capture`, S16_LE/48 kHz/mono: 144000 samples in three seconds,
  RMS 32.08 raw units, mean -0.39, min/max -161/+187, no clipped samples.
  This is room noise, not a speech loudness/accuracy measurement.
* The actual sounddevice input path also captures at 16 kHz/mono: three seconds,
  normalized RMS 0.000688, peak 0.00784. It is nonzero and unclipped.
* Simultaneous five-second dsnoop capture plus a one-second, low-amplitude 660 Hz
  dmix playback completes in 5.049 s without reported xruns. Stereo capture has
  left RMS 33.02 / peak 496, right exactly zero, no clipping. The mic occupies
  the expected left slot. **Later per-second analysis falsified continuity:**
  the last two seconds were all zero despite successful command exit.
* Controlled eight-second capture with one-second playback at t=2 reproduced
  the failure with both plughw and dmix/dsnoop: each produced all-zero seconds
  4–7 after TX stopped. Capture-only eight-second runs remained nonzero.
* With silent dmix TX held open, eight-second capture remains nonzero after
  one-shot playback closes. The committed duplex checker was observed to fail
  with the brain/keepalive stopped, then pass with the real Audio keepalive:
  left RMS/s 37.450, 33.882, 19.747, 8.268, 11.343, 32.224, 55.313, 27.194.
  A later 15-second real stereo/sounddevice capture also remained nonzero in
  every second, with no PortAudio status flags.
* After the stream, live I2S regmap reads are TX0CHSEL 0x00110003,
  TX1/2/3CHSEL 0x00100000 and RXCHSEL 0x00110000: data_late is 1 on every
  tested TX/RX field. Real capture does not show the zero/full-scale failure.

* **Pin map (40-pin header):** PB5 = I2S0 BCLK, PB6 = I2S0 LRCK, PB8 = I2S0 DIN
  (mic in), PB7 = I2S0 DOUT (amp out), PB4 = I2S0 MCLK (**unconnected**).
* **Clock math:** at 48 kHz, BCLK = 64× fs = **3.072 MHz**, derived from a
  **24.576 MHz** PLL → `bclk_ratio = 8` (24.576 / 3.072).
* **Data-delay registers:** TX0/1/2/3CHSEL at `0x34/0x38/0x3c/0x40` and RXCHSEL at `0x64`; the
  data-delay select is **bits[21:20]** (`data_late`), written `1` after every
  `set_fmt()`.
* **Reference:** the Allwinner **Whisplay** driver (its `zero3w` variant) is the
  working A733 I2S reference this design is modelled on; the sequence and the
  data-delay fix were taken from it.

## Falsified

* **"No xruns and successful command exit prove duplex works."** Both
  processes completed normally while capture's tail was entirely zero. Inspect
  samples after playback stops, not just aggregate RMS or process status.
* **"CONT/clk_keep already prevents capture loss."** It did not on this
  kernel. The audio module docstring and config comments claimed it without
  measurement; they now describe the observed opt-in silent-TX workaround.
* **"dmix/dsnoop causes this particular dropout."** Direct plughw reproduced
  it too. Capture alone worked, and silent TX fixed the transition in the
  shared topology. This does not rule out other plugin timing issues.

* **"aplay says no sound cards, so overlay/probe failed."** That was a
  permissions artifact: `/proc/asound/cards` and kernel logs showed `owl` already
  registered. The same query as robotowl/root listed it; the owner could already
  hear the owl WAV. Check both card registration and caller permissions first.

* **"The uncompiled driver uses valid kernel/ASoC names."** The first native
  build failed: `SND_SOC_DAIFMT_CFC_CFC` does not exist. The codec-consumer
  format is `SND_SOC_DAIFMT_CBC_CFC`; the dummy codec also needs component
  name `snd-soc-dummy` and DAI name `snd-soc-dummy-dai`, not just the former
  as its DAI name. The running kernel's ASoC DAI listing confirms the latter.
* **"Dotted PCM names are harmless."** `owl.playback` and `owl.capture`
  resolved to Unknown PCM even after quoting the definitions. Underscore names
  reach the expected hardware-card lookup. This was tested without opening
  the XIAO serial port or enabling the audio controller.
* **"The generic installer matches DietPi."** The package is
  `device-tree-compiler` (not `dtc`); headers use the vendor package above;
  the serial group is `dialout` (not `dial`); and DietPi uses `user_overlays`,
  not `dtoverlay=`. The installer now preserves deployed config on unattended
  re-runs and supports `--no-reboot --no-enable-service` for safe staging.

* **"Just add a DT overlay, like the Pi's hifiberry-i2s-lite."** No. That works
  on the clean `snd-soc-bcm2835` driver. The A733 vendor controller needs the
  data-delay fix (R-015.2), which is not DT-exposable — so an overlay alone
  yields an amp that plays one bit off and a mic that returns garbage.
* **"The amp needs MCLK."** No. Neither the MAX98357A nor the ICS43434 uses
  MCLK; PB4 is unconnected. Requiring an MCLK would dead-end the design.
* **"The overlay must also configure `ac101`."** No. `ac101` may not exist in
  the 3W base `.dts`; a `target=<&ac101>` to an undefined label fails the *whole*
  overlay, whereas an orphaned `ac101` node is harmless. The overlay disables
  `i2s0_mach` only.
* **"`dmix`/`dsnoop` is always the right topology."** Unproven on the 3W. The
  a7z reference deliberately avoids it (plain `plug` on `hw:`). Kept as the
  default with a documented fallback rather than asserted.

## Acceptance

1. On the board, `aplay -l` lists a card named **`owl`**.
2. A test tone through `owl_playback` is audible from the MAX98357A (and is not
   one bit off — no DC offset / no hard clipping at the edge).
3. `arecord -D owl_capture -f S16_LE -r 48000 -d 3 out.raw` captures the mic:
   non-silent, and **not** the data-delay failure signature (samples alternating
   0 / full-scale).
4. Run `orangepi-brain/tools/check_audio_duplex.py` as robotowl: every second
   after one-shot playback closes must still contain nonzero mic data. Run it
   against the broken condition first; a successful process exit is not enough.
5. `cd orangepi-brain && python3 tests/run_tests.py` is still green.
6. `bash -n setup.sh` and `bash -n deploy/install.sh` both pass.

## Open

* **Other OS images/kernels remain unverified.** This acceptance is for the
  installed DietPi/vendor kernel, not a generic A733 driver compatibility claim.
* **Whether `CONT` is needed or beneficial on the A733.** It is on by default
  (R-015.6) with a DT opt-out; whether it must be on for the mic to stay awake,
  or is neutral, is unknown until the board runs.
* **Underlying TX-close/RX failure and sustained timing.** Silent TX prevents
  the reproduced failure; it does not establish its exact hardware cause or
  validate hours of operation. If capture underruns or playback stutters,
  compare the a7z-style config documented in `asound.conf` and record the result.
