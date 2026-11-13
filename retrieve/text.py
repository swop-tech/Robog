"""Text normalisation and tokenisation shared by every retriever.

The corpus is trilingual, so matching has to survive accents, German umlauts,
Italian apostrophes and the decimal-comma/point difference between the
translations ("29,2 V" in DE/IT, "29.2 V" in EN).

Standard library only (see docs/stack-decisions.md).
"""

from __future__ import annotations

import re
import unicodedata

# Latin letters that NFKD does not decompose.
_SPECIAL_FOLDS = str.maketrans({"ß": "ss", "ø": "o", "ł": "l", "æ": "ae", "œ": "oe"})

# Words are: letters/digits, optional decimal part, optional unit suffix
# ("1.2", "m/s", "29,4", "ip54", "6.6").
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)?(?:/[a-z0-9]+)?")

# Stopwords are deliberately *distinctive* per language: no single-letter words
# ("a", "e", "i") that also occur in another language.
STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset("""
        a an the and or of to in for on with is are was were be been being by at from that this these those
        it its as not no do does did can could may might will would should must shall if then than when
        where which who whom whose what how why all any both each more most other some such only own same
        so too very just about into over under between during please tell me show give want need
        """.split()),
    "de": frozenset("""
        der die das den dem des ein eine einen einem einer eines und oder aber von zu zum zur im am an
        mit ist sind war waren sein hat haben hatte werden wird wurde worden nicht kein keine keinen
        wenn dann als auch es sie er wir ich man kann können muss müssen soll sollen darf dürfen
        bei über unter nach vor für durch gegen ohne um wie was welche welcher welches welchen viel viele
        wo wann warum weshalb bitte sagen zeigen gibt es
        """.split()),
    "it": frozenset("""
        il lo la gli le un una uno dei delle degli del della dello al alla alle allo nel nella nei nelle
        di da con su per tra fra e ed è sono era erano essere ha hanno aveva non no che chi cui come
        quando dove perché anche più molto poco se ma però questo questa questi queste quello quella
        quale quali quanto quanti posso deve devono vuole volevo mi ti ci si ne
        """.split()),
}

ALL_STOPWORDS = frozenset().union(*STOPWORDS.values())


def fold(text: str) -> str:
    """Lowercase and strip diacritics: ``"Kapazität"`` -> ``"kapazitat"``.

    Applied to both documents and queries, so ä/ae or é/e spellings still match.
    """
    text = text.translate(_SPECIAL_FOLDS)
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.lower()


def tokenize(text: str, *, drop_stopwords: bool = True) -> list[str]:
    """Split text into folded tokens, normalising decimal commas.

    ``"Gewicht: 14,3 kg"`` -> ``["gewicht", "14.3", "kg"]``
    """
    tokens = []
    for token in _TOKEN_RE.findall(fold(text)):
        if token[0].isdigit() and "," in token:
            token = token.replace(",", ".")
        if drop_stopwords and token in ALL_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def numbers(text: str) -> set[str]:
    """Numeric values in a text, normalised (``"29,2 V"`` -> ``{"29.2"}``)."""
    return {
        t.replace(",", ".")
        for t in _TOKEN_RE.findall(fold(text))
        if t and t[0].isdigit()
    }
