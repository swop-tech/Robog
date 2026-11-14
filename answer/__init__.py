"""Grounded answer composition + evidence validation (phase 3, gap G5, Bonus 02).

    question -> retrieve -> evidence -> generate -> validate -> answer

The public surface is small on purpose:

* :class:`~answer.composer.AnswerComposer` -- ``engine`` + pluggable generator,
  returns a validated :class:`~answer.composer.Answer`;
* :class:`~answer.evidence.Evidence` -- what the retrieved material actually
  says, numbered so every claim can point at it;
* :func:`~answer.validator.validate_answer` -- the three deterministic checks
  (citation, numeric, quote) that turn "faithful" from a claim into a mechanism;
* :func:`~answer.formatter.format_answer` -- the offline, deterministic answer
  that makes the whole phase run with no API key.

Typical use::

    from retrieve.engine import SearchEngine
    from answer import AnswerComposer

    composer = AnswerComposer(SearchEngine.from_build())
    answer = composer.answer("Wieviel Zuladung hat der FlashBot?")
    print(answer.text, answer.validation.ok)
"""

from .composer import Answer, AnswerComposer
from .evidence import Evidence, EvidenceRef
from .formatter import Draft, format_answer
from .llm import GeminiGenerator, OfflineGenerator, build_generator
from .validator import Issue, ValidationReport, validate_answer

__all__ = [
    "Answer",
    "AnswerComposer",
    "Draft",
    "Evidence",
    "EvidenceRef",
    "GeminiGenerator",
    "Issue",
    "OfflineGenerator",
    "ValidationReport",
    "build_generator",
    "format_answer",
    "validate_answer",
]
