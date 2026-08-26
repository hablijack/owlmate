#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Live-Assistent fuer die BNO055-Kalibrierung der Roboter-Eule.

Einmal ausfuehren, danach liegen die Offsets im NVS-Flash der ESP32 und werden
bei jedem Boot wiederhergestellt ("IMU: restored calibration offsets from
flash"). Nur noetig, wenn der Sensor getauscht/neu montiert wurde oder das
NVS geloescht wurde.

Liest die Telemetrie der ESP32 mit und fuehrt Schritt fuer Schritt durch die
Kalibrierung. Die Phasen wechseln automatisch, sobald der jeweilige Zaehler
auf 3 steht -- es gibt keine festen Zeiten.

Start:  ~/.platformio/penv/bin/python kalibrieren.py
Abbruch: Strg-C
"""
import json
import sys
import time

import serial

def find_port():
    """Serieller Port der ESP32 - plattformunabhaengig.

    Reihenfolge: Umgebungsvariable OWL_PORT, dann automatische Suche. Deckt
    macOS (/dev/cu.usbmodem*) und Linux/Raspberry Pi (/dev/ttyACM*, ttyUSB*)
    ab, damit dasselbe Skript auf beiden Hosts laeuft.
    """
    import glob
    import os
    env = os.environ.get("OWL_PORT")
    if env:
        return env
    for pattern in ("/dev/cu.usbmodem*", "/dev/ttyACM*", "/dev/ttyUSB*"):
        found = sorted(glob.glob(pattern))
        if found:
            return found[0]
    return None


BAUD = 115200

B = "\033[1m"; R = "\033[0m"; G = "\033[32m"; Y = "\033[33m"; C = "\033[36m"

# REIHENFOLGE IST WICHTIG: die Bewegungsphase (mag) kommt VOR der Ruhephase
# (accel). Der accel-Zaehler faellt bei anhaltender Bewegung auf 0 zurueck --
# macht man die Acht zuletzt, zerstoert sie die gerade erreichte
# accel-Kalibrierung wieder. Umgekehrt bleibt mag stabil, wenn die Eule
# anschliessend still gehalten wird. (Genau in diese Falle sind wir am
# 2026-08-26 gelaufen: accel stand nach der Acht wieder auf 0/3.)
PHASEN = [
    ("gyro", "GYROSKOP",
     ["Eule ganz RUHIG stehen lassen. Nicht anfassen.",
      "Dauert nur ein paar Sekunden."]),
    ("mag", "MAGNETOMETER  (Bewegung)",
     ["Eule in der Luft in einer langsamen",
      "liegenden ACHT bewegen und dabei um alle",
      "Achsen mitdrehen. Das ist der laengste Teil.",
      "Abstand halten von Laptop / Lautsprechern /",
      "Metall - die verzerren das Magnetfeld."]),
    ("accel", "BESCHLEUNIGUNGSSENSOR  (Ruhe)",
     ["Jetzt NICHT mehr schwenken. Eule in ca. 6",
      "LAGEN bringen und jede ~5 s voellig still",
      "halten - am besten auf dem Tisch ABSETZEN:",
      "  aufrecht / auf den Kopf / linke Seite /",
      "  rechte Seite / Schnabel hoch / Schnabel runter",
      "mag bleibt dabei stabil auf 3."]),
    ("sys", "SYSTEM",
     ["Fast fertig. Eule ruhig halten, bis der",
      "letzte Zaehler einrastet.",
      "sys schwankt normalerweise - das ist kein",
      "Fehler, es ist ein Live-Konfidenzwert."]),
]


def balken(werte):
    teile = []
    for name in ("sys", "gyro", "accel", "mag"):
        v = werte.get(name, 0)
        farbe = G if v >= 3 else (Y if v > 0 else "")
        teile.append("%s%s[%s%s] %d/3%s" % (farbe, name.ljust(6),
                                            "#" * v, "." * (3 - v), v, R))
    return "   ".join(teile)


def kopf(nr, titel, zeilen):
    print()
    print("%s%s== Phase %d/%d: %s ==%s" % (C, B, nr, len(PHASEN), titel, R))
    for z in zeilen:
        print("   " + z)
    print()


def main():
    print("%s%sBNO055 Kalibrierung - Roboter-Eule%s" % (B, C, R))
    PORT = find_port()
    if not PORT:
        print("FEHLER: kein serieller Port gefunden.")
        print("Board angeschlossen? Sonst OWL_PORT=/dev/... setzen.")
        return 1
    print("Verbinde mit %s ..." % PORT)
    try:
        s = serial.Serial(PORT, BAUD, timeout=1)
    except Exception as e:
        print("FEHLER: Port nicht erreichbar (%s)" % e)
        print("Laeuft noch ein anderer Monitor auf dem Port?")
        return 1
    s.reset_input_buffer()
    print("Verbunden. Warte auf Telemetrie ...")

    werte = {"sys": 0, "gyro": 0, "accel": 0, "mag": 0}
    phase = 0
    gespeichert = False
    kopf(1, PHASEN[0][1], PHASEN[0][2])
    begonnen = time.time()
    letzte_ausgabe = 0.0

    try:
        while True:
            zeile = s.readline()
            if not zeile:
                continue
            text = zeile.decode("utf-8", "replace").strip()

            if "IMU:" in text:
                print("\n%s%s>> %s%s" % (B, G, text, R))
                if "saved" in text:
                    gespeichert = True

            if not text.startswith('{"type":"telemetry"'):
                continue
            try:
                cal = json.loads(text)["imu"]["cal"]
            except Exception:
                continue

            for k in werte:
                werte[k] = cal.get(k, 0)

            jetzt = time.time()
            if jetzt - letzte_ausgabe > 0.4:
                sys.stdout.write("\r   %s   (%3ds)" % (balken(werte),
                                                       int(jetzt - begonnen)))
                sys.stdout.flush()
                letzte_ausgabe = jetzt

            # Phase abgeschlossen? Naechste Anleitung zeigen.
            while phase < len(PHASEN) and werte[PHASEN[phase][0]] >= 3:
                print("\n%s%s   ✓ %s fertig (%d/3)%s"
                      % (B, G, PHASEN[phase][1], werte[PHASEN[phase][0]], R))
                phase += 1
                if phase < len(PHASEN):
                    kopf(phase + 1, PHASEN[phase][1], PHASEN[phase][2])

            if all(v >= 3 for v in werte.values()):
                print("\n")
                print("%s%s=========================================%s" % (B, G, R))
                print("%s%s  KALIBRIERUNG KOMPLETT - alles auf 3/3  %s" % (B, G, R))
                print("%s%s=========================================%s" % (B, G, R))
                if gespeichert:
                    print("Offsets sind im Flash gespeichert.")
                else:
                    print("Warte kurz auf die Speicher-Meldung ...")
                    ende = time.time() + 5
                    while time.time() < ende and not gespeichert:
                        l2 = s.readline()
                        if l2 and "saved" in l2.decode("utf-8", "replace"):
                            gespeichert = True
                    print("Gespeichert." if gespeichert else
                          "Keine Speicher-Meldung gesehen (evtl. schon vorher gespeichert).")
                print()
                print("Ab jetzt startet die Eule bei jedem Boot kalibriert.")
                print("Der Kompass (yaw) ist jetzt nutzbar.")
                break
    except KeyboardInterrupt:
        print("\nAbgebrochen. Stand: %s" % balken(werte))
    finally:
        s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
