#!/usr/bin/env python3
"""Beweis-Footage: das Video zeigt die echte Quelle, statt sie zu behaupten.

Warum: Stockmaterial begleitet eine Aussage nur. Wenn die Stimme "twenty
dollars" sagt und im selben Moment auf der echten Seite "$20" angestrichen
wird, ist das ein Beleg - genau das trennt einen Kanal mit Einordnung von
austauschbarer KI-Ware.

Ablauf:
  1. Chromium oeffnet die Quelle in Handybreite (360 CSS-px, dreifach
     aufgeloest = exakt 1080 px). Kein Hochskalieren, Schrift bleibt gross.
  2. Cookie-Banner werden weggeklickt oder ausgeblendet.
  3. Ein Capture der ganzen Seite plus die Positionen der Belegstellen:
     Zahlen ("$20", "5-hour") und gesprochene Phrasen, die woertlich auf der
     Seite stehen ("per token").
  4. Eine virtuelle Kamera faehrt ueber den Capture und liest mit: sie
     scrollt zur Stelle, sobald die Stimme sie nennt, und streicht sie an.

Bewusste Grenzen der Kamera:
  * Kein seitlicher Schwenk und hoechstens 16 % Zoom. Staerkerer Zoom
    schneidet Textzeilen links und rechts mitten im Wort ab - das sah im
    ersten Test billig aus.
  * Virtuelle Kamera statt Live-Mitschnitt: Playwrights Videoaufnahme
    liefert auf 2 Kernen ruckelige Bildraten mit Artefakten.

Lokaler Test:
  python beweis.py --url https://... --audio voice.mp3 --words words.json --out test.mp4
"""
from __future__ import annotations

import argparse
import functools
import io
import ipaddress
import json
import logging
import os
import re
import socket
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("beweis")

BREITE, HOEHE = 1080, 1920
FPS = 30
CSS_BREITE, CSS_HOEHE = 360, 640     # Handy-Viewport, 9:16
DPR = 3                              # 360 * 3 = 1080 px
MAX_CSS_HOEHE = 5000                 # 15000 px - darueber kachelt Chromium Screenshots fehlerhaft
LEISTE = 150                         # Hoehe der Browserleiste im Bild
FOKUS_Y = 760                        # dort sitzt die Belegstelle auf dem Schirm
UNTERTITEL_Y = 1330
ZOOM_ZAHL = 1.16                     # schneidet nur den Seitenrand weg, nie Text
ZOOM_PHRASE = 1.07
MIN_ABSTAND = 1.4                    # Sekunden zwischen zwei entfernten Kamerazielen
HALTE_TITEL = 0.9                    # so lange steht die Ueberschrift, bevor die Kamera losfaehrt
MAX_BROLL_ANTEIL = 0.5               # der Beleg behaelt die Mehrheit der Laufzeit
MIN_BROLL = 1.2                      # kuerzere Clip-Einblendungen wirken wie Flackern

AKZENT = (52, 226, 148)
ROT = (255, 92, 92)
WEISS = (255, 255, 255)

MOBIL_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)

SCHRIFTEN = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",     # nur fuer lokale Tests unter Windows
    "C:/Windows/Fonts/arialbd.ttf",
]


@functools.lru_cache(maxsize=16)
@functools.lru_cache(maxsize=256)
def _host_oeffentlich(host: str) -> bool:
    """True, wenn JEDE Adresse des Hosts im oeffentlichen Internet liegt.

    Schutz gegen SSRF: Die Quelle kommt als URL von aussen. Ohne diese Pruefung
    koennte ein Aufrufer den Browser auf interne Dienste (andere Container,
    localhost, Cloud-Metadaten) schicken und deren Inhalt als Video zurueckbekommen.
    """
    try:
        adressen = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    return bool(adressen) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in adressen)


def ist_oeffentliche_url(url: str) -> bool:
    """Nur http(s) auf oeffentliche Hosts. Gilt fuer Quelle, Weiterleitungen und Unterressourcen."""
    try:
        teile = urlparse(url)
    except ValueError:
        return False
    return teile.scheme in ("http", "https") and bool(teile.hostname) and _host_oeffentlich(teile.hostname)


def _nur_oeffentlich(route) -> None:
    """Playwright-Route: blockt jede Anfrage an interne Adressen, auch nach Redirects."""
    url = route.request.url
    if url.startswith(("data:", "blob:")) or ist_oeffentliche_url(url):
        route.continue_()
    else:
        log.warning("Beweis: Anfrage an nicht-oeffentliche Adresse geblockt: %s", url[:120])
        route.abort()


def schrift(groesse: int) -> ImageFont.FreeTypeFont:
    for p in SCHRIFTEN:
        if os.path.exists(p):
            return ImageFont.truetype(p, groesse)
    return ImageFont.load_default()


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_io(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


# ------------------------------------------------------------ Zahlen im Text
ZW = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
      "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
      "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
      "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
      "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
      "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000,
      "million": 1000000}
EINHEIT = {"percent": "%", "dollars": "$", "dollar": "$", "bucks": "$",
           "hour": "h", "hours": "h", "minute": "min", "minutes": "min",
           "day": "d", "days": "d", "week": "w", "weeks": "w",
           "month": "mo", "months": "mo", "times": "x"}
NEGATION = {"killed", "kills", "dropped", "drops", "removed", "removes",
            "scrapped", "ends", "ended", "no", "gone", "without"}
ZAHLWORT = {v: k for k, v in ZW.items() if v <= 20}


