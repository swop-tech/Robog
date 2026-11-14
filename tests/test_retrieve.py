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
from retrieve.profiles import ProfileIndex
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
            "Show me a picture of the BellaBot": "en",
            "Zeig mir ein Bild vom FlashBot": "de",
            "Mostrami una foto del FlashBot": "it",
            "Compare BellaBot vs FlashBot": "en",
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

    def test_figure_multilingual(self):
        self.assertEqual(route("Show me a picture of the BellaBot", "en").kind, "figure")
        self.assertEqual(route("What does the KettyBot Pro look like?", "en").kind, "figure")
        self.assertEqual(route("Which figure shows the emergency stop switch?", "en").kind, "figure")
        self.assertEqual(route("Zeig mir ein Bild vom FlashBot", "de").kind, "figure")
        self.assertEqual(route("Wie sieht der PUDU T300 aus?", "de").kind, "figure")
        self.assertEqual(route("In welcher Abbildung ist der Notausschalter?", "de").kind, "figure")
        self.assertEqual(route("Mostrami una foto della stazione di ricarica CC1", "it").kind, "figure")
        self.assertEqual(route("Com'è fatto il PuduBot 2?", "it").kind, "figure")
        self.assertEqual(route("Quale figura mostra il pulsante?", "it").kind, "figure")

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


