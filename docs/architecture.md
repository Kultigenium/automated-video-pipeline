# Orchestration layer (n8n)

Two workflows drive the pipeline. The JSON exports stay private (they reference credentials and delivery targets); this documents their logic.

![Scout](architecture_1_scout.svg)

## Workflow 1 — Scout (daily cron)

```
Cron (daily, early morning)
→ Source registry (one Code node, one line per source: type rss|json, weight, max age)
→ Fetch all sources (HTTP, continue on error)
→ Parse RSS/Atom + HN Algolia JSON + GitHub Search JSON into one article list
   · prompt-injection sanitizer on titles/descriptions
   · per-source age window (news 24 h, vendor blogs up to 7 days)
   · GitHub: non-Latin descriptions dropped, bought-star pattern (>1,000 stars, <1 % forks) gets no bonus
→ Dedupe (in-run + across runs, 500-link memory in workflow static data, tracking params stripped)
→ Keyword scoring (niche terms +, funding/enterprise/policy terms −, source weight, max 4 per source)
→ Top 10 → Gemini relevance score 1–10 (structured JSON, retry 3×)
→ IF score ≥ 7 → fetch full article text of the top story (HTML → text, capped, sanitized)
                → notify + POST to Workflow 2 (webhook)
   ELSE        → "no story today" alert
```

Design notes:
- **Adding a source is one line**, not a new node — the parser knows every format.
- **One dead feed never stops the run.** Failures are counted in a per-run source report.
- **Cheap filter before the paid one.** The keyword score removes clearly off-topic items before any LLM quota is spent.
- **Full text beats teasers.** RSS descriptions are too thin for a fact-dense script; the top story's body is fetched and appended (the story is kept even if the fetch fails).

## Workflow 2 — Script Generator (webhook)

![Script and gates](architecture_2_script.svg)

![Voice, video, delivery](architecture_3_video.svg)

```
Webhook → normalize + duplicate check
→ Groq: script (four spoken beats: problem hook · news facts · use case · "my take"; loop ending)
→ Gate 1: rule check (deterministic)      ── violation ──┐
→ Gate 2: Gemini + Google Search          ── FIX ────────┤→ Groq revises with findings → Gate 1 (max 2 rounds)
                                          ── REJECT ─────→ notify with reason, stop
→ PASS
→ TTS service (audio + word timings)  ── error → Kokoro fallback (audio only; renderer estimates timings)
→ Top story? → Video renderer (style "beweis") → MP4
→ Groq: upload metadata (title, description, hashtags)
→ Messenger: script, audio, video, upload template → human publishes
```

Design notes:
- **Two gates with different failure modes.** Gate 1 is free and deterministic (word count, hook length, verdict beat present, no digits/hype/CTAs/labels). Gate 2 is a different model than the writer, with live search — it catches what changes over time: superseded versions, wrong prices, off-niche topics, advertiser risk. See [`quality-gates/`](../quality-gates).
- **FIX vs. REJECT.** FIX = the topic is good, the script is not → revise. REJECT = the topic itself is not worth a video → revising won't help.
- **Every external call has an explicit error branch** that ends in a notification — the operator always learns *why* a video is missing.
- **Word counts, not durations.** "Aim for 30 seconds" is not something an LLM can hit; 85–100 words is.

## Rendering pipeline (video-renderer, style `beweis`)

1. **Capture:** headless Chromium opens the source at phone width (360 CSS px × device scale 3 = exactly 1080 px), dismisses cookie banners, takes one full-page capture and records the positions of evidence: figures with units and phrases the script quotes verbatim.
2. **Match:** spoken words are mapped to evidence (`twenty dollars` → `$20`, `five-hour` → `5-hour`). Negations get a strike-through instead of a highlight.
3. **Camera:** a virtual camera scrolls the capture in sync with the word timings, highlights the evidence as it is spoken. No horizontal pan, max. 16 % zoom.
4. **Cut:** facts play on the source; commentary and evidence-free stretches play on stock clips (switch every 2–3 s on beat boundaries, per-story ledger against repetition).
5. **Encode:** raw frames are piped straight into FFmpeg with the audio → MP4 1080×1920.

If there is neither a usable source nor B-roll, the renderer falls back to `motion` (kinetic typography driven by the same word timings), then to `slideshow`.
