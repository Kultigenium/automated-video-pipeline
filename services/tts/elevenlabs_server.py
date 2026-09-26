"""FastAPI-Wrapper für ElevenLabs TTS — inklusive Wort-Timings für Captions.

Endpoints:
  GET  /health  - Service-Status + konfigurierte Stimme (ohne Key-Ausgabe)
  POST /tts     - Body {"text":"..."} -> JSON mit MP3 (base64) + Wort-Timings

Warum JSON statt direktem MP3:
ElevenLabs liefert über /with-timestamps das Audio UND die Zeitstempel in EINEM
Call. Zwei getrennte Calls würden doppelt Credits kosten. Der Renderer bekommt
die Wort-Timings dadurch geschenkt und braucht kein Whisper mehr.

Bind: 0.0.0.0:5502, intern über das Docker-Network n8n_default.
"""

from __future__ import annotations

import base64
import logging
import os

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("elevenlabs-tts")

API_BASE = "https://api.elevenlabs.io/v1"
API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "")
MODEL_ID = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
OUTPUT_FORMAT = os.environ.get("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")
MAX_TEXT_LENGTH = int(os.environ.get("MAX_TEXT_LENGTH", "5000"))
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "120"))

# Voice-Settings: Die Regler aus dem Web-Editor gelten NICHT automatisch für die
# API. Damit die Produktion so klingt wie der Test im Browser, müssen sie hier
# gesetzt werden. Werte aus dem Editor übernehmen.
STABILITY = float(os.environ.get("ELEVENLABS_STABILITY", "0.5"))
SIMILARITY_BOOST = float(os.environ.get("ELEVENLABS_SIMILARITY_BOOST", "0.75"))
STYLE = float(os.environ.get("ELEVENLABS_STYLE", "0.0"))
SPEED = float(os.environ.get("ELEVENLABS_SPEED", "1.0"))
SPEAKER_BOOST = os.environ.get("ELEVENLABS_SPEAKER_BOOST", "true").lower() == "true"


def voice_settings() -> dict:
    return {
        "stability": STABILITY,
        "similarity_boost": SIMILARITY_BOOST,
        "style": STYLE,
        "speed": SPEED,
        "use_speaker_boost": SPEAKER_BOOST,
    }


def words_from_alignment(alignment: dict) -> list[dict]:
    """Baut aus dem Zeichen-Alignment von ElevenLabs Wort-Timings.

    ElevenLabs liefert Start-/Endzeit pro ZEICHEN. Für Captions brauchen wir
    Wörter, also an Whitespace gruppieren und die Zeiten des ersten/letzten
    Zeichens übernehmen.
    """
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []

    words: list[dict] = []
    current = ""
    current_start: float | None = None
    current_end: float | None = None

    for char, start, end in zip(chars, starts, ends):
        if char.isspace():
            if current:
                words.append(
                    {"word": current, "start": current_start, "end": current_end}
                )
                current = ""
                current_start = None
                current_end = None
            continue

        if not current:
            current_start = start
        current += char
        current_end = end

    if current:
        words.append({"word": current, "start": current_start, "end": current_end})

    return words


app = FastAPI(title="ShortFormPipeline ElevenLabs-TTS", version="1.0.0")


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_LENGTH)
    # Optionale Overrides — ohne Angabe gelten die Env-Werte.
    voice_id: str | None = Field(default=None, min_length=1, max_length=100)
    model_id: str | None = Field(default=None, min_length=1, max_length=100)


@app.get("/health")
async def health() -> dict:
    # Der API-Key wird NIE ausgegeben, nur ob er gesetzt ist.
    return {
        "status": "ok",
        "engine": "elevenlabs",
        "api_key_configured": bool(API_KEY),
        "voice_id_configured": bool(VOICE_ID),
        "voice_id": VOICE_ID,
        "model_id": MODEL_ID,
        "output_format": OUTPUT_FORMAT,
        "voice_settings": voice_settings(),
    }


@app.post("/tts")
async def tts(req: TTSRequest) -> dict:
    if not API_KEY:
        raise HTTPException(status_code=500, detail="ELEVENLABS_API_KEY not set")

    voice_id = req.voice_id or VOICE_ID
    if not voice_id:
        raise HTTPException(status_code=500, detail="ELEVENLABS_VOICE_ID not set")

    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text must not be blank")

    model_id = req.model_id or MODEL_ID
    url = f"{API_BASE}/text-to-speech/{voice_id}/with-timestamps"

    log.info(
        "tts request chars=%d voice=%s model=%s",
        len(text),
        voice_id,
        model_id,
    )

    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": voice_settings(),
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                url,
                params={"output_format": OUTPUT_FORMAT},
                headers={
                    "xi-api-key": API_KEY,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="ElevenLabs timeout") from exc
    except httpx.HTTPError as exc:
        log.error("ElevenLabs request failed: %s", exc)
        raise HTTPException(status_code=502, detail="ElevenLabs unreachable") from exc

    if response.status_code == 401:
        raise HTTPException(status_code=502, detail="ElevenLabs: invalid API key")
    if response.status_code == 422:
        # Häufigster Fall: Voice-ID existiert nicht oder gehört nicht zum Account.
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs rejected the request: {response.text[:300]}",
        )
    if response.status_code == 429:
        # Kontingent erschöpft -> n8n soll auf den Fallback (kokoro) schwenken.
        raise HTTPException(
            status_code=429,
            detail="ElevenLabs quota exceeded or rate limited",
        )
    if response.status_code >= 400:
        log.error("ElevenLabs error %s: %s", response.status_code, response.text[:300])
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs error {response.status_code}",
        )

    data = response.json()
    audio_base64 = data.get("audio_base64")
    if not audio_base64:
        raise HTTPException(status_code=502, detail="ElevenLabs returned no audio")

    # alignment = Originaltext, normalized_alignment = vorgelesene Normalform.
    # Für Captions ist der Originaltext richtig; nur wenn er fehlt, ausweichen.
    alignment = data.get("alignment") or data.get("normalized_alignment") or {}
    words = words_from_alignment(alignment)
    duration = words[-1]["end"] if words else None

    try:
        audio_bytes = len(base64.b64decode(audio_base64))
    except Exception:  # pragma: no cover - defensive
        audio_bytes = None

    log.info(
        "tts done words=%d duration=%.2fs bytes=%s",
        len(words),
        duration or 0.0,
        audio_bytes,
    )

    return {
        "audio_base64": audio_base64,
        "words": words,
        "duration_seconds": duration,
        "audio_bytes": audio_bytes,
        "characters_used": len(text),
        "voice_id": voice_id,
        "model_id": model_id,
    }
