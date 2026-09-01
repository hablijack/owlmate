# ESP32-S3 Wiring Guide — XIAO ESP32-S3

Complete **XIAO ESP32-S3** wiring guide, matching the firmware pin assignments in `esp32-s3-sense/include/config.h`.

> ⚠️ **Silkscreen caution**: On the XIAO ESP32-S3, D4/D5 are printed with their *default* I2C function (`SDA`/`SCL`). That is only the default — the firmware remaps I2C onto D0/D1 and uses D4/D5 as plain GPIO/SPI via the ESP32-S3 GPIO matrix. Wire them to the LCDs as listed below, not to I2C devices.

> ⚠️ **`config.h` is authoritative, not this file.** If the two ever disagree,
> `esp32-s3-sense/include/config.h` wins and this table is the bug. The table
> below was corrected against it on 2026-08-27; before that it had the two eyes'
> CS lines swapped, listed the left DC on an unused pin, and put the vibration
> sensor on **D3 — which is the right eye's DC**. Soldering to the old table
> would have shorted the two together.

> **Side convention**: "left" and "right" are the **owl's own** left and right,
> as it would describe them looking forward. Standing in front of the owl, its
> left eye is the one on your right. Same convention as `config.h`.

---

## 🔌 All Pins You Need to Solder

Cable colours are the harness as physically built, so an existing loom can be
traced without a continuity check.

| Board PAD | GPIO | Purpose | Cable | Connect To |
|:---------:|:----:|---------|:-----:|------------|
| **D0** | GPIO 1 | I2C SDA | — | LSM303AGR IMU + PA1010D GPS + PCA9685 Servo Driver |
| **D1** | GPIO 2 | I2C SCL | — | LSM303AGR IMU + PA1010D GPS + PCA9685 Servo Driver |
| **D2** | GPIO 3 | Vibration sensor (SW420) | — | SW420 signal pin (DO) |
| **D4** | GPIO 5 | LCD SPI SCK (shared) | yellow | Both LCDs: CLK |
| **D8** | GPIO 7 | LCD SPI MOSI (shared) | white | Both LCDs: DIN |
| **D6** | GPIO 43 | LCD RST (shared) | blue | Both LCDs: RST |
| **D7** | GPIO 44 | LCD CS — **Left** eye | orange | Left LCD: CS |
| **D10** | GPIO 9 | LCD DC — **Left** eye | green | Left LCD: DC |
| **D9** | GPIO 8 | LCD CS — **Right** eye | orange | Right LCD: CS |
| **D3** | GPIO 4 | LCD DC — **Right** eye | green | Right LCD: DC |
| **GP10** | GPIO 10 | Camera XCLK | — | Sense expansion camera (back B2B pad) |

Both panels also take **VCC (red) → 3.3 V**, **GND (black) → GND** and
**BL (purple) → the same 3.3 V as VCC** — the backlights are always on.

> **GPIO 10** is the camera's XCLK on the XIAO ESP32-S3 **Sense** — it must **not** be used for the LCDs. Neither eye's CS uses it.

> The eye pin assignments were verified on hardware on 2026-08-26 by driving
> each panel a different colour and noting which eye lit up. To re-settle it in
> 30 seconds: `pio run -e dualtest -t upload` drives one eye red, the other blue.

---

## 🔋 Power & Ground — Combine These Lines

| Rail | Devices to combine |
|------|-------------------|
| **3.3V** | LSM303AGR IMU, **PA1010D GPS**, PCA9685 Servo Driver, both LCDs (logic + backlight) |
| **5V (optional)** | Servo motor supply (recommended: external 5V for servos, not the Pi rail) |
| **GND** | Everything — all I2C devices, both LCDs, vibration sensor, servo driver, GPS |

> 💡 **Tip**: Use a small breadboard or perfboard as a power rail to daisy-chain 3.3V and GND instead of running individual wires from the ESP32 to every component.

---

## 📡 Raspberry Pi Connection — Direct Native USB (no cable)

> **⚠️ NOT YET BUILT as of 2026-09-01.** Everything else in this file is the
> harness as physically wired; this section is the *planned* link, documented
> ahead of time because the pad identification was verified against Seeed's
> schematic and netlist (see below) and is worth not re-deriving. Delete this
> notice once the four wires exist.
>
> **Why this link is needed at all:** the IMU sits in the owl's head, and
> mounting it means closing the head, which makes the XIAO's USB-C connector
> inaccessible. Without a soldered USB path there would be no serial telemetry
> once assembled — and `tools/kalibrieren.py` needs it. (Firmware updates alone
> would survive via OTA, 4 taps → SoftAP `RobotOwl-Update` → `/update`.)

