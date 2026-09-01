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

* `LCD_SPI_FREQ` = 16 MHz. A full frame is 51,200 bytes per eye.
  **A FULL flush of both eyes takes 149.9 ms and is not clock-bound** — the same
  149.9 ms at 40 MHz. This bullet asserted "~61 ms per eye, roughly
  820 kbit / clock" until 2026-08-31, which was the arithmetic this spec's own
  Falsified section debunks three sections further down; it sat here contradicting
  it for three days. A Verified-facts entry that was never measured is worse than
  no entry, because the heading is the claim.
* **A blink costs 102–126 ms of one loop iteration, and it is the largest
  remaining cost in the idle loop.** Measured 2026-08-31 over 1729 telemetry
  frames: idle `loop_max_ms` is 25 ms median with a p95 of 110–115 ms, the tail
  being the auto-blink at its 2.5–6.5 s cadence. That is the dirty-row flush
  doing its job — a blink repaints the lid band, not the 51,200 bytes a full
  frame would cost, which is why it is 110 ms and not 149.9. It is a legitimate
  cost, not a defect; noted so the next person reading a `loop_max_ms` sequence
  does not go hunting for it.
* Pin map, verified on hardware by driving each panel a different colour and
  asking which eye lit up. **"left"/"right" are the owl's own**, per SPEC-000: shared CLK=D4/GPIO5, DIN=D8/GPIO7, RST=D6/GPIO43;
  **left** CS=D7/GPIO44 DC=D10/GPIO9; **right** CS=D9/GPIO8 DC=D3/GPIO4.
* The panel connector is **8 pins** — VCC, GND, DIN, CLK, CS, DC, RST, BL.
  There is no SDO/MISO. Nothing can be read back from these panels, ever.

## Driver surface

`GC9D01` is deliberately small. Drawing only touches the PSRAM framebuffer;
`flush()` is what transmits, and `Eyes::renderEye()` ends with it.

Public: `attachBus`, `resetShared`, `begin`, `flush`, `fillScreen`, `fillRect`,
`fillCircle`, `drawLine`, `drawFastVLine`, `drawFastHLine`, `drawPixel`.
`drawRect` and `drawCircle` were removed on 2026-08-27 — neither had a caller,
internally or externally. The eye renderer works column by column through
`drawFastVLine` (that is what makes independent top and bottom edge profiles
fall out for free, see SPEC-004), so the outline primitives never found a use.
Unused generality in a driver on a 160×160 panel is cost without benefit; add
back whatever a caller actually needs.

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
* **"Frame time is set by `LCD_SPI_FREQ`, so 16 MHz gives ~61 ms for both
  eyes."** Measured 2026-08-28: both eyes flush in **149.9 ms at 16 MHz and
  149.9 ms at 40 MHz** — identical to 0.1 ms. The transfer is not clock-bound at
  all, and the ~61 ms figure that stood in `config.h` was arithmetic
  (`820 kbit / 16 MHz`) presented as a measurement. Two follow-up hypotheses
  died the same way, each giving the same 149.9 ms: the **PSRAM framebuffer**
  (moving it to internal RAM changed nothing) and the **byte swap** in
  `writePixels()` (`writeBytes()` without the swap changed nothing — legal here
  only because `COLOR_BG`/`COLOR_INK` are `0xFFFF`/`0x0000` and byte-order
  symmetric). What is left is per-chunk overhead inside the bulk transfer,
  ~1.56 us per byte. **Raising the clock to make the eyes faster does not
  work; do not try it again without re-measuring.**

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

* **RESOLVED 2026-08-29: the flush now sends only the rows that changed.**
  `GC9D01` tracks a dirty row span, and `drawPixel()`/`fillScreen()` mark a row
  only when a pixel's VALUE differs — so after a redraw the span is exactly the
  union of "where there was ink" and "where there is ink now". `flush()` sets the
  address window to that span, and returns without touching the bus at all when
  nothing changed.

  **Rows, not a true rectangle, and that is deliberate.** The bottleneck is
  per-transfer overhead (~1.56 µs/byte, identical at 16 and 40 MHz). A rectangle
  needs one transfer per row and loses to a single contiguous block even though
  the block carries untouched pixels either side.

  Measured on hardware, main loop while interacting:

      4.3 Hz   before any of this work
     10.5 Hz   after gating the dirty flag on a real change
     29 Hz     with the partial flush and a slower blink cadence
     29 Hz     steady, once the inference left the loop (2026-08-31)

  Blink cadence matters here because every blink forces a redraw of both eyes;
  it went from ~1.6 s to ~4.5 s between blinks (`BLINK_GAP_MIN_MS`).

  **The last row is the same number meaning something different, and the
  distinction is the whole point.** The 29 Hz above was a *mean* over a loop
  that ran at ~42 Hz between detections and stopped dead inside each one; the
  29 Hz below is what every single sample reads, because `FaceDetector_Detect()`
  now runs on core 0 (SPEC-009, SPEC-001). Judging this subsystem by its mean
  hid a periodic freeze for weeks. Measure the sequence.

  **This did not improve the detection rate.** Attempts rose 4.36/s → 5.93/s but
  hits did not follow, because the real limit was elsewhere entirely — see
  SPEC-009, where the face turned out to be too small in frame. The loop work is
  worth having for responsiveness; it was not the cause it was believed to be.

* **The render is now the largest cost in the main loop, and the loop is the
  cap on the eye frame rate.** Since 2026-08-31 the inference is on core 0, so
  what remains on core 1 is `delay(16)` plus this renderer: ~35 ms per iteration
  while tracking (28-29 Hz) against ~18 ms idle (56 Hz, i.e. the `delay(16)`
  ceiling). The difference is the iris moving on every gaze target, which now
  arrives every 181 ms instead of every 382. Anything that wants a higher
  tracking frame rate is work in *this* spec — no longer in SPEC-009.
* DMA transfer for the remaining full-frame path is still unwritten. With the
  partial flush in place the payoff is much smaller, since full frames are now
  rare. It is, however, the most obvious remaining lever on the 35 ms above.
* Raising `LCD_SPI_FREQ` above 16 MHz is **not** a speed lever — see Falsified.
  It remains untested above 40 MHz for signal integrity, but there is no reason
  to raise it.
