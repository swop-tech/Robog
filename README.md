# Robog

Conversational access to robot documentation — hackathon project for the **Telmekom Challenge 2026**.

Turn buried robot docs (manuals, datasheets, specs, accessories guides) into accurate, clear,
source-grounded answers for both technicians and non-technical users.

- 📄 Challenge analysis & suggested path: [`docs/challenge-analysis.md`](docs/challenge-analysis.md)
- 🔍 Review of the team's pipeline diagram: [`docs/pipeline-review.md`](docs/pipeline-review.md)
- 🧰 Stack decisions (embeddings, RAG framework, vector DB): [`docs/stack-decisions.md`](docs/stack-decisions.md)
- 🧹 Data processing (clean, chunk, metadata, parameter index): [`docs/data-processing.md`](docs/data-processing.md)
- 🔎 Retrieval engine (routing, language policy, evaluation): [`docs/retrieval.md`](docs/retrieval.md)
- 📦 Dataset layout: [`data/README.md`](data/README.md)
- 🎯 Approach: hybrid RAG + structured parameter index, retrieval engine exposed as an API
  (Bonus 01), multilingual ask/answer (Bonus 02), web chat as the required interface.

## Stack (all free)

| Layer | Choice |
|---|---|
| Data processing | Plain Python (`ingest/`), no dependencies |
| Retrieval | Plain Python (`retrieve/`): BM25 + parameter index + language policy, no dependencies |
| Embeddings | `gemini-embedding-2` (free tier) or `multilingual-e5-small` (offline) — pluggable |
| Vector DB | Chroma (embedded) |
| RAG framework | LlamaIndex (retrieval + answering, phase 3) |
| Environment | Python 3.11+, VS Code, Git — any laptop, CPU-only |

## Data processing (phase 1 — done, tested on the real dataset)

```bash
# run the tests (40 tests, stdlib only)
python -m unittest tests.test_ingest -v

# clean + chunk the manuals -> build/chunks.jsonl
#   + parameter index      -> build/params.jsonl
#   + validation report    -> build/data-report.md   (non-zero exit on errors)
python -m ingest.run --data data
```

**On the real dataset: 63 markdown files → 1,351 chunks (706 text · 169 table · 476 figure)
and 2,039 structured parameter rows. Validation: 0 errors, 1 warning.**
Details, metadata schema and the quirks handled: [`docs/data-processing.md`](docs/data-processing.md).

Every chunk carries its full citation trail: `doc_id`, `product`, `doc_type`
(robot | accessory), `title`, `language`, `source_pdf`, `section`, `section_number`,
`page` / `pdf_page`, `chunk_type` (text | table | figure) and — for figures — `image`.
Figures and tables get their own chunks so answers can return exact parameter values
and the relevant image; tables additionally become rows in the parameter index
(`product → parameter → value/unit → source page`) for exact technician lookups.

## Retrieval engine (keyword baseline and optional dense layer)

```bash
python -m ingest.run --data data                          # build the local indexes first
python -m retrieve "Wieviel Zuladung hat der FlashBot?"   # standalone CLI (Bonus 01)
python -m retrieve.evaluate                               # 20-question benchmark
python -m unittest tests.test_retrieve -v                 # 37 tests
```

Keyword retrieval with **no embedding model and no dependencies**: BM25 over the chunks, the
structured parameter index for exact values, a domain lexicon (payload ≈ Zuladung ≈ carico) and a
per-question language policy (user's language first, cross-lingual fallback for the manuals
without one). On a small 20-question keyword-matching benchmark (EN/DE/IT, both personas):

| Configuration | Pass@8 | Hits in question language | Cross-language duplicates |
|---|---|---|---|
| every language at once (naive RAG) | 100% | 68% | 12% |
| user's language only (hard filter) | 85% | 85% | 0% |
| **same language first, fallback** | **100%** | **85%** | **0%** |

These checks do not establish answer correctness; advisory cases only check product diversity.
Details: [`docs/retrieval.md`](docs/retrieval.md).

## Embedding + indexing (phase 3 — dense layer)

```bash
pip install chromadb google-genai                # or: pip install -r requirements-rag.txt
cp .env.example .env                             # add GEMINI_API_KEY=... (git-ignored)
python -m ingest.embed_index --provider gemini   # embeds 1,351 chunks via Gemini API -> build/chroma
python -m retrieve "robot won't start" --vector  # auto-detects Gemini provider & dims from Chroma

# Offline alternative (no API key; downloads multilingual-e5-small once):
python -m ingest.embed_index --provider local --reset
```

The dense path is optional and requires a compatible built index and embedding provider.
The local model must be downloaded before it can run offline. End-to-end embedding quality
has not been validated by the keyword-only benchmark above. The API and answer composer remain work in progress.

## Known gap: images are indexed but not yet viewable

All 476 figures are indexed as their own chunks with a resolvable `image` path and a `Labels:`
legend, so retrieval already selects the right picture — but there is no media endpoint, no
"show me a picture of X" route, and no UI that renders it. Deferred on purpose, specced in
[`docs/pipeline-review.md` §9a](docs/pipeline-review.md#9a-deferred-making-images-actually-usable-g6)
(media endpoint + figure route + language-correct image choice), to be built together with the API.
