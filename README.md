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

## Retrieval engine (phase 2 — done, measured)

```bash
python -m retrieve "Wieviel Zuladung hat der FlashBot?"   # standalone CLI (Bonus 01)
python -m retrieve.evaluate                               # 20-question benchmark
python -m unittest tests.test_retrieve -v                 # 37 tests
```

Hybrid retrieval with **no embedding model and no dependencies**: BM25 over the chunks, the
structured parameter index for exact values, a domain lexicon (payload ≈ Zuladung ≈ carico) and a
per-question language policy (user's language first, cross-lingual fallback for the manuals
without one). Measured on 20 questions (EN/DE/IT, both personas):

| Configuration | Recall@8 | Hits in question language | Cross-language duplicates |
|---|---|---|---|
| every language at once (naive RAG) | 100% | 68% | 12% |
| user's language only (hard filter) | 85% | 85% | 0% |
| **same language first, fallback** | **100%** | **85%** | **0%** |

Details: [`docs/retrieval.md`](docs/retrieval.md).

## Embedding + indexing (phase 3 — dense layer)

```bash
pip install -r requirements-rag.txt
python -m ingest.embed_index --provider local    # offline, no key
python -m ingest.embed_index --provider gemini   # needs GEMINI_API_KEY (free)
```

*Work in progress: data processing is complete; embeddings + hybrid retrieval are next.*
