"""Evidence validation (gap G5): is the generated answer actually grounded?

Three deterministic post-checks run on every answer *before* it is returned,
no matter who wrote it (Gemini or the offline formatter):

1. **Citation check** -- every factual sentence carries at least one valid
   reference (``[1]``, ``[2, 3]``) pointing into the retrieved evidence; a
   citation of a ref that was never retrieved is an error. A sentence whose
   citation is missing is repaired by attaching the best-matching ref, and only
   dropped when nothing in the evidence supports it.
2. **Numeric verification** -- every number/unit in the answer must occur in the
   evidence (retrieved chunks + parameter rows + profile cards). A number that
   cannot be found is removed from the sentence and reported; the answer never
   ships with an invented specification. Numbers without a unit are checked
   against the evidence values; numbers with a unit must match value *and* unit,
   which is what catches the classic ``25 Ah`` -> ``25 V`` slip.
3. **Quote check** -- text inside quotes (``"…"``, ``„…“``, ``«…»``) must appear
   verbatim in the text of the refs the sentence cites (or, for a sentence that
   cites nothing yet, anywhere in the evidence). Because the validator sees the
   answer *before* repairs, citations inside a quotation are neutralised first --
   otherwise an excerpt that quotes paragraphs containing our own ``[n]`` markers
   could never verify. A quote that cannot be found is unquoted into plain text
   and reported.

All checks are regex + set membership: cheap, language-agnostic and model-free,
so a live demo can *show* verification instead of promising it.

Usage:
    from answer.validator import validate_answer
    report = validate_answer(answer_markdown, evidence, language="it")
    report.ok, report.issues, report.answer
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from retrieve.text import fold

from .evidence import Evidence, iter_number_units
from .textutil import (
    CITATION_RE,
    REFERENCE_LINE_RE,
    alnum_tokens,
    citation_markers,
    collapse,
    cited_refs,
    split_sentences,
)

Severity = Literal["error", "warning", "info"]

#: Quoted spans: ``"…"``, ``„…“`` and ``«…»`` (the excerpt styles of the corpus).
#: Built by concatenation rather than an escaped class so the typographic
#: quotes are matched as characters, not as regex escapes.
_QUOTE_CHARS = '\u201e\u201c\u201d\u00ab\u00bb"'
QUOTE_RE = re.compile(
    "[" + _QUOTE_CHARS + "]([^" + _QUOTE_CHARS + "\\n]{2,400})[" + _QUOTE_CHARS + "]"
)

#: A quoted bare measurement ("20 kg") is a numeric claim, not a quotation.
_NUMERIC_QUOTE_RE = re.compile(r"^\s*[-+]?\d+(?:[.,]\d+)?\s*[A-Za-z°%/µ]{0,8}\s*$")

#: Sentences that are scaffolding, not claims (no citation needed).
_NON_CLAIM_STARTS = (
    "note:", "hinweis:", "nota:", "answer:", "antwort:", "risposta:",
    "source:", "quellen:", "fonti:", "confidence:",
)
_NON_CLAIM_CONTAINS = (
    "could not find", "nichts gefunden", "non ho trovato",
    "no information", "keine information", "nessuna informazione",
    "offline formatter", "offline-formatierer", "formatter offline",
    "no llm call", "kein llm-aufruf", "nessuna chiamata llm",
)

#: Replacement when an unverified number/unit must leave a sentence.
REDACTION = {"en": "some", "de": "einige", "it": "alcuni"}

_REF_INLINE_RE = re.compile(r"\[\s*\d+")

#: Leftover markers after a redaction (``"(several)"``, ``"some / h"``).
_EMPTY_PARENS_RE = re.compile(r"\(\s*(?:several|some|einige|alcuni)\s*\)")
_EMPTY_SLASH_RE = re.compile(
    r"\b(?:several|some|einige|alcuni|diversi)\s*(?:/\s*[a-zA-Z°%]+)?\s*(?=[),.;:\]]|$)"
)


@dataclass
class Issue:
    """One finding of the evidence validator."""

    check: Literal["citation", "numeric", "quote"]
    severity: Severity
    message: str
    detail: str = ""
    #: zero-based index of the affected sentence (None for whole-answer issues)
    sentence: int | None = None
    action: Literal["kept", "repaired", "dropped", "flagged"] = "flagged"

    def to_dict(self) -> dict:
        return {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
            "detail": self.detail,
            "sentence": self.sentence,
            "action": self.action,
        }


@dataclass
class ValidationReport:
    """Outcome of the three checks, plus the repaired answer and a summary."""

    answer: str
    issues: list[Issue] = field(default_factory=list)
    sentences_checked: int = 0
    sentences_cited: int = 0
    numbers_checked: int = 0
    numbers_verified: int = 0
    quotes_checked: int = 0
    quotes_verified: int = 0
    refs_used: list[int] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def ok(self) -> bool:
        """True when nothing was dropped or left ungrounded."""
        return not self.errors

    @property
    def verified(self) -> bool:
        """True when every claim carried a citation and no repair was needed."""
        return self.ok and not self.warnings

    @property
    def citation_rate(self) -> float:
        if not self.sentences_checked:
            return 1.0
        return self.sentences_cited / self.sentences_checked

    @property
    def numeric_rate(self) -> float:
        if not self.numbers_checked:
            return 1.0      # nothing numeric to verify is not a numeric failure
        return self.numbers_verified / self.numbers_checked

    @property
    def quote_rate(self) -> float:
        if not self.quotes_checked:
            return 1.0
        return self.quotes_verified / self.quotes_checked

    def summary(self, language: str = "en") -> str:
        """One line for the UI ("all 4 claims cited, 6/6 numbers verified")."""
        from .strings import text

        return text(
            "validation_summary",
            language,
            cited=self.sentences_cited,
            sentences=self.sentences_checked,
            verified=self.numbers_verified,
            numbers=self.numbers_checked,
        )

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "verified": self.verified,
            "sentences_checked": self.sentences_checked,
            "sentences_cited": self.sentences_cited,
            "citation_rate": round(self.citation_rate, 3),
            "numbers_checked": self.numbers_checked,
            "numbers_verified": self.numbers_verified,
            "numeric_rate": round(self.numeric_rate, 3),
            "quotes_checked": self.quotes_checked,
            "quotes_verified": self.quotes_verified,
            "quote_rate": round(self.quote_rate, 3),
            "refs_used": self.refs_used,
            "issues": [issue.to_dict() for issue in self.issues],
            "answer": self.answer,
        }


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------
def validate_answer(
    answer: str,
    evidence: Evidence,
    *,
    language: str | None = None,
    repair: bool = True,
) -> ValidationReport:
    """Run cite/number/quote checks over ``answer`` and return the cleaned answer.

    ``repair=True`` (default) removes unverifiable numbers, drops citations that
    point at nothing, attaches a citation to an ungrounded claim or removes the
    claim, and unquotes text that is not in the source. ``repair=False`` only
    reports -- useful for measuring a generator without changing its output.
    """
    language = language or evidence.language or "en"
    report = ValidationReport(answer=answer)
    if not answer.strip() or not evidence.refs:
        return report

    known = {ref.n for ref in evidence.refs}
    out_lines: list[str] = []

    for line in answer.splitlines():
        if not line.strip():
            out_lines.append(line)
            continue
        if REFERENCE_LINE_RE.match(line):
            _check_reference_line(line, known, report)
            out_lines.append(line)
            continue
        if line.lstrip().startswith("#") or not _is_claim(line):
            out_lines.append(line)
            continue
        out_lines.append(
            _check_line(line, evidence, known, report, language, repair)
        )

    report.answer = (_tidy("\n".join(out_lines)) if repair else answer).strip()
    report.refs_used = sorted(set(report.refs_used))
    return report


# --------------------------------------------------------------------------
# line / sentence handling
# --------------------------------------------------------------------------
def _check_line(
    line: str,
    evidence: Evidence,
    known: set[int],
    report: ValidationReport,
    language: str,
    repair: bool,
) -> str:
    """Validate one line, keeping its markdown prefix and quoting intact."""
    prefix_match = re.match(r"^(\s*(?:[-*•]|\d+\.)\s+)", line)
    prefix = prefix_match.group(1) if prefix_match else ""
    body = line[len(prefix):] if prefix else line
    if not body.strip():
        return line

    # The quote check needs the *original* text (quotes may span full sentences
    # and contain our own markers); quoting is neutralised inside citations.
    body = _verify_quotes(body, evidence, report, _sentence_index(report), language, repair)
    body = _strip_invalid_citations(body, known, report, _sentence_index(report))
    body = _verify_numbers(body, evidence, report, _sentence_index(report), language, repair)
    body = _ensure_citations(body, evidence, report, repair)
    if not body.strip():
        return ""
    return prefix + body


def _sentence_index(report: ValidationReport) -> int:
    return report.sentences_checked


def _verify_quotes(
    body: str,
    evidence: Evidence,
    report: ValidationReport,
    index: int,
    language: str,
    repair: bool,
) -> str:
    cited = cited_refs(body)
    scope = evidence.texts_for(cited) if cited else [ref.support for ref in evidence.refs]
    haystacks = [_neutralise(collapse(fold(text))) for text in scope]

    def replace(match: re.Match) -> str:
        quote = match.group(1)
        if not alnum_tokens(quote) or _NUMERIC_QUOTE_RE.match(quote):
            return match.group(0)      # a quoted number/unit is a numeric claim
        report.quotes_checked += 1
        needle = _neutralise(collapse(fold(quote))).strip(" .…-–—'’")
        # An excerpt may be truncated with an ellipsis at either end.
        needle = re.sub(r"^(?:\.{3}|…)\s*|\s*(?:\.{3}|…)$", "", needle).strip()
        if any(needle and needle in haystack for haystack in haystacks):
            report.quotes_verified += 1
            return match.group(0)
        report.issues.append(
            Issue(
                "quote", "error",
                "quoted text does not appear verbatim in the cited source",
                detail=quote[:160], sentence=index,
                action="repaired" if repair else "flagged",
            )
        )
        return quote if repair else match.group(0)

    return QUOTE_RE.sub(replace, body)


def _verify_numbers(
    body: str,
    evidence: Evidence,
    report: ValidationReport,
    index: int,
    language: str,
    repair: bool,
) -> str:
    """Check every number/unit; splice out the ones the evidence cannot support."""
    allowed = evidence.numbers
    allowed_values = {value for value, _unit in allowed}
    edits: list[tuple[int, int, str]] = []

    for value, unit, start, end in iter_number_units(body):
        if _inside_citation(body, start):
            continue
        if _inside_locator(body, start):
            continue
        report.numbers_checked += 1
        if (value, unit) in allowed or (not unit and value in allowed_values):
            report.numbers_verified += 1
            continue
        hint = _explain_number(value, unit, evidence)
        report.issues.append(
            Issue(
                "numeric", "error",
                f"number {value}{(' ' + unit) if unit else ''} is not in the evidence"
                + (f" ({hint})" if hint else ""),
                detail=body[max(0, start - 30): end + 30].strip(),
                sentence=index,
                action="repaired" if repair else "flagged",
            )
        )
        if repair:
            edits.append((start, end, REDACTION.get(language, "some")))

    if not edits:
        return body
    repaired = body
    for start, end, replacement in sorted(edits, reverse=True):
        repaired = repaired[:start] + replacement + repaired[end:]
    repaired = _EMPTY_PARENS_RE.sub("", repaired)
    repaired = _EMPTY_SLASH_RE.sub("", repaired)
    return repaired


def _ensure_citations(
    body: str, evidence: Evidence, report: ValidationReport, repair: bool
) -> str:
    """Every claim needs a valid ``[n]``; attach the best ref or drop the claim."""
    sentences = split_sentences(body)
    claim_sentences = [s for s in sentences if _is_claim(s) and not REFERENCE_LINE_RE.match(s)]
    if not claim_sentences:
        return body

    appended: list[str] = []
    dropped: list[str] = []
    for sentence in claim_sentences:
        index = report.sentences_checked
        report.sentences_checked += 1
        refs = _refs_in(sentence)
        if refs:
            report.sentences_cited += 1
            for n in refs:
                if n not in report.refs_used:
                    report.refs_used.append(n)
            continue
        best = _best_ref(sentence, evidence)
        if best is None:
            report.issues.append(
                Issue(
                    "citation", "error",
                    "claim has no citation and nothing in the evidence supports it",
                    detail=sentence[:200], sentence=index,
                    action="dropped" if repair else "flagged",
                )
            )
            dropped.append(sentence)
            continue
        report.issues.append(
            Issue(
                "citation", "warning",
                f"claim was missing a citation; attached [{best.n}]",
                detail=sentence[:200], sentence=index,
                action="repaired" if repair else "flagged",
            )
        )
        report.sentences_cited += 1
        if best.n not in report.refs_used:
            report.refs_used.append(best.n)
        appended.append(f"{sentence.rstrip()} [{best.n}]")

    if not repair:
        return body
    if dropped:
        # Remove the ungrounded claim from its line (leaving the cited rest).
        repaired = body
        for sentence in dropped:
            repaired = repaired.replace(sentence.strip(), "", 1)
        repaired = re.sub(r"\s{2,}", " ", repaired).strip(" -;:")
        if not repaired:
            return ""
        return repaired
    if not appended:
        return body
    # Attach each missing marker to the sentence it belongs to, in place.
    repaired = body
    for marker_sentence in appended:
        sentence = marker_sentence.rsplit(" [", 1)[0]
        n = marker_sentence.rsplit("[", 1)[1].rstrip("]")
        target = sentence.rstrip()
        position = repaired.find(target)
        if position == -1:
            continue
        insert_at = position + len(target)
        # Keep a trailing quote/emphasis marker outside the citation.
        while insert_at < len(repaired) and repaired[insert_at] in "*_":
            insert_at += 1
        repaired = repaired[:insert_at] + f" [{n}]" + repaired[insert_at:]
    return repaired


def _strip_invalid_citations(
    body: str, known: set[int], report: ValidationReport, index: int
) -> str:
    def replace(match: re.Match) -> str:
        numbers = [int(n) for n in re.findall(r"\d+", match.group(0))]
        valid = [n for n in numbers if n in known]
        for n in numbers:
            if n not in known:
                report.issues.append(
                    Issue(
                        "citation", "error",
                        f"cited [{n}], which was not retrieved (evidence is {sorted(known)})",
                        sentence=index, action="dropped",
                    )
                )
        if not valid:
            return ""
        return "[" + ", ".join(str(n) for n in valid) + "]"

    return CITATION_RE.sub(replace, body)


def _check_reference_line(line: str, known: set[int], report: ValidationReport) -> None:
    for n in _numbers_in(line):
        if n not in known:
            report.issues.append(
                Issue(
                    "citation", "error",
                    f"reference list cites [{n}], which was not retrieved",
                    action="flagged",
                )
            )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
_QUOTE_FOLD = str.maketrans({char: "'" for char in '"„“”«»‘’‚'})


def _neutralise(text: str) -> str:
    """Make a passage comparable with a quotation of it.

    * citation markers are blanked -- an excerpt that quotes a paragraph still
      carrying our ``[n]`` markers must compare clean;
    * every quote character becomes a straight apostrophe, because the excerpt
      is wrapped in ``„…“`` / ``«…»`` and inner quotes are normalised by the
      formatter.
    """
    return CITATION_RE.sub(" ", text).translate(_QUOTE_FOLD)


def _inside_citation(body: str, offset: int) -> bool:
    return any(start <= offset < end for start, end, _ in citation_markers(body))


def _inside_locator(body: str, offset: int) -> bool:
    """True inside ``p. 40`` / ``§2.3`` -- a locator, not a claim."""
    from .textutil import LOCATOR_RE

    return any(match.start() <= offset < match.end() for match in LOCATOR_RE.finditer(body))


def _explain_number(value: str, unit: str, evidence: Evidence) -> str:
    """Human hint for a failed numeric check ("value found in [2] with unit V")."""
    for ref in evidence.refs:
        for ref_value, ref_unit, _start, _end in iter_number_units(ref.support):
            if ref_value == value and ref_unit != unit:
                shown = f"unit {ref_unit}" if ref_unit else "without a unit"
                return f"value appears in [{ref.n}] with {shown}"
    return ""


def _numbers_in(text: str) -> list[int]:
    return [n for _, _, numbers in citation_markers(text) for n in numbers]


def _refs_in(text: str) -> list[int]:
    return [n for _, _, numbers in citation_markers(text) for n in numbers]


def _is_claim(sentence: str) -> bool:
    """False for headings, list bullets and disclaimer lines."""
    stripped = sentence.strip()
    if not stripped or stripped.startswith("#"):
        return False
    # A fully bold line is a heading ("**Passages from the manuals**"), and a
    # bullet that ends in a colon is a lead-in -- neither states a fact.
    if stripped.startswith("**") and stripped.endswith("**") and stripped.count("**") == 2:
        return False
    body = stripped.lstrip("-*• ").strip()
    if not body:
        return False
    if body.endswith(":") and not _REF_INLINE_RE.search(body):
        return False
    lowered = fold(body)
    if lowered.startswith(_NON_CLAIM_STARTS):
        return False
    if any(marker in lowered for marker in _NON_CLAIM_CONTAINS):
        return False
    return bool(alnum_tokens(body))


def _best_ref(sentence: str, evidence: Evidence):
    """The evidence ref that best supports a sentence (token overlap).

    Two shared content words are required, and they must cover 30% of the
    sentence: one coincidental word ("robot" appears in every passage) is not
    support. Numeric literals count, so "it weighs 55 kg" attaches to the card
    that states 55 kg.
    """
    from retrieve.text import ALL_STOPWORDS

    words = {
        token
        for token in alnum_tokens(fold(sentence))
        if len(token) > 2 and token not in ALL_STOPWORDS
    }
    numbers = {value for value, _unit, _s, _e in iter_number_units(sentence)}
    tokens = words | numbers
    if not tokens:
        return None
    best = None
    best_score = 0.0
    for ref in evidence.refs:
        ref_words = {
            token
            for token in alnum_tokens(fold(ref.support + " " + ref.citation))
            if len(token) > 2 and token not in ALL_STOPWORDS
        }
        ref_numbers = {value for value, _unit, _s, _e in iter_number_units(ref.support)}
        shared = words & ref_words
        shared_numbers = numbers & ref_numbers
        # Two shared content words, or a shared measured value (a number is
        # strong evidence on its own: "it weighs 55 kg").
        if len(shared) < 2 and not shared_numbers:
            continue
        overlap = (len(shared) + 2 * len(shared_numbers)) / len(tokens)
        if overlap > best_score:
            best, best_score = ref, overlap
    return best if best_score >= 0.3 else None


def _tidy(text: str) -> str:
    """Cosmetic cleanup after repairs (double spaces, empty bullets)."""
    lines = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if re.fullmatch(r"\s*[-*•]\s*", stripped):
            continue
        if re.fullmatch(r"\s*", stripped):
            lines.append("")
            continue
        stripped = re.sub(r"[ \t]{2,}", " ", stripped)
        stripped = re.sub(r"\s+([,.;:])", r"\1", stripped)
        stripped = re.sub(r"\[\s*\]", "", stripped)
        lines.append(stripped)
    cleaned = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()