@dataclass
class Zahl:
    label: str          # "$20", "5h", "40%"
    zeit: float         # Beginn der Zahl im Audio
    negiert: bool = False


def zahlen_aus(words) -> list[Zahl]:
    """Findet ALLE Zahlen mit Einheit im gesprochenen Text, samt Zeitpunkt.

    Anders als motion3 nicht nur die erste pro Beat, und Bindestriche werden
    getrennt: "five-hour" kommt als EIN Wort vom TTS.
    """
    toks: list[tuple[str, float]] = []
    for w in words or []:
        try:
            if isinstance(w, dict):
                wort, start = str(w.get("word", "")), float(w["start"])
            else:
                wort, start = str(w[2]), float(w[0])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        for teil in re.split(r"[-\u2010-\u2013/]", wort):
            t = re.sub(r"[^a-z0-9$%.]", "", teil.lower()).strip(".")
            if t:
                toks.append((t, start))

    out: list[Zahl] = []
    i = 0
    while i < len(toks):
        t = toks[i][0]
        m = re.fullmatch(r"(\$?)(\d+(?:\.\d+)?)(%?)", t)
        if m:
            wert, j = float(m.group(2)), i + 1
            einheit = "$" if m.group(1) else ("%" if m.group(3) else "")
        elif t in ZW:
            werte, j = [ZW[t]], i + 1
            while j < len(toks) and toks[j][0] in ZW:
                werte.append(ZW[toks[j][0]])
                j += 1
            wert = werte[0]
            for v in werte[1:]:
                wert = wert * v if v >= 100 else wert + v
            einheit = ""
        else:
            i += 1
            continue

        if not einheit:
            for k in range(j, min(j + 2, len(toks))):
                if toks[k][0] in EINHEIT:
                    einheit = EINHEIT[toks[k][0]]
                    break
        # Zahlen ohne Einheit sind mehrdeutig ("the one thing") - bewusst weglassen
        if einheit:
            zahl = str(int(wert)) if wert == int(wert) else str(wert)
            label = "$" + zahl if einheit == "$" else zahl + einheit
            vorher = {x[0] for x in toks[max(0, i - 3):i]}
            out.append(Zahl(label, toks[i][1], bool(vorher & NEGATION)))
        i = j
    return out


def schreibweisen(label: str) -> list[str]:
    """Wie eine gesprochene Zahl auf einer Webseite geschrieben steht."""
    m = re.fullmatch(r"\$(\d+(?:\.\d+)?)", label)
    if m:
        n = m.group(1)
        return ["$" + n, n + " usd", "usd " + n]
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(%|h|min|d|w|mo|x)", label)
    if not m:
        return []
    n, e = m.groups()
    if e == "%":
        return [n + "%", n + " %", n + " percent"]
    if e == "x":
        return [n + "x", n + "\u00d7", n + " times"]
    wort = {"h": "hour", "min": "minute", "d": "day", "w": "week", "mo": "month"}[e]
    varianten = [n + "-" + wort, n + " " + wort, n + e]
    if n.isdigit() and int(n) in ZAHLWORT:
        varianten += [ZAHLWORT[int(n)] + "-" + wort, ZAHLWORT[int(n)] + " " + wort]
    return varianten


# ---------------------------------------------------------- Phrasen im Text
STOPP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
         "is", "are", "was", "it", "its", "it's", "this", "that", "you", "your",
         "we", "our", "they", "their", "now", "just", "so", "if", "at", "by",
         "be", "as", "but", "not", "no", "my", "me", "i", "do", "does", "get",
         "gets", "got", "from", "up", "out", "about", "than", "then", "there",
         "here", "what", "which", "who", "how", "all", "any", "more", "most",
         "very", "can", "will", "would", "should", "could", "has", "have",
         "had", "after", "before", "into", "over", "take"}


@dataclass
class Phrase:
    text: str
    zeit: float
    laenge: int
    beat: tuple            # (start, ende) des Beats


def phrasen_aus(words) -> list[Phrase]:
    """Zwei- und Dreiwort-Phrasen je Beat - Kandidaten zum Mitlesen auf der Seite."""
    from motion3 import zerlege

    out: list[Phrase] = []
    for b in zerlege(words):
        toks = []
        for w in b.woerter:
            t = re.sub(r"[^a-z0-9'-]", "", w.text.lower().replace("\u2019", "'")).strip("-'")
            if t:
                toks.append((t, w.start))
        for n in (3, 2):
            for i in range(len(toks) - n + 1):
                gruppe = toks[i:i + n]
                woerter = [g[0] for g in gruppe]
                if woerter[0] in STOPP or woerter[-1] in STOPP:
                    continue
                if not any(len(x) > 3 and x not in STOPP for x in woerter):
                    continue
                out.append(Phrase(" ".join(woerter), gruppe[0][1], n, (b.start, b.ende)))
    return out


def phrasen_nadeln(p: Phrase) -> list[str]:
    return [p.text, p.text.replace(" ", "-")]


# ------------------------------------------------------------------ Capture
COOKIE_KNOEPFE = re.compile(
    r"^\s*(accept( all)?( cookies)?|agree|i agree|allow all|got it|okay|ok|"
    r"alle akzeptieren|akzeptieren|zustimmen|einverstanden)\s*$", re.I)

BANNER_WEG_JS = """
() => {
  const muster = /cookie|consent|gdpr|cmp-|onetrust|didomi|privacy-banner/i;
  for (const el of document.querySelectorAll('body *')) {
    const s = getComputedStyle(el);
    if (s.position !== 'fixed' && s.position !== 'sticky') continue;
    const name = (el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '');
    if (muster.test(name)) el.style.setProperty('display', 'none', 'important');
  }
  document.documentElement.style.overflow = 'visible';
  document.body.style.overflow = 'visible';
}
"""

