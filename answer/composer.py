"""The grounded answer composer: retrieve -> generate -> validate -> return.

This is the `/ask` logic of the answering phase, in one place, with no
framework. It is deliberately a thin orchestration layer:

```
question ──► SearchEngine.search          (retrieve/ — Bonus 01 engine)
         ──► Evidence.from_result         (citable, numbered, checkable)
         ──► generator.draft              (Gemini, or the offline formatter)
         ──► validate_answer              (G5: citation + numeric + quote)
         ──► regenerate once if needed    (only the failed claims are named)
         ──► Answer                       (text + citations + images + report)
```

The two guarantees the challenge is judged on:

* **Faithfulness (G5)** -- an answer never reaches a caller unvalidated. A
  generated answer is replaced by the deterministic formatter's version only
  when it still states a number the evidence does not contain, or cites a
  source that was not retrieved, after one retry -- and the response says so.
  Everything else (a greeting, a transition, "the manuals do not cover that")
  is the model's own wording and is kept; sentences without a source are
  reported as uncited rather than removed. ``strict=True`` (or
  ``ROBOG_ANSWER_STRICT=1``) restores the all-or-nothing rule, where any
  sentence without a source discards the generated answer. Retrieved evidence
  is returned alongside, so a user can check any claim with one click.
* **Bonus 02 (multilingual)** -- the answer language is the *question's*
  language, detected deterministically by ``retrieve.lang``; when the evidence
  had to come from another language the answer says which one. This happens in
  the offline formatter too, so the demo works without a key.

Usage:
    from answer.composer import AnswerComposer
    composer = AnswerComposer(engine)                   # engine = SearchEngine
    answer = composer.answer("Quanto pesa il FlashBot?")
    answer.text            # Italian answer, cites [1] [2]
    answer.citations       # manual, §section, page, language
    answer.validation.ok   # True: every claim checked out
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from retrieve.engine import SearchEngine, SearchResult

from .evidence import Evidence
from retrieve.profiles import has_numeric_constraint
from .formatter import Draft, format_answer
from .llm import Generator, build_generator, describe_provider
from .prompt import build_prompt, language_instruction
from .strings import text
from .validator import ValidationReport, validate_answer

#: ``1`` / ``true``: any sentence without a source discards the generated answer.
ENV_ANSWER_STRICT = "ROBOG_ANSWER_STRICT"


def _env_strict() -> bool:
    return (os.environ.get(ENV_ANSWER_STRICT) or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Answer:
    """A grounded answer plus everything needed to check and display it."""

    question: str
    text: str
    language: str
    route: str
    provider: str
    model: str | None = None
    citations: list[dict] = field(default_factory=list)
    figures: list[dict] = field(default_factory=list)
    evidence: Evidence | None = None
    validation: ValidationReport | None = None
    result: SearchResult | None = None
    notes: list[str] = field(default_factory=list)
    latency_ms: int = 0
    #: Why the deterministic formatter wrote this answer instead of the LLM
    #: (``None`` when the LLM's answer was used). Shown to the user, so "the
    #: bot pastes passages" is never a mystery.
    fallback_reason: str | None = None

    @property
    def grounded(self) -> bool:
        """True when nothing had to be dropped or flagged unrepaired."""
        return bool(self.validation and self.validation.ok)

    @property
    def verified(self) -> bool:
        return bool(self.validation and self.validation.verified)

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "answer": self.text,
            "language": self.language,
            "route": self.route,
            "provider": self.provider,
            "model": self.model,
            "grounded": self.grounded,
            "verified": self.verified,
            "citations": self.citations,
            "figures": self.figures,
            "validation": self.validation.to_dict() if self.validation else None,
            "language_instruction": language_instruction(self.language),
            "cross_lingual": bool(self.evidence and self.evidence.cross_lingual_languages()),
            "source_languages": self.evidence.languages if self.evidence else [],
            "notes": self.notes,
            "fallback_reason": self.fallback_reason,
            "latency_ms": self.latency_ms,
            "search": _result_dict(self.result),
            "evidence": self.evidence.to_dict() if self.evidence else None,
        }


class AnswerComposer:
    """Retrieval + pluggable generation + evidence validation."""

    def __init__(
        self,
        engine: SearchEngine,
        *,
        generator: Generator | None = None,
        provider: str | None = None,
        model: str | None = None,
        k: int = 5,
        max_regenerations: int = 1,
        max_chunks: int = 5,
        strict: bool | None = None,
    ):
        self.engine = engine
        self.k = k
        self.max_regenerations = max(1, max_regenerations)
        self.max_chunks = max_chunks
        #: ``None`` reads ``ROBOG_ANSWER_STRICT`` per request (default: off).
        self._strict = strict
        self._generator = generator
        self._provider = provider
        self._model = model

    # -- generator lifecycle ----------------------------------------------
    @property
    def generator(self) -> Generator:
        """Built lazily so importing the composer never touches the network."""
        if self._generator is None:
            self._generator = build_generator(self._provider, model=self._model)
        return self._generator

    @generator.setter
    def generator(self, value: Generator) -> None:
        self._generator = value

    @property
    def provider_info(self) -> dict:
        return describe_provider(self.generator)

    @property
    def strict(self) -> bool:
        return _env_strict() if self._strict is None else bool(self._strict)

    # -- main entry point --------------------------------------------------
    def answer(
        self,
        question: str,
        *,
        k: int | None = None,
        language: str | None = None,
        result: SearchResult | None = None,
        history: list[str] | None = None,
        **search_kwargs: Any,
    ) -> Answer:
        """Answer one question, grounded and validated.

        Recent user turns may resolve a follow-up's product reference, but never
        supply answer facts; all claims still come from newly retrieved evidence.
        """
        started = time.perf_counter()
        history = [
            " ".join(str(turn).split())[:300]
            for turn in (history or [])[-6:]
            if str(turn).strip()
        ]
        mentioned_products = getattr(self.engine, "mentioned_products", None)
        if not result and not search_kwargs.get("product") and callable(mentioned_products):
            mentioned_now = mentioned_products(question)
            if not mentioned_now:
                # Only infer from the nearest prior question that names a product;
                # an ambiguous comparison is not silently resolved to one side.
                for prior in reversed(history):
                    mentioned_before = mentioned_products(prior)
                    if mentioned_before:
                        if len(mentioned_before) == 1:
                            search_kwargs["product"] = mentioned_before[0]
                        break
        requested_language = language        # an explicit request wins over detection
        result = result or self.engine.search(question, k=k or self.k, language=language, **search_kwargs)
        evidence = Evidence.from_result(result, max_chunks=self.max_chunks)
        language = result.language or language or "en"

        notes: list[str] = []

        # "hello" / "danke" / "ciao" is not a question about a robot. Retrieval
        # still returns its closest passage for the word, and answering a
        # greeting with a paragraph about delivery modes is the clearest case of
        # "the bot pastes the manual". Reply in kind instead, without evidence.
        small_talk = _small_talk(question)
        if small_talk is not None:
            kind, talk_language = small_talk
            language = requested_language or talk_language or language
            draft = Draft(
                text=text("greeting" if kind == "greeting" else "thanks_reply", language),
                provider="offline",
                language=language,
                notes=[f"small talk ({kind}): answered without retrieval evidence"],
            )
            empty = Evidence(language=language, notes=list(result.notes))
            return self._finish(question, draft, empty, result, language, notes, started)

        # "What is the value of this parameter?" names neither a robot nor a
        # parameter. The index holds 2,039 parameter rows, so whatever passage
        # BM25 returns for the bare words "value"/"parameter" is a guess dressed
        # as an answer -- and the technician persona in the brief is better
        # served by the one thing that actually helps: ask which robot and which
        # parameter. The gate runs on the retrieval result, before evidence
        # selection, because junk passages are perfectly retrievable.
        if result.route == "lookup" and not result.params and not result.products:
            draft = Draft(
                text=text("needs_robot_and_parameter", language),
                provider="offline",
                language=language,
                notes=["under-specified lookup: no robot and no parameter named"],
            )
            empty = Evidence(language=language, notes=list(result.notes))
            notes.append("asked back: question named neither a robot nor a parameter")
            return self._finish(question, draft, empty, result, language, notes, started)

        # A stated load or passage that no card satisfies: say so, do not
        # substitute a robot that fails the requirement.
        if (
            result.route == "advisory"
            and not result.products
            and not result.profiles
            and not result.needs_scenario
            and has_numeric_constraint(question)
        ):
            draft = Draft(
                text=text("no_match_constraint", language),
                provider="offline",
                language=language,
                notes=["advisory: no robot satisfies the stated load or width"],
            )
            empty = Evidence(language=language, notes=list(result.notes))
            notes.append("asked back: no robot satisfies the stated constraint")
            return self._finish(question, draft, empty, result, language, notes, started)

        # "Which robot is best?" names no use case, environment or number. Any
        # ranking would be arbitrary, so ask for the scenario instead of listing
        # all 13 robots as if they were a recommendation.
        if result.route == "advisory" and result.needs_scenario:
            draft = Draft(
                text=text("needs_scenario", language),
                provider="offline",
                language=language,
                notes=["under-specified advisory: no use case named"],
            )
            empty = Evidence(language=language, notes=list(result.notes))
            notes.append("asked back: advisory question named no use case")
            return self._finish(question, draft, empty, result, language, notes, started)

        if not evidence.refs:
            draft = format_answer(evidence, question=question, language=language, route=result.route)
            return self._finish(question, draft, evidence, result, language, notes, started)

        generator = self.generator
        strict = self.strict
        fallback_reason: str | None = None
        prompt = build_prompt(
            question, evidence, language=language, route=result.route, history=history
        )
        draft = _safe_draft(
            generator, prompt, evidence, language, result.route, question
        )
        if draft is None:
            error = getattr(generator, "last_error", None)
            fallback_reason = f"the LLM call failed: {error}" if error else "the LLM call failed"
            draft = format_answer(evidence, question=question, language=language, route=result.route)
            draft.notes.append(text("provider_fallback_note", language))
            notes.append(
                f"llm unavailable -> offline formatter ({error})"
                if error else "llm unavailable -> offline formatter"
            )
        elif getattr(draft, "provider", "") != "gemini":
            info = self.provider_info
            if getattr(draft, "provider", "") == "offline":
                if (self._provider or "").strip().lower() in ("offline", "deterministic", "none"):
                    fallback_reason = "the offline formatter was selected for this request"
                else:
                    fallback_reason = info.get("reason") or "no LLM is configured"
            notes.append(
                f"offline formatter ({info['reason']})" if info.get("reason") else "offline formatter"
            )

        from_llm = getattr(draft, "provider", "") == "gemini"
        report = validate_answer(draft.text, evidence, language=language, strict=strict)

        # One retry, with the concrete failures named in the prompt. Only worth
        # it for an LLM: the deterministic formatter cannot do better twice.
        attempts = 0
        while not report.ok and from_llm and attempts < self.max_regenerations:
            attempts += 1
            notes.append("regenerated after failed evidence validation")
            retry_prompt = _repair_prompt(prompt, report)
            retry = _safe_draft(
                generator, retry_prompt, evidence, language, result.route, question
            )
            if retry is None:
                break
            retry_report = validate_answer(retry.text, evidence, language=language, strict=strict)
            if len(retry_report.errors) <= len(report.errors):
                draft, report = retry, retry_report

        if not report.ok or (from_llm and not report.answer.strip()):
            # Last resort: deterministic, grounded, always citable.
            fallback = format_answer(evidence, question=question, language=language, route=result.route)
            fallback_report = validate_answer(
                fallback.text, evidence, language=language, strict=strict
            )
            if fallback_report.ok:
                notes.append("answered by the offline formatter after failed validation")
                if from_llm:
                    fallback_reason = _validation_reason(report)
                draft, report = fallback, fallback_report

        return self._finish(
            question, draft, evidence, result, language, notes, started, report,
            fallback_reason=fallback_reason,
        )

    # -- assembly ----------------------------------------------------------
    def _finish(
        self,
        question: str,
        draft: Draft,
        evidence: Evidence,
        result: SearchResult,
        language: str,
        notes: list[str],
        started: float,
        report: ValidationReport | None = None,
        fallback_reason: str | None = None,
    ) -> Answer:
        report = report or validate_answer(draft.text, evidence, language=language)
        citations = _citations(report, evidence)
        figures = _figures(evidence, result)
        notes = list(dict.fromkeys(notes + list(draft.notes) + list(result.notes)))
        if not evidence.refs:
            report.sentences_checked = 0
        return Answer(
            question=question,
            text=report.answer,
            language=language,
            route=result.route,
            provider=draft.provider,
            model=draft.model,
            citations=citations,
            figures=figures,
            evidence=evidence,
            validation=report,
            result=result,
            notes=notes,
            latency_ms=int((time.perf_counter() - started) * 1000),
            fallback_reason=fallback_reason,
        )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _result_dict(result: SearchResult | None) -> dict | None:
    """The retrieval result as JSON when it supports it (test doubles may not)."""
    if result is None:
        return None
    to_dict = getattr(result, "to_dict", None)
    return to_dict() if callable(to_dict) else None


def _safe_draft(
    generator: Generator,
    prompt: str,
    evidence: Evidence,
    language: str,
    route: str = "default",
    question: str = "",
) -> Draft | None:
    """Call a generator, converting every failure into ``None`` (demo-safe)."""
    try:
        return generator.draft(
            prompt, evidence, language=language, route=route, question=question
        )
    except Exception as exc:  # a generator must never break /ask
        import sys

        print(
            f"warning: answer generator {getattr(generator, 'name', '?')} failed: "
            f"{type(exc).__name__}: {str(exc)[:200]}",
            file=sys.stderr,
        )
        return None


#: Whole-message greetings and thanks, per language. Matched only when they are
#: the entire message, so "Hello, how do I clean the BellaBot?" is a question.
_SMALL_TALK: dict[str, dict[str, frozenset[str]]] = {
    "en": {
        "greeting": frozenset({
            "hi", "hello", "hey", "hi there", "hello there", "hey there",
            "good morning", "good afternoon", "good evening",
        }),
        "thanks": frozenset({
            "thanks", "thank you", "thanks a lot", "thank you very much", "many thanks", "thx",
        }),
    },
    "de": {
        "greeting": frozenset({
            "hallo", "guten tag", "guten morgen", "guten abend", "servus", "moin",
            "gruss gott", "gruss dich", "hallo zusammen",
        }),
        "thanks": frozenset({
            "danke", "danke schon", "dankeschon", "vielen dank", "danke sehr", "besten dank",
        }),
    },
    "it": {
        "greeting": frozenset({
            "ciao", "salve", "buongiorno", "buon giorno", "buonasera", "buona sera",
        }),
        "thanks": frozenset({"grazie", "grazie mille", "molte grazie", "ti ringrazio"}),
    },
}


def _small_talk(question: str) -> tuple[str, str] | None:
    """``("greeting" | "thanks", language)`` when the whole message is small talk."""
    from retrieve.text import fold

    words = "".join(char if char.isalnum() else " " for char in fold(question or "")).split()
    if words and words[-1] == "robog":
        words = words[:-1]
    phrase = " ".join(words)
    if not phrase or len(words) > 4:
        return None
    for language, kinds in _SMALL_TALK.items():
        for kind, phrases in kinds.items():
            if phrase in phrases:
                return kind, language
    return None


def _validation_reason(report: ValidationReport) -> str:
    """One sentence on why a generated answer was not used."""
    what = {
        "numeric": "a number that is not in the retrieved manual passages",
        "quote": "a quotation that is not in the cited source",
    }
    parts: list[str] = []
    for issue in report.errors:
        if issue.check == "citation" and issue.message.startswith("claim has no citation"):
            part = "a sentence with no source (strict mode)"
        elif issue.check == "citation":
            part = "a citation that points at no retrieved source"
        else:
            part = what.get(issue.check, issue.check)
        if part not in parts:
            parts.append(part)
    if not parts:
        return "the generated answer was empty after the evidence check"
    return "the generated answer contained " + " and ".join(parts)


def _repair_prompt(prompt: str, report: ValidationReport) -> str:
    """Append the validator's findings so the model can fix exactly those."""
    problems: list[str] = []
    for issue in report.issues:
        if issue.severity != "error":
            continue
        line = f"- {issue.check}: {issue.message}"
        if issue.detail:
            line += f" (offending text: {issue.detail[:160]!r})"
        if line not in problems:
            problems.append(line)
    if not problems:
        return prompt
    return (
        prompt
        + "\n\nYour previous answer failed the evidence check:\n"
        + "\n".join(problems)
        + "\n\nRewrite the answer in the same helpful, conversational style. Use only numbers "
        "that appear in the numbered evidence (same value, same unit), cite only the evidence "
        "numbers listed above, and leave out anything you cannot support."
    )


