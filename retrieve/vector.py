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
  ``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``) environment variable or ``.env``
  file -- never from source code. Vectors below 3072 dims are L2-normalized per
  Google's Matryoshka embedding spec so cosine distance stays exact.

Configuration via environment variables (defaults match ``ingest/embed_index.py``):

    ROBOG_CHROMA_PATH        persisted Chroma directory   (default: build/chroma)
    ROBOG_CHROMA_COLLECTION  collection name              (default: robog)
    ROBOG_EMBED_PROVIDER     local | gemini               (default: store metadata or local)
    ROBOG_EMBED_MODEL        override embedding model id  (default: gemini-embedding-2 / e5)
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

import hashlib
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .env import load_dotenv

DEFAULT_CHROMA_PATH = "build/chroma"
DEFAULT_COLLECTION = "robog"           # must match ingest/embed_index.py
DEFAULT_DIMS = 1536                    # gemini output dimensions
LOCAL_MODEL = "intfloat/multilingual-e5-small"
GEMINI_MODEL = "gemini-embedding-2"    # must match ingest/embed_index.py
E5_QUERY_PREFIX = "query: "            # e5 models need it; passages used "passage: "

ENV_PATH = "ROBOG_CHROMA_PATH"
ENV_COLLECTION = "ROBOG_CHROMA_COLLECTION"
ENV_PROVIDER = "ROBOG_EMBED_PROVIDER"
ENV_MODEL = "ROBOG_EMBED_MODEL"
ENV_DIMS = "ROBOG_EMBED_DIMS"
ENV_TPM = "ROBOG_EMBED_TPM"

_RETRYABLE_MARKERS = (
    "429",
    "resource_exhausted",
    "rate limit",
    "quota",
    "503",
    "unavailable",
    "deadline_exceeded",
    "timed out",
)
_QUOTA_MARKERS = ("429", "resource_exhausted", "rate limit", "quota")
_DAILY_QUOTA_MARKERS = ("perday", "per_day", "requests per day", "daily limit")
_RETRY_AFTER_RE = re.compile(
    r"(?:retry\s+in|retrydelay['\":\s]+)\s*(\d+(?:\.\d+)?)\s*s",
    re.IGNORECASE,
)


class VectorStoreUnavailable(RuntimeError):
    """The dense path cannot be used. The message says what to run to fix it."""


class VectorStoreBusy(VectorStoreUnavailable):
    """The dense path cannot be used *right now* (rate limit, slow response).

    Raised while answering a question, where waiting for the quota window is
    not an option: the engine carries on with keyword retrieval and tries the
    dense path again a little later.
    """


#: While a user is waiting for an answer, a query embedding may take this long ...
QUERY_EMBED_TIMEOUT = 8.0
#: ... and is retried only if the wait before the retry is this short.
QUERY_EMBED_MAX_WAIT = 2.0


@dataclass
class DenseHit:
    """One chunk returned by the vector store, with its score."""

    chunk_id: str
    score: float            # 1 - cosine distance (higher is better)
    text: str
    metadata: dict


# ---------------------------------------------------------------------------
# Shared vector & Gemini helpers (used by both ingest and retrieve)
# ---------------------------------------------------------------------------
def l2_normalize(vector: list[float]) -> list[float]:
    """Return a unit-length copy of ``vector`` (safe for zero vectors)."""
    norm = math.sqrt(sum(float(v) * float(v) for v in vector))
    if norm <= 0.0:
        return [float(v) for v in vector]
    return [float(v) / norm for v in vector]


