"""Deterministic grounded formatter -- the answer composer with no model.

This is the fallback, and it is deliberately the *baseline* of the whole
answering phase: it must always work, must never invent a value, and must be
good enough that a live demo survives a dead Wi-Fi, an exhausted free tier or a
missing API key.

What it produces, in the question's language:

1. the exact parameter rows the retriever found (gap G1) -- quoted verbatim with
   the manual, §section and page;
2. the top cited passages, quoted as excerpts (the evidence, not a paraphrase);
3. for advisory questions, the product profile cards side by side;
4. for figure questions, the selected diagram with its label legend;
5. a note when the evidence had to come from another language (Bonus 02).

Every line carries ``[n]`` references into :class:`answer.evidence.Evidence`, so
the same validator that guards the LLM's prose also runs over this text -- the
formatting cannot smuggle in an unchecked number.

Usage:
    from answer.formatter import format_answer
    draft = format_answer(evidence)          # -> Draft(text, provider="offline")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from retrieve.lexicon import concepts_for
from retrieve.text import fold, tokenize

from .evidence import Evidence, EvidenceRef
from .strings import join_languages, language_name, text

#: Excerpt length limits (characters) -- long enough to be checkable, short
#: enough to stay readable in a chat bubble.
EXCERPT_MAX = 340
SUMMARY_MAX = 240

#: Words that end a sentence we are willing to cut at.
_CUT_CHARS = ".!?;"


@dataclass
class Draft:
    """An answer before validation: text + which source produced it."""

    text: str
    provider: str = "offline"
    model: str | None = None
    language: str = "en"
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def format_answer(
    evidence: Evidence,
    *,
    question: str = "",
    language: str | None = None,
    route: str = "default",
) -> Draft:
    """Compose a grounded, fully cited answer from evidence alone."""
    language = language or evidence.language or "en"
    if language not in ("en", "de", "it"):
        language = "en"
    notes: list[str] = []

    if not evidence.refs:
        return Draft(
            text=text("no_evidence", language),
            provider="offline",
            language=language,
            notes=["no evidence retrieved"],
        )

    blocks: list[str] = []
    question_tokens = _question_tokens(question)
    # A parameter row answers a parameter question. When the question is a
    # procedure ("how do I switch it on"), a stray "stereo power 10 W" row is
    # not an answer -- keep only rows whose parameter name relates to the
    # question (literal token or lexicon concept).
    matched_params = [ref for ref in evidence.refs if ref.kind == "param"]
    params = _rank_params(
        [ref for ref in matched_params if _param_matches(ref, question_tokens)],
        question_tokens,
        limit=2 if route == "lookup" else 1,
    )
    profiles = [ref for ref in evidence.refs if ref.kind == "profile"]
    chunks = [ref for ref in evidence.refs if ref.kind == "chunk"]
    if matched_params and not params:
        # No parameter row answers this question: a value from an unrelated row
        # would be noise, and its chunk belongs to the excerpt block instead.
        chunks = [ref for ref in chunks if ref.n not in {p.n for p in matched_params}]
    chunks = _prefer_text(chunks)

    # An advisory question wants the shortlist first; a spec question wants the
    # exact value first. Order the blocks accordingly instead of always leading
    # with whatever the retriever happened to rank highest.
    if profiles and route == "advisory":
        blocks.append(_profile_block(profiles, language))
        if params:
            blocks.append(_parameter_block(params, language))
    else:
        if params:
            blocks.append(_parameter_block(params, language))
        if profiles:
            blocks.append(_profile_block(profiles, language))

    figures = [ref for ref in chunks if _is_figure(ref)]
    if route == "figure" and figures:
        blocks.append(_figure_block(figures, language))
        chunks = [ref for ref in chunks if not _is_figure(ref)]

    if route == "advisory" and profiles:
        # The profile cards *are* the comparison; extra overview excerpts would
        # only repeat the same paragraphs in a longer form.
        chunks = []
    # Boilerplate (copyright notices, warranty legalese, part lists) is real
    # corpus text but never an answer: it is retrievable, not citable as substance.
    chunks = [ref for ref in chunks if not _is_boilerplate(ref, question_tokens)]
    if chunks:
        # When a parameter row already answered the question, extra passages are
        # only useful if they literally mention the thing that was asked about.
        blocks.append(
            _excerpt_block(chunks, language, question, strict=bool(params))
        )

    used = {int(n) for n in re.findall(r"\[(\d+)\]", "\n".join(blocks))}
    note = _language_note(evidence, language, used=used)
    if note:
        blocks.append(note)
        notes.append("cross-lingual evidence disclosed")

    body = "\n\n".join(block for block in blocks if block.strip())
    if not body.strip():
        body = text("no_evidence", language)
    return Draft(text=body, provider="offline", language=language, notes=notes)


# --------------------------------------------------------------------------
# blocks
# --------------------------------------------------------------------------
def _parameter_block(params: list[EvidenceRef], language: str) -> str:
    lines: list[str] = []
    for ref in params:
        parameter, _, value = _split_parameter(ref.text)
        if not parameter or not value:
            continue
        key = "param_lead" if ref.product else "param_lead_no_product"
        lines.append(
            text(key, language, parameter=parameter, product=ref.product, value=value, ref=ref.n)
        )
    return "\n".join(lines)


def _profile_block(profiles: list[EvidenceRef], language: str) -> str:
    """One bullet per robot: decisive specs, then what it is for -- one citation.

    A comparison answer is read side by side, so the format is deliberately
    narrow: robot, category, the specs that decide the choice, one sentence of
    intended use, and the card it all came from.
    """
    lines = [text("profiles_header", language)]
    for ref in profiles:
        lines.append(
            text(
                "profile_line",
                language,
                product=ref.product or "—",
                category=_category_label(ref.source.get("category", ""), language),
                specs=(_short_specs(ref.source) or "—").rstrip(" ,;"),
                summary=_first_sentence(ref.source.get("key_use_case", ""), SUMMARY_MAX),
                ref=ref.n,
            )
        )
    return "\n".join(lines)


def _figure_block(figures: list[EvidenceRef], language: str) -> str:
    """Name the selected diagram, its page, and the numbered labels in it."""
    ref = figures[0]
    line = text(
        "figure_note",
        language,
        title=_figure_title(ref, language),
        citation=ref.citation,
        ref=ref.n,
    )
    if ref.labels:
        labels = "; ".join(ref.labels[:12])
        line += "\n  - " + text("figure_labels", language, labels=labels, ref=ref.n)
    return line


def _figure_title(ref: EvidenceRef, language: str) -> str:
    """A human name for a figure: "§2.2 Appearance and Components, p. 38"."""
    parts = []
    if ref.section:
        parts.append(f"§{ref.section}")
    if ref.page:
        parts.append(text("page_label", language, page=ref.page))
    return ", ".join(parts) or (ref.title or "figure")


_ON_MARKERS_RE = re.compile(r"einschalt|power on|accension|\bon\b", re.IGNORECASE)
_OFF_MARKERS_RE = re.compile(r"ausschalt|power off|spegniment|\boff\b", re.IGNORECASE)


#: Words that settle a power question's direction (the lexicon's "switch"
#: carries both concepts, so it cannot decide on its own).
_POWER_ON_WORDS = frozenset("on ein einzuschalten einschalten accendere accend start starten".split())
_POWER_OFF_WORDS = frozenset("off aus auszuschalten ausschalten spegnere spegn shut down".split())


def _power_direction(tokens: set[str]) -> str | None:
    """Which way a power question points: 'on', 'off', or None."""
    on = bool(tokens & _POWER_ON_WORDS)
    off = bool(tokens & _POWER_OFF_WORDS)
    if on and not off:
        return "on"
    if off and not on:
        return "off"
    concepts = {concept for token in tokens for concept in concepts_for(token)}
    if "power_on" in concepts and "power_off" not in concepts:
        return "on"
    if "power_off" in concepts and "power_on" not in concepts:
        return "off"
    return None


def _excerpt_block(
    chunks: list[EvidenceRef], language: str, question: str = "", *, strict: bool = False
) -> str:
    """Quote the passages that actually relate to the question, best first.

    Retrieval always returns *something* -- an unknown question drifts to
    whatever shares a stopword. Ranking the excerpts by how many question words
    they contain (and dropping the ones that share none) is what turns that into
    "the manual does not cover this" instead of an unrelated paragraph.
    """
    ranked = _rank_by_question(chunks, question, strict=strict)
    title_tokens = _question_tokens(question)
    if title_tokens:
        # Within the relevant passages, the one whose *section title* names the
        # topic ("Power off:") is the procedure -- rank it above an incidental
        # mention inside another section.
        ranked = sorted(
            ranked,
            key=lambda ref: (
                -_relevance_score(EvidenceRef(
                    n=ref.n, kind=ref.kind, text=ref.text, citation=ref.citation,
                    support=ref.section or "",
                ), title_tokens),
                -_relevance_score(ref, title_tokens),
                ranked.index(ref),
            ),
        )
    lines = [text("excerpts_header", language)]
    for ref in ranked[:4]:
        snippet = _excerpt(ref, language)
        if not snippet:
            continue
        if _is_table(ref):
            # A table flattened into "row: value; row: value" is a faithful
            # restatement, not a quotation -- the cell order inside the markdown
            # was already changed at ingestion, so it must not be quoted.
            lines.append(text("excerpt_plain", language, snippet=snippet, ref=ref.n))
        else:
            lines.append(text("excerpt", language, snippet=snippet, ref=ref.n))
    return "\n".join(lines) if len(lines) > 1 else ""


_QUESTION_STOPWORDS = frozenset(
    """
    the and for with that this what which how does can could would should are was were
    der die das und oder mit von für fur ist sind wie was welche welcher kann muss soll
    il lo la gli le di da con per che come quale quali cosa sono può puo deve
    robot robots roboter manual manuals handbuch pudu pudubot
    dell sull nell all dall coll dell' delle degli della dello nel nella negli
    des dem den ein eine einer eines und ist sind mit von zu zum zur bei
    """.split()
)


def _rank_by_question(
    chunks: list[EvidenceRef], question: str, *, strict: bool = False
) -> list[EvidenceRef]:
    """Keep the passages that share a word *or a domain concept* with the question.

    The concept part is what bridges colloquial questions to manual wording:
    "Wie schalte ich den FlashBot ein?" shares no word with "Halten Sie die
    Netztaste 0,5 s gedrückt", but both are the ``power_on`` concept.
    """
    tokens = {
        token
        for token in tokenize(question)
        if token not in _QUESTION_STOPWORDS and len(token) > 2
    }
    if not tokens:
        return chunks
    concepts = {concept for token in tokens for concept in concepts_for(token)}
    wanted_power = _power_direction(tokens)
    scored = []
    for position, ref in enumerate(chunks):
        body = (ref.support or "")[:2000]
        body_tokens = set(tokenize(body))
        body_concepts = {concept for token in body_tokens for concept in concepts_for(token)}
        # A literal word match is worth more than a concept match.
        overlap = 3 * len(tokens & body_tokens) + len(concepts & body_concepts)
        # "How do I switch it ON" must not be answered by the power-OFF section.
        if wanted_power:
            section = fold(ref.section or "")
            if wanted_power == "on" and _OFF_MARKERS_RE.search(section):
                overlap -= 2
            elif wanted_power == "off" and _ON_MARKERS_RE.search(section):
                overlap -= 2
        scored.append((overlap, position, ref))
    scored.sort(key=lambda item: (-item[0], item[1]))
    relevant = [ref for overlap, _position, ref in scored if overlap > 0]
    if strict and relevant:
        # Concepts alone can drag in a safety notice; require a real word match.
        literal = [
            ref for ref in relevant if tokens & set(tokenize((ref.support or "")[:2000]))
        ]
        return literal
    return relevant


def _relevance_score(ref: EvidenceRef, question_tokens: set[str]) -> float:
    """How much of the question a passage covers (words + concepts), for ranking."""
    if not question_tokens:
        return 0.0
    body = (ref.support or "")[:2000]
    body_tokens = set(tokenize(body))
    overlap = 3 * len(question_tokens & body_tokens)
    concepts = {concept for token in question_tokens for concept in concepts_for(token)}
    body_concepts = {concept for token in body_tokens for concept in concepts_for(token)}
    return float(overlap + len(concepts & body_concepts))



def _language_note(evidence: Evidence, language: str, used: set[int] | None = None) -> str:
    """Bonus 02: say it out loud when the evidence is in another language."""
    foreign = evidence.cross_lingual_languages()
    if not foreign:
        return ""
    foreign_refs = [
        ref
        for ref in evidence.refs
        if ref.language and ref.language != language and ref.language in foreign
    ]
    # Cite the cross-lingual refs the answer already uses; otherwise the top one.
    cited = [ref.n for ref in foreign_refs if used and ref.n in used]
    refs_arg = ", ".join(str(n) for n in (cited or [ref.n for ref in foreign_refs[:2]])) or "1"
    languages = join_languages(foreign, in_language=language)
    missing = _missing_language_products(evidence, language)
    if missing:
        return text(
            "no_own_language_note",
            language,
            lang_name=language_name(language, in_language=language),
            product=missing,
            languages=languages,
            refs=refs_arg,
        )
    return text("cross_lingual_note", language, languages=languages, refs=refs_arg)


def _missing_language_products(evidence: Evidence, language: str) -> str:
    """Products that have no manual in the question's language (from retriever notes)."""
    mismatch = language_name(language).upper()
    for note in evidence.notes:
        lowered = note.lower()
        if f"no {mismatch.lower()} manual for" in lowered or "no it manual for" in lowered:
            start = lowered.find("for ") + 4
            end = lowered.find(" (available")
            product = note[start:end] if end > start else note[start:]
            if product.strip():
                return product.strip()
    products = [p for p in evidence.products if p]
    return ", ".join(products)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _question_tokens(question: str) -> set[str]:
    return {
        token
        for token in tokenize(question)
        if token not in _QUESTION_STOPWORDS and len(token) > 2
    }


