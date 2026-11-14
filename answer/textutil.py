"""Small text helpers the evidence validator needs (no dependencies).

Two jobs:

* ``split_sentences`` -- cut an answer into claim-sized units so the citation
  check can ask "does *this* sentence carry a source reference?" without being
  fooled by German/Italian abbreviations (``z. B.``, ``p. 40``, ``ecc.``).
* ``blank_non_claims`` -- blank the regions of an answer that contain numbers
  which are *not* factual claims: the ``[1, 2]`` citation markers, the
  ``§2.3`` / ``p. 40`` locators inside a reference, and reference list lines.
  Numeric verification runs on the blanked text, otherwise every page number
  would be reported as an "unverified number".
"""

from __future__ import annotations

import re

#: ``[1]``, ``[1, 2]``, ``[1] [3]`` -- the only citation syntax we allow.
CITATION_RE = re.compile(r"\[\s*\d+(?:\s*[,;]\s*\d+)*\s*\]")

#: A reference-list line (``[1] FlashBot User Manual, §2.3, p. 40, DE``).
REFERENCE_LINE_RE = re.compile(r"^\s*\[\d+\]\s")

#: Locators that carry numbers but make no factual claim.
LOCATOR_RE = re.compile(
    r"(?:\b(?:p|pp|page|pages|seite|seiten|pagina|pagine|s|vgl|siehe|see|fig|figure|abbildung|abb)\.\s*\d+(?:\s*[–-]\s*\d+)?)"
    r"|(?:§\s*[\d.]+)",
    re.IGNORECASE,
)

#: Abbreviations that must not end a sentence.
_ABBREVIATIONS = frozenset(
    """
    z b e g i ca bzw etc usw vgl siehe s nr no art abs mind max min approx fig
    p pp page seite seiten pagina pagine vgl ecc es pt sr dr prof
    mwst inkl exkl ggf sog u a d h
    """.split()
)

#: A sentence starts after one of these (and an optional closing quote).
_BOUNDARY_RE = re.compile(r"(?<=[.!?])[\"'“”«»)\]]*\s+")


def split_sentences(text: str) -> list[str]:
    """Split an answer into sentences, keeping list items separate.

    Splitting is line-based first (answers are markdown), then on sentence
    punctuation, and it refuses to split after ``z.``/``p.``/``bzw.`` -- and
    inside a quotation, where a period is usually part of the excerpt rather
    than the end of a claim.
    """
    sentences: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("|", "[", "-", "*", "#", ">")) and len(stripped) < 4:
            continue
        for candidate in _split_line(stripped):
            candidate = candidate.strip()
            if candidate:
                sentences.append(candidate)
    return sentences


_QUOTE_CHARS = "\"„“«»"


def _inside_quote(line: str, offset: int) -> bool:
    """True when ``offset`` sits between an opening and a closing quote."""
    depth = sum(1 for char in line[:offset] if char in _QUOTE_CHARS)
    return depth % 2 == 1


def _split_line(line: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for match in _BOUNDARY_RE.finditer(line):
        head = line[start:match.start()].rstrip()
        if not head:
            continue
        last_word = re.split(r"[\s(\[]", head)[-1].lower().strip(".,;:!?)\"")
        if last_word in _ABBREVIATIONS or len(last_word) == 1:
            continue
        if _inside_quote(line, match.start()):
            continue
        # "Max. moving speed", "z. B. der Akku", "e.g. the battery": a period
        # followed by a lowercase word is an abbreviation, not a sentence end.
        following = line[match.end(): match.end() + 1]
        if following and following.islower():
            continue
        parts.append(head)
        start = match.end()
    tail = line[start:].strip()
    if tail:
        parts.append(tail)
    return _merge_marker_fragments(parts) or [line]


_MARKER_ONLY_RE = re.compile(r"^[\s\[\d,;\]]+$")


def _merge_marker_fragments(parts: list[str]) -> list[str]:
    """``["… functions.", "[1]"]`` -> ``["… functions. [1]"]``.

    A citation marker separated from its sentence by the splitter is part of
    that sentence; keeping it apart would make the claim look uncited.
    """
    merged: list[str] = []
    for part in parts:
        if merged and _MARKER_ONLY_RE.match(part) and "[" in part:
            merged[-1] = f"{merged[-1]} {part.strip()}"
        else:
            merged.append(part)
    return merged


def blank_non_claims(text: str) -> str:
    """Return ``text`` with citation markers and locators replaced by spaces.

    Character positions are preserved (every match is replaced by the same
    number of spaces), so offsets found on the blanked text still point at the
    same place in the original answer -- which is what the stripper needs.
    """
    blanked = []
    for line in text.splitlines():
        if REFERENCE_LINE_RE.match(line):
            blanked.append(" " * len(line))
        else:
            blanked.append(line)
    result = "\n".join(blanked)
    result = CITATION_RE.sub(lambda m: " " * len(m.group(0)), result)
    result = LOCATOR_RE.sub(lambda m: " " * len(m.group(0)), result)
    return result


def citation_markers(text: str) -> list[tuple[int, int, list[int]]]:
    """All ``[n]`` markers as ``(start, end, ref_numbers)``."""
    found = []
    for match in CITATION_RE.finditer(text):
        numbers = [int(n) for n in re.findall(r"\d+", match.group(0))]
        found.append((match.start(), match.end(), numbers))
    return found


def cited_refs(text: str) -> list[int]:
    """Referenced numbers in order of first appearance."""
    order: list[int] = []
    for _, _, numbers in citation_markers(text):
        for number in numbers:
            if number not in order:
                order.append(number)
    return order


def collapse(text: str) -> str:
    """Collapse whitespace -- quotes and OCR'd line breaks rarely agree exactly."""
    return re.sub(r"\s+", " ", text).strip()


def alnum_tokens(text: str) -> list[str]:
    """Word-ish tokens, for deciding whether a sentence makes a claim."""
    return re.findall(r"[^\W\d_]+", text, flags=re.UNICODE)
