#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Misst, WIE OFT die Eule ein Gesicht wirklich findet - nicht ob.

Beantwortet die Frage, die "die Erkennung funktioniert" offenlaesst: die Augen
bekommen nur bei einem TREFFER ein neues Blickziel. Bei 6 Versuchen je Sekunde
und 31 % Trefferquote sind das keine 6, sondern knapp 2 neue Ziele je Sekunde -
und genau das sieht man als nachlaufenden Blick. Am 2026-08-31 wurde deshalb
dreimal am Glaettungsfilter gedreht, obwohl der nie die Ursache war.

Waehrend der Messung normal vor der Eule sitzen und den Kopf ein wenig bewegen.

Start:  ~/.platformio/penv/bin/python esp32-s3-sense/tools/trefferquote.py [Sekunden]
Live:   ~/.platformio/penv/bin/python esp32-s3-sense/tools/trefferquote.py --live
"""
import json
import sys
import time

import serial

sys.path.insert(0, __import__("os").path.dirname(__file__))
from schnappschuss import find_port  # noqa: E402


def live(port):
    """Laufende Anzeige: zeigt bei JEDER Telemetrie, ob gerade ein Gesicht
    gefunden wird und wie gross es im Bild steht.

    Dafuer gedacht, sich beim Zuschauen zu bewegen: naeher herangehen, weiter
    weg, zur Seite. Die Augen koennen nur so schnell folgen, wie hier Treffer
    auftauchen - steht rechts lange "---", liegt es nicht an den Augen.

    Abbruch mit Strg-C.
    """
    s = serial.Serial(port, 115200, timeout=0.3)
    time.sleep(3.0)
    s.reset_input_buffer()
    print("  Treffer | Breite | Blick x/y      | Zustand")
    print("  --------+--------+----------------+---------")
    try:
        while True:
            line = s.readline().decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") != "telemetry":
                continue
            f = o["face"]
            if f["detected"]:
                bar = "#" * min(20, max(1, f["w"] // 4))
                print("  JA      | %3d px | %+5.2f / %+5.2f  | %-11s %s"
                      % (f["w"], f["gaze_x"], f["gaze_y"], o["state"], bar))
            else:
                print("  ---     |        |                | %-11s" % o["state"])
    except KeyboardInterrupt:
        pass
    finally:
        s.close()
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--live", "-l", "live"):
        return live(find_port())
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    port = find_port()
    print("Port: %s" % port)
    print("Jetzt normal vor die Eule setzen. Messung laeuft %.0f s ..." % secs)
    s = serial.Serial(port, 115200, timeout=0.3)
    time.sleep(3.0)               # Board bootet beim Oeffnen des Ports neu
    s.reset_input_buffer()

    first = last = None
    widths, states = [], set()
    t0 = time.time()
    while time.time() - t0 < secs:
        line = s.readline().decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") != "telemetry":
            continue
        f = o["face"]
        rec = (f["attempts"], f["total"], o["uptime"])
        if first is None:
            first = rec
        last = rec
        states.add(o["state"])
        if f["detected"]:
            widths.append(f["w"])
    s.close()

    if first is None or last is None:
        print("Keine Telemetrie empfangen - laeuft die Hauptfirmware?")
        return 1

    da, dh, du = last[0] - first[0], last[1] - first[1], last[2] - first[2]
    if du <= 0 or da <= 0:
        print("Zu kurz gemessen.")
        return 1

    print("")
    print("  Zustaende          : %s" % ", ".join(sorted(states)))
    print("  Versuche           : %d  (%.1f Hz)" % (da, da * 1000.0 / du))
    print("  TREFFER            : %d  (%.1f Hz)" % (dh, dh * 1000.0 / du))
    print("  Trefferquote       : %.0f %%" % (100.0 * dh / da))
    if dh:
        print("  Neues Blickziel    : alle %.0f ms   <- so schnell koennen die Augen folgen"
              % (float(du) / dh))
    else:
        print("  Neues Blickziel    : NIE - kein einziger Treffer")
    if widths:
        mean = sum(widths) / float(len(widths))
        print("  Gesichtsbreite     : %d-%d px (Mittel %.0f) von 160 = %.0f %% des Ausschnitts"
              % (min(widths), max(widths), mean, mean / 160.0 * 100.0))
        print("")
        print("  Groesse IM BILD ist laut SPEC-008/009 der staerkste Hebel auf die")
        print("  Trefferquote. Unter ~20 %% wird es sporadisch; naeher herangehen oder")
        print("  CAM_DETECT_CROP_DIV erhoehen (kostet Sichtfeld).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
