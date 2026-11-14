"""Phase 2: embed the chunks and store them in a vector database (Chroma).

Needs the extra dependencies (phase 1 needs none):

    pip install -r requirements-rag.txt

Both embedding providers are free -- pick one with ``--provider`` or
``ROBOG_EMBED_PROVIDER`` in ``.env``:

* ``local``  -- ``intfloat/multilingual-e5-small`` via sentence-transformers.
  Runs offline on any laptop, no API key, no rate limits (~20s on CPU).
* ``gemini`` -- ``gemini-embedding-2`` via the Google AI Studio free tier.
  Needs ``GEMINI_API_KEY`` in ``.env`` or the environment. Google's free tier
  enforces a **30,000 tokens-per-minute (TPM)** rolling window, so this script:
  1. Paces requests under ``--tpm-limit`` (default 25,000 TPM; set 0 on paid tier),
  2. Saves **every batch incrementally** to Chroma as soon as it returns, and
  3. Automatically **resumes** from already-indexed chunks on re-run (pass
     ``--reset`` to rebuild from scratch).

Usage:

    python -m ingest.embed_index --chunks build/chunks.jsonl --provider gemini
    python -m ingest.embed_index --chunks build/chunks.jsonl --provider local
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections import deque
from pathlib import Path
from typing import Callable

from retrieve.env import load_dotenv
from retrieve.vector import (
    DEFAULT_CHROMA_PATH,
    DEFAULT_COLLECTION,
    DEFAULT_DIMS,
    ENV_COLLECTION,
    ENV_DIMS,
    ENV_MODEL,
    ENV_PATH,
    ENV_PROVIDER,
    ENV_TPM,
    GEMINI_MODEL,
    LOCAL_MODEL,
    VectorStoreUnavailable,
    create_gemini_client,
    embed_gemini_batch,
)

# E5 models were trained with these prefixes -- they matter for quality.
_E5_QUERY_PREFIX = "query: "
_E5_PASSAGE_PREFIX = "passage: "

# 40 chunks (~7k-9k tokens) stays comfortably below Google AI Studio's 30k TPM
# ceiling per request and checkpoints progress to Chroma frequently.
DEFAULT_BATCH_SIZE = 40
DEFAULT_GEMINI_TPM = 25_000
QUOTA_WINDOW_SECONDS = 62.0
CHROMA_UPSERT_BATCH = 500


def format_passage(row: dict) -> str:
    """Prepend product and section breadcrumb so embeddings capture scope.

    The stored document text in Chroma remains ``row["text"]`` verbatim so
    citations and numeric checks see only the original manual text.
    """
    meta = row.get("metadata") or {}
    product = str(meta.get("product") or "").strip()
    section = str(meta.get("section") or meta.get("section_title") or "").strip()
    header = " — ".join(part for part in (product, section) if part)
    body = str(row.get("text") or "").strip()
    if header and body:
        return f"{header}\n{body}"
    return body or header


def estimate_tokens(text: str) -> int:
    """Conservative token estimate (~3.5 chars/token across EN/DE/IT + tables)."""
    return max(1, math.ceil(len(text) / 3.5))


class TokenRateLimiter:
    """Sliding 60-second token-per-minute pacer for free-tier embedding APIs."""

    WINDOW_SECONDS = 60.5

    def __init__(
        self,
        tpm_limit: int = DEFAULT_GEMINI_TPM,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.tpm_limit = max(0, int(tpm_limit))
        self._clock = clock
        self._sleep = sleep_fn
        self._history: deque[tuple[float, int]] = deque()

    def _prune(self, now: float) -> int:
        cutoff = now - self.WINDOW_SECONDS
        while self._history and self._history[0][0] <= cutoff:
            self._history.popleft()
        return sum(tokens for _, tokens in self._history)

    def acquire(self, tokens: int) -> float:
        """Sleep if needed so ``tokens`` stays within ``tpm_limit`` over 60s.

        Returns total seconds slept. A single batch larger than ``tpm_limit``
        is allowed once the window empties so execution can never deadlock.
        """
        if self.tpm_limit <= 0 or tokens <= 0:
            return 0.0
        slept = 0.0
        while True:
            now = self._clock()
            used = self._prune(now)
            if used == 0 or used + tokens <= self.tpm_limit:
                self._history.append((self._clock(), tokens))
                return slept
            oldest_ts = self._history[0][0]
            wait_s = max(0.1, (oldest_ts + self.WINDOW_SECONDS) - now)
            print(
                f"  pacing for free-tier {self.tpm_limit:,} TPM limit "
                f"({used:,} tokens used in last 60s) -- waiting {wait_s:.1f}s..."
            )
            self._sleep(wait_s)
            slept += wait_s


# --------------------------------------------------------------------------
# Embedding providers (both free)
# --------------------------------------------------------------------------
class LocalEmbedder:
    """multilingual-e5-small: free, offline, great for EN/DE/IT."""

    provider = "local"

    def __init__(self, model_name: str = LOCAL_MODEL):
        try:
            from sentence_transformers import SentenceTransformer  # heavy import
        except ImportError as exc:
            raise SystemExit(
                "sentence-transformers is not installed -- "
                "pip install -r requirements-rag.txt  (or use --provider gemini)"
            ) from exc

        self.model_name = model_name
        self.model = SentenceTransformer(model_name)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        prefix = _E5_PASSAGE_PREFIX if "e5" in self.model_name.lower() else ""
        vectors = self.model.encode(
            [prefix + t for t in texts],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed(self, texts: list[str]) -> list[list[float]]:
        prefix = _E5_PASSAGE_PREFIX if "e5" in self.model_name.lower() else ""
        vectors = self.model.encode(
            [prefix + t for t in texts],
            normalize_embeddings=True,
            show_progress_bar=True,
        )
        return vectors.tolist()


class GeminiEmbedder:
    """Gemini Embedding API via the free Google AI Studio tier."""

    provider = "gemini"

    def __init__(
        self,
        dims: int = DEFAULT_DIMS,
        model_name: str = GEMINI_MODEL,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        tpm_limit: int = DEFAULT_GEMINI_TPM,
        client=None,
        rate_limiter: TokenRateLimiter | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        if not 128 <= dims <= 3072:
            raise SystemExit(f"--dims={dims} out of range (Gemini embeddings support 128-3072).")
        if batch_size <= 0:
            raise SystemExit("--batch-size must be >= 1.")
        try:
            self.client = client if client is not None else create_gemini_client()
        except VectorStoreUnavailable as exc:
            raise SystemExit(str(exc)) from exc
        self.model_name = model_name
        self.dims = dims
        self.batch_size = batch_size
        self.sleep_fn = sleep_fn
        self.rate_limiter = rate_limiter or TokenRateLimiter(tpm_limit, sleep_fn=sleep_fn)

    def embed_batch(self, batch: list[str]) -> list[list[float]]:
        if not batch:
            return []
        batch_tokens = sum(estimate_tokens(t) for t in batch)
        self.rate_limiter.acquire(batch_tokens)
        try:
            return embed_gemini_batch(
                self.client,
                batch,
                model_name=self.model_name,
                dims=self.dims,
                task_type="RETRIEVAL_DOCUMENT",
                max_retries=6,
                quota_retry_seconds=QUOTA_WINDOW_SECONDS,
                sleep_fn=self.sleep_fn,
            )
        except VectorStoreUnavailable as exc:
            raise SystemExit(str(exc)) from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            vectors.extend(self.embed_batch(batch))
            print(f"  embedded {min(start + self.batch_size, len(texts))}/{len(texts)}")
        return vectors


# --------------------------------------------------------------------------
# Indexing
# --------------------------------------------------------------------------
def _peek_collection_dims(collection) -> int | None:
    meta = getattr(collection, "metadata", None) or {}
    if meta.get("dims") is not None:
        try:
            return int(meta["dims"])
        except (TypeError, ValueError):
            pass
    try:
        if collection.count() > 0 and hasattr(collection, "peek"):
            peeked = collection.peek(limit=1)
            embs = (peeked or {}).get("embeddings")
            if embs is not None and len(embs) > 0 and len(embs[0]) > 0:
                return len(embs[0])
    except Exception:
        pass
    return None


def _is_collection_compatible(
    collection,
    *,
    provider: str,
    model_name: str,
    vector_dim: int | None,
) -> tuple[bool, str]:
    old_meta = getattr(collection, "metadata", None) or {}
    old_dims = _peek_collection_dims(collection)
    old_provider = old_meta.get("provider")
    old_model = old_meta.get("model")
    if vector_dim is not None and old_dims is not None and old_dims != vector_dim:
        return False, f"was dims={old_dims}, now dims={vector_dim}"
    if old_provider and str(old_provider) != provider:
        return False, f"was provider={old_provider}, now provider={provider}"
    if model_name and old_model and str(old_model) != model_name:
        return False, f"was model={old_model}, now model={model_name}"
    return True, ""


def _prepare_collection(
    client,
    name: str,
    *,
    provider: str,
    model_name: str,
    vector_dim: int,
    reset: bool = False,
):
    """Return a Chroma collection ready for ``vector_dim`` vectors.

    Recreates the collection automatically if ``--reset`` is passed or an
    existing collection was indexed with a different provider, model or vector
    dimensionality (e.g. switching from 384-dim local e5 to 1536-dim Gemini).
    """
    desired_meta = {
        "hnsw:space": "cosine",
        "provider": provider,
        "model": model_name,
        "dims": vector_dim,
    }
    existing = None
    try:
        existing = client.get_collection(name=name)
    except Exception:
        existing = None

    if existing is not None:
        compatible, reason_detail = _is_collection_compatible(
            existing, provider=provider, model_name=model_name, vector_dim=vector_dim
        )
        if reset or not compatible:
            old_meta = getattr(existing, "metadata", None) or {}
            old_dims = _peek_collection_dims(existing)
            reason = "--reset requested" if reset else (
                f"replacing incompatible index (was provider={old_meta.get('provider') or '?'}, "
                f"model={old_meta.get('model') or '?'}, dims={old_dims or '?'}: {reason_detail})"
            )
            print(f"Recreating collection {name!r}: {reason}")
            client.delete_collection(name=name)
            existing = None

    if existing is not None:
        return existing
    return client.get_or_create_collection(name=name, metadata=desired_meta)


def _existing_chunk_ids(collection) -> set[str]:
    """Return chunk IDs already persisted in ``collection`` (for fast resume)."""
    if not hasattr(collection, "get"):
        return set()
    try:
        if collection.count() <= 0:
            return set()
        got = collection.get(include=[])
        return set((got or {}).get("ids") or [])
    except Exception:
        return set()


def _upsert_rows(collection, rows: list[dict], vectors: list[list[float]]) -> None:
    metadatas = [
        {k: v for k, v in (row.get("metadata") or {}).items() if isinstance(v, (str, int, float, bool))}
        for row in rows
    ]
    ids = [row["id"] for row in rows]
    documents = [row["text"] for row in rows]
    for start in range(0, len(rows), CHROMA_UPSERT_BATCH):
        end = start + CHROMA_UPSERT_BATCH
        collection.upsert(
            ids=ids[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
            embeddings=vectors[start:end],
        )


def index_chunks(
    chunks_path: Path,
    persist_path: Path,
    *,
    collection_name: str = DEFAULT_COLLECTION,
    provider: str = "local",
    dims: int = DEFAULT_DIMS,
    model_name: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    tpm_limit: int = DEFAULT_GEMINI_TPM,
    reset: bool = False,
    embedder=None,
    chroma_client=None,
) -> int:
    """Embed ``chunks_path`` JSONL rows into Chroma incrementally with resume."""
    if not chunks_path.exists():
        raise SystemExit(f"{chunks_path} not found -- run 'python -m ingest.run --data data' first.")
    rows = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit(f"No chunks in {chunks_path} -- run 'python -m ingest.run' first.")
    print(f"Loaded {len(rows)} chunks from {chunks_path}")

    if chroma_client is None:
        try:
            import chromadb  # light import, only needed now
        except ImportError as exc:
            raise SystemExit(
                "chromadb is not installed -- pip install -r requirements-rag.txt"
            ) from exc
        chroma_client = chromadb.PersistentClient(path=str(persist_path))

    if embedder is None:
        if provider == "gemini":
            resolved_model = model_name or os.environ.get(ENV_MODEL) or GEMINI_MODEL
            embedder = GeminiEmbedder(
                dims=dims,
                model_name=resolved_model,
                batch_size=batch_size,
                tpm_limit=tpm_limit,
            )
        else:
            resolved_model = model_name or os.environ.get(ENV_MODEL) or LOCAL_MODEL
            embedder = LocalEmbedder(model_name=resolved_model)

    eff_provider = getattr(embedder, "provider", provider)
    eff_model = getattr(embedder, "model_name", model_name or "")
    eff_batch = max(1, int(getattr(embedder, "batch_size", batch_size) or batch_size))
    known_dim: int | None = getattr(embedder, "dims", None)

    # Check if an existing compatible collection already holds part of the index
    # BEFORE making any embedding API calls so re-runs skip completed chunks.
    collection = None
    already_done: set[str] = set()
    if known_dim is not None:
        collection = _prepare_collection(
            chroma_client,
            collection_name,
            provider=eff_provider,
            model_name=eff_model,
            vector_dim=int(known_dim),
            reset=reset,
        )
        if not reset:
            already_done = _existing_chunk_ids(collection)
    elif not reset:
        try:
            existing = chroma_client.get_collection(name=collection_name)
        except Exception:
            existing = None
        if existing is not None:
            compatible, _ = _is_collection_compatible(
                existing, provider=eff_provider, model_name=eff_model, vector_dim=None
            )
            if compatible:
                already_done = _existing_chunk_ids(existing)

    pending_rows = [row for row in rows if row["id"] not in already_done] if already_done else rows
    skipped = len(rows) - len(pending_rows)
    if skipped > 0:
        print(
            f"Resuming: {skipped}/{len(rows)} chunks already saved in {persist_path}, "
            f"{len(pending_rows)} remaining."
        )
    if not pending_rows and collection is not None:
        total = collection.count()
        print(f"All {total} chunks already indexed in {persist_path} (pass --reset to re-embed).")
        return total
    if not pending_rows:
        existing = chroma_client.get_collection(name=collection_name)
        total = existing.count()
        print(f"All {total} chunks already indexed in {persist_path} (pass --reset to re-embed).")
        return total

    embed_step = getattr(embedder, "embed_batch", None) or embedder.embed
    completed = skipped
    vector_dim = int(known_dim) if known_dim is not None else 0

    for start in range(0, len(pending_rows), eff_batch):
        batch_rows = pending_rows[start:start + eff_batch]
        passages = [format_passage(row) for row in batch_rows]
        batch_vectors = embed_step(passages)
        if len(batch_vectors) != len(batch_rows) or not batch_vectors[0]:
            raise SystemExit(
                f"Embedder returned {len(batch_vectors)} vectors for {len(batch_rows)} chunks."
            )
        vector_dim = len(batch_vectors[0])
        if collection is None:
            collection = _prepare_collection(
                chroma_client,
                collection_name,
                provider=eff_provider,
                model_name=eff_model,
                vector_dim=vector_dim,
                reset=reset,
            )
        _upsert_rows(collection, batch_rows, batch_vectors)
        completed += len(batch_rows)
        print(f"  embedded & saved {completed}/{len(rows)}")

    assert collection is not None
    total = collection.count()
    print(
        f"Indexed {total} chunks into {persist_path} "
        f"(collection {collection_name!r}, provider={eff_provider}, dims={vector_dim})."
    )
    return total


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    default_provider = (os.environ.get(ENV_PROVIDER) or "local").strip().lower()
    if default_provider not in ("local", "gemini"):
        default_provider = "local"
    try:
        default_dims = int(os.environ.get(ENV_DIMS, str(DEFAULT_DIMS)))
    except ValueError:
        default_dims = DEFAULT_DIMS
    try:
        default_tpm = int(os.environ.get(ENV_TPM, str(DEFAULT_GEMINI_TPM)))
    except ValueError:
        default_tpm = DEFAULT_GEMINI_TPM

    parser = argparse.ArgumentParser(description="Embed chunks into a Chroma vector DB.")
    parser.add_argument("--chunks", default=Path("build/chunks.jsonl"), type=Path)
    parser.add_argument(
        "--persist",
        default=Path(os.environ.get(ENV_PATH, DEFAULT_CHROMA_PATH)),
        type=Path,
        help=f"where the vector DB lives (default: ${ENV_PATH} or {DEFAULT_CHROMA_PATH})",
    )
    parser.add_argument(
        "--collection",
        default=os.environ.get(ENV_COLLECTION, DEFAULT_COLLECTION),
        help=f"Chroma collection name (default: ${ENV_COLLECTION} or {DEFAULT_COLLECTION})",
    )
    parser.add_argument(
        "--provider",
        choices=["local", "gemini"],
        default=default_provider,
        help=f"embedding provider (default: ${ENV_PROVIDER} or local)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"override embedding model ID (default: ${ENV_MODEL} or provider default)",
    )
    parser.add_argument(
        "--dims",
        type=int,
        default=default_dims,
        help=f"gemini only: output dimensions 128-3072 (default: ${ENV_DIMS} or {DEFAULT_DIMS})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"chunks per batch, checkpointed to Chroma after every batch (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--tpm-limit",
        type=int,
        default=default_tpm,
        help=f"gemini free-tier tokens-per-minute budget; 0 disables pacing (default: ${ENV_TPM} or {DEFAULT_GEMINI_TPM})",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="drop and recreate the Chroma collection instead of resuming",
    )
    args = parser.parse_args(argv)

    index_chunks(
        args.chunks,
        args.persist,
        collection_name=args.collection,
        provider=args.provider,
        dims=args.dims,
        model_name=args.model,
        batch_size=args.batch_size,
        tpm_limit=args.tpm_limit,
        reset=args.reset,
    )


if __name__ == "__main__":
    main()
