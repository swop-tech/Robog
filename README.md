# Robog

Conversational access to robot documentation — hackathon project for the **Telmekom Challenge 2026**.

Turn buried robot docs (manuals, datasheets, specs, accessories guides) into accurate, clear,
source-grounded answers for both technicians and non-technical users.

- 📄 Challenge analysis & suggested path: [`docs/challenge-analysis.md`](docs/challenge-analysis.md)
- 🔍 Review of the team's pipeline diagram: [`docs/pipeline-review.md`](docs/pipeline-review.md)
- 🧰 Stack decisions (embeddings, RAG framework, vector DB): [`docs/stack-decisions.md`](docs/stack-decisions.md)
- 📦 Dataset layout: [`data/README.md`](data/README.md)
- 🎯 Approach: hybrid RAG + structured parameter index, retrieval engine exposed as an API
  (Bonus 01), multilingual ask/answer (Bonus 02), web chat as the required interface.

## Stack (all free)

| Layer | Choice |
|---|---|
| Data processing | Plain Python (`ingest/`), no dependencies |
| Embeddings | `gemini-embedding-2` (free tier) or `multilingual-e5-small` (offline) — pluggable |
| Vector DB | Chroma (embedded) |
| RAG framework | LlamaIndex (retrieval + answering, phase 3) |
| Environment | Python 3.11+, VS Code, Git — any laptop, CPU-only |

## Data processing (phase 1 — done, tested)

```bash
# run the tests (15 tests, stdlib only)
python -m unittest tests.test_ingest -v

# clean + chunk the manuals -> build/chunks.jsonl
# (the dataset lives in data/ — see data/README.md)
python -m ingest.run --data data --out build/chunks.jsonl
```

Every chunk carries citation metadata: `title`, `language`, `section`, `page`,
`chunk_type` (text | table | figure), `image`. Figures and tables get their own
chunks so answers can return exact parameter values and the relevant image.

## Embedding + indexing (phase 2)

```bash
pip install -r requirements-rag.txt
python -m ingest.embed_index --provider local    # offline, no key
python -m ingest.embed_index --provider gemini   # needs GEMINI_API_KEY (free)
```

*Work in progress on branch `arena/1cc436fc-robog`.*
