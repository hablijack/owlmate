#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Holt Kamerabilder der Eule als JPEG auf den Rechner (env: camsnap).

Beantwortet die Frage, die eine Helligkeitsmessung NICHT beantwortet: zeigt die
Kamera ueberhaupt dorthin, wo ein Gesicht steht, und steht das Bild richtig
herum? Nach jedem Oeffnen des Kopfes ist beides offen, und die
Gesichtserkennung findet bei verdrehtem Bild grundsaetzlich nichts.

Die Firmware liefert jeden Durchlauf vier Bilder - alle Kombinationen von
vflip/hmirror. Nur eine davon zeigt aufrechte Gesichter, und genau die gehoert
als CAM_VFLIP/CAM_HMIRROR in include/config.h.

Vorher:  pio run -e camsnap -t upload
Start:   ~/.platformio/penv/bin/python tools/schnappschuss.py [Zielordner]
"""
import base64
import glob
import os
import sys
import time

import serial


def find_port():
    """Serieller Port der ESP32 - wie in den uebrigen Werkzeugen."""
    env = os.environ.get("OWL_PORT")
    if env:
        return env
    for pattern in ("/dev/cu.usbmodem*", "/dev/ttyACM*", "/dev/ttyUSB*"):
        found = sorted(glob.glob(pattern))
        if found:
            return found[0]
    return None


BAUD = 115200


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(outdir, exist_ok=True)

    port = find_port()
    if not port:
        print("Kein serieller Port gefunden. OWL_PORT setzen oder Kabel pruefen.")
        return 1
    print("Port: %s" % port)

    ser = serial.Serial(port, BAUD, timeout=1)
    print("Warte auf einen vollstaendigen Durchlauf ...")

    header = None
    chunks = []
    written = []
    # Erst ab dem NAECHSTEN Sweep-Anfang mitschreiben. Wer mittendrin
    # einsteigt, bekommt sonst nur die restlichen Bilder des laufenden
    # Durchlaufs und vergleicht eine unvollstaendige Reihe.
    im_sweep = False
    deadline = time.time() + 90

    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", "replace").strip()

        if line == "---SWEEP---":
            im_sweep = True
            continue

        if not im_sweep:
            continue

        if line.startswith("---SNAP "):
            # z.B. "---SNAP vflip=1 hmirror=0 len=12345---"
            teile = line.replace("---", "").split()
            werte = dict(p.split("=") for p in teile[1:] if "=" in p)
            header = werte
            chunks = []
            continue

        if line == "---END---" and header is not None:
            data = base64.b64decode("".join(chunks))
            name = "snap_vflip%s_hmirror%s.jpg" % (header.get("vflip", "?"),
                                                   header.get("hmirror", "?"))
            pfad = os.path.join(outdir, name)
            with open(pfad, "wb") as f:
                f.write(data)
            erwartet = int(header.get("len", 0))
            ok = "" if len(data) == erwartet else "  ACHTUNG: erwartet %d" % erwartet
            print("  %-32s %6d Byte%s" % (name, len(data), ok))
            written.append(pfad)
            header = None
            chunks = []
            continue

        if line == "---SWEEPEND---" and written:
            break

        if header is not None and line:
            chunks.append(line)

    ser.close()

    if not written:
        print("Keine Bilder empfangen. Laeuft wirklich das env 'camsnap'?")
        return 1
    print("\n%d Bilder in %s" % (len(written), os.path.abspath(outdir)))
    print("Das Bild mit AUFRECHTEN Gesichtern bestimmt CAM_VFLIP/CAM_HMIRROR.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
