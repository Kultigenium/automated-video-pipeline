import base64
import logging
import math
import subprocess
import tempfile
import os
import random
import re
import traceback
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("video-renderer")

app = FastAPI(title="ShortFormPipeline Video Renderer", version="0.6.0")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.error("Unhandled exception at %s: %s", request.url.path, exc)
    log.error(traceback.format_exc())
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
WIDTH = 1080
HEIGHT = 1920
MAX_TITLE_CHARS = 90

# Dynamische Slideshow
SEGMENT_LEN = 2.5          # Sekunden pro Bild (harter Cut danach)
ZOOM_MAX = 1.25            # Ken-Burns Push-In Ziel
TARGET_IMAGE_COUNT = 6     # so viele Hintergrund-Bilder anstreben

# Captions
WHISPER_MODEL_SIZE = "base"
WORDS_PER_CUE = 2
CAPTION_FONTSIZE = 110           # Basis-Schriftgroesse (passt zur Style-Zeile im ASS_HEADER)
CAPTION_MARGIN = 120             # L/R-Rand in px (passt zur Style-Zeile)
CAPTION_GLYPH_FACTOR = 0.68      # grobe Glyph-Breite DejaVu Sans Bold (uppercase) je fontsize-Einheit

# Bild-Akquise
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 8.0
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB Hard-Limit


class RenderRequest(BaseModel):
    audio_base64: str = Field(..., description="MP3 audio as base64 string")
    title: str = Field(..., description="Title shown at top of video")
    link: str = Field("", description="Article URL — for OG-Image fetch")
    background_color: str = Field("0x0E1116", description="Solid background color (hex with 0x prefix) — Fallback if no image")
    text_color: str = Field("white")
    watermark: str = Field("DEMO")
    captions: bool = Field(True, description="Kinetic Word-Captions einbrennen")
    script: str = Field("", description="Exakter gesprochener Text (Groq) — als Whisper-Vorlage gegen Falsch-Transkription (z.B. Code->Kot)")


def escape_for_ffmpeg_text(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "’")
    text = text.replace("%", "\\%")
    return text


def wrap_title(title: str, width_chars: int = 28) -> str:
    if len(title) > MAX_TITLE_CHARS:
        title = title[: MAX_TITLE_CHARS - 1].rstrip() + "..."
    words = title.split()
    lines = []
    current = ""
    for word in words:
        if not current:
            current = word
            continue
        if len(current) + 1 + len(word) <= width_chars:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)


def fetch_og_image(article_url: str) -> bytes | None:
    """Versucht og:image / twitter:image vom Artikel zu ziehen."""
    if not article_url:
        return None
    try:
        headers = {"User-Agent": USER_AGENT}
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True, headers=headers) as client:
            r = client.get(article_url)
            if r.status_code != 200 or not r.text:
                log.info("OG-fetch: HTTP %s for %s", r.status_code, article_url)
                return None
            soup = BeautifulSoup(r.text, "html.parser")
            candidates = []
            for prop in ("og:image", "og:image:secure_url", "twitter:image", "twitter:image:src"):
                tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
                if tag and tag.get("content"):
                    candidates.append(tag["content"])
            if not candidates:
                log.info("OG-fetch: no og:image tag in %s", article_url)
                return None
            img_url = candidates[0]
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            elif img_url.startswith("/"):
                img_url = urljoin(article_url, img_url)
            img_r = client.get(img_url)
            if img_r.status_code != 200:
                log.info("OG-fetch: image HTTP %s", img_r.status_code)
                return None
            data = img_r.content
            if len(data) > MAX_IMAGE_BYTES or len(data) < 1024:
                log.info("OG-fetch: image size out of bounds %d", len(data))
                return None
            log.info("OG-fetch: ok (%d bytes)", len(data))
            return data
    except Exception as exc:
        log.info("OG-fetch failed: %s", exc)
        return None


# Pexels Photo API (Bildquelle seit 2026-06-09, da Pollinations 402/paid)
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
PEXELS_TIMEOUT = 12.0
QUERY_STOPWORDS = {
    "der", "die", "das", "und", "oder", "von", "mit", "fuer", "für", "ist", "hat",
    "ein", "eine", "einen", "dem", "den", "des", "auf", "bei", "nach", "aus", "als",
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "has", "neue",
    "neuer", "neues", "alle", "mehr", "schon", "wird", "werden", "ihre", "sein",
}


