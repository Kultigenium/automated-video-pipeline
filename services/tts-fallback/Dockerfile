FROM python:3.11-slim

WORKDIR /app

# ffmpeg = für WAV -> MP3 Konvertierung (Telegram sendAudio braucht MP3)
# curl = Voice-Modell Download im Build
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Voice-Modell: Thorsten-Medium (DE, männlich, MIT-Lizenz)
# Quelle: https://huggingface.co/rhasspy/piper-voices/tree/main/de/de_DE/thorsten/medium
ENV VOICE_DIR=/voices
ENV VOICE_NAME=de_DE-thorsten-medium

RUN mkdir -p ${VOICE_DIR} \
 && curl -fSL -o ${VOICE_DIR}/${VOICE_NAME}.onnx \
      https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx \
 && curl -fSL -o ${VOICE_DIR}/${VOICE_NAME}.onnx.json \
      https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx.json

COPY piper_server.py .

EXPOSE 5500

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:5500/health || exit 1

CMD ["uvicorn", "piper_server:app", "--host", "0.0.0.0", "--port", "5500"]
