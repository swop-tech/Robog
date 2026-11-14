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


class GeminiFormatTests(unittest.TestCase):
    """Official Gemini Embedding 2 (question-answering) templates, text-only."""

    def test_document_text_with_title(self):
        from ingest.embed_index import gemini_document_text
        row = {"text": "Load capacity: 20 kg",
               "metadata": {"title": "FlashBot User Manual V1.0"}}
        self.assertEqual(
            gemini_document_text(row),
            "title: FlashBot User Manual V1.0 | text: Load capacity: 20 kg",
        )

    def test_document_text_without_title_uses_none(self):
        from ingest.embed_index import gemini_document_text
        self.assertEqual(
            gemini_document_text({"text": "x", "metadata": {}}),
            "title: none | text: x",
        )
        # Missing metadata entirely must not crash either.
        self.assertEqual(gemini_document_text({"text": "x"}), "title: none | text: x")

    def test_query_instruction_template(self):
        from retrieve.vector import GEMINI_QUERY_INSTRUCTION, gemini_query_text
        self.assertEqual(
            gemini_query_text("How many kg can the FlashBot carry?"),
            GEMINI_QUERY_INSTRUCTION + "How many kg can the FlashBot carry?",
        )
        self.assertEqual(
            gemini_query_text("q"),
            "task: question answering | query: q",
        )


if __name__ == "__main__":
    unittest.main()
