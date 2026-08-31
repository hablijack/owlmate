#pragma once

// ============================================================================
// Wiring verification. Probes every peripheral the owl depends on and reports
// each one's status over serial (and on the eye displays), so a fresh harness
// can be verified in seconds without reading a log.
//
// Built ONLY when -DHARDWARE_CHECK=1: src/hardware_check.cpp compiles to
// nothing otherwise, which is the same "one diagnostic, one source file"
// pattern the ten diagnostic envs follow via build_src_filter. Until 2026-08-31
// this was ~150 lines inside main.cpp and therefore linked into every
// production image.
// ============================================================================

// config.h FIRST: this header switches on HARDWARE_CHECK, and config.h is where
// its default lives. It arrives transitively through owl.h today, but relying on
// that would make the whole file silently compile to nothing if that chain ever
// changed -- while main.cpp, which includes config.h directly, still called
// runHardwareCheck(). That is a link error at best and a confusing one.
#include "config.h"
#include "owl.h"

#if HARDWARE_CHECK

#include <Wire.h>

// Probe every peripheral, print one {"type":"hardware_check",...} line (its
// i2c_found array lists every responding address), then return. Replaces the
// normal boot; the caller then idles.
void runHardwareCheck();

// The scan that runs FIRST at boot, before PSRAM/LCD/camera, so it needs
// minimal current and still reports on a marginal supply. Prints the answering
// addresses to serial.
void i2cScanToSerial();

// Walk the I2C bus and invoke `onFound` for every address that ACKs.
//
// Two scans with genuinely different bodies used to sit in main.cpp -- one
// building the i2c_found JSON array and setting device flags, the other
// printing to serial from setup(). They are the same WALK with different
// reporting, so this takes the reporting as a callback. Do NOT "fix" it by
// making one caller print what the other needs.
//
// A template rather than a function pointer so callers can pass a capturing
// lambda and keep their own tallies local.
template <typename OnFound>
void i2cScan(OnFound onFound) {
    for (uint8_t addr = 1; addr < 127; addr++) {
        Wire.beginTransmission(addr);
        if (Wire.endTransmission() == 0) {
            onFound(addr);
        }
    }
}

#endif  // HARDWARE_CHECK
