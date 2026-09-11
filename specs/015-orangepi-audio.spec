# SPEC-015: Orange Pi audio — the A733 I2S stack

Status: open — implemented in code (driver, overlay, asound.conf, installer);
        never run on the A733 board (built on a Mac; the vendor kernel module
        cannot be neither compiled nor loaded here)
Verified: not yet — first flash of `orangepi-brain/setup.sh` on a DietPi
          Orange Pi Zero 3W is the acceptance test (see Acceptance)
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

**Continuous clock ON by default, with a DT opt-out.** `SND_SOC_DAIFMT_CONT`
keeps the ICS43434 powered between captures (a stopped MCLK/LRCK can leave the
mic silent until the next stream re-clocks it). It is on by default and can be
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
2. A test tone through `owl:playback` is audible from the MAX98357A (and is not
   one bit off — no DC offset / no hard clipping at the edge).
3. `arecord -D owl:capture -f S16_LE -r 48000 -d 3 out.raw` captures the mic:
   non-silent, and **not** the data-delay failure signature (samples alternating
   0 / full-scale).
4. `cd orangepi-brain && python3 tests/run_tests.py` is still green.
5. `bash -n setup.sh` and `bash -n deploy/install.sh` both pass.

## Open

* **Never run on the A733 board.** Everything here was written on a Mac; the
  `owl_i2s` module cannot be compiled or loaded without the
  `orange-pi-6.6-sun60iw2` kernel build tree. The first `setup.sh` flash is the
  real test, and any of the "Verified facts" above may need correcting against
  it.
* **DT-overlay enablement on DietPi is unverified.** Armbian/DietPi images select
  overlays differently (`armbianEnv.txt` `dtoverlay=`, U-Boot `fdtoverlay`, or
  `extlinux.conf`). `setup.sh` probes the boot dir, tries `armbianEnv.txt`, and
  **warns** (not dies) if it is absent, pointing here. Fallback: merge the
  overlay into the base `.dtb` (dtc decompile → edit → recompile).
* **Whether the base `.dts` already sets `i2s0_plat` pinctrl.** The overlay sets
  it; a conflict would surface as a `dtc` warning at compile time.
* **Whether `snd-soc-dummy` is the correct DAI name on this kernel.** Assumed
  from the Whisplay reference; unconfirmed against the 3W kernel.
* **Whether `CONT` is needed or beneficial on the A733.** It is on by default
  (R-015.6) with a DT opt-out; whether it must be on for the mic to stay awake,
  or is neutral, is unknown until the board runs.
* **`dmix`/`dsnoop` vs `plug`-on-`hw:` timing on the 3W.** Default is
  dmix/dsnoop; if capture underruns or playback stutters, switch to the a7z-style
  config documented in `asound.conf` and record which worked here.
