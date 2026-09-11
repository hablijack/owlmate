#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Live-Test der 4-Tipp-Sequenz fuer den OTA-Update-Modus.

Zeigt jeden gezaehlten Klopfer mit dem exakten Abstand zum vorherigen und
sagt sofort, ob er im gueltigen Fenster lag. Damit laesst sich beurteilen, ob
UPDATE_TAP_GAP_MS (aktuell 1500 ms) praxistauglich ist.

    ~/.platformio/penv/bin/python tools/klopftest.py     # Strg-C beendet
"""
import json
import sys
import time

import serial

def find_port():
    """Serieller Port der ESP32 - plattformunabhaengig.

    Reihenfolge: Umgebungsvariable OWL_PORT, dann automatische Suche. Deckt
    macOS (/dev/cu.usbmodem*) und Linux/Orange Pi (/dev/ttyACM*, ttyUSB*)
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


GAP_MAX = 1500      # UPDATE_TAP_GAP_MS
GAP_MIN = 760       # Nachprellen (~510 ms) + VIBRATION_BURST_GAP_MS (250 ms)
NEED = 4            # UPDATE_TAP_REQUIRED

B, G, Y, R, RS = "\033[1m", "\033[32m", "\033[33m", "\033[31m", "\033[0m"

print("%s%s4-Tipp-Test (OTA-Update-Modus)%s" % (B, R, RS))
PORT = find_port()
if not PORT:
    print("FEHLER: kein serieller Port gefunden.")
    print("Board angeschlossen? Sonst OWL_PORT=/dev/... setzen.")
    sys.exit(1)
try:
    s = serial.Serial(PORT, 115200, timeout=1)
except Exception as e:
    print("FEHLER: Port belegt oder nicht da (%s)" % e)
    sys.exit(1)

print("""
ANLEITUNG
  Klopfe 4x etwa im SEKUNDENTAKT. Nicht hektisch: ein Klopfer prellt
  ~0,5 s nach, zu schnell verschmelzen zwei Klopfer zu einem (der
  Zaehler springt dann gar nicht).

  gueltiger Abstand: %d - %d ms
  zu kurz  -> beide Klopfer zaehlen als einer
  zu lang  -> Sequenz beginnt von vorn

  Bei Erfolg geht die Eule in den SoftAP. Ein EINZELNER Klopfer
  holt sie wieder heraus (nach 1 s Schonfrist).
""" % (GAP_MIN, GAP_MAX))

taps, run, last_state, seq_shown = [], 0, None, False
try:
    while True:
        line = s.readline()
        if not line:
            continue
        d = line.decode("utf-8", "replace").strip()
        if not d.startswith('{"type":"telemetry"'):
            continue
        try:
            j = json.loads(d)
        except Exception:
            continue

        st = j.get("state")
        if st != last_state:
            col = G if st == "update" else ""
            print("\n  Zustand: %s%s%s" % (col, st, RS))
            if st == "update":
                u = j.get("update", {})
                print("  %s%s>>> UPDATE-MODUS ERREICHT <<<%s" % (B, G, RS))
                if u:
                    print("      SSID %s / %s" % (u.get("ssid"), u.get("password")))
                    print("      %s" % u.get("url"))
                print("      Ein einzelner Klopfer beendet den Modus.")
                run = 0
            last_state = st

        ld = j.get("vibration", {}).get("lastDetected", 0)
        if ld and (not taps or taps[-1] != ld):
            if taps:
                gap = ld - taps[-1]
                if gap > GAP_MAX:
                    run = 1
                    verd = "%sZU LANG%s  -> Sequenz beginnt von vorn" % (Y, RS)
                else:
                    run += 1
                    verd = "%sOK%s" % (G, RS)
                print("  Klopfer #%d   Abstand %5d ms   %s   (Folge: %d/%d)"
                      % (len(taps) + 1, gap, verd, run, NEED))
            else:
                run = 1
                print("  Klopfer #1   (Start der Folge)")
            taps.append(ld)
except KeyboardInterrupt:
    gaps = [taps[i] - taps[i - 1] for i in range(1, len(taps))]
    print("\n\nAUSWERTUNG")
    print("  Klopfer erfasst: %d" % len(taps))
    if gaps:
        good = sum(1 for g in gaps if GAP_MIN <= g <= GAP_MAX)
        print("  Abstaende (ms) : %s" % gaps)
        print("  im Fenster     : %d von %d" % (good, len(gaps)))
        print("  min %d / max %d ms" % (min(gaps), max(gaps)))
        if good < len(gaps):
            print("\n  -> Nicht alle Abstaende trafen das Fenster. Wenn sich das")
            print("     schlecht treffen laesst, UPDATE_TAP_GAP_MS erhoehen.")
    if last_state == "update":
        print("\n  Update-Modus wurde erreicht - Sequenz funktioniert.")
finally:
    s.close()