class ProfileIndexTests(unittest.TestCase):
    CARDS = [
        {
            "id": "profile-bellabot-en",
            "product": "BellaBot",
            "doc_type": "robot",
            "language": "en",
            "category": "delivery",
            "title": "BellaBot User Manual",
            "dimensions": "565x537x1290 mm",
            "weight": "55 kg",
            "payload": "10 kg/layer (Four)",
            "trays_or_compartments": "Four",
            "tank_or_dustbin": None,
            "battery_runtime": "25.6 Ah / 12-24 h",
            "charging_time": "4.5 h",
            "speed": "0.2-1.2 m/s",
            "min_passage_width": "70 cm",
            "navigation": "Laser and visual SLAM",
            "operating_modes": ["Delivery Mode", "Birthday Mode"],
            "special_features": ["voice_interaction", "tray_detection"],
            "environments": ["restaurant_dining"],
            "key_use_case": "BellaBot is an indoor delivery robot for restaurants.",
            "card_text": "BellaBot [delivery] | payload: 10 kg/layer | min passage: 70 cm",
            "image": "images/en-p003R-fig01.png",
            "overview_chunk_id": "ov-bella",
            "spec_chunk_id": "sp-bella",
            "chunk_ids": ["ov-bella", "sp-bella"],
            "pages": [2, 3],
            "source_pdf": "BellaBot.pdf",
        },
        {
            "id": "profile-bellabot-it",
            "product": "BellaBot",
            "doc_type": "robot",
            "language": "it",
            "category": "delivery",
            "title": "BellaBot User Manual",
            "dimensions": "565x537x1290 mm",
            "weight": "55 kg",
            "payload": "10 kg/strato",
            "trays_or_compartments": "4",
            "tank_or_dustbin": None,
            "battery_runtime": "25,6 Ah / 12-24 h",
            "charging_time": "4,5 h",
            "speed": "0,2-1,2 m/s",
            "min_passage_width": "70 cm",
            "navigation": "SLAM laser e visivo",
            "operating_modes": ["Modalità di consegna"],
            "special_features": ["voice_interaction"],
            "environments": ["restaurant_dining"],
            "key_use_case": "BellaBot è un robot per consegne al chiuso nei ristoranti.",
            "card_text": "BellaBot [delivery] | payload: 10 kg/strato",
            "image": "images/it-p026L-fig01.png",
            "overview_chunk_id": "ov-bella-it",
            "spec_chunk_id": "sp-bella-it",
            "chunk_ids": ["ov-bella-it", "sp-bella-it"],
            "pages": [47, 48],
            "source_pdf": "BellaBot.pdf",
        },
        {
            "id": "profile-cc1-en",
            "product": "PUDU CC1",
            "doc_type": "robot",
            "language": "en",
            "category": "cleaning",
            "title": "PUDU CC1 User Manual",
            "dimensions": "629x552x695 mm",
            "weight": "75 kg",
            "payload": None,
            "trays_or_compartments": None,
            "tank_or_dustbin": "Solution tank: 15 L; Wastewater tank: 15 L",
            "battery_runtime": "50 Ah / 5 h",
            "charging_time": "3 h",
            "speed": "0.2-1.2 m/s",
            "min_passage_width": "70 cm",
            "navigation": "Visual + Laser SLAM",
            "operating_modes": ["Scrubbing", "Sweeping"],
            "special_features": ["floor_scrubbing_sweeping", "auto_water_refill_drain"],
            "environments": ["floor_cleaning", "retail_supermarket"],
            "key_use_case": "PUDU CC1 is a versatile cleaning robot for sweeping and scrubbing.",
            "card_text": "PUDU CC1 [cleaning] | tanks: 15 L",
            "image": "images/en-cc1.png",
            "overview_chunk_id": "ov-cc1",
            "spec_chunk_id": "sp-cc1",
            "chunk_ids": ["ov-cc1", "sp-cc1"],
            "pages": [2, 3],
            "source_pdf": "CC1.pdf",
        },
        {
            "id": "profile-t300-en",
            "product": "PUDU T300",
            "doc_type": "robot",
            "language": "en",
            "category": "industrial_delivery",
            "title": "PUDU T300 User Manual",
            "dimensions": "835x500x1350 mm",
            "weight": "65 kg",
            "payload": "300 kg",
            "trays_or_compartments": None,
            "tank_or_dustbin": None,
            "battery_runtime": "30 Ah / 12 h",
            "charging_time": "2 h",
            "speed": "0.2-1.2 m/s",
            "min_passage_width": "60 cm",
            "navigation": "VSLAM + Laser SLAM",
            "operating_modes": ["Delivery mode"],
            "special_features": ["heavy_industrial_payload"],
            "environments": ["industrial_warehouse"],
            "key_use_case": "PUDU T300 is an industrial material transfer robot carrying 300 kg.",
            "card_text": "PUDU T300 [industrial_delivery] | payload: 300 kg",
            "image": "images/en-t300.png",
            "overview_chunk_id": "ov-t300",
            "spec_chunk_id": "sp-t300",
            "chunk_ids": ["ov-t300", "sp-t300"],
            "pages": [2, 3],
            "source_pdf": "T300.pdf",
        },
        {
            "id": "profile-flashbot-en",
            "product": "FlashBot",
            "doc_type": "robot",
            "language": "en",
            "category": "delivery",
            "title": "FlashBot User Manual V1.0",
            "dimensions": "53.8x53.4x105.2 cm",
            "weight": "57 kg",
            "payload": "20 kg (10 kg per tray)",
            "trays_or_compartments": "2 ~ 4 (Adjustable)",
            "tank_or_dustbin": None,
            "battery_runtime": "10 Ah / 9 h",
            "charging_time": "4.5 h",
            "speed": "1.2 m/s",
            "min_passage_width": "70 cm",
            "navigation": "Laser SLAM",
            "operating_modes": ["Delivery mode", "Escorting mode"],
            "special_features": ["elevator_control", "enclosed_compartments"],
            "environments": ["hotel_hospitality", "multi_floor_elevator"],
            "key_use_case": "FlashBot building delivery robot with optional elevator control.",
            "card_text": "FlashBot [delivery] | elevator_control enclosed_compartments",
            "image": "images/en-flashbot.png",
            "overview_chunk_id": "ov-flash",
            "spec_chunk_id": "sp-flash",
            "chunk_ids": ["ov-flash", "sp-flash"],
            "pages": [3, 5],
            "source_pdf": "FlashBot.pdf",
        },
    ]

    def setUp(self):
        self.index = ProfileIndex(self.CARDS)

    def test_restaurant_query_ranks_delivery_first(self):
        hits = self.index.search("Which robot fits my restaurant?", language="en")
        self.assertEqual(hits[0].product, "BellaBot")
        self.assertEqual(hits[0].category, "delivery")

    def test_cleaning_query_ranks_cleaning_first(self):
        hits = self.index.search("Which robot is best for floor scrubbing in a supermarket?", language="en")
        self.assertEqual(hits[0].product, "PUDU CC1")
        self.assertEqual(hits[0].category, "cleaning")

    def test_numeric_payload_constraint_boosts_t300(self):
        hits = self.index.search("Which robot can carry 250 kg in a warehouse?", language="en")
        self.assertEqual(hits[0].product, "PUDU T300")

    def test_cross_lingual_capability_surfaces_flashbot_in_italian(self):
        hits = self.index.search(
            "Quale robot può prendere l'ascensore in hotel?",
            language="it",
            extra_tokens=expand_tokens(["ascensore", "hotel"]),
        )
        self.assertEqual(hits[0].product, "FlashBot")
        self.assertTrue(hits[0].cross_lingual)
        # Hard filter suppresses cross-lingual cards
        strict = self.index.search(
            "Quale robot può prendere l'ascensore in hotel?",
            language="it",
            cross_lingual_fallback=False,
            extra_tokens=expand_tokens(["ascensore", "hotel"]),
        )
        self.assertNotIn("FlashBot", [h.product for h in strict])

    def test_catalog_returns_all_robots_with_fallback(self):
        cat_it = self.index.catalog("it", cross_lingual_fallback=True)
        self.assertEqual(len(cat_it), 4)
        by_prod = {h.product: h for h in cat_it}
        self.assertEqual(by_prod["BellaBot"].language, "it")
        self.assertFalse(by_prod["BellaBot"].cross_lingual)
        self.assertEqual(by_prod["FlashBot"].language, "en")
        self.assertTrue(by_prod["FlashBot"].cross_lingual)


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
        for key in ("query", "language", "route", "policy", "confidence", "hits", "params", "profiles"):
            self.assertIn(key, payload)
        self.assertIn("citation", payload["hits"][0])
        self.assertIn("labels", payload["hits"][0])

    def test_figure_overview_en_de_it(self):
        en = self.engine.search("Show me a picture of the BellaBot", k=3)
        self.assertEqual(en.route, "figure")
        self.assertEqual(en.hits[0].chunk_type, "figure")
        self.assertEqual(en.hits[0].product, "BellaBot")
        self.assertEqual(en.hits[0].language, "en")
        self.assertTrue((en.hits[0].image or "").startswith("images/en-"))
        self.assertIn("2.3", en.hits[0].section)

        de = self.engine.search("Zeig mir ein Bild vom FlashBot", k=3)
        self.assertEqual(de.route, "figure")
        self.assertEqual(de.hits[0].chunk_type, "figure")
        self.assertEqual(de.hits[0].product, "FlashBot")
        self.assertEqual(de.hits[0].language, "de")
        self.assertTrue((de.hits[0].image or "").startswith("images/de-"))
        self.assertIn("2.2", de.hits[0].section)

        it = self.engine.search("Mostrami una foto della stazione di ricarica CC1", k=3)
        self.assertEqual(it.route, "figure")
        self.assertEqual(it.hits[0].chunk_type, "figure")
        self.assertEqual(it.hits[0].product, "CC1 Charging Station")
        self.assertEqual(it.hits[0].language, "it")
        self.assertTrue((it.hits[0].image or "").startswith("images/it-"))

    def test_figure_specific_component_keeps_topic(self):
        res = self.engine.search("Which figure shows the emergency stop switch on the BellaBot?", k=3)
        self.assertEqual(res.route, "figure")
        self.assertEqual(res.hits[0].chunk_type, "figure")
        self.assertIn("emergency stop switch", fold(res.hits[0].text))
        self.assertTrue(res.hits[0].labels)

    def test_figure_cross_lingual_fallback_prefers_en_for_flashbot_it(self):
        res = self.engine.search("Mostrami una foto del FlashBot", k=3)
        self.assertEqual(res.language, "it")
        self.assertEqual(res.route, "figure")
        self.assertTrue(res.cross_lingual)
        self.assertEqual(res.hits[0].product, "FlashBot")
        self.assertEqual(res.hits[0].language, "en")
        self.assertTrue((res.hits[0].image or "").startswith("images/en-"))

        strict = self.engine.search("Mostrami una foto del FlashBot", k=3, cross_lingual_fallback=False)
        self.assertEqual(strict.hits, [])

    def test_advisory_restaurant_returns_profiles_and_excludes_accessories(self):
        res = self.engine.search("Which robot fits my restaurant?", k=5)
        self.assertEqual(res.route, "advisory")
        self.assertGreaterEqual(len(res.profiles), 5)
        self.assertTrue(all(p.category == "delivery" for p in res.profiles[:5]))
        prof_names = {p.product for p in res.profiles}
        self.assertIn("BellaBot", prof_names)
        self.assertIn("PuduBot 2", prof_names)
        self.assertTrue(all(h.metadata.get("doc_type") == "robot" for h in res.hits))

    def test_advisory_multi_product_comparison_preserves_both_products(self):
        res = self.engine.search("BellaBot vs KettyBot Pro for a narrow restaurant", k=4)
        self.assertEqual(res.route, "advisory")
        self.assertIn("BellaBot", res.products)
        self.assertIn("KettyBot Pro", res.products)
        self.assertEqual([p.product for p in res.profiles], ["KettyBot Pro", "BellaBot"])

    def test_advisory_numeric_300kg_ranks_t300_first(self):
        res = self.engine.search("Which robot can carry 300 kg in a factory?", k=4)
        self.assertEqual(res.route, "advisory")
        self.assertEqual(res.profiles[0].product, "PUDU T300")
        self.assertEqual(res.hits[0].product, "PUDU T300")

    def test_advisory_cross_lingual_elevator_it_surfaces_flashbot(self):
        res = self.engine.search("Quale robot può usare l'ascensore per consegnare in hotel?", k=5)
        self.assertEqual(res.language, "it")
        self.assertEqual(res.route, "advisory")
        self.assertTrue(res.cross_lingual)
        self.assertEqual(res.profiles[0].product, "FlashBot")
        self.assertTrue(res.profiles[0].cross_lingual)

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
