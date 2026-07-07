"""FastAPI-Wrapper um Piper TTS (Open-Source, lokal, kein Reverse-Engineering).

Fallback-Service neben edge-tts. Wird vom n8n-Workflow nur angesprochen,
wenn edge-tts einen Fehler liefert.

Endpoints:
  GET  /health    - Service-Status
  GET  /selftest  - Erzeugt einen Sample-MP3 (fuer Smoke-Test)
  POST /tts       - Body {"text":"..."} -> MP3 audio/mpeg

Bind: 0.0.0.0:5500 (intern via docker network n8n_default).
"""
from __future__ import annotations

import io
import logging
import os
import subprocess
import wave
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from piper.voice import PiperVoice
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("piper-tts")

VOICE_DIR = Path(os.environ.get("VOICE_DIR", "/voices"))
VOICE_NAME = os.environ.get("VOICE_NAME", "de_DE-thorsten-medium")
MAX_TEXT_LENGTH = 5000

VOICE_PATH = VOICE_DIR / f"{VOICE_NAME}.onnx"
CONFIG_PATH = VOICE_DIR / f"{VOICE_NAME}.onnx.json"

app = FastAPI(title="ShortFormPipeline Piper-TTS", version="1.0.0")

_voice: PiperVoice | None = None


def get_voice() -> PiperVoice:
    global _voice
    if _voice is None:
        if not VOICE_PATH.exists():
            raise RuntimeError(f"voice file missing: {VOICE_PATH}")
        log.info("loading voice %s", VOICE_PATH)
        _voice = PiperVoice.load(str(VOICE_PATH), config_path=str(CONFIG_PATH))
    return _voice


def synth_mp3(text: str) -> bytes:
    voice = get_voice()
    wav_buf = io.BytesIO()
    with wave.open(wav_buf, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)
    wav_bytes = wav_buf.getvalue()
    if not wav_bytes:
        raise RuntimeError("piper returned empty audio")

    proc = subprocess.run(
        [
            "ffmpeg", "-loglevel", "error",
            "-f", "wav", "-i", "pipe:0",
            "-codec:a", "libmp3lame", "-b:a", "96k",
            "-f", "mp3", "pipe:1",
        ],
        input=wav_bytes,
        capture_output=True,
        check=True,
        timeout=60,
    )
    mp3_bytes = proc.stdout
    if not mp3_bytes:
        raise RuntimeError("ffmpeg returned empty mp3")
    return mp3_bytes


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_LENGTH)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "voice": VOICE_NAME,
        "voice_loaded": _voice is not None,
        "voice_file_present": VOICE_PATH.exists(),
    }


@app.get("/selftest")
async def selftest() -> JSONResponse:
    """Erzeugt einen kurzen Sample und gibt nur die Bytes-Anzahl zurueck.

    Zweck: Smoke-Test ohne JSON-Body in der Shell schreiben zu muessen.
    """
    sample = "Hallo. Dies ist ein Test der Piper Stimme Thorsten."
    try:
        mp3 = synth_mp3(sample)
    except Exception as exc:
        log.exception("selftest failed")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(exc)},
        )
    return JSONResponse(
        content={
            "status": "ok",
            "sample_text": sample,
            "mp3_bytes": len(mp3),
            "voice_loaded": _voice is not None,
        }
    )


@app.post("/tts")
async def tts(req: TTSRequest) -> Response:
    log.info("tts request len=%d", len(req.text))
    try:
        mp3_bytes = synth_mp3(req.text)
    except subprocess.CalledProcessError as exc:
        log.error("ffmpeg failed stderr=%s", exc.stderr.decode("utf-8", errors="replace"))
        raise HTTPException(status_code=502, detail="ffmpeg conversion failed") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="ffmpeg timeout") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("piper synthesize failed")
        raise HTTPException(status_code=502, detail=f"piper error: {exc}") from exc

    return Response(
        content=mp3_bytes,
        media_type="audio/mpeg",
        headers={"Content-Disposition": 'inline; filename="piper.mp3"'},
    )
