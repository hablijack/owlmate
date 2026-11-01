#pragma once
// ============================================================================
// Gemeinsamer I2C-Bus: Schleife (IMU, GPS) und Servo-Task teilen sich Wire.
//
// Nur im Bewegungs-Image (SERVO_MOTION_ENABLED=1) gibt es den Servo-Task, der den
// PCA9685 mit festen 50 Hz bedient - unabhaengig von den 80-110-ms-Aussetzern der
// Hauptschleife (GPS-Lesen, Augen-Flush, Telemetrie), die als Ruckeln sichtbar waren.
// Wire ist nicht threadsicher, deshalb umschliesst jede Busoperation einen Mutex.
// Im gesperrten Image ist I2cGuard leer: dort laeuft nichts parallel zur Schleife.
//
// Regel: I2cGuard NUR um einzelne Busoperationen halten, nie um lange Schleifen -
// der Servo-Task wartet sonst. Nie ein anderes Lock halten, waehrend I2cGuard
// genommen wird, ausser der Servo-Task (Reihenfolge: Servo-Zustand -> I2C-Bus).
// ============================================================================
#include "config.h"

#if SERVO_MOTION_ENABLED
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

SemaphoreHandle_t i2cBusMutex();

struct I2cGuard {
    I2cGuard() { xSemaphoreTake(i2cBusMutex(), portMAX_DELAY); }
    ~I2cGuard() { xSemaphoreGive(i2cBusMutex()); }
    I2cGuard(const I2cGuard&) = delete;
    I2cGuard& operator=(const I2cGuard&) = delete;
};
#else
struct I2cGuard { I2cGuard() {} ~I2cGuard() {} };
#endif