# Bild-Query: deutsche Titel -> thematische ENGLISCHE Pexels-Query. Pexels ist eine englische
# Stock-DB; transliterierte deutsche Keywords liefern schwache/leere Treffer (#4).
# Generisch-aber-thematisch-passend schlaegt spezifisch-aber-leer.
IMAGE_THEME_MAP = (
    (("roboter", "robot", "humanoid"), "humanoid robot futuristic"),
    (("chip", "gpu", "nvidia", "prozessor", "halbleiter", "rechenzentrum", "server", "cloud"), "server data center technology"),
    (("code", "coding", "programmier", "entwickl", "software", "developer", "github", "copilot"), "programmer coding on screen"),
    (("bild", "foto", "kunst", "midjourney", "dall", "diffusion", "design", "grafik"), "digital art creative technology"),
    (("video", "film", "kino", "sora", "animation"), "filmmaking camera technology"),
    (("auto", "fahrzeug", "selbstfahr", "tesla", "drohne", "drone"), "autonomous car technology"),
    (("gesundheit", "medizin", "health", "klinik", "patient", "bio"), "medical technology laboratory"),
    (("sprach", "chatbot", "chatgpt", "assistent", "stimme", "übersetz"), "person using ai chatbot phone"),
    (("sicherheit", "security", "hack", "cyber", "datenschutz", "betrug", "deepfake"), "cybersecurity dark digital"),
    (("geld", "wirtschaft", "milliard", "invest", "markt", "aktie", "startup", "umsatz"), "business finance technology"),
    (("gehirn", "neuro", "agi", "bewusstsein", "superintel"), "glowing neural network brain"),
)
DEFAULT_IMAGE_QUERY = "artificial intelligence futuristic technology"


def build_image_query(title: str) -> str:
    """Deutscher Titel -> thematische englische Pexels-Query (#4: relevantere Treffer als
    transliterierte deutsche Keywords). Faellt auf eine generische KI-Tech-Query zurueck."""
    t = (title or "").lower()
    for keys, query in IMAGE_THEME_MAP:
        if any(k in t for k in keys):
            return query
    return DEFAULT_IMAGE_QUERY


def fetch_pexels_multi(title: str, count: int) -> list[bytes]:
    """Holt bis zu `count` verschiedene Hochformat-Fotos von Pexels."""
    if not PEXELS_API_KEY or count <= 0:
        if not PEXELS_API_KEY:
            log.info("Pexels: kein API-Key konfiguriert")
        return []
    query = build_image_query(title)
    headers = {"Authorization": PEXELS_API_KEY, "User-Agent": USER_AGENT}
    params = {"query": query, "orientation": "portrait", "per_page": 30, "size": "large"}
    out: list[bytes] = []
    try:
        with httpx.Client(timeout=PEXELS_TIMEOUT, headers=headers) as client:
            r = client.get("https://api.pexels.com/v1/search", params=params)
            if r.status_code != 200:
                log.info("Pexels: search HTTP %s for query '%s'", r.status_code, query)
                return []
            photos = r.json().get("photos", [])
            random.shuffle(photos)
            for photo in photos:
                if len(out) >= count:
                    break
                src = photo.get("src", {})
                img_url = src.get("portrait") or src.get("large2x") or src.get("large") or src.get("original")
                if not img_url:
                    continue
                try:
                    ir = client.get(img_url)
                    if ir.status_code != 200:
                        continue
                    data = ir.content
                    if len(data) < 2048 or len(data) > MAX_IMAGE_BYTES:
                        continue
                    out.append(data)
                except Exception:
                    continue
            log.info("Pexels: %d/%d Fotos fuer query '%s'", len(out), count, query)
            return out
    except Exception as exc:
        log.info("Pexels-multi failed: %s", exc)
        return out


def gather_background_images(title: str, link: str, target_count: int = TARGET_IMAGE_COUNT) -> list[bytes]:
    """OG-Image (falls vorhanden) + mehrere Pexels-Fotos fuer die Slideshow."""
    images: list[bytes] = []
    og = fetch_og_image(link)
    if og:
        images.append(og)
    need = target_count - len(images)
    if need > 0:
        images.extend(fetch_pexels_multi(title, need))
    return images


