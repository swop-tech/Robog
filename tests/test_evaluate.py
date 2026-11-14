"""Regression checks for benchmark expectations and missing indexes."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from retrieve.engine import Hit, SearchEngine
from retrieve.evaluate import evaluate
from retrieve.params import ParamHit
from retrieve.profiles import ProfileHit


class StubEngine:
    def __init__(self, params=(), profiles=(), cross=False, route="lookup", image=None, chunk_type="text"):
        meta = {"product": "FlashBot", "language": "en", "chunk_type": chunk_type}
        if image:
            meta["image"] = image
        self.result = SimpleNamespace(
            hits=[Hit("c", "payload 20 kg", meta, 1, 1)],
            params=params,
            profiles=profiles,
            language="en",
            route=route,
            cross_lingual=cross,
            confidence="high",
        )

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

    def test_figure_route_and_image_expectations_are_enforced(self):
        q = {
            "id": "F1",
            "question": "Show me a picture of FlashBot",
            "expect": {"product": "FlashBot", "chunk_type": "figure", "expect_image": True, "expect_route": "figure"},
        }
        self.assertFalse(evaluate(StubEngine(route="figure", chunk_type="figure", image=None), [q]).results[0].passed)
        self.assertFalse(evaluate(StubEngine(route="default", chunk_type="figure", image="images/en.png"), [q]).results[0].passed)
        self.assertTrue(evaluate(StubEngine(route="figure", chunk_type="figure", image="images/en.png"), [q]).results[0].passed)

    def test_profile_card_expectations_are_enforced(self):
        q = {
            "id": "A1",
            "question": "Which robot fits my restaurant?",
            "expect": {
                "expect_route": "advisory",
                "expect_profile_min": 2,
                "expect_profile_top": "BellaBot",
                "expect_profile_category": "delivery",
            },
        }
        bella = ProfileHit({"product": "BellaBot", "category": "delivery", "language": "en"}, 9.0)
        pudu2 = ProfileHit({"product": "PuduBot 2", "category": "delivery", "language": "en"}, 8.0)
        self.assertFalse(evaluate(StubEngine(route="advisory", profiles=[bella]), [q]).results[0].passed)
        res = evaluate(StubEngine(route="advisory", profiles=[bella, pudu2]), [q])
        self.assertTrue(res.results[0].passed)
        self.assertEqual(res.profile_rate, 1.0)

    def test_missing_index_is_reported_in_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            chunks = Path(tmp) / "chunks.jsonl"
            chunks.write_text(json.dumps({"id": "1", "text": "payload 20 kg", "metadata": {}}) + "\n")
            engine = SearchEngine.from_build(chunks, Path(tmp) / "missing.jsonl", profiles_path=Path(tmp) / "no-prof.jsonl")
            result = engine.search("payload")
            self.assertTrue(any("parameter index not found" in note for note in result.notes))


if __name__ == "__main__":
    unittest.main()
