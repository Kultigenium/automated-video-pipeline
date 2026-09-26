FROM python:3.11-slim

WORKDIR /app

# curl nur für den HEALTHCHECK; kein ffmpeg nötig, da ElevenLabs fertiges MP3 liefert.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV ELEVENLABS_MODEL_ID=eleven_multilingual_v2
ENV ELEVENLABS_OUTPUT_FORMAT=mp3_44100_128
ENV MAX_TEXT_LENGTH=5000

# API-Key und Voice-ID kommen NIE ins Image, sondern zur Laufzeit via env_file.

COPY elevenlabs_server.py .

EXPOSE 5502

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:5502/health || exit 1

CMD ["uvicorn", "elevenlabs_server:app", "--host", "0.0.0.0", "--port", "5502", "--workers", "1"]
