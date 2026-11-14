"""Dense (vector) retrieval -- the embedding half of the hybrid engine.

``ingest/embed_index.py`` (Sujal's side of the pipeline) turns every chunk into
a vector and stores it in a Chroma collection -- by default the directory
``build/chroma``, collection ``robog``, cosine distance. This module is the
*read* side: embed the question with the SAME model, dimensionality and query
convention used for indexing, ask Chroma for the nearest chunks, and hand a
ranked list back to the engine, where it is fused with BM25 and the parameter
index via reciprocal rank fusion.

Query embedding must match ingestion:

* ``local``   -- ``intfloat/multilingual-e5-small`` via sentence-transformers.
  Free, offline, no key. The model was trained with the prefix ``query: `` for
  queries and ``passage: `` for documents; the ingest side handles the passage
  prefix, this side adds ``query: ``.
* ``gemini``  -- ``gemini-embedding-2`` at 1536 dimensions (the default Sujal's
  pipeline uses), via the free Google AI Studio tier. The key comes from the
  ``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``) environment variable -- never from
  source code.

Configuration via environment variables (defaults match ``ingest/embed_index.py``):

    ROBOG_CHROMA_PATH        persisted Chroma directory   (default: build/chroma)
    ROBOG_CHROMA_COLLECTION  collection name              (default: robog)
    ROBOG_EMBED_PROVIDER     local | gemini               (default: local)
    ROBOG_EMBED_DIMS         gemini output dims 128-3072  (default: 1536)

Design rules, on purpose:

* **Read-only.** The collection is opened with ``get_collection`` -- never
  ``get_or_create_collection`` -- and nothing is upserted. There is exactly one
  vector database and it is Sujal's; this module only queries it.
* **Lazy imports.** This module is importable without chromadb /
  sentence-transformers / google-genai installed, so the offline BM25 engine
  and the unit tests keep working. Whatever is missing raises
  ``VectorStoreUnavailable`` with an actionable message; callers fall back to
  keyword retrieval instead of crashing a demo.
* **Policy-aware.** ``search`` accepts ``allowed_ids`` -- the same language /
  product allow-list the BM25 path gets -- so the language policy is enforced
  uniformly across both halves of the hybrid, not just the keyword half.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CHROMA_PATH = "build/chroma"
DEFAULT_COLLECTION = "robog"           # must match ingest/embed_index.py
DEFAULT_DIMS = 1536                    # gemini output dimensions
LOCAL_MODEL = "intfloat/multilingual-e5-small"
GEMINI_MODEL = "gemini-embedding-2"    # must match ingest/embed_index.py
E5_QUERY_PREFIX = "query: "            # e5 models need it; passages used "passage: "

ENV_PATH = "ROBOG_CHROMA_PATH"
ENV_COLLECTION = "ROBOG_CHROMA_COLLECTION"
ENV_PROVIDER = "ROBOG_EMBED_PROVIDER"
ENV_DIMS = "ROBOG_EMBED_DIMS"


class VectorStoreUnavailable(RuntimeError):
    """The dense path cannot be used. The message says what to run to fix it."""


@dataclass
class DenseHit:
    """One chunk returned by the vector store, with its score."""

    chunk_id: str
    score: float            # 1 - cosine distance (higher is better)
    text: str
    metadata: dict


# ---------------------------------------------------------------------------
# Query embedders (callables: list[str] -> list[list[float]])
# ---------------------------------------------------------------------------
def _local_embedder():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise VectorStoreUnavailable(
            "sentence-transformers is not installed -- "
            "pip install -r requirements-rag.txt  (or set ROBOG_EMBED_PROVIDER=gemini)"
        ) from exc
    try:
        model = SentenceTransformer(LOCAL_MODEL)
    except Exception as exc:
        raise VectorStoreUnavailable(f"could not load local embedding model {LOCAL_MODEL}: {exc}") from exc

    def embed(texts: list[str]) -> list[list[float]]:
        vectors = model.encode(
            [E5_QUERY_PREFIX + text for text in texts],
            normalize_embeddings=True,
        )
        return vectors.tolist()

    return embed


def _gemini_embedder(dims: int):
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        raise VectorStoreUnavailable(
            "GEMINI_API_KEY is not set -- create a free key in Google AI Studio "
            "and export it, or set ROBOG_EMBED_PROVIDER=local for the offline model"
        )
    try:
        from google import genai
    except ImportError as exc:
        raise VectorStoreUnavailable(
            "google-genai is not installed -- pip install -r requirements-rag.txt"
        ) from exc
    client = genai.Client()

    def embed(texts: list[str]) -> list[list[float]]:
        response = client.models.embed_content(
            model=GEMINI_MODEL,
            contents=list(texts),
            output_dimensionality=dims,
        )
        return [list(embedding.values) for embedding in response.embeddings]

    return embed


def make_embedder(provider: str | None = None, dims: int | None = None):
    """Build the query embedder. Returns ``(embedder, provider_name)``.

    Parameters override the environment variables; no API key is ever stored
    in code.
    """
    provider_name = (provider or os.environ.get(ENV_PROVIDER, "local")).strip().lower()
    if dims is None:
        raw_dims = os.environ.get(ENV_DIMS, str(DEFAULT_DIMS))
        try:
            dims = int(raw_dims)
        except ValueError:
            raise VectorStoreUnavailable(f"{ENV_DIMS}={raw_dims!r} is not an integer") from None
    if provider_name == "local":
        return _local_embedder(), "local"
    if provider_name == "gemini":
        if not 128 <= dims <= 3072:
            raise VectorStoreUnavailable(f"{ENV_DIMS}={dims} out of range (gemini-embedding-2: 128-3072)")
        return _gemini_embedder(dims), "gemini"
    raise VectorStoreUnavailable(
        f"unknown provider {provider_name!r} (expected 'local' or 'gemini')"
    )


def _load_client(path: Path):
    try:
        import chromadb
    except ImportError as exc:
        raise VectorStoreUnavailable(
            "chromadb is not installed -- pip install -r requirements-rag.txt"
        ) from exc
    try:
        return chromadb.PersistentClient(path=str(path))
    except Exception as exc:  # corrupt store, wrong version, ...
        raise VectorStoreUnavailable(f"could not open vector store '{path}': {exc}") from exc


# ---------------------------------------------------------------------------
# The retriever
# ---------------------------------------------------------------------------
class ChromaRetriever:
    """Read-only dense retriever over the existing Chroma collection."""

    def __init__(self, collection, embedder, *, provider: str = "local",
                 collection_name: str = DEFAULT_COLLECTION):
        # ``collection`` only needs the Chroma query() API, which keeps this
        # class trivially testable with a fake (no Chroma, no network).
        self.collection = collection
        self.embedder = embedder
        self.provider = provider
        self.collection_name = collection_name
        self._query_cache: dict[str, list[float]] = {}

    # -- construction ------------------------------------------------------
    @classmethod
    def connect(cls, persist_dir: str | Path | None = None,
                collection_name: str | None = None,
                provider: str | None = None,
                dims: int | None = None) -> "ChromaRetriever":
        """Open the existing store. Never creates anything.

        Raises ``VectorStoreUnavailable`` (with the fix in the message) when the
        store or collection does not exist yet or the provider cannot load.
        """
        path = Path(persist_dir or os.environ.get(ENV_PATH, DEFAULT_CHROMA_PATH))
        name = collection_name or os.environ.get(ENV_COLLECTION, DEFAULT_COLLECTION)
        if not path.is_dir():
            raise VectorStoreUnavailable(
                f"no vector store at '{path}' -- run "
                f"python -m ingest.embed_index --provider {provider or os.environ.get(ENV_PROVIDER, 'local')} first"
            )
        client = _load_client(path)
        try:
            collection = client.get_collection(name=name)  # read-only, by design
        except Exception as exc:
            raise VectorStoreUnavailable(
                f"collection '{name}' not found in '{path}' -- build it with "
                f"python -m ingest.embed_index first"
            ) from exc
        try:
            count = collection.count()
        except Exception as exc:
            raise VectorStoreUnavailable(
                f"collection '{name}' in '{path}' cannot be read: {exc}"
            ) from exc
        if count == 0:
            raise VectorStoreUnavailable(
                f"collection '{name}' in '{path}' is empty -- run python -m ingest.embed_index first"
            )
        embedder, provider_name = make_embedder(provider, dims)
        return cls(collection, embedder, provider=provider_name, collection_name=name)

    @property
    def count(self) -> int:
        return self.collection.count()

    # -- retrieval ---------------------------------------------------------
    def _embed_query(self, query: str) -> list[float]:
        cached = self._query_cache.get(query)
        if cached is None:
            cached = self.embedder([query])[0]
            self._query_cache[query] = cached
        return cached

    def search(self, query: str, k: int = 10,
               *, allowed_ids: set[str] | None = None) -> list[DenseHit]:
        """Return up to ``k`` chunks nearest to ``query``.

        ``allowed_ids`` is the language/product allow-list from the engine's
        policy; when set, it is enforced by over-fetching and filtering, so the
        policy holds for the dense half exactly as it does for BM25.
        """
        query = (query or "").strip()
        if not query or k <= 0 or allowed_ids == set():
            return []
        try:
            vector = self._embed_query(query)
        except VectorStoreUnavailable:
            raise
        except Exception as exc:
            raise VectorStoreUnavailable(f"query embedding failed: {exc}") from exc
        try:
            total = self.collection.count()
        except Exception as exc:
            raise VectorStoreUnavailable(f"Chroma count() failed: {exc}") from exc
        if total == 0:
            return []
        # Expand the global candidate set until enough allowed results are found
        # or the collection is exhausted. A fixed over-fetch can miss all rows
        # of a small product/language subset.
        n_fetch = min(total, max(k * 4 if allowed_ids is not None else k * 2, 20))
        while True:
            try:
                result = self.collection.query(
                    query_embeddings=[vector], n_results=n_fetch,
                    include=["documents", "metadatas", "distances"],
                )
            except Exception as exc:
                raise VectorStoreUnavailable(f"Chroma query failed: {exc}") from exc

            ids = (result or {}).get("ids") or [[]]
            docs = (result or {}).get("documents") or [[]]
            metas = (result or {}).get("metadatas") or [[]]
            dists = (result or {}).get("distances") or [[]]
            hits: list[DenseHit] = []
            for chunk_id, doc, meta, distance in zip(ids[0], docs[0], metas[0], dists[0]):
                if allowed_ids is not None and chunk_id not in allowed_ids:
                    continue
                try:
                    score = 1.0 - float(distance)
                except (TypeError, ValueError):
                    score = 0.0
                hits.append(DenseHit(str(chunk_id), score, doc or "", dict(meta or {})))
                if len(hits) >= k:
                    break
            if len(hits) >= k or n_fetch >= total or allowed_ids is None:
                return hits
            n_fetch = min(total, n_fetch * 2)