def build_background_filter(image_paths: list[str], duration: float):
    """Baut ffmpeg-Inputs + Filter fuer dynamische Ken-Burns-Slideshow.

    Audio ist Input 0, Bilder sind Inputs 1..N. Liefert (input_args, bg_filter, n_seg).
    bg_filter endet mit [bg]; (Hintergrund-Video).
    """
    multi = len(image_paths) >= 2
    if multi:
        seg_len = SEGMENT_LEN
        n_seg = max(2, math.ceil(duration / seg_len))
    else:
        seg_len = max(duration, 1.0)
        n_seg = 1
    seg_frames = max(int(seg_len * 25), 25)
    step = (ZOOM_MAX - 1.0) / seg_frames

    # Ankerpunkte fuer Abwechslung (zoom-in, aber Push Richtung wechselt)
    anchors = [
        ("iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),  # center
        ("iw/2-(iw/zoom/2)", "0"),                  # top
        ("iw/2-(iw/zoom/2)", "ih-ih/zoom"),         # bottom
        ("0", "ih/2-(ih/zoom/2)"),                  # left
        ("iw-iw/zoom", "ih/2-(ih/zoom/2)"),         # right
    ]

    input_args: list[str] = []
    seg_filters: list[str] = []
    for i in range(n_seg):
        img = image_paths[i % len(image_paths)]
        input_args += ["-loop", "1", "-i", img]
        in_idx = i + 1  # audio belegt Index 0
        ax, ay = anchors[i % len(anchors)]
        seg_filters.append(
            f"[{in_idx}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},setsar=1,"
            f"zoompan=z='min(zoom+{step:.6f},{ZOOM_MAX})':d={seg_frames}:"
            f"x='{ax}':y='{ay}':s={WIDTH}x{HEIGHT}:fps=25,"
            f"trim=duration={seg_len},setpts=PTS-STARTPTS,format=yuv420p[seg{i}]"
        )
    concat_in = "".join(f"[seg{i}]" for i in range(n_seg))
    bg_filter = ";".join(seg_filters) + f";{concat_in}concat=n={n_seg}:v=1:a=0[bg];"
    log.info("Slideshow: %d Segmente aus %d Bildern (%.1fs/Segment)", n_seg, len(image_paths), seg_len)
    return input_args, bg_filter, n_seg


# ---- Captions (faster-whisper -> ASS) ----
_WHISPER = None


def get_whisper():
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel
        _WHISPER = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    return _WHISPER


def transcribe_words(audio_path: str, script: str = ""):
    """Liefert Liste (start, end, wort) mit Millisekunden-Timing via faster-whisper.
    script: exakter gesprochener Text (Groq) als initial_prompt -> biast Whisper auf die
    richtigen Woerter (verhindert z.B. 'Code'->'Kot'). Leer = altes Verhalten (Auto-Erkennung)."""
    try:
        model = get_whisper()
        segments, _info = model.transcribe(
            audio_path, language="de", word_timestamps=True, beam_size=1,
            initial_prompt=(script.strip() or None),
        )
        words = []
        for seg in segments:
            for w in (seg.words or []):
                t = (w.word or "").strip()
                if t:
                    words.append((float(w.start), float(w.end), t))
        log.info("Whisper: %d Woerter transkribiert", len(words))
        return words
    except Exception as exc:
        log.error("Whisper transcribe failed: %s", exc)
        return []


def _ass_time(t: float) -> str:
    if t < 0:
        t = 0.0
    cs = int(round(t * 100))
    h = cs // 360000
    cs -= h * 360000
    m = cs // 6000
    cs -= m * 6000
    s = cs // 100
    cs -= s * 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


ASS_HEADER = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "PlayResX: 1080\n"
    "PlayResY: 1920\n"
    "ScaledBorderAndShadow: yes\n"
    "WrapStyle: 0\n\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
    "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
    "Alignment, MarginL, MarginR, MarginV, Encoding\n"
    "Style: Kinetic,DejaVu Sans,110,&H00FFFFFF,&H000000FF,&H00101010,&H96000000,"
    "-1,0,0,0,100,100,0,0,1,8,2,5,120,120,0,1\n\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)


def _fit_caption_fontsize(txt: str, base: int = CAPTION_FONTSIZE) -> int:
    """Schrumpft die Schrift nur so weit, dass das laengste (unzerbrechbare) Wort in die
    nutzbare Breite passt — verhindert, dass lange Woerter ueber den Rand hinausragen.
    Kurze Woerter bleiben gross/knackig (gibt dann unveraendert die Basis-Groesse zurueck)."""
    usable = (WIDTH - 2 * CAPTION_MARGIN) * 0.93
    longest = max((len(w) for w in txt.split()), default=len(txt)) or 1
    max_fs = usable / (longest * CAPTION_GLYPH_FACTOR)
    return max(40, min(base, int(max_fs)))


def _clean_caption_word(w: str) -> str:
    r"""Entfernt Satzzeichen (Punkt/Komma/Anfuehrungszeichen…) — virale Captions zeigen keine.
    \w (Unicode) behaelt Buchstaben inkl. Umlaute + Ziffern, alles andere faellt weg."""
    return re.sub(r"[^\w\s]", "", w, flags=re.UNICODE).strip()


def generate_ass(words, total_duration: float, words_per_cue: int = WORDS_PER_CUE):
    """Erzeugt ASS mit Wort-Cues + Pop-Animation. None wenn keine Woerter.
    - Satzzeichen werden entfernt (#9 — virale Captions zeigen keine).
    - EINE einheitliche Schriftgroesse fuers ganze Video, am laengsten Wort ausgerichtet (#7),
      damit die Groesse nicht von Cue zu Cue springt (ruhigerer, fluessiger Look)."""
    if not words:
        return None
    cues = []
    i = 0
    while i < len(words):
        group = words[i:i + words_per_cue]
        text = " ".join(_clean_caption_word(w[2]) for w in group).strip()
        if text:
            cues.append([group[0][0], group[-1][1], text])
        i += words_per_cue
    if not cues:
        return None
    # Luecken schliessen: jede Cue laeuft bis zur naechsten
    for idx in range(len(cues)):
        if idx + 1 < len(cues):
            cues[idx][1] = max(cues[idx][1], cues[idx + 1][0])
        else:
            cues[idx][1] = max(cues[idx][1], min(cues[idx][0] + 1.2, total_duration))

    # Einheitliche Schriftgroesse: am laengsten Wort im ganzen Video ausrichten -> kein Springen
    all_words = [w for c in cues for w in c[2].split()]
    longest_word = max(all_words, key=len, default="")
    video_fs = _fit_caption_fontsize(longest_word) if longest_word else CAPTION_FONTSIZE
    eff = "{\\fad(50,0)\\fs%d\\fscx72\\fscy72\\t(0,110,\\fscx100\\fscy100)}" % video_fs

    lines = [ASS_HEADER]
    for start, end, text in cues:
        if end <= start:
            end = start + 0.2
        txt = _ass_escape(text).upper()
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Kinetic,,0,0,0,,{eff}{txt}\n"
        )
    return "".join(lines)


