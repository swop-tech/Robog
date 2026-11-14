# Gemini Embedding 2 + ChromaDB — the production run, explained honestly

> Companion to `vector_database_explanation.md` (concepts) and `judge_demo_script.md`
> (presenting). This file records **exactly what has and has not been executed**
> for the production vector database, and the precise configuration to use.
> Status when written: **the production embedding run has NOT been executed yet.**

---

## 1. Why Gemini Embedding 2 (the chosen provider)

Per the team decision, the production index uses **`gemini-embedding-2`** (free Google AI
Studio tier). Reasons:

- Multilingual — our corpus is EN/DE/IT (469 DE · 466 EN · 416 IT in the current build).
- Matryoshka dimensions (128–3072) — we chose **1536**, stored consistently in documents and queries.
- Free tier is sufficient for a one-shot indexing job of this size (math below).
- The offline path (`--provider local`, `multilingual-e5-small`) remains documented in
  `stack-decisions.md` as the emergency/no-internet fallback; nothing here uses it.

**We did not switch providers. We have not silently mixed embeddings from different models.**
Any collection indexed with Gemini must be queried with Gemini at the same dimension —
mixing with e5 vectors invalidates all distances.

## 2. Exact model and configuration (verify, then freeze)

Inspected in this repository — `ingest/embed_index.py`:

| Setting | Value | Where |
|---|---|---|
| Model id (exact) | `gemini-embedding-2` | `GEMINI_MODEL` constant |
| Output dimension | **1536** | `--dims` default, stored via `output_dimensionality` |
| Batching | 100 texts per `embed_content` call | `range(0, len(texts), 100)` |
| Auth | `GEMINI_API_KEY` or `GOOGLE_API_KEY` env var — never in code, never in git | `GeminiEmbedder.__init__` |
| Document input | Official QA template: ``title: {title} \| text: {text}`` (``title: none`` when absent) | ``gemini_document_text()`` in the module (tested) |
| Vectors normalized | no — Gemini outputs unit-normalized embeddings for cosine space | Chroma collection metadata `hnsw:space: cosine` |
| Input limit check | max chunk 2,832 chars ≈ 809 tokens — all chunks ≪ 8,192-token limit | measured on `build/chunks.jsonl` |
| Input volume | ≈ 237k–271k tokens**†** total; **14** API calls for 1,351 chunks | measured on `build/chunks.jsonl` |
| Persistent store | `build/chroma/` (default `--persist`) | `main()` |
| Collection | `robog`, created with `get_or_create_collection`, upsert by chunk id | indexing side |

**†** Characters/3.5–4.0 — an estimate, not a billed measurement. There is no per-token charge
on the free tier; the number exists so we can see we are nowhere near limits.

**Query side — `retrieve/vector.py`:** identical model id, identical dimension (1536),
and the matching official query template `task: question answering | query: {question}`,
same env-var auth, plus a per-session cache so fallback languages on identical text don't
re-embed. Document side and query side are implemented symmetrically and unit-tested —
that is the hard requirement; nothing is mixed across providers.

**Task instructions (APPROVED decision, implemented):** Gemini Embedding 2 moves the
task signal into the text itself (no legacy `task_type`). We use exactly the approved
official question-answering format for text-only retrieval:

- document: `title: {title} | text: {passage}` (`title: none` when a chunk has no title;
  every current chunk carries `metadata.title`)
- query: `task: question answering | query: {question}`
- Figure chunks carry caption/label text with the same document template; the image
  pixels are never embedded; multimodal image instruction formats are intentionally not
  applied (nothing image-like is indexed).

Status honestly stated: templates are implemented and unit-tested, but the exact
live-API behaviour has **not** been exercised end to end — that happens with the
production run on the team's machine (§5 measures it). Use exactly these strings; do
not improvise variants.

## 3. What will be embedded — modality by modality (measured representation)

The corpus is markdown converted from PDFs. **Nothing image-like is embedded** — Gemini
Embedding 2 *supports* multimodal input, our index is **text-only by design**:

