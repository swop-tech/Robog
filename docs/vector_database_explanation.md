# The vector database, explained from zero

> Audience: teammates, judges, and future contributors who are **not** ML engineers.
> Everything here describes this repository exactly as it exists — paths, model names and
> numbers are not generic examples.

---

## 1. What is an embedding?

A computer cannot compare two sentences by "meaning". So we translate each piece of text into
a **list of numbers** — a vector. An embedding model is a neural network trained so that
texts with similar meaning get vectors that point in similar directions.

```
"The FlashBot carries 20 kg."        → [0.021, -0.118, 0.554, ...]   (e.g. 1,536 numbers)
"Wie viel trägt der FlashBot?"      → [0.019, -0.121, 0.548, ...]    (very similar direction)
"BellaBot charging voltage"         → [-0.402, 0.077, -0.290, ...]   (different direction)
```

Two vectors are compared with **cosine similarity**: 1.0 = same direction, 0 = unrelated.
The model does the counting; we never see the individual numbers.

No training happens in our project. The embedding models come **pre-trained**; we only *use* them.

## 2. Why do we embed chunks, not whole documents?

Our PDFs are far too large to embed as one piece, and vague either way — a whole manual has no
single meaning. So ingestion (`ingest/`) first cuts every manual into **chunks**: paragraphs,
table rows and figure explanations. Each chunk is a small, self-contained fact with an address:

```json
{
  "id": "flashbot_user_manual_v1.0-en-p005-c0012",
  "text": "Load capacity: 20 kg per tray ...",
  "metadata": {
    "product": "FlashBot", "language": "en", "doc_type": "robot",
    "title": "FlashBot User Manual V1.0", "section": "2.3", "section_number": "2.3",
    "section_title": "Technical Specifications", "page": 5, "pdf_page": 5,
    "chunk_type": "table", "source_pdf": "FlashBot_User_Manual_V1.0.pdf",
    "translation_group": "tg-…"
  }
}
```

1,351 chunks in total (706 text · 169 table · 476 figure). Embeddings are computed **once per
chunk** — not at question time.

## 3. What Gemini Embedding 2 does (and the offline alternative)

Per `ingest/embed_index.py` and `docs/stack-decisions.md`, two free options are supported —
nothing else, and no paid services:

| Provider | Model | Output | Needs | When to use |
|---|---|---|---|---|
| `--provider local` (default) | `intfloat/multilingual-e5-small` | 384 dims | nothing — runs offline, no key | demos, laptops, no internet |
| `--provider gemini` | `gemini-embedding-2` | 1,536 dims (Matryoshka; 128–3072 possible) | `GEMINI_API_KEY` env variable (free Google AI Studio tier) | highest quality |

Facts worth repeating:

- **Both are multilingual** — comfortable with EN/DE/IT, which is why these manuals
  (German/English source editions, Italian partner edition) embed well.
- **e5 was trained with asymmetric prefixes**: documents are embedded with the prefix
  `passage: ` (the ingest script does this) and questions **must** be embedded with
  `query: ` (the retrieval engine does this in `retrieve/vector.py`). Mixing them up
  quietly destroys retrieval quality, so the code keeps the two sides explicitly matched.
- **gemini-embedding-2 uses official question-answering templates in the text itself**
  (no legacy `task_type`): documents are embedded as
  `title: {title} | text: {text}` (`title: none` when a chunk has no title) and
  questions as `task: question answering | query: {question}`. Both sides are
  implemented and tested (`ingest/embed_index.py: gemini_document_text`,
  `retrieve/vector.py: gemini_query_text`); image pixels are never embedded.
- Vectors are **normalized** before storage (cosine space).
- Ingestion happens in **batches** (100 texts per call) — ~14 calls for 1,351 chunks,
  comfortably inside the free tier (~1,500 requests/day as documented by the team).
- We embed only the chunk `text`. Metadata (product, page, …) is stored alongside,
  not inside, the vector.

## 4. What is ChromaDB?

ChromaDB is an open-source **vector database**: think of it as a search engine for "most
similar vector" with a side of traditional storage.

- **Embedded mode** (what we use): no server, no account, no ports. It is a **folder on disk**.
  Our folder is `build/chroma/` and our collection is `robog`.
- It stores: the **id**, the **vector**, the **text** and the **metadata** of every chunk,
  and answers queries of the form "give me the k nearest vectors to this one".
- It persists across restarts — we reopen the folder and everything is still there.
- Crucially for us, it supports **metadata payloads** on every record, so the citation
  (product, section, page, language) travels with the vector.

`build/chroma/` is **git-ignored**. It is a build artifact, like the chunks: every teammate
regenerates it locally (see §"Building the database yourself" below). We never commit it.

## 5. What exactly is stored for each chunk?

