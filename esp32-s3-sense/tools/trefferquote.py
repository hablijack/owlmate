#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Misst, WIE OFT die Eule ein Gesicht wirklich findet - nicht ob.

Beantwortet die Frage, die "die Erkennung funktioniert" offenlaesst: die Augen
bekommen nur bei einem TREFFER ein neues Blickziel. Bei 6 Versuchen je Sekunde
und 31 % Trefferquote sind das keine 6, sondern knapp 2 neue Ziele je Sekunde -
und genau das sieht man als nachlaufenden Blick. Am 2026-08-31 wurde deshalb
dreimal am Glaettungsfilter gedreht, obwohl der nie die Ursache war.

Meldet ausserdem loop_hz als FOLGE, nicht nur als Mittelwert. Das ist kein
Schmuck: eine blockierende Inferenz sieht im Mittelwert harmlos aus (~30 Hz),
waehrend die Einzelwerte 42 33 41 43 28 42 33 10 40 lauten - die Augen laufen
300 ms fluessig und stehen dann 170 ms still. Ein Mittelwert kann ein Stocken
nicht zeigen, und genau dieses Stocken war monatelang das ungeklaerte "mal
echtzeitnah, mal haengt es".

Waehrend der Messung normal vor der Eule sitzen und den Kopf ein wenig bewegen.

Start:  ~/.platformio/penv/bin/python esp32-s3-sense/tools/trefferquote.py [Sekunden]
Live:   ~/.platformio/penv/bin/python esp32-s3-sense/tools/trefferquote.py --live

Aus einer MITSCHRIFT statt vom Port - fuer Messungen an einer LAUFENDEN Eule:

    timeout 30 cat /dev/cu.usbmodem* > lauf.ndjson
    python3 esp32-s3-sense/tools/trefferquote.py --datei lauf.ndjson

