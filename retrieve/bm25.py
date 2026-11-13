"""BM25 keyword retrieval -- the exactness half of hybrid retrieval (gap G1).

Embeddings blur numbers, units and model codes; BM25 does not. A technician
asking "what is the charger power output" or "29.2 V" needs the chunk that
literally contains it, and that is what this index finds.

Pure Python, ~1,400 chunks: building the index takes well under a second, so
there is no reason to add a dependency (docs/stack-decisions.md §2 keeps the
retriever simple on purpose; a framework comes in with the answering phase).

Usage:
    from retrieve.bm25 import BM25
    index = BM25([("chunk-1", "FlashBot ... 20 kg payload")])
    hits = index.search("payload kg", k=5)        # [(chunk_id, score), ...]
"""

from __future__ import annotations

import bisect
import math
from collections import Counter
from dataclasses import dataclass

from .text import tokenize


@dataclass
class BM25Hit:
    doc_id: str
    score: float

    @property
    def chunk_id(self) -> str:
        """Alias: BM25 documents are chunks, the engine speaks chunk ids."""
        return self.doc_id


class BM25:
    """Okapi BM25 with a metadata allow-list per query.

    ``allowed`` lets the caller pre-filter by language/product so that scoring
    only ever considers documents that pass the language policy.
    """

    def __init__(
        self,
        docs: list[tuple[str, str]],
        *,
        k1: float = 1.5,
        b: float = 0.75,
        title_boost: int = 2,
    ):
        """``docs`` is a list of ``(doc_id, text)`` pairs."""
        self.k1 = k1
        self.b = b
        self.title_boost = title_boost
        self.doc_ids: list[str] = []
        self.tfs: dict[str, Counter[str]] = {}
        self.lengths: dict[str, int] = {}
        self.postings: dict[str, set[str]] = {}

        for doc_id, text in docs:
            tokens = tokenize(text)
            tf = Counter(tokens)
            self.doc_ids.append(doc_id)
            self.tfs[doc_id] = tf
            self.lengths[doc_id] = len(tokens)
            for token in tf:
                self.postings.setdefault(token, set()).add(doc_id)

        self._vocab = sorted(self.postings)
        self.n_docs = len(self.doc_ids)
        self.avg_len = (sum(self.lengths.values()) / self.n_docs) if self.n_docs else 0.0

    # -- scoring -----------------------------------------------------------
    def _idf(self, token: str) -> float:
        df = len(self.postings.get(token, ()))
        if not df:
            return 0.0
        return math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))

    def _prefix_expand(self, token: str, *, min_prefix: int = 4, cap: int = 10) -> list[str]:
        """Vocabulary terms sharing a prefix -- a poor man's stemmer.

        "consuma" is not in the index, but "consumo"/"consumption" are, and a
        shared prefix is enough to find them without adding a stemmer.
        """
        if len(token) < min_prefix:
            return []
        prefix = token[: max(min_prefix, len(token) - 2)]
        start = bisect.bisect_left(self._vocab, prefix)
        found = []
        for word in self._vocab[start:]:
            if not word.startswith(prefix):
                break
            if word != token:
                found.append(word)
            if len(found) >= cap:
                break
        return found

    def search(
        self,
        query: str,
        k: int = 10,
        *,
        allowed: set[str] | None = None,
        query_tokens: list[str] | None = None,
    ) -> list[BM25Hit]:
        tokens = query_tokens if query_tokens is not None else tokenize(query)
        if not tokens:
            return []
        # Words missing from the index are retried as prefixes.
        expanded = list(tokens)
        for token in tokens:
            if token not in self.postings:
                expanded.extend(self._prefix_expand(token))
        tokens = list(dict.fromkeys(expanded))

        # Candidate set: documents containing at least one query term.
        candidates: set[str] = set()
        for token in set(tokens):
            candidates |= self.postings.get(token, set())
        if allowed is not None:
            candidates &= allowed
        if not candidates:
            return []

        scores: dict[str, float] = {}
        for doc_id in candidates:
            tf = self.tfs[doc_id]
            length = self.lengths[doc_id] or 1
            score = 0.0
            for token in set(tokens):
                freq = tf.get(token, 0)
                if not freq:
                    continue
                idf = self._idf(token)
                denom = freq + self.k1 * (1 - self.b + self.b * length / (self.avg_len or 1))
                score += idf * (freq * (self.k1 + 1)) / denom
            if score > 0:
                scores[doc_id] = score

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return [BM25Hit(doc_id, score) for doc_id, score in ranked[:k]]

    # -- introspection -----------------------------------------------------
    def best_score(self, query: str, *, allowed: set[str] | None = None) -> float:
        hits = self.search(query, k=1, allowed=allowed)
        return hits[0].score if hits else 0.0
