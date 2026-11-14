"""Answer-level evaluation: is the answering phase actually grounded?

``retrieve.evaluate`` measures retrieval. This measures what comes *out* of the
composer, on the same 28 questions in ``eval/questions.jsonl``, and it works
with either generator -- the offline formatter (default) or Gemini:

    python -m answer.evaluate                      # offline, no API key
    python -m answer.evaluate --provider gemini    # needs GEMINI_API_KEY
    python -m answer.evaluate --out build/answer-report.md

Metrics, all computed from the answers themselves (never from the model):

| Metric | Question it answers |
|---|---|
| answer rate | did every question produce a non-empty answer? |
| grounded rate | did every answer survive citation + numeric + quote validation? |
| citation rate | share of factual sentences that carry a valid ``[n]`` |
| numeric rate | share of numbers/units that occur in the retrieved evidence |
| quote rate | share of quotations that appear verbatim in the cited source |
| language match | is the answer in the question's language? (Bonus 02) |
| cross-lingual disclosure | when evidence came from another language, did the answer say so? |
| value coverage | for lookup questions with an expected value, is that value in the answer? |
| invented values | numbers in the answer that are not in the evidence (must be 0 after repair) |
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from retrieve.engine import SearchEngine
from retrieve.env import load_dotenv
from retrieve.text import fold

from .composer import Answer, AnswerComposer

QUESTIONS_PATH = Path("eval/questions.jsonl")


@dataclass
class AnswerResult:
    qid: str
    question: str
    expected_language: str
    answer_language: str
    route: str
    provider: str
    answered: bool = False
    grounded: bool = False
    verified: bool = False
    citations_ok: bool = True
    language_ok: bool = False
    cross_lingual: bool = False
    disclosed: bool = False
    expected_value_found: bool | None = None
    invented_numbers: list[str] = field(default_factory=list)
    sentences: int = 0
    cited: int = 0
    numbers: int = 0
    numbers_ok: int = 0
    quotes: int = 0
    quotes_ok: int = 0
    text: str = ""
    issues: list[str] = field(default_factory=list)
    #: why the formatter wrote this answer instead of the model (``None`` if it did not)
    fallback_reason: str | None = None


@dataclass
class AnswerReport:
    results: list[AnswerResult] = field(default_factory=list)
    provider: str = "offline"

    @property
    def n(self) -> int:
        return len(self.results)

    def rate(self, predicate) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if predicate(r)) / self.n

    @property
    def answer_rate(self) -> float:
        return self.rate(lambda r: r.answered)

    @property
    def grounded_rate(self) -> float:
        return self.rate(lambda r: r.grounded)

    @property
    def verified_rate(self) -> float:
        return self.rate(lambda r: r.verified)

    @property
    def language_rate(self) -> float:
        return self.rate(lambda r: r.language_ok)

    @property
    def cross_lingual_rate(self) -> float:
        crossed = [r for r in self.results if r.cross_lingual]
        if not crossed:
            return 1.0
        return sum(1 for r in crossed if r.disclosed) / len(crossed)

    @property
    def value_rate(self) -> float:
        checked = [r for r in self.results if r.expected_value_found is not None]
        if not checked:
            return 1.0
        return sum(1 for r in checked if r.expected_value_found) / len(checked)

    @property
    def citation_rate(self) -> float:
        total = sum(r.sentences for r in self.results)
        return (sum(r.cited for r in self.results) / total) if total else 1.0

    @property
    def numeric_rate(self) -> float:
        total = sum(r.numbers for r in self.results)
        return (sum(r.numbers_ok for r in self.results) / total) if total else 1.0

    @property
    def quote_rate(self) -> float:
        total = sum(r.quotes for r in self.results)
        return (sum(r.quotes_ok for r in self.results) / total) if total else 1.0

    @property
    def invented(self) -> int:
        return sum(len(r.invented_numbers) for r in self.results)

    @property
    def written_by_model(self) -> int:
        """Answers whose text came from the configured LLM, not the formatter."""
        if self.provider == "offline":
            return 0
        return sum(1 for r in self.results if r.provider == self.provider)

    def fallback_reasons(self) -> list[tuple[str, int]]:
        """``[(reason, count)]`` for the answers the formatter wrote instead, most common first."""
        counts: dict[str, int] = {}
        for result in self.results:
            if result.fallback_reason:
                counts[result.fallback_reason] = counts.get(result.fallback_reason, 0) + 1
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def evaluate(composer: AnswerComposer, questions: list[dict]) -> AnswerReport:
    """Answer every question and score the answers (offline or LLM)."""
    report = AnswerReport(provider=composer.provider_info["provider"])
    for item in questions:
        answer = composer.answer(item["question"])
        report.results.append(_score(item, answer))
    return report


def _score(item: dict, answer: Answer) -> AnswerResult:
    validation = answer.validation
    expected_language = item.get("language", "en")
    evidence = answer.evidence
    cross = bool(evidence and evidence.cross_lingual_languages())
    disclosed = cross and _discloses_language(answer.text, expected_language, evidence)

    expected_value_found: bool | None = None
    keywords = (item.get("expect") or {}).get("keywords") or []
    # Only values with a digit are checkable as "the expected value appears";
    # procedure keywords ("power switch") are a retrieval concern.
    numeric_keywords = [kw for kw in keywords if any(char.isdigit() for char in kw)]
    if numeric_keywords and item.get("type") in ("lookup", "advisory"):
        haystack = fold(answer.text).replace(",", ".")
        expected_value_found = any(fold(kw).replace(",", ".") in haystack for kw in numeric_keywords)

    invented: list[str] = []
    if validation is not None:
        for issue in validation.errors:
            if issue.check == "numeric":
                invented.append(issue.message)

    return AnswerResult(
        qid=item.get("id", "?"),
        question=item["question"],
        expected_language=expected_language,
        answer_language=answer.language,
        route=answer.route,
        provider=answer.provider,
        answered=bool(answer.text.strip()),
        grounded=answer.grounded,
        verified=answer.verified,
        citations_ok=bool(validation and validation.ok),
        language_ok=answer.language == expected_language,
        cross_lingual=cross,
        disclosed=disclosed,
        expected_value_found=expected_value_found,
        invented_numbers=invented,
        fallback_reason=answer.fallback_reason,
        sentences=validation.sentences_checked if validation else 0,
        cited=validation.sentences_cited if validation else 0,
        numbers=validation.numbers_checked if validation else 0,
        numbers_ok=validation.numbers_verified if validation else 0,
        quotes=validation.quotes_checked if validation else 0,
        quotes_ok=validation.quotes_verified if validation else 0,
        text=answer.text,
        issues=[f"{i.check}/{i.severity}: {i.message}" for i in (validation.issues if validation else [])],
    )


_DISCLOSURE_MARKERS = {
    "en": ("note:", "there is no", "no english manual", "composed from"),
    "de": ("hinweis:", "es gibt kein", "kein handbuch auf", "stützt sich auf"),
    "it": ("nota:", "non esiste", "nessun manuale in", "si basa su fonti"),
}


def _discloses_language(text_value: str, language: str, evidence) -> bool:
    """Did the answer say that the evidence comes from another language?"""
    markers = _DISCLOSURE_MARKERS.get(language, _DISCLOSURE_MARKERS["en"])
    lowered = fold(text_value)
    if any(marker in lowered for marker in markers):
        return True
    # Also accept naming the source language explicitly.
    from .strings import language_name

    return any(
        fold(language_name(code, in_language=language)) in lowered
        for code in (evidence.languages if evidence else ())
        if code != language
    )


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def render_markdown(report: AnswerReport) -> str:
    lines = [
        "# Answer composer + evidence validation — evaluation report",
        "",
        f"Generator: **{report.provider}** · questions: **{report.n}** · "
        "source: `eval/questions.jsonl`",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| answered | {_pct(report.answer_rate)} |",
        f"| grounded (all three checks passed) | {_pct(report.grounded_rate)} |",
        f"| fully verified (no repairs needed) | {_pct(report.verified_rate)} |",
        f"| claims cited | {_pct(report.citation_rate)} |",
        f"| numbers verified against evidence | {_pct(report.numeric_rate)} |",
        f"| quotes verified verbatim | {_pct(report.quote_rate)} |",
        f"| answer in the question's language | {_pct(report.language_rate)} |",
        f"| cross-lingual evidence disclosed | {_pct(report.cross_lingual_rate)} |",
        f"| expected value present in the answer | {_pct(report.value_rate)} |",
        f"| unverified numbers surviving in answers | {report.invented} |",
        "",
        "## Per question",
        "",
        "| id | lang | route | grounded | cited | numbers | quotes | value | cross-lingual |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for result in report.results:
        lines.append(
            "| {qid} | {lang} | {route} | {grounded} | {cited}/{sentences} | "
            "{numbers_ok}/{numbers} | {quotes_ok}/{quotes} | {value} | {cross} |".format(
                qid=result.qid,
                lang=result.answer_language.upper(),
                route=result.route,
                grounded="yes" if result.grounded else "**no**",
                cited=result.cited,
                sentences=result.sentences,
                numbers_ok=result.numbers_ok,
                numbers=result.numbers,
                quotes_ok=result.quotes_ok,
                quotes=result.quotes,
                value="—" if result.expected_value_found is None
                else ("yes" if result.expected_value_found else "**missing**"),
                cross=("disclosed" if result.disclosed else "**silent**")
                if result.cross_lingual
                else "—",
            )
        )
    failures = [r for r in report.results if not r.grounded]
    if failures:
        lines += ["", "## Answers that did not pass validation", ""]
        for result in failures:
            lines.append(f"### {result.qid} — {result.question}")
            for issue in result.issues:
                lines.append(f"- {issue}")
            lines.append("")
    return "\n".join(lines) + "\n"


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Evaluate the answer composer end to end.")
    parser.add_argument("--questions", default=str(QUESTIONS_PATH))
    parser.add_argument("--provider", default=None, help="gemini | offline (default: auto)")
    parser.add_argument("--k", default=5, type=int)
    parser.add_argument("--out", default=None, help="write a markdown report to this path")
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    parser.add_argument("--verbose", action="store_true", help="print every answer")
    parser.add_argument(
        "--vector", action="store_true",
        help="answer through the hybrid (dense build/chroma + keyword) instead of keyword only, "
             "so the embedding layer can be measured on answers and not just on retrieval",
    )
    args = parser.parse_args(argv)

    path = Path(args.questions)
    if not path.exists():
        print(f"error: {path} not found", file=sys.stderr)
        return 2
    with path.open(encoding="utf-8") as fh:
        questions = [json.loads(line) for line in fh if line.strip()]

    engine = SearchEngine.from_build(vector="auto" if args.vector else None)
    if args.vector and engine.retriever_note:
        # Without this the run silently measures the keyword floor and reports it
        # as the hybrid: the whole point of --vector is to see the dense layer.
        print(f"Note: {engine.retriever_note}")
    composer = AnswerComposer(engine, provider=args.provider, k=args.k)
    report = evaluate(composer, questions)

    if args.json:
        print(json.dumps(
            {
                "provider": report.provider,
                "retrieval": (
                    "hybrid" if engine.retriever is not None else "keyword-only"
                ),
                "metrics": {
                    "answered": report.answer_rate,
                    "grounded": report.grounded_rate,
                    "verified": report.verified_rate,
                    "citations": report.citation_rate,
                    "numbers": report.numeric_rate,
                    "quotes": report.quote_rate,
                    "language": report.language_rate,
                    "cross_lingual_disclosed": report.cross_lingual_rate,
                    "expected_value": report.value_rate,
                    "invented_numbers": report.invented,
                },
                "results": [
                    {k: v for k, v in result.__dict__.items() if k != "text"}
                    for result in report.results
                ],
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0 if report.grounded_rate == 1.0 else 1

    retrieval_mode = (
        f"hybrid (dense {getattr(engine.retriever, 'provider', 'vector')} + keyword)"
        if engine.retriever is not None
        else "keyword only"
    )
    print(
        f"\nAnswer composer evaluation ({report.provider}, {report.n} questions, "
        f"retrieval: {retrieval_mode})"
    )
    print(f"  answered ....................... {_pct(report.answer_rate)}")
    print(f"  grounded (validation passed) ... {_pct(report.grounded_rate)}")
    print(f"  fully verified (no repairs) .... {_pct(report.verified_rate)}")
    print(f"  claims cited ................... {_pct(report.citation_rate)}")
    print(f"  numbers verified ............... {_pct(report.numeric_rate)}")
    print(f"  quotes verbatim ................ {_pct(report.quote_rate)}")
    print(f"  answer language correct ........ {_pct(report.language_rate)}")
    print(f"  cross-lingual disclosed ........ {_pct(report.cross_lingual_rate)}")
    print(f"  expected value present ......... {_pct(report.value_rate)}")
    print(f"  unverified numbers surviving ... {report.invented}")
    if report.provider != "offline":
        # The number that answers "is the model writing, or is it still quoting?"
        print(f"  written by the model ........... {report.written_by_model}/{report.n}")
        for reason, count in report.fallback_reasons():
            print(f"      {count} x formatter instead: {reason}")

    if args.verbose:
        for result in report.results:
            print(f"\n[{result.qid}] {result.question}")
            print(f"  {result.route} · {result.answer_language} · "
                  f"{'grounded' if result.grounded else 'FAILED'}")
            print("  " + result.text.replace("\n", "\n  ")[:600])

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(report), encoding="utf-8")
        print(f"\nreport written to {out}")
    return 0 if report.grounded_rate == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