LAZY_JS = """
async (maxCss) => {
  const h = Math.min(document.documentElement.scrollHeight, maxCss);
  for (let y = 0; y < h; y += 600) {
    window.scrollTo(0, y);
    await new Promise(r => setTimeout(r, 120));
  }
  window.scrollTo(0, 0);
}
"""

FINDE_JS = """
([nadeln, maxCss]) => {
  const knoten = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    if (!n.nodeValue || !n.nodeValue.trim() || !n.parentElement) continue;
    const s = getComputedStyle(n.parentElement);
    if (s.visibility === 'hidden' || s.display === 'none') continue;
    knoten.push(n);
  }
  // nur gleich lange Ersetzungen, damit die Zeichenpositionen stimmen
  const norm = t => t.toLowerCase()
    .replace(/[\\u00a0\\u2009\\u202f]/g, ' ')
    .replace(/[\\u2010-\\u2013]/g, '-')
    .replace(/[\\u2018\\u2019]/g, "'");
  const wortzeichen = /[a-z0-9]/;
  const treffer = {};
  for (const nadel of nadeln) {
    const ziel = nadel.toLowerCase();
    treffer[nadel] = [];
    for (const k of knoten) {
      const text = norm(k.nodeValue);
      let i = text.indexOf(ziel);
      while (i >= 0 && treffer[nadel].length < 3) {
        // nur ganze Woerter: "$20" nicht in "$200", "cap" nicht in "capacity"
        const vor = text[i - 1] || ' ', nach = text[i + ziel.length] || ' ';
        if (!wortzeichen.test(vor) && !wortzeichen.test(nach)) {
          const r = document.createRange();
          r.setStart(k, i);
          r.setEnd(k, i + ziel.length);
          // bei Zeilenumbruch nur das Stueck auf der ersten Zeile - das
          // umschliessende Rechteck waere sonst zeilenbreit
          const b = [...r.getClientRects()].find(q => q.width > 2 && q.height > 2);
          const y = b ? b.top + window.scrollY : 0;
          if (b && y < maxCss) {
            treffer[nadel].push([b.left + window.scrollX, y, b.width, b.height]);
          }
        }
        i = text.indexOf(ziel, i + ziel.length);
      }
      if (treffer[nadel].length >= 3) break;
    }
  }
  const h = [...document.querySelectorAll('h1, h2')].find(e => {
    const b = e.getBoundingClientRect();
    return b.width > 10 && b.height > 10;
  });
  let titel = null;
  if (h) {
    const b = h.getBoundingClientRect();
    titel = [b.left + window.scrollX, b.top + window.scrollY, b.width, b.height];
  }
  return {
    treffer, titel,
    hoehe: Math.min(document.documentElement.scrollHeight, maxCss),
    textlaenge: (document.body.innerText || '').length,
    seitentitel: document.title || '',
  };
}
"""

GESPERRT = re.compile(r"just a moment|access denied|attention required|verify you are human|"
                      r"are you a robot|403 forbidden", re.I)


@dataclass
class Erfassung:
    bild: Image.Image       # Capture, 1080 px breit
    domain: str
    titel: tuple | None     # (x, y, b, h) in Capture-Pixeln
    treffer: dict           # Schreibweise -> [(x, y, b, h), ...]


def _banner_weg(page) -> None:
    for rolle in ("button", "link"):
        try:
            knopf = page.get_by_role(rolle, name=COOKIE_KNOEPFE).first
            if knopf.is_visible():
                knopf.click(timeout=1500)
                page.wait_for_timeout(400)
                break
        except Exception:
            continue
    try:
        page.evaluate(BANNER_WEG_JS)
    except Exception:
        pass


def erfasse(url: str, nadeln: list[str], timeout_s: float = 25.0) -> Erfassung | None:
    """Oeffnet die Quelle und liefert Capture + Belegstellen. None bei jedem Problem."""
    if not url or not ist_oeffentliche_url(url):
        log.warning("Beweis: Quelle ist keine oeffentliche http(s)-URL - uebersprungen")
        return None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("playwright fehlt - kein Beweis-Footage")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                ctx = browser.new_context(
                    viewport={"width": CSS_BREITE, "height": CSS_HOEHE},
                    device_scale_factor=DPR, is_mobile=True, has_touch=True,
                    user_agent=MOBIL_UA, locale="en-US", color_scheme="dark",
                    reduced_motion="reduce",
                )
                ctx.route("**/*", _nur_oeffentlich)
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                _banner_weg(page)
                try:
                    page.evaluate(LAZY_JS, MAX_CSS_HOEHE)
                    page.wait_for_timeout(500)
                except Exception:
                    pass
                daten = page.evaluate(FINDE_JS, [nadeln, MAX_CSS_HOEHE])
                if daten["textlaenge"] < 400 or GESPERRT.search(daten["seitentitel"]):
                    log.warning("Quelle nicht lesbar (%d Zeichen, '%s'): %s",
                                daten["textlaenge"], daten["seitentitel"][:60], url)
                    return None
                hoehe_css = int(max(CSS_HOEHE, min(daten["hoehe"], MAX_CSS_HOEHE)))
                png = page.screenshot(
                    full_page=True, animations="disabled",
                    clip={"x": 0, "y": 0, "width": CSS_BREITE, "height": hoehe_css},
                )
            finally:
                browser.close()
    except Exception as exc:
        log.warning("Beweis-Capture fehlgeschlagen (%s): %s", url, exc)
        return None

    bild = Image.open(io.BytesIO(png)).convert("RGB")
    if bild.width != BREITE:
        bild = bild.resize((BREITE, round(bild.height * BREITE / bild.width)), Image.LANCZOS)
    # die Kamera braucht mindestens eine volle Bildschirmhoehe
    if bild.height < HOEHE + 2:
        leinwand = Image.new("RGB", (BREITE, HOEHE + 2), bild.getpixel((0, bild.height - 1)))
        leinwand.paste(bild, (0, 0))
        bild = leinwand

    skala = BREITE / CSS_BREITE
    px = lambda b: tuple(v * skala for v in b)  # noqa: E731
    treffer = {k: [px(b) for b in v] for k, v in daten["treffer"].items() if v}
    titel = px(daten["titel"]) if daten.get("titel") else None
    domain = urlparse(url).netloc
    domain = domain[4:] if domain.startswith("www.") else domain
    log.info("Beweis-Capture: %s, %dx%d px, %d Stellen gefunden",
             domain, bild.width, bild.height, len(treffer))
    return Erfassung(bild, domain, titel, treffer)


