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
> parallel translations via `translation_group`. The keyword path alone passes all 20 EN/DE/IT
> benchmark questions, **Pass@8 100%, MRR 0.90**, including the Italian-question/no-Italian-manual
> cases — our Bonus 02 story. Citations are metadata, never generated text. The answer composer
> and chat UI are the next phase and are honestly not built yet."

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
(Sujal, next phase) answer composer with citations + API + chat UI
```

## Live demo sequence (verbatim commands)

```bash
wc -l build/chunks.jsonl                                   # 1351 — real data, not a slide gimmick
python -m unittest discover -q                             # 97 tests, on screen
python -m retrieve.evaluate                                # the benchmark table, live
python -m retrieve "Wieviel Zuladung hat der FlashBot?"    # German → 20 kg, §2.3, p. 40, DE
python -m retrieve "Quanto pesa la stazione di ricarica CC1?"    # IT question, no IT manual → says so, answers from EN/DE
python -m retrieve "Which robot is best for a busy restaurant?"  # advisory → several products
python -m retrieve "Wieviel Zuladung hat der FlashBot?" --vector   # finale: [bm25+vector] if indexed; warning+fallback if not
```

Order discipline: corpus proof → tests → benchmark → keyword CLI → cross-lingual case →
advisory case → (optional) hybrid. If indexing has happened, the finale is the mic-drop.
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
BM25 + parameter index + a small lexicon — measured in **~0.4 s** for the full 20-question
benchmark on one core, no network, no GPU. The dense path adds one query embedding.

**"What's NOT done yet?"**
The production Gemini Embedding 2 → ChromaDB run (one command on any team machine — not yet
counted as done until `Indexed 1351 chunks` prints and the integration test runs), the LLM
answer composer, and the chat/API layer. What you see is measured retrieval, not a mock UI.

## Measurements (verified; hybrid row pending the real index)

| Check | Result |
|---|---|
| Dataset: chunks / unique ids / invalid | 1,351 / 1,351 / 0 |
| Chunk types (text/table/figure) | 706 / 169 / 476 |
| Validation | 0 errors · 1 warning |
| Parameter index rows | 2,039 (22 products) |
| Tests | **97 passed** (real-Chroma integration test included when chromadb installed) |
| Keyword benchmark pass@8 / MRR | **100% / 0.90** (20 EN/DE/IT questions) |
| Dense + hybrid with real Gemini embeddings | **pending the production run** (see gemini_chromadb_explanation.md §7) |

## Implemented vs planned (read the distinction out loud)

**Implemented & measured:** ingestion with validation gate · cited chunking · parameter
index · BM25 + lexicon + language policy + routing · hybrid fusion code (RRF) with filtered
vector search · 97-test suite + benchmark · CLI (standalone service, Bonus 01 surface).
**Implemented, awaiting validation:** production ChromaDB build (documented, one command).
**Not built:** LLM answer composer, API, web chat UI, figure display, advisory product cards,
Telegram bot. **Never claimed:** a complete autonomous chatbot.
