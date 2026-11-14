"""The retrieval engine: route -> language policy -> hybrid ranking -> citations.

This is the standalone retriever of **Bonus 01** (one module, no UI, JSON-ready
output) and the place where three review gaps meet:

* G1 exactness -- BM25 over the chunks + the structured parameter index;
* G2 question types -- the router (lookup / advisory / default);
* G3 + Bonus 02 multilingual -- the language policy from ``retrieve.lang``.

Keyword ranking is deliberately simple and inspectable: a strict BM25 pass over the
question's own words, a wider pass over the domain lexicon
(``retrieve.lexicon``, which is also what bridges DE/IT questions to English
manuals), and the parameter index folded in with reciprocal rank fusion.

No embedding model is required: this runs offline, on any laptop. A dense
retriever (Chroma + the same embeddings Sujal's pipeline indexes with, see
``retrieve/vector.py``) can be attached -- ``--vector`` on the CLI,
``SearchEngine.from_build(..., vector="auto")``, or an injected retriever --
and its ranked list is fused into the same reciprocal-rank-fusion, honoring
the same language policy. Without it, behavior is unchanged.

Usage:
    from retrieve.engine import SearchEngine
    engine = SearchEngine.from_build()
    result = engine.search("Wieviel Zuladung hat der FlashBot?")
    for hit in result.hits:
        print(hit.score, hit.citation)
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .bm25 import BM25
from .lang import LANGUAGES, LanguagePlan, detect_language, plan
from .lexicon import concepts_for, expand_tokens
from .params import ParamHit, ParamIndex
from .router import Route, route as classify
from .text import fold, tokenize
from .vector import ChromaRetriever, DenseHit, VectorStoreUnavailable

# Words every product name contains -- they say nothing about *which* product.
GENERIC_PRODUCT_WORDS = {
    "pudu", "pudubot", "user", "manual", "series", "robot",
}

# Reciprocal-rank-fusion constant (standard value from the IR literature).
RRF_K = 60
# An expanded (synonym) match counts less than a literal one.
EXPANSION_WEIGHT = 0.45


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
@dataclass
class Hit:
    chunk_id: str
    text: str
    metadata: dict
    score: float
    rank: int
    sources: list[str] = field(default_factory=list)

    @property
    def language(self) -> str:
        return self.metadata.get("language", "")

    @property
    def product(self) -> str:
        return self.metadata.get("product", "")

    @property
    def chunk_type(self) -> str:
        return self.metadata.get("chunk_type", "text")

    @property
    def image(self) -> str | None:
        return self.metadata.get("image")

    @property
    def section(self) -> str:
        number = self.metadata.get("section_number")
        title = self.metadata.get("section_title") or self.metadata.get("section", "")
        return f"{number} {title}".strip() if number else title

    @property
    def page(self) -> int | None:
        return self.metadata.get("page") or self.metadata.get("pdf_page")

    @property
    def citation(self) -> str:
        """Human-readable, checkable reference -- shown next to every claim."""
        parts = [self.metadata.get("title", "").strip()]
        if self.section:
            parts.append(f"§{self.section}")
        if self.page:
            parts.append(f"p. {self.page}")
        parts.append(self.language.upper() or "?")
        return ", ".join(p for p in parts if p)

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "score": round(self.score, 4),
            "text": self.text,
            "citation": self.citation,
            "language": self.language,
            "product": self.product,
            "chunk_type": self.chunk_type,
            "image": self.image,
            "sources": self.sources,
            "metadata": self.metadata,
        }


@dataclass
class SearchResult:
    query: str
    language: str | None
    route: str
    route_reason: str
    policy: str
    hits: list[Hit] = field(default_factory=list)
    params: list[ParamHit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tiers_used: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    coverage: float = 0.0  # share of the question's content words found in hit #1

    @property
    def confidence(self) -> str:
        """How well the corpus matches the question -- '' when nothing matched.

        Used later by the answer composer to say "this is not in the manuals"
        instead of inventing something (faithfulness, review gap G5).
        """
        if not self.hits:
            return "none"
        if self.coverage >= 0.6:
            return "high"
        if self.coverage >= 0.3:
            return "medium"
        return "low"

    @property
    def answer_language(self) -> str | None:
        """Language the answer must be written in (the user's, not the source's)."""
        return self.language

    @property
    def cross_lingual(self) -> bool:
        """True when the answer must be composed from another language's sources."""
        return bool(self.language) and any(hit.language != self.language for hit in self.hits)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "language": self.language,
            "route": self.route,
            "route_reason": self.route_reason,
            "policy": self.policy,
            "cross_lingual": self.cross_lingual,
            "products": self.products,
            "tiers_used": self.tiers_used,
            "confidence": self.confidence,
            "coverage": round(self.coverage, 3),
            "notes": self.notes,
            "params": [
                {"parameter": p.parameter, "value": p.value, "citation": p.citation, **p.record}
                for p in self.params
            ],
            "hits": [hit.to_dict() for hit in self.hits],
        }


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------
class SearchEngine:
    """Hybrid retriever over the chunk corpus (+ parameter index)."""

    def __init__(
        self,
        chunks: list[dict],
        params: ParamIndex | None = None,
        *,
        retriever: ChromaRetriever | None = None,
    ):
        self.chunks = chunks
        self.by_id = {chunk["id"]: chunk for chunk in chunks}
        self.params = params or ParamIndex([])
        # Optional dense (vector) retriever. When None, the engine behaves
        # exactly as before (offline keyword retrieval). Attach one to fuse
        # Chroma's ranked list into the same RRF fusion.
        self.retriever = retriever
        self.retriever_note: str | None = None

        # Section title is repeated into the indexed text (a small but real
        # boost: the heading names the topic better than the body prose).
        docs = []
        for chunk in chunks:
            meta = chunk.get("metadata", {})
            heading = meta.get("section_title") or ""
            docs.append((chunk["id"], f"{heading} {heading} {heading} {chunk.get('text', '')}"))
        self.bm25 = BM25(docs)

        self.product_languages: dict[str, set[str]] = defaultdict(set)
        self._alias_products: dict[str, set[str]] = defaultdict(set)
        for chunk in chunks:
            meta = chunk.get("metadata", {})
            self.product_languages[meta.get("product", "")].add(meta.get("language", ""))
        for product in self.product_languages:
            for alias in self._aliases_for(product):
                self._alias_products[alias].add(product)
        self._alias_patterns = [
            (re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE), alias)
            for alias in sorted(self._alias_products, key=len, reverse=True)
        ]

    # -- construction ------------------------------------------------------
    @classmethod
    def from_build(
        cls,
        chunks_path: str | Path = "build/chunks.jsonl",
        params_path: str | Path = "build/params.jsonl",
        *,
        vector: str | ChromaRetriever | None = None,
    ) -> "SearchEngine":
        """Build the engine from the pipeline's build directory.

        ``vector``:

        * ``None`` (default) -- keyword retrieval only, fully offline;
        * ``"auto"`` -- connect to the existing Chroma store (see
          ``retrieve/vector.py``); if it is not built yet, the engine silently
          keeps working in keyword mode and says so in ``retriever_note``;
        * a ``ChromaRetriever`` (or compatible object) -- use it directly.
        """
        chunks_file = Path(chunks_path)
        if not chunks_file.exists():
            raise SystemExit(
                f"{chunks_file} not found -- run 'python -m ingest.run --data data' first."
            )
        with chunks_file.open(encoding="utf-8") as fh:
            chunks = [json.loads(line) for line in fh if line.strip()]
        engine = cls(chunks, ParamIndex.from_jsonl(params_path))
        if vector is None:
            return engine
        if isinstance(vector, str):
            if vector != "auto":
                raise ValueError(f"vector must be None, 'auto' or a retriever, got {vector!r}")
            try:
                engine.retriever = ChromaRetriever.connect()
            except VectorStoreUnavailable as exc:
                engine.retriever = None
                engine.retriever_note = f"dense retrieval disabled: {exc}"
            return engine
        engine.retriever = vector
        return engine

    @staticmethod
    def _aliases_for(product: str) -> set[str]:
        """Product names as users type them: full name, without "PUDU", model code."""
        aliases = {product}
        aliases.add(re.sub(r"^(?:PUDU|Pudu)\s+", "", product))
        for token in re.findall(r"[A-Za-z]+\d+[A-Za-z]*|\d+[A-Za-z]+", product):
            aliases.add(token)
        return {fold(a) for a in aliases if a and a.strip()}

    # -- helpers -----------------------------------------------------------
    def languages_for(self, products: list[str] | None = None) -> set[str]:
        if products:
            langs = {lang for product in products for lang in self.product_languages.get(product, ())}
            if langs:
                return {lang for lang in langs if lang}
        return {lang for langs in self.product_languages.values() for lang in langs if lang}

    def mentioned_products(self, query: str) -> list[str]:
        """Products named in the question (a code like "CC1" may mean several)."""
        folded = fold(query)
        found: list[str] = []
        for pattern, alias in self._alias_patterns:
            if not pattern.search(folded):
                continue
            for product in sorted(self._alias_products[alias]):
                if product not in found:
                    found.append(product)
        return found

    def _disambiguate(self, products: list[str], expansion: list[str]) -> list[str]:
        """A code like "CC1" matches six products; keep the ones the question
        actually describes ("stazione di ricarica" -> CC1 Charging Station)."""
        if len(products) <= 1:
            return products
        query_tokens = set(expansion)
        scored = []
        for product in products:
            name_tokens = set(tokenize(product)) - GENERIC_PRODUCT_WORDS
            scored.append((len(name_tokens & query_tokens), product))
        best = max(score for score, _ in scored)
        if best <= 0:
            return products
        return [product for score, product in scored if score == best]

    def _allowed_ids(
        self,
        *,
        language: str | None = None,
        products: list[str] | None = None,
        doc_type: str | None = None,
        chunk_type: str | None = None,
    ) -> set[str] | None:
        if not any((language, products, doc_type, chunk_type)):
            return None
        wanted = set(products or ())
        allowed = set()
        for chunk_id, chunk in self.by_id.items():
            meta = chunk.get("metadata", {})
            if language and meta.get("language") != language:
                continue
            if wanted and meta.get("product") not in wanted:
                continue
            if doc_type and meta.get("doc_type") != doc_type:
                continue
            if chunk_type and meta.get("chunk_type") != chunk_type:
                continue
            allowed.add(chunk_id)
        return allowed

    def _chunk_view(self, chunk_id: str, dense_by_id: dict[str, DenseHit]) -> tuple[str, dict]:
        """(text, metadata) for a chunk id.

        Prefers the chunk corpus (``build/chunks.jsonl``) so citations have one
        source of truth; falls back to what the vector store holds (the same
        rows, indexed by id) when the two are out of sync.
        """
        chunk = self.by_id.get(chunk_id)
        if chunk is not None:
            return chunk.get("text", ""), chunk.get("metadata", {})
        dense = dense_by_id.get(chunk_id)
        if dense is not None:
            return dense.text, dense.metadata
        return "", {"id": chunk_id}

    def _dense_hits(
        self,
        query: str,
        allowed: set[str] | None,
        k: int,
    ) -> list[DenseHit]:
        """Ask the vector store for candidates; degrade gracefully on failure.

        A demo laptop without the store, the embedding model or the network must
        still answer -- the keyword path carries on and the note says why.
        """
        if self.retriever is None:
            return []
        try:
            return self.retriever.search(query, k=k, allowed_ids=allowed)
        except VectorStoreUnavailable as exc:
            self.retriever = None
            self.retriever_note = f"dense retrieval disabled: {exc}"
            return []
        except Exception as exc:  # any retriever failure must not kill search
            self.retriever = None
            self.retriever_note = f"dense retrieval disabled: unexpected {type(exc).__name__}: {exc}"
            return []

    def _rank_tier(
        self,
        tokens: list[str],
        expansion: list[str],
        *,
        allowed: set[str] | None,
        k: int,
        dense: list[DenseHit] | None = None,
    ) -> list[Hit]:
        """BM25 strict pass + lexicon pass + dense pass, fused with RRF."""
        candidate_k = max(k * 3, 12)
        strict = self.bm25.search("", k=candidate_k, allowed=allowed, query_tokens=tokens)
        loose = (
            self.bm25.search("", k=candidate_k, allowed=allowed, query_tokens=expansion)
            if expansion and expansion != tokens
            else []
        )
        dense = dense or []
        dense_by_id = {hit.chunk_id: hit for hit in dense}

        scores: dict[str, float] = defaultdict(float)
        sources: dict[str, list[str]] = defaultdict(list)
        for rank, hit in enumerate(strict, start=1):
            scores[hit.chunk_id] += 1.0 / (RRF_K + rank)
            sources[hit.chunk_id].append("bm25")
        for rank, hit in enumerate(loose, start=1):
            scores[hit.chunk_id] += EXPANSION_WEIGHT * 1.0 / (RRF_K + rank)
            if "bm25" not in sources[hit.chunk_id]:
                sources[hit.chunk_id].append("lexicon")
        for rank, hit in enumerate(dense, start=1):
            # Dense is a co-equal retriever: same RRF weight as the BM25 pass.
            scores[hit.chunk_id] += 1.0 / (RRF_K + rank)
            sources[hit.chunk_id].append("vector")

        # Drop ids that exist in no source of truth (stale index vs corpus).
        for chunk_id in list(scores):
            if chunk_id not in self.by_id and chunk_id not in dense_by_id:
                del scores[chunk_id]

        # Chunks whose own heading contains query terms are strong candidates.
        if len(tokens) > 1:
            query_set = set(tokens)
            for chunk_id in scores:
                _, meta = self._chunk_view(chunk_id, dense_by_id)
                title = meta.get("section_title") or ""
                title_tokens = set(tokenize(title))
                if title_tokens:
                    overlap = len(query_set & title_tokens) / len(query_set)
                    scores[chunk_id] *= 1 + 1.0 * overlap

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:candidate_k]
        hits: list[Hit] = []
        for rank, (chunk_id, score) in enumerate(ranked, start=1):
            text, meta = self._chunk_view(chunk_id, dense_by_id)
            hits.append(
                Hit(
                    chunk_id=chunk_id,
                    text=text,
                    metadata=meta,
                    score=score,
                    rank=rank,
                    sources=sources[chunk_id],
                )
            )
        return hits

    @staticmethod
    def _dedupe_translations(hits: list[Hit], preference: list[str]) -> list[Hit]:
        """Drop a hit when the same passage was already returned in a preferred
        language (the parallel manuals repeat content EN/DE/IT)."""
        seen: dict[str, set[str]] = defaultdict(set)
        kept: list[Hit] = []
        for hit in hits:
            group = hit.metadata.get("translation_group") or hit.chunk_id
            languages = seen[group]
            better = [lang for lang in preference if lang in languages]
            if better and hit.language not in better:
                continue
            languages.add(hit.language)
            kept.append(hit)
        return kept

    @staticmethod
    def _diversify(hits: list[Hit], k: int, per_product: int = 2) -> list[Hit]:
        """Advisory questions need several products, not five chunks of one."""
        counts: dict[str, int] = defaultdict(int)
        kept: list[Hit] = []
        overflow: list[Hit] = []
        for hit in hits:
            if counts[hit.product] >= per_product:
                overflow.append(hit)
                continue
            counts[hit.product] += 1
            kept.append(hit)
            if len(kept) == k:
                return kept
        for hit in overflow:
            if len(kept) == k:
                break
            kept.append(hit)
        return kept

    # -- search ------------------------------------------------------------
    def search(
        self,
        query: str,
        k: int = 8,
        *,
        language: str | None = None,
        product: str | None = None,
        doc_type: str | None = None,
        chunk_type: str | None = None,
        follow_user_language: bool = True,
        cross_lingual_fallback: bool = True,
        dedupe_translations: bool = True,
    ) -> SearchResult:
        """Retrieve the best chunks for a question.

        ``language`` forces the question language (the UI can pass it); otherwise
        it is detected. ``follow_user_language=False`` disables the language
        policy entirely -- used by the evaluation harness as a baseline.
        """
        query = (query or "").strip()
        tokens = tokenize(query)
        detected = language or detect_language(query)
        products = [product] if product else self.mentioned_products(query)
        if len(products) > 1:
            products = self._disambiguate(products, expand_tokens(tokenize(query)))
        available = self.languages_for(products)
        routed: Route = classify(query, detected)

        if not tokens:
            return SearchResult(query, detected, routed.kind, routed.reason, "empty query")

        notes: list[str] = []
        if detected and products and detected not in available:
            notes.append(
                f"no {detected.upper()} manual for {', '.join(products)} "
                f"(available: {', '.join(sorted(available)).upper()})"
            )

        template = dict(products=products, doc_type=doc_type, chunk_type=chunk_type)
        if follow_user_language:
            policy: LanguagePlan = plan(detected, available)
            if not cross_lingual_fallback:  # baseline: hard language filter
                policy.tiers = [detected] if detected else [None]
                policy.cross_lingual = False
        else:
            policy = LanguagePlan(user_language=detected, tiers=[None], available=available)

        # The product name is enforced by the metadata filter, so its tokens must
        # not dominate BM25 (every hit already matches the product).
        name_tokens = {tok for product in products for tok in tokenize(product)}
        scoring_tokens = [tok for tok in tokens if tok not in name_tokens] or tokens
        expansion = expand_tokens(scoring_tokens)
        tiers_used: list[str] = []
        hits: list[Hit] = []
        # Accept the user's language when it has a usable ranking; only fall back
        # when it is empty or clearly weaker (same scorer, so scores compare).
        min_hits = max(2, k // 4)
        best_top = 0.0
        for tier in policy.tiers:
            allowed = self._allowed_ids(language=tier, **template)
            if allowed is not None and not allowed:
                tiers_used.append(f"{tier or 'any'}:0")
                continue
            dense_hits = self._dense_hits(query, allowed, k=max(k * 3, 12))
            tier_hits = self._rank_tier(
                scoring_tokens, expansion, allowed=allowed, k=max(k * 2, 12), dense=dense_hits
            )
            tiers_used.append(f"{tier or 'any'}:{len(tier_hits)}")
            if not tier_hits:
                continue
            top = tier_hits[0].score
            if not hits:
                hits, best_top = tier_hits, top
                if len(hits) >= min_hits:
                    break
                continue
            if top > best_top * 1.25:      # clearly better across languages
                hits, best_top = tier_hits, top
            break

        if self.retriever_note:
            notes.append(self.retriever_note)
        elif self.retriever is not None:
            provider = getattr(self.retriever, "provider", "vector")
            notes.append(f"dense retrieval on ({provider})")

        params: list[ParamHit] = []
        # "Qual è la velocità massima del FlashBot?" has no English lookup cue,
        # but it *is* a parameter question -- the lexicon knows "velocità".
        parameter_shaped = any(concepts_for(token) for token in scoring_tokens)
        if routed.is_lookup or parameter_shaped or any(c.isdigit() for c in query):
            params = self.params.search(
                query,
                k=5,
                language=policy.tiers[0] if follow_user_language else None,
                products=products,
                extra_tokens=expansion,
            )
            if not routed.is_lookup:
                # Keep only specification-style rows (a value with a number or
                # unit); prose rows belong to the chunk path, not the index.
                params = [
                    p for p in params
                    if p.record.get("value_number") is not None or p.record.get("unit")
                ]
            param_chunk_ids = [p.record.get("chunk_id") for p in params if p.record.get("chunk_id")]
            if param_chunk_ids:
                fused: dict[str, float] = defaultdict(float)
                for hit in hits:
                    fused[hit.chunk_id] += hit.score
                for rank, chunk_id in enumerate(dict.fromkeys(param_chunk_ids), start=1):
                    fused[chunk_id] += 1.0 / (RRF_K + rank)
                known = {hit.chunk_id for hit in hits}
                for chunk_id in fused:
                    if chunk_id in known or chunk_id not in self.by_id:
                        continue
                    chunk = self.by_id[chunk_id]
                    hits.append(
                        Hit(
                            chunk_id=chunk_id,
                            text=chunk.get("text", ""),
                            metadata=chunk.get("metadata", {}),
                            score=0.0,
                            rank=len(hits) + 1,
                            sources=["params"],
                        )
                    )
                for hit in hits:
                    hit.score = fused[hit.chunk_id]
                    if hit.chunk_id in param_chunk_ids and "params" not in hit.sources:
                        hit.sources.append("params")
                hits.sort(key=lambda hit: (-hit.score, hit.chunk_id))

        preference = ([detected] if detected else []) + [
            lang for lang in LANGUAGES if lang != detected
        ]
        if dedupe_translations:
            hits = self._dedupe_translations(hits, preference)
        if routed.is_advisory:
            hits = self._diversify(hits, k)
        hits = hits[:k]
        for rank, hit in enumerate(hits, start=1):
            hit.rank = rank

        if detected and any(hit.language != detected for hit in hits):
            cross = sorted({hit.language.upper() for hit in hits if hit.language != detected})
            notes.append(f"answer language {detected.upper()}; sources also in {', '.join(cross)}")

        coverage = 0.0
        if hits and scoring_tokens:
            # A question word counts as covered when one of the best hits has it
            # *or* any of its lexicon forms ("payload" -> "load capacity").
            hit_tokens = set(
                tokenize(" ".join(hit.text + " " + hit.metadata.get("section_title", "") for hit in hits[:3]))
            )
            unique_tokens = list(dict.fromkeys(scoring_tokens))
            covered = sum(
                1
                for token in unique_tokens
                if token in hit_tokens or any(form in hit_tokens for form in expand_tokens([token]))
            )
            coverage = covered / len(unique_tokens)

        return SearchResult(
            query=query,
            language=detected,
            route=routed.kind,
            route_reason=routed.reason,
            policy=policy.describe(),
            hits=hits,
            params=params,
            notes=notes,
            tiers_used=tiers_used,
            products=products,
            coverage=coverage,
        )