def probe_audio_duration(audio_path: str) -> float:
    """Audio-Laenge via ffprobe in Sekunden. Fallback: 30s."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ],
            capture_output=True, text=True, timeout=10,
        )
        return max(float(out.stdout.strip()), 1.0)
    except Exception:
        return 30.0


@app.get("/health")
def health():
    return {"status": "ok", "service": "video-renderer", "version": "0.6.0", "ffmpeg": _ffmpeg_version()}


def _ffmpeg_version() -> str:
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        return out.stdout.splitlines()[0] if out.stdout else "unknown"
    except Exception:
        return "unavailable"


def _title_watermark_filter(in_label: str, title_escaped: str, watermark_escaped: str, text_color: str) -> str:
    """drawtext-Kette: nur Watermark oben (aus YT-Shorts-Bottom-Sperrzone gezogen 2026-06-25, war y=h-150). Statischer Titel-Overlay entfernt 2026-06-21
    (Nutzer-Wunsch: kein dauerhaft eingebrannter Titel; die Kinetic-Captions tragen den Inhalt).
    title_escaped/text_color bleiben in der Signatur (Aufrufer unveraendert), werden aber nicht mehr genutzt."""
    return (
        f"[{in_label}]drawtext=fontfile={FONT_PATH}:text='{watermark_escaped}':"
        f"fontcolor=white@0.55:fontsize=40:x=(w-text_w)/2:y=160:"
        f"box=1:boxcolor=black@0.4:boxborderw=10[outv]"
    )


# Globales Color-Grading + Vignette + Film-Grain (Marken-Look, kaschiert heterogenen Stock-Mix)
COLOR_GRADE = (
    "eq=contrast=1.12:saturation=0.92:brightness=-0.01,"
    "colorbalance=rs=0.02:gs=-0.005:bs=-0.03:bm=-0.02,"
    "vignette=PI/5,"
    "noise=alls=7:allf=t+u"
)


ENCODE_ARGS = [
    "-c:v", "libx264",
    "-pix_fmt", "yuv420p",
    "-preset", "veryfast",
    "-crf", "23",
    "-c:a", "aac",
    "-b:a", "128k",
    "-shortest",
    "-movflags", "+faststart",
]


@app.post("/render")
def render(req: RenderRequest):
    log.info(
        "Render request: title_len=%d, audio_b64_len=%d, link=%s, captions=%s",
        len(req.title or ""), len(req.audio_base64 or ""), req.link or "(none)", req.captions,
    )
    try:
        audio_bytes = base64.b64decode(req.audio_base64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"audio_base64 not decodable: {exc}")

    if not audio_bytes:
        raise HTTPException(status_code=400, detail="audio_base64 empty")
    log.info("Audio decoded: %d bytes", len(audio_bytes))

    title_wrapped = wrap_title(req.title)
    title_escaped = escape_for_ffmpeg_text(title_wrapped)
    watermark_escaped = escape_for_ffmpeg_text(req.watermark)

    tmpdir = tempfile.mkdtemp(prefix="video_render_")
    audio_path = os.path.join(tmpdir, "input.mp3")
    video_path = os.path.join(tmpdir, "out.mp4")

    with open(audio_path, "wb") as fh:
        fh.write(audio_bytes)

    duration = probe_audio_duration(audio_path)
    log.info("Audio duration ~%.2fs", duration)

    # Captions: Stimme transkribieren -> ASS
    ass_path = None
    if req.captions:
        words = transcribe_words(audio_path, req.script)
        ass_content = generate_ass(words, duration)
        if ass_content:
            ass_path = os.path.join(tmpdir, "subs.ass")
            with open(ass_path, "w", encoding="utf-8") as fh:
                fh.write(ass_content)
            log.info("Captions: ASS mit %d Cues", ass_content.count("Dialogue:"))

    # Hintergrund-Bilder sammeln: OG (Artikel) + mehrere Pexels-Fotos
    images = gather_background_images(req.title, req.link)
    image_paths: list[str] = []
    for idx, img in enumerate(images):
        p = os.path.join(tmpdir, f"bg_{idx}.img")
        with open(p, "wb") as fh:
            fh.write(img)
        image_paths.append(p)
    log.info("Background: %d Bilder gesammelt", len(image_paths))

    if image_paths:
        input_args, bg_part, n_seg = build_background_filter(image_paths, duration)
        bg_mode = f"slideshow({n_seg}seg/{len(image_paths)}img)"
    else:
        input_args = []
        bg_part = f"color=c={req.background_color}:s={WIDTH}x{HEIGHT}:d=300[bg];"
        bg_mode = "solid"

    # Color-Grading + Grain + Vignette auf die Bilder (vor Captions/Titel → Text bleibt crisp)
    grade_part = f"[bg]{COLOR_GRADE}[bgg];"

    # Captions-Layer einbrennen (zwischen [bgg] und Titel)
    if ass_path:
        cap_part = f"[bgg]ass={ass_path}[capbg];"
        final_in = "capbg"
    else:
        cap_part = ""
        final_in = "bgg"

    filter_complex = bg_part + grade_part + cap_part + _title_watermark_filter(
        final_in, title_escaped, watermark_escaped, req.text_color
    )

    cmd = ["ffmpeg", "-y", "-i", audio_path] + input_args + [
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "0:a",
    ] + ENCODE_ARGS + [video_path]

    log.info("Running ffmpeg (bg=%s, captions=%s)...", bg_mode, bool(ass_path))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        log.error("FFmpeg failed (rc=%d): %s", result.returncode, result.stderr[-2500:])
        raise HTTPException(status_code=500, detail=f"ffmpeg failed: {result.stderr[-1500:]}")
    log.info("FFmpeg succeeded, output size=%d", os.path.getsize(video_path) if os.path.exists(video_path) else -1)

    if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
        raise HTTPException(status_code=500, detail="output mp4 missing or empty")

    return FileResponse(video_path, media_type="video/mp4", filename="reel.mp4")
