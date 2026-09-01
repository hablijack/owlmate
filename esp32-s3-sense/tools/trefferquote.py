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

Meldet seit 2026-08-31 ausserdem die laengste EINZELNE Runde je Fenster und den
Haldenstand - fuer genau die Frage "warum stockt es nach zwoelf Minuten". Ein
Leck laesst `frei` fallen, eine Fragmentierung laesst `frei` stehen und den
groessten Block schrumpfen, und eine blockierte Runde steht als Millisekundenzahl
da, deren GROESSE den Verursacher benennt.

Waehrend der Messung normal vor der Eule sitzen und den Kopf ein wenig bewegen.

Start:  ~/.platformio/penv/bin/python esp32-s3-sense/tools/trefferquote.py [Sekunden]
Live:   ~/.platformio/penv/bin/python esp32-s3-sense/tools/trefferquote.py --live

Aus einer MITSCHRIFT statt vom Port - fuer Messungen an einer LAUFENDEN Eule:

    timeout 30 cat /dev/cu.usbmodem* > lauf.ndjson
    python3 esp32-s3-sense/tools/trefferquote.py --datei lauf.ndjson

Mitschreiben UND zusehen, ebenfalls ohne Neustart:

    cat /dev/cu.usbmodem* | tee lauf.ndjson \\
        | python3 esp32-s3-sense/tools/trefferquote.py --folge

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