def nadeln_fuer(zahlen: list[Zahl], phrasen: list[Phrase]) -> list[str]:
    return sorted({n for z in zahlen for n in schreibweisen(z.label)}
                  | {n for p in phrasen for n in phrasen_nadeln(p)})


# ------------------------------------------------------------------- Kamera
@dataclass
class Fokus:
    zeit: float
    box: tuple              # (x, y, b, h) in Capture-Pixeln
    label: str = ""
    negiert: bool = False
    art: str = "zahl"       # zahl | phrase


def fokusse_fuer(erf: Erfassung, zahlen: list[Zahl], phrasen: list[Phrase]) -> list[Fokus]:
    """Waehlt die Kameraziele: Zahlen zuerst, sonst die laengste Phrase je Beat."""
    kandidaten = []
    for z in zahlen:
        boxen = next((erf.treffer[n] for n in schreibweisen(z.label) if n in erf.treffer), None)
        if boxen:
            kandidaten.append((z.zeit, boxen, z.label, z.negiert, "zahl"))
    zahl_zeiten = [k[0] for k in kandidaten]

    beste: dict[tuple, tuple[Phrase, list]] = {}
    for p in phrasen:
        boxen = next((erf.treffer[n] for n in phrasen_nadeln(p) if n in erf.treffer), None)
        if not boxen:
            continue
        bisher = beste.get(p.beat)
        if bisher is None or (p.laenge, len(p.text)) > (bisher[0].laenge, len(bisher[0].text)):
            beste[p.beat] = (p, boxen)
    for (start, ende), (p, boxen) in beste.items():
        if not any(start - 0.3 <= zt <= ende + 0.3 for zt in zahl_zeiten):
            kandidaten.append((p.zeit, boxen, p.text, False, "phrase"))

    kandidaten.sort(key=lambda k: k[0])
    fokusse: list[Fokus] = []
    vorher_y = erf.titel[1] if erf.titel else 0.0
    for zeit, boxen, label, negiert, art in kandidaten:
        # bei mehreren Treffern den naechstgelegenen: die Kamera soll lesen, nicht springen
        box = min(boxen, key=lambda b: abs(b[1] - vorher_y))
        nah = abs(box[1] - vorher_y) < 500
        if fokusse and zeit - fokusse[-1].zeit < MIN_ABSTAND and not nah:
            if art == "phrase" or fokusse[-1].art == "zahl":
                continue
            fokusse.pop()          # eine Zahl verdraengt die Phrase kurz davor
        fokusse.append(Fokus(zeit, box, label, negiert, art))
        vorher_y = box[1]
    return fokusse


class Kamera:
    """Keyframes (zeit, cy, zoom) in Capture-Pixeln, weich interpoliert."""

    def __init__(self, keyframes):
        self.k = sorted(keyframes, key=lambda k: k[0])

    def an(self, t: float) -> tuple[float, float]:
        k = self.k
        if t <= k[0][0]:
            return k[0][1], k[0][2]
        for (ta, ya, za), (tb, yb, zb) in zip(k, k[1:]):
            if ta <= t <= tb:
                p = ease_io((t - ta) / max(1e-6, tb - ta))
                return ya + (yb - ya) * p, za + (zb - za) * p
        return k[-1][1], k[-1][2]


def _cy_fuer(ziel_y: float, schirm_y: float, zoom: float) -> float:
    """Kamera-Mitte, damit ziel_y (Capture) auf schirm_y (Bildschirm) landet."""
    return ziel_y + (HOEHE / 2 - schirm_y) / zoom


