#!/usr/bin/env python3
"""B-Roll aus echtem Videomaterial (Pexels Video API).

Warum: Standbilder mit Ken-Burns wirken alt, und ein Overlay allein bleibt
statisch. Bewegtes Material im Hintergrund ist der Unterschied zwischen
"Slideshow" und "Video".

Die Clips werden bewusst stark abgedunkelt und leicht unscharf gelegt - sie
sind Kulisse, nicht Hauptdarsteller. Das haelt den Text lesbar und vermeidet,
dass generisches Stockmaterial die Aufmerksamkeit zieht.

Kostenlos im Rahmen der Pexels-Limits, derselbe API-Key wie fuer die Fotos.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import random
import re
import subprocess
import threading
import urllib.parse
from collections.abc import Iterable

import httpx

log = logging.getLogger("broll")

PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
# Sperrliste schon verwendeter Clips. Liegt bewusst ausserhalb des Images,
# damit sie Container-Neustarts ueberlebt - sonst zieht jedes Video wieder
# dieselben Treffer.
LEDGER = os.environ.get("BROLL_LEDGER", "/tmp/shortform_broll_ledger.json")
LEDGER_MAX = 240                     # so viele IDs bleiben gesperrt
TREFFER_PRO_SEITE = 30               # Pexels erlaubt bis 80; 5 war viel zu eng
TIMEOUT = 20.0
BREITE, HOEHE = 1080, 1920

# Themenwortschatz -> Suchbegriffe, die tatsaechlich brauchbares Portrait-
# Material liefern. Bewusst abstrakt/technisch: konkrete Produktaufnahmen
# gibt es als Stockvideo ohnehin nicht.
THEMEN = [
    (("price", "pricing", "cost", "bill", "budget", "cheap", "dollar", "token"),
     ["finance data screen", "abstract numbers motion", "server rack lights"]),
    (("model", "llm", "ai", "inference", "neural", "gpt", "llama"),
     ["abstract technology network", "data center servers", "circuit board macro"]),
    (("code", "api", "sdk", "developer", "programming", "terminal", "open source"),
     ["code screen programmer", "typing keyboard closeup", "software developer desk"]),
    (("gpu", "chip", "hardware", "compute", "cluster", "local"),
     ["computer hardware macro", "server room blue", "circuit board macro"]),
    (("speed", "fast", "latency", "performance", "benchmark"),
     ["fast motion light streaks", "abstract speed lines", "network data flow"]),
]
STANDARD = ["abstract technology network", "data center servers", "code screen programmer"]


# Fuellwoerter, die als Suchbegriff nichts beitragen.
STOPP_WOERTER = {
    "just", "this", "that", "with", "from", "have", "been", "will", "your",
    "they", "them", "than", "then", "what", "when", "which", "while", "more",
    "most", "some", "only", "also", "into", "over", "under", "after", "before",
    "about", "would", "could", "should", "their", "there", "these", "those",
    "make", "makes", "made", "here", "does", "doing", "each", "every", "other",
    "same", "still", "even", "much", "many", "very", "like", "want", "need",
}
# Alle Trigger der Themen-Tabelle - die haben die Basis schon bestimmt und
# taugen darum nicht mehr als unterscheidendes Merkmal der Story.
_TRIGGER = {k for schluessel, _ in THEMEN for k in schluessel}


def _story_begriffe(text: str, limit: int = 3) -> list[str]:
    """Zieht die auffaelligen Woerter der konkreten Story heraus.

    Die Themen-Tabelle beschreibt nur die Schublade ("Preis", "Modell"). Ohne
    einen Bezug zur einzelnen Story liefert Pexels bei jedem Preis-Thema
    dieselben Treffer - und kanalweit gleiches Stockmaterial ist genau das
    Muster, das YouTube als "inauthentic content" abstraft.
    """
    out: list[str] = []
    for wort in re.findall(r"[a-z][a-z0-9]{3,}", text):
        if wort in STOPP_WOERTER or wort in _TRIGGER or wort in out:
            continue
        out.append(wort)
        if len(out) >= limit:
            break
    return out


def suchbegriffe(titel: str, script: str = "") -> list[str]:
    """Baut die Pexels-Anfragen: Thema als Basis, Story als Einfaerbung."""
    text = (titel + " " + script).lower()
    basis: list[str] = []
    for schluessel, begriffe in THEMEN:
        if any(k in text for k in schluessel):
            basis.extend(begriffe)
    if not basis:
        basis = list(STANDARD)

    eigen = _story_begriffe(text)
    gesehen: set[str] = set()
    out: list[str] = []
    for i, b in enumerate(basis):
        # Erst die eingefaerbte Variante, dann der abstrakte Begriff als
        # Rueckfall - falls Pexels zur Story-Variante nichts findet.
        kandidaten = []
        if i < len(eigen):
            kandidaten.append("%s %s" % (eigen[i], b.split()[0]))
        kandidaten.append(b)
        for k in kandidaten:
            if k not in gesehen:
                gesehen.add(k)
                out.append(k)
    return out[:6]


def _zu_int(wert) -> int | None:
    try:
        return int(wert)
    except (TypeError, ValueError):
        return None


def _lies_ids(ziel: str) -> list[int]:
    """Liest die Sperrliste der Reihe nach. Aelteste zuerst - so trifft der
    spaetere Zuschnitt auf LEDGER_MAX immer die aeltesten Eintraege."""
    try:
        with open(ziel, encoding="utf-8") as fh:
            daten = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        # Stumm zurueckfallen hiesse: die Sperrliste ist weg und niemand merkt
        # es - genau das Feature waere dann wirkungslos.
        log.warning("Clip-Sperrliste %s unlesbar (%s) - beginne neu", ziel, exc)
        return []
    roh = daten.get("ids", []) if isinstance(daten, dict) else []
    return [i for i in (_zu_int(x) for x in roh) if i is not None]


def lade_gesperrte(pfad: str = "") -> set[int]:
    """Pexels-IDs, die im Kanal zuletzt schon zu sehen waren."""
    return set(_lies_ids(pfad or LEDGER))


def merke_clips(ids: Iterable[int], pfad: str = "") -> None:
    """Schreibt verwendete IDs fort. Ein Fehler hier darf kein Video kosten.

    Lesen, Ergaenzen und Schreiben passieren unter einer Dateisperre: der
    Renderer laeuft als sync FastAPI-Route im Thread-Pool, parallele Renders
    (n8n-Retry, Doppel-Trigger) wuerden einander sonst ueberschreiben. Der
    Austausch ist atomar, damit ein Abbruch keine halbe Datei hinterlaesst.
    """
    neu = [i for i in (_zu_int(x) for x in ids) if i is not None]
    if not neu:
        return
    ziel = pfad or LEDGER
    try:
        ordner = os.path.dirname(ziel)
        if ordner:
            os.makedirs(ordner, exist_ok=True)
        with open(ziel + ".lock", "a+") as sperre:
            fcntl.flock(sperre, fcntl.LOCK_EX)
            try:
                alt = _lies_ids(ziel)
                for i in neu:
                    if i not in alt:
                        alt.append(i)
                # Prozess UND Thread im Namen: die Sperre serialisiert zwar,
                # aber ein kollidierender Temp-Name waere genau dann fatal,
                # wenn die Sperre einmal nicht greift (z.B. auf NFS).
                tmp = "%s.%d.%d.tmp" % (ziel, os.getpid(), threading.get_ident())
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"ids": alt[-LEDGER_MAX:]}, fh)
                os.replace(tmp, ziel)
            finally:
                fcntl.flock(sperre, fcntl.LOCK_UN)
    except OSError as exc:
        log.warning("Clip-Sperrliste %s nicht schreibbar: %s", ziel, exc)


def hole_clips(titel: str, script: str, ziel_dir: str, anzahl: int = 4,
               gesperrt: set[int] | None = None, ledger: str = "") -> list[str]:
    """Laedt bis zu `anzahl` Portrait-Clips herunter. Gibt Dateipfade zurueck.

    `gesperrt` haelt Pexels-IDs zurueck, die im Kanal schon liefen. Ohne diese
    Sperre zieht jedes Video zum selben Thema dasselbe Material.
    """
    if not PEXELS_API_KEY:
        log.warning("PEXELS_API_KEY fehlt - kein B-Roll moeglich")
        return []

    os.makedirs(ziel_dir, exist_ok=True)
    # Kopie: hole_clips ergaenzt die Sperre waehrend des Laufs, soll aber das
    # Set des Aufrufers nicht hinter dessen Ruecken veraendern.
    gesperrt = set(gesperrt) if gesperrt is not None else lade_gesperrte(ledger)
    genutzt: list[int] = []
    pfade: list[str] = []
    headers = {"Authorization": PEXELS_API_KEY, "User-Agent": "ShortFormPipeline/1.0"}

    with httpx.Client(timeout=TIMEOUT, headers=headers, follow_redirects=True) as client:
        for begriff in suchbegriffe(titel, script):
            if len(pfade) >= anzahl:
                break
            url = ("https://api.pexels.com/videos/search?query=%s&orientation=portrait"
                   "&size=medium&per_page=%d" % (urllib.parse.quote(begriff),
                                                 TREFFER_PRO_SEITE))
            try:
                r = client.get(url)
                r.raise_for_status()
                videos = r.json().get("videos", [])
            except Exception as exc:
                log.warning("Pexels-Video-Suche '%s' fehlgeschlagen: %s", begriff, exc)
                continue

            # Schon gezeigtes Material faellt raus, der Rest wird gemischt -
            # so ist die Auswahl breit UND ueber Videos hinweg verschieden.
            videos = [v for v in videos if _zu_int(v.get("id")) not in gesperrt]
            random.shuffle(videos)
            for v in videos:
                if len(pfade) >= anzahl:
                    break
                # kleinste Datei ab 1000px Hoehe: reicht fuer eine abgedunkelte
                # Kulisse und spart Download-Zeit auf dem VPS
                dateien = [f for f in v.get("video_files", [])
                           if (f.get("height") or 0) >= 1000 and f.get("link")]
                if not dateien:
                    continue
                datei = sorted(dateien, key=lambda f: f.get("height", 0))[0]
                p = os.path.join(ziel_dir, "clip_%d.mp4" % len(pfade))
                try:
                    with client.stream("GET", datei["link"]) as resp:
                        resp.raise_for_status()
                        with open(p, "wb") as fh:
                            for chunk in resp.iter_bytes(65536):
                                fh.write(chunk)
                except Exception as exc:
                    log.warning("Clip-Download fehlgeschlagen: %s", exc)
                    continue
                if os.path.getsize(p) > 40000:
                    pfade.append(p)
                    kennung = _zu_int(v.get("id"))
                    if kennung is not None:
                        genutzt.append(kennung)
                        gesperrt.add(kennung)
                    log.info("B-Roll: %s (id %s, %.1f MB)", begriff,
                             v.get("id", "?"), os.path.getsize(p) / 1e6)

    if genutzt:
        merke_clips(genutzt, ledger)
    return pfade


def baue_hintergrund(clips: list[str], dauer: float, ziel: str,
                     segment: float = 3.2) -> bool:
    """Schneidet die Clips zu einem durchgehenden Hintergrund der Laenge `dauer`.

    Jeder Clip liefert ein Segment, danach wird zyklisch weitergemacht. Das
    Material wird abgedunkelt und leicht weichgezeichnet - es ist Kulisse.
    """
    if not clips:
        return False

    n_seg = max(1, int(dauer / segment) + 1)
    eingaben: list[str] = []
    filter_teile: list[str] = []
    for i in range(n_seg):
        quelle = clips[i % len(clips)]
        # verschiedene Startpunkte, damit derselbe Clip nicht identisch wirkt
        start = 0.4 + (i // len(clips)) * 1.7
        eingaben += ["-ss", "%.2f" % start, "-t", "%.2f" % (segment + 0.4), "-i", quelle]
        filter_teile.append(
            "[%d:v]scale=%d:%d:force_original_aspect_ratio=increase,"
            "crop=%d:%d,setsar=1,fps=30,"
            "eq=brightness=-0.22:contrast=1.05:saturation=0.65,"
            "gblur=sigma=6[v%d];" % (i, BREITE, HOEHE, BREITE, HOEHE, i)
        )

    ketten = "".join("[v%d]" % i for i in range(n_seg))
    filter_complex = ("".join(filter_teile) + ketten
                      + "concat=n=%d:v=1:a=0,trim=duration=%.2f[out]" % (n_seg, dauer + 0.5))

    cmd = (["ffmpeg", "-y", "-loglevel", "error"] + eingaben
           + ["-filter_complex", filter_complex, "-map", "[out]",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
              "-pix_fmt", "yuv420p", ziel])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        log.error("B-Roll-Bau: Timeout")
        return False
    if r.returncode != 0:
        log.error("B-Roll-Bau fehlgeschlagen: %s", r.stderr[-600:])
        return False
    return os.path.exists(ziel) and os.path.getsize(ziel) > 10000


def bilder_aus_clip(pfad: str, start: float, dauer: float, fps: int = 30, ffmpeg: str = "ffmpeg"):
    """Liefert dauer*fps Frames eines Clips als PIL-Bilder (RGB, 1080x1920).

    Anders als baue_hintergrund() bleibt das Material scharf und hell: im
    Beweis-Schnitt ist der Clip fuer ein paar Sekunden das eigentliche Bild,
    keine Kulisse. Ist der Clip zu kurz, steht das letzte Bild.
    """
    from PIL import Image

    anzahl = max(1, round(dauer * fps))
    vf = ("scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1,fps=%d,"
          "eq=contrast=1.06:saturation=0.9:brightness=-0.03" % (BREITE, HOEHE, BREITE, HOEHE, fps))
    cmd = [ffmpeg, "-v", "error", "-ss", "%.2f" % start, "-i", pfad, "-t", "%.3f" % (dauer + 0.5),
           "-vf", vf, "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    groesse = BREITE * HOEHE * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    letztes = None
    try:
        for _ in range(anzahl):
            roh = proc.stdout.read(groesse)
            if len(roh) == groesse:
                letztes = Image.frombytes("RGB", (BREITE, HOEHE), roh)
            if letztes is None:
                return
            # immer eine Kopie: der Aufrufer zeichnet Untertitel direkt ins Bild
            yield letztes.copy()
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()
