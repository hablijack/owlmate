# SPEC-002: Build, framework modes, partitions, flashing

Status: implemented
Verified: 2026-08-26, all seven environments build; firmware runs on hardware
Depends on: 009 (face detection is why the firmware env is what it is)

## Intent

The firmware needs esp-dl for on-device face detection, and esp-dl is only
reachable in one particular PlatformIO build mode — one that costs two orders of
magnitude in build time. The diagnostic builds, which exist to be run over and
over while probing hardware, must not pay that price.

## Requirements

* **R-002.1** The firmware build must be able to pull managed ESP-IDF components.
* **R-002.2** Diagnostic builds must stay fast (target: seconds, not minutes).
* **R-002.3** The octal PSRAM must initialise. It carries the LCD framebuffers
  and the camera buffers; without it neither works.
* **R-002.4** Flashing must never destroy stored calibration data.
* **R-002.5** A diagnostic env must build only the source it is diagnosing. An
  image that links the whole firmware cannot answer a question about the whole
  firmware's load.

## Decisions

**Two framework modes in one project.**

| | firmware `[env:xiao_esp32s3]` | diagnostics |
|---|---|---|
| framework | `arduino, espidf` | `arduino` |
| build time | ~40 s incremental, ~280 s cold | 1.5–10 s |
| esp-dl | available | not needed |

Common settings live in an `[arduino_base]` section; the diagnostic envs extend
*that*, not the firmware env. Extending the firmware env would have dragged them
into espidf mode.

**PSRAM is configured in `sdkconfig.defaults`, not via `board_build.*`.**
`board_build.memory_type = qio_opi` is an Arduino-framework option and has no
effect in espidf mode. The equivalent is `CONFIG_SPIRAM_MODE_OCT=y`.

**Partitions were re-cut to fit esp-dl.** The firmware needs ~3.23 MB; the
original 3.00 MB OTA slots were too small. The 1.875 MB `spiffs` partition was
unused (no SPIFFS/LittleFS/FFat anywhere in the code) and was folded into the
app slots:

    nvs       0x9000   0x6000     <- IMU calibration lives here, do not move
    otadata   0xf000   0x2000
    phy_init  0x11000  0x1000
    ota_0     0x20000  0x3F0000   (4.03 MB)
    ota_1     0x410000 0x3F0000   (4.03 MB)

Dual-bank OTA is kept: the owl can be updated over its own SoftAP (SPEC-007).

**Core logging is off in the firmware** (`CORE_DEBUG_LEVEL=0`). That USB CDC port
carries the NDJSON protocol the Orange Pi parses; stray `[E][esp32-hal-i2c]` lines are
protocol garbage. Diagnostic envs override `build_flags` and keep their logs.

**It only covers the Arduino core, not ESP-IDF components** — found 2026-08-31,
the first time anyone captured the OTA transition. `WiFi.softAP()` emits about
forty `I (…) wifi:` / `phy_init` / `esp_netif_lwip` lines directly onto the
protocol port, and the Orange Pi logs a parse warning for each. The flag that would
cover those is `CONFIG_LOG_DEFAULT_LEVEL_NONE` in `sdkconfig.defaults`, not
`CORE_DEBUG_LEVEL`. Unfixed; BACKLOG Step 6 item 6, SPEC-007 Open.

**The firmware uses both cores** since 2026-08-31, which is a build-relevant
fact and not only an architectural one: the Arduino loop is pinned by
`CONFIG_ARDUINO_RUNNING_CORE=1` in the generated `sdkconfig.xiao_esp32s3`, and
`src/vision.cpp` pins its detection task to core 0 on the strength of that. If
that config value ever changes, both tasks land on one core and the stall
returns silently — nothing would fail to build. See SPEC-001.

## Verified facts

Measured 2026-08-26:

* Firmware image 3,230,773 bytes = **78.3 %** of a 4,128,768-byte slot.
  RAM 20.3 %.
* PSRAM in espidf mode: `octal_psram: vendor id 0x0d (AP), density 0x03
  (64 Mbit), good-die Pass` → `Found 8MB PSRAM device, Speed: 80MHz` →
  `SPI SRAM memory test OK`. A 256 KB write/read test passed.
* `pio run -e dualtest` still completes in ~1.5 s with an `idf_component.yml`
  present in `src/`, confirming the Arduino-only envs are unaffected by it.

Four hard requirements discovered by hitting them:

1. `CONFIG_FREERTOS_HZ=1000`. The Arduino core checks this in its CMakeLists and
   aborts: *"esp32-arduino requires CONFIG_FREERTOS_HZ=1000 (currently 100)"*.
   The IDF default is 100.
2. `CONFIG_LIBC_NEWLIB=y`. Under picolibc, `espressif__cbor` fails to compile —
   it uses `fopencookie()`, which picolibc lacks.
