"""FastAPI-Wrapper für Kokoro-82M via ONNX Runtime (CPU-only).

Endpoints:
  GET  /health  - Service-Status, Modell und verfügbare Stimmen
  POST /tts     - Body {"text":"...", "voice":"am_michael"} -> MP3 audio/mpeg

Bind: 0.0.0.0:5501, intern über das Docker-Network n8n_default.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import subprocess
import threading
import wave
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from kokoro_onnx import Kokoro
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("kokoro-tts")

MODEL_DIR = Path(os.environ.get("MODEL_DIR", "/models"))
MODEL_NAME = os.environ.get("MODEL_NAME", "kokoro-v1.0.int8.onnx")
VOICES_NAME = os.environ.get("VOICES_NAME", "voices-v1.0.bin")
DEFAULT_VOICE = os.environ.get("DEFAULT_VOICE", "am_michael")
LANGUAGE = os.environ.get("KOKORO_LANGUAGE", "en-us")
MAX_TEXT_LENGTH = int(os.environ.get("MAX_TEXT_LENGTH", "5000"))
SPEED = float(os.environ.get("KOKORO_SPEED", "1.0"))

MODEL_PATH = MODEL_DIR / MODEL_NAME
VOICES_PATH = MODEL_DIR / VOICES_NAME

# Gute englische Presets:
#   am_michael - American English, male
#   am_adam    - American English, male
#   af_sarah   - American English, female
RECOMMENDED_VOICES = ["am_michael", "af_sarah", "am_adam"]

_model: Kokoro | None = None
_available_voices: list[str] = []
_model_lock = threading.Lock()


def load_model() -> Kokoro:
    """Lädt Modell und Voice-Styles einmal pro Prozess."""
    global _model, _available_voices

    if _model is not None:
        return _model

    with _model_lock:
        if _model is not None:
            return _model

        if not MODEL_PATH.is_file():
            raise RuntimeError(f"Kokoro model missing: {MODEL_PATH}")
        if not VOICES_PATH.is_file():
            raise RuntimeError(f"Kokoro voices file missing: {VOICES_PATH}")

        log.info(
            "loading Kokoro model=%s voices=%s",
            MODEL_PATH,
            VOICES_PATH,
        )
        model = Kokoro(str(MODEL_PATH), str(VOICES_PATH))
        voices = sorted(str(voice) for voice in model.get_voices())

        if DEFAULT_VOICE not in voices:
            raise RuntimeError(
                f"default voice {DEFAULT_VOICE!r} is unavailable; "
                f"available voices: {', '.join(voices)}"
            )

        _model = model
        _available_voices = voices
        log.info(
            "Kokoro loaded with %d voices; default=%s",
            len(voices),
            DEFAULT_VOICE,
        )

    return _model


def samples_to_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    """Konvertiert Kokoros Float-Audio ohne zusätzliche Audio-Library in PCM16-WAV."""
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        raise RuntimeError("Kokoro returned empty audio")

    audio = np.nan_to_num(audio, nan=0.0, posinf=1.0, neginf=-1.0)
    audio = np.clip(audio, -1.0, 1.0)
    pcm16 = (audio * 32767.0).astype("<i2")

    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(int(sample_rate))
        wav_file.writeframes(pcm16.tobytes())

    return wav_buffer.getvalue()


def wav_to_mp3(wav_bytes: bytes) -> bytes:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "96k",
            "-f",
            "mp3",
            "pipe:1",
        ],
        input=wav_bytes,
        capture_output=True,
        check=True,
        timeout=120,
    )

    if not proc.stdout:
        raise RuntimeError("ffmpeg returned empty MP3 audio")

    return proc.stdout


def synth_mp3(text: str, voice: str, speed: float = SPEED) -> bytes:
    model = load_model()

    if voice not in _available_voices:
        raise ValueError(
            f"unknown voice {voice!r}; available voices: "
            f"{', '.join(_available_voices)}"
        )

    # Ein Lock verhindert parallele Zugriffe auf dieselbe ONNX-Session und
    # begrenzt zugleich CPU-/RAM-Spitzen auf kleinen VPS-Systemen.
    with _model_lock:
        samples, sample_rate = model.create(
            text,
            voice=voice,
            speed=speed,
            lang=LANGUAGE,
        )

    return wav_to_mp3(samples_to_wav(samples, sample_rate))


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Beim Containerstart laden: /health ist erst erfolgreich, wenn Modell
    # und Voices-Datei tatsächlich lesbar und kompatibel sind.
    load_model()
    yield


app = FastAPI(
    title="ShortFormPipeline Kokoro-TTS",
    version="1.0.0",
    lifespan=lifespan,
)


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_LENGTH)
    voice: str = Field(default=DEFAULT_VOICE, min_length=1, max_length=100)
    # Sprechtempo pro Request: < 1.0 wirkt ruhiger/wärmer, > 1.0 treibender.
    # Ohne Angabe gilt KOKORO_SPEED aus der Umgebung.
    speed: float = Field(default=SPEED, ge=0.5, le=2.0)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "engine": "kokoro-onnx",
        "model": MODEL_NAME,
        "model_loaded": _model is not None,
        "model_file_present": MODEL_PATH.is_file(),
        "voices_file": VOICES_NAME,
        "voices_file_present": VOICES_PATH.is_file(),
        "default_voice": DEFAULT_VOICE,
        "recommended_english_voices": RECOMMENDED_VOICES,
        "available_voices": _available_voices,
        "language": LANGUAGE,
        "sample_rate_hz": 24000,
        "cpu_only": True,
    }


@app.post("/tts")
async def tts(req: TTSRequest) -> Response:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text must not be blank")

    log.info(
        "tts request len=%d voice=%s speed=%.2f",
        len(text),
        req.voice,
        req.speed,
    )

    try:
        # Synthese und ffmpeg sind blockierend; der Event Loop bleibt dadurch
        # für Health-Checks und weitere HTTP-Verbindungen verfügbar.
        mp3_bytes = await asyncio.to_thread(synth_mp3, text, req.voice, req.speed)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace")
        log.error("ffmpeg failed: %s", stderr)
        raise HTTPException(
            status_code=502,
            detail="ffmpeg conversion failed",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="ffmpeg timeout") from exc
    except RuntimeError as exc:
        log.error("Kokoro runtime error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Kokoro synthesis failed")
        raise HTTPException(
            status_code=502,
            detail=f"Kokoro error: {exc}",
        ) from exc

    return Response(
        content=mp3_bytes,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": 'inline; filename="kokoro.mp3"',
            "X-TTS-Voice": req.voice,
        },
    )
