# Quality gates

Every script passes two automated checks before any voice or video is produced. They replace a manual approval per video.

| Gate | When | Who checks | Node code |
|---|---|---|---|
| **1 — Rules** | right after writing | deterministic code, free | [`nodes/p1_regelcheck.js`](nodes/p1_regelcheck.js) |
| **2 — Sense & monetization** | after Gate 1 passed | Gemini with Google Search (a different model than the writer, sees today's web) | prompt built in `p1_regelcheck.js`, verdict in [`nodes/urteil_bilden.js`](nodes/urteil_bilden.js) |

```
writer → Gate 1 → Gate 2 → PASS   → voice → video → delivery
                          ↘ FIX    → writer revises with the findings → Gate 1 → Gate 2   (max. 2 revisions)
                          ↘ REJECT → dropped, reason sent to the operator
```

**Gate 1** — hard violations go back to the writer, soft ones are passed to Gate 2 as hints:
- 70–120 words (target 85–100), hook ≤ 18 words (target ≤ 12)
- a "My take" verdict naming one concrete limitation and who it is worth it for
- numbers, versions and prices spelled out (TTS reads them reliably)
- no hype words, no calls to action, no labels or markdown
- hook does not open with a vague pronoun; last sentence echoes the hook (loop)

**Gate 2** — five checks: *current* (nothing newer superseded it), *facts* (every figure verified), *niche* (actionable for a solo builder today), *take* (concrete, not generic), *monetizable* (advertiser-friendly, no dubious projects). PASS needs all five and a score ≥ 7.

**FIX vs. REJECT:** FIX means the topic is good but the script is not. REJECT means the topic itself is not worth a video.

## Tests

```bash
node test_protokolle.mjs
```

The node files are plain n8n Code-node bodies; the test harness wraps them with stubs for `$input` and `$('Node name')`, so they run without n8n.
