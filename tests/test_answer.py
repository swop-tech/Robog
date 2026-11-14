"""Tests for the answer composer + evidence validator (standard library only).

Run from the repository root:

    python -m unittest tests.test_answer -v

Nothing here needs an API key, a network connection or an embedding model: the
LLM is either a deterministic stub or a fake client, and the corpus tests read
the real manuals in ``data/`` (skipped when the dataset is absent).

The tests are organised around the three claims the answering phase makes:

* the answer is written in the question's language, from sources that may not
  be (Bonus 02);
* every claim is cited, every number is in the evidence, every quote is verbatim
  (gap G5);
* all of it works with the LLM switched off (the offline formatter).
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from answer.composer import AnswerComposer
from answer.evidence import Evidence, EvidenceRef, iter_number_units
from answer.formatter import format_answer
from answer.llm import GeminiGenerator, OfflineGenerator, build_generator
from answer.prompt import build_prompt, evidence_block
from answer.strings import text
from answer.validator import validate_answer
from retrieve.engine import Hit, SearchEngine

DATA = Path(__file__).parent.parent / "data"


# ---------------------------------------------------------------------------
# fixtures: a tiny hand-written evidence set (no corpus needed)
# ---------------------------------------------------------------------------
def make_evidence(**overrides) -> Evidence:
    refs = [
        EvidenceRef(
            n=1,
            kind="param",
            text="Load capacity: 20 kg (10 kg per tray)",
            citation="FlashBot User Manual V1.0, §2.3, p. 40, DE",
            language="de",
            product="FlashBot",
        ),
        EvidenceRef(
            n=2,
            kind="chunk",
            text=(
                "The robot is switched on with the power switch. "
                "Hold the power switch for 0.5 seconds to switch the robot on."
            ),
            citation="FlashBot User Manual V1.0, §Power on:, p. 41, EN",
            language="en",
            product="FlashBot",
            chunk_id="c2",
        ),
        EvidenceRef(
            n=3,
            kind="profile",
            text="payload: 10 kg/layer (Four) | weight: 55 kg | battery: 25.6 Ah",
            citation="BellaBot User Manual, p. 31–32, EN",
            language="en",
            product="BellaBot",
            support=(
                "payload: 10 kg/layer (Four) | weight: 55 kg | battery: 25.6 Ah\n"
                "BellaBot is an indoor delivery robot with four trays."
            ),
        ),
    ]
    evidence = Evidence(
        refs=refs,
        language="de",
        products=["FlashBot"],
        languages=["de", "en"],
    )
    for key, value in overrides.items():
        setattr(evidence, key, value)
    return evidence


class StubGenerator:
    """A generator that always answers with the text it was given."""

    model = None

    def __init__(self, text_value: str, name: str = "stub"):
        self.text_value = text_value
        self.name = name
        self.calls: list[str] = []

    def draft(self, prompt, evidence, *, language, route="default", question=""):
        from answer.formatter import Draft

        self.calls.append(prompt)
        return Draft(self.text_value, provider=self.name, language=language)


class BrokenGenerator:
    name = "broken"
    model = None

    def draft(self, *args, **kwargs):
        raise RuntimeError("provider exploded")


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------
class EvidenceTests(unittest.TestCase):
    def test_refs_are_numbered_and_kinds_ordered(self):
        evidence = Evidence.from_result(_result_with_params_and_hits())
        kinds = [ref.kind for ref in evidence.refs]
        self.assertEqual(kinds[0], "param")
        self.assertEqual([ref.n for ref in evidence.refs], list(range(1, len(evidence.refs) + 1)))
        self.assertIn("chunk", kinds)

    def test_parameter_ref_keeps_value_and_citation(self):
        evidence = Evidence.from_result(_result_with_params_and_hits())
        param = evidence.refs[0]
        self.assertIn("20 kg", param.text)
        self.assertIn("p. 40", param.citation)
        self.assertEqual(param.product, "FlashBot")

    def test_numbers_cover_chunks_and_profile_cards(self):
        evidence = make_evidence()
        self.assertIn(("20", "kg"), evidence.numbers)
        self.assertIn(("0.5", "seconds"), evidence.numbers)    # 0.5 seconds
        self.assertIn(("25.6", "ah"), evidence.numbers)        # profile card
        self.assertIn(("55", "kg"), evidence.numbers)

    def test_profile_support_holds_specs_and_use_case(self):
        evidence = make_evidence()
        profile = evidence.ref(3)
        self.assertIn("four trays", profile.support)
        self.assertIn("25.6 Ah", profile.support)

    def test_decimal_comma_equals_decimal_point(self):
        evidence = make_evidence()
        numbers = evidence.numbers
        self.assertIn(("25.6", "ah"), numbers)
        # evidence written with a German decimal comma normalises to the same key
        from answer.evidence import number_unit_pairs

        self.assertIn(("25.6", "ah"), number_unit_pairs("Akkukapazität: 25,6 Ah"))

    def test_missing_ref_returns_none(self):
        evidence = make_evidence()
        self.assertIsNone(evidence.ref(99))
        self.assertEqual(evidence.texts_for([99]), [])

    def test_iter_number_units_offsets(self):
        found = iter_number_units("20 kg (10 kg pro Tablett)")
        self.assertEqual(found[0][0:2], ("20", "kg"))
        start, end = found[0][2], found[0][3]
        self.assertEqual("20 kg (10 kg pro Tablett)"[start:end], "20 kg")

    def test_cross_lingual_languages(self):
        evidence = make_evidence()          # question language 'de'
        self.assertEqual(evidence.cross_lingual_languages(), ["en"])


def _result_with_params_and_hits():
    from retrieve.params import ParamHit

    param = ParamHit(
        {
            "parameter": "Ladekapazität",
            "value": "20 kg (10 kg pro Tablett)",
            "product": "FlashBot",
            "language": "de",
            "title": "FlashBot User Manual V1.0",
            "section_number": "2.3",
            "page": 40,
            "chunk_id": "c1",
        },
        1.0,
    )
    hit = Hit(
        chunk_id="c2",
        text="Die Abbildung zeigt den Notausschalter.",
        metadata={
            "product": "FlashBot",
            "language": "de",
            "chunk_type": "text",
            "title": "FlashBot User Manual V1.0",
            "section_number": "2.3",
            "page": 40,
        },
        score=0.5,
        rank=1,
    )
    return SimpleNamespace(
        params=[param], profiles=[], hits=[hit], language="de",
        products=["FlashBot"], notes=[], route="lookup", query="",
    )


# ---------------------------------------------------------------------------
# validator: citations (G5.1)
# ---------------------------------------------------------------------------
class CitationCheckTests(unittest.TestCase):
    def test_valid_citations_pass(self):
        report = validate_answer("The robot has a load capacity of 20 kg [1].", make_evidence())
        self.assertTrue(report.ok)
        self.assertTrue(report.verified)
        self.assertEqual(report.sentences_cited, 1)
        self.assertEqual(report.refs_used, [1])

    def test_citation_to_missing_ref_is_dropped(self):
        report = validate_answer("The robot has a load capacity of 20 kg [7].", make_evidence())
        self.assertNotIn("[7]", report.answer)
        self.assertTrue(any(issue.check == "citation" and issue.severity == "error"
                            for issue in report.issues))

    def test_partially_invalid_marker_keeps_valid_numbers(self):
        report = validate_answer("Load capacity is 20 kg [1, 9].", make_evidence())
        self.assertIn("[1]", report.answer)
        self.assertNotIn("9", report.answer)

    def test_uncited_claim_gets_the_matching_ref(self):
        answer = "Hold the power switch for 0.5 seconds to switch the robot on."
        report = validate_answer(answer, make_evidence())
        self.assertTrue(report.ok)
        self.assertIn("[2]", report.answer)
        self.assertTrue(any(issue.action == "repaired" for issue in report.issues))

    def test_uncited_claim_without_support_is_dropped(self):
        answer = "The robot can also make espresso for the staff."
        report = validate_answer(answer, make_evidence())
        self.assertFalse(report.ok)
        self.assertEqual(report.answer.strip(), "")
        self.assertTrue(any(issue.action == "dropped" for issue in report.issues))

    def test_headings_need_no_citation(self):
        report = validate_answer("**Passages from the manuals**", make_evidence())
        self.assertEqual(report.sentences_checked, 0)
        self.assertTrue(report.ok)
        self.assertIn("**Passages from the manuals**", report.answer)

    def test_multi_sentence_line_each_claim_checked(self):
        answer = "The robot has 20 kg payload [1]. It also weighs 55 kg."
        report = validate_answer(answer, make_evidence())
        self.assertTrue(report.ok)
        self.assertEqual(report.sentences_checked, 2)

    def test_marker_separated_by_the_splitter_is_kept(self):
        # "… functions. [1]" must not be read as an uncited sentence
        report = validate_answer("See the overview. [2]", make_evidence())
        self.assertTrue(report.ok)
        self.assertEqual(report.sentences_cited, report.sentences_checked)


# ---------------------------------------------------------------------------
# validator: numbers (G5.2)
# ---------------------------------------------------------------------------
class NumericCheckTests(unittest.TestCase):
    def test_verified_number_passes(self):
        report = validate_answer("Load capacity is 20 kg [1].", make_evidence())
        self.assertEqual(report.numbers_checked, 1)
        self.assertEqual(report.numbers_verified, 1)

    def test_invented_number_is_removed(self):
        report = validate_answer("The robot carries up to 45 kg [1].", make_evidence())
        self.assertNotIn("45", report.answer)
        self.assertIn("carries up to", report.answer)      # the claim itself survives
        self.assertEqual(report.numbers_verified, 0)
        self.assertTrue(any(issue.check == "numeric" and issue.severity == "error"
                            for issue in report.issues))

    def test_right_value_wrong_unit_is_flagged(self):
        # 25.6 Ah exists in the evidence; 25.6 V does not.
        report = validate_answer("The battery is 25.6 V [3].", make_evidence())
        self.assertNotIn("25.6", report.answer)
        self.assertTrue(any("unit" in issue.message for issue in report.issues))

    def test_citation_numbers_are_not_claims(self):
        report = validate_answer("See the manual [1, 2, 3].", make_evidence())
        self.assertEqual(report.numbers_checked, 0)
        self.assertTrue(report.ok)

    def test_reference_locators_are_not_claims(self):
        report = validate_answer("See the manual, §2.3, p. 40 [1].", make_evidence())
        self.assertEqual(report.numbers_checked, 0)

    def test_decimal_comma_in_the_answer_matches_point_in_the_evidence(self):
        report = validate_answer("Die Batterie hat 25,6 Ah [3].", make_evidence())
        self.assertEqual(report.numbers_verified, 1)

    def test_unitless_number_is_checked_against_values(self):
        # "10" appears in "10 kg/layer" -- the bare value is enough without a unit.
        report = validate_answer("The robot has 10 kg on each layer [3].", make_evidence())
        self.assertEqual(report.numbers_verified, 1)
        bare = validate_answer("The robot weighs 55 kg [3].", make_evidence())
        self.assertEqual(bare.numbers_verified, 1)

    def test_repair_false_only_reports(self):
        answer = "The robot carries up to 45 kg [1]."
        report = validate_answer(answer, make_evidence(), repair=False)
        self.assertFalse(report.ok)
        self.assertEqual(report.answer, answer)


# ---------------------------------------------------------------------------
# validator: quotes (G5.3)
# ---------------------------------------------------------------------------
class QuoteCheckTests(unittest.TestCase):
    def test_verbatim_quote_passes(self):
        report = validate_answer(
            'The manual says "Hold the power switch for 0.5 seconds" [2].', make_evidence()
        )
        self.assertEqual(report.quotes_checked, 1)
        self.assertEqual(report.quotes_verified, 1)
        self.assertIn("Hold the power switch for 0.5 seconds", report.answer)

    def test_quote_is_case_and_whitespace_insensitive(self):
        report = validate_answer(
            'It says "hold  the POWER switch for 0.5 seconds" [2].', make_evidence()
        )
        self.assertEqual(report.quotes_verified, 1)

    def test_german_and_italian_quote_styles(self):
        report = validate_answer(
            "Der Text lautet „Hold the power switch for 0.5 seconds“ [2].", make_evidence()
        )
        self.assertEqual(report.quotes_verified, 1)
        report = validate_answer(
            "Il manuale dice «Hold the power switch for 0.5 seconds» [2].", make_evidence()
        )
        self.assertEqual(report.quotes_verified, 1)

    def test_fabricated_quote_is_unquoted(self):
        report = validate_answer(
            'The manual says "the robot must be blessed with holy water" [2].', make_evidence()
        )
        self.assertEqual(report.quotes_verified, 0)
        self.assertNotIn('"the robot must be blessed', report.answer)
        self.assertTrue(any(issue.check == "quote" and issue.severity == "error"
                            for issue in report.issues))

    def test_quote_from_another_ref_fails(self):
        # The phrase is in [2] but the sentence cites [1].
        report = validate_answer(
            'The manual says "Hold the power switch for 0.5 seconds" [1].', make_evidence()
        )
        self.assertEqual(report.quotes_verified, 0)

    def test_excerpt_with_truncation_marker_verifies(self):
        report = validate_answer(
            '“Hold the power switch for 0.5 seconds to switch the robot on …” [2]',
            make_evidence(),
        )
        self.assertEqual(report.quotes_verified, 1)

    def test_quoted_number_is_a_numeric_claim_not_a_quote(self):
        report = validate_answer('The value is "20 kg" [1].', make_evidence())
        self.assertEqual(report.quotes_checked, 0)


# ---------------------------------------------------------------------------
# validator: overall behaviour
# ---------------------------------------------------------------------------
class ValidatorBehaviourTests(unittest.TestCase):
    def test_no_evidence_answer_is_returned_untouched(self):
        empty = Evidence(refs=[], language="it")
        report = validate_answer("Non ho trovato nulla.", empty)
        self.assertEqual(report.answer, "Non ho trovato nulla.")
        self.assertTrue(report.ok)

    def test_no_evidence_report_reports_checks_not_run(self):
        # 0/0 claims passes every check vacuously: `verified` stays True for
        # compatibility, but `checks_run` tells callers not to render a green
        # badge for an answer that was never grounded in anything.
        empty = Evidence(refs=[], language="en")
        report = validate_answer("I could not find this in the manuals.", empty)
        self.assertTrue(report.ok)
        self.assertFalse(report.checks_run)
        self.assertIn("checks_run", report.to_dict())
        self.assertFalse(report.to_dict()["checks_run"])
        self.assertIn("Nothing to verify", report.summary("en"))
        self.assertIn("Nichts zu prüfen", report.summary("de"))

    def test_checks_run_is_true_when_something_was_checked(self):
        report = validate_answer("Load capacity is 20 kg [1].", make_evidence())
        self.assertTrue(report.checks_run)

    def test_report_is_json_ready(self):
        import json

        report = validate_answer("Load capacity is 20 kg [1].", make_evidence())
        payload = json.loads(json.dumps(report.to_dict(), ensure_ascii=False))
        for key in (
            "ok", "verified", "sentences_checked", "sentences_cited", "numbers_checked",
            "numbers_verified", "quotes_checked", "quotes_verified", "refs_used", "issues",
        ):
            self.assertIn(key, payload)

    def test_validation_is_idempotent(self):
        answer = "Load capacity is 20 kg [1]. The robot has 45 kg extra [1]."
        first = validate_answer(answer, make_evidence())
        second = validate_answer(first.answer, make_evidence())
        self.assertEqual(first.answer, second.answer)
        self.assertTrue(second.ok)

    def test_summary_is_localized(self):
        report = validate_answer("Load capacity is 20 kg [1].", make_evidence())
        self.assertIn("1/1", report.summary("de"))
        self.assertIn("Zahlen", report.summary("de"))
        self.assertIn("numeri", report.summary("it"))


# ---------------------------------------------------------------------------
# offline formatter
# ---------------------------------------------------------------------------
class FormatterTests(unittest.TestCase):
    def test_answers_in_each_language(self):
        evidence = make_evidence()
        for language, marker in (("en", "According to the manual"),
                                 ("de", "Laut Handbuch"),
                                 ("it", "Secondo il manuale")):
            draft = format_answer(evidence, language=language)
            self.assertIn(marker, draft.text, language)
            self.assertEqual(draft.provider, "offline")

    def test_parameter_value_is_quoted_not_reworded(self):
        # The value is copied from the row the retriever returned -- in this
        # fixture the EN row, so the EN wording appears verbatim in a DE answer.
        draft = format_answer(make_evidence(), language="de")
        self.assertIn("20 kg (10 kg per tray)", draft.text)
        self.assertIn("Laut Handbuch", draft.text)

    def test_cross_lingual_note_names_the_source_languages(self):
        draft = format_answer(make_evidence(), language="de")
        self.assertIn("Englisch", draft.text)       # evidence in EN, question in DE
        draft = format_answer(make_evidence(), language="it")
        self.assertIn("inglese", draft.text)

    def test_no_note_when_evidence_matches_the_question_language(self):
        evidence = make_evidence(language="de", refs=[make_evidence().ref(1)])
        draft = format_answer(evidence, language="de")
        self.assertNotIn("Hinweis", draft.text)

    def test_empty_evidence_says_so_instead_of_guessing(self):
        for language, marker in (("en", "could not find"), ("de", "nichts gefunden"),
                                 ("it", "Non ho trovato")):
            draft = format_answer(Evidence(refs=[], language=language), language=language)
            self.assertIn(marker, draft.text)

    def test_every_reference_in_the_draft_exists_in_the_evidence(self):
        draft = format_answer(make_evidence(), language="en")
        known = {ref.n for ref in make_evidence().refs}
        import re

        for n in re.findall(r"\[(\d+)\]", draft.text):
            self.assertIn(int(n), known)

    def test_formatter_output_passes_validation(self):
        evidence = make_evidence()
        draft = format_answer(evidence, language="de")
        report = validate_answer(draft.text, evidence, language="de")
        self.assertTrue(report.ok, report.to_dict())
        self.assertGreater(report.sentences_checked, 0)


# ---------------------------------------------------------------------------
# generator wiring (no network)
# ---------------------------------------------------------------------------
class GeneratorTests(unittest.TestCase):
    def test_build_offline_never_touches_the_network(self):
        generator = build_generator("offline")
        self.assertIsInstance(generator, OfflineGenerator)
        draft = generator.draft("prompt", make_evidence(), language="en", question="payload?")
        self.assertIn("20 kg", draft.text)

    def test_gemini_generator_without_key_is_unavailable_offline(self):
        generator = GeminiGenerator(api_key="", client=_FailingClient())
        self.assertIsNone(generator.draft("prompt", make_evidence(), language="en"))

    def test_gemini_generator_reads_response_text(self):
        client = _FakeClient("Load capacity is 20 kg [1].")
        generator = GeminiGenerator(client=client)
        draft = generator.draft("prompt", make_evidence(), language="en")
        self.assertEqual(draft.provider, "gemini")
        self.assertEqual(draft.model, "gemini-3.8-flash")
        self.assertIn("20 kg", draft.text)
        self.assertEqual(client.calls[0]["model"], "gemini-3.8-flash")

    def test_gemini_generator_falls_back_on_error(self):
        generator = GeminiGenerator(client=_FailingClient(), attempts=2, sleep_fn=lambda _s: None)
        self.assertIsNone(generator.draft("prompt", make_evidence(), language="en"))

    def test_model_can_be_overridden(self):
        client = _FakeClient("ok")
        generator = GeminiGenerator(model="gemini-2.5-pro", client=client)
        generator.draft("p", make_evidence(), language="en")
        self.assertEqual(client.calls[0]["model"], "gemini-2.5-pro")

    def test_response_text_tolerates_candidate_shapes(self):
        from answer.llm import _response_text

        parts = [SimpleNamespace(text="Hello "), SimpleNamespace(text="world")]
        candidate = SimpleNamespace(content=SimpleNamespace(parts=parts))
        self.assertEqual(_response_text(SimpleNamespace(candidates=[candidate])), "Hello world")
        self.assertEqual(_response_text(SimpleNamespace(text="direct")), "direct")
        self.assertEqual(_response_text(SimpleNamespace()), "")


class _FakeClient:
    def __init__(self, text_value: str):
        self.text_value = text_value
        self.calls: list[dict] = []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, *, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents})
        return SimpleNamespace(text=self.text_value)


class _FailingClient:
    def __init__(self):
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **_kwargs):
        raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")


# ---------------------------------------------------------------------------
# prompt
# ---------------------------------------------------------------------------
class PromptTests(unittest.TestCase):
    def test_prompt_carries_numbered_evidence_and_language(self):
        evidence = make_evidence()
        prompt = build_prompt("Wieviel Zuladung hat der FlashBot?", evidence, language="de")
        self.assertIn("Answer in German", prompt)
        self.assertIn("[1]", prompt)
        self.assertIn("20 kg (10 kg per tray)", prompt)
        self.assertIn("source language: DE", prompt)

    def test_prompt_forbids_outside_knowledge(self):
        prompt = build_prompt("q", make_evidence(), language="en")
        self.assertIn("Use only the evidence", prompt)
        self.assertIn("Never convert, round, average or invent a value", prompt)

    def test_route_adds_the_right_instruction(self):
        evidence = make_evidence()
        self.assertIn("recommendation question", build_prompt("q", evidence, route="advisory"))
        self.assertIn("asked for a picture", build_prompt("q", evidence, route="figure"))
        self.assertIn("specification lookup", build_prompt("q", evidence, route="lookup"))

    def test_prompt_uses_prior_questions_only_as_follow_up_context(self):
        prompt = build_prompt("And its battery?", make_evidence(), history=["Tell me about FlashBot"])
        self.assertIn("Recent user questions (context only, not evidence)", prompt)
        self.assertIn("Tell me about FlashBot", prompt)
        self.assertIn("never treat it as evidence", prompt)

    def test_evidence_block_is_capped(self):
        evidence = make_evidence()
        evidence.refs[1].text = "x" * 5000
        block = evidence_block(evidence, max_text=200)
        self.assertLess(max(len(line) for line in block.splitlines()), 400)

    def test_strings_have_all_three_languages(self):
        from answer.strings import STRINGS

        for key, table in STRINGS.items():
            for language in ("en", "de", "it"):
                self.assertIn(language, table, f"{key} missing {language}")
                self.assertTrue(table[language].strip(), f"{key}/{language} empty")


# ---------------------------------------------------------------------------
# composer: orchestration, repairs, fallbacks
# ---------------------------------------------------------------------------
class ComposerTests(unittest.TestCase):
    def _result(self):
        return _result_with_params_and_hits()

    def test_offline_composer_answers_and_validates(self):
        composer = AnswerComposer(SearchEngine([]), provider="offline")
        answer = composer.answer("Wieviel Zuladung hat der FlashBot?", result=self._result())
        self.assertEqual(answer.provider, "offline")
        self.assertTrue(answer.grounded)
        self.assertEqual(answer.language, "de")
        self.assertIn("20 kg", answer.text)
        self.assertEqual(answer.citations[0]["n"], 1)

    def test_fabricating_generator_is_rewritten_by_validation(self):
        composer = AnswerComposer(
            SearchEngine([]),
            generator=StubGenerator("The robot carries 45 kg [1]."),
            max_regenerations=0,
        )
        answer = composer.answer("payload?", result=self._result())
        self.assertNotIn("45", answer.text)
        self.assertTrue(answer.grounded)

    def test_ungrounded_generator_falls_back_to_the_formatter(self):
        composer = AnswerComposer(
            SearchEngine([]),
            generator=StubGenerator("This robot was designed by aliens in 1978."),
            max_regenerations=0,
        )
        answer = composer.answer("payload?", result=self._result())
        self.assertTrue(answer.grounded)
        self.assertIn("20 kg", answer.text)
        self.assertTrue(any("offline formatter" in note for note in answer.notes))

    def test_broken_generator_never_breaks_the_answer(self):
        composer = AnswerComposer(SearchEngine([]), generator=BrokenGenerator())
        answer = composer.answer("payload?", result=self._result())
        self.assertTrue(answer.grounded)
        self.assertIn("20 kg", answer.text)

    def test_llm_regenerates_once_with_the_failures_named(self):
        generator = StubGenerator("The robot carries 45 kg [1].", name="gemini")
        composer = AnswerComposer(SearchEngine([]), generator=generator, max_regenerations=1)
        composer.answer("payload?", result=self._result())
        self.assertEqual(len(generator.calls), 2)
        self.assertIn("failed the evidence check", generator.calls[1])

    def test_provider_metadata_and_json_shape(self):
        composer = AnswerComposer(SearchEngine([]), provider="offline")
        answer = composer.answer("payload?", result=self._result())
        payload = answer.to_dict()
        for key in (
            "question", "answer", "language", "route", "provider", "grounded", "verified",
            "citations", "figures", "validation", "language_instruction", "cross_lingual",
            "source_languages", "notes", "search", "evidence",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["provider"], "offline")
        self.assertEqual(payload["source_languages"], ["de"])   # fixture is DE-only
        self.assertFalse(payload["cross_lingual"])

    def test_empty_question_does_not_crash(self):
        composer = AnswerComposer(SearchEngine([]), provider="offline")
        answer = composer.answer("")
        self.assertTrue(answer.text)


# ---------------------------------------------------------------------------
# corpus tests (real manuals)
# ---------------------------------------------------------------------------
@unittest.skipUnless((DATA / "manuals").exists(), "dataset not present")
class CorpusAnswerTests(unittest.TestCase):
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
        cls.engine = SearchEngine(chunks, __import__("retrieve.params", fromlist=["ParamIndex"]).ParamIndex(build_param_index(chunks)))
        cls.composer = AnswerComposer(cls.engine, provider="offline")

    def test_payload_lookup_is_exact_and_cited(self):
        answer = self.composer.answer("What is the payload of the FlashBot?")
        self.assertIn("20 kg", answer.text)
        self.assertTrue(answer.grounded)
        self.assertEqual(answer.language, "en")
        self.assertEqual(answer.validation.numbers_verified, answer.validation.numbers_checked)
        self.assertTrue(any("p. 40" in c["citation"] or "p. 5" in c["citation"]
                            for c in answer.citations))

    def test_vague_best_robot_question_asks_for_the_use_case(self):
        # \"Which robot is best?\" has no scenario: listing 13 robots would look
        # like a recommendation, so the composer asks back instead.
        answer = self.composer.answer("which robot is best")
        self.assertEqual(answer.route, "advisory")
        self.assertIn("Tell me the use case", answer.text)
        self.assertEqual(answer.citations, [])
        self.assertEqual(answer.figures, [])
        self.assertFalse(answer.validation.checks_run)
        self.assertTrue(any("asked back" in note for note in answer.notes), answer.notes)

    def test_vague_best_robot_question_is_asked_back_in_german_and_italian(self):
        de = self.composer.answer("Welcher Roboter ist am besten?")
        self.assertEqual(de.language, "de")
        self.assertIn("Einsatzort", de.text)
        it = self.composer.answer("Quale robot è il migliore?")
        self.assertEqual(it.language, "it")
        self.assertIn("dove lavorerà", it.text)

    def test_advisory_question_with_a_use_case_is_still_ranked(self):
        answer = self.composer.answer("Which robot fits my restaurant?")
        self.assertEqual(answer.route, "advisory")
        self.assertNotIn("Tell me the use case", answer.text)
        self.assertIn("BellaBot", answer.text)
        self.assertTrue(answer.citations)

    def test_advisory_question_with_a_load_is_still_ranked(self):
        answer = self.composer.answer("Which robot can carry 300 kg in a factory?")
        self.assertNotIn("Tell me the use case", answer.text)
        self.assertIn("T300", answer.text)

    def test_load_question_only_names_robots_that_carry_the_load(self):
        # A sweeper that cannot carry 300 kg must not appear in the answer.
        answer = self.composer.answer("Which robot can carry 300 kg in a factory?")
        self.assertIn("T300", answer.text)
        self.assertNotIn("MT1", answer.text)
        self.assertNotIn("PuduBot 2", answer.text)
        self.assertTrue(answer.validation.ok)

    def test_impossible_load_says_no_robot_meets_it(self):
        answer = self.composer.answer("Which robot can carry 900 kg?")
        self.assertIn("None of the robots", answer.text)
        self.assertEqual(answer.citations, [])

    def test_narrow_restaurant_comparison_names_the_narrowest_passage(self):
        answer = self.composer.answer("BellaBot vs KettyBot Pro for a narrow restaurant")
        self.assertIn("Narrowest passage among these: 55 cm", answer.text)
        self.assertIn("KettyBot Pro", answer.text)
        self.assertTrue(answer.validation.ok, [i.message for i in answer.validation.issues])

    def test_under_specified_lookup_asks_for_the_robot_and_parameter(self):
        # The brief's technician persona asks "What's the value of this
        # parameter?": quoting whatever BM25 returns for the bare words
        # "value"/"parameter" would be a guess dressed as an answer.
        answer = self.composer.answer("What is the value of this parameter?")
        self.assertEqual(answer.route, "lookup")
        self.assertIn("Which robot and which parameter", answer.text)
        self.assertEqual(answer.citations, [])
        self.assertEqual(answer.figures, [])
        self.assertFalse(answer.validation.checks_run)
        self.assertTrue(any("asked back" in note for note in answer.notes), answer.notes)

    def test_under_specified_lookup_asks_back_in_the_question_language(self):
        answer = self.composer.answer("Qual è il valore di questo parametro?")
        self.assertEqual(answer.language, "it")
        self.assertIn("Quale robot e quale parametro", answer.text)

    def test_a_lookup_that_names_its_parameter_still_answers(self):
        answer = self.composer.answer("What is the value of the FlashBot load capacity?")
        self.assertIn("20 kg", answer.text)
        self.assertTrue(answer.validation.checks_run)

    def test_german_question_gets_a_german_answer(self):
        answer = self.composer.answer("Wieviel Zuladung hat der FlashBot?")
        self.assertEqual(answer.language, "de")
        self.assertIn("Laut Handbuch", answer.text)
        self.assertIn("20 kg", answer.text)

    def test_bonus02_italian_question_from_german_and_english_manuals(self):
        answer = self.composer.answer("Qual è la velocità massima del FlashBot?")
        self.assertEqual(answer.language, "it")
        self.assertIn("Secondo il manuale", answer.text)
        self.assertTrue(answer.to_dict()["cross_lingual"])
        self.assertTrue(any("italiano" in note.lower() or "IT" in note for note in answer.notes))
        # The value comes from a manual that has no Italian edition.
        self.assertIn("1.2 m/s", answer.text)

    def test_figure_question_returns_an_image(self):
        answer = self.composer.answer("Zeig mir ein Bild vom FlashBot")
        self.assertEqual(answer.route, "figure")
        self.assertTrue(answer.figures)
        self.assertTrue(answer.figures[0]["image"].startswith("images/"))
        self.assertTrue(answer.figures[0]["labels"])

    def test_advisory_question_compares_robots(self):
        answer = self.composer.answer("Which robot fits my restaurant?")
        self.assertEqual(answer.route, "advisory")
        self.assertGreaterEqual(len(answer.citations), 4)
        self.assertTrue(answer.grounded)

    def test_unknown_question_is_not_answered_with_an_invented_spec(self):
        answer = self.composer.answer("How do I reset the quantum flux capacitor?")
        self.assertTrue(answer.grounded)
        self.assertNotIn("flux capacitor is", answer.text)

    def test_every_eval_question_is_answered_without_invented_numbers(self):
        """The offline path must be trustworthy for all 28 benchmark questions."""
        import json

        path = Path(__file__).parent.parent / "eval/questions.jsonl"
        with path.open(encoding="utf-8") as fh:
            questions = [json.loads(line) for line in fh if line.strip()]
        failures = []
        for item in questions:
            answer = self.composer.answer(item["question"])
            if not answer.grounded:
                failures.append((item["id"], [i.to_dict() for i in answer.validation.errors]))
            self.assertEqual(answer.language, item["language"], item["id"])
        self.assertEqual(failures, [])

    def test_figure_paths_resolve_on_disk(self):
        """`Answer.figures` must be servable later (and is checked here, not assumed)."""
        import os

        answer = self.composer.answer("Mostrami una foto del FlashBot")
        self.assertTrue(answer.figures)
        figure = answer.figures[0]
        hit = next(h for h in answer.result.hits if h.image == figure["image"])
        path = Path(hit.metadata["source_file"]).parent / figure["image"]
        self.assertTrue(os.path.exists(path), path)
        self.assertTrue(figure["image"].startswith("images/en-"))   # no IT manual

    def test_offline_answer_names_why_it_is_offline(self):
        answer = self.composer.answer("What is the payload of the FlashBot?")
        self.assertEqual(answer.provider, "offline")
        self.assertTrue(any("offline formatter" in note for note in answer.notes))

    def test_all_three_languages_produce_a_localized_answer(self):
        cases = {
            "What is the maximum speed of the BellaBot?": "en",
            "Wie hoch ist die Akkukapazität des KettyBot Pro?": "de",
            "Quanto pesa la stazione di ricarica CC1?": "it",
        }
        for question, language in cases.items():
            answer = self.composer.answer(question)
            self.assertEqual(answer.language, language, question)
            self.assertTrue(answer.grounded, (question, answer.text))
            self.assertIn(text("param_lead", language).split("{")[0].strip()[:12],
                          answer.text, question)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# answer-level evaluation
# ---------------------------------------------------------------------------
class StubEngine:
    """Returns the same small retrieval result for any question (no corpus)."""

    def __init__(self, language="en"):
        self.result = _result_with_params_and_hits()
        self.result.language = language

    def search(self, query, k=5, **kwargs):
        self.result.query = query
        return self.result


class AnswerEvaluationTests(unittest.TestCase):
    def test_evaluate_scores_answers_and_language(self):
        from answer.evaluate import evaluate
        from answer.formatter import Draft

        class Fixed:
            name = "fixed"
            model = None

            def draft(self, prompt, evidence, *, language, route="default", question=""):
                return Draft("Load capacity is 20 kg [1].", provider=self.name, language=language)

        composer = AnswerComposer(StubEngine(language="en"), generator=Fixed())
        questions = [
            {"id": "1", "question": "payload?", "language": "en", "type": "lookup",
             "expect": {"keywords": ["20 kg"]}},
            {"id": "2", "question": "Zuladung?", "language": "de", "type": "lookup",
             "expect": {"keywords": ["20 kg"]}},
        ]
        report = evaluate(composer, questions)
        self.assertEqual(report.n, 2)
        self.assertEqual(report.answer_rate, 1.0)
        self.assertEqual(report.grounded_rate, 1.0)
        self.assertEqual(report.value_rate, 1.0)
        # The stub always answers in English; the DE question therefore fails.
        self.assertEqual(report.language_rate, 0.5)

    def test_evaluate_counts_invented_numbers(self):
        from answer.evaluate import evaluate
        from answer.formatter import Draft

        class Liar:
            name = "gemini"
            model = "fake"

            def draft(self, prompt, evidence, *, language, route="default", question=""):
                # repair=False is not used by the composer, so validation strips
                # the number; the evaluator must still see a clean answer.
                return Draft("The robot carries 45 kg [1].", provider="gemini", language=language)

        composer = AnswerComposer(StubEngine(language="en"), generator=Liar(), max_regenerations=0)
        questions = [{"id": "1", "question": "payload?", "language": "en", "type": "lookup"}]
        report = evaluate(composer, questions)
        self.assertEqual(report.invented, 0)
        self.assertIn("20 kg", report.results[0].text)

    def test_markdown_report_renders(self):
        from answer.evaluate import AnswerReport, AnswerResult, render_markdown

        report = AnswerReport(results=[
            AnswerResult(qid="L01", question="payload?", expected_language="en",
                         answer_language="en", route="lookup", provider="offline",
                         answered=True, grounded=True, verified=True),
        ])
        markdown = render_markdown(report)
        self.assertIn("# Answer composer + evidence validation", markdown)
        self.assertIn("| L01 |", markdown)
        self.assertIn("100%", markdown)
