# Orchestration layer (n8n)

Two workflows drive the pipeline. The JSON exports stay private (they contain credentials references and delivery targets); this documents their logic.

## Workflow 1 — Scout (daily cron)

```
Cron (daily) → 10× RSS Read → Merge → Normalize (title/link/description)
→ Dedupe (7-day keyword history via getWorkflowStaticData)
→ Keyword pre-score → cap to top 10 articles
→ Gemini 2.5 relevance scoring (retry 3×, batched, 1 article per call — free-tier rate limits)
→ IF score ≥ 7 → mark highest as top story
→ Messenger notification + hand-off to Workflow 2 (webhook)
```

Design notes:
- **Batching with delays** is mandatory on free-tier LLM APIs (requests/day caps).
- LLM chain nodes only return the completion — original fields (title, link) must be explicitly re-joined from earlier nodes (`$('NodeName').all()`).
- Structured JSON output from the scoring prompt is required for reliable IF conditions.

## Workflow 2 — Script Generator (webhook-triggered)

```
Webhook → Normalize + keyword extraction
→ Groq Llama 3.3 70B: script generation
   (system prompt enforces: negative-statement hook, 3-beat body,
    loop ending that reprises the hook, hard word-count floor,
    spoken-style commas for TTS pacing, few-shot examples)
→ Parse + validate (non-empty guard)
→ parallel:
   ├─ Messenger: script for human review
   └─ TTS service (POST /tts)
        → IF audio ok → Messenger audio + IF top story:
        │     → base64 encode (getBinaryDataBuffer)
        │     → video renderer (POST /render)
        │     → IF video ok → Messenger video  ELSE → alert
        └─ ELSE → Piper fallback (POST /tts) → same continuation / alert
→ Groq: upload metadata (title ≤60 chars with product name first, description, hashtags)
```

Design notes:
- Every external call has an explicit error branch that ends in a messenger alert — the operator always learns *why* a video is missing.
- The word-count floor lives in the prompt because "aim for ~30 seconds" is not something an LLM can hit reliably; word counts are.
- Deploys go through the n8n REST API surgically: fetch live workflow, replace only the prompt nodes by name, `PUT` back, then a deactivate/activate cycle (n8n caches the active version otherwise).

## Rendering pipeline (inside video-renderer)

1. Fetch article OG-image; top up with stock photos (keyword search) to ~6 images.
2. Transcribe the TTS MP3 with faster-whisper (word timestamps).
3. Generate ASS subtitles: 2-word cues, pop-in animation, screen-center alignment, font auto-shrink so the longest word never overflows.
4. FFmpeg single pass: photo slideshow (hard cuts + Ken-Burns zoom), color grade + vignette + film grain, burned-in captions, top-center watermark, AAC audio mux → MP4 1080×1920.
