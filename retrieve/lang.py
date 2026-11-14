"""Question-language detection and the language policy of the retriever.

Two jobs:

1. ``detect_language(question)`` -- which of EN / DE / IT is this message in?
   Deliberately deterministic (no LLM, no model download): the policy must be
   testable and must not fail on a laptop without internet.
2. ``plan(query, ...)`` -- turn the detected language into a retrieval plan:
   try the user's language first, fall back across languages, and say so.

Why a *preference* and not a filter: 19 of 22 products exist in all three
languages, but FlashBot, GW1 and Pager have no Italian. A hard filter would
return nothing for exactly the questions that demo Bonus 02.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .text import STOPWORDS, fold, tokenize

LANGUAGES = ("en", "de", "it")

# Letters that strongly suggest one language (weighted heavily).
_CHAR_CUES = {
    "de": "äöüß",
    "it": "àèéìòù",
    "en": "",
}

# Word endings that are characteristic of a language (weighted lightly).
_SUFFIX_CUES = {
    "de": ("ung", "keit", "heit", "schaft", "chen", "lich", "zeit", "maß"),
    "it": ("zione", "zioni", "mente", "ità", "aggio", "tore", "batteria", "quanto"),
    "en": ("tion", "tions", "ing", "ness", "ment"),
}

# Strong single-word cues for short questions ("Akkufach?"), including the
# question words a technician would actually type.
_WORD_CUES = {
    "de": {
        "wie", "was", "welche", "welcher", "welches", "warum", "wann", "wo",
        "kann", "muss", "soll", "darf", "ist", "sind", "der", "die", "das",
        "und", "nicht", "mit", "für", "von", "bei", "über", "unter", "auch",
        # domain words (short technician queries)
        "gewicht", "akku", "akkukapazität", "batterie", "spannung", "ladestation",
        "reinigung", "reinigen", "schmutzwassertank", "geschwindigkeit",
        "abmessungen", "zuladung", "ladegerät", "einschalten", "ausschalten",
        "temperatur", "feuchtigkeit", "garantie", "notausschalter",
    },
    "it": {
        "quale", "quali", "quanto", "quanti", "come", "dove", "quando",
        "perché", "perche", "cosa", "posso", "deve", "sono", "che", "non",
        "per", "con", "del", "della", "delle", "degli", "una", "uno", "anche",
        # domain words (short technician queries)
        "peso", "batteria", "tensione", "ricarica", "pulizia", "pulire",
        "serbatoio", "velocità", "dimensioni", "carico", "alimentatore",
        "accensione", "spegnimento", "temperatura", "umidità", "garanzia",
    },
    "en": {
        "what", "which", "how", "why", "when", "where", "does", "do", "can",
        "should", "is", "are", "the", "and", "not", "with", "for", "from",
        "about", "much", "many",
        # domain words that make a one-word question unambiguous
        "weight", "battery", "voltage", "charging", "cleaning", "tank", "speed",
        "dimensions", "payload", "charger", "power", "humidity", "warranty",
    },
}


def _score_language(text: str, lang: str) -> int:
    # Character cues must be read *before* folding: NFKD turns "à" into "a"
    # and "ß" into "ss", so a folded text has no accents left to count.
    raw = text.lower()
    tokens = tokenize(text, drop_stopwords=False)
    score = 0
    score += 3 * sum(raw.count(ch) for ch in _CHAR_CUES[lang] if ch)
    # function words: distinctive stopwords and question words
    distinctive = STOPWORDS[lang] & (_WORD_CUES[lang] | STOPWORDS[lang])
    score += 2 * sum(1 for t in tokens if t in _WORD_CUES[lang])
    score += 1 * sum(1 for t in tokens if t in distinctive)
    # endings
    score += sum(1 for t in tokens if any(t.endswith(s) for s in _SUFFIX_CUES[lang]))
    return score


def detect_language(text: str, *, min_score: int = 2) -> str | None:
    """Return ``"en"``/``"de"``/``"it"`` for a question, or None if unsure.

    None means "no preference": the caller searches every language and lets the
    ranking decide (the right behaviour for "Akkufach?" or a model code alone).
    """
    if not text or not text.strip():
        return None
    scores = {lang: _score_language(text, lang) for lang in LANGUAGES}
    best = max(scores, key=lambda lang: scores[lang])
    best_score = scores[best]
    if best_score < min_score:
        return None
    # Require a margin, otherwise short mixed queries ("FlashBot specs") are
    # claimed by whichever language has more coincidental stopwords.
    runner_up = max((s for lang, s in scores.items() if lang != best), default=0)
    if best_score - runner_up < 1 and best_score < 4:
        return None
    return best


@dataclass
class LanguagePlan:
    """How a single query should treat languages."""

    user_language: str | None
    #: tiers to try, in order; None means "no language restriction"
    tiers: list[str | None] = field(default_factory=list)
    #: languages that actually have content for the products in the question
    available: set[str] = field(default_factory=set)
    cross_lingual: bool = False

    def describe(self) -> str:
        if self.user_language is None:
            return "language: auto (no restriction)"
        if not self.cross_lingual:
            return f"language: {self.user_language} first, cross-lingual fallback"
        return (
            f"language: {self.user_language} not available for this product — "
            f"searching {', '.join(sorted(self.available)) or 'all languages'}"
        )


def plan(user_language: str | None, available: set[str] | None = None) -> LanguagePlan:
    """Build the tier list: user's language first, then everything.

    ``available`` is the set of languages the products in the question actually
    have. When the user's language is missing there (Italian + FlashBot), the
    plan starts cross-lingual instead of wasting a tier on an empty filter.
    """
    available = set(available or ())
    if user_language is None:
        return LanguagePlan(user_language=None, tiers=[None], available=available)

    if available and user_language not in available:
        return LanguagePlan(
            user_language=user_language,
            tiers=[None],           # cross-lingual straight away
            available=available,
            cross_lingual=True,
        )
    tiers: list[str | None] = [user_language]
    if available - {user_language} or not available:
        tiers.append(None)          # fallback: any language
    return LanguagePlan(user_language=user_language, tiers=tiers, available=available)
