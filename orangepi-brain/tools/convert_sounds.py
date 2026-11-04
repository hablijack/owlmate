#!/usr/bin/env python3
"""Wandelt die Roboter-Sounds (MP3) in die WAVs um, die brain/audio.py spielt.

    python3 tools/convert_sounds.py <ordner-mit-mp3> [assets/sounds]

Pro Datei: ffmpeg dekodiert auf 22050 Hz mono 16 Bit, Stille an Anfang/Ende wird
abgeschnitten, kurze Ein-/Ausblendung (10 / 60 ms, gegen Knacken), dann alle
Clips auf dieselbe Lautheit (RMS) gebracht, Spitze auf 0,9 begrenzt -- damit kein
Sound lauter schreit als der andere. Benoetigt ffmpeg und numpy.
"""
import os
import subprocess
import sys
import wave

import numpy as np

RATE = 22050
TARGET_RMS = 0.12
PEAK_CAP = 0.9


def decode(path):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def trim(x, floor_db=-45.0, pad_ms=30):
    """Stille vorn/hinten weg (Schwelle relativ zur Spitze), mit etwas Rand."""
    thr = np.abs(x).max() * 10 ** (floor_db / 20)
    idx = np.nonzero(np.abs(x) > thr)[0]
    if idx.size == 0:
        return x
    pad = int(RATE * pad_ms / 1000)
    return x[max(0, idx[0] - pad): idx[-1] + pad]


def fade(x, in_ms=10, out_ms=60):
    n_in, n_out = int(RATE * in_ms / 1000), int(RATE * out_ms / 1000)
    x = x.copy()
    n_in, n_out = min(n_in, len(x) // 2), min(n_out, len(x) // 2)
    if n_in:
        x[:n_in] *= np.linspace(0.0, 1.0, n_in)
    if n_out:
        x[-n_out:] *= np.linspace(1.0, 0.0, n_out)
    return x


def match_loudness(x):
    rms = float(np.sqrt((x ** 2).mean())) or 1e-6
    gain = TARGET_RMS / rms
    peak = float(np.abs(x).max()) or 1e-6
    return x * min(gain, PEAK_CAP / peak)


def write_wav(path, x):
    pcm = (np.clip(x, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "..", "assets", "sounds")
    os.makedirs(dst, exist_ok=True)
    for name in sorted(os.listdir(src)):
        if not name.lower().endswith(".mp3"):
            continue
        x = match_loudness(fade(trim(decode(os.path.join(src, name)))))
        out = os.path.join(dst, name[:-4] + ".wav")
        write_wav(out, x)
        print("%-18s %.2f s  rms %.3f  peak %.2f" % (name[:-4], len(x) / RATE,
                                                      float(np.sqrt((x ** 2).mean())), float(np.abs(x).max())))


if __name__ == "__main__":
    main()
