# Robog — judge demo script

> NO! Hackathon · Telmekon Challenge. Rehearse this twice before the room.
> Numbers in **bold** are measured on this machine; everything else is designed.
> If something is listed as "pending", do not claim it — say it is one command away.

---

## 30-second project explanation

> "Robot manuals are where support answers go to die. We turn 63 manuals —
> 22 products, English, German and Italian — into **1,351 cited chunks**: text, tables and
> figure legends, each tagged with product, section, page and language. Questions are answered
> by a retrieval engine that combines **exact keyword search (BM25)** with **Gemini Embedding 2
> vectors** in **ChromaDB**, fused by reciprocal rank fusion, filtered by a language policy.
> Every claim traces to a manual title, section and page. No evidence, no answer."

## 1-minute technical explanation

> "Ingestion runs once: clean markdown, chunk, tag — **706 text, 169 table, 476 figure
> chunks, 0 validation errors** — plus a **2,039-row parameter index** for verbatim spec
> lookups ('payload: 20 kg, p. 40'). At question time we detect EN/DE/IT deterministically,
> route the question (lookup vs advisory), then vote with BM25, a curated EN/DE/IT lexicon
> (payload ≈ Zuladung ≈ carico), the parameter index and — once the production index runs —
> dense Gemini Embedding 2 vectors in ChromaDB, fused with RRF, deduplicated across the three
> parallel translations via `translation_group`. The keyword path alone passes all 28 EN/DE/IT
> benchmark questions, **Pass@8 100%, MRR 0.95**, including the Italian-question/no-Italian-manual
> cases — our Bonus 02 story. Citations are metadata, never generated text. The answer is
> written by Gemini 2.5 Flash (or a deterministic formatter with no key) and then checked by
> our evidence validator — **28/28 answers grounded, every number verified** — and served by
> a FastAPI service with a web chat in front."

## Architecture walkthrough (point, don't recite)

```
PDFs ──► markdown ──► ingest/ ──► build/chunks.jsonl  (1,351 chunks, cited)
  │                       ├──► build/params.jsonl     (2,039 parameter rows)
  │                       └──► embed (gemini-embedding-2, 1536 d) ──► build/chroma/ (collection "robog", cosine)
  ▼
QUESTION ──► retrieve/  (Gaurav)
              lang.py → router.py → bm25.py + lexicon.py + params.py [+ vector.py]
              → RRF fusion → dedupe translations → diversify products → top-k
              → SearchResult (JSON contract for the API)
  ▼
answer/ composer + evidence validator ──► api/ FastAPI (/search /ask /products /media /health) ──► ui/ web chat
```

## Live demo sequence (verbatim commands)

```bash
wc -l build/chunks.jsonl                                   # 1351 — real data, not a slide gimmick
python -m unittest discover -q                             # 295 tests, on screen
python -m retrieve.evaluate                                # the benchmark table, live
python -m retrieve "Wieviel Zuladung hat der FlashBot?"    # German → 20 kg, §2.3, p. 40, DE
python -m retrieve "Quanto pesa la stazione di ricarica CC1?"    # IT question, no IT manual → says so, answers from EN/DE
python -m retrieve "Which robot is best for a busy restaurant?"  # advisory → several products
python -m retrieve "Wieviel Zuladung hat der FlashBot?" --vector   # [bm25+vector] if indexed; warning+fallback if not
uvicorn api.app:app                                        # finale: the chat at http://localhost:8000
curl -s localhost:8000/health                              # vector_active + provider, on screen
```

Order discipline: corpus proof → tests → benchmark → keyword CLI → cross-lingual case →
advisory case → (optional) hybrid → the chat. Check `/health` **before** the room: say
"hybrid" only if `vector_active` is `true`, "Gemini" only if `provider` is `gemini`. If indexing has happened, the finale is the mic-drop.
If it hasn't, the fallback we show is **by design**, not by accident — say the sentence.

## Five likely judge questions — accurate answers

**"Why not just ChatGPT with the PDF pasted in?"**
It invents numbers and page references — fatally, for spec sheets. Ours retrieves the exact
chunk and its page, and refuses when nothing matches. Evidence, not eloquence.

**"Why two search technologies?"**
Complementary blind spots: keyword search can't paraphrase; vectors fumble rare codes and
numbers. RRF fuses votes from both — plus the parameter index, so spec questions are exact.

**"Does it answer in Italian?"**
Detection, policy, lexicon and the benchmark cover EN/DE/IT. Three products lack Italian
editions (FlashBot, GW1, Pager); the engine answers in Italian anyway — from EN/DE sources,
and says so on screen.

**"Why is retrieval so fast?"**
BM25 + parameter index + a small lexicon — measured in **under a second** for the full 28-question
benchmark on one core, no network, no GPU. The dense path adds one query embedding.

**"What's NOT done yet?"**
Measured hybrid numbers: the Gemini Embedding 2 → ChromaDB run is one command per machine
and is not counted as done until `Indexed 1351 chunks` prints and `/health` shows
`vector_active: true`. Also not built: auth, rate limiting, streaming, a Telegram client.

## Measurements (verified; hybrid row pending the real index)

| Check | Result |
|---|---|
| Dataset: chunks / unique ids / invalid | 1,351 / 1,351 / 0 |
| Chunk types (text/table/figure) | 706 / 169 / 476 |
| Validation | 0 errors · 1 warning |
| Parameter index rows | 2,039 (22 products) |
| Product profile cards | 38 (13 robots) |
| Tests | **274 passed** (real-Chroma integration tests included when chromadb installed) |
| Keyword benchmark pass@8 / MRR | **100% / 0.95** (28 EN/DE/IT questions) |
| Answers grounded / numbers verified (offline composer) | **28/28 / 100%** |
| Dense + hybrid with real Gemini embeddings | **pending the production run** (see gemini_chromadb_explanation.md §7) |

## Implemented vs planned (read the distinction out loud)

| Area | Status |
|---|---|
| Ingestion with validation gate · cited chunking · parameter index · profile cards | ✅ implemented & measured |
| BM25 + lexicon + language policy + routing · 28-question benchmark | ✅ implemented & measured |
| Hybrid fusion (RRF) with filtered vector search · resumable Gemini indexing | ✅ implemented, tested with deterministic embeddings |
| Answer composer + evidence validator | ✅ implemented & measured (offline formatter) |
| FastAPI service (Bonus 01) · web chat · figure display | ✅ implemented & tested |
| Production ChromaDB build · hybrid numbers · live Gemini answers | ⏳ pending the live run on a laptop |
| Auth / rate limiting · streaming · Telegram bot | ❌ not built |

Full table with the proof command for each row: `judge_explanation.md`.