def kamerafahrt(erf: Erfassung, dauer: float, fokusse: list[Fokus]) -> Kamera:
    if erf.titel:
        cy = _cy_fuer(erf.titel[1] + erf.titel[3] / 2, 560, 1.0)
    else:
        cy = HOEHE / 2
    z, t_akt = 1.0, 0.0
    keys = [(0.0, cy, z)]
    # Bei schneller Stimme (191 wpm) faellt die erste Zahl nach gut einer
    # halben Sekunde - die Anfahrt begann dann bei 0,1 s und das Bild wanderte,
    # bevor der Zuschauer die Ueberschrift ueberhaupt erfasst hatte.
    frueheste_abfahrt = HALTE_TITEL if erf.titel else 0.0
    for f in fokusse:
        ziel_z = ZOOM_ZAHL if f.art == "zahl" else ZOOM_PHRASE
        ziel_cy = _cy_fuer(f.box[1] + f.box[3] / 2, FOKUS_Y, ziel_z)
        fahrt = 0.45 if abs(ziel_cy - cy) < HOEHE else 0.75
        ankunft = max(t_akt + 0.3, f.zeit - 0.06, frueheste_abfahrt + 0.2)
        abfahrt = max(t_akt, ankunft - fahrt, frueheste_abfahrt)
        frueheste_abfahrt = 0.0        # gilt nur fuer den ersten Schwenk
        warte = abfahrt - t_akt
        # bis zur Abfahrt weiterlesen: langsam nach unten, der Zoom atmet
        warte_z = min(z + 0.02, ZOOM_ZAHL) if z < 1.01 else max(1.0, z - min(warte * 0.03, 0.06))
        keys.append((abfahrt, cy + min(warte * 30, 240), warte_z))
        keys.append((ankunft, ziel_cy, ziel_z))
        cy, z, t_akt = ziel_cy, ziel_z, ankunft
    rest = max(0.0, dauer - t_akt)
    keys.append((max(dauer, t_akt + 0.01), cy + min(rest * 28, 420), max(1.0, z - 0.06)))
    return Kamera(keys)


def _ausschnitt(erf: Erfassung, kam: Kamera, t: float):
    W, H = erf.bild.size
    cy, z = kam.an(t)
    vw = W / z
    vh = vw * HOEHE / BREITE
    x0 = (W - vw) / 2                          # immer mittig - kein Schwenk
    y0 = min(max(cy - vh / 2, 0.0), H - vh)
    # nur den benoetigten Streifen anfassen - der Capture ist bis zu 15000 px hoch
    kx, ky = int(x0), int(y0)
    streifen = erf.bild.crop((kx, ky, min(W, int(x0 + vw) + 2), min(H, int(y0 + vh) + 2)))
    bild = streifen.resize((BREITE, HOEHE), Image.BILINEAR,
                           box=(x0 - kx, y0 - ky, x0 - kx + vw, y0 - ky + vh))
    return bild, (x0, y0, BREITE / vw)