The firmware runs **USB CDC** (`ARDUINO_USB_CDC_ON_BOOT=1`, `ARDUINO_USB_MODE=1`), so `Serial` maps to the chip's **native USB**. Instead of a USB-C cable, solder the XIAO's **backside D+/D− pads** directly to the Raspberry Pi 4's USB pads for a compact, cable-free link (perfect for embedding both boards in the owl body).

`Serial` appears on the Pi as **`/dev/ttyACM0`** (USB 2.0 Full-Speed, 12 Mbps CDC).

### Wire mapping

| XIAO ESP32-S3 | Raspberry Pi 4 (USB pads) | Wire colour | Notes |
|:--------------|:------------------------|:-----------:|-------|
| **`TP8`** — bottom pad, net `ESP_USB_D+` (GPIO20) | USB **D+** | Green | twist with D− |
| **`TP7`** — bottom pad, net `ESP_USB_D−` (GPIO19) | USB **D−** | White | twist with D+ |
| **`5V`** — **edge castellation** (net `VBUS`) | USB **5V** | Red | powers the XIAO, any USB port works |
| **`GND`** — **edge castellation** (or bottom pad `TP1`) | USB **GND** | Black | mandatory, common ground |

Only the **data pair** needs the fiddly bottom pads. `5V` and `GND` are ordinary
edge castellations sitting next to each other, and the edge `5V` pad is
electrically the *same net* as the USB-C connector's VBUS (`U9` pad 14 =
`VBUS`), so there is no reason to hunt for a bottom pad for power.

Standard USB cable colours are red = VBUS, black = GND, green = D+, white = D−,
so cutting up a known-good USB cable gives you a correctly twisted data pair for
free — **but meter the colours, cheap cables lie.**

### Locating the pads

- **XIAO side:** the backside carries **eight test pads in a 2×4 grid**, 2.54 mm
  pitch, sitting behind the USB-C connector. Read out of Seeed's KiCad netlist
  for the Sense v1.5, so these are designators and nets, not guesses:

  Viewed **from the bottom** with the USB-C connector at the top (so the `5V`
  castellation column appears on the **left**):

  ```
               USB-C
     ┌──────────────────────┐
  5V │●                    ●│ D0
     │      TP4  TP3        │
  GND│●                    ●│ D1
     │      TP1  TP6        │
  3V3│●                    ●│ D2
     │      TP5  TP2        │
  D10│●                    ●│ D3
     │      TP8  TP7        │
  D9 │●     D+   D−        ●│ D4
  D8 │●                    ●│ D5
  D7 │●                    ●│ D6
     └──────────────────────┘
  ```

  | pad | net | use |
  |:---:|:----|:----|
  | `TP7` | `ESP_USB_D−` | **solder D− here** |
  | `TP8` | `ESP_USB_D+` | **solder D+ here** |
  | `TP1` | `GND` | usable ground |
  | `TP6` | `EN` | ☠️ chip enable — bridging to GND resets the board |
  | `TP4` | IO40 / `CAM_SDA` | ☠️ **camera I²C — in use** |
  | `TP5` | IO39 / `CAM_SCL` | ☠️ **camera I²C — in use** |
  | `TP3` | IO41 / `PDM_DATA` | ☠️ microphone — in use |
  | `TP2` | IO42 / `PDM_CLK` | ☠️ microphone — in use |

  **The data pair is the row FURTHEST from the USB-C connector**, roughly level
  with the `D10`/`D3` castellations, and `D+` is on the same side as
  `5V`/`GND`/`3V3`. The six pads you must not bridge are only 2.54 mm away —
  `TP4`/`TP5` are the camera's I²C lines (`GPIO40`/`GPIO39` in `config.h`), so a
  slip there kills face detection, not the USB link.

- **Identify the pads by RESISTANCE, not continuity.** `R3` and `R4` are **22 Ω**
  in series between the USB-C connector and the ESP32, and `TP7`/`TP8` sit on the
  **chip side** of them. So with the board unpowered:
  - `TP7` → USB-C pin **A7/B7** reads **≈22 Ω**
  - `TP8` → USB-C pin **A6/B6** reads **≈22 Ω**
  - `TP1` → edge `GND` reads **0 Ω**

  A beep-test looking for 0 Ω on the data pads will report "wrong pad" on the
  *correct* pad. The 22 Ω reading is a positive fingerprint — use it, it is the
  one check that does not depend on reading the mirror image correctly.

- **Tapping at `TP7`/`TP8` bypasses those 22 Ω resistors.** They are series
  termination for signal integrity; over a short lead at USB full-speed
  (12 Mbps) their absence is fine, and tapping the connector pads instead is far
  harder for no real gain. `C1`/`C2` on these lines are marked **DNP** and there
  is no ESD array or common-mode choke, so nothing protective is being skipped.
