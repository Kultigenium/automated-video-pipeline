FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium fuer das Beweis-Footage: nur die Headless-Shell, --with-deps holt die
# Systembibliotheken per apt. Fester Pfad, damit Build und Laufzeit ihn finden.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python -m playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*

# faster-whisper Modell (base, multilingual) ins Image backen -> kein Runtime-Download
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"

COPY server.py motion.py motion3.py broll.py beweis.py ./

EXPOSE 8788

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8788"]
