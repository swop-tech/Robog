"""Regression checks for benchmark expectations and missing indexes."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from retrieve.engine import Hit, SearchEngine
from retrieve.evaluate import evaluate
from retrieve.params import ParamHit


class StubEngine:
    def __init__(self, params=(), cross=False):
        self.result = SimpleNamespace(
            hits=[Hit("c", "payload 20 kg", {"product": "FlashBot", "language": "en"}, 1, 1)],
            params=params, language="en", route="lookup", cross_lingual=cross, confidence="high")

    def search(self, *args, **kwargs):
        return self.result


class EvaluateTests(unittest.TestCase):
    def test_parameter_expectation_and_product_are_enforced(self):
        q = {"id": "1", "question": "payload?", "expect": {
            "product": "FlashBot", "keywords": ["20 kg"], "expect_param": True}}
        self.assertFalse(evaluate(StubEngine(), [q]).results[0].passed)
        wrong = ParamHit({"product": "Other", "parameter": "load", "value": "20 kg"}, 1)
        self.assertFalse(evaluate(StubEngine([wrong]), [q]).results[0].passed)
        right = ParamHit({"product": "FlashBot", "parameter": "load", "value": "20 kg"}, 1)
        self.assertTrue(evaluate(StubEngine([right]), [q]).results[0].passed)

    def test_cross_language_expectation_is_enforced(self):
        q = {"id": "1", "question": "payload?", "expect": {
            "keywords": ["20 kg"], "expect_cross_lingual": True}}
        self.assertFalse(evaluate(StubEngine(cross=False), [q]).results[0].passed)
        self.assertTrue(evaluate(StubEngine(cross=True), [q]).results[0].passed)

    def test_missing_index_is_reported_in_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            chunks = Path(tmp) / "chunks.jsonl"
            chunks.write_text(json.dumps({"id": "1", "text": "payload 20 kg", "metadata": {}}) + "\n")
            engine = SearchEngine.from_build(chunks, Path(tmp) / "missing.jsonl")
            result = engine.search("payload")
            self.assertTrue(any("parameter index not found" in note for note in result.notes))