- **Pi 4 side:** the Pi 4 has **4 USB-A ports** on the right edge (2× USB 3.0 using the blue ports, 2× USB 2.0 using the black ports). All four are behind the same VL805 hub, so any of them works. The solder pads are the **through-hole pads on the underside of the PCB**, directly below each USB connector.
  - **Pick a USB 2.0 (black) port.** The USB 3.0 (blue) ports still carry the D+/D− lines, but the connector has 9 pins and the super-speed pairs sit right beside the signal pads — much easier to bridge accidentally. A black port's 4 pads are: **VBUS, D−, D+, GND** (verify order with a multimeter).
  - Mark the chosen port and **never plug anything into it** afterwards.
  - Use the bottom pads on the board → no connector removal needed; tin them gently and keep wires under ~6 cm.

### Soldering recipe

1. **Tin** each XIAO pad and each Pi pad with a fresh, small solder bead (leaded solder, flux core).
2. Use short **30–32 AWG** silicone or enameled (magnet) wire. Cut four lengths of ~3–6 cm.
3. **Twist the DP + DN pair** tightly together (~3–6 twists/cm) and keep them far from the 5V and GND wires — this preserves the USB differential signal.
4. **Common ground is critical**: GND must be joined between the two boards (do not rely on a shared supply only).
5. Solder **one wire at a time**, then re-check with a multimeter: no shorts
   between `TP7`/`TP8`, `TP8`↔5V, `TP7`↔GND — and none to the neighbouring
   `TP2`–`TP6`, especially the two camera I²C pads.
6. Add a blob of hot glue over the joints to relieve strain.

### Powering the XIAO

- With this method the XIAO is powered from the **Pi's 5V rail** via the USB VBUS wire; its onboard regulator produces 3.3V.
- Do **not** also plug a USB-C cable into the XIAO while the direct solder link
  is live. This is not mere caution: the edge `5V` pad and the connector's VBUS
  are **literally the same net** with no isolation diode between them, so you
  would tie two 5 V supplies together *and* put two USB hosts on one device's
  data lines.
- Servos draw high current — power the PCA9685 motor rail from a **separate 5V supply with a common GND**, not from the Pi's rail.

> ⚠️ If the Pi doesn't enumerate the XIAO (`/dev/ttyACM0` missing), first check a
> **`TP7`/`TP8` swap** (most common — D+/D− reversed simply never enumerates),
> then GND continuity, then wire length. Re-check contrast with a normal USB-C cable to isolate firmware vs. solder issues.

---

## 🔊 Audio — MAX98357A (Adafruit) on the Raspberry Pi

The owl's voice/output is a **MAX98357A** mono class-D amplifier (Adafruit 2980 / 3322) driven over the Pi's **I2S** bus. It takes a small speaker (4Ω or 8Ω) directly. It is **entirely on the Raspberry Pi side** — no ESP32 pins are used, so it does not touch the pin map above.

The RPi brain generates short procedural sound effects (beeps/chirps) in-process and plays them through the amp with `aplay` (ALSA). All audio lives on the Pi; the ESP32 is not involved in sound.

### Wire mapping (Pi 40-pin header → MAX98357A)

| MAX98357A pin | Raspberry Pi pin | Function |
|:-------------:|:----------------:|----------|----------|
| **GND** | GND (pin 6/9/14/20/25) | Common ground |
| **BCLK** | GPIO 18 (pin 12) | Bit clock |
| **LRCLK** | GPIO 19 (pin 21) | Word-select (L/R) |
| **DIN** | GPIO 21 (pin 40) | Serial data in |
| **SD MODE** | 3.3V (pin 1) | **Tie to 3.3V** for I2S (leave floating = PWM) |
| **GAIN** | 3.3V (pin 1) | High-gain mode (0 dB); tie to GND for −6 dB if too loud |
| **VSUP** | 5V (pin 2/4) | Amp supply (5–35 V). 5 V is fine for a small speaker |
| **Speaker +** | speaker + | 4Ω or 8Ω speaker |
| **Speaker −** | speaker − | Speaker ground |

> ⚠️ The **SD MODE pin must be tied to 3.3V** for I2S operation. Left floating, the amp defaults to PWM mode and the Pi's I2S output will be silent. This is the #1 "no sound" mistake.

