#!/usr/bin/env python3
"""Ein einzelner kleiner PWM-Schritt, ohne Zentrieren und ohne Rueckfahrt.

Benoetigt Firmware mit servo_step. Brain/Monitor stoppen. Der Port muss bereits
mit einem gueltigen PWM-Signal laufen; ohne Startsignal wird NICHT geraten.
USB-Oeffnen kann trotz abgeschaltetem DTR/RTS einen Neustart ausloesen; die normale
Boot-Firmware kann dabei andere Servos zentrieren! Bei mechanischer Gefahr nicht
benutzen, bevor auch dieses Boot-Verhalten abgesichert ist.
"""
import argparse
import glob
import json
import os
import time


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('channel', type=int, choices=range(16), metavar='PORT (0-15)')
    parser.add_argument('--port', default=os.environ.get('OWL_PORT'), help='USB-Port')
    parser.add_argument('--direction', type=int, choices=(-1, 1), default=1,
                        help='Richtung: -1 oder 1 (Standard: 1)')
    return parser.parse_args()


def find_port():
    ports = sorted({p for pattern in ('/dev/cu.usbmodem*', '/dev/ttyACM*', '/dev/ttyUSB*')
                    for p in glob.glob(pattern)})
    if len(ports) != 1:
        raise RuntimeError('Kein eindeutiger USB-Port; --port oder OWL_PORT angeben.')
    return ports[0]


def send_step(stream, channel, direction):
    # Kein Retry: eine verlorene Antwort darf keinen zweiten Schritt ausloesen.
    command = {'type': 'servo_step', 'channel': channel, 'direction': direction}
    stream.write((json.dumps(command) + '\n').encode('utf-8'))
    stream.flush()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            message = json.loads(stream.readline())
        except (ValueError, UnicodeError):
            continue
        if not isinstance(message, dict):
            continue
        if message.get('type') == 'servo_step_ack' and message.get('channel') == channel:
            if message.get('error') == 'servo_locked':
                raise RuntimeError('Servo-Sicherheitssperre aktiv. Kein Schritt ausgefuehrt. '
                                   'Belegung und individuelle Grenzen muessen zuerst verifiziert werden.')
            if message.get('ok') is not True:
                raise RuntimeError('Kein Schritt: Ausgang nicht aktiv/gueltig, noch in Bewegung, '
                                   'kein IDLE oder I2C-Fehler. Keine Startposition geraten.')
            return
    raise RuntimeError('Keine Bestaetigung (Firmware aktuell?). NICHT automatisch wiederholen: '
                       'der Schritt koennte bereits ausgefuehrt worden sein.')


def main():
    args = parse_args()
    import serial  # --help geht auch ohne pyserial

    port = args.port or find_port()
    print('Kanal %d: genau EIN PWM-Tick in Richtung %+d. Keine Rueckfahrt.'
          % (args.channel, args.direction))
    print('Brain/Monitor stoppen. Neustart kann Boot-Zentrierung ausloesen!')
    print('Auch kleinste Schritte koennen am Anschlag schaden. Strom griffbereit halten.')
    input('Bereit? Enter startet, Strg-C bricht ab: ')
    # DTR/RTS vor open deaktivieren, nicht erst danach. Kein bewusster Reset.
    stream = serial.Serial(port=None, baudrate=115200, timeout=0.1, write_timeout=1)
    stream.dtr = False
    stream.rts = False
    stream.port = port
    try:
        stream.open()
        send_step(stream, args.channel, args.direction)
        print('Schritt bestaetigt. Position bleibt stehen; kein Zentrieren/Rueckstellen.')
        print('Autonomes Verhalten bleibt pausiert. wake/Neustart hebt die Pause auf '
              'und kann groessere Bewegungen ausloesen!')
    finally:
        # Absichtlich KEIN Bewegungsbefehl bei Ende, Fehler oder Strg-C.
        stream.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nAbgebrochen. Kein Rueckstellbefehl gesendet.')
    except (RuntimeError, OSError) as exc:
        raise SystemExit(str(exc))
