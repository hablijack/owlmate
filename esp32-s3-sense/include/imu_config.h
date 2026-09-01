#pragma once

#include "OwlImu.h"
#include "config.h"

// Fuellt OwlImuConfig aus config.h - an EINER Stelle, fuer alle Aufrufer.
//
// Warum es diese Datei gibt: lib/OwlImu ist wie die anderen Bibliotheken unter
// lib/ in sich geschlossen und sieht include/config.h nicht. Ohne diesen
// Trichter muessten Firmware (src/Sensors.cpp) und Diagnose (src/imuaxis.cpp)
// die Zuordnung jede fuer sich hinschreiben - und damit gaebe es die Werte
// zweimal. "Wenn eine Tatsache an zwei Stellen steht, ist eine davon ein
// Fehler" (AGENTS.md); das ist genau so ein Fall.
static inline OwlImuConfig owlImuConfigFromHeader() {
    OwlImuConfig c;
    c.accelAddr = ADDR_LSM303_ACCEL;
    c.magAddr = ADDR_LSM303_MAG;
    c.remapX = IMU_REMAP_X;
    c.remapY = IMU_REMAP_Y;
    c.remapZ = IMU_REMAP_Z;
    c.headingOffsetDeg = IMU_HEADING_OFFSET_DEG;
    c.sampleIntervalMs = IMU_SAMPLE_INTERVAL_MS;
    c.filterAlpha = IMU_FILTER_ALPHA;
    c.magMinSpanUt = MAG_CAL_MIN_SPAN_UT;
    c.maxReadFails = IMU_MAX_READ_FAILS;
    c.failBackoffMs = IMU_FAIL_BACKOFF_MS;
    return c;
}
