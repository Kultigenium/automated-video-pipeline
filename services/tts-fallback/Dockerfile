FROM python:3.11-slim

WORKDIR /app

# ffmpeg: WAV -> MP3, analog zum bestehenden Piper-Service
# curl: Modell-Download während des Image-Builds
# espeak-ng: robuste englische Phonemisierung auf Debian/Linux
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      espeak-ng \
      ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV MODEL_DIR=/models
ENV MODEL_NAME=kokoro-v1.0.int8.onnx
ENV VOICES_NAME=voices-v1.0.bin
ENV DEFAULT_VOICE=am_michael
ENV KOKORO_LANGUAGE=en-us
ENV KOKORO_SPEED=1.0
ENV MAX_TEXT_LENGTH=5000

# Offizielle Release-Artefakte des kokoro-onnx-Projekts:
# - ONNX-Datei: Modellgewichte und Inferenzgraph
# - BIN-Datei: NumPy/NPZ-basierte Style-Vektoren aller Stimmen
#
# INT8 wird gewählt, weil das Modell CPU-only auf einem 8-GB-VPS laufen soll.
# Es ist mit ca. 88 MB deutlich kleiner als FP32 (ca. 310 MB).
RUN mkdir -p "${MODEL_DIR}" \
 && curl --fail --location --retry 3 \
      --output "${MODEL_DIR}/${MODEL_NAME}" \
      "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx" \
 && curl --fail --location --retry 3 \
      --output "${MODEL_DIR}/${VOICES_NAME}" \
      "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin" \
 && test -s "${MODEL_DIR}/${MODEL_NAME}" \
 && test -s "${MODEL_DIR}/${VOICES_NAME}"

COPY kokoro_server.py .

EXPOSE 5501

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD curl -fsS http://127.0.0.1:5501/health || exit 1

CMD ["uvicorn", "kokoro_server:app", "--host", "0.0.0.0", "--port", "5501", "--workers", "1"]
