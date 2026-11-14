"""Query routing -- the right technique per question type (gaps G2 & G6).

Four question shapes, four retrieval strategies:

* **figure** ("Show me a picture of the BellaBot", "Zeig mir ein Bild...",
  "Mostrami una foto...") -> ``chunk_type="figure"`` chunks, preferring
  overview/appearance diagrams and respecting the language policy because
  callout labels are rendered into the PNG pixels (gap G6).
* **advisory** ("Which robot fits my restaurant?") -> structured product
  profile cards across all 13 robots + diversified overview/spec chunks so the
  answer can compare (gap G2).
* **lookup** ("What is the payload of the FlashBot?") -> parameter index first,
  BM25 second, quotes values verbatim (gap G1).
* **default** -> ordinary hybrid search.

The router is deterministic and cheap; an LLM router would add latency, cost
and non-determinism for a decision simple linguistic cues already get right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text import fold, numbers, tokenize

# -- visual / picture cues (gap G6) ----------------------------------------
_FIGURE_CUES = {
    "en": (
        "show me a picture", "show a picture", "show me pictures",
        "picture of", "pictures of",
        "show me a photo", "show a photo", "photo of", "photos of",
        "show me an image", "show an image", "image of", "images of",
        "show me a diagram", "show me the diagram", "show a diagram", "diagram of",
        "component diagram", "parts diagram", "labeled diagram",
        "which figure", "what figure", "in which figure",
        "show me the figure", "show the figure", "figure of",
        "appearance of",
    ),
    "de": (
        "zeig mir ein bild", "zeigen sie mir ein bild", "zeig ein bild", "zeig mir das bild",
        "bild von", "bild vom", "bild des", "bild der", "bilder von", "bilder vom",
        "zeig mir ein foto", "zeigen sie mir ein foto",
        "foto von", "foto vom", "foto des", "foto der",
        "in welcher abbildung", "welche abbildung",
        "zeig mir die abbildung", "zeigen sie mir die abbildung",
        "abbildung von", "abbildung vom", "abbildung des", "abbildung der",
        "komponentendiagramm", "ubersichtszeichnung", "aussehen von", "aussehen des",
    ),
    "it": (
        "mostrami una foto", "mostra una foto", "fammi vedere una foto", "vedere una foto",
        "foto di", "foto del", "foto della", "foto dello", "foto dei", "foto delle", "foto dell",
        "mostrami un immagine", "mostrami una immagine", "fammi vedere un immagine",
        "immagine di", "immagine del", "immagine della", "immagine dello", "immagine dell",
        "in quale figura", "quale figura", "mostrami la figura",
        "figura di", "figura del", "figura della",
        "diagramma dei componenti", "schema dei componenti",
        "che aspetto ha", "come appare",
    ),
}

_FIGURE_PATTERNS = {
    "en": (
        re.compile(r"\b(?:what|how)\s+does\b.*\blook(?:\s+like)?\b"),
        re.compile(r"\b(?:show|display|see)\b.*\b(?:picture|photo|image|diagram|figure)s?\b"),
    ),
    "de": (
        re.compile(r"\bwie\s+sieht\b.*\baus\b"),
        re.compile(r"\b(?:zeig\w*|sehen)\b.*\b(?:bild\w*|foto\w*|abbildung\w*|diagramm\w*)\b"),
    ),
    "it": (
        re.compile(r"\bcom\s*e\s+fatto\b"),
        re.compile(r"\bcome\s+e\s+fatto\b"),
        re.compile(r"\b(?:mostr\w*|veder\w*)\b.*\b(?:foto|immagin\w*|figur\w*|diagramm\w*|schem\w*)\b"),
    ),
}

# -- advisory / comparison cues (gap G2) -----------------------------------
_ADVISORY_CUES = {
    "en": (
        "which robot", "which one", "best for", "best suited", "recommend",
        "suggest", "compare", "comparison", "difference between", "vs", "versus",
        "should i choose", "what should i buy", "fits my", "fits a", "suitable",
        "ideal for", "right robot",
    ),
    "de": (
        "welcher roboter", "welches modell", "welche roboter", "empfehl",
        "empfiehl", "vergleich", "unterschied zwischen", "eignet sich",
        "passt zu", "passend fur", "sollte ich", "welches gerat", "besser fur",
    ),
    "it": (
        "quale robot", "quale modello", "consigli", "consiglio", "confronto",
        "differenza tra", "differenze tra", "adatto", "adatta", "meglio per",
        "quale scegliere", "conviene", "ideale per",
    ),
}

# -- exact lookup cues (gap G1) --------------------------------------------
_LOOKUP_CUES = {
    "en": (
        "what is the", "how much", "how many", "value of", "weight", "voltage",
        "capacity", "dimension", "payload", "battery life", "runtime", "spec",
        "specification", "power output", "temperature range", "size of",
    ),
    "de": (
        "wie viel", "wieviel", "wie hoch", "wie lang", "wie gross",
        "wert von", "gewicht", "spannung", "kapazitat", "abmessung",
        "masse", "zuladung", "nutzlast", "akkulaufzeit", "technische daten",
    ),
    "it": (
        "quanto", "quanti", "qual e il valore", "peso", "tensione",
        "capacita", "dimensioni", "carico", "autonomia", "scheda tecnica",
        "dati tecnici", "potenza",
    ),
}

# "29.2 V", "20 kg", "1.2 m/s" -- a value quoted in the question.
_VALUE_IN_QUERY_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:kg|g|mm|cm|m/s|m|v|a|w|hz|%|ah|wh|°c|℃|h|min|s)\b",
    re.IGNORECASE,
)

COMPARISON_JOINERS = (" vs ", " versus ", " oder ", " o ", " or ", " contro ", " gegenuber ")


def _norm_cues(text: str) -> str:
    """Fold accents and turn apostrophes/punctuation into spaces for cue matching."""
    folded = fold(text)
    cleaned = re.sub(r"['’`´]", " ", folded)
    return re.sub(r"\s+", " ", cleaned).strip()


@dataclass
class Route:
    kind: str            # "figure" | "lookup" | "advisory" | "default"
    reason: str

    @property
    def is_figure(self) -> bool:
        return self.kind == "figure"

    @property
    def is_lookup(self) -> bool:
        return self.kind == "lookup"

    @property
    def is_advisory(self) -> bool:
        return self.kind == "advisory"


def route(query: str, language: str | None = None) -> Route:
    """Classify a question into figure / advisory / lookup / default."""
    text = _norm_cues(query)
    tokens = tokenize(query)
    if not tokens:
        return Route("default", "empty query")

    languages = [language] if language else ["en", "de", "it"]
    # Always inspect all languages as fallback when forced/detected language
    # differs from an international loanword in the query.
    all_langs = languages + [lg for lg in ("en", "de", "it") if lg not in languages]

    for lang in all_langs:
        for cue in _FIGURE_CUES.get(lang, ()):
            if cue in text:
                return Route("figure", f"figure cue '{cue}'")
        for pattern in _FIGURE_PATTERNS.get(lang, ()):
            if pattern.search(text):
                return Route("figure", f"figure pattern ({lang})")

    for lang in languages:
        for cue in _ADVISORY_CUES.get(lang, ()):
            if cue in text:
                return Route("advisory", f"advisory cue '{cue}'")
    # "A vs B" is a comparison even without a keyword
    if any(joiner in f" {text} " for joiner in COMPARISON_JOINERS) and len(tokens) <= 12:
        return Route("advisory", "comparison between two subjects")

    for lang in languages:
        for cue in _LOOKUP_CUES.get(lang, ()):
            if cue in text:
                return Route("lookup", f"lookup cue '{cue}'")
    if _VALUE_IN_QUERY_RE.search(query) or numbers(query):
        return Route("lookup", "question quotes a value/unit")

    return Route("default", "no specific cue")
