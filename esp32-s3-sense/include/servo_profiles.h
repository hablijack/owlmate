#pragma once
// ============================================================================
// Servo-Profile des Bewegungs-Images (SERVO_MOTION_ENABLED=1) - SPEC-016.
//
// Quelle: die am 2026-10-30/31 mit dem Besitzer am Mechanismus gemessenen Werte
// (BACKLOG.md, "RECORD"-Abschnitte). Der Besitzer hat jeweils am Anschlag auf
// Brummen/Kaempfen gehoert; die Tabellenenden liegen mit Reserve DAVOR:
//
//   Kanal  Servo         gemessen (leise)      Anschlag/Verbot            Tabelle
//   8      rechter Fluegel  1147 .. 1996        offen = Kurbel-Wendepunkt   1160 .. 1990
//   9      linker Fluegel   1000 .. 1747        >= ~1776 pendelt heftig     1010 .. 1730
//   12     linkes Ohr       1972 .. 2323        <1950 drueckt unten         1985 .. 2310
//   13     rechtes Ohr       532 ..  800        522 bzw. ~840 brummt         545 ..  800
//   10     Kopf              498 .. 2201        2298 kaempft                 520 .. 2150
//
// Einheiten (pos): Ohren und Fluegel 0..100 (Ohr 0 = unten, 100 = oben; Fluegel
// 0 = zu, 100 = offen). Kopf in NOMINALEN Grad, positiv = nach LINKS (von der
// Eule aus), 0 = geradeaus (1498 us). Der Kopf sitzt direkt auf der Servoachse und
// ist damit LINEAR; die Gradzahl nimmt 500..2500 us = 180 Grad an (11,1 us pro Grad)
// und ist NICHT gemessen. Die Schaetzungen des Besitzers beim Durchlauf waren nur
// grob und sind keine Kennlinie (Besitzer, 2026-10-31).
//
// Geschwindigkeiten sind absichtlich gemaessigt. Die Fluegel sind schneller (300 %/s), weil
// der volle Hub in einem Schlag sichtbar sein soll (Besitzer, 2026-10-31).
// ============================================================================
#include "ServoMotion.h"
#include "config.h"

static const ServoProfile SERVO_PROFILES[] = {
    // channel, n, {pos,us}..., maxSpeed, smoothTime [s], rest
    { CH_RIGHT_WING, 2, {{0, 1990}, {100, 1160}}, 300.0f, 0.10f, 0.0f },
    { CH_LEFT_WING,  2, {{0, 1010}, {100, 1730}}, 300.0f, 0.10f, 0.0f },
    { CH_LEFT_EAR,   2, {{0, 1985}, {100, 2310}}, 220.0f, 0.14f, 12.0f },
    { CH_RIGHT_EAR,  2, {{0,  800}, {100,  545}}, 220.0f, 0.14f, 12.0f },
    { CH_HEAD,       2, {{-88, 520}, {58.7f, 2150}}, 70.0f, 0.16f, 0.0f },
};
static const uint8_t NUM_SERVO_PROFILES = sizeof(SERVO_PROFILES) / sizeof(SERVO_PROFILES[0]);