def corpus_fingerprint(chunk_ids) -> str:
    """Stable digest of the chunk ids a vector index was built from.

    A Chroma collection outlives the ``build/chunks.jsonl`` it was embedded
    from: re-run ingest with a changed chunker and the store still answers with
    vectors -- and text -- for chunks that no longer exist. The engine serves
    such a dense-only hit from the vector store (``_chunk_view``), so a stale
    index can put words in an answer that are in no current manual. Digest the
    ids (order-insensitive) so ``connect`` can refuse it loudly instead.
    """
    digest = hashlib.sha256()
    for chunk_id in sorted(str(cid) for cid in chunk_ids):
        digest.update(chunk_id.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()[:16]


def resolve_gemini_api_key() -> str | None:
    """Load ``.env`` if present and return ``GEMINI_API_KEY`` or ``GOOGLE_API_KEY``."""
    load_dotenv()
    key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    return key or None


def create_gemini_client(api_key: str | None = None):
    """Create a ``google.genai.Client`` with clear errors when key/SDK is missing."""
    resolved_key = (api_key or resolve_gemini_api_key() or "").strip()
    if not resolved_key:
        raise VectorStoreUnavailable(
            "GEMINI_API_KEY is not set -- create a free key in Google AI Studio, "
            "put GEMINI_API_KEY=... in .env (or export it), or use --provider local"
        )
    try:
        from google import genai
    except ImportError as exc:
        raise VectorStoreUnavailable(
            "google-genai is not installed -- pip install -r requirements-rag.txt"
        ) from exc
    return genai.Client(api_key=resolved_key)


def _is_daily_quota_error(exc: Exception) -> bool:
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in msg for marker in _DAILY_QUOTA_MARKERS)


def _is_quota_error(exc: Exception) -> bool:
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in msg for marker in _QUOTA_MARKERS)


def _is_timeout_error(exc: Exception) -> bool:
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in msg for marker in ("timeout", "timed out", "deadline", "504"))


def _is_retryable_error(exc: Exception) -> bool:
    if _is_daily_quota_error(exc):
        return False
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in msg for marker in _RETRYABLE_MARKERS)


def _parse_retry_after_seconds(exc: Exception) -> float | None:
    """Extract server-advised wait time (e.g. ``Please retry in 42.5s``)."""
    match = _RETRY_AFTER_RE.search(str(exc))
    if not match:
        return None
    try:
        return max(0.0, float(match.group(1)))
    except ValueError:
        return None


def _is_model_not_found(exc: Exception) -> bool:
    msg = f"{type(exc).__name__}: {exc}".lower()
    return "404" in msg or "not_found" in msg or ("not found" in msg and "model" in msg)