def folge(stream):
    """Kompakte Laufansicht aus einer PIPE - ohne pyserial, ohne Neustart.

    Der Unterschied zu --live ist der ganze Zweck: --live oeffnet den Port mit
    pyserial und startet das Board damit NEU. Fuer "es stockt nach zwoelf
    Minuten" ist das genau die Messung, die die Sache kaputtmacht. Hier kommt
    die Zeile aus einer Pipe, also von `cat`, und `cat` zieht kein DTR/RTS.

    Gedacht fuer:
        cat /dev/cu.usbmodem* | tee lauf.ndjson | trefferquote.py --folge

    Damit laeuft die Mitschrift fuer die spaetere Auswertung mit, waehrend man
    zusieht - und man kann die Uhrzeit notieren, ab der es ruckelt.
    """
    print("   Laufzeit | loop_hz | laengste | Halde frei / groesster Block | Treffer")
    print("   ---------+---------+----------+------------------------------+--------")
    n = 0
    for line in stream:
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") != "telemetry":
            continue
        n += 1
        # Die ersten beiden Frames verwerfen: das Anhaengen an einen Port, den
        # lange niemand gelesen hat, laesst den ersten loop_hz-Wert einbrechen.
        # Siehe _collect(). Ohne das meldet die Ansicht sofort ein Stocken, das
        # nur das Anhaengen selbst war.
        if n <= 2:
            continue
        h = o.get("heap") or {}
        mx = o.get("loop_max_ms")
        hz = float(o.get("loop_hz") or 0.0)
        # Markieren statt nur drucken: eine blockierte Runde und ein Einbruch
        # der Bildrate sind das, wonach diese Ansicht sucht.
        warn = ""
        if mx is not None and int(mx) >= 200:
            warn += "  <<< BLOCKIERTE RUNDE"
        if hz and hz < STALL_HZ:
            warn += "  <<< STOCKT"
        print("   %6.1f s | %5.1f   | %6s   | %8s / %-8s          | %s%s"
              % (int(o.get("uptime", 0)) / 1000.0, hz,
                 "-" if mx is None else "%d ms" % int(mx),
                 h.get("free", "-"), h.get("largest", "-"),
                 "JA" if (o.get("face") or {}).get("detected") else "--",
                 warn))
        sys.stdout.flush()
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
    Messung "47 47 47 53 ...").

    "Vermutlich raeumt der volle USB-CDC-Puffer sich erst beim Leser ab" stand
    hier, als Vermutung, bis am selben Tag jemand nachgemessen hat - und die
    Vermutung zeigte auf einen echten Fehler in der Firmware. Die Schleife blieb
    wirklich stehen, loop_max_ms 2256 und 4023 ms, weil Serial.println() einer
    ~900-Byte-Zeile auf einem 256-Byte-Ring wartete, solange niemand las.
    Behoben (SPEC-010), das Artefakt ist seitdem klein. Die Lehre ist groesser
    als der Fix: ein Artefakt, das man ERKLAERT, aber nicht GEMESSEN hat, ist
    eine ungelesene Fehlermeldung.
    """
    first = last = None
    widths, states, hz, timing = [], set(), [], []
    # Seit 2026-08-31: die laengste EINZELNE Runde je Fenster, und der
    # Haldenstand. Beides fehlt in aelteren Mitschriften, deshalb ueberall
    # .get() mit Vorbehalt.
    maxms, heap = [], []
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
        if o.get("loop_max_ms") is not None:
            maxms.append(int(o["loop_max_ms"]))
        h = o.get("heap")
        if isinstance(h, dict) and h.get("free") is not None:
            heap.append((int(o["uptime"]), int(h.get("free", 0)),
                         int(h.get("min", 0)), int(h.get("largest", 0)),
                         int(h.get("psram_free", 0)),
                         int(h.get("psram_largest", 0))))
        if f["detected"]:
            widths.append(f["w"])
    return first, last, widths, states, hz, timing, maxms, heap


def _folge(werte, fmt="%s"):
    """Die Folge als Zeile - bei langen Mitschriften gekuerzt.

    Die Folge ist der Punkt (ein Mittelwert kann ein Stocken nicht zeigen), aber
    bei 1727 Abtastungen einer 15-Minuten-Mitschrift sind das ueber 5000 Zeichen
    auf einer Zeile, und dann liest sie niemand mehr. Anfang und Ende zeigen,
    ob sich etwas ueber die Laufzeit VERAENDERT hat - genau die Frage, fuer die
    lange Mitschriften gemacht werden. Fuer den Blick auf jede einzelne
    Abtastung gibt es --folge.
    """
    n = 40
    if len(werte) <= 2 * n:
        return " ".join(fmt % v for v in werte)
    kopf = " ".join(fmt % v for v in werte[:n])
    fuss = " ".join(fmt % v for v in werte[-n:])
    return "%s  ...(%d weitere)...  %s" % (kopf, len(werte) - 2 * n, fuss)


def _report(first, last, widths, states, hz, timing, maxms=(), heap=()):
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
        print("  Folge              : %s" % _folge(hz, "%.0f"))
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

    if maxms:
        # Die Zahl, die ein Stocken BEZIFFERT statt es nur anzudeuten. Der
        # Mittelwert daneben kann es prinzipiell nicht zeigen; die Groesse hier
        # benennt zusaetzlich den Verursacher. Die Schwellen sind Zuordnungen
        # aus dem Quelltext, keine Messungen: I2C_TIMEOUT_MS ist 250, und
        # HWCDC::write() gibt nach 20 Versuchen a 100 ms auf.
        print("")
        print("  Laengste Runde     : %d ms  (normal ~35 bei 29 Hz)" % max(maxms))
        print("  Folge              : %s" % _folge(maxms))
        m = max(maxms)
        if m >= 1500:
            print("                       -> passt auf einen USB-CDC-Port, den niemand")
            print("                          mehr leerliest (20 x 100 ms tx-Timeout)")
        elif m >= 200:
            print("                       -> passt auf einen I2C-Timeout (250 ms):")
            print("                          ein Slave am Bus antwortet nicht mehr")
        elif m >= 100:
            print("                       -> blockiert, aber unter 200 ms: eher Rendern")
            print("                          oder ein einzelner langsamer Bustransfer")
        else:
            print("                       -> keine blockierte Runde in dieser Mitschrift")

    if len(heap) >= 2:
        a, b = heap[0], heap[-1]
        dt = (b[0] - a[0]) / 1000.0
        print("")
        print("  HALDE intern       : frei %d -> %d Byte (%+d in %.0f s)"
              % (a[1], b[1], b[1] - a[1], dt))
        print("                       groesster Block %d -> %d Byte (%+d)"
              % (a[3], b[3], b[3] - a[3]))
        print("                       Tiefstand seit Boot: %d Byte" % b[2])
        print("  HALDE PSRAM        : frei %d -> %d Byte (%+d)"
              % (a[4], b[4], b[4] - a[4]))
        print("                       groesster Block %d -> %d Byte (%+d)"
              % (a[5], b[5], b[5] - a[5]))
        # Nur das PAAR ist aussagekraeftig - siehe HeapData in serial_handler.py.
        # Die 5 % sind eine Lesehilfe, keine gemessene Grenze: fuer ein Urteil
        # muss die Mitschrift lang genug sein, um den Effekt zu zeigen.
        free_drop = (a[1] - b[1]) / float(a[1]) if a[1] else 0.0
        big_drop = (a[3] - b[3]) / float(a[3]) if a[3] else 0.0
        if free_drop > 0.05:
            print("                       -> frei faellt: sieht nach einem LECK aus")
        elif big_drop > 0.05:
            print("                       -> frei bleibt, groesster Block faellt:")
            print("                          sieht nach FRAGMENTIERUNG aus")
        else:
            print("                       -> stabil, weder Leck noch Fragmentierung")
    elif heap:
        print("")
        print("  HALDE              : nur ein Frame - zu kurz fuer einen Verlauf")

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
        print("  Trefferquote. Unter ~20 % wird es sporadisch; naeher herangehen oder")
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
    if args and args[0] in ("--folge", "-f", "--follow"):
        try:
            return folge(sys.stdin)
        except KeyboardInterrupt:
            return 0
        except BrokenPipeError:
            # Nachgeschaltetes head/grep hat dichtgemacht. Kein Fehler, aber
            # ohne dieses Aufraeumen schreibt Python beim Beenden noch einmal
            # auf denselben toten Deskriptor und meldet das als Absturz.
            try:
                sys.stdout.close()
            except Exception:
                pass
            return 0
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