3. **`sdkconfig.defaults` is only read when no `sdkconfig.<env>` exists.** A
   stale committed `sdkconfig.xiao_esp32s3` silently overrode everything. After
   changing defaults, delete the generated file or the change does nothing.
4. No stray `CMakeLists.txt` outside `src/`. A committed `main/CMakeLists.txt`
   from an ancient ESP-IDF attempt registered `main.c`/`gc9d01.c`/`pca9685.c` —
   files that never existed in this C++/Arduino project. Invisible in Arduino
   mode, fatal in espidf mode. And `src/CMakeLists.txt` must glob only `*.c`
   and `*.cpp`; a `src/*.*` glob sweeps up `idf_component.yml` as a source file.

## Falsified

* **"Arduino core 3.x dropped esp-dl, so we must downgrade the core."** False in
  both halves. esp-dl is present in the platform as a component
  (`espressif__esp-dl` appears in the build component list); it is only missing
  from the *prebuilt Arduino libraries*. And downgrading was never an option
  anyway — the older core is what failed to initialise this board's PSRAM.
* **"Switching to espidf mode risks the PSRAM configuration."** It was the main
  worry before trying, and it turned out backwards: PSRAM comes up more reliably
  in espidf mode, with far better diagnostics than Arduino mode ever printed.
* **"`extends` inherits `build_flags`."** It does not — a `build_flags` in the
  child *replaces* the parent's rather than appending. Every env therefore
  re-lists the same `-DARDUINO_USB_*` and `-DFACE_DETECTION_ENABLED` flags, and
  `[env:xiao_esp32s3]` silently loses the `-DBOARD_HAS_PSRAM` that
  `[arduino_base]` sets. Harmless here only because in espidf mode `sdkconfig`
  owns PSRAM, not that flag — but it is an accident, not a decision. The
  consequence to watch for is that **the value of a flag depends on which env you
  are reading**: `FACE_DETECTION_ENABLED` is `0` in `[arduino_base]` and `1` in
  the firmware env, and `AGENTS.md` stated the `0` as though it were global.
  Use `${arduino_base.build_flags}` if this is ever tidied.
* **"A compile-time flag is enough to make a minimal image."** `powerprobe` was a
  `#if POWER_PROBE` branch inside `main.cpp`'s `setup()`/`loop()`, and its env had
  no `build_src_filter` — so it built the entire firmware, constructed every
  peripheral object, and skipped the work at runtime with an early `return`. Its
  only question is "does the 3.3 V rail hold under a LIGHT load?", which that
  image cannot answer, because the load was never light. Split into
  `src/powerprobe.cpp` on 2026-08-27: 297 KB flash / 22.5 KB RAM, against the
  firmware's 3.28 MB. Hence R-002.5. See also SPEC-011, where the same mistake is
  recorded as a diagnostics failure — it is both.

## Acceptance

1. `pio run` — all seven environments succeed.
2. A diagnostic env builds in under ~10 s.
3. Firmware boot log shows `Found 8MB PSRAM device` and `SPI SRAM memory test OK`.
4. Flash usage stays below ~85 % of the slot.

## Flashing — and the one way to get it wrong

The board exposes **only** USB-Serial/JTAG; there is no second UART broken out,
so esptool cannot enter download mode via DTR/RTS. Use `--before usb-reset`.
This is a hardware property and applies on every host.

**Never write `firmware.factory.bin` at 0x0.** The combined image spans past
0x9000 and erases the NVS partition, with 0xFF padding that reads as blank
flash. Doing this on 2026-08-26 destroyed the BNO055 calibration
(`restored: false`, `mag: 0`) and it had to be redone. Flash separately:

| offset | file |
|---|---|
| `0x0` | `bootloader.bin` |
| `0x8000` | `partitions.bin` (3072 B — fits its sector, does not reach NVS) |
| `0xf000` | `boot_app0.bin` |
| `0x20000` | `firmware.bin` |

## Open

* Nothing from the 2026-08-26 session is committed yet.
* **`esp32-s3-sense/sdkconfig.xiao_esp32s3` is still tracked by git.** It is a
  generated file and is now in `.gitignore`, but that does not untrack it — it
  will keep showing as modified until it is removed from the index once:

      git rm --cached esp32-s3-sense/sdkconfig.xiao_esp32s3

  This matters because a committed copy of it is exactly what caused the picolibc
  failure above: `sdkconfig.defaults` is only consulted when no `sdkconfig`
  exists, so a checked-in generated one silently wins.

  Tracked on purpose, by contrast: `sdkconfig.defaults` (hand-maintained),
  `CMakeLists.txt` and `src/CMakeLists.txt` (the latter carries a deliberate
  glob fix), and `dependencies.lock` (a component lockfile — tracking it is what
  makes the build reproducible).
