"""Phase 2: embed the chunks and store them in a vector database (Chroma).

Needs the extra dependencies (phase 1 needs none):

    pip install -r requirements-rag.txt

Both embedding providers are free -- pick one with --provider:

* ``local``  (default) -- intfloat/multilingual-e5-small via sentence-transformers.
  Runs offline on any laptop, no API key, no rate limits. Best for demos.
* ``gemini`` -- gemini-embedding-2 via the Google AI Studio free tier
  (key from https://aistudio.google.com, read from the GEMINI_API_KEY or
  GOOGLE_API_KEY environment variable). The free tier as shown in the
  maintainer's AI Studio project on 2026-11-13 is: 100 requests/minute,
  30,000 input tokens/minute and 1,000 requests/day. Google does not
  publish these numbers and they can change -- check your own project.

How the Gemini path works (text-only):

* It calls the *synchronous* ``batchEmbedContents`` endpoint through the
  google-genai SDK (``client.models.embed_content``). Each chunk is sent
  as its OWN Content (one content per text -- a list of raw strings would
  be folded by the SDK into one multipart content and return a single
  embedding per call). The endpoint returns one embedding per request, in
  order, and accepts *at most 100 requests per call* -- so we batch 100 at
  a time (14 calls for 1,351 chunks). No async Batch API is used.
* A token-aware throttle (default: at most 24,000 estimated input
  tokens per rolling 60-second window, ~20% headroom under the 30,000
  TPM free-tier limit) paces the batches instead of firing them blindly.
* Transient errors (429 / 5xx) are retried with exponential backoff and
  jitter. Fatal errors (400/401/403, e.g. bad model id, bad key,
  permission denied) abort immediately -- retrying cannot fix them.
* Checkpoint + resume: every embedded batch is appended to a checkpoint
  file (default ``build/embed_checkpoint.jsonl``; a header line records
  the sha256 of the chunks file plus the model and dims). If the run is
  interrupted, re-running the same command resumes where it stopped.
  If the chunks file changes, the checkpoint is rejected -- vectors are
  never mixed across different corpora.
* The Chroma collection is written only after *all* vectors are present,
  so an interrupted run never leaves a half-indexed collection.

Gemini Embedding 2 uses the official question-answering format for text-only
retrieval (inputs are NOT prefixed with the legacy ``task_type``; the
instruction lives in the text):

* documents: ``title: {title} | text: {text}``   (``title: none`` when absent)
* queries:   ``task: question answering | query: {question}``
  (applied on the search side, see ``retrieve/vector.py``)

The local e5 provider keeps its own ``passage: ``/``query: `` prefixes and is
unaffected. Both sides match their provider exactly -- mixing templates across
providers breaks the vectors.

Usage:

    python -m ingest.embed_index --chunks build/chunks.jsonl
    python -m ingest.embed_index --chunks build/chunks.jsonl --provider gemini

Smoke test (3-5 chunks, separate persist dir and collection so the real
collection is never touched; separate checkpoint too):

    python -m ingest.embed_index --provider gemini --limit 5 \\
        --persist build/chroma_smoke --collection robog_smoke \\
        --checkpoint build/embed_checkpoint_smoke.jsonl

NOTE for queries later: with the local e5 model, queries must be prefixed
"query: " and passages "passage: " (this script handles the passages; the
search side must add "query: " to the question). With gemini, the same rule
applies, using the two formats above.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from collections import deque
from pathlib import Path

# E5 models were trained with these prefixes -- they matter for quality.
_E5_QUERY_PREFIX = "query: "
_E5_PASSAGE_PREFIX = "passage: "

# Synchronous batchEmbedContents accepts at most 100 requests per call.
GEMINI_BATCH_SIZE = 100
# Conservative token budget per rolling 60s window (free tier: 30,000 TPM).
DEFAULT_TOKEN_BUDGET_PER_MIN = 24_000


def gemini_document_text(row: dict) -> str:
    """Official Gemini Embedding 2 document template (text-only question
    answering):

        title: {title} | text: {text}

    ``title: none`` is used when a chunk has no title. Figure chunks contribute
    their caption/label text here; the image pixels are never embedded.
    """
    title = (row.get("metadata") or {}).get("title") or "none"
    return f"title: {title} | text: {row.get('text', '')}"

GEMINI_MODEL = "gemini-embedding-2"
LOCAL_MODEL = "intfloat/multilingual-e5-small"


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (chars / 4) for throttling only -- never used
    for anything billing-related."""
    return max(1, len(text) // 4)


def _chunks_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_checkpoint(path: Path, *, chunks_sha256: str, model: str, dims: int) -> dict[str, list[float]]:
    """Return {chunk_id: vector} from a checkpoint file.

    Rejects checkpoints that were created for a different chunks file, a
    different model, or different dimensions -- vectors are never mixed
    across corpora or models.
    """
    if not path.exists():
        return {}
    vectors: dict[str, list[float]] = {}
    with path.open("r", encoding="utf-8") as fh:
        first = fh.readline()
        try:
            header = json.loads(first)
        except json.JSONDecodeError:
            raise SystemExit(f"Checkpoint {path} is corrupt (first line is not valid JSON). Delete it to re-embed.")
        meta = header.get("__meta__") if isinstance(header, dict) else None
        if not meta:
            raise SystemExit(f"Checkpoint {path} has no __meta__ header. Delete it to re-embed.")
        expected = {"chunks_sha256": chunks_sha256, "model": model, "dims": dims}
        for key, want in expected.items():
            got = meta.get(key)
            if got != want:
                raise SystemExit(
                    f"Checkpoint {path} was created for a different {key} "
                    f"({got!r} != {want!r}). Delete it to re-embed, or pass a fresh --checkpoint path."
                )
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            vectors[rec["id"]] = rec["vector"]  # last write wins (crash-safe append)
    return vectors


def append_checkpoint(path: Path, *, chunks_sha256: str, model: str, dims: int,
                      records: list[tuple[str, list[float]]]) -> None:
    """Append embedded (id, vector) pairs to the checkpoint, writing the
    header first when creating the file."""
    new_file = not path.exists()
    with path.open("a", encoding="utf-8") as fh:
        if new_file:
            fh.write(json.dumps({"__meta__": {"chunks_sha256": chunks_sha256,
                                              "model": model,
                                              "dims": dims}}) + "\n")
        for cid, vector in records:
            fh.write(json.dumps({"id": cid, "vector": vector}) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _is_fatal_error(exc: Exception) -> bool:
    text = str(exc)
    fatal_markers = ("400", "401", "403", "INVALID_ARGUMENT", "UNAUTHENTICATED",
                     "PERMISSION_DENIED", "not found", "404", "embeddings in response")
    return any(marker in text for marker in fatal_markers)


# --------------------------------------------------------------------------
# Embedding providers (both free)
# --------------------------------------------------------------------------
class LocalEmbedder:
    """multilingual-e5-small: free, offline, great for EN/DE/IT."""

    def __init__(self, model_name: str = LOCAL_MODEL):
        from sentence_transformers import SentenceTransformer  # heavy import

        self.model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(
            [_E5_PASSAGE_PREFIX + t for t in texts],
            normalize_embeddings=True,
            show_progress_bar=True,
        )
        return vectors.tolist()


class GeminiEmbedder:
    """gemini-embedding-2 via the Google AI Studio free tier.

    One ``embed_batch`` call = one synchronous ``batchEmbedContents`` API
    request returning one embedding per input text, in order.
    """

    def __init__(self, dims: int = 1536, model_name: str = GEMINI_MODEL):
        from google import genai  # light import

        if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
            raise SystemExit("Set the GEMINI_API_KEY environment variable (free key from Google AI Studio).")
        self.client = genai.Client()
        self.model_name = model_name
        self.dims = dims

    def embed_batch(self, batch: list[str]) -> list[list[float]]:
        """Embed one batch (<=100 texts) in a single synchronous
        batchEmbedContents call. Raises on failure -- retry logic lives in
        ``embed_gemini_resumable``.

        IMPORTANT (google-genai SDK, gemini-embedding-2): a list of raw
        strings is folded by ``t_contents`` into ONE multipart content
        (verified: ['a','b'] -> 1 content with 2 parts), which yields ONE
        embedding per call, not one per string. Passing one Content dict
        per text produces one request per text in batchEmbedContents,
        hence one embedding per chunk, in order.
        """
        if len(batch) > GEMINI_BATCH_SIZE:
            raise ValueError(f"batch of {len(batch)} exceeds the 100-request endpoint limit")
        contents = [{"role": "user", "parts": [{"text": t}]} for t in batch]
        response = self.client.models.embed_content(
            model=self.model_name,
            contents=contents,
            config={"output_dimensionality": self.dims},
        )
        vectors = [list(e.values) for e in response.embeddings]
        if len(vectors) != len(batch):
            raise RuntimeError(
                f"expected {len(batch)} embeddings in response, got {len(vectors)}")
        return vectors


def embed_gemini_resumable(
    rows: list[dict],
    embedder: GeminiEmbedder,
    *,
    checkpoint_path: Path,
    chunks_sha256: str,
    token_budget_per_min: int = DEFAULT_TOKEN_BUDGET_PER_MIN,
    batch_size: int = GEMINI_BATCH_SIZE,
    max_attempts: int = 6,
) -> list[list[float]]:
    """Embed all rows, checkpointing after every batch and resuming from it.

    Throttles to ``token_budget_per_min`` estimated input tokens per rolling
    60-second window and retries transient failures (429/5xx) with
    exponential backoff. Fatal errors abort immediately.
    """
    batch_size = min(batch_size, GEMINI_BATCH_SIZE)
    texts = [gemini_document_text(row) for row in rows]
    vectors_by_id = load_checkpoint(
        checkpoint_path, chunks_sha256=chunks_sha256,
        model=embedder.model_name, dims=embedder.dims)
    if vectors_by_id:
        already = sum(1 for r in rows if r["id"] in vectors_by_id)
        print(f"Checkpoint {checkpoint_path}: resuming -- {already}/{len(rows)} chunks already embedded")

    todo = [i for i, row in enumerate(rows) if row["id"] not in vectors_by_id]
    window: deque[tuple[float, int]] = deque()

    for chunk_start in range(0, len(todo), batch_size):
        idxs = todo[chunk_start:chunk_start + batch_size]
        batch = [texts[i] for i in idxs]
        batch_tokens = sum(_estimate_tokens(t) for t in batch)

        # Token-aware throttle: never let the trailing 60s of estimated
        # input tokens exceed the budget.
        while True:
            now = time.monotonic()
            while window and now - window[0][0] >= 60:
                window.popleft()
            used = sum(tokens for _, tokens in window)
            if used + batch_tokens <= token_budget_per_min or not window:
                break
            sleep_s = 60 - (now - window[0][0]) + 0.5
            print(f"  throttle: ~{used} tokens in the last 60s; sleeping {sleep_s:.0f}s "
                  f"(budget {token_budget_per_min}/min)")
            time.sleep(sleep_s)

        ids = [rows[i]["id"] for i in idxs]
        batch_no = chunk_start // batch_size + 1
        total_batches = (len(todo) + batch_size - 1) // batch_size
        response_vecs = None
        for attempt in range(1, max_attempts + 1):
            try:
                response_vecs = embedder.embed_batch(batch)
                break
            except Exception as exc:
                if _is_fatal_error(exc):
                    raise SystemExit(
                        f"Fatal API error on batch {batch_no} (not retrying): "
                        f"{type(exc).__name__}: {exc}\n"
                        "Check the model id, the API key environment variable and your project's quota.")
                if attempt == max_attempts:
                    raise SystemExit(
                        f"Batch {batch_no} failed after {max_attempts} attempts; "
                        f"last error: {type(exc).__name__}: {exc}. "
                        f"Re-run the same command to resume from the checkpoint ({checkpoint_path}).")
                wait = min(2 ** (attempt - 1) * 5, 60) + random.uniform(0, 2)
                print(f"  batch {batch_no}/{total_batches}: attempt {attempt} failed "
                      f"({type(exc).__name__}: {exc}); retrying in {wait:.0f}s")
                time.sleep(wait)
        assert response_vecs is not None
        append_checkpoint(checkpoint_path,
                          chunks_sha256=chunks_sha256,
                          model=embedder.model_name, dims=embedder.dims,
                          records=list(zip(ids, response_vecs)))
        for cid, vec in zip(ids, response_vecs):
            vectors_by_id[cid] = vec
        window.append((time.monotonic(), batch_tokens))
        done = sum(1 for r in rows if r["id"] in vectors_by_id)
        print(f"  embedded {done}/{len(rows)} (batch {batch_no}/{total_batches}, "
              f"~{batch_tokens} tokens, checkpoint: {checkpoint_path})")

    try:
        vectors = [vectors_by_id[row["id"]] for row in rows]
    except KeyError as exc:  # pragma: no cover - defensive
        raise SystemExit(f"Missing embedding for chunk {exc.args[0]} after resume; delete {checkpoint_path} and re-embed.")
    for row, vec in zip(rows, vectors):
        if len(vec) != embedder.dims:
            raise SystemExit(f"Chunk {row['id']}: expected {embedder.dims}-dim vector, got {len(vec)}. "
                             f"Delete {checkpoint_path} and re-embed.")
    return vectors


def _l2_norm(vec: list[float]) -> float:
    return sum(x * x for x in vec) ** 0.5


# --------------------------------------------------------------------------
# Indexing
# --------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Embed chunks into a Chroma vector DB.")
    parser.add_argument("--chunks", default=Path("build/chunks.jsonl"), type=Path)
    parser.add_argument("--persist", default=Path("build/chroma"), type=Path,
                        help="where the vector DB lives (default: build/chroma)")
    parser.add_argument("--collection", default="robog",
                        help="Chroma collection name (default: robog)")
    parser.add_argument("--provider", choices=["local", "gemini"], default="local")
    parser.add_argument("--dims", type=int, default=1536,
                        help="gemini only: output dimensions 128-3072 (default: 1536)")
    parser.add_argument("--checkpoint", default=Path("build/embed_checkpoint.jsonl"), type=Path,
                        help="gemini only: checkpoint file for resume (default: build/embed_checkpoint.jsonl)")
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET_PER_MIN,
                        help="gemini only: max estimated input tokens per 60s window (default: 24000)")
    parser.add_argument("--batch-size", type=int, default=GEMINI_BATCH_SIZE,
                        help="gemini only: texts per batchEmbedContents call, max 100 (default: 100)")
    parser.add_argument("--limit", type=int, default=None,
                        help="embed only the first N chunks (for smoke tests; use a separate --persist/--collection)")
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.chunks.read_text(encoding="utf-8").splitlines() if line]
    if not rows:
        raise SystemExit(f"No chunks in {args.chunks} -- run 'python -m ingest.run' first.")

    if args.limit is not None:
        rows = rows[:args.limit]
        print(f"*** SMOKE RUN: only the first {len(rows)} chunks ***")
    print(f"Loaded {len(rows)} chunks from {args.chunks}")

    embedder = GeminiEmbedder(dims=args.dims) if args.provider == "gemini" else LocalEmbedder()
    if args.provider == "gemini":
        texts = [gemini_document_text(row) for row in rows]
        chunks_sha256 = _chunks_sha256(args.chunks)
        total_tokens = sum(_estimate_tokens(t) for t in texts)
        n_batches = (len(rows) + min(args.batch_size, GEMINI_BATCH_SIZE) - 1) // min(args.batch_size, GEMINI_BATCH_SIZE)
        print("Template: Gemini question-answering document format (title: … | text: …)")
        print(f"Plan: synchronous batchEmbedContents, {n_batches} call(s) "
              f"({min(args.batch_size, GEMINI_BATCH_SIZE)} texts/call), "
              f"~{total_tokens} estimated input tokens, "
              f"throttle {args.token_budget} tokens/60s, checkpoint {args.checkpoint}")
        vectors = embed_gemini_resumable(
            rows, embedder,
            checkpoint_path=args.checkpoint,
            chunks_sha256=chunks_sha256,
            token_budget_per_min=args.token_budget,
            batch_size=args.batch_size,
        )
        print(f"All {len(vectors)} embeddings present (dims={embedder.dims}).")
        norms = [_l2_norm(v) for v in vectors]
        print(f"Vector L2 norm: min={min(norms):.4f} max={max(norms):.4f} mean={sum(norms)/len(norms):.4f}")
        unique = len({tuple(round(x, 6) for x in v) for v in vectors})
        print(f"Distinct vectors: {unique}/{len(vectors)}")
    else:
        texts = [row["text"] for row in rows]
        print("Template: E5 passage prefix (passage: …)")
        vectors = embedder.embed(texts)

    import chromadb  # light import, only needed now

    client = chromadb.PersistentClient(path=str(args.persist))
    collection = client.get_or_create_collection(
        name=args.collection, metadata={"hnsw:space": "cosine"}
    )
    # Chroma metadata values must be str / int / float / bool -- drop the rest
    # (e.g. null section_number for unnumbered headings).
    metadatas = [
        {k: v for k, v in row["metadata"].items() if isinstance(v, (str, int, float, bool))}
        for row in rows
    ]
    collection.upsert(
        ids=[row["id"] for row in rows],
        documents=[row["text"] for row in rows],
        metadatas=metadatas,
        embeddings=vectors,
    )
    print(f"Indexed {collection.count()} chunks into {args.persist} (collection '{args.collection}').")


if __name__ == "__main__":
    main()
