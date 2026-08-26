# SPEC-003: Two LCD panels on one SPI bus

Status: implemented
Verified: 2026-08-26 — both panels driven independently with distinct images
Depends on: 002 (PSRAM for the framebuffers)

## Intent

Two round 160x160 GC9D01 panels are the owl's eyes. The ESP32-S3 has few free
pins, so the panels share the SPI bus. They must be addressable independently —
a shared bus that can only ever show the same thing on both eyes is useless.

## Requirements

* **R-003.1** Both panels share SCK, MOSI and RST; each has its own CS and DC.
* **R-003.2** Either panel must be able to show an image the other is not showing.
* **R-003.3** Initialising the second panel must not disturb the first.
* **R-003.4** A failed framebuffer allocation must degrade to "no image", never
  to a crash loop.

## Decisions

**Chip-select is driven in software, by the driver, around every transaction.**
`GC9D01::startWrite()` / `endWrite()` are nestable via a depth counter, so a
compound operation (a full `flush()` = CASET + RASET + RAMWR + 51,200 bytes of
pixels) is one single chip-select assertion.

**The SPI bus is always injected, never owned.** `attachBus(SPIClass*)` is the
only way in; the driver has no `SPIClass` member of its own and never calls
`begin()`. The bus is begun **once**, by the caller, with **SS = -1**.

**`attachBus()` parks the panel deselected immediately** (CS as OUTPUT, HIGH).
This makes the ordering rule enforceable by construction: attach every panel
before initialising any of them, and no panel can be listening while a sibling
is being set up.

**The shared reset line is pulsed exactly once**, by `resetShared()`, before any
`begin()`. A second `begin()` pulsing RST would wipe the first panel's completed
initialisation.

Bring-up order, encoded in `bringUpDisplays()` in `main.cpp`:

    bus.begin(SCK, -1, MOSI, -1);   // once, SS = -1
    left.attachBus(&bus);           // parks CS high
    right.attachBus(&bus);          // both, before any begin()
    left.resetShared();             // once
    left.begin();  right.begin();

**Drawing never touches SPI.** All draw calls write to a PSRAM framebuffer
(160x160x2 = 51,200 bytes per eye); `flush()` is the only thing that transmits.

## Verified facts

* `LCD_SPI_FREQ` = 16 MHz. Frame time measured: **~61 ms per eye**, 161 ms for
  both at the earlier 6 MHz. A full frame is 51,200 bytes, so frame time is
  roughly 820 kbit / clock.
* Pin map, verified on hardware by driving each panel a different colour and
  asking which eye lit up: shared CLK=D4/GPIO5, DIN=D8/GPIO7, RST=D6/GPIO43;
  **left** CS=D7/GPIO44 DC=D10/GPIO9; **right** CS=D9/GPIO8 DC=D3/GPIO4.
* The panel connector is **8 pins** — VCC, GND, DIN, CLK, CS, DC, RST, BL.
  There is no SDO/MISO. Nothing can be read back from these panels, ever.

## Falsified

This subsystem produced the two most expensive wrong beliefs in the project.

* **"Both panels are dead / the panel logic net is unpowered."** The evidence was
  that every ID read returned `0x00` on every DC×CS combination, plus a 0.00 V
  reading taken with `touchRead()` on a pin assumed to be the 3.3 V pad. But the
  connector has **no data-out line** — an ID read cannot return anything but
  zero regardless of panel health. The whole diagnosis rested on a measurement
  that is structurally incapable of producing a different answer. Both panels
  were fine. **Do not write an ID/readback probe for these panels.**
* **"27 MHz is too fast for hand-wired jumpers, that is why no pixels arrive."**
  No clock rate would have worked: no chip-select was ever asserted. The panels
  were not refusing data, they were never addressed. The clock was reduced to
  6 MHz on this theory and has since been raised to 16 MHz with no ill effect.

The actual root cause: the driver never touched CS in any write path. A single
panel worked anyway because `_cs` was passed to `SPIClass::begin()` as the
peripheral's **hardware SS pin**, which the SPI driver toggles invisibly. The
shared-bus path begins with `SS = -1`, so with two panels neither was ever
selected. **Never reintroduce hardware SS here.**

A third, smaller one: `Eyes::renderEye()` drew a complete frame and never called
`flush()`. Even with CS fixed, the eye renderer would have painted to memory
forever.

## Acceptance

`pio run -e dualtest -t upload`, then look at the owl:

1. Phase A — one eye solid red, the other solid blue.
2. Phase B — the colours swap. (This is the part that proves independent
   addressing rather than coincidence.)
3. Phase E — a two-tone horizontal split on each, proving the address window.

Any panel showing the other's content, or both showing the same colour, is a
chip-select fault.

## Open

* The clock can probably go above 16 MHz; nothing has been measured above it.
  Watch for torn or speckled pixels, which is what an over-clocked panel
  looks like.
