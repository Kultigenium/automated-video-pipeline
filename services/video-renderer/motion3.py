#!/usr/bin/env python3
"""Motion-Renderer v3 — die Aussage wird belegt, nicht begleitet.

Der Unterschied zu v2: Wenn die Stimme eine Zahl nennt, fuellt diese Zahl den
Bildschirm. Wenn sie sagt, dass etwas abgeschafft wurde, wird die Zahl
durchgestrichen. Das Bild BEWEIST die Aussage im selben Moment, in dem sie
faellt - genau daran haengt, ob jemand weiterschaut.

Bewusst gegen den Vorgaenger:
  * Zahlen sind gross (bis 340 px), nicht Beiwerk unter dem Text.
  * Der Fliesstext rueckt nach unten und wird kleiner, sobald es eine Zahl
    gibt - zwei gleich laute Elemente ergeben Brei.
  * Jeder Beat startet mit einem kurzen Punch (Ueberzoom, 0,12 s).
  * Negationen ("killed", "no more", "stop") streichen die Zahl durch.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

BREITE, HOEHE = 1080, 1920
FPS = 30

WEISS = (255, 255, 255)
GRUEN = (52, 226, 148)
BLAU = (96, 175, 255)
ORANGE = (255, 172, 64)
ROT = (255, 92, 92)

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation"]


def _f(name, g):
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ImageFont.truetype(p, g)
    return ImageFont.load_default()


def sans(g):
    return _f("DejaVuSans-Bold.ttf", g)


def mono(g):
    return _f("DejaVuSansMono-Bold.ttf", g)


@dataclass
class Wort:
    text: str
    start: float
    ende: float


@dataclass
class Beat:
    woerter: list[Wort]
    start: float
    ende: float
    art: str = "fakt"
    zahl: str = ""
    zahl_zeit: float = 0.0        # wann die Zahl gesprochen wird
    durchgestrichen: bool = False
    zusatz: str = ""

    @property
    def satz(self):
        return " ".join(w.text for w in self.woerter)


VERDICT = ("however", "but ", "my take", "the catch", "worth it", "only if",
           "still expensive", "downside", "do not", "is not", "not your")
NEGATION = ("killed", "no more", "stop", "dropped", "removed", "gone",
            "without", "never", "ends", "no ")

ZW = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
      "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
      "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
      "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
      "hundred": 100, "thousand": 1000, "million": 1000000}
EINHEIT = [("percent", "%"), ("dollars", "$"), ("dollar", "$"), ("cents", "c"),
           ("hour", "h"), ("hours", "h"), ("minute", "m"), ("day", "d"),
           ("week", "w"), ("month", "mo"), ("million", "M"), ("thousand", "K")]


def _woerter(words):
    out = []
    for w in words or []:
        try:
            if isinstance(w, dict):
                out.append(Wort(str(w.get("word", "")), float(w["start"]), float(w["end"])))
            elif isinstance(w, (list, tuple)) and len(w) >= 3:
                out.append(Wort(str(w[2]), float(w[0]), float(w[1])))
        except (TypeError, ValueError, KeyError):
            continue
    return [w for w in out if w.text.strip()]


def _zahl_aus(woerter: list[Wort]):
    """Findet die erste Zahlwortkette und formatiert sie als kurzes Label."""
    tokens = [re.sub(r"[^a-z]", "", w.text.lower()) for w in woerter]
    for i, tok in enumerate(tokens):
        if tok not in ZW:
            continue
        werte, j = [ZW[tok]], i + 1
        while j < len(tokens) and tokens[j] in ZW:
            werte.append(ZW[tokens[j]])
            j += 1
        # "twenty" + "hundred" multipliziert, sonst addiert
        wert = werte[0]
        for v in werte[1:]:
            wert = wert * v if v >= 100 else wert + v
        einheit = ""
        for k in range(i, min(j + 3, len(tokens))):
            for name, sym in EINHEIT:
                if tokens[k] == name:
                    einheit = sym
                    break
            if einheit:
                break
        label = ("$%d" % wert) if einheit == "$" else ("%d%s" % (wert, einheit))
        return label, woerter[i].start
    return "", 0.0


def zerlege(words, max_dauer: float = 1.6) -> list[Beat]:
    worte = _woerter(words)
    if not worte:
        return []
    beats, puffer = [], []
    for i, w in enumerate(worte):
        puffer.append(w)
        pause = (worte[i + 1].start - w.ende) if i + 1 < len(worte) else 9.9
        if ((re.search(r"[.!?]$", w.text.strip()) or pause >= 0.13
             or (w.ende - puffer[0].start) >= max_dauer) and len(puffer) >= 2):
            beats.append(Beat(puffer, puffer[0].start, w.ende))
            puffer = []
    if puffer:
        if beats and len(puffer) < 2:
            beats[-1].woerter += puffer
            beats[-1].ende = puffer[-1].ende
        else:
            beats.append(Beat(puffer, puffer[0].start, puffer[-1].ende))

    for b in beats:
        tief = b.satz.lower()
        b.zahl, b.zahl_zeit = _zahl_aus(b.woerter)
        if b.zahl:
            b.art = "zahl"
            b.durchgestrichen = any(n in tief for n in NEGATION)
        if any(k in tief for k in VERDICT):
            b.art = "verdict"
    if beats:
        beats[0].art = "hook"
        beats[-1].art = "loop"
    return beats


# ------------------------------------------------------------------ Zeichnen
def ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def punch(t):
    """Kurzer Ueberzoom am Beat-Anfang - der visuelle Schlag."""
    if t >= 0.12:
        return 1.0
    return 1.0 + 0.16 * (1 - ease(t / 0.12))


def farbe_fuer(b: Beat):
    if b.durchgestrichen:
        return ROT
    return {"verdict": ORANGE, "hook": GRUEN, "loop": GRUEN, "zahl": BLAU}.get(b.art, WEISS)


def _umbrich(d, text, f, maxb):
    zeilen, akt = [], ""
    for w in text.split():
        probe = (akt + " " + w).strip()
        if d.textlength(probe, font=f) <= maxb:
            akt = probe
        else:
            if akt:
                zeilen.append(akt)
            akt = w
    if akt:
        zeilen.append(akt)
    return zeilen


def _schatten_text(d, xy, text, f, farbe):
    x, y = xy
    for dx, dy in ((4, 4), (-3, 3), (3, -3), (0, 5)):
        d.text((x + dx, y + dy), text, font=f, fill=(0, 0, 0, 205))
    d.text((x, y), text, font=f, fill=farbe)


def zeichne(beat: Beat, t: float, marke: str, dauer: float) -> Image.Image:
    img = Image.new("RGBA", (BREITE, HOEHE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lokal = t - beat.start
    farbe = farbe_fuer(beat)
    hat_zahl = bool(beat.zahl)

    # Marke
    fm = mono(26)
    d.rectangle([70, 90, 73, 118], fill=GRUEN + (255,))
    d.text((88, 90), marke.upper(), font=fm, fill=(255, 255, 255, 130))

    # Fortschritt
    y = HOEHE - 100
    d.rectangle([70, y, BREITE - 70, y + 5], fill=(255, 255, 255, 40))
    d.rectangle([70, y, 70 + int((BREITE - 140) * max(0, min(1, t / dauer))), y + 5],
                fill=GRUEN + (255,))

    # ---------------------------------------------------------- die Zahl
    zahl_unten = 0
    if hat_zahl:
        # erscheint genau dann, wenn sie gesprochen wird
        seit = t - beat.zahl_zeit
        if seit >= -0.12:
            a = ease(min(1.0, (seit + 0.12) / 0.26))
            groesse = int(340 * (0.72 + 0.28 * a) * punch(lokal))
            fz = mono(max(90, groesse))
            while d.textlength(beat.zahl, font=fz) > BREITE - 120 and fz.size > 90:
                fz = mono(fz.size - 10)
            bw = d.textlength(beat.zahl, font=fz)
            zx, zy = (BREITE - bw) / 2, 470
            _schatten_text(d, (zx, zy), beat.zahl, fz, farbe + (255,))
            if beat.durchgestrichen and seit > 0.18:
                sb = ease(min(1.0, (seit - 0.18) / 0.22))
                ly = zy + fz.size * 0.62
                d.line([(zx - 24, ly), (zx - 24 + (bw + 48) * sb, ly)],
                       fill=ROT + (255,), width=16)
            zahl_unten = zy + fz.size + 40

    # ---------------------------------------------------------- der Text
    gross = beat.art in ("hook", "loop") and not hat_zahl
    basis = 82 if gross else (54 if hat_zahl else 68)
    f = sans(int(basis * (punch(lokal) if not hat_zahl else 1.0)))
    zeilen = _umbrich(d, beat.satz, f, BREITE - 150)
    zh = int(f.size * 1.26)
    block = len(zeilen) * zh

    if hat_zahl:
        oben = max(zahl_unten, 980)
    else:
        oben = (HOEHE - block) // 2 + math.sin(lokal * 1.6) * 8

    ein = ease(min(1.0, lokal / 0.22))
    oben += (1 - ein) * 60

    aktiv = next((w.text for w in beat.woerter if w.start <= t <= w.ende + 0.05), None)
    yy = oben
    for zeile in zeilen:
        x = (BREITE - d.textlength(zeile, font=f)) / 2
        for wort in zeile.split():
            b = d.textlength(wort + " ", font=f)
            if aktiv is not None and wort == aktiv:
                d.rounded_rectangle([x - 10, yy - 6, x + b - 6, yy + f.size + 14],
                                    radius=12, fill=farbe + (58,))
                _schatten_text(d, (x, yy), wort, f, farbe + (255,))
            else:
                _schatten_text(d, (x, yy), wort, f, WEISS + (255,))
            x += b
        yy += zh

    # Verdict-Balken
    if beat.art == "verdict" and not hat_zahl:
        br = int(300 * ease(min(1.0, lokal / 0.35)))
        by = oben + block + 46
        d.rounded_rectangle([(BREITE - br) / 2, by, (BREITE + br) / 2, by + 7],
                            radius=4, fill=ORANGE + (255,))
    return img


def render_overlay_frames(words, out_dir, marke="DEMO", fps=FPS):
    beats = zerlege(words)
    if not beats:
        return 0, 0.0, []
    dauer = beats[-1].ende
    os.makedirs(out_dir, exist_ok=True)
    gesamt = int(dauer * fps) + fps // 2
    for n in range(gesamt):
        t = n / fps
        beat = next((b for b in beats if b.start <= t <= b.ende), None)
        if beat is None:
            beat = beats[-1] if t > dauer else beats[0]
        zeichne(beat, t, marke, dauer).save(os.path.join(out_dir, "o_%05d.png" % n))
    return gesamt, dauer, beats


def uebersicht(beats):
    from collections import Counter
    c = Counter(b.art for b in beats)
    z = [b.zahl + ("(durchgestrichen)" if b.durchgestrichen else "") for b in beats if b.zahl]
    return "%d Beats (%s) | Zahlen: %s" % (
        len(beats), ", ".join("%s=%d" % kv for kv in sorted(c.items())), z or "keine")