### Enable I2S on the Pi
Add to `/boot/config.txt` (or `/boot/firmware/config.txt` on Bookworm) and reboot:
```
dtoverlay=hifiberry-i2s-lite
```
This maps the standard I2S pins (BCLK=18, LRCLK=19, DIN=21) to the `snd-soc-bcm2835` driver, so `aplay -l` shows a `bcm2835` playback device. (The MAX98357A needs no codec I2C address — it's a dumb amp — so no `dtparameter` is required.)

### Verify
```
aplay -l                      # should list a bcm2835-I2S-hw-0 playback device
sudo apt install espeak-ng    # optional: test with a real voice
espeak-ng "hello owl"         # should speak through the speaker
```

### Software
`brain/audio.py` generates WAV bytes in-process (no external assets) and plays them via `aplay` in a daemon thread, so the serial read loop is never blocked. See the **NDJSON Protocol** table in `README.md` for the `sound` command the RPi forwards.

---

## 📊 I2C Bus Summary (Physically on D0 + D1)

| Device | Address | SDA → D0 | SCL → D1 |
|--------|:-------:|:--------:|:--------:|
| LSM303AGR IMU (accel) | 0x19 | ✅ | ✅ |
| LSM303AGR IMU (magnetometer) | 0x1E | ✅ | ✅ |
| PA1010D GPS | 0x10 | ✅ | ✅ |
| PCA9685 Servo Driver | 0x40 | ✅ | ✅ |

All four share the same bus — no conflict since each has a unique address. Note the IMU is **two** devices on one breakout: a missing 0x1E with 0x19 present means the board is there but the magnetometer is mute.

---

## ✅ GPS (PA1010D) — Now on I2C (firmware fixed)

The GPS is wired on the **I2C bus (D0/D1)** and `Sensors.cpp` now reads it natively:

- `Adafruit_GPS GPS(&Wire)` — I2C driver, no `HardwareSerial`/UART pins involved
- `GPS.begin(ADDR_GPS)` (0x10) probes the module on the bus
- `PMTK_SET_NMEA_OUTPUT_RMCGGA` + `PMTK_SET_NMEA_UPDATE_1HZ` configure the output sentences

The old `gpsSerial.begin(..., 1, 2)` on the I2C pins has been removed, so GPIO 1/2 are exclusively I2C now.

---

## 🔢 Complete Pin Reference (XIAO ESP32-S3 Board Pads)

> This is a **second** view of the same pin map as the soldering table above,
> which is why it drifted: on 2026-08-27 the first table was corrected by hand
> and this one was missed, leaving five wrong rows — vibration on D3 (which is
> the right eye's DC), the left DC on an unused pin, and both CS lines swapped
> again. `tools/check_docs.py` now parses **every** `**D<n>**` row in this file
> against `config.h`, so the two cannot disagree again. Run it after editing.

| Board Pad | GPIO | Used? | Function |
|:---------:|:----:|:-----:|----------|
| D0 | 1 | ✅ | I2C SDA |
| D1 | 2 | ✅ | I2C SCL |
| **D2** | **3** | ✅ | Vibration sensor (SW420) |
| **D3** | **4** | ✅ | LCD DC — Right |
| **D4** | **5** | ✅ | LCD SPI SCK (shared) |
| D5 | 6 | ⬜ | Free — the firmware does not use this pin |
| **D6** | **43** | ✅ | LCD RST (shared) — silkscreen `TX`, repurposed as GPIO |
| **D7** | **44** | ✅ | LCD CS — Left — silkscreen `RX`, repurposed as GPIO |
| **D8** | **7** | ✅ | LCD SPI MOSI (shared) |
| **D9** | **8** | ✅ | LCD CS — Right |
| **D10** | **9** | ✅ | LCD DC — Left |
| — | 10 | ✅ | Camera XCLK (Sense B2B, not a D pad) |
| D11 | 42 | ⬜ | Free |
| D12 | 41 | ⬜ | Free |

> D6 (GPIO 43) and D7 (GPIO 44) print `TX`/`RX`, but are used here as plain GPIOs (or left free). This works because native USB CDC does not occupy the UART pins.

---

## 🔌 Connection Count Summary

1. **SDA** → D0 (branches to LSM303AGR + GPS + PCA9685)
2. **SCL** → D1 (branches to LSM303AGR + GPS + PCA9685)
3. **3.3V** → all 3.3V devices (common rail)
4. **GND** → all devices (common rail)
5. **Vibration signal** → D3
6. **LCD SCK** → D4 (shared by both LCDs)
7. **LCD MOSI** → D8 (shared by both LCDs)
8. **LCD DC Left** → D5
9. **LCD CS Left** → D9
10. **LCD DC Right** → D10
11. **LCD CS Right** → D7
12. **LCD RST** → D6 (shared by both LCDs)
13. **USB data pair** → XIAO D+/D− (backside) to Pi 4 USB 2.0 port pads
14. **Power to Pi link** → 5V (VBUS) + GND to Pi 4 USB port pads

With common power rails this collapses to ~12 signal wires plus the 4-wire native-USB link to the Raspberry Pi.