_POWER_ACTION_CONCEPTS = frozenset({"power_on", "power_off", "emergency_stop"})

_NUMERIC_PARAM_RE = re.compile(r"\d")


def _rank_params(
    params: list[EvidenceRef], question_tokens: set[str], *, limit: int = 2
) -> list[EvidenceRef]:
    """Best parameter rows first: shared words, then rows that state a value.

    A troubleshooting row ("Poor water recovery: worn squeegee blades") is a
    parameter-shaped record with no measurement -- it must not outrank
    "Solution tank capacity: 4 L" when the question asks for a capacity.
    """
    question_concepts = {
        concept for token in question_tokens for concept in concepts_for(token)
    } - _POWER_ACTION_CONCEPTS

    def score(ref: EvidenceRef) -> tuple[int, int, int]:
        name_tokens = set(tokenize(ref.text.split(":", 1)[0]))
        name_concepts = {concept for token in name_tokens for concept in concepts_for(token)}
        shared = len(name_tokens & question_tokens)
        shared += len(name_concepts & question_concepts)
        has_number = 1 if _NUMERIC_PARAM_RE.search(ref.text.split(":", 1)[-1]) else 0
        return (-shared, -has_number, ref.n)

    ranked = sorted(params, key=score)
    if not ranked:
        return []
    if not _NUMERIC_PARAM_RE.search(ranked[0].text):
        # Nothing measurable matched: prefer the procedure text over a row.
        return []
    return ranked[:limit]


