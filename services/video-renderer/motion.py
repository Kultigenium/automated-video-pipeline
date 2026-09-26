#!/usr/bin/env python3
"""Motion-Renderer: Typografie zuerst.

Erzeugt aus Sprechtext + exakten Wort-Timings (ElevenLabs) eine Bildfolge, in
der der Text selbst der Hauptdarsteller ist - kein Stock-Foto mit Ken-Burns.

Warum kein Headless Chrome: Der VPS hat 2 Kerne. Ein Browser pro Frame kostet
dort ein Vielfaches dessen, was PIL braucht, und fuer Typografie, Tabellen und
Balken brauchen wir kein CSS-Layout.

Warum das authentischer ist: Bei "charges per token" steht die Preisstruktur im
Bild, bei einem Vergleich stehen die echten Zahlen da. Das Bild zeigt, wovon
die Stimme spricht - genau das unterscheidet es von austauschbarer Stockware.

Aufruf:
  render_motion.py --words words.json --script script.txt --out frames/
  render_motion.py ... --nur-keyframes    # nur ein paar Standbilder zum Pruefen
"""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

# ----------------------------------------------------------------- Design
BREITE, HOEHE = 1080, 1920
FPS = 30

GRUND = (13, 17, 23)          # sehr dunkel, Terminal-Anmutung
GRUND_2 = (22, 27, 34)        # Karten-Flaeche
TEXT = (230, 237, 243)
TEXT_MATT = (125, 133, 144)
AKZENT = (57, 211, 142)       # gruen: Zustimmung, Preis faellt
AKZENT_2 = (88, 166, 255)     # blau: neutral, Fakt
WARN = (255, 166, 87)         # orange: Einschraenkung, Verdict
GITTER = (33, 38, 45)

FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/liberation",
]


def font(name: str, groesse: int) -> ImageFont.FreeTypeFont:
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ImageFont.truetype(p, groesse)
    return ImageFont.load_default()


def sans(g: int, fett: bool = True) -> ImageFont.FreeTypeFont:
    return font("DejaVuSans-Bold.ttf" if fett else "DejaVuSans.ttf", g)


def mono(g: int, fett: bool = False) -> ImageFont.FreeTypeFont:
    return font("DejaVuSansMono-Bold.ttf" if fett else "DejaVuSansMono.ttf", g)


# ----------------------------------------------------------------- Struktur
@dataclass
class Wort:
    text: str
    start: float
    ende: float


@dataclass
class Szene:
    art: str                  # hook | fakt | liste | verdict | loop
    woerter: list[Wort]
    start: float
    ende: float
    zahlen: list[str] = field(default_factory=list)
    eintraege: list[str] = field(default_factory=list)

    @property
    def satz(self) -> str:
        return " ".join(w.text for w in self.woerter)


VERDICT_MARKER = ("however", "but ", "my take", "the catch", "worth it",
                  "only if", "downside", "still cheaper", "is not", "do not")
ZAHLWORT = r"(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|percent|dollars|cents|point|half|double)"


def zerlege(script: str, words: list[dict]) -> list[Szene]:
    """Schneidet den Text an Satzgrenzen und ordnet die Wort-Timings zu."""
    # Zwei Aufrufer, zwei Formate: der Renderer reicht sein internes
    # (start, end, wort) durch, die CLI liest {word,start,end} aus JSON.
    # Beide akzeptieren, statt am Aufrufer zu drehen.
    worte = []
    for w in words:
        try:
            if isinstance(w, dict):
                worte.append(Wort(str(w.get("word", "")), float(w["start"]), float(w["end"])))
            elif isinstance(w, (list, tuple)) and len(w) >= 3:
                worte.append(Wort(str(w[2]), float(w[0]), float(w[1])))
        except (TypeError, ValueError, KeyError):
            continue
    worte = [w for w in worte if w.text.strip()]

    # Satzenden anhand der Interpunktion in den Wort-Tokens
    szenen: list[Szene] = []
    puffer: list[Wort] = []
    for w in worte:
        puffer.append(w)
        if re.search(r"[.!?]$", w.text.strip()):
            szenen.append(_baue(puffer))
            puffer = []
    if puffer:
        szenen.append(_baue(puffer))

    # Sehr kurze Saetze mit dem naechsten zusammenlegen - sonst flackert es
    verschmolzen: list[Szene] = []
    for s in szenen:
        if verschmolzen and (s.ende - s.start) < 1.4 and len(s.woerter) <= 4:
            v = verschmolzen[-1]
            v.woerter += s.woerter
            v.ende = s.ende
            v.zahlen += s.zahlen
        else:
            verschmolzen.append(s)

    if verschmolzen:
        verschmolzen[0].art = "hook"
        verschmolzen[-1].art = "loop"
    return verschmolzen


