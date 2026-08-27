# SPEC-000: Index and format

Status: living document
Last updated: 2026-08-27

## What this directory is

The single source of truth for *what this machine is supposed to do and why*.
`README.md` describes the project to a reader; `BACKLOG.md` tracks work;
`AGENTS.md` tells a coding agent how to move around. These specs record the
**decisions** — with the measurements that justify them and the hypotheses that
turned out to be wrong.

They were written **retroactively**, on 2026-08-26, after the subsystems already
worked. That is unusual and worth stating plainly: the numbers in here are not
targets that were designed up front, they are values measured on the one physical
owl that exists. Treat them as observations, not as guarantees about a second
unit built from the same parts.

Revised on **2026-08-27** after a refactoring pass. That pass turned up a trap
specific to retroactive specs, recorded in SPEC-010: a spec can describe the
design as *intended* and be read as describing the code. SPEC-010 listed three
telemetry fields the RPi never parsed and called its dataclasses frozen when
they were mutable — and the closer a document is to right, the less likely
anyone checks it. Where a claim here is now backed by a test, the spec says so;
treat an unbacked claim as a description of intent.

## Why the "Falsified" sections matter most

Nearly all the time lost on this project went into believing a measurement that
could not mean what it appeared to mean. A dead-panel diagnosis from ID reads
over a bus with no data-out line. A "broken I2C bus" that was a normal NACK
during a chip reset. A "failing solder joint" that was a potentiometer being
turned between two measurements. Each spec therefore carries a **Falsified**
section. Read it before re-diagnosing anything in that subsystem.

## Format

Every spec follows the same skeleton:

    # SPEC-NNN: Title
    Status:      implemented | partial | open
    Verified:    date + how it was verified on hardware
    Depends on:  other specs

    ## Intent          - what problem this solves, in one paragraph
    ## Requirements    - numbered, testable statements
    ## Decisions       - what was chosen, and the reason
    ## Verified facts  - measurements, with numbers and dates
    ## Falsified       - what we believed that measurement disproved
    ## Acceptance      - how to tell it still works
    ## Open            - what is unresolved

Requirements are numbered `R-NNN.n` so they can be cited from code comments and
commit messages.

## Index

| Spec | Subject | Status |
|---|---|---|
| [001](001-system-architecture.spec) | System split, who owns behaviour | implemented |
| [002](002-build-and-toolchain.spec) | Platform, framework modes, partitions, flashing | implemented |
| [003](003-display-bus.spec) | Two LCDs on one SPI bus | implemented |
| [004](004-eye-expressions.spec) | Visual language and the shape model | implemented |
| [005](005-i2c-bus.spec) | I2C bus and its three devices | implemented |
| [006](006-imu-orientation.spec) | IMU axes, mounting, calibration, heading | partial |
| [007](007-vibration-and-ota.spec) | Vibration sensing and OTA entry | implemented |
| [008](008-camera.spec) | Camera module, mounting, compatibility | implemented |
| [009](009-face-detection.spec) | On-device face detection | partial |
| [010](010-serial-protocol.spec) | NDJSON contract between ESP32 and RPi | implemented |
| [011](011-diagnostics.spec) | Diagnostic builds and host tools | implemented |
| [012](012-rpi-brain.spec) | RPi brain internals: ownership, packaging, testing | implemented |
| [013](013-navigation.spec) | Navigation — the owl as a compass | partial |
| [014](014-speech.spec) | Speech — hearing the user and reacting | partial |

## Conventions used throughout

* **Pin numbers are real GPIO numbers, never XIAO `D#` silkscreen labels.** The
  two differ on this board and confusing them has broken this project before:
  D0=1 D1=2 D2=3 D3=4 D4=5 D5=6 D6=43 D7=44 D8=7 D9=8 D10=9.
* **"Left" and "right" always mean the owl's own left and right**, as it would
  describe them looking forward. Standing in front of the owl, its left side is
  the one on your right. This holds for the eyes and the servos alike — worth
  stating because both were named before the convention was written down, and it
  was confirmed with the owner on 2026-08-27 that they agree. If the eyes ever
  look mirrored, `pio run -e dualtest -t upload` settles it in 30 seconds.
* `esp32-s3-sense/include/config.h` is the authority for pins and timing
  constants. A spec that contradicts it is out of date; fix the spec.
* Documentation and code comments are English. Scripts under
  `esp32-s3-sense/tools/` speak German to the user on purpose — they are read
  live while working on the hardware.
