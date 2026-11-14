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
| Retrieval | Plain Python (`retrieve/`): BM25 + parameter index + product profile cards + figure route + language policy, no dependencies |
| Embeddings | `gemini-embedding-2` (free tier) or `multilingual-e5-small` (offline) — pluggable |
| Vector DB | Chroma (embedded) |
| RAG framework | LlamaIndex (retrieval + answering, phase 3) |
| Environment | Python 3.11+, VS Code, Git — any laptop, CPU-only |

## Data processing (phase 1 — done, tested on the real dataset)

```bash
# run the tests (stdlib only)
python -m unittest tests.test_ingest -v

# clean + chunk the manuals -> build/chunks.jsonl
#   + parameter index      -> build/params.jsonl
#   + product profiles     -> build/profiles.jsonl
#   + validation report    -> build/data-report.md   (non-zero exit on errors)
python -m ingest.run --data data
```

**On the real dataset: 63 markdown files → 1,351 chunks (706 text · 169 table · 476 figure),
2,039 structured parameter rows, and 38 localized product profile cards across all 13 robots.
Validation: 0 errors, 1 warning.**
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
python -m retrieve "Show me a picture of the BellaBot"    # figure route (G6)
python -m retrieve "Which robot fits my restaurant?"      # advisory + product profile cards (G2)
python -m retrieve.evaluate                               # 28-question benchmark
python -m unittest discover -s tests -p "test_*.py"       # 129 tests
```

Keyword retrieval with **no embedding model and no dependencies**: BM25 over the chunks, the
structured parameter index for exact values, structured product profile cards across all 13 robots
for advisory/comparison queries, a `figure` route for visual requests, a domain lexicon
(payload ≈ Zuladung ≈ carico), and a per-question language policy (user's language first,
cross-lingual fallback for the manuals without one). On the 28-question benchmark (EN/DE/IT,
lookup + figure + procedure + advisory):

| Configuration | Pass@8 | MRR | Hits in question language | Cross-language duplicates | Param index hit | Profile card hit |
|---|---|---|---|---|---|---|
| every language at once (naive RAG) | 100% | 0.92 | 52% | 18% | 100% | 100% |
| user's language only (hard filter) | 82% | 0.82 | 86% | 0% | 80% | 86% |
| **same language first, fallback** | **100%** | **0.95** | **85%** | **0%** | **100%** | **100%** |

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

## Known gap: images are retrieved with language-correct paths, not yet served over HTTP

All 476 figures are indexed as their own chunks with a resolvable `image` path and parsed `labels`
legend, and the `figure` route (`G6`) now selects language-appropriate overview/callout diagrams in
EN, DE, and IT (falling back to EN when a manual lacks Italian). Still pending in the next step:
the FastAPI media endpoint (`GET /media/...`) and UI rendering, specced in
[`docs/pipeline-review.md` §9a](docs/pipeline-review.md#9a-deferred-making-images-actually-usable-g6).
