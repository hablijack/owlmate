#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Live-Assistent fuer die Magnetometer-Kalibrierung der Roboter-Eule.

Einmal ausfuehren, danach liegen die Hartmagnet-Offsets im NVS-Flash der ESP32
und werden bei jedem Boot wiederhergestellt ("IMU: Hartmagnet-Offsets aus dem
Flash: ..."). Nur noetig, wenn der Sensor getauscht/neu montiert wurde, das NVS
geloescht wurde, oder sich die magnetische Umgebung der Eule geaendert hat.

SEIT 2026-09-01 IST DAS EIN ANDERES VERFAHREN. Vorher sass ein BNO055 in der
Eule, der vier eigene Kalibrierzaehler (sys/gyro/accel/mag) hatte und ein
vierphasiges Programm brauchte, bei dem die Reihenfolge zwingend war: die
liegende Acht MUSSTE vor den Ruhelagen kommen, weil anhaltende Bewegung den
accel-Zaehler wieder auf 0 zurueckwarf.

DAS IST ALLES WEG. Der LSM303AGR hat kein Gyroskop, keine interne Fusion und
keine Zaehler - er liefert nur zwei Rohvektoren, und alles andere rechnet die
Firmware (lib/OwlImu). Zu kalibrieren ist deshalb nur noch EINE Sache: der
konstante Magnetfeldversatz, den die Eule selbst erzeugt (Lautsprecher, Servos,
Schrauben). Das braucht genau eine langsame Volldrehung und hat keine
Reihenfolge mehr.

WARUM 2 VON 3 ACHSEN GENUEGT: das Erdfeld hat hier etwa 49 uT bei ~66 Grad
Neigung, also nur ~20 uT waagerechten Anteil. Eine Drehung um die Hochachse
ueberstreicht damit die zwei WAAGERECHTEN Achsen voll und die senkrechte gar
nicht. 2/3 ist bei einer waagerechten Drehung das Maximum, nicht ein
Teilerfolg - fuer den Kompass reicht es. 3/3 gaebe es nur, wenn man die Eule
zusaetzlich ueber Kopf dreht.

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


BAUD = 115200
# Ab so vielen abgedeckten Achsen ist der Kurs brauchbar - dieselbe Schwelle,
# die die Firmware fuer imu.calibrated benutzt (siehe MAG_CAL_MIN_SPAN_UT und
# OwlImu::fuse()).
ZIEL_ACHSEN = 2

B = "\033[1m"; R = "\033[0m"; G = "\033[32m"; Y = "\033[33m"; C = "\033[36m"

ANLEITUNG = [
    "Eule LANGSAM einmal ganz um die Hochachse drehen -",
    "eine Volldrehung in etwa 20-30 Sekunden. Ruhig",
    "weiterdrehen, auch nach der ersten Runde.",
    "",
    "Abstand halten von Laptop / Lautsprechern / Metall -",
    "die verzerren das Magnetfeld und kalibrieren einen",
    "Fehler mit ein, der hinterher nicht mehr da ist.",
    "",
    "Die Eule darf dabei auf dem Tisch stehen bleiben.",
]


def balken(achsen, heading_ok, restored):
    farbe = G if achsen >= ZIEL_ACHSEN else (Y if achsen > 0 else "")
    teile = ["%sAchsen [%s%s] %d/3%s" % (farbe, "#" * achsen,
                                         "." * (3 - achsen), achsen, R)]
    teile.append("Kurs %s%s%s" % (G if heading_ok else Y,
                                  "ok " if heading_ok else "-- ", R))
    if restored:
        teile.append("(Offsets aus dem Flash)")
    return "   ".join(teile)


def main():
    print("%s%sMagnetometer-Kalibrierung (LSM303AGR) - Roboter-Eule%s" % (B, C, R))
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

    print()
    print("%s%s== So geht es ==%s" % (C, B, R))
    for z in ANLEITUNG:
        print("   " + z)
    print()

    achsen = 0
    heading_ok = False
    restored = False
    gespeichert = False
    gesehen = False
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
                if "gespeichert" in text:
                    gespeichert = True

            if not text.startswith('{"type":"telemetry"'):
                continue
            try:
                imu = json.loads(text)["imu"]
                cal = imu["cal"]
            except Exception:
                # Kein imu-Objekt = Sensor nicht bereit. Die Firmware laesst
                # das Objekt dann bewusst ganz weg.
                continue

            achsen = int(cal.get("axes", 0))
            heading_ok = bool(cal.get("heading_ok", False))
            restored = bool(cal.get("restored", False))

            jetzt = time.time()
            if jetzt - letzte_ausgabe > 0.4:
                sys.stdout.write("\r   %s   (%3ds)" % (
                    balken(achsen, heading_ok, restored), int(jetzt - begonnen)))
                sys.stdout.flush()
                letzte_ausgabe = jetzt

            if achsen >= ZIEL_ACHSEN and not gesehen:
                gesehen = True
                print("\n")
                print("%s%s=============================================%s" % (B, G, R))
                print("%s%s  GENUG GEDREHT - %d von 3 Achsen abgedeckt   %s"
                      % (B, G, achsen, R))
                print("%s%s=============================================%s" % (B, G, R))
                if not gespeichert:
                    print("Warte kurz auf die Speicher-Meldung ...")
                    ende = time.time() + 5
                    while time.time() < ende and not gespeichert:
                        l2 = s.readline()
                        if l2 and "gespeichert" in l2.decode("utf-8", "replace"):
                            gespeichert = True
                print("Offsets sind im Flash." if gespeichert else
                      "Keine Speicher-Meldung gesehen (evtl. schon vorher gespeichert).")
                print()
                print("Ab jetzt startet die Eule bei jedem Boot kalibriert.")
                if heading_ok:
                    print("Der Kompass (yaw) ist jetzt nutzbar.")
                else:
                    print("%sKurs noch nicht brauchbar - steht der Schnabel"
                          " zu steil?%s" % (Y, R))
                print()
                print("%sACHTUNG - das ist noch nicht der ganze Weg zum"
                      " richtigen Kurs:%s" % (Y, R))
                print("   Die Kalibrierung macht den Kurs STABIL, nicht"
                      " automatisch RICHTIG.")
                print("   Solange der Einbau-Anteil von IMU_HEADING_OFFSET_DEG"
                      " nicht gemessen ist,")
                print("   ist yaw um die Montagedrehung verschoben. Siehe"
                      " specs/006 Open.")
                print()
                print("Weiterdrehen verbessert die Offsets noch; Strg-C beendet.")
    except KeyboardInterrupt:
        print("\nBeendet. Stand: %s" % balken(achsen, heading_ok, restored))
    finally:
        s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
