"""Tests for the retrieval engine (standard library only).

Run from the repository root:

    python -m unittest tests.test_retrieve -v

The unit tests build their own tiny corpora. The corpus tests read the real
manuals in ``data/`` (skipped when the dataset is absent), so they check the
behaviour that matters for the demo: same-language first, cross-lingual only
when needed, no duplicated passages, parameter lookups with citations.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from retrieve.bm25 import BM25
from retrieve.engine import SearchEngine
from retrieve.evaluate import CONFIGS, evaluate
from retrieve.lang import detect_language, plan
from retrieve.lexicon import expand_tokens
from retrieve.params import ParamIndex
from retrieve.router import route
from retrieve.text import fold, numbers, tokenize

DATA = Path(__file__).parent.parent / "data"


class TextTests(unittest.TestCase):
    def test_fold_strips_accents(self):
        self.assertEqual(fold("Kapazität"), "kapazitat")
        self.assertEqual(fold("Velocità"), "velocita")
        self.assertEqual(fold("Straße"), "strasse")

    def test_tokenize_normalises_decimal_comma(self):
        self.assertEqual(tokenize("Gewicht: 14,3 kg"), ["gewicht", "14.3", "kg"])

    def test_tokenize_keeps_units(self):
        self.assertIn("m/s", tokenize("speed 1.2 m/s"))
        self.assertIn("1.2", tokenize("speed 1.2 m/s"))

    def test_stopwords_dropped(self):
        self.assertEqual(tokenize("What is the payload of the robot?"), ["payload", "robot"])

    def test_numbers(self):
        self.assertEqual(numbers("29,2 V"), {"29.2"})
        self.assertEqual(numbers("100–240 V"), {"100", "240"})


class LexiconTests(unittest.TestCase):
    def test_synonyms_bridge_languages(self):
        expansion = expand_tokens(["zuladung"])
        self.assertIn("payload", expansion)
        self.assertIn("ladekapazitat", expansion)      # what the DE manual says
        self.assertIn("carico", expansion)             # Italian

    def test_italian_inflections(self):
        self.assertIn("peso", expand_tokens(["pesa"]))
        self.assertIn("peso", expand_tokens(["pesano"]))

    def test_generic_words_are_not_expanded(self):
        self.assertNotIn("max", expand_tokens(["speed"]))

    def test_unknown_word(self):
        self.assertEqual(expand_tokens(["fluxcapacitor"]), ["fluxcapacitor"])


class LanguageTests(unittest.TestCase):
    def test_detection(self):
        cases = {
            "What is the payload of the FlashBot?": "en",
            "Wieviel Zuladung hat der FlashBot?": "de",
            "Quanto pesa il FlashBot?": "it",
            "Qual è la velocità massima del FlashBot?": "it",
            "In welcher Abbildung ist der Notausschalter?": "de",
            "Gewicht?": "de",
            "Peso?": "it",
        }
        for text, expected in cases.items():
            self.assertEqual(detect_language(text), expected, text)

    def test_ambiguous_short_query_is_undecided(self):
        # "Filter" exists in German and English; guessing would be wrong.
        self.assertIsNone(detect_language("Filter?"))
        self.assertIsNone(detect_language("FlashBot specs"))

    def test_plan_same_language_first(self):
        p = plan("de", {"en", "de", "it"})
        self.assertEqual(p.tiers, ["de", None])
        self.assertFalse(p.cross_lingual)

    def test_plan_cross_lingual_when_language_missing(self):
        p = plan("it", {"en", "de"})
        self.assertEqual(p.tiers, [None])
        self.assertTrue(p.cross_lingual)
        self.assertIn("not available", p.describe())

    def test_plan_without_detection(self):
        self.assertEqual(plan(None, {"en"}).tiers, [None])


class RouterTests(unittest.TestCase):
    def test_lookup(self):
        self.assertEqual(route("What is the payload of the FlashBot?", "en").kind, "lookup")
        self.assertEqual(route("Wie hoch ist die Akkukapazität?", "de").kind, "lookup")

    def test_advisory(self):
        self.assertEqual(route("Which robot is best for a hotel?", "en").kind, "advisory")
        self.assertEqual(route("Welcher Roboter eignet sich für ein Hotel?", "de").kind, "advisory")
        self.assertEqual(route("Quale robot consigli per un ristorante?", "it").kind, "advisory")

    def test_comparison_without_keyword(self):
        self.assertEqual(route("BellaBot vs KettyBot", None).kind, "advisory")

    def test_default(self):
        self.assertEqual(route("How do I clean the filter?", "en").kind, "default")


class BM25Tests(unittest.TestCase):
    DOCS = [
        ("a", "FlashBot payload 20 kg maximum"),
        ("b", "BellaBot speed 1.2 m/s"),
        ("c", "KettyBot battery 25 Ah"),
    ]

    def setUp(self):
        self.index = BM25(self.DOCS)

    def test_ranking(self):
        hits = self.index.search("payload kg", k=3)
        self.assertEqual(hits[0].doc_id, "a")

    def test_allowed_filter(self):
        hits = self.index.search("Ah", k=3, allowed={"c"})
        self.assertEqual([h.doc_id for h in hits], ["c"])

    def test_no_match(self):
        self.assertEqual(self.index.search("flux capacitor", k=3), [])

    def test_prefix_fallback(self):
        # "consuma" is not in the corpus, "consumption" is.
        index = BM25([("x", "power consumption 5 W"), ("y", "weight 3 kg")])
        hits = index.search("consuma", k=2)
        self.assertEqual([h.doc_id for h in hits], ["x"])


class ParamIndexTests(unittest.TestCase):
    RECORDS = [
        {"parameter": "Load capacity", "value": "20 kg", "unit": "kg", "value_number": 20.0,
         "language": "en", "product": "FlashBot", "chunk_id": "c1", "page": 5, "title": "FlashBot User Manual"},
        {"parameter": "Ladekapazität", "value": "20 kg", "unit": "kg", "value_number": 20.0,
         "language": "de", "product": "FlashBot", "chunk_id": "c2", "page": 40, "title": "FlashBot Handbuch"},
        {"parameter": "Battery capacity", "value": "25 Ah", "unit": "Ah", "value_number": 25.0,
         "language": "en", "product": "KettyBot Pro", "chunk_id": "c3", "page": 3, "title": "KettyBot Pro"},
    ]

    def setUp(self):
        self.index = ParamIndex(self.RECORDS)

    def test_finds_parameter(self):
        hits = self.index.search("load capacity", k=2)
        self.assertEqual(hits[0].parameter, "Load capacity")
        self.assertIn("20 kg", hits[0].citation)

    def test_language_filter_is_strict(self):
        hits = self.index.search("load capacity", k=5, language="it")
        self.assertEqual(hits, [])

    def test_product_filter(self):
        hits = self.index.search("capacity", k=5, products=["KettyBot Pro"])
        self.assertEqual([h.parameter for h in hits], ["Battery capacity"])

    def test_lexicon_synonym(self):
        hits = self.index.search("zuladung", k=2, extra_tokens=expand_tokens(["zuladung"]))
        self.assertTrue(hits)
        self.assertIn(hits[0].value, {"20 kg"})


@unittest.skipUnless((DATA / "manuals").exists(), "dataset not present")
class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ingest.chunk import chunk_manual
        from ingest.clean import load_manual
        from ingest.params import build_param_index
        from ingest.run import discover_manuals

        chunks = [
            chunk.to_json()
            for path in discover_manuals(DATA)
            for chunk in chunk_manual(load_manual(path))
        ]
        cls.engine = SearchEngine(chunks, ParamIndex(build_param_index(chunks)))

    def test_same_language_first(self):
        result = self.engine.search("Wieviel Zuladung hat der FlashBot?", k=5)
        self.assertEqual(result.language, "de")
        self.assertFalse(result.cross_lingual)
        self.assertEqual(result.hits[0].language, "de")
        self.assertIn("20 kg", result.hits[0].text)

    def test_cross_lingual_fallback_for_italian_less_manual(self):
        result = self.engine.search("Qual è la velocità massima del FlashBot?", k=5)
        self.assertEqual(result.language, "it")
        self.assertTrue(result.cross_lingual)
        self.assertTrue(all(hit.language != "it" for hit in result.hits))
        self.assertTrue(any("FlashBot" in note for note in result.notes))

    def test_hard_language_filter_would_fail(self):
        result = self.engine.search(
            "Qual è la velocità massima del FlashBot?", k=5, cross_lingual_fallback=False
        )
        self.assertEqual(result.hits, [])

    def test_no_cross_language_duplicates(self):
        result = self.engine.search("What is the maximum speed of the BellaBot?", k=8)
        groups: dict[str, str] = {}
        for hit in result.hits:
            group = hit.metadata["translation_group"]
            self.assertNotEqual(groups.get(group), hit.language, "duplicate passage in two languages")
            groups[group] = hit.language

    def test_advisory_spans_products(self):
        result = self.engine.search("Which robot is best for delivering food in a restaurant?", k=6)
        self.assertEqual(result.route, "advisory")
        self.assertGreaterEqual(len({hit.product for hit in result.hits}), 2)

    def test_parameter_lookup_has_citation(self):
        result = self.engine.search("Wie hoch ist die Akkukapazität des KettyBot Pro?", k=3)
        self.assertTrue(result.params)
        param = result.params[0]
        self.assertIn("Ah", param.value)
        self.assertIn("p. ", param.citation)
        self.assertEqual(param.record["product"], "KettyBot Pro")

    def test_product_filter(self):
        result = self.engine.search("filter cleaning", k=5, product="PUDU SH1")
        self.assertTrue(result.hits)
        self.assertTrue(all(hit.product == "PUDU SH1" for hit in result.hits))

    def test_confidence_drops_for_unknown_questions(self):
        result = self.engine.search("How do I reset the quantum flux capacitor?", k=5)
        self.assertIn(result.confidence, ("low", "none"))

    def test_citation_format(self):
        result = self.engine.search("What is the payload of the FlashBot?", k=1)
        citation = result.hits[0].citation
        self.assertIn("FlashBot User Manual", citation)
        self.assertIn("p. ", citation)
        self.assertIn("EN", citation)

    def test_json_shape(self):
        payload = self.engine.search("What is the payload of the FlashBot?", k=2).to_dict()
        for key in ("query", "language", "route", "policy", "confidence", "hits", "params"):
            self.assertIn(key, payload)
        self.assertIn("citation", payload["hits"][0])

    def test_language_policy_beats_no_policy(self):
        questions = [
            {"id": "T1", "question": "Wieviel Zuladung hat der FlashBot?", "language": "de",
             "expect": {"keywords": ["20 kg"], "expect_param": True}},
            {"id": "T2", "question": "Quanto pesa la stazione di ricarica CC1?", "language": "it",
             "expect": {"keywords": ["14.3 kg"]}},
        ]
        policy = evaluate(self.engine, questions, k=8, config="policy")
        naive = evaluate(self.engine, questions, k=8, config="no_policy")
        self.assertEqual(policy.recall, 1.0)
        self.assertGreaterEqual(policy.same_language_share, naive.same_language_share)
        self.assertLessEqual(policy.duplicate_share, naive.duplicate_share)
        self.assertIn("hard_filter", CONFIGS)


if __name__ == "__main__":
    unittest.main()