| Chunk type | Count | What the vector represents | Multimodal? |
|---|---|---|---|
| text | 706 | paragraph/section prose | no |
| table | 169 | the markdown table text (values as words/numbers) | no |
| figure | 476 | caption + `Labels:` legend (what the figure *says*) + `image` path kept as metadata | no — the PNG itself is **not** embedded |

Figure images stay on disk and are referenced by metadata (`image` path on the chunk).
"Show me the part" retrieval works by matching the legend text and returning the image
path to the UI. Upgrading to true multimodal embeddings would be an additive later step,
evaluated against the same benchmark — not silently mixed into this collection.

## 4. Freemium/quota/cost assessment (as far as verifiable)

- Calls: ~14 (+ occasional retries). Input ≈ a quarter-million tokens.
- Free tier rate limits as documented by the team (`stack-decisions.md`): ~1,500 requests/day,
  input may be used by Google to improve products. Robot-manual content is public documentation — acceptable.
- No billing is to be enabled; if the quota turns out to be different on the day we run,
  we **stop and reassess**, per the approved process. We do not switch tiers or providers without explicit approval.
- Pricing for `gemini-embedding-2` could not be independently verified from the sandbox
  (pricing page unreachable). Expected cost within free tier: **$0**.
- Before the run we verify model access on the key's project in AI Studio; any 404/permission
  error means stop, not workaround.

## 5. What the production run will verify (planned, not yet executed)

Indexing step expected last line:

```
Indexed 1351 chunks into build/chroma (collection 'robog').
```

Verification checklist for the person who runs it (or on the machine where it runs):

1. `wc -l build/chunks.jsonl` → 1351; all ids unique; all `text` non-empty ✅ (already verified here).
2. `python -m ingest.embed_index --provider gemini` → watch the `embedded N/1351` progress lines; note wall time (record it below).
3. Reopen test — in a fresh Python process:
   ```python
   import chromadb
   c = chromadb.PersistentClient("build/chroma").get_collection("robog")
   print(c.count())                       # expect 1351
   meta = c.get(ids=[c.get()["ids"][0]])["metadatas"][0]
   print(meta["title"], meta.get("page"), meta["language"])
   ```
4. Dense sanity: `python -m retrieve "Wieviel Zuladung hat der FlashBot?" --vector` → expect `[bm25+vector]` tags.
5. Full tests: `python -m unittest discover -q` → the real-store integration test **must run, not skip**.
6. Re-run indexing → upsert by same chunk ids, `count()` stays **1351** — no duplicates.
7. Fill in measured numbers in §7 of this file.

**Resumability honesty:** the existing script embeds in one pass; a crash mid-run means
"delete and rerun" (idempotent upsert, so re-running is safe and cheap at ~$0). A
checkpoint/resume layer was intentionally not bolted onto a shared file mid-task; if the
first real run shows flaky rate limits, we add minimal per-batch retry + progress logging
in `ingest/` with Sujal's sign-off.

## 6. Team distribution of this artifact

`build/` is git-ignored — the database is **never committed**. Every teammate obtains it
by running the two commands (`ingest.run`, then `embed_index --provider gemini`). No API
key is shared; each person's key executes their own indexing; vectors are identical for
identical input, so the DB is reproducible on every machine.

## 7. Measured results (fill after the real run)

| Metric | Value |
|---|---|
| Chunks embedded / failed | pending real run (design target 1,351 / 0) |
| Output dimension | 1,536 (configured) |
| Wall time for indexing | pending |
| DB size on disk | pending |
| Dense vs BM25 vs hybrid Pass@8 / MRR (20-question `eval/questions.jsonl`) | pending — keyword-only today: 100% / 0.90 |

## 8. Limitations

Production embedding quality is unproven until §5 is executed (templates are implemented and
unit-tested but unverified against the live API); the benchmark is
keyword-matching on a small set and does not prove answer correctness; figure PNG pixels
are not embedded; Google free-tier terms allow input use for product improvement (public
docs, fine); no answer composer yet (Sujal's stage) — this database feeds retrieval only.
