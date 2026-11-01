#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Live-Klopftest: Zaehler und rohe Flanken, ohne OTA-Geste.

    ~/.platformio/penv/bin/python tools/klopftest.py   # Strg-C beendet

Oeffnen des Ports startet das Board neu. Nicht parallel zum Brain verwenden.
Klopfen und Gehen duerfen den Update-Modus nicht mehr aktivieren/beenden.
"""
import glob
import json
import os
import sys

import serial


def find_port():
    override = os.environ.get('OWL_PORT')
    if override:
        return override
    for pattern in ('/dev/cu.usbmodem*', '/dev/ttyACM*', '/dev/ttyUSB*'):
        ports = sorted(glob.glob(pattern))
        if ports:
            return ports[0]
    return None


port = find_port()
if not port:
    sys.exit('FEHLER: kein serieller Port gefunden. OWL_PORT=/dev/... setzen.')
print('Klopftest: Klopfer und Flanken beobachten. OTA nur per Orange-Pi-Software.')
try:
    with serial.Serial(port, 115200, timeout=1) as stream:
        previous = None
        while True:
            try:
                frame = json.loads(stream.readline())
            except (ValueError, UnicodeError):
                continue
            if not isinstance(frame, dict) or frame.get('type') != 'telemetry':
                continue
            vib = frame.get('vibration', {})
            status = (frame.get('state'), vib.get('count'), vib.get('pulses'), vib.get('detected'))
            if status != previous:
                print('Zustand: %s | Klopfer: %s | Flanken: %s | erkannt: %s' % status)
                previous = status
except KeyboardInterrupt:
    print('\nKlopftest beendet.')
