# Stack Decisions — Data Processing, Embeddings, RAG Framework, Vector DB

> Answers to the team's project questions (dataset ~600 MB, PDFs/TXT/images,
> beginner experience, **free tools only**). Companion to
> [pipeline-review.md](pipeline-review.md).

## 0. First, an important correction: RAG does **not** "train" anything

Nothing in a RAG system is trained on your data. The embedding model and the
LLM come **pre-trained**; your manuals are only:

1. **cleaned + chunked** (data processing — already built in `ingest/`),
2. **embedded + indexed** (each chunk turned into a vector and stored),
3. **retrieved + fed to the LLM** at question time ("answer only from this").

This is great news: no GPU, no training cost, no ML expertise needed, and the
whole thing stays free.

## 1. Embedding model — `gemini-embedding-2` is a good pick, but make it pluggable

`gemini-embedding-2` is GA since April 2026: multilingual (perfect for EN/DE/IT),
8,192-token input, 128–3,072 output dimensions (Matryoshka — you can store 1,536
dims without retraining), top-tier retrieval quality. Free tier exists (rate
limits ~1,500 requests/day; free-tier input may be used to improve Google's
products — fine here, robot manuals are public documents).

**Two cautions for a hackathon:**

- A live demo must not die on API rate limits or missing Wi-Fi.
- Free tier has request-per-day limits — embedding ~10–15k chunks must be batched.

**Recommendation: pluggable providers, `local` default, `gemini` as the quality option:**

| Provider | Model | Free? | Needs key? | When to use |
|---|---|---|---|---|
| `local` (default) | `intfloat/multilingual-e5-small` | ✅ forever | ❌ | Demos, development, offline |
| `gemini` | `gemini-embedding-2` (1536 dims) | ✅ free tier | GEMINI_API_KEY | Best quality, when online with a key |

This is already implemented in `ingest/embed_index.py` (`--provider local|gemini`).
Both are strong for German/Italian/English. E5 note: queries need the prefix
`query: ` (the script handles the `passage: ` side for indexing).

## 2. RAG framework — **LlamaIndex** (for phase 3), plain Python for ingestion

| Option | Beginner-friendly | Verdict |
|---|---|---|
| **LlamaIndex** | ✅ very (10 lines to a working query engine) | **Recommended** for retrieval + answering: built-in hybrid retriever (BM25 + vectors with score fusion), citation-aware query engine, dozens of tutorials |
| LangChain | ⚠️ steeper, more moving parts | Fine alternative; bigger ecosystem but more boilerplate |
| Haystack | ⚠️ medium | Nice pipelines, fewer beginner tutorials |
| No framework | ❌ more code | Most defensible, most work — we avoid it where it doesn't pay |

**Our split (deliberate, and a good story to defend):**

- **Ingestion (clean + chunk) = plain Python** in `ingest/` — our markdown has
  structure (page markers, figures with labels, tables) that generic splitters
  mangle, and stdlib code is easy for the whole team to read and test.
- **Retrieval + answering (phase 3) = LlamaIndex** — hybrid retrieval, reranking,
  and grounded answer generation with citations are where a framework saves days.

## 3. Vector database — **Chroma (embedded)**

First, size reality: 600 MB is mostly **PDFs and images**. The actual markdown
text is maybe 10–20 MB → roughly 10–15k chunks. Any vector DB handles that in
its sleep — we are choosing for **simplicity**, not scale.

| Option | Runs where | Beginner-friendly | Verdict |
|---|---|---|---|
| **Chroma** | embedded in the app, zero setup | ✅ | **Recommended** — pip install, metadata filtering (by language/product/chunk_type), persists to a folder |
| FAISS (library) | in-process | ⚠️ | Fast, but no built-in metadata filtering or persistence |
| LanceDB | embedded | ✅ | Good alternative if we outgrow Chroma |
| Qdrant | Docker server | ⚠️ | The "serious" choice; overkill for a hackathon |
| pgvector | Postgres server | ⚠️ | Only if we already run Postgres |

Chroma is already wired in `ingest/embed_index.py`. Because we go through
LlamaIndex later, swapping the store is a few lines if needed.

## 4. Development environment (my call)

- **Python 3.11+** with a virtual environment (`python -m venv .venv`) — runs on
  any laptop, CPU-only, no GPU needed.
- **VS Code** + **Git/GitHub** for the team (this repo; work on
  `arena/cb500270-robog`, never `main`).
- Everything local and free; no Docker required for development.
- Free deploy options when we demo (later): Streamlit Community Cloud,
  Hugging Face Spaces, or Render free tier.

One more decision we still owe: the **generation LLM** (who writes the answer).
Free options: **Gemini Flash via Google AI Studio free tier** (same key as the
Gemini embeddings — easiest), or **Ollama** running a small model locally
(completely offline). We'll lock this in when we build the answering phase.

## 5. What this means for the 600 MB dataset

- We ingest only the **markdown** (`en.md` / `de.md` / `it.md`) — it is already
  distilled from the PDFs (see [data/README.md](../data/README.md)).
- **Images** stay on disk and are referenced from `chunk_type="figure"` chunks —
  that's how the final answer returns "the relevant image".
- **PDFs** are kept for source verification only; never ingested.
- TXT files (if any appear) can be added to the discovery list in
  `ingest/run.py` with one line.

## 6. Status: data processing is DONE and tested

```bash
python -m unittest tests.test_ingest -v        # 15 tests, all green
python -m ingest.run --data data --out build/chunks.jsonl   # once the dataset is in data/
pip install -r requirements-rag.txt            # only needed for phase 2
python -m ingest.embed_index --provider gemini # or --provider local
```

Chunks come out as JSONL with full citation metadata
(`title`, `language`, `section`, `page`, `chunk_type`, `image`) — exactly what
the "preserve source metadata" box of the pipeline needs.
