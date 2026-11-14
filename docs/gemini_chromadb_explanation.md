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
| Model id (exact) | `gemini-embedding-2` (override with `ROBOG_EMBED_MODEL`) | `GEMINI_MODEL` constant |
| Output dimension | **1536** | `--dims` default, stored via `output_dimensionality` |
| Task signal | `task_type` in the request config: `RETRIEVAL_DOCUMENT` when indexing, `RETRIEVAL_QUERY` when querying | `GeminiEmbedder.embed_batch` / `_gemini_embedder` |
| Batching | **40** texts per `embed_content` call (`DEFAULT_BATCH_SIZE`), paced under the free-tier 30k TPM window | `TokenRateLimiter`, `range(0, len(texts), eff_batch)` |
| Auth | `GEMINI_API_KEY` or `GOOGLE_API_KEY` env var — never in code, never in git | `resolve_gemini_api_key()` |
| Document input | `"{product} — {section}\n{chunk text}"` — a scope breadcrumb plus the chunk **verbatim** | `format_passage()` |
| Stored document | the chunk text **verbatim** (no breadcrumb), so citations and the numeric check see only original manual text | `_upsert_rows` |
| Vectors normalized | **yes** — every vector is L2-normalized before it is stored | `l2_normalize()` in `embed_gemini_batch` |
| Distance space | cosine | collection metadata `hnsw:space: cosine` |
| Corpus stamp | `corpus_digest` (sha256 of the chunk ids) + `corpus_chunks` in the collection metadata | `_stamp_corpus()` |
| Input volume | ≈ 237k–271k tokens**†** total; **~34** API calls for 1,351 chunks at batch 40 | measured on `build/chunks.jsonl` |
| Persistent store | `build/chroma/` (default `--persist`) | `main()` |
| Collection | `robog`, created with `get_or_create_collection`, upsert by chunk id | indexing side |

**†** Characters/3.5–4.0 — an estimate, not a billed measurement. There is no per-token charge
on the free tier; the number exists so we can see we are nowhere near limits.

**Query side — `retrieve/vector.py`:** identical model id, identical dimension (1536),
`task_type=RETRIEVAL_QUERY`, same env-var auth, plus a per-session cache so fallback
languages on identical text don't re-embed. Document side and query side are symmetric
and unit-tested (`tests/test_vector.py::GeminiEmbeddingContractTests`) — that is the hard
requirement; nothing is mixed across providers, and `connect()` refuses a collection whose
recorded provider/dims differ from the embedder it was asked to use.

**How the task signal is carried (implemented, not aspirational):** an earlier revision of
this document described text templates (`title: {title} | text: {passage}` for documents,
`task: question answering | query: {question}` for queries) and a `gemini_document_text()`
helper. **That helper does not exist in this repository and never shipped.** What the code
actually does:

- the task signal goes through the API's own `task_type` field (`RETRIEVAL_DOCUMENT` /
  `RETRIEVAL_QUERY`), which is what the embedding API is designed for;
- document scope is carried by the `"{product} — {section}"` breadcrumb that
  `format_passage()` prepends to the embedded text only — the stored document stays
  verbatim, so nothing invented can reach a citation;
- the offline `multilingual-e5-small` path uses the prefixes that model was trained with
  (`passage: ` / `query: `), applied on the matching side each.

If the team decides to switch to text templates instead of `task_type`, that is a
one-function change (`format_passage` + `_gemini_embedder`) — but it must be re-embedded
end to end, and the `corpus_digest` will refuse to serve a half-migrated index.

**Stale-index guard:** `ingest/embed_index.py` stamps the collection with a digest of the
chunk ids it embedded; `ChromaRetriever.connect(corpus_ids=...)` (wired from
`SearchEngine.from_build`) compares it against the corpus actually loaded and raises
`VectorStoreUnavailable` on mismatch. This matters for faithfulness, not just hygiene: a
dense-only hit is served from the vector store's own copy of the text
(`SearchEngine._chunk_view`), so a stale index could put words into a cited answer that
exist in no current manual. Indexes built before the stamp have no digest and are still
accepted.

Status honestly stated: the contract above is unit-tested against a fake client and against
real Chroma for storage/dimensions, but the live Gemini API has **not** been exercised end
to end from this repository (no key). Run `python -m ingest.embed_index --provider gemini`
and then `python -m answer.evaluate --vector` to measure the dense layer on answers, not
just on retrieval.

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

8. Server check: `uvicorn api.app:app`, then `curl -s localhost:8000/health` →
   `"vector_active": true` and `"provider": {"provider": "gemini", ...}`. Ask one question
   in the chat and re-check: a failing embedding call flips `vector_active` to `false` and
   puts the error in `vector_detail` (full table in `api.md` → *Live end-to-end check*).

**Resumability:** the indexer checkpoints to Chroma after every batch, paces itself under
the free-tier tokens-per-minute budget (`--tpm-limit`, default 25,000) and honours
`Retry-After`. A crash or quota stop mid-run is resumed by running the same command again —
already-stored chunk ids are skipped; `--reset` drops the collection and starts over.

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
| Dense vs BM25 vs hybrid Pass@8 / MRR (28-question `eval/questions.jsonl`) | pending — keyword-only today: 100% / 0.95 |

## 8. Limitations

Production embedding quality is unproven until §5 is executed (templates are implemented and
unit-tested but unverified against the live API); the benchmark is
keyword-matching on a small set and does not prove answer correctness; figure PNG pixels
are not embedded; Google free-tier terms allow input use for product improvement (public
docs, fine); the answer composer (`answer/`) consumes what this database retrieves, but its
Gemini path is likewise unexercised against the live API until the run in §5.