Der Unterschied ist wichtig. pyserial zieht beim Oeffnen DTR/RTS und startet das
Board NEU, jede Messung beginnt also bei Betriebssekunde 0 und mit kaltem Cache;
`cat` tut das nicht. Fuer Trefferquoten ist der Neustart egal, fuer "warum
stockt es nach neun Minuten Laufzeit" nicht. Der Dateimodus braucht kein
pyserial und laeuft mit jedem python3.
"""
import json
import sys
import time

# pyserial wird ERST im Portmodus importiert: der Dateimodus soll auf jedem
# python3 laufen, auch ohne die PlatformIO-Umgebung.


def _open(port):
    import serial
    return serial.Serial(port, 115200, timeout=0.3)


def _find_port():
    sys.path.insert(0, __import__("os").path.dirname(__file__))
    from schnappschuss import find_port
    return find_port()


def live(port):
    """Laufende Anzeige: zeigt bei JEDER Telemetrie, ob gerade ein Gesicht
    gefunden wird und wie gross es im Bild steht.

    Dafuer gedacht, sich beim Zuschauen zu bewegen: naeher herangehen, weiter
    weg, zur Seite. Die Augen koennen nur so schnell folgen, wie hier Treffer
    auftauchen - steht rechts lange "---", liegt es nicht an den Augen.

    Abbruch mit Strg-C.
    """
    s = _open(port)
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


# Unterhalb dieser Bildrate liest ein Blick nicht mehr als Bewegung, sondern
# als Standbild. Auf Hardware am 2026-08-31 gegeneinander gehalten: 5,8 Hz
# waehrend der Verfolgung wurde als "tot" beurteilt, 29 Hz als "lebendig".
STALL_HZ = 15


def _collect(lines, skip=0):
    """Aus NDJSON-Zeilen die Kennzahlen ziehen. Gemeinsam fuer Port und Datei.

    `skip` verwirft die ersten Telemetrieframes. Im Dateimodus 2, und das ist
    keine Kosmetik: das Anhaengen von `cat` an einen Port, den lange niemand
    gelesen hat, laesst den ersten loop_hz-Wert einbrechen (auf Hardware
    gemessen 2026-08-31: "4 22 40 56 50 ..." bei sofort danach wiederholter
    Messung "47 47 47 53 ..."). Vermutlich raeumt der volle USB-CDC-Puffer sich
    erst beim Leser ab. Ohne dieses Verwerfen meldet JEDE Mitschrift ein
    Einbrechen, das nur die Messung selbst war - und das ist genau die Sorte
    Artefakt, an die dieses Projekt schon Tage verloren hat.
    """
    first = last = None
    widths, states, hz, timing = [], set(), [], []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") != "telemetry":
            continue
        if skip > 0:
            skip -= 1
            continue
        f = o["face"]
        rec = (f["attempts"], f["total"], o["uptime"])
        if first is None:
            first = rec
        last = rec
        states.add(o["state"])
        if o.get("loop_hz") is not None:
            hz.append(float(o["loop_hz"]))
        # capture_ms/infer_ms gibt es erst seit dem Umzug der Inferenz auf
        # Kern 0; aeltere Mitschriften haben sie nicht.
        if f.get("infer_ms"):
            timing.append((int(f.get("capture_ms", 0)), int(f["infer_ms"]),
                           int(f.get("stack_free", 0))))
        if f["detected"]:
            widths.append(f["w"])
    return first, last, widths, states, hz, timing


def _report(first, last, widths, states, hz, timing):
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

    if hz:
        print("")
        print("  BILDRATE loop_hz   : %.1f / %.1f / %.1f  (min / Mittel / max)"
              % (min(hz), sum(hz) / len(hz), max(hz)))
        print("  Folge              : %s" % " ".join("%.0f" % v for v in hz))
        # Die Zahl, um die es bei der Inferenz auf Kern 0 ueberhaupt geht: ein
        # Mittelwert von 30 Hz kann ein regelmaessiges Einfrieren verstecken.
        #
        # Feste Schwelle statt "halber Median", und zwar mit Absicht: relativ
        # gerechnet schlaegt sie im Leerlauf bei jedem harmlosen Einbruch von
        # 62 auf 29 Hz an, und ein Warnhinweis, der immer angeht, wird nicht
        # mehr gelesen. 15 Hz ist die Grenze, ab der ein Blick nicht mehr als
        # Bewegung durchgeht, sondern als Ruckeln.
        stalls = [v for v in hz if v < STALL_HZ]
        print("  Sichtbare Standbilder: %d von %d Abtastungen unter %d Hz"
              % (len(stalls), len(hz), STALL_HZ))
        if stalls:
            print("                       -> es STOCKT; tiefster Wert %.1f Hz" % min(stalls))
        else:
            print("                       -> keine, die Augen laufen durchgehend")

    if timing:
        cap = [t[0] for t in timing]
        inf = [t[1] for t in timing]
        stack = [t[2] for t in timing if t[2]]
        print("")
        print("  Kamera warten      : %d-%d ms (Mittel %.0f)"
              % (min(cap), max(cap), sum(cap) / float(len(cap))))
        print("  Rechnen (Inferenz) : %d-%d ms (Mittel %.0f)"
              % (min(inf), max(inf), sum(inf) / float(len(inf))))
        print("                       -> ein Durchlauf ~%.0f ms, davon %.0f %% Rechnen"
              % (sum(cap) / float(len(cap)) + sum(inf) / float(len(inf)),
                 100.0 * sum(inf) / float(sum(cap) + sum(inf))))
        if stack:
            print("  Stapel frei (Kern 0): %d Byte Tiefstand von %d"
                  % (min(stack), 8192))

    if widths:
        mean = sum(widths) / float(len(widths))
        print("")
        print("  Gesichtsbreite     : %d-%d px (Mittel %.0f) von 160 = %.0f %% des Ausschnitts"
              % (min(widths), max(widths), mean, mean / 160.0 * 100.0))
        print("")
        print("  Groesse IM BILD ist laut SPEC-008/009 der staerkste Hebel auf die")
        print("  Trefferquote. Unter ~20 %% wird es sporadisch; naeher herangehen oder")
        print("  CAM_DETECT_CROP_DIV erhoehen (kostet Sichtfeld).")
    return 0


def from_file(path):
    """Mitschrift auswerten. Kein Port, kein Neustart, kein pyserial."""
    with open(path) as fh:
        return _report(*_collect(fh, skip=2))


def main():
    args = sys.argv[1:]
    if args and args[0] in ("--live", "-l", "live"):
        return live(_find_port())
    if args and args[0] in ("--datei", "-d", "--file"):
        if len(args) < 2:
            print("Nutzung: trefferquote.py --datei <mitschrift.ndjson>")
            return 1
        return from_file(args[1])
    secs = float(args[0]) if args else 20.0
    port = _find_port()
    print("Port: %s" % port)
    print("Jetzt normal vor die Eule setzen. Messung laeuft %.0f s ..." % secs)
    s = _open(port)
    time.sleep(3.0)               # Board bootet beim Oeffnen des Ports neu
    s.reset_input_buffer()

    def stream():
        t0 = time.time()
        while time.time() - t0 < secs:
            yield s.readline().decode("utf-8", "replace")

    rc = _report(*_collect(stream()))
    s.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