For every one of the 1,351 chunks, ChromaDB holds exactly one record:

| Field | Content | Key for |
|---|---|---|
| `id` | the chunk id from `build/chunks.jsonl` (unique; verified: 1,351 unique ids, 0 invalid rows) | dedup, traceability |
| `embedding` | 1,536 (gemini) or 384 (local e5) floats | semantic search |
| `document` | the chunk's raw text | showing evidence |
| `metadatas` | product, doc_type, title, language, section(+number), page / pdf_page, chunk_type (text/table/figure), `source_pdf`, `translation_group`, `image` (figures only) | filters, citations, dedup |

Metadata values are stored only if they are `str/int/float/bool` (ChromaDB's rule) — the
indexer drops everything else (e.g. `null` section numbers) gracefully, it does not crash.

**Tables and figures get first-class chunks**, not footnotes: a table chunk contains the
markdown table text; a figure chunk contains the caption + the `Labels:` legend of the image
and an `image` path resolvable on disk. That is how the system can point at "Figure 3.2,
page 17" instead of paraphrasing it.

## 6. How a question becomes a query embedding

At question time (offline path: `python -m retrieve "…" --vector`, or `SearchEngine(...,
vector="auto")` from code):

1. The user question (EN/DE/IT) is tokenized and its **language is detected**
   (`retrieve/lang.py`, deterministic, no model).
2. The question is embedded with the **same model and dimensionality used for indexing** —
   this symmetry is mandatory, otherwise "distance" between vectors is meaningless.
   e5: prefix with `query: `. Gemini: use `task: question answering | query: {question}`.
3. That vector is handed to ChromaDB: "nearest neighbours, please".

Query embeddings are **cached per session**, so asking in German after English (the
cross-lingual fallback) does not pay for two embedding calls if the text is identical.

## 7. How dense retrieval finds the right chunks

`retrieve/vector.py` (`ChromaRetriever`) asks ChromaDB for the nearest chunks
(`hnsw:space: cosine`) and converts distance to score: `1 − distance`.

Two design points worth knowing:

- **Over-fetch + expand**: a fixed "fetch k×2" can return zero *eligible* rows when a query
  targets a small slice (e.g. only German manuals). Instead we expand the global candidate
  set until we have enough allowed rows or the collection is exhausted (PR #9 hardening).
- **Policy-aware by construction**: `search()` takes `allowed_ids` — the exact same
  language/product allow-list the BM25 side must respect — so the language policy holds
  uniformly. A German question never "leaks" English hits on tier 1 just because the
  embedding model thinks they are similar.
- **Read-only, always**: the collection is opened with `get_collection`, never
  `get_or_create_collection`, and nothing is ever upserted from the retrieval side. There is
  exactly one vector database (Sujal's) — no parallel/competing store, no writes, ever.
- If anything is missing (folder, collection, deps, network, key), the failure is an
  actionable `VectorStoreUnavailable` message — and the engine falls back to keyword
  retrieval with a printed warning instead of crashing the demo.

## 8. BM25 + dense: why two eyes are better than one

**BM25** (keyword) is exact and brittle: perfect for `Notausschalter`, `20 kg`, product codes,
terrible at `"the robot won't start" ↔ "power-on failure"`.
**Vector search** is semantic and fuzzy: perfect for paraphrase and cross-lingual phrasing,
weak at rare exact tokens and numbers.

Their overlap is not a bug — it is the signal. When both agree, the chunk is almost certainly
right; when only one speaks, we still get a fair vote.

| | BM25 (`retrieve/bm25.py`) | Dense (`retrieve/vector.py`) |
|---|---|---|
| Matches | exact words | meaning |
| Fails on | synonyms/paraphrase | rare tokens, numbers, codes |
| Needs | nothing (offline, stdlib) | the embedding pipeline + ChromaDB |
| Speed | milliseconds | fast (local model or 1 API call) |

## 9. RRF — the voting system (`retrieve/engine.py`)

Scores from different retrievers live on different scales (BM25 unbounded, cosine ∈ [0,1]),
so we fuse **ranks**, not scores:

```
score(chunk) = Σ over retrievers  1 / (60 + rank_in_that_list)
```

- Strict BM25 list: weight 1.0 · lexicon-expanded BM25: 0.45 · dense: 1.0 · parameter index
  chunks: 1.0 (same constant, `RRF_K = 60`).
- A chunk found by both ranks near the top beats a single-source top hit: fusion rewards
  **agreement**.
- Duplicate chunks are impossible by construction (one score per chunk id).
- Each hit carries `sources` (`["bm25", "vector", …]`) — the UI can show which engine voted.
- Then: translation de-duplication (parallel EN/DE/IT editions of the same manual, via
  `translation_group`), advisory diversification (max 2 chunks per product so comparison
  questions can compare), and top-k with reassigned ranks.

## 10. How the retrieval side looks end to end

```
question (EN / DE / IT)
  → language detection (deterministic)          retrieve/lang.py
  → query routing: lookup | advisory | default retrieve/router.py
  → BM25 strict pass + lexicon pass             retrieve/bm25.py, retrieve/lexicon.py
  → parameter index (exact values)              retrieve/params.py   (build/params.jsonl, 2,039 rows)
  → dense pass (if ChromaDB built)              retrieve/vector.py
  → Reciprocal Rank Fusion                      retrieve/engine.py
  → dedupe translations / diversify / top-k     (same)
  → SearchResult: chunks + citations + confidence + language notes
```

Citations never travel separately from chunks: every chunk carries
`product · §section_number title · page · language`, so the future answer composer can only
cite what retrieval actually found — the hook we rely on to fight hallucination (review gap G5).

## 11. How sources and pages survive all the way to the answer

1. Ingestion reads the `<!-- page: N -->` markers in the converted markdown and stores
   `page` (and `pdf_page`), `source_pdf`, `title`, `section`/`section_number`, `chunk_type`,
   and for figures the `image` path — **before** any embedding happens.
2. ChromaDB stores those metadata fields verbatim.
3. Retrieval never drops them: `Hit.citation` renders e.g.
   `FlashBot User Manual V1.0, §2.3 Technical Specifications, p. 5, EN`.
4. `to_dict()` serializes everything (text, score, sources, metadata, params) — the contract
   `api/` will consume; nothing is re-derived or guessed downstream.

## 12. Honest limitations (say these out loud)

- **No measured production run is recorded yet** — `build/chroma` is built per machine by
  `ingest/embed_index.py` and never committed, and no run against the live Gemini API has
  had its numbers written down (`gemini_chromadb_explanation.md` §7). Everything dense so far (including the real-Chroma
  integration test) used deterministic test embeddings or was validated against
  builder-local stores; end-to-end *embedding quality* is unproven until the indexer runs.
- The 28-question benchmark is **keyword-matching based**, factual and small. 100% Pass@8
  means the harness's expectations pass — it does **not** prove the chatbot's answers are
  correct. `python -m answer.evaluate` checks grounding and numbers on the same questions
  with the offline composer; neither is a user study.
- Multilingual synonyms rely on a small curated lexicon; missing terms degrade to prefix
  matching or the vector side.
- Topic coverage = corpus coverage: only the 22 PUDU manuals (EN/DE/IT). The dataset is
  documentation-heavy robotics, not telecom troubleshooting records.
- Figures are retrieved by a dedicated route and rendered in the web chat, but the PNG
  pixels are not embedded — a figure is found through its caption and surrounding text.
- Advisory answers are built from product profile cards derived from the manuals; they
  compare what the manuals state, not real-world suitability.
- Gemini free-tier limits (~1,500 req/day, and free input may be used by Google for
  model improvement — fine for public manuals, not for confidential data).
- If both retrievers fail (e.g. only tables match), the parameter index may outrank the
  focused procedure — fusion weights may be tuned once the real index exists.

## Building the database yourself (10 minutes, once per machine)

```bash
pip install -r requirements-rag.txt
python -m ingest.run --data data                       # rebuild chunks + params (optional, build/ is git-ignored)
python -m ingest.embed_index --provider local         # free, offline, no key — recommended first
# …or, with a free AI Studio key (create it yourself, keep it out of git):
export GEMINI_API_KEY=...
python -m ingest.embed_index --provider gemini
```

Expected last line: `Indexed 1351 chunks into build/chroma (collection 'robog').`

Then verify it is real:

```bash
python -m retrieve "Wieviel Zuladung hat der FlashBot?" --vector   # look for [bm25+vector] sources
python -m unittest discover -q                                    # real-Chroma integration test now runs, not skips
```

Rebuilding is safe and idempotent (`upsert` by chunk id, same input ⇒ same ids). To start
truly clean, delete `build/chroma/` first and re-run — it regenerates deterministically from
`data/`. **Never commit `build/`**; every teammate runs the two commands. If you want Gemini
vectors you must use Gemini to index *and* to query (`ROBOG_EMBED_PROVIDER=gemini`); mixing
providers makes vectors meaningless.

## Configuration reference

| Knob | Default | Meaning |
|---|---|---|
| `ROBOG_CHROMA_PATH` | `build/chroma` | persisted DB location |
| `ROBOG_CHROMA_COLLECTION` | `robog` | collection (must match `ingest/embed_index.py`) |
| `ROBOG_EMBED_PROVIDER` | `local` | `local` (e5-small, offline) or `gemini` |
| `ROBOG_EMBED_DIMS` | `1536` | Gemini output dims, 128–3072 |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | — | env-only; never in code, never in git |