def embed_gemini_batch(
    client,
    texts: list[str],
    *,
    model_name: str = GEMINI_MODEL,
    dims: int = DEFAULT_DIMS,
    task_type: str = "RETRIEVAL_QUERY",
    max_retries: int = 4,
    base_delay: float = 1.5,
    quota_retry_seconds: float | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_wait_seconds: float | None = None,
    timeout: float | None = None,
) -> list[list[float]]:
    """Embed ``texts`` with ``google-genai`` and return L2-normalized vectors.

    ``output_dimensionality`` and ``task_type`` are passed via ``config=...``
    matching ``google.genai.types.EmbedContentConfig``. When the free-tier
    per-minute token limit (30,000 TPM) returns ``429 RESOURCE_EXHAUSTED``,
    this honours Google's ``retry in Xs`` delay (or ``quota_retry_seconds``
    during bulk indexing) so the rolling 60-second window resets cleanly.

    That patience is right for indexing and wrong for a chat question, where
    it meant sleeping 40 s and more inside the request. A caller that is
    answering a user sets ``max_wait_seconds`` (a longer advised wait raises
    :class:`VectorStoreBusy` at once) and ``timeout`` (seconds for the HTTP
    call; a timed-out call is not repeated).
    """
    if not texts:
        return []
    config: dict = {
        "output_dimensionality": dims,
        "task_type": task_type,
    }
    if timeout is not None and timeout > 0:
        config["http_options"] = {"timeout": max(1, int(timeout * 1000))}   # milliseconds
    attempt = 0
    while True:
        try:
            response = client.models.embed_content(
                model=model_name,
                contents=list(texts),
                config=config,
            )
            embeddings = getattr(response, "embeddings", None) or []
            return [l2_normalize(list(emb.values)) for emb in embeddings]
        except Exception as exc:
            if _is_model_not_found(exc):
                raise VectorStoreUnavailable(
                    f"Gemini embedding model {model_name!r} was not found ({exc}). "
                    f"Set {ENV_MODEL}=gemini-embedding-001 or gemini-embedding-2-preview "
                    "in .env if your Google AI Studio tier uses that model ID."
                ) from exc
            if _is_daily_quota_error(exc):
                raise VectorStoreUnavailable(
                    f"Gemini daily free-tier quota reached ({exc}). "
                    "Already-saved batches remain in Chroma -- re-run later to resume, "
                    "or run with --provider local for offline indexing."
                ) from exc
            if timeout is not None and _is_timeout_error(exc):
                raise VectorStoreBusy(
                    f"Gemini embedding did not answer within {timeout:g} s"
                ) from exc
            if attempt < max_retries and _is_retryable_error(exc):
                advised = _parse_retry_after_seconds(exc)
                if advised is not None:
                    wait_s = advised + 2.0
                elif quota_retry_seconds is not None and _is_quota_error(exc):
                    wait_s = quota_retry_seconds
                else:
                    wait_s = base_delay * (2 ** attempt)
                if max_wait_seconds is not None and wait_s > max_wait_seconds:
                    raise VectorStoreBusy(
                        f"Gemini embedding is rate limited (retry in {wait_s:.0f} s)"
                    ) from exc
                if wait_s >= 10.0:
                    print(
                        f"  rate limit hit (free-tier TPM window) -- waiting {wait_s:.1f}s "
                        f"before retry {attempt + 1}/{max_retries}..."
                    )
                sleep_fn(wait_s)
                attempt += 1
                continue
            if max_wait_seconds is not None and _is_retryable_error(exc):
                # Still rate limited after the one short retry: temporary, not broken.
                raise VectorStoreBusy(f"Gemini embedding is rate limited ({exc})") from exc
            raise VectorStoreUnavailable(f"Gemini embedding request failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Query embedders (callables: list[str] -> list[list[float]])
# ---------------------------------------------------------------------------
def _local_embedder(model_name: str | None = None):
    resolved_model = (model_name or LOCAL_MODEL).strip()
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise VectorStoreUnavailable(
            "sentence-transformers is not installed -- "
            "pip install -r requirements-rag.txt  (or set ROBOG_EMBED_PROVIDER=gemini)"
        ) from exc
    try:
        model = SentenceTransformer(resolved_model)
    except Exception as exc:
        raise VectorStoreUnavailable(
            f"could not load local embedding model {resolved_model}: {exc}"
        ) from exc

    prefix = E5_QUERY_PREFIX if "e5" in resolved_model.lower() else ""

    def embed(texts: list[str]) -> list[list[float]]:
        vectors = model.encode(
            [prefix + text for text in texts],
            normalize_embeddings=True,
        )
        return vectors.tolist()

    return embed


def _gemini_embedder(dims: int, model_name: str | None = None):
    resolved_model = (model_name or os.environ.get(ENV_MODEL) or GEMINI_MODEL).strip()
    client = create_gemini_client()

    def embed(texts: list[str]) -> list[list[float]]:
        return embed_gemini_batch(
            client,
            list(texts),
            model_name=resolved_model,
            dims=dims,
            task_type="RETRIEVAL_QUERY",
            # A user is waiting: one short retry, a hard time limit, and no
            # sleeping through a quota window (keyword retrieval answers instead).
            max_retries=1,
            base_delay=1.0,
            max_wait_seconds=QUERY_EMBED_MAX_WAIT,
            timeout=QUERY_EMBED_TIMEOUT,
        )

    return embed


def make_embedder(
    provider: str | None = None,
    dims: int | None = None,
    *,
    model: str | None = None,
):
    """Build the query embedder. Returns ``(embedder, provider_name)``.

    Parameters override the environment variables (loaded from ``.env`` when
    present); no API key is ever stored in code.
    """
    load_dotenv()
    provider_name = (provider or os.environ.get(ENV_PROVIDER, "local")).strip().lower()
    resolved_model = (model or os.environ.get(ENV_MODEL) or "").strip() or None
    if dims is None:
        raw_dims = os.environ.get(ENV_DIMS, str(DEFAULT_DIMS))
        try:
            dims = int(raw_dims)
        except ValueError:
            raise VectorStoreUnavailable(f"{ENV_DIMS}={raw_dims!r} is not an integer") from None
    if provider_name == "local":
        return _local_embedder(resolved_model), "local"
    if provider_name == "gemini":
        if not 128 <= dims <= 3072:
            raise VectorStoreUnavailable(
                f"{ENV_DIMS}={dims} out of range (Gemini embeddings support 128-3072)"
            )
        return _gemini_embedder(dims, resolved_model), "gemini"
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
                dims: int | None = None,
                *,
                model: str | None = None,
                corpus_ids=None) -> "ChromaRetriever":
        """Open the existing store. Never creates anything.

        Auto-detects ``provider``, ``model`` and ``dims`` recorded in the
        Chroma collection metadata by ``ingest.embed_index`` when not
        explicitly overridden, and rejects accidental provider mismatches
        (e.g. querying a 1536-dim Gemini index with a 384-dim local model).

        ``corpus_ids`` are the chunk ids of the corpus currently loaded; when
        the collection records a different ``corpus_digest`` the index is stale
        and is refused (keyword retrieval carries on, and says why).
        """
        load_dotenv()
        path = Path(persist_dir or os.environ.get(ENV_PATH, DEFAULT_CHROMA_PATH))
        name = collection_name or os.environ.get(ENV_COLLECTION, DEFAULT_COLLECTION)
        hint_provider = provider or os.environ.get(ENV_PROVIDER, "gemini")
        if not path.is_dir():
            raise VectorStoreUnavailable(
                f"no vector store at '{path}' -- run "
                f"python -m ingest.embed_index --provider {hint_provider} first"
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

        stored_meta = getattr(collection, "metadata", None) or {}

        stored_digest = str(stored_meta.get("corpus_digest") or "").strip()
        if stored_digest and corpus_ids is not None:
            current_ids = list(corpus_ids)
            current_digest = corpus_fingerprint(current_ids)
            if current_digest != stored_digest:
                provider_hint = stored_meta.get("provider") or "gemini"
                raise VectorStoreUnavailable(
                    f"collection '{name}' in '{path}' is stale: embedded from "
                    f"{stored_meta.get('corpus_chunks', '?')} chunks (digest {stored_digest}), "
                    f"the corpus now has {len(current_ids)} (digest {current_digest}) -- re-run "
                    f"python -m ingest.embed_index --provider {provider_hint} --reset"
                )

        stored_provider = str(stored_meta.get("provider") or "").strip().lower() or None
        explicit_provider = (provider or os.environ.get(ENV_PROVIDER) or "").strip().lower() or None

        if explicit_provider and stored_provider and explicit_provider != stored_provider:
            raise VectorStoreUnavailable(
                f"collection '{name}' in '{path}' was indexed with provider={stored_provider!r} "
                f"(dims={stored_meta.get('dims', '?')}), cannot query with provider={explicit_provider!r} -- "
                f"use --provider {stored_provider} (or {ENV_PROVIDER}={stored_provider}) "
                f"or rebuild with: python -m ingest.embed_index --provider {explicit_provider} --reset"
            )

        resolved_provider = explicit_provider or stored_provider or "local"
        resolved_dims = dims
        if resolved_dims is None and ENV_DIMS not in os.environ and stored_meta.get("dims"):
            try:
                resolved_dims = int(stored_meta["dims"])
            except (TypeError, ValueError):
                resolved_dims = None

        resolved_model = model or os.environ.get(ENV_MODEL) or None
        if resolved_model is None and stored_meta.get("model") and (
            not stored_provider or resolved_provider == stored_provider
        ):
            resolved_model = str(stored_meta["model"])

        if resolved_model is not None:
            embedder, provider_name = make_embedder(resolved_provider, resolved_dims, model=resolved_model)
        else:
            embedder, provider_name = make_embedder(resolved_provider, resolved_dims)
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
