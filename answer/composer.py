"""The grounded answer composer: retrieve -> generate -> validate -> return.

This is the `/ask` logic of the answering phase, in one place, with no
framework. It is deliberately a thin orchestration layer:

```
question ──► SearchEngine.search          (retrieve/ — Bonus 01 engine)
         ──► Evidence.from_result         (citable, numbered, checkable)
         ──► generator.draft              (Gemini 2.5 Flash, or offline)
         ──► validate_answer              (G5: citation + numeric + quote)
         ──► regenerate once if needed    (only the failed claims are named)
         ──► Answer                       (text + citations + images + report)
```

The two guarantees the challenge is judged on:

* **Faithfulness (G5)** -- an answer never reaches a caller unvalidated. If the
  LLM's prose cannot be repaired, the answer is replaced by the deterministic
  formatter's grounded version, and the response says so. Retrieved evidence is
  returned alongside, so a user can check any claim with one click.
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

import time
from dataclasses import dataclass, field
from typing import Any

from retrieve.engine import SearchEngine, SearchResult

from .evidence import Evidence
from .formatter import Draft, format_answer
from .llm import Generator, build_generator, describe_provider
from .prompt import build_prompt, language_instruction
from .strings import text
from .validator import ValidationReport, validate_answer


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
    ):
        self.engine = engine
        self.k = k
        self.max_regenerations = max(1, max_regenerations)
        self.max_chunks = max_chunks
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

    # -- main entry point --------------------------------------------------
    def answer(
        self,
        question: str,
        *,
        k: int | None = None,
        language: str | None = None,
        result: SearchResult | None = None,
        **search_kwargs: Any,
    ) -> Answer:
        """Answer one question, grounded and validated."""
        started = time.perf_counter()
        result = result or self.engine.search(question, k=k or self.k, language=language, **search_kwargs)
        evidence = Evidence.from_result(result, max_chunks=self.max_chunks)
        language = result.language or language or "en"

        notes: list[str] = []
        if not evidence.refs:
            draft = format_answer(evidence, question=question, language=language, route=result.route)
            return self._finish(question, draft, evidence, result, language, notes, started)

        generator = self.generator
        prompt = build_prompt(question, evidence, language=language, route=result.route)
        draft = _safe_draft(
            generator, prompt, evidence, language, result.route, question
        )
        if draft is None:
            draft = format_answer(evidence, question=question, language=language, route=result.route)
            draft.notes.append(text("provider_fallback_note", language))
            notes.append("llm unavailable -> offline formatter")
        elif getattr(draft, "provider", "") != "gemini":
            info = self.provider_info
            notes.append(
                f"offline formatter ({info['reason']})" if info.get("reason") else "offline formatter"
            )

        report = validate_answer(draft.text, evidence, language=language)

        # One retry, with the concrete failures named in the prompt. Only worth
        # it for an LLM: the deterministic formatter cannot do better twice.
        attempts = 0
        while (
            not report.ok
            and getattr(draft, "provider", "") == "gemini"
            and attempts < self.max_regenerations
        ):
            attempts += 1
            notes.append("regenerated after failed evidence validation")
            retry_prompt = _repair_prompt(prompt, report)
            retry = _safe_draft(
                generator, retry_prompt, evidence, language, result.route, question
            )
            if retry is None:
                break
            retry_report = validate_answer(retry.text, evidence, language=language)
            if retry_report.ok or not report.ok:
                draft, report = retry, retry_report

        if not report.ok:
            # Last resort: deterministic, grounded, always citable.
            fallback = format_answer(evidence, question=question, language=language, route=result.route)
            fallback_report = validate_answer(fallback.text, evidence, language=language)
            if fallback_report.ok:
                notes.append("answered by the offline formatter after failed validation")
                draft, report = fallback, fallback_report

        return self._finish(question, draft, evidence, result, language, notes, started, report)

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
        + "\n\nRewrite the answer. Use only numbers and quotes that appear verbatim in the "
        "numbered evidence, cite every factual sentence with [n], and omit anything you "
        "cannot support."
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
