"""Unit tests for ingest/embed_index.py -- checkpoint handling, resume
logic and batch-limit plumbing. No network calls: a fake embedder is
injected (duck-typed) in place of GeminiEmbedder."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ingest.embed_index import (
    GeminiEmbedder,
    append_checkpoint,
    embed_gemini_resumable,
    load_checkpoint,
)


class FakeEmbedder:
    model_name = "gemini-embedding-2"
    dims = 4

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed_batch(self, batch):
        self.calls.append(list(batch))
        # deterministic, distinct vectors per input text
        return [[float(len(text)), 1.0, 2.0, 3.0] for text in batch]


def make_rows(n):
    return [
        {"id": f"chunk-{i:04d}", "text": f"text number {i}", "metadata": {"title": f"T{i}"}}
        for i in range(n)
    ]


class GeminiBatchShapeTests(unittest.TestCase):
    """Regression test for the production bug where a list of raw strings
    was folded by the SDK into ONE multipart content, yielding 1 embedding
    for N texts."""

    def _make_embedder(self, capture: dict):
        embedder = GeminiEmbedder.__new__(GeminiEmbedder)  # skip genai.Client()
        embedder.model_name = "gemini-embedding-2"
        embedder.dims = 4

        class FakeModels:
            def embed_content(self, *, model, contents, config):
                capture["model"] = model
                capture["contents"] = contents
                capture["config"] = config
                n = len(contents)
                return SimpleNamespace(
                    embeddings=[SimpleNamespace(values=[1.0, 2.0, 3.0, 4.0]) for _ in range(n)]
                )

        embedder.client = SimpleNamespace(models=FakeModels())
        return embedder

    def test_one_content_per_text_and_config_kwarg(self):
        capture: dict = {}
        embedder = self._make_embedder(capture)
        texts = [f"chunk text {i}" for i in range(5)]
        vectors = embedder.embed_batch(texts)
        self.assertEqual(len(vectors), 5)
        # one Content dict per text, single text part, correct config
        self.assertEqual(len(capture["contents"]), 5)
        for i, content in enumerate(capture["contents"]):
            self.assertEqual(content["role"], "user")
            self.assertEqual(content["parts"], [{"text": texts[i]}])
        self.assertEqual(capture["config"], {"output_dimensionality": 4})
        self.assertNotIn("output_dimensionality", capture)  # top-level kwarg must not be used

    def test_counts_mismatch_raises(self):
        capture: dict = {}
        embedder = self._make_embedder(capture)

        class FakeModels:
            def embed_content(self, *, model, contents, config):
                return SimpleNamespace(
                    embeddings=[SimpleNamespace(values=[1.0, 2.0, 3.0, 4.0])]  # always 1
                )

        embedder.client = SimpleNamespace(models=FakeModels())
        with self.assertRaises(RuntimeError):
            embedder.embed_batch(["a", "b"])


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.checkpoint = self.tmp / "ckpt.jsonl"
        self.hash = "a" * 64

    def test_roundtrip(self):
        append_checkpoint(self.checkpoint, chunks_sha256=self.hash,
                          model="gemini-embedding-2", dims=4,
                          records=[("id-1", [0.1, 0.2, 0.3, 0.4])])
        loaded = load_checkpoint(self.checkpoint, chunks_sha256=self.hash,
                                 model="gemini-embedding-2", dims=4)
        self.assertEqual(loaded, {"id-1": [0.1, 0.2, 0.3, 0.4]})
        # second append adds to the same file
        append_checkpoint(self.checkpoint, chunks_sha256=self.hash,
                          model="gemini-embedding-2", dims=4,
                          records=[("id-2", [0.5, 0.6, 0.7, 0.8])])
        loaded = load_checkpoint(self.checkpoint, chunks_sha256=self.hash,
                                 model="gemini-embedding-2", dims=4)
        self.assertEqual(set(loaded), {"id-1", "id-2"})

    def test_mismatch_rejected(self):
        append_checkpoint(self.checkpoint, chunks_sha256=self.hash,
                          model="gemini-embedding-2", dims=4,
                          records=[("id-1", [0.1, 0.2, 0.3, 0.4])])
        for kwargs in ({"chunks_sha256": "b" * 64}, {"model": "other"}, {"dims": 8}):
            params = dict(chunks_sha256=self.hash, model="gemini-embedding-2", dims=4)
            params.update(kwargs)
            with self.assertRaises(SystemExit):
                load_checkpoint(self.checkpoint, **params)

    def test_missing_checkpoint_is_empty(self):
        self.assertEqual(
            load_checkpoint(self.checkpoint, chunks_sha256=self.hash,
                            model="gemini-embedding-2", dims=4),
            {})


class ResumableEmbedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.checkpoint = self.tmp / "ckpt.jsonl"
        self.hash = "c" * 64

    def test_full_then_resume_is_noop(self):
        rows = make_rows(7)
        fake = FakeEmbedder()
        vectors = embed_gemini_resumable(rows, fake, checkpoint_path=self.checkpoint,
                                         chunks_sha256=self.hash, batch_size=3)
        self.assertEqual(len(vectors), 7)
        self.assertEqual(sum(len(c) for c in fake.calls), 7)
        # second run: everything already in the checkpoint -- no API calls
        fake2 = FakeEmbedder()
        vectors2 = embed_gemini_resumable(rows, fake2, checkpoint_path=self.checkpoint,
                                          chunks_sha256=self.hash, batch_size=3)
        self.assertEqual(fake2.calls, [])
        self.assertEqual(vectors, vectors2)

    def test_partial_checkpoint_resumes_remainder(self):
        rows = make_rows(7)
        fake = FakeEmbedder()
        embed_gemini_resumable(rows[:2], fake, checkpoint_path=self.checkpoint,
                               chunks_sha256=self.hash, batch_size=2)
        fake2 = FakeEmbedder()
        vectors = embed_gemini_resumable(rows, fake2, checkpoint_path=self.checkpoint,
                                         chunks_sha256=self.hash, batch_size=4)
        self.assertEqual(len(vectors), 7)
        # only the 5 missing rows were embedded in the second run
        self.assertEqual(sum(len(c) for c in fake2.calls), 5)

    def test_vectors_align_with_rows(self):
        rows = make_rows(5)
        fake = FakeEmbedder()
        vectors = embed_gemini_resumable(rows, fake, checkpoint_path=self.checkpoint,
                                         chunks_sha256=self.hash, batch_size=100)
        for row, vec in zip(rows, vectors):
            i = int(row["id"][-4:])
            self.assertEqual(vec[0], float(len(f"title: T{i} | text: {row['text']}")))

    def test_dims_mismatch_detected(self):
        rows = make_rows(3)
        fake = FakeEmbedder()
        fake.dims = 8  # checkpoint roundtrip still fine; final check must catch it
        with self.assertRaises(SystemExit):
            embed_gemini_resumable(rows, fake, checkpoint_path=self.checkpoint,
                                   chunks_sha256=self.hash, batch_size=3)


if __name__ == "__main__":
    unittest.main()