# ----------------------------------------------------------------- Zeichnen
@functools.lru_cache(maxsize=4)
def _kopf_ebene(domain: str, marke: str) -> Image.Image:
    """Browserleiste + Kanalmarke - einmal gezeichnet, pro Frame nur eingefuegt."""
    ebene = Image.new("RGBA", (BREITE, LEISTE + 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(ebene)
    d.rectangle([0, 0, BREITE, LEISTE], fill=(24, 26, 31, 255))
    d.rounded_rectangle([36, 44, BREITE - 36, 124], radius=40, fill=(44, 47, 54, 255))
    grau = (160, 166, 176, 255)
    d.rounded_rectangle([76, 84, 100, 106], radius=4, fill=grau)
    d.arc([79, 68, 97, 94], 180, 360, fill=grau, width=4)
    d.text((120, 64), domain, font=schrift(38), fill=(230, 233, 238, 255))
    fm = schrift(26)
    tb = d.textlength(marke.upper(), font=fm)
    d.rounded_rectangle([36, LEISTE + 16, 36 + tb + 44, LEISTE + 62], radius=12, fill=(0, 0, 0, 150))
    d.rectangle([50, LEISTE + 26, 54, LEISTE + 52], fill=AKZENT + (255,))
    d.text((66, LEISTE + 22), marke.upper(), font=fm, fill=(255, 255, 255, 220))
    return ebene


@functools.lru_cache(maxsize=1)
def _verlauf() -> Image.Image:
    """Abdunklung unten, damit die Untertitel auf jeder Seite lesbar bleiben."""
    h = 720
    alpha = Image.linear_gradient("L").resize((BREITE, h)).point(lambda v: int(v * 0.7))
    ebene = Image.new("RGBA", (BREITE, h), (8, 10, 14, 0))
    ebene.putalpha(alpha)
    return ebene


def _markiere(bild: Image.Image, f: Fokus, t: float, sicht) -> None:
    x0, y0, s = sicht
    bx, by, bw, bh = f.box
    sx, sy, sw, sh = (bx - x0) * s, (by - y0) * s, bw * s, bh * s
    if sy + sh < LEISTE or sy > HOEHE:
        return
    seit = t - f.zeit + 0.05
    if seit < 0:
        return

    # nur ein kleines Feld um die Stelle bemalen statt eines ganzen Frames
    rand = 130
    px0, py0 = int(sx - rand), int(sy - rand)
    feld = Image.new("RGBA", (int(sw + 2 * rand), int(sh + 2 * rand)), (0, 0, 0, 0))
    d = ImageDraw.Draw(feld)
    lx, ly = sx - px0, sy - py0
    farbe = ROT if f.negiert else AKZENT

    if f.art == "zahl":
        breite = (sw + 28) * ease(seit / 0.35)
        if breite > 24:
            d.rounded_rectangle([lx - 14, ly - 8, lx - 14 + breite, ly + sh + 8],
                                radius=10, fill=farbe + (72,))
            d.rectangle([lx - 14, ly + sh + 8, lx - 14 + breite, ly + sh + 16], fill=farbe + (255,))
        if f.negiert and seit > 0.35:
            q = ease((seit - 0.35) / 0.25)
            ym = ly + sh * 0.55
            d.line([(lx - 10, ym), (lx - 10 + (sw + 20) * q, ym)],
                   fill=ROT + (255,), width=max(6, int(sh * 0.12)))
        # Tipp-Welle: wirkt wie ein Finger, der auf die Stelle zeigt
        if seit < 0.6:
            r = 24 + 90 * ease(seit / 0.6)
            a = int(170 * (1 - seit / 0.6))
            mx, my = lx + sw / 2, ly + sh / 2
            d.ellipse([mx - r, my - r, mx + r, my + r], outline=(255, 255, 255, a), width=6)
    else:
        # Phrasen werden nur unterstrichen - die Kamera liest mit, sie schreit nicht
        breite = (sw + 8) * ease(seit / 0.5)
        if breite > 4:
            d.rectangle([lx - 4, ly + sh + 4, lx - 4 + breite, ly + sh + 11], fill=farbe + (255,))
    bild.paste(feld, (px0, py0), feld)


class Untertitel:
    """Untertitel-Kaesten, je Wortzustand einmal gerendert und wiederverwendet."""

    def __init__(self):
        self._anordnung = {}
        self._felder = {}

    def _ordne(self, schluessel, woerter):
        if schluessel not in self._anordnung:
            f = schrift(58)
            mess = ImageDraw.Draw(Image.new("L", (1, 1)))
            zeilen, akt, akt_b = [], [], 0.0
            for idx, w in enumerate(woerter):
                b = mess.textlength(w.text + " ", font=f)
                if akt and akt_b + b > 860:
                    zeilen.append(akt)
                    akt, akt_b = [], 0.0
                akt.append((idx, w.text, b))
                akt_b += b
            if akt:
                zeilen.append(akt)
            zh = int(f.size * 1.28)
            breite = int(max(sum(b for _, _, b in z) for z in zeilen) + 56)
            self._anordnung[schluessel] = (f, zeilen, zh, breite, len(zeilen) * zh + 44)
        return self._anordnung[schluessel]

    def zeichne(self, bild: Image.Image, schluessel, woerter, start: float, t: float) -> None:
        f, zeilen, zh, breite, hoehe = self._ordne(schluessel, woerter)
        stufe = min(5, max(0, int((t - start) / 0.036)))
        aktiv = next((i for i, w in enumerate(woerter) if w.start <= t <= w.ende + 0.05), -1)
        feld = self._felder.get((schluessel, aktiv, stufe))
        if feld is None:
            if len(self._felder) > 400:
                self._felder.clear()
            ein = ease(stufe / 5)
            feld = Image.new("RGBA", (breite, hoehe + 30), (0, 0, 0, 0))
            d = ImageDraw.Draw(feld)
            dy = (1 - ein) * 30
            d.rounded_rectangle([0, dy, breite - 1, dy + hoehe], radius=28,
                                fill=(10, 12, 16, int(225 * ein)))
            y = dy + 22
            for zeile in zeilen:
                x = (breite - sum(b for _, _, b in zeile)) / 2 + 6
                for idx, text, b in zeile:
                    farbe = AKZENT if idx == aktiv else WEISS
                    d.text((x, y), text, font=f, fill=farbe + (int(255 * ein),))
                    x += b
                y += zh
            self._felder[(schluessel, aktiv, stufe)] = feld
        bild.paste(feld, ((BREITE - breite) // 2, UNTERTITEL_Y), feld)


@functools.lru_cache(maxsize=8)
def _marken_ebene(marke: str, domain: str) -> Image.Image:
    """Kanalmarke + Quelle fuer Clip-Abschnitte - die Herkunft bleibt sichtbar."""
    ebene = Image.new("RGBA", (BREITE, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(ebene)
    fm = schrift(26)
    tb = d.textlength(marke.upper(), font=fm)
    d.rounded_rectangle([36, 76, 36 + tb + 44, 122], radius=12, fill=(0, 0, 0, 150))
    d.rectangle([50, 86, 54, 112], fill=AKZENT + (255,))
    d.text((66, 82), marke.upper(), font=fm, fill=(255, 255, 255, 220))
    if domain:
        text = "SOURCE  " + domain
        tq = d.textlength(text, font=fm)
        d.rounded_rectangle([36, 134, 36 + tq + 36, 180], radius=12, fill=(0, 0, 0, 150))
        d.text((54, 140), text, font=fm, fill=(255, 255, 255, 200))
    return ebene


# ------------------------------------------------------------------ Schnitt
@dataclass
class Abschnitt:
    art: str            # beleg = Quelle im Bild, broll = Stock-Clip
    start: float
    ende: float


def _halte_broll_budget(abschnitte: list[Abschnitt], dauer: float,
                        anteil_max: float = MAX_BROLL_ANTEIL) -> list[Abschnitt]:
    """Gibt der Quelle die Mehrheit der Laufzeit zurueck.

    Im ersten echten Video lag der Clip-Anteil bei 71 % - der Beweis, also das
    einzige, was den Kanal von einer TTS-ueber-Stock-Schablone unterscheidet,
    kam zu kurz. Gekuerzt wird der jeweils laengste Clip-Abschnitt von hinten,
    damit Bewegtbild erhalten bleibt statt ganz zu verschwinden.
    """
    out = [Abschnitt(a.art, a.start, a.ende) for a in abschnitte]
    for _ in range(len(out) + 1):
        broll_idx = [i for i, a in enumerate(out) if a.art == "broll"]
        ueber = sum(out[i].ende - out[i].start for i in broll_idx) - anteil_max * dauer
        if not broll_idx or ueber <= 1e-6:
            break
        i = max(broll_idx, key=lambda j: out[j].ende - out[j].start)
        a = out[i]
        rest = (a.ende - a.start) - ueber
        if rest >= MIN_BROLL:
            out[i] = Abschnitt("broll", a.start, a.start + rest)
            out.insert(i + 1, Abschnitt("beleg", a.start + rest, a.ende))
        else:
            out[i] = Abschnitt("beleg", a.start, a.ende)

    zusammen: list[Abschnitt] = []
    for a in out:
        if zusammen and zusammen[-1].art == a.art:
            zusammen[-1].ende = a.ende
        else:
            zusammen.append(Abschnitt(a.art, a.start, a.ende))
    return zusammen


def plane_schnitt(beats, fokusse: list[Fokus], dauer: float,
                  mit_beleg: bool, mit_broll: bool) -> list[Abschnitt]:
    """Legt fest, wann die Quelle und wann ein Clip im Bild ist.

    Fakten gehoeren auf die Quelle, Einordnung ("my take") auf Bewegtbild.
    Geschnitten wird nur an Beat-Anfaengen - dort, wo die Stimme ohnehin absetzt.
    """
    if not beats or not (mit_beleg and mit_broll):
        return [Abschnitt("beleg" if mit_beleg else "broll", 0.0, dauer)]

    arten = []
    for i, b in enumerate(beats):
        if i == 0 or any(b.start - 0.3 <= f.zeit <= b.ende for f in fokusse):
            arten.append("beleg")
        elif b.art == "verdict":
            arten.append("broll")
        else:
            arten.append("offen")

    # Strecken ohne Beleg: kurze bleiben auf der Seite (die Kamera liest
    # weiter), laengere bekommen Bewegtbild
    i = 0
    while i < len(arten):
        if arten[i] != "offen":
            i += 1
            continue
        j = i
        while j < len(arten) and arten[j] == "offen":
            j += 1
        fuellung = "beleg" if beats[j - 1].ende - beats[i].start < 2.5 else "broll"
        arten[i:j] = [fuellung] * (j - i)
        i = j

    abschnitte: list[Abschnitt] = []
    for i, (b, art) in enumerate(zip(beats, arten)):
        if abschnitte and abschnitte[-1].art == art:
            continue
        start = 0.0 if i == 0 else b.start
        if abschnitte:
            abschnitte[-1].ende = start
        abschnitte.append(Abschnitt(art, start, dauer))

    # Clip-Schnipsel unter 1,2 s wirken wie Flackern -> zurueck auf die Quelle
    for a in abschnitte:
        if a.art == "broll" and a.ende - a.start < 1.2:
            a.art = "beleg"
    zusammen: list[Abschnitt] = []
    for a in abschnitte:
        if zusammen and zusammen[-1].art == a.art:
            zusammen[-1].ende = a.ende
        else:
            zusammen.append(a)
    return _halte_broll_budget(zusammen, dauer)


def teile_broll(abschnitte: list[Abschnitt], beats, ziel: float = 2.0,
                min_rest: float = 1.2) -> list[Abschnitt]:
    """Teilt lange Clip-Strecken an Beat-Grenzen in mehrere Einstellungen.

    Ein 6-s-Clip am Stueck wirkte im ersten VPS-Test traege. Etwa alle 2-3 s
    ein neuer Clip haelt das Tempo - geschnitten wird trotzdem nur dort, wo
    die Stimme absetzt.
    """
    out: list[Abschnitt] = []
    for a in abschnitte:
        if a.art != "broll":
            out.append(a)
            continue
        start = a.start
        for b in beats:
            if b.start - start >= ziel and a.ende - b.start >= min_rest:
                out.append(Abschnitt("broll", start, b.start))
                start = b.start
        out.append(Abschnitt("broll", start, a.ende))
    return out


def _schnitt_bilder(erf: Erfassung | None, beats, fokusse: list[Fokus], abschnitte: list[Abschnitt],
                    clips: list[str], dauer: float, marke: str, ffmpeg: str, fps: int = FPS):
    """Erzeugt die Frames des ganzen Schnitts als PIL-Bilder (RGB) - direkt in ffmpeg pipebar."""
    import broll

    kam = kamerafahrt(erf, dauer, fokusse) if erf else None
    kopf = _kopf_ebene(erf.domain, marke) if erf else None
    marken = _marken_ebene(marke, erf.domain if erf else "")
    verlauf = _verlauf()
    untertitel = Untertitel()
    gesamt = int(dauer * fps) + fps // 2
    n = 0
    for nr, a in enumerate(abschnitte):
        bis = gesamt if nr == len(abschnitte) - 1 else round(a.ende * fps)
        clip = None
        if a.art == "broll" and clips:
            # jeder Clip-Abschnitt nimmt den naechsten Clip, bei Wiederholung spaeter im Material
            runde, idx = divmod(sum(1 for x in abschnitte[:nr] if x.art == "broll"), len(clips))
            clip = broll.bilder_aus_clip(clips[idx], 0.4 + 1.7 * runde, (bis - n) / fps, fps, ffmpeg)
        try:
            while n < bis:
                t = n / fps
                bild = next(clip, None) if clip is not None else None
                if bild is not None:
                    bild.paste(verlauf, (0, HOEHE - verlauf.height), verlauf)
                    bild.paste(marken, (0, 0), marken)
                elif erf is not None:
                    bild, sicht = _ausschnitt(erf, kam, t)
                    # nur die aktuelle Stelle markieren - die Kamera verlaesst die alte
                    for i, f in enumerate(fokusse):
                        naechste = fokusse[i + 1].zeit if i + 1 < len(fokusse) else 1e9
                        if f.zeit - 0.05 <= t < naechste - 0.5:
                            _markiere(bild, f, t, sicht)
                    bild.paste(verlauf, (0, HOEHE - verlauf.height), verlauf)
                    bild.paste(kopf, (0, 0), kopf)
                else:
                    bild = Image.new("RGB", (BREITE, HOEHE), (13, 17, 23))
                bi = next((i for i, b in enumerate(beats) if b.start <= t <= b.ende + 0.25), None)
                if bi is not None:
                    untertitel.zeichne(bild, bi, beats[bi].woerter, beats[bi].start, t)
                yield bild
                n += 1
        finally:
            if clip is not None:
                clip.close()


def bilder(erf: Erfassung, words, fokusse: list[Fokus], dauer: float,
           marke: str = "DEMO", fps: int = FPS):
    """Nur die Quelle, ohne Clips - fuer lokale Tests ohne Pexels-Key."""
    from motion3 import zerlege

    return _schnitt_bilder(erf, zerlege(words), fokusse, [Abschnitt("beleg", 0.0, dauer)],
                           [], dauer, marke, "ffmpeg", fps)


def render_video(words, audio: str, link: str, titel: str, ziel: str, arbeitsordner: str,
                 marke: str = "DEMO", skript: str = "", ffmpeg: str = "ffmpeg") -> dict | None:
    """Kompletter Beweis-Schnitt: Quelle + Stock-Clips + Untertitel.

    Liefert Kennzahlen fuers Log - oder None, wenn weder die Quelle noch
    B-Roll zu haben war. Dann entscheidet der Aufrufer ueber den Fallback.
    """
    import time

    import broll
    from motion3 import zerlege

    t0 = time.time()
    beats = zerlege(words)
    if not beats:
        return None
    dauer = beats[-1].ende + 0.35
    zahlen = zahlen_aus(words)
    phrasen = phrasen_aus(words)
    erf = erfasse(link, nadeln_fuer(zahlen, phrasen))
    fokusse = fokusse_fuer(erf, zahlen, phrasen) if erf else []
    t_capture = time.time() - t0

    text = skript or " ".join(b.satz for b in beats)
    # Sechs statt vier: bei 50 % Clip-Anteil und Schnitten alle ~2 s wiederholt
    # sich vier Mal zu frueh auch innerhalb eines Videos. suchbegriffe() liefert
    # jetzt sechs Anfragen, also gibt es auch sechs verschiedene Treffer.
    clips = broll.hole_clips(titel, text, os.path.join(arbeitsordner, "broll"), anzahl=6)
    if erf is None and not clips:
        return None
    abschnitte = teile_broll(plane_schnitt(beats, fokusse, dauer, erf is not None, bool(clips)), beats)
    log.info("Schnitt: %s", " | ".join("%s %.1f-%.1f" % (a.art, a.start, a.ende) for a in abschnitte))

    t1 = time.time()
    ok = kodiere(_schnitt_bilder(erf, beats, fokusse, abschnitte, clips, dauer, marke, ffmpeg),
                 audio, ziel, ffmpeg)
    if not ok:
        return None
    return {
        "quelle": erf.domain if erf else None,
        "fokusse": [(f.art, f.label, round(f.zeit, 2)) for f in fokusse],
        "abschnitte": [(a.art, round(a.start, 1), round(a.ende, 1)) for a in abschnitte],
        "broll_anteil": round(sum(a.ende - a.start for a in abschnitte if a.art == "broll") / dauer, 2),
        "capture_s": round(t_capture, 1),
        "render_s": round(time.time() - t1, 1),
        "mb": round(os.path.getsize(ziel) / 1e6, 2),
    }


def kodiere(frames, audio: str, ziel: str, ffmpeg: str = "ffmpeg", fps: int = FPS) -> bool:
    """Pipt Rohframes in ffmpeg - spart das Schreiben tausender PNGs."""
    cmd = [ffmpeg, "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (BREITE, HOEHE),
           "-r", str(fps), "-i", "-", "-i", audio, "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", ziel]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for bild in frames:
            proc.stdin.write(bild.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    fehler = proc.stderr.read().decode(errors="replace")
    proc.wait()
    if proc.returncode != 0:
        log.error("ffmpeg (beweis) fehlgeschlagen: %s", fehler[-600:])
        return False
    return os.path.exists(ziel) and os.path.getsize(ziel) > 10000


def main() -> None:
    import time

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--words", required=True, help="JSON: Liste oder {words: [...]}")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--marke", default="DEMO")
    args = ap.parse_args()

    daten = json.load(open(args.words, encoding="utf-8"))
    words = daten["words"] if isinstance(daten, dict) else daten
    dauer = float(words[-1]["end"]) + 0.3

    zahlen = zahlen_aus(words)
    phrasen = phrasen_aus(words)
    print("Zahlen:", [(z.label, round(z.zeit, 2), z.negiert) for z in zahlen])

    t0 = time.time()
    erf = erfasse(args.url, nadeln_fuer(zahlen, phrasen))
    if erf is None:
        raise SystemExit("Capture fehlgeschlagen")
    fokusse = fokusse_fuer(erf, zahlen, phrasen)
    print("Capture %.1fs | Fokusse: %s" % (
        time.time() - t0, [(f.art, f.label, round(f.zeit, 2)) for f in fokusse]))

    t1 = time.time()
    ok = kodiere(bilder(erf, words, fokusse, dauer, args.marke), args.audio, args.out, args.ffmpeg)
    print("Render %.1fs | ok=%s -> %s" % (time.time() - t1, ok, args.out))


if __name__ == "__main__":
    main()
