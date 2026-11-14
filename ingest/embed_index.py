"""Phase 2: embed the chunks and store them in a vector database (Chroma).

Needs the extra dependencies (phase 1 needs none):

    pip install -r requirements-rag.txt

Both embedding providers are free -- pick one with ``--provider`` or
``ROBOG_EMBED_PROVIDER`` in ``.env``:

* ``local``  -- ``intfloat/multilingual-e5-small`` via sentence-transformers.
  Runs offline on any laptop, no API key, no rate limits.
* ``gemini`` -- ``gemini-embedding-2`` via the Google AI Studio free tier.
  Needs ``GEMINI_API_KEY`` in ``.env`` or the environment, batches requests with
  automatic retry on free-tier rate limits, passes ``output_dimensionality`` and
  ``task_type="RETRIEVAL_DOCUMENT"`` via ``EmbedContentConfig``, and L2-normalizes
  1536-dim vectors so Chroma's cosine distance stays exact.

Usage:

    python -m ingest.embed_index --chunks build/chunks.jsonl
    python -m ingest.embed_index --chunks build/chunks.jsonl --provider gemini

NOTE for queries: with the local e5 model, queries must be prefixed ``query: ``
and passages ``passage: `` (this script handles the passages; ``retrieve.vector``
adds ``query: `` to the question). Collection metadata records the provider,
model and vector dimensions so ``retrieve.vector`` connects with matching
settings automatically.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

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
    GEMINI_MODEL,
    LOCAL_MODEL,
    VectorStoreUnavailable,
    create_gemini_client,
    embed_gemini_batch,
)

# E5 models were trained with these prefixes -- they matter for quality.
_E5_QUERY_PREFIX = "query: "
_E5_PASSAGE_PREFIX = "passage: "

DEFAULT_BATCH_SIZE = 100
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
        client=None,
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

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            try:
                batch_vectors = embed_gemini_batch(
                    self.client,
                    batch,
                    model_name=self.model_name,
                    dims=self.dims,
                    task_type="RETRIEVAL_DOCUMENT",
                )
            except VectorStoreUnavailable as exc:
                raise SystemExit(str(exc)) from exc
            vectors.extend(batch_vectors)
            print(f"  embedded {min(start + self.batch_size, len(texts))}/{len(texts)}")
        return vectors


# --------------------------------------------------------------------------
# Indexing
# --------------------------------------------------------------------------
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
        old_meta = getattr(existing, "metadata", None) or {}
        old_dims = old_meta.get("dims")
        old_provider = old_meta.get("provider")
        old_model = old_meta.get("model")
        # If an older index predates metadata tags, peek at one stored embedding
        # so dimension mismatches still trigger a clean recreate instead of a crash.
        if old_dims is None and existing.count() > 0:
            try:
                peeked = existing.peek(limit=1)
                embs = (peeked or {}).get("embeddings")
                if embs is not None and len(embs) > 0 and len(embs[0]) > 0:
                    old_dims = len(embs[0])
            except Exception:
                old_dims = None

        incompatible = (
            (old_dims is not None and int(old_dims) != vector_dim)
            or (old_provider and str(old_provider) != provider)
            or (old_model and str(old_model) != model_name)
        )
        if reset or incompatible:
            reason = "--reset requested" if reset else (
                f"replacing incompatible index (was provider={old_provider or '?'}, "
                f"model={old_model or '?'}, dims={old_dims or '?'})"
            )
            print(f"Recreating collection {name!r}: {reason}")
            client.delete_collection(name=name)
            existing = None

    if existing is not None:
        return existing
    return client.get_or_create_collection(name=name, metadata=desired_meta)


def index_chunks(
    chunks_path: Path,
    persist_path: Path,
    *,
    collection_name: str = DEFAULT_COLLECTION,
    provider: str = "local",
    dims: int = DEFAULT_DIMS,
    model_name: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    reset: bool = False,
    embedder=None,
    chroma_client=None,
) -> int:
    """Embed ``chunks_path`` JSONL rows into Chroma and return collection count."""
    if not chunks_path.exists():
        raise SystemExit(f"{chunks_path} not found -- run 'python -m ingest.run --data data' first.")
    rows = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit(f"No chunks in {chunks_path} -- run 'python -m ingest.run' first.")
    print(f"Loaded {len(rows)} chunks from {chunks_path}")

    if embedder is None:
        if provider == "gemini":
            resolved_model = model_name or os.environ.get(ENV_MODEL) or GEMINI_MODEL
            embedder = GeminiEmbedder(dims=dims, model_name=resolved_model, batch_size=batch_size)
        else:
            resolved_model = model_name or os.environ.get(ENV_MODEL) or LOCAL_MODEL
            embedder = LocalEmbedder(model_name=resolved_model)

    passages = [format_passage(row) for row in rows]
    vectors = embedder.embed(passages)
    if len(vectors) != len(rows) or not vectors[0]:
        raise SystemExit(
            f"Embedder returned {len(vectors)} vectors for {len(rows)} chunks."
        )
    vector_dim = len(vectors[0])

    if chroma_client is None:
        try:
            import chromadb  # light import, only needed now
        except ImportError as exc:
            raise SystemExit(
                "chromadb is not installed -- pip install -r requirements-rag.txt"
            ) from exc
        chroma_client = chromadb.PersistentClient(path=str(persist_path))

    collection = _prepare_collection(
        chroma_client,
        collection_name,
        provider=getattr(embedder, "provider", provider),
        model_name=getattr(embedder, "model_name", model_name or ""),
        vector_dim=vector_dim,
        reset=reset,
    )

    # Chroma metadata values must be str / int / float / bool -- drop the rest
    # (e.g. null section_number for unnumbered headings).
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

    total = collection.count()
    print(
        f"Indexed {total} chunks into {persist_path} "
        f"(collection {collection_name!r}, provider={provider}, dims={vector_dim})."
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
        help=f"gemini only: chunks per API call (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="drop and recreate the Chroma collection before indexing",
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
        reset=args.reset,
    )


if __name__ == "__main__":
    main()
