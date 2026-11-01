#!/usr/bin/env python3
"""A733-Benchtest: acht Sekunden Mikrofon, dazwischen ein leiser Testton.

Als robotowl ausfuehren (gleiche ALSA-IPC-Rechte wie der Brain-Dienst).
Ohne laufenden TX-Keepalive reproduziert dieser Test auf dem installierten
Kernel das Verstummen nach Playback-Ende. Er testet keine Spracherkennung.
Aufnahmen liegen nur in einem temporaeren Verzeichnis und werden geloescht.
"""
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np


def main():
    with tempfile.TemporaryDirectory(prefix='owl-duplex-') as directory:
        root = Path(directory)
        t = np.arange(48000) / 48000
        tone = (np.sin(2*np.pi*660*t) * 0.025 * 32767).astype('<i2')
        with wave.open(str(root/'tone.wav'), 'wb') as f:
            f.setnchannels(2)
            f.setsampwidth(2)
            f.setframerate(48000)
            f.writeframes(np.repeat(tone[:, None], 2, axis=1).tobytes())
        capture = subprocess.Popen(['arecord', '-q', '-D', 'owl_capture', '-f',
                                    'S16_LE', '-r', '48000', '-c', '2', '-d', '8',
                                    str(root/'capture.wav')])
        try:
            time.sleep(2)
            subprocess.run(['aplay', '-q', '-D', 'owl_playback', str(root/'tone.wav')],
                           check=True, timeout=6)
            if capture.wait(timeout=10) != 0:
                raise RuntimeError('Aufnahme fehlgeschlagen')
        finally:
            if capture.poll() is None:
                capture.kill()
                capture.wait(timeout=2)
        with wave.open(str(root/'capture.wav')) as f:
            if f.getnframes() != 8*48000:
                raise RuntimeError('Aufnahme unvollstaendig')
            samples = np.frombuffer(f.readframes(f.getnframes()), dtype='<i2')
            left = samples.reshape(-1, 2)[:, 0].astype(float)
        rms = [float(np.sqrt(np.mean(block*block))) for block in np.array_split(left, 8)]
        print('Linkes Mikrofon, RMS je Sekunde:', [round(x, 3) for x in rms])
        if any(x == 0 for x in rms[4:]):
            raise RuntimeError('Mikrofon nach Playback-Ende digital stumm!')
        if np.any(np.abs(left) >= 32760):
            raise RuntimeError('Aufnahme clippt')
        print('OK: Mikrofon liefert auch nach Playback-Ende weiter Samples.')


if __name__ == '__main__':
    main()