def _param_matches(ref: EvidenceRef, question_tokens: set[str]) -> bool:
    """Does a parameter row answer the question? Literal or lexicon match.

    Deliberately literal (a shared token, or a shared domain *concept* such as
    payload/Zuladung/carico): a chatty synonym expansion would let "switch on"
    match "stereo power", which is exactly the off-topic row to avoid.
    """
    if not question_tokens:
        return True
    name_tokens = set(tokenize(ref.text.split(":", 1)[0]))
    if name_tokens & question_tokens:
        return True
    name_concepts = {concept for token in name_tokens for concept in concepts_for(token)}
    # Never match through the power_on/power_off switch vocabulary: "how do I
    # switch it on" must not surface "stereo power" (a spec-table neighbour).
    name_concepts -= _POWER_ACTION_CONCEPTS
    question_concepts = {
        concept
        for token in question_tokens
        for concept in concepts_for(token)
        if concept not in _POWER_ACTION_CONCEPTS
    }
    return bool(name_concepts & question_concepts)


def _split_parameter(line: str) -> tuple[str, str, str]:
    if ":" not in line:
        return "", "", ""
    parameter, _, value = line.partition(":")
    return parameter.strip(), ":", value.strip()


#: Phrases that only ever appear in the legal preamble of a manual.
_BOILERPLATE_MARKERS = (
    # EN
    "all rights reserved",
    "may not be copied",
    "no organization or individual",
    "without the express written",
    "without prior written",
    "for reference only and are subject to change",
    "warranty service",
    # DE
    "alle rechte vorbehalten",
    "ohne ausdrueckliche schriftliche",
    "ohne ausdrückliche schriftliche",
    "ohne vorherige schriftliche",
    "zustimmung von shenzhen pudu",
    "nur als referenz",
    "garantieleistungen",
    # IT
    "tutti i diritti riservati",
    "senza il consenso scritto",
    "senza previa autorizzazione",
    "approvazione scritta di shenzhen pudu",
    "solo come riferimento",
    "servizio di garanzia",
)

