"""Query routing -- the right technique per question type (gap G2).

Three question shapes, three retrieval strategies:

* **lookup** ("What is the payload of the FlashBot?") -> parameter index first,
  BM25 second, quotes values verbatim.
* **advisory** ("Which robot suits a busy restaurant?") -> semantic/keyword
  search spread over several products, so the answer can compare.
* **default** -> ordinary hybrid search.

The router is a handful of cue phrases per language plus a numeric/unit test.
It is deterministic and cheap; an LLM router would add latency, cost and
non-determinism for a decision three regex groups already get right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text import fold, numbers, tokenize

# -- cue phrases -----------------------------------------------------------
_ADVISORY_CUES = {
    "en": (
        "which robot", "which one", "best for", "best suited", "recommend",
        "suggest", "compare", "comparison", "difference between", "vs", "versus",
        "should i choose", "what should i buy", "fits my", "suitable",
    ),
    "de": (
        "welcher roboter", "welches modell", "welche roboter", "empfehl",
        "empfiehl", "vergleich", "unterschied zwischen", "eignet sich",
        "passt zu", "sollte ich", "welches gerät", "besser für",
    ),
    "it": (
        "quale robot", "quale modello", "consigli", "consiglio", "confronto",
        "differenza tra", "differenze tra", "adatto", "adatta", "meglio per",
        "quale scegliere", "conviene",
    ),
}

_LOOKUP_CUES = {
    "en": (
        "what is the", "how much", "how many", "value of", "weight", "voltage",
        "capacity", "dimension", "payload", "battery life", "runtime", "spec",
        "specification", "power output", "temperature range", "size of",
    ),
    "de": (
        "wie viel", "wieviel", "wie hoch", "wie lang", "wie groß", "wie gross",
        "wert von", "gewicht", "spannung", "kapazität", "kapazitat", "abmessung",
        "maße", "masse", "zuladung", "nutzlast", "akkulaufzeit", "technische daten",
    ),
    "it": (
        "quanto", "quanti", "qual è il valore", "peso", "tensione", "capacità",
        "capacita", "dimensioni", "carico", "autonomia", "scheda tecnica",
        "dati tecnici", "potenza",
    ),
}

# "29.2 V", "20 kg", "1.2 m/s" -- a value quoted in the question.
_VALUE_IN_QUERY_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:kg|g|mm|cm|m/s|m|v|a|w|hz|%|ah|wh|°c|℃|h|min|s)\b",
    re.IGNORECASE,
)

COMPARISON_JOINERS = (" vs ", " versus ", " oder ", " o ", " or ", " contro ", " gegenüber ")


@dataclass
class Route:
    kind: str            # "lookup" | "advisory" | "default"
    reason: str

    @property
    def is_lookup(self) -> bool:
        return self.kind == "lookup"

    @property
    def is_advisory(self) -> bool:
        return self.kind == "advisory"


def route(query: str, language: str | None = None) -> Route:
    """Classify a question into lookup / advisory / default."""
    text = fold(query)
    tokens = tokenize(query)
    if not tokens:
        return Route("default", "empty query")

    languages = [language] if language else ["en", "de", "it"]

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
