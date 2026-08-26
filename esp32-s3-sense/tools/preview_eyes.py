#!/usr/bin/env python3
"""Rendert die Augen-Formtabelle als HTML-Kontaktbogen zum Vergleich.

Liest SHAPES[] direkt aus lib/Eyes/Eyes.cpp und benutzt exakt dieselbe Formel
wie Eyes::drawBlob() -- Superellipse plus topSag / botRise / slantIn / slantOut.
Damit kann die Vorschau nicht von der Firmware abweichen: es gibt nur eine
Quelle fuer die Zahlen. Formen umstellen -> Tabelle in Eyes.cpp aendern ->
Skript neu laufen lassen -> Seite neu laden.

    python3 tools/preview_eyes.py && open /tmp/eyes.html
"""
import os
import re
import sys

W = H = 160
CX = CY = 80

# tools/ liegt in esp32-s3-sense/, die Bibliothek daneben unter lib/Eyes/.
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CPP = os.path.join(PROJ, "lib", "Eyes", "Eyes.cpp")
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/eyes.html"

ROW = re.compile(r"/\*\s*(\w+)\s*\*/\s*\{\s*" + r",\s*".join([r"(-?\d+)"] * 9) + r"\s*\}")

# Reihenfolge wie auf der Vorlage, damit man direkt vergleichen kann.
SHEET = [
    ["NEUTRAL", "BLINK_HIGH", "HAPPY", "GLEE", "BLINK_LOW", "SAD_DOWN", "SAD_UP"],
    ["WORRIED", "FOCUSED", "ANNOYED", "SURPRISED", "SKEPTIC", "BORED", "UNIMPRESSED"],
    ["SLEEPY", "SUSPICIOUS", "SQUINT", "ANGRY", "FURIOUS", "SCARED", "AWE"],
    ["SLEEPING", "SEARCHING", "DETECTING"],
]
LABEL = {
    "BLINK_HIGH": "Blink (high)", "BLINK_LOW": "Blink (low)",
    "SAD_DOWN": "Sad (looking down)", "SAD_UP": "Sad (looking up)",
    "FOCUSED": "Focused / Determined", "BORED": "Frustrated / Bored",
    "SEARCHING": "searching (= suspicious)", "DETECTING": "detecting (= focused)",
    "SLEEPING": "sleeping (Balken)",
}
KEYS = "halfW halfH roundness topSag botRise yOff slantIn slantOut asymH".split()


def parse_table(path):
    shapes = {}
    for m in ROW.finditer(open(path).read()):
        shapes[m.group(1)] = dict(zip(KEYS, [int(x) for x in m.groups()[1:]]))
    return shapes


def spans(s, mirrored):
    """Genau die Schleife aus Eyes::drawBlob(): pro Spalte oben/unten bestimmen."""
    n = s["roundness"] / 10.0
    halfH = s["halfH"] - (s["asymH"] if mirrored else 0)
    halfW = s["halfW"]
    if halfH <= 0 or halfW == 0:
        return []
    yc = CY + s["yOff"]
    out = []
    for dx in range(-halfW, halfW + 1):
        t = abs(dx) / halfW
        inner = 1.0 - t ** n
        if inner <= 0.0:
            continue
        ext = halfH * inner ** (1.0 / n)
        bell = 1.0 - t * t
        yTop = yc - ext + s["topSag"] * bell
        yBot = yc + ext - s["botRise"] * bell
        uIn = (0.5 - dx / (2.0 * halfW)) if mirrored else (0.5 + dx / (2.0 * halfW))
        if s["slantIn"]:
            yTop = max(yTop, yc - halfH + s["slantIn"] * uIn)
        if s["slantOut"]:
            yTop = max(yTop, yc - halfH + s["slantOut"] * (1.0 - uIn))
        y0, y1 = round(yTop), round(yBot)
        if y1 >= y0:
            out.append((CX + dx, y0, y1 - y0 + 1))
    return out


def eye_svg(s, mirrored, bar=False):
    if bar:  # SLEEPING wird als Balken gezeichnet, nicht aus der Tabelle
        rects = [(CX - 46, CY - 3, 92, 6)]
    else:
        rects = [(x, y, 1, h) for (x, y, h) in spans(s, mirrored)]
    body = "".join('<rect x="%g" y="%g" width="%g" height="%g"/>' % r for r in rects)
    return ('<svg viewBox="0 0 %d %d" width="86" height="86">'
            '<circle cx="%d" cy="%d" r="79" fill="#fff"/>'
            '<g fill="#000">%s</g></svg>' % (W, H, CX, CY, body))


def main():
    shapes = parse_table(CPP)
    missing = [n for row in SHEET for n in row if n not in shapes]
    if missing:
        print("nicht in der Tabelle gefunden:", missing)
        return 1
    print("%d Formen gelesen aus %s" % (len(shapes), CPP))

    cells = []
    for row in SHEET:
        for name in row:
            s = shapes[name]
            cells.append(
                '<figure><div class="pair">%s%s</div><figcaption>%s</figcaption>'
                '<div class="p">w%d h%d r%.1f%s%s%s%s%s</div></figure>' % (
                    eye_svg(s, False, name == "SLEEPING"),
                    eye_svg(s, True, name == "SLEEPING"),
                    LABEL.get(name, name.capitalize()),
                    s["halfW"], s["halfH"], s["roundness"] / 10.0,
                    " sag%d" % s["topSag"] if s["topSag"] else "",
                    " rise%d" % s["botRise"] if s["botRise"] else "",
                    " y%+d" % s["yOff"] if s["yOff"] else "",
                    " in%d" % s["slantIn"] if s["slantIn"] else "",
                    " out%d" % s["slantOut"] if s["slantOut"] else ""))
        cells.append('<div class="br"></div>')

    html = """<!doctype html><meta charset="utf-8"><title>Eule - Augen</title>
<style>
 body{background:#f4f4f4;color:#111;font:14px -apple-system,Helvetica,sans-serif;
      margin:0;padding:28px}
 h1{font-size:19px;margin:0 0 4px} p.sub{margin:0 0 22px;color:#666;max-width:60em}
 .grid{display:flex;flex-wrap:wrap;gap:10px 14px}
 .br{flex-basis:100%;height:6px}
 figure{margin:0;background:#fff;border:1px solid #ddd;border-radius:10px;
        padding:10px 8px 8px;width:196px;text-align:center}
 .pair{display:flex;justify-content:center;gap:6px}
 svg{shape-rendering:crispEdges}
 figcaption{margin-top:7px;font-weight:600;font-size:13px}
 .p{color:#999;font:11px ui-monospace,Menlo,monospace;margin-top:3px}
</style>
<h1>Roboter-Eule &mdash; Augen-Ausdr&uuml;cke</h1>
<p class="sub">Gerendert aus <code>lib/Eyes/Eyes.cpp</code> SHAPES[] mit der
gleichen Formel wie <code>Eyes::drawBlob()</code> &mdash; die Vorschau kann
also nicht von der Firmware abweichen. Linkes und rechtes Auge so wie auf den
Panels; Schr&auml;gen spiegeln nach innen zum Schnabel. Der wei&szlig;e Kreis
ist der sichtbare Bereich des runden Displays.</p>
<div class="grid">@@CELLS@@</div>""".replace("@@CELLS@@", "".join(cells))

    open(OUT, "w").write(html)
    print("geschrieben:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
