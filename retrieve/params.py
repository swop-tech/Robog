"""Structured parameter lookup -- the technician path (gap G1).

"Wieviel Zuladung hat der FlashBot?" should not depend on an embedding model
finding a table row. ``build/params.jsonl`` already holds 2,039 rows of
``product -> parameter -> value/unit -> source page``, so a parameter-shaped
question can be answered by matching the parameter name and quoting the value
verbatim, with the same citation trail as any chunk.

Usage:
    from retrieve.params import ParamIndex
    index = ParamIndex.from_jsonl("build/params.jsonl")
    hits = index.search("Zuladung FlashBot", language="de", k=3)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .text import numbers, tokenize

# Words that appear in every product name; they must not drive a parameter match.
ALL_PRODUCT_WORDS: set[str] = {
    "pudu", "pudubot", "robot", "user", "manual", "station", "series", "pro",
}


@dataclass
class ParamHit:
    record: dict
    score: float

    @property
    def parameter(self) -> str:
        return self.record.get("parameter", "")

    @property
    def value(self) -> str:
        return self.record.get("value", "")

    @property
    def citation(self) -> str:
        meta = self.record
        page = meta.get("page") or meta.get("pdf_page")
        return f"{meta.get('product', '')} — {meta.get('parameter', '')}: {meta.get('value', '')} ({meta.get('title', '')}, §{meta.get('section_number') or meta.get('section', '')}, p. {page})"


class ParamIndex:
    """Inverted index over parameter names, with product/language awareness."""

    def __init__(self, records: list[dict]):
        self.records = records
        self.by_language: dict[str, list[int]] = {}
        self.by_product: dict[str, list[int]] = {}
        self._tokens: list[list[str]] = []
        postings: dict[str, set[int]] = {}

        for index, record in enumerate(records):
            tokens = tokenize(record.get("parameter", ""))
            self._tokens.append(tokens)
            for token in set(tokens):
                postings.setdefault(token, set()).add(index)
            self.by_language.setdefault(record.get("language", ""), []).append(index)
            self.by_product.setdefault(record.get("product", ""), []).append(index)

        self.postings = postings
        self.n = len(records)
        self.avg_len = (sum(len(t) for t in self._tokens) / self.n) if self.n else 1.0
        self._df = {token: len(docs) for token, docs in postings.items()}

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "ParamIndex":
        p = Path(path)
        if not p.exists():
            return cls([])
        with p.open(encoding="utf-8") as fh:
            return cls([json.loads(line) for line in fh if line.strip()])

    # -- search ------------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        k: int = 5,
        language: str | None = None,
        product: str | None = None,
        products: list[str] | None = None,
        extra_tokens: list[str] | None = None,
    ) -> list[ParamHit]:
        query_tokens = set(tokenize(query))
        if not query_tokens:
            return []
        query_numbers = numbers(query)
        # A product name in the query must not be needed to match a parameter.
        for name in [product or ""] + list(products or []):
            query_tokens -= set(tokenize(name))
        query_tokens -= ALL_PRODUCT_WORDS
        # Lexicon expansion (payload -> "load capacity", Zuladung, carico ...)
        synonyms = set(extra_tokens or ()) - query_tokens - ALL_PRODUCT_WORDS

        candidates: set[int] = set()
        for token in query_tokens | synonyms:
            candidates |= self.postings.get(token, set())
        wanted = set(products or ())
        if product:
            wanted.add(product)
        # Filters are strict: an empty intersection means "no such row", not
        # "ignore the filter" (the language policy must hold everywhere).
        if wanted:
            product_ids = {i for p in wanted for i in self.by_product.get(p, [])}
            candidates &= product_ids
        if language:
            candidates &= set(self.by_language.get(language, []))
        if not candidates:
            return []

        scored: list[ParamHit] = []
        for index in candidates:
            tokens = self._tokens[index]
            if not tokens:
                continue
            length = len(tokens) or 1
            score = 0.0
            for token, weight in [(t, 1.0) for t in query_tokens] + [(t, 0.45) for t in synonyms]:
                df = self._df.get(token, 0)
                if not df:
                    continue
                idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
                tf = tokens.count(token)
                score += weight * idf * (tf * 2.5) / (tf + 1.5 * (0.25 + 0.75 * length / (self.avg_len or 1)))
            # exact parameter-name match is worth a lot
            record = self.records[index]
            name_tokens = set(tokens)
            if name_tokens and name_tokens <= query_tokens:
                score *= 1.6
            # the query quoting the value ("29.2 V") is a strong signal
            if query_numbers and str(record.get("value_number")) in query_numbers:
                score += 5.0
            if score > 0:
                scored.append(ParamHit(record, round(score, 4)))

        scored.sort(key=lambda hit: (-hit.score, hit.parameter))
        return scored[:k]

    # -- helpers -----------------------------------------------------------
    @property
    def products(self) -> list[str]:
        return sorted(self.by_product)

    def languages_for(self, product: str) -> set[str]:
        """Which languages a product's parameter rows exist in."""
        return {
            self.records[i].get("language", "")
            for i in self.by_product.get(product, [])
        }
