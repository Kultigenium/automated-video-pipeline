"""FastAPI-TTS-Service: Azure Speech (primär, SSML) mit edge-tts-Fallback.

Endpoint: POST /tts
Body: {"text": "...", "voice": "de-DE-FlorianMultilingualNeural", "rate": "+0%", "pitch": "+0Hz"}
Response: audio/mpeg (MP3), Header X-TTS-Engine: azure | edge-tts

Azure wird genutzt, wenn AZURE_SPEECH_KEY + AZURE_SPEECH_REGION gesetzt sind
(env_file auf dem VPS, NICHT im Git/Image). Schlägt Azure fehl oder fehlt der
Key, greift edge-tts — gleiche Voice-Namen, n8n merkt keinen Unterschied.

SSML: WÖRTER IN GROSSBUCHSTABEN (ab 3 Zeichen) werden mit <emphasis> betont.

Bind: 127.0.0.1:8787 (nur lokal, n8n läuft auf demselben VPS).
"""
from __future__ import annotations

import html
import io
import logging
import os
import re
from typing import Optional

import edge_tts
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tts")

DEFAULT_VOICE = "de-DE-KatjaNeural"
MAX_TEXT_LENGTH = 5000

AZURE_KEY = os.environ.get("AZURE_SPEECH_KEY", "").strip()
AZURE_REGION = os.environ.get("AZURE_SPEECH_REGION", "").strip()
AZURE_OUTPUT_FORMAT = "audio-24khz-96kbitrate-mono-mp3"
AZURE_TIMEOUT = 30.0

app = FastAPI(title="ShortFormPipeline TTS (Azure + edge-tts)", version="2.0.0")


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_LENGTH)
    voice: str = DEFAULT_VOICE
    rate: str = "+0%"
    pitch: str = "+0Hz"


def build_ssml(text: str, voice: str, rate: str, pitch: str) -> str:
    body = html.escape(text, quote=False)
    # CAPS-Betonung: GROSSGESCHRIEBENE Wörter (>=3 Zeichen) bekommen Emphasis.
    body = re.sub(
        r"\b([A-ZÄÖÜ]{3,})\b",
        lambda m: f'<emphasis level="strong">{m.group(1)}</emphasis>',
        body,
    )
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="de-DE">'
        f'<voice name="{voice}"><prosody rate="{rate}" pitch="{pitch}">{body}</prosody></voice>'
        "</speak>"
    )


async def azure_tts(req: TTSRequest) -> bytes:
    url = f"https://{AZURE_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_KEY,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": AZURE_OUTPUT_FORMAT,
        "User-Agent": "video-pipeline-tts",
    }
    ssml = build_ssml(req.text, req.voice, req.rate, req.pitch)
    async with httpx.AsyncClient(timeout=AZURE_TIMEOUT) as client:
        resp = await client.post(url, headers=headers, content=ssml.encode("utf-8"))
    if resp.status_code != 200:
        raise RuntimeError(f"Azure HTTP {resp.status_code}: {resp.text[:200]}")
    if not resp.content:
        raise RuntimeError("Azure returned empty audio")
    return resp.content


async def edge_tts_fallback(req: TTSRequest) -> bytes:
    buf = io.BytesIO()
    communicate = edge_tts.Communicate(
        text=req.text, voice=req.voice, rate=req.rate, pitch=req.pitch
    )
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    audio = buf.getvalue()
    if not audio:
        raise RuntimeError("edge-tts returned empty audio")
    return audio


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "default_voice": DEFAULT_VOICE,
        "azure_configured": bool(AZURE_KEY and AZURE_REGION),
    }


@app.get("/voices")
async def voices(locale: Optional[str] = "de-DE") -> list[dict]:
    all_voices = await edge_tts.list_voices()
    if locale:
        all_voices = [v for v in all_voices if v.get("Locale", "").lower() == locale.lower()]
    return [
        {"name": v["ShortName"], "gender": v["Gender"], "locale": v["Locale"]}
        for v in all_voices
    ]


@app.post("/tts")
async def tts(req: TTSRequest) -> Response:
    log.info("tts request voice=%s rate=%s len=%d", req.voice, req.rate, len(req.text))
    audio: bytes = b""
    engine = "edge-tts"

    if AZURE_KEY and AZURE_REGION:
        try:
            audio = await azure_tts(req)
            engine = "azure"
        except Exception:
            log.exception("azure tts failed, falling back to edge-tts")

    if not audio:
        try:
            audio = await edge_tts_fallback(req)
            engine = "edge-tts"
        except Exception as exc:
            log.exception("edge-tts failed")
            raise HTTPException(status_code=502, detail=f"tts error: {exc}") from exc

    log.info("tts ok engine=%s bytes=%d", engine, len(audio))
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": 'inline; filename="tts.mp3"',
            "X-TTS-Engine": engine,
        },
    )