def _baue(woerter: list[Wort]) -> Szene:
    satz = " ".join(w.text for w in woerter)
    tief = satz.lower()

    zahlen = []
    treffer = re.findall(ZAHLWORT + r"(?:\s+" + ZAHLWORT + r")*", tief)
    if treffer:
        # zusammenhaengende Zahlwortketten als eine Zahl fassen
        for m in re.finditer(r"(?:" + ZAHLWORT + r"[\s-]*)+", tief):
            zahlen.append(m.group(0).strip())

    eintraege = []
    m = re.search(r"across ([^.]+?) plans", tief)
    if m:
        eintraege = [t.strip(" ,") for t in re.split(r",| and ", m.group(1)) if t.strip(" ,")]

    art = "fakt"
    if any(k in tief for k in VERDICT_MARKER):
        art = "verdict"
    elif eintraege:
        art = "liste"

    return Szene(art, woerter, woerter[0].start, woerter[-1].ende, zahlen, eintraege)


# ----------------------------------------------------------------- Zeichnen
def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def umbrich(draw, text, f, max_breite):
    zeilen, akt = [], ""
    for wort in text.split():
        probe = (akt + " " + wort).strip()
        if draw.textlength(probe, font=f) <= max_breite:
            akt = probe
        else:
            if akt:
                zeilen.append(akt)
            akt = wort
    if akt:
        zeilen.append(akt)
    return zeilen


def hintergrund(img: Image.Image, t: float) -> None:
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, BREITE, HOEHE], fill=GRUND)
    # dezentes Raster, das langsam wandert - Bewegung ohne Ablenkung
    versatz = int((t * 12) % 60)
    for x in range(-60 + versatz, BREITE + 60, 60):
        d.line([(x, 0), (x, HOEHE)], fill=GITTER, width=1)
    for y in range(-60 + versatz, HOEHE + 60, 60):
        d.line([(0, y), (BREITE, y)], fill=GITTER, width=1)


def kopfzeile(d: ImageDraw.ImageDraw, marke: str) -> None:
    f = mono(30)
    d.rectangle([70, 96, 74, 128], fill=AKZENT)
    d.text((94, 96), marke, font=f, fill=TEXT_MATT)


def fusszeile(d: ImageDraw.ImageDraw, t: float, dauer: float) -> None:
    y = HOEHE - 120
    d.rectangle([70, y, BREITE - 70, y + 6], fill=GITTER)
    anteil = max(0.0, min(1.0, t / dauer if dauer else 0))
    d.rectangle([70, y, 70 + int((BREITE - 140) * anteil), y + 6], fill=AKZENT)


