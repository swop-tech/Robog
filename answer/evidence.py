"""Evidence: what the retrieved material actually says (the ground truth).

The composer writes an answer; the validator must then decide whether every
claim in it exists in the sources. Both need one canonical view of "the
evidence", so this module builds it once per request:

* ``refs``     -- numbered evidence items (``[1]``, ``[2]`` …) with the *exact*
  text they are allowed to support, plus their citation (manual, §section,
  page, language) and, for figures, the image path.
* ``numbers``  -- every number/unit pair that appears anywhere in the evidence,
  keyed so that translations of the same fact compare equal (``29,2 V`` in
  German == ``29.2 V`` in English).
* ``texts``    -- the searchable text blob per ref number, for the quote check.

References are assigned deterministically from the retrieval result:

1. the structured parameter rows (exact technician values, gap G1),
2. the product profile cards (advisory answers, gap G2),
3. the retrieved chunks, in rank order.

Why parameters come before chunks: the parameter row is the *authoritative*
value and usually renders as ``20 kg (10 kg pro Tablett)``, whereas the chunk
is the whole markdown table it came from. Putting the row first means the
composer can cite ``[1]`` for a clean sentence and the chunk keeps its own
number for context.

Usage:
    from answer.evidence import Evidence
    evidence = Evidence.from_result(result)
    evidence.numbers             # {"20 kg", "10 kg", ...} (normalized keys)
    evidence.ref(n=1).citation   # 'FlashBot User Manual V1.0, §2.3 …, p. 40, DE'
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

from retrieve.text import fold

#: The units that actually occur in this corpus (plus a few obvious neighbours).
#: Used to pair a number with the unit that follows it ("20 kg", "1.2 m/s").
UNITS: tuple[str, ...] = (
    # mass / length / area / volume
    "kg", "g", "mg", "t", "lb", "lbs",
    "mm", "cm", "m", "km", "in", "inch", "ft",
    "m2", "cm2", "mm2", "m²", "cm²", "mm²",
    "l", "ml", "cl", "dl", "gal",
    # time
    "s", "sec", "secs", "second", "seconds", "sekunde", "sekunden", "secondo", "secondi",
    "min", "mins", "minute", "minutes", "minuten", "minuto", "minuti",
    "h", "hr", "hrs", "hour", "hours", "stunde", "stunden", "ora", "ore",
    # speed / power / electrical
    "m/s", "km/h", "rpm", "mm/s",
    "v", "vac", "vdc", "mv", "kv",
    "a", "ah", "ma", "mah",
    "w", "kw", "wh", "kwh",
    "hz", "khz", "mhz", "ghz",
    "dbm", "db", "percent", "%",
    # environment
    "c", "°c", "f", "°f", "k",
    "rh", "%rh",
    # pressure / force / torque
    "pa", "kpa", "bar", "psi", "n", "nm",
    # data / angles
    "mb", "gb", "tb", "kb", "°",
    # misc counts / grades
    "kg/h", "l/min", "ip54", "ip55", "ip65", "ip67", "v1", "v1.0", "v2",
)

#: A number: ``20``, ``1.2``, ``29,2``, ``-20``, ``1 000``, ``4,5`` -- but not the
#: digits inside a word (``IP54`` handled separately, ``T300``/``V1.0`` are names).
NUMBER_RE = re.compile(r"(?<![\w.,/-])(-?\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?|-?\d+(?:[.,]\d+)?)(?![\w])")

#: A quoted fallback phrase around a number (``"20 kg"``, ``1,2 m/s``).
_CITATION_NUMBER_RE = re.compile(r"\[\s*\d+(?:\s*[,;]\s*\d+)*\s*\]")


def normalize_number(raw: str) -> str:
    """Canonical form of a numeric literal: ``"1 000,50"`` -> ``"1000.50"``."""
    text = raw.replace("\u00a0", "").replace(" ", "")
    text = text.replace(",", ".")
    # strip a trailing lone dot ("20." at the end of a sentence tabular cell)
    text = text.rstrip(".")
    if text.startswith("+"):
        text = text[1:]
    if text.startswith("-"):
        body = text[1:]
    else:
        body = text
    if "." in body:
        head, _, tail = body.partition(".")
        if tail and not head:
            body = "0." + tail
    else:
        body = str(int(body)) if body.isdigit() else body
    return f"-{body}" if text.startswith("-") else body


def numeric_values(text: str) -> set[str]:
    """Normalized numeric literals in a text (``{"20", "29.2"}``)."""
    return {normalize_number(m.group(1)) for m in NUMBER_RE.finditer(text or "")}


_UNITS_LONGEST_FIRST = tuple(sorted(UNITS, key=len, reverse=True))


def fold_aligned(text: str) -> str:
    """Lowercase + de-accent ``text`` **without changing its length**.

    ``retrieve.text.fold`` is the right normaliser for matching, but NFKD turns
    ``"à"`` into two characters before the accent is dropped, so offsets shift.
    Evidence validation splices replacements into the original answer, so it
    needs a fold that keeps every character at its original index.
    """
    out: list[str] = []
    for char in text:
        decomposed = unicodedata.normalize("NFKD", char)
        base = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
        out.append((base[:1] or char).lower())
    return "".join(out)


def iter_number_units(text: str) -> list[tuple[str, str, int, int]]:
    """``(value, unit, start, end)`` for every number/unit in ``text``.

    ``end`` covers the unit as well, so a caller can splice a replacement into
    the *original* string (offsets are computed on the folded text, which keeps
    the same character positions for ASCII digits and units).

    ``"20 kg (10 kg pro Tablett)"`` -> ``[("20", "kg", 0, 5), ("10", "kg", 11, 16)]``
    """
    folded = fold_aligned(text or "")
    found: list[tuple[str, str, int, int]] = []
    for match in NUMBER_RE.finditer(folded):
        value = normalize_number(match.group(1))
        tail = folded[match.end(): match.end() + 16]
        unit = ""
        unit_end = match.end()
        stripped = tail.lstrip()
        spaces = len(tail) - len(stripped)
        for candidate in _UNITS_LONGEST_FIRST:
            if not stripped.startswith(candidate):
                continue
            after = stripped[len(candidate): len(candidate) + 1]
            # "m" must not match the "m" of "mm" or of a following word
            if candidate[0].isalnum() and after and (after.isalnum() or after in "/"):
                continue
            unit = candidate
            unit_end = match.end() + spaces + len(candidate)
            break
        found.append((value, unit, match.start(), unit_end))
    return found


def number_unit_pairs(text: str) -> set[tuple[str, str]]:
    """``(value, unit)`` pairs: ``"20 kg (10 kg pro Tablett)"`` -> {("20","kg"), ...}.

    The unit is looked up immediately after the number (allowing one space), so
    ``"1.2 m/s"`` and ``"1,2 m/s"`` both yield ``("1.2", "m/s")``. Numbers
    without a unit are still collected by :func:`numeric_values`.
    """
    return {(value, unit) for value, unit, _start, _end in iter_number_units(text)}


@dataclass
class EvidenceRef:
    """One citable item: a parameter row, a profile card, or a chunk."""

    n: int
    kind: str                     # "param" | "profile" | "chunk"
    #: short display text (one spec list, one passage)
    text: str
    citation: str                 # human-readable locator
    language: str = ""
    product: str = ""
    chunk_id: str = ""
    image: str | None = None
    labels: list[str] = field(default_factory=list)
    page: int | None = None
    section: str = ""
    title: str = ""
    source: dict[str, Any] = field(default_factory=dict)
    #: everything this ref can support (defaults to ``text``). For a profile
    #: card that is the whole card -- specs *and* the use-case paragraph -- so
    #: the quote check accepts a sentence taken from either.
    support: str = ""

    @property
    def label(self) -> str:
        return f"[{self.n}]"

    def __post_init__(self) -> None:
        if not self.support:
            self.support = self.text

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "kind": self.kind,
            "text": self.text,
            "support": self.support,
            "citation": self.citation,
            "language": self.language,
            "product": self.product,
            "chunk_id": self.chunk_id,
            "image": self.image,
            "labels": self.labels,
            "page": self.page,
            "section": self.section,
            "title": self.title,
        }


@dataclass
class Evidence:
    """The citable, checkable universe for one question."""

    refs: list[EvidenceRef] = field(default_factory=list)
    language: str | None = None
    products: list[str] = field(default_factory=list)
    #: languages present in the evidence (for the cross-lingual note)
    languages: list[str] = field(default_factory=list)
    #: note from the retriever ("no IT manual for FlashBot (available: DE, EN)")
    notes: list[str] = field(default_factory=list)

    # -- construction ------------------------------------------------------
    @classmethod
    def from_result(cls, result: Any, *, max_chunks: int = 5, max_params: int = 3) -> "Evidence":
        """Turn a ``retrieve.engine.SearchResult`` into numbered evidence."""
        refs: list[EvidenceRef] = []
        seen_chunks: set[str] = set()

        for hit in list(getattr(result, "params", ()) or ())[:max_params]:
            record = getattr(hit, "record", {}) or {}
            parameter = record.get("parameter") or getattr(hit, "parameter", "")
            value = record.get("value") or getattr(hit, "value", "")
            if not parameter or not value:
                continue
            page = record.get("page") or record.get("pdf_page")
            section = str(record.get("section_number") or record.get("section") or "")
            refs.append(
                EvidenceRef(
                    n=len(refs) + 1,
                    kind="param",
                    text=f"{parameter}: {value}",
                    citation=_citation(
                        record.get("title", ""), section, page, record.get("language", "")
                    ),
                    language=record.get("language", ""),
                    product=record.get("product", ""),
                    chunk_id=record.get("chunk_id", "") or "",
                    page=page if isinstance(page, int) else None,
                    section=section,
                    title=record.get("title", ""),
                    source=record,
                )
            )
            if record.get("chunk_id"):
                seen_chunks.add(record["chunk_id"])

        for prof in list(getattr(result, "profiles", ()) or []):
            record = getattr(prof, "record", {}) or {}
            product = record.get("product", "")
            if not product:
                continue
            specs = _profile_specs(record)
            refs.append(
                EvidenceRef(
                    n=len(refs) + 1,
                    kind="profile",
                    text=" | ".join(specs) if specs else record.get("key_use_case", ""),
                    citation=record.get("citation", ""),
                    language=record.get("language", ""),
                    product=product,
                    chunk_id=record.get("overview_chunk_id", "") or "",
                    image=record.get("image"),
                    page=(record.get("pages") or [None])[0],
                    title=record.get("title", ""),
                    source=record,
                    support=" ".join(
                        part
                        for part in (
                            record.get("card_text", ""),
                            record.get("key_use_case", ""),
                            " | ".join(specs),
                        )
                        if part
                    ),
                )
            )

        for hit in list(getattr(result, "hits", ()) or []):
            if len(seen_chunks) >= max_chunks and hit.chunk_id in seen_chunks:
                # A chunk already cited through a parameter row keeps its own
                # ref only when it is one of the first `max_chunks` chunks.
                continue
            if hit.chunk_id in seen_chunks:
                continue
            meta = getattr(hit, "metadata", {}) or {}
            refs.append(
                EvidenceRef(
                    n=len(refs) + 1,
                    kind="chunk",
                    text=getattr(hit, "text", "") or "",
                    citation=getattr(hit, "citation", "") or "",
                    language=meta.get("language", ""),
                    product=meta.get("product", ""),
                    chunk_id=hit.chunk_id,
                    image=meta.get("image"),
                    labels=list(getattr(hit, "labels", ()) or ()),
                    page=meta.get("page") or meta.get("pdf_page"),
                    section=str(meta.get("section_number") or meta.get("section") or ""),
                    title=meta.get("title", ""),
                    source=meta,
                )
            )
            seen_chunks.add(hit.chunk_id)
            if sum(1 for r in refs if r.kind == "chunk") >= max_chunks:
                break

        languages: list[str] = []
        for ref in refs:
            if ref.language and ref.language not in languages:
                languages.append(ref.language)

        return cls(
            refs=refs,
            language=getattr(result, "language", None),
            products=list(getattr(result, "products", ()) or ()),
            languages=languages,
            notes=list(getattr(result, "notes", ()) or ()),
        )

    # -- queries -----------------------------------------------------------
    def __bool__(self) -> bool:
        return bool(self.refs)

    def __len__(self) -> int:
        return len(self.refs)

    def ref(self, n: int) -> EvidenceRef | None:
        for ref in self.refs:
            if ref.n == n:
                return ref
        return None

    @property
    def numbers(self) -> set[tuple[str, str]]:
        """Every ``(value, unit)`` pair stated by the evidence."""
        pairs: set[tuple[str, str]] = set()
        for ref in self.refs:
            pairs |= number_unit_pairs(ref.support)
            pairs |= number_unit_pairs(ref.citation)
        return pairs

    @property
    def values(self) -> set[str]:
        """Every bare numeric value in the evidence (unit-less membership test)."""
        values: set[str] = set()
        for ref in self.refs:
            values |= numeric_values(ref.support)
        return values

    def texts_for(self, numbers: Iterable[int]) -> list[str]:
        """The exact texts behind a set of citation numbers (unknown ones skipped)."""
        wanted = set(numbers)
        return [ref.support for ref in self.refs if ref.n in wanted]

    def bulk_text(self, numbers: Iterable[int] | None = None) -> str:
        """One searchable blob for quote verification (all refs when None)."""
        if numbers is None:
            return "\n".join(ref.support for ref in self.refs)
        return "\n".join(self.texts_for(numbers))

    def cross_lingual_languages(self) -> list[str]:
        """Evidence languages that differ from the question language."""
        if not self.language:
            return []
        return [lang for lang in self.languages if lang and lang != self.language]

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "products": self.products,
            "languages": self.languages,
            "refs": [ref.to_dict() for ref in self.refs],
        }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
_SPEC_LABELS = (
    ("payload", "payload"),
    ("trays_or_compartments", "compartments"),
    ("tank_or_dustbin", "tank"),
    ("dimensions", "dimensions"),
    ("weight", "weight"),
    ("battery", "battery"),
    ("runtime", "runtime"),
    ("charging_time", "charging"),
    ("speed", "speed"),
    ("min_passage_width", "min width"),
    ("cleaning_efficiency", "cleaning efficiency"),
)


def _profile_specs(record: dict) -> list[str]:
    specs = []
    for key, label in _SPEC_LABELS:
        value = record.get(key)
        if value:
            specs.append(f"{label}: {value}")
    return specs


def _citation(title: str, section: str, page: Any, language: str) -> str:
    parts = [title.strip()] if title else []
    if section:
        parts.append(f"§{section}")
    if page:
        parts.append(f"p. {page}")
    if language:
        parts.append(language.upper())
    return ", ".join(parts)