#: Topics where the legal text *is* the answer (so it must not be filtered).
_WARRANTY_TOPICS = frozenset(
    "warranty garantie garanzia legal recht licence license lizenz copyright urheberrecht".split()
)


def _is_boilerplate(ref: EvidenceRef, question_tokens: set[str] | None = None) -> bool:
    """Copyright / warranty notices that precede nearly every manual."""
    if question_tokens and question_tokens & _WARRANTY_TOPICS:
        return False
    body = fold((ref.support or "")[:1500])
    return any(marker in body for marker in _BOILERPLATE_MARKERS)


def _prefer_text(chunks: list[EvidenceRef]) -> list[EvidenceRef]:
    """Procedures live in prose; figures/tables only illustrate them.

    When a question is answered by body text, the excerpt block shows text
    chunks first (figures keep their own block, tables stay for spec questions).
    """
    def kind_rank(ref: EvidenceRef) -> int:
        chunk_type = ref.source.get("chunk_type")
        if ref.image or chunk_type == "figure":
            return 2
        if chunk_type == "table":
            return 1
        return 0

    return sorted(chunks, key=lambda ref: (kind_rank(ref), chunks.index(ref)))


def _is_table(ref: EvidenceRef) -> bool:
    return ref.source.get("chunk_type") == "table" or (ref.text or "").lstrip().startswith("|")


