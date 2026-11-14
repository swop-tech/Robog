"""Tests for the dense (vector) retrieval path.

Run from the repository root:

    python -m unittest tests.test_vector -v

Everything here is offline and self-contained: no Chroma installation, no
network, no embedding model download, and no real vector database. A
deterministic hashed "embedding" stands in for e5/Gemini, and a fake collection
implements the Chroma ``query()`` API surface the retriever relies on. The fake
is a test double, not a second vector database -- the production path is always
the real ``build/chroma`` collection built by ``python -m ingest.embed_index``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import unittest
from pathlib import Path

from retrieve.engine import SearchEngine
from retrieve.params import ParamIndex
from retrieve.text import tokenize
from retrieve.vector import (
    ChromaRetriever,
    DenseHit,
    VectorStoreUnavailable,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class FakeEmbedder:
    """Deterministic bag-of-words embeddings: stable, free, good enough to rank."""

    DIM = 64

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * self.DIM
            for token in tokenize(text):
                digest = hashlib.md5(token.encode("utf-8")).hexdigest()
                vec[int(digest, 16) % self.DIM] += 1.0
            norm = math.sqrt(sum(value * value for value in vec)) or 1.0
            vectors.append([value / norm for value in vec])
        return vectors


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return 1.0 - dot  # both vectors are normalised


class FakeCollection:
    """Minimal stand-in for a Chroma collection (ids/query/count)."""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self._embeddings = FakeEmbedder().embed([row["text"] for row in rows])

    def count(self) -> int:
        return len(self._rows)

    def query(self, query_embeddings, n_results, include=None, where=None):
        query = query_embeddings[0]
        distances = [_cosine_distance(query, emb) for emb in self._embeddings]
        order = sorted(range(len(distances)), key=lambda i: distances[i])[:n_results]
        return {
            "ids": [[self._rows[i]["id"] for i in order]],
            "documents": [[self._rows[i]["text"] for i in order]],
            "metadatas": [[self._rows[i]["metadata"] for i in order]],
            "distances": [[distances[i] for i in order]],
        }


def _row(chunk_id: str, text: str, language: str = "en", product: str = "FlashBot") -> dict:
    return {
        "id": chunk_id,
        "text": text,
        "metadata": {
            "product": product,
            "language": language,
            "chunk_type": "text",
            "title": f"{product} User Manual",
            "section": "2",
            "section_number": "2.1",
            "section_title": "Overview",
            "page": 40,
            "translation_group": f"tg-{chunk_id}",
        },
    }


def _chunk(row: dict) -> dict:
    """Corpus row as found in build/chunks.jsonl (id/text/metadata)."""
    return {"id": row["id"], "text": row["text"], "metadata": row["metadata"]}


ROWS = [
    _row("en-payload", "FlashBot payload 20 kg maximum allowable load", "en"),
    _row("en-speed", "FlashBot maximum speed 1.2 m/s on flat floor", "en"),
    _row("de-payload", "FlashBot Ladekapazität 20 kg maximale Zuladung", "de"),
]


def _retriever(rows=ROWS) -> ChromaRetriever:
    return ChromaRetriever(
        FakeCollection(rows),
        FakeEmbedder().embed,  # embedder is a plain callable: list[str] -> vectors
        provider="fake",
        collection_name="robog",
    )


# ---------------------------------------------------------------------------
class RetrieverTests(unittest.TestCase):
    def setUp(self):
        self.retriever = _retriever()

    def test_returns_ranked_hits_with_metadata(self):
        hits = self.retriever.search("payload kg", k=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk_id, "en-payload")
        self.assertEqual(hits[0].metadata["product"], "FlashBot")
        self.assertEqual(hits[0].metadata["page"], 40)
        self.assertIn("20 kg", hits[0].text)

    def test_scores_follow_cosine_similarity(self):
        hits = self.retriever.search("speed", k=3)
        self.assertGreaterEqual(hits[0].score, hits[-1].score)

    def test_empty_query_returns_nothing(self):
        self.assertEqual(self.retriever.search("", k=3), [])
        self.assertEqual(self.retriever.search("   ", k=3), [])

    def test_allowed_ids_are_enforced(self):
        hits = self.retriever.search("payload", k=5, allowed_ids={"de-payload"})
        self.assertEqual([hit.chunk_id for hit in hits], ["de-payload"])

    def test_unallowed_language_gets_zero_rows(self):
        hits = self.retriever.search("payload", k=5, allowed_ids={"nonexistent"})
        self.assertEqual(hits, [])

    def test_count(self):
        self.assertEqual(self.retriever.count, 3)


class ConnectTests(unittest.TestCase):
    def test_missing_directory_raises_actionable_error(self):
        with self.assertRaises(VectorStoreUnavailable) as ctx:
            ChromaRetriever.connect(persist_dir="does/not/exist-anywhere")
        self.assertIn("ingest.embed_index", str(ctx.exception))

    def test_empty_directory_raises(self):
        # Either chromadb is missing or the collection does not exist yet --
        # both must surface as VectorStoreUnavailable, never as a raw crash.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(VectorStoreUnavailable):
                ChromaRetriever.connect(persist_dir=tmp)


class FailingRetriever:
    provider = "fake"

    def search(self, query, k, *, allowed_ids=None):
        raise VectorStoreUnavailable("boom")


class EngineFusionTests(unittest.TestCase):
    def setUp(self):
        self.chunks = [_chunk(row) for row in ROWS]
        self.params = ParamIndex([])

    def test_dense_hits_are_fused_and_deduplicated(self):
        engine = SearchEngine(self.chunks, self.params, retriever=_retriever())
        result = engine.search("payload kg", k=3)
        ids = [hit.chunk_id for hit in result.hits]
        self.assertEqual(len(ids), len(set(ids)), "a chunk must never appear twice")
        self.assertTrue(any("vector" in hit.sources for hit in result.hits))
        self.assertTrue(any("bm25" in hit.sources for hit in result.hits))

    def test_dense_only_hits_carry_citation_metadata(self):
        # Empty corpus forces every hit to come from the dense side; the
        # citation must still be built from the vector store's metadata.
        engine = SearchEngine([], ParamIndex([]), retriever=_retriever())
        result = engine.search("payload kg", k=2)
        self.assertTrue(result.hits)
        citation = result.hits[0].citation
        self.assertIn("p. 40", citation)
        self.assertIn("FlashBot User Manual", citation)

    def test_vector_failure_falls_back_to_keyword_search(self):
        engine = SearchEngine(self.chunks, self.params, retriever=FailingRetriever())
        result = engine.search("payload kg", k=3)
        self.assertTrue(result.hits, "keyword retrieval must keep answering")
        self.assertIsNone(engine.retriever, "broken retriever is disabled after failure")
        self.assertTrue(any("dense retrieval disabled: boom" in note for note in result.notes))

    def test_rank_tier_fuses_dense_list_directly(self):
        engine = SearchEngine(self.chunks, self.params)
        dense = [DenseHit(chunk_id="de-payload", score=0.99,
                          text=ROWS[2]["text"], metadata=ROWS[2]["metadata"])]
        hits = engine._rank_tier(["payload"], ["payload"], allowed=None, k=4, dense=dense)
        by_id = {hit.chunk_id: hit for hit in hits}
        self.assertIn("de-payload", by_id)
        self.assertEqual(by_id["de-payload"].sources, ["vector"])

    def test_from_build_auto_missing_store_keeps_keyword_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            chunks_path = Path(tmp) / "chunks.jsonl"
            chunks_path.write_text(
                json.dumps({"id": "c1", "text": "FlashBot payload 20 kg",
                            "metadata": {"language": "en", "product": "FlashBot"}}) + "\n",
                encoding="utf-8",
            )
            old_path = os.environ.get("ROBOG_CHROMA_PATH")
            os.environ["ROBOG_CHROMA_PATH"] = str(Path(tmp) / "no-such-store")
            try:
                engine = SearchEngine.from_build(chunks_path, Path(tmp) / "params.jsonl",
                                                 vector="auto")
            finally:
                if old_path is None:
                    os.environ.pop("ROBOG_CHROMA_PATH", None)
                else:
                    os.environ["ROBOG_CHROMA_PATH"] = old_path
        self.assertIsNone(engine.retriever)
        self.assertIn("dense retrieval disabled", engine.retriever_note or "")

class FilterRegressionTests(unittest.TestCase):
    def test_finds_allowed_result_beyond_initial_candidates(self):
        rows = [_row(f"other-{i}", "payload", product="Other") for i in range(100)]
        rows.append(_row("wanted", "unrelated words", product="Wanted"))
        hits = _retriever(rows).search("payload", k=1, allowed_ids={"wanted"})
        self.assertEqual([hit.chunk_id for hit in hits], ["wanted"])


class RealChromaIntegrationTests(unittest.TestCase):
    def test_persistent_store_and_engine_with_deterministic_embeddings(self):
        try:
            import chromadb
        except ImportError:
            self.skipTest("install chromadb to run the real-store integration test")
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            client = chromadb.PersistentClient(path=tmp)
            collection = client.create_collection(name="robog", metadata={"hnsw:space": "cosine"})
            collection.add(ids=[r["id"] for r in ROWS],
                           documents=[r["text"] for r in ROWS],
                           metadatas=[r["metadata"] for r in ROWS],
                           embeddings=FakeEmbedder().embed([r["text"] for r in ROWS]))
            with patch("retrieve.vector.make_embedder", return_value=(FakeEmbedder().embed, "fake")):
                retriever = ChromaRetriever.connect(persist_dir=tmp)
            hits = retriever.search("payload", k=2, allowed_ids={"de-payload"})
            self.assertEqual([hit.chunk_id for hit in hits], ["de-payload"])
            engine = SearchEngine(ROWS, ParamIndex([]), retriever=retriever)
            result = engine.search("payload kg", k=3, language="en")
            self.assertTrue(any("vector" in h.sources for h in result.hits))

    def test_index_chunks_roundtrip_and_dimension_switch_on_real_chroma(self):
        try:
            import chromadb  # noqa: F401
        except ImportError:
            self.skipTest("install chromadb to run the real-store integration test")
        from unittest.mock import patch
        from ingest.embed_index import GeminiEmbedder, index_chunks

        with tempfile.TemporaryDirectory() as tmp:
            chunks_file = Path(tmp) / "chunks.jsonl"
            chunks_file.write_text(
                "\n".join(json.dumps(r) for r in ROWS) + "\n",
                encoding="utf-8",
            )
            persist_dir = Path(tmp) / "chroma"

            # First index at 128 dims, then re-index at 256 dims to prove real
            # Chroma recreates cleanly instead of raising InvalidDimensionException.
            client_128 = _StrictGenAIClient()
            index_chunks(
                chunks_file,
                persist_dir,
                provider="gemini",
                dims=128,
                embedder=GeminiEmbedder(dims=128, client=client_128),
            )
            client_256 = _StrictGenAIClient()
            total = index_chunks(
                chunks_file,
                persist_dir,
                provider="gemini",
                dims=256,
                embedder=GeminiEmbedder(dims=256, client=client_256),
            )
            self.assertEqual(total, len(ROWS))

            clean_env = {
                k: v for k, v in os.environ.items()
                if k not in ("ROBOG_EMBED_PROVIDER", "ROBOG_EMBED_DIMS", "ROBOG_EMBED_MODEL")
            }
            query_client = _StrictGenAIClient()
            with patch.dict(os.environ, clean_env, clear=True), \
                 patch("retrieve.vector.load_dotenv", return_value=None), \
                 patch("retrieve.vector.create_gemini_client", return_value=query_client):
                # No provider or dims passed -- auto-detected from Chroma metadata!
                retriever = ChromaRetriever.connect(persist_dir=persist_dir)
                self.assertEqual(retriever.provider, "gemini")
                hits = retriever.search("payload kg", k=2)
                self.assertEqual(len(hits), 2)
                self.assertEqual(
                    query_client.models.calls[0]["config"],
                    {"output_dimensionality": 256, "task_type": "RETRIEVAL_QUERY"},
                )


# ---------------------------------------------------------------------------
# .env loader, Gemini SDK contract, L2 normalization & Chroma metadata tests
# ---------------------------------------------------------------------------
class _FakeEmbeddingObj:
    def __init__(self, values: list[float]):
        self.values = values


class _FakeEmbedResponse:
    def __init__(self, embeddings: list[list[float]]):
        self.embeddings = [_FakeEmbeddingObj(v) for v in embeddings]


class _StrictGenAIModels:
    """Mimics google.genai.models.Models.embed_content(*, model, contents, config=None).

    Any unexpected top-level keyword (such as the old broken
    ``output_dimensionality=...``) raises ``TypeError`` just like the real SDK.
    """

    def __init__(self, fail_times: int = 0, fail_error: Exception | None = None):
        self.calls: list[dict] = []
        self.fail_times = fail_times
        self.fail_error = fail_error

    def embed_content(self, *, model: str, contents, config=None):
        self.calls.append({"model": model, "contents": list(contents), "config": dict(config or {})})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise (self.fail_error or RuntimeError("429 RESOURCE_EXHAUSTED: rate limit"))
        if self.fail_error is not None:
            raise self.fail_error
        dim = int((config or {}).get("output_dimensionality") or 4)
        # Deliberately unnormalized vectors [3.0, 4.0, 0.0, ...] (norm = 5.0)
        raw = [[3.0, 4.0] + [0.0] * max(0, dim - 2) for _ in contents]
        return _FakeEmbedResponse(raw)


class _StrictGenAIClient:
    def __init__(self, fail_times: int = 0, fail_error: Exception | None = None):
        self.models = _StrictGenAIModels(fail_times=fail_times, fail_error=fail_error)


class DotenvTests(unittest.TestCase):
    def test_parses_comments_quotes_and_exports(self):
        from retrieve.env import parse_dotenv

        parsed = parse_dotenv(
            "# comment\n"
            "GEMINI_API_KEY='AIza-test-123'\n"
            "export ROBOG_EMBED_PROVIDER=\"gemini\"\n"
            "ROBOG_EMBED_DIMS=768 # inline comment\n"
            "INVALID LINE\n"
        )
        self.assertEqual(parsed["GEMINI_API_KEY"], "AIza-test-123")
        self.assertEqual(parsed["ROBOG_EMBED_PROVIDER"], "gemini")
        self.assertEqual(parsed["ROBOG_EMBED_DIMS"], "768")

    def test_load_dotenv_does_not_clobber_existing_env(self):
        from unittest.mock import patch
        from retrieve.env import load_dotenv

        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "GEMINI_API_KEY=from-file\nROBOG_EMBED_MODEL=gemini-embedding-001\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"GEMINI_API_KEY": "already-exported"}, clear=False):
                os.environ.pop("ROBOG_EMBED_MODEL", None)
                loaded = load_dotenv(env_file)
                self.assertEqual(loaded, env_file)
                self.assertEqual(os.environ["GEMINI_API_KEY"], "already-exported")
                self.assertEqual(os.environ["ROBOG_EMBED_MODEL"], "gemini-embedding-001")


class GeminiEmbeddingContractTests(unittest.TestCase):
    def test_document_embedder_uses_config_task_type_and_normalizes(self):
        from ingest.embed_index import GeminiEmbedder

        client = _StrictGenAIClient()
        embedder = GeminiEmbedder(dims=128, model_name="gemini-embedding-2", batch_size=2, client=client)
        vectors = embedder.embed(["first chunk", "second chunk", "third chunk"])

        self.assertEqual(len(vectors), 3)
        self.assertEqual(len(client.models.calls), 2, "3 texts at batch_size=2 -> 2 API calls")
        for call in client.models.calls:
            self.assertEqual(call["model"], "gemini-embedding-2")
            self.assertEqual(
                call["config"],
                {"output_dimensionality": 128, "task_type": "RETRIEVAL_DOCUMENT"},
            )
        for vec in vectors:
            self.assertEqual(len(vec), 128)
            norm = math.sqrt(sum(v * v for v in vec))
            self.assertAlmostEqual(norm, 1.0, places=6)
            self.assertAlmostEqual(vec[0], 0.6, places=6)
            self.assertAlmostEqual(vec[1], 0.8, places=6)

    def test_query_embedder_uses_retrieval_query_task_type(self):
        from unittest.mock import patch
        from retrieve.vector import make_embedder

        client = _StrictGenAIClient()
        with patch("retrieve.vector.create_gemini_client", return_value=client):
            embed_fn, provider = make_embedder("gemini", 256, model="gemini-embedding-001")
        self.assertEqual(provider, "gemini")
        vectors = embed_fn(["how heavy is FlashBot?"])
        self.assertEqual(len(vectors), 1)
        self.assertEqual(len(vectors[0]), 256)
        self.assertEqual(
            client.models.calls[0]["config"],
            {"output_dimensionality": 256, "task_type": "RETRIEVAL_QUERY"},
        )
        self.assertEqual(client.models.calls[0]["model"], "gemini-embedding-001")

    def test_retries_transient_rate_limit_errors(self):
        from retrieve.vector import embed_gemini_batch

        sleeps: list[float] = []
        client = _StrictGenAIClient(fail_times=2)
        vectors = embed_gemini_batch(
            client,
            ["test"],
            dims=128,
            base_delay=0.5,
            sleep_fn=sleeps.append,
        )
        self.assertEqual(len(vectors), 1)
        self.assertEqual(len(client.models.calls), 3)
        self.assertEqual(sleeps, [0.5, 1.0])

    def test_model_not_found_gives_model_override_hint(self):
        from retrieve.vector import embed_gemini_batch

        client = _StrictGenAIClient(fail_error=RuntimeError("404 NOT_FOUND: model not found"))
        with self.assertRaises(VectorStoreUnavailable) as ctx:
            embed_gemini_batch(client, ["test"], model_name="gemini-embedding-2", dims=128)
        self.assertIn("ROBOG_EMBED_MODEL", str(ctx.exception))


class IndexAndCollectionMetadataTests(unittest.TestCase):
    def test_format_passage_adds_product_and_section_without_mutating_stored_doc(self):
        from ingest.embed_index import format_passage, index_chunks

        row = _row("c1", "Check battery switch.", "en", "BellaBot")
        passage = format_passage(row)
        self.assertIn("BellaBot", passage)
        self.assertIn("Check battery switch.", passage)

        # Fake in-memory Chroma client to verify collection replacement & stored docs
        class _MemCollection:
            def __init__(self, name: str, metadata: dict):
                self.name = name
                self.metadata = dict(metadata)
                self.upserted: list[dict] = []

            def count(self) -> int:
                return sum(len(b["ids"]) for b in self.upserted)

            def upsert(self, *, ids, documents, metadatas, embeddings):
                self.upserted.append(
                    {"ids": ids, "documents": documents, "metadatas": metadatas, "embeddings": embeddings}
                )

        class _MemClient:
            def __init__(self):
                self.collections: dict[str, _MemCollection] = {}
                self.deleted: list[str] = []

            def get_collection(self, name: str):
                if name not in self.collections:
                    raise ValueError("missing")
                return self.collections[name]

            def delete_collection(self, name: str):
                self.deleted.append(name)
                self.collections.pop(name, None)

            def get_or_create_collection(self, name: str, metadata: dict):
                if name not in self.collections:
                    self.collections[name] = _MemCollection(name, metadata)
                return self.collections[name]

        mem_client = _MemClient()
        # Seed an incompatible 384-dim 'local' collection first
        mem_client.get_or_create_collection(
            "robog", {"hnsw:space": "cosine", "provider": "local", "model": "e5", "dims": 384}
        )

        with tempfile.TemporaryDirectory() as tmp:
            chunks_file = Path(tmp) / "chunks.jsonl"
            chunks_file.write_text(json.dumps(row) + "\n", encoding="utf-8")
            gemini_client = _StrictGenAIClient()
            from ingest.embed_index import GeminiEmbedder

            embedder = GeminiEmbedder(dims=128, model_name="gemini-embedding-2", client=gemini_client)
            total = index_chunks(
                chunks_file,
                Path(tmp) / "chroma",
                provider="gemini",
                dims=128,
                embedder=embedder,
                chroma_client=mem_client,
            )

        self.assertEqual(total, 1)
        self.assertEqual(mem_client.deleted, ["robog"], "stale 384d local collection must be replaced")
        coll = mem_client.collections["robog"]
        self.assertEqual(coll.metadata["provider"], "gemini")
        self.assertEqual(coll.metadata["dims"], 128)
        # Stored document text remains clean verbatim text (no prepended header)
        self.assertEqual(coll.upserted[0]["documents"], ["Check battery switch."])
        # Embedded input included the product context
        self.assertIn("BellaBot", gemini_client.models.calls[0]["contents"][0])

    def test_connect_autodetects_store_metadata_and_guards_mismatch(self):
        from unittest.mock import patch

        class _StoredCollection:
            metadata = {"provider": "gemini", "model": "gemini-embedding-001", "dims": 768}

            def count(self) -> int:
                return 3

        class _StoredClient:
            def get_collection(self, name: str):
                return _StoredCollection()

        with tempfile.TemporaryDirectory() as tmp:
            clean_env = {
                k: v for k, v in os.environ.items()
                if k not in ("ROBOG_EMBED_PROVIDER", "ROBOG_EMBED_DIMS", "ROBOG_EMBED_MODEL")
            }
            with patch.dict(os.environ, clean_env, clear=True), \
                 patch("retrieve.vector.load_dotenv", return_value=None), \
                 patch("retrieve.vector._load_client", return_value=_StoredClient()), \
                 patch("retrieve.vector.make_embedder", return_value=(FakeEmbedder().embed, "gemini")) as mocked:
                retriever = ChromaRetriever.connect(persist_dir=tmp)
                self.assertEqual(retriever.provider, "gemini")
                mocked.assert_called_once_with("gemini", 768, model="gemini-embedding-001")

                # Forcing provider='local' on a gemini store must raise an actionable error
                with self.assertRaises(VectorStoreUnavailable) as ctx:
                    ChromaRetriever.connect(persist_dir=tmp, provider="local")
                self.assertIn("provider='gemini'", str(ctx.exception))


class RateLimitAndResumeTests(unittest.TestCase):
    def test_token_rate_limiter_paces_sliding_minute_window(self):
        from ingest.embed_index import TokenRateLimiter

        now = [100.0]
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(round(seconds, 2))
            now[0] += seconds

        limiter = TokenRateLimiter(1_000, clock=lambda: now[0], sleep_fn=fake_sleep)
        self.assertEqual(limiter.acquire(600), 0.0)
        now[0] += 10.0  # t=110.0, 600 used in window
        slept = limiter.acquire(600)  # 600 + 600 > 1000 -> waits until t=160.5
        self.assertAlmostEqual(slept, 50.5, places=1)
        self.assertEqual(len(sleeps), 1)

    def test_honours_google_retry_in_seconds_and_stops_on_daily_quota(self):
        from retrieve.vector import embed_gemini_batch

        sleeps: list[float] = []
        client = _StrictGenAIClient(
            fail_times=1,
            fail_error=RuntimeError("429 RESOURCE_EXHAUSTED: Quota exceeded. Please retry in 42.5s."),
        )
        # Clear fail_error once fail_times drops to 0 so second attempt succeeds
        orig_embed = client.models.embed_content

        def _fail_once(*, model, contents, config=None):
            if client.models.fail_times <= 0:
                client.models.fail_error = None
            return orig_embed(model=model, contents=contents, config=config)

        client.models.embed_content = _fail_once
        vectors = embed_gemini_batch(client, ["chunk"], dims=128, sleep_fn=sleeps.append)
        self.assertEqual(len(vectors), 1)
        self.assertEqual(sleeps, [44.5])  # 42.5s advised + 2.0s safety margin

        daily_sleeps: list[float] = []
        daily_client = _StrictGenAIClient(
            fail_error=RuntimeError("429 RESOURCE_EXHAUSTED: EmbedContentRequestsPerDayPerUser")
        )
        with self.assertRaises(VectorStoreUnavailable) as ctx:
            embed_gemini_batch(daily_client, ["chunk"], dims=128, sleep_fn=daily_sleeps.append)
        self.assertEqual(daily_sleeps, [], "daily quota exhaustion must not sleep")
        self.assertIn("daily", str(ctx.exception).lower())

    def test_incremental_checkpoint_and_resume_skips_completed_chunks(self):
        from ingest.embed_index import GeminiEmbedder, index_chunks

        class _CheckpointCollection:
            def __init__(self, name: str, metadata: dict):
                self.name = name
                self.metadata = dict(metadata)
                self.rows: dict[str, dict] = {}

            def count(self) -> int:
                return len(self.rows)

            def get(self, include=None):
                return {"ids": list(self.rows.keys())}

            def upsert(self, *, ids, documents, metadatas, embeddings):
                for cid, doc, meta, emb in zip(ids, documents, metadatas, embeddings):
                    self.rows[cid] = {"doc": doc, "meta": meta, "emb": emb}

        class _CheckpointClient:
            def __init__(self):
                self.colls: dict[str, _CheckpointCollection] = {}

            def get_collection(self, name: str):
                if name not in self.colls:
                    raise ValueError("missing")
                return self.colls[name]

            def delete_collection(self, name: str):
                self.colls.pop(name, None)

            def get_or_create_collection(self, name: str, metadata: dict):
                if name not in self.colls:
                    self.colls[name] = _CheckpointCollection(name, metadata)
                return self.colls[name]

        chroma = _CheckpointClient()
        with tempfile.TemporaryDirectory() as tmp:
            chunks_file = Path(tmp) / "chunks.jsonl"
            chunks_file.write_text(
                "\n".join(json.dumps(r) for r in ROWS) + "\n",
                encoding="utf-8",
            )
            # Simulate a run that succeeds on batch 1 (2 rows) and crashes on batch 2
            call_count = [0]
            first_client = _StrictGenAIClient()
            real_call = first_client.models.embed_content

            def _crash_second_batch(*, model, contents, config=None):
                call_count[0] += 1
                if call_count[0] == 2:
                    raise RuntimeError("429 RESOURCE_EXHAUSTED: EmbedContentRequestsPerDayPerUser")
                return real_call(model=model, contents=contents, config=config)

            first_client.models.embed_content = _crash_second_batch
            embedder_1 = GeminiEmbedder(
                dims=128, batch_size=2, tpm_limit=0, client=first_client, sleep_fn=lambda _: None
            )
            with self.assertRaises(SystemExit):
                index_chunks(
                    chunks_file, Path(tmp) / "chroma",
                    provider="gemini", dims=128, batch_size=2,
                    embedder=embedder_1, chroma_client=chroma,
                )

            # First 2 chunks are ALREADY persisted in Chroma despite batch 2 failing!
            self.assertEqual(chroma.colls["robog"].count(), 2)

            # Re-running resumes cleanly and ONLY embeds the 1 remaining chunk!
            second_client = _StrictGenAIClient()
            embedder_2 = GeminiEmbedder(
                dims=128, batch_size=2, tpm_limit=0, client=second_client, sleep_fn=lambda _: None
            )
            total = index_chunks(
                chunks_file, Path(tmp) / "chroma",
                provider="gemini", dims=128, batch_size=2,
                embedder=embedder_2, chroma_client=chroma,
            )
            self.assertEqual(total, 3)
            self.assertEqual(len(second_client.models.calls), 1)
            self.assertEqual(len(second_client.models.calls[0]["contents"]), 1)


if __name__ == "__main__":
    unittest.main()