def zeichne_szene(img: Image.Image, szene: Szene, t: float, marke: str, dauer: float) -> None:
    d = ImageDraw.Draw(img)
    kopfzeile(d, marke)
    fusszeile(d, t, dauer)

    lokal = t - szene.start
    farbe_akzent = {"verdict": WARN, "hook": AKZENT, "loop": AKZENT}.get(szene.art, AKZENT_2)

    # ---- Textblock: Woerter erscheinen, sobald sie gesprochen werden
    gross = szene.art in ("hook", "loop")
    f = sans(78 if gross else 62)
    max_breite = BREITE - 160

    sichtbar = [w for w in szene.woerter if t >= w.start - 0.05]
    text_bisher = " ".join(w.text for w in sichtbar)
    zeilen = umbrich(d, text_bisher, f, max_breite) if text_bisher else []

    zeilenhoehe = int(f.size * 1.32)
    block_h = len(zeilen) * zeilenhoehe
    oben = (HOEHE - block_h) // 2 - (170 if (szene.zahlen or szene.eintraege) else 0)

    # aktuell gesprochenes Wort bestimmen
    aktiv = None
    for w in szene.woerter:
        if w.start <= t <= w.ende + 0.06:
            aktiv = w.text
            break

    y = oben
    for zeile in zeilen:
        x = (BREITE - d.textlength(zeile, font=f)) / 2
        for wort in zeile.split():
            b = d.textlength(wort + " ", font=f)
            ist_aktiv = aktiv is not None and wort == aktiv
            if ist_aktiv:
                pad = 10
                d.rounded_rectangle(
                    [x - pad, y - 6, x + b - 10 + pad, y + f.size + 14],
                    radius=12, fill=(farbe_akzent[0] // 5, farbe_akzent[1] // 5, farbe_akzent[2] // 5),
                )
            d.text((x, y), wort, font=f, fill=farbe_akzent if ist_aktiv else TEXT)
            x += b
        y += zeilenhoehe

    # ---- Belegkarte: zeigt, wovon gerade die Rede ist
    if szene.eintraege:
        _karte_liste(d, szene, lokal, farbe_akzent, oben + block_h + 70)
    elif szene.zahlen:
        _karte_zahl(d, szene, lokal, farbe_akzent, oben + block_h + 70)
    elif szene.art == "verdict":
        _karte_verdict(d, lokal, oben + block_h + 70)


def _karte_liste(d, szene, lokal, farbe, y0):
    x0, x1 = 150, BREITE - 150
    hoehe = 90 + len(szene.eintraege) * 92
    d.rounded_rectangle([x0, y0, x1, y0 + hoehe], radius=22, fill=GRUND_2)
    d.text((x0 + 40, y0 + 28), "PLANS", font=mono(30, True), fill=TEXT_MATT)
    fm = mono(46, True)
    for i, e in enumerate(szene.eintraege):
        # Eintraege bauen sich nacheinander auf
        erscheint = 0.35 + i * 0.42
        if lokal < erscheint:
            continue
        a = ease((lokal - erscheint) / 0.4)
        y = y0 + 88 + i * 92
        d.rectangle([x0 + 40, y + 12, x0 + 40 + int(8 * a), y + 46], fill=farbe)
        d.text((x0 + 70, y), e.upper(), font=fm, fill=TEXT)
        d.text((x1 - 40 - d.textlength("per token", font=mono(34)), y + 8),
               "per token", font=mono(34), fill=TEXT_MATT)


def _karte_zahl(d, szene, lokal, farbe, y0):
    zahl = max(szene.zahlen, key=len)
    x0, x1 = 150, BREITE - 150
    d.rounded_rectangle([x0, y0, x1, y0 + 210], radius=22, fill=GRUND_2)
    a = ease(lokal / 0.5)
    f = mono(int(lerp(60, 96, a)), True)
    txt = zahl.upper()
    while d.textlength(txt, font=f) > (x1 - x0 - 80) and f.size > 40:
        f = mono(f.size - 4, True)
    d.text(((BREITE - d.textlength(txt, font=f)) / 2, y0 + 60), txt, font=f, fill=farbe)


def _karte_verdict(d, lokal, y0):
    x0, x1 = 150, BREITE - 150
    d.rounded_rectangle([x0, y0, x1, y0 + 130], radius=22, fill=GRUND_2)
    d.rectangle([x0, y0, x0 + 8, y0 + 130], fill=WARN)
    a = ease(lokal / 0.5)
    d.text((x0 + 44, y0 + 26), "MY TAKE", font=mono(34, True), fill=WARN)
    d.text((x0 + 44, y0 + 72), "the honest catch", font=mono(34),
           fill=(int(TEXT_MATT[0] * a), int(TEXT_MATT[1] * a), int(TEXT_MATT[2] * a)))


# ----------------------------------------------------------------- Bibliothek
def render_frames(words: list, out_dir: str, marke: str = "DEMO",
                  fps: int = FPS) -> tuple:
    """Rendert die Bildfolge und gibt (anzahl_frames, dauer, szenen) zurueck.

    words: [{word,start,end}] - aus dem TTS oder von Whisper. Beide Quellen
    liefern dasselbe Format, daher funktioniert der Motion-Stil auch dann,
    wenn der Kokoro-Fallback gesprochen hat.
    """
    szenen = zerlege("", words)
    if not szenen:
        return 0, 0.0, []
    dauer = szenen[-1].ende
    os.makedirs(out_dir, exist_ok=True)

    gesamt = int(dauer * fps) + fps // 2
    for n in range(gesamt):
        t = n / fps
        szene = next((s for s in szenen if s.start <= t <= s.ende), None)
        if szene is None:
            szene = szenen[-1] if t > dauer else szenen[0]
        img = Image.new("RGB", (BREITE, HOEHE), GRUND)
        hintergrund(img, t)
        zeichne_szene(img, szene, t, marke, dauer)
        img.save(os.path.join(out_dir, "f_%05d.png" % n))
    return gesamt, dauer, szenen


def szenen_uebersicht(szenen: list) -> str:
    """Einzeiler fuers Log - welche Szenenarten kamen heraus."""
    from collections import Counter
    c = Counter(s.art for s in szenen)
    return ", ".join("%s=%d" % (k, v) for k, v in sorted(c.items()))


# ----------------------------------------------------------------- CLI
def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", required=True)
    ap.add_argument("--script", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--marke", default="DEMO")
    ap.add_argument("--fps", type=int, default=FPS)
    ap.add_argument("--nur-keyframes", action="store_true")
    args = ap.parse_args()

    words = json.load(open(args.words, encoding="utf-8"))
    szenen = zerlege("", words)
    dauer = szenen[-1].ende if szenen else 0.0
    print("Szenen: %d | Dauer: %.2fs" % (len(szenen), dauer))
    for i, s in enumerate(szenen):
        print("  %d. [%-7s] %5.2f-%5.2fs  %s" % (i + 1, s.art, s.start, s.ende, s.satz[:52]))

    if args.nur_keyframes:
        os.makedirs(args.out, exist_ok=True)
        for i, s in enumerate(szenen):
            t = s.start + (s.ende - s.start) * 0.75
            img = Image.new("RGB", (BREITE, HOEHE), GRUND)
            hintergrund(img, t)
            zeichne_szene(img, s, t, args.marke, dauer)
            img.save(os.path.join(args.out, "szene_%02d_%s.png" % (i + 1, s.art)))
        print("%d Keyframes -> %s" % (len(szenen), args.out))
        return

    n, d, sz = render_frames(words, args.out, args.marke, args.fps)
    print("%d Frames (%.2fs) -> %s" % (n, d, args.out))


if __name__ == "__main__":
    main()