def _is_figure(ref: EvidenceRef) -> bool:
    if ref.kind != "chunk":
        return False
    return ref.source.get("chunk_type") == "figure" or bool(ref.image)


def _short_specs(record: dict) -> str:
    wanted = (
        ("payload", "payload"),
        ("weight", "weight"),
        ("dimensions", "dimensions"),
        ("battery_runtime", "battery"),
        ("speed", "speed"),
        ("min_passage_width", "min width"),
    )
    parts = [f"{label} {record[key]}" for key, label in wanted if record.get(key)]
    return ", ".join(parts[:4])


_CATEGORY = {
    "en": {
        "delivery": "delivery robot",
        "cleaning": "cleaning robot",
        "industrial": "industrial transport robot",
        "disinfection": "disinfection robot",
    },
    "de": {
        "delivery": "Servierroboter",
        "cleaning": "Reinigungsroboter",
        "industrial": "Industrietransportroboter",
        "disinfection": "Desinfektionsroboter",
    },
    "it": {
        "delivery": "robot di servizio",
        "cleaning": "robot di pulizia",
        "industrial": "robot per trasporto industriale",
        "disinfection": "robot di disinfezione",
    },
}


def _category_label(category: str, language: str) -> str:
    if not category:
        return "—"
    table = _CATEGORY.get(language) or _CATEGORY["en"]
    return table.get(category, category)


def _first_sentence(value: str, limit: int) -> str:
    value = " ".join((value or "").split())
    if not value:
        return ""
    for index, char in enumerate(value):
        if char in _CUT_CHARS and index >= 60:
            return value[: index + 1].strip()
    return value[:limit].strip() + ("…" if len(value) > limit else "")


def _excerpt(ref: EvidenceRef, language: str) -> str:
    """A quotable excerpt of a chunk: drop the ``Labels:`` legend and table pipes.

    Tables are rendered as ``row: value`` lines instead of raw markdown so the
    excerpt reads like a sentence a technician can check. The words themselves
    are never altered -- they are quoted by the formatter and verified later.
    """
    body = ref.text or ""
    if "\nLabels:" in body:
        body = body.split("\nLabels:", 1)[0]
    body = body.replace("<br>", " ").replace("<br/>", " ")
    if body.lstrip().startswith("|"):
        rows = []
        for line in body.splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not cells or all(set(cell) <= set("-: ") for cell in cells if cell):
                continue
            if len(cells) >= 2 and cells[0]:
                rows.append(f"{cells[0]}: {', '.join(cell for cell in cells[1:] if cell)}")
            elif cells:
                rows.append(cells[0])
        body = "; ".join(rows)
    # Reassemble wrapped lines, then pick a sentence-ish window.
    body = " ".join(body.split())
    body = _strip_quote_chars(body)
    if not body:
        return ""
    if len(body) <= EXCERPT_MAX:
        return _quote_safe(body)
    cut = body.rfind(". ", 0, EXCERPT_MAX)
    if cut < EXCERPT_MAX // 2:
        cut = body.rfind(" ", 0, EXCERPT_MAX)
    snippet = body[:cut].rstrip(" ,;:") if cut > 0 else body[:EXCERPT_MAX]
    snippet = snippet.rstrip(" ,;:")
    return _quote_safe(snippet + " …")


_QUOTE_LIKE = ('"', "„", "“", "”", "«", "»", "‘", "’", "‚")


def _strip_quote_chars(value: str) -> str:
    """Normalise quote characters so a quoted excerpt survives its own quoting.

    German „…“ and Italian «…» wrap the excerpt, so any quote character inside
    the source text becomes a straight apostrophe and the validator can compare
    the excerpt with the cleaned source text.
    """
    for char in _QUOTE_LIKE:
        value = value.replace(char, "'")
    return value


def _quote_safe(value: str) -> str:
    """Remove characters that would break the „…“ / «…» quoting of an excerpt."""
    return _strip_quote_chars(value).strip()