def _citations(report: ValidationReport, evidence: Evidence) -> list[dict]:
    """The evidence items the answer actually cites, in reference order."""
    from .textutil import cited_refs

    used = cited_refs(report.answer) or report.refs_used
    citations = []
    for n in used:
        ref = evidence.ref(n)
        if ref is None:
            continue
        citations.append(
            {
                "n": ref.n,
                "kind": ref.kind,
                "citation": ref.citation,
                "language": ref.language,
                "product": ref.product,
                "page": ref.page,
                "section": ref.section,
                "title": ref.title,
                "excerpt": (ref.text or "")[:280],
                "image": ref.image,
            }
        )
    return citations


def _figures(evidence: Evidence, result: SearchResult) -> list[dict]:
    """Figures to render next to the answer (route=figure first, then any hit)."""
    figures: list[dict] = []
    seen: set[str] = set()

    def add(ref_or_hit) -> None:
        image = getattr(ref_or_hit, "image", None)
        if not image or image in seen:
            return
        seen.add(image)
        meta = getattr(ref_or_hit, "metadata", {}) or {}
        labels = list(getattr(ref_or_hit, "labels", ()) or ())
        figures.append(
            {
                "image": image,
                "language": getattr(ref_or_hit, "language", "") or meta.get("language", ""),
                "product": getattr(ref_or_hit, "product", "") or meta.get("product", ""),
                "caption": getattr(ref_or_hit, "citation", "") or meta.get("section_title", ""),
                "labels": labels,
            }
        )

    for ref in evidence.refs:
        if ref.image:
            add(ref)
    for hit in result.hits:
        if getattr(hit, "image", None):
            add(hit)
    return figures[:3]
