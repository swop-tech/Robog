"""The retrieval engine: route -> language policy -> hybrid ranking -> citations.

This is the standalone retriever of **Bonus 01** (one module, no UI, JSON-ready
output) and the place where four review gaps meet:

* G1 exactness -- BM25 over the chunks + the structured parameter index;
* G2 question types -- the router (figure / advisory / lookup / default) +
  deterministic product profile cards across all 13 robots;
* G3 + Bonus 02 multilingual -- the language policy from ``retrieve.lang``;
* G6 visual lookup -- ``figure`` route ranking ``chunk_type="figure"`` chunks,
  preferring overview/appearance diagrams in the user's language because labels
  are rendered into the PNG pixels.

No embedding model is required: this runs offline, on any laptop. A dense
retriever (Chroma + embeddings, see ``retrieve/vector.py``) can be attached --
``--vector`` on the CLI, ``SearchEngine.from_build(..., vector="auto")``, or an
injected retriever -- and its ranked list is fused into the same
reciprocal-rank-fusion, honoring the same language policy.

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
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ingest.profiles import appearance_figure_score

from .bm25 import BM25
from .lang import LANGUAGES, LanguagePlan, detect_language, plan
from .lexicon import concepts_for, expand_tokens
from .params import ParamHit, ParamIndex
from .profiles import ADVISORY_FRAMING_WORDS, ProfileHit, ProfileIndex, has_scenario_signal
from .router import Route, route as classify
from .text import fold, tokenize
from .vector import ChromaRetriever, DenseHit, VectorStoreBusy, VectorStoreUnavailable

#: After a rate limit or a slow embedding call, answer from keywords for this
#: long before asking the vector store again.
DENSE_PAUSE_SECONDS = 60.0
_PAUSED_NOTE = "dense retrieval paused"

# Words every product name contains -- they say nothing about *which* product.
GENERIC_PRODUCT_WORDS = {
    "pudu", "pudubot", "user", "manual", "series", "robot",
}

# Visual-intent words asking *for* a picture/diagram rather than naming a
# specific component inside the picture.
FIGURE_META_WORDS: frozenset[str] = frozenset(
    """
    picture pictures photo photos image images figure figures diagram diagrams
    illustration illustrations view views look looks looking like show shows showing
    see display appearance exterior overview full whole clear nice complete
    bild bilder foto fotos abbildung abbildungen zeichnung ansicht aussehen
    erscheinungsbild aussenansicht uberblick zeig zeige zeigen sieht sehen aus
    mir mich uns ihnen vom beim mal ganz komplett
    immagine immagini figura diagramma schema vista aspetto panoramica
    mostra mostrami vedere vedi fammi fatto appare bella chiara completa intera
    dello della dell sul sulla sullo com cos dov qual
    """.split()
)

# Words indicating the user explicitly asks about an accessory rather than a robot.
ACCESSORY_QUERY_WORDS: frozenset[str] = frozenset(
    """
    accessory accessories station dock docking charger gateway pager beeper
    zubehor ladestation dockingstation arbeitsstation ladegerat rufsender
    accessori stazione caricabatterie
    """.split()
)

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
    def labels(self) -> list[str]:
        """Numbered callout labels printed in a figure chunk, if present."""
        if "\nLabels:" in self.text:
            raw = self.text.split("\nLabels:", 1)[1]
        elif self.text.startswith("Labels:"):
            raw = self.text.split("Labels:", 1)[1]
        else:
            return []
        return [item.strip() for item in raw.split(";") if item.strip()]

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
        payload = {
            "chunk_id": self.chunk_id,
            "score": round(self.score, 4),
            "text": self.text,
            "citation": self.citation,
            "language": self.language,
            "product": self.product,
            "chunk_type": self.chunk_type,
            "image": self.image,
            "labels": self.labels,
            "sources": self.sources,
            "metadata": self.metadata,
        }
        return payload


@dataclass
class SearchResult:
    query: str
    language: str | None
    route: str
    route_reason: str
    policy: str
    hits: list[Hit] = field(default_factory=list)
    params: list[ParamHit] = field(default_factory=list)
    profiles: list[ProfileHit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tiers_used: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    coverage: float = 0.0  # share of the question's content words found in top hits
    # An advisory question with no use case, environment or number ("which robot
    # is best?"): there is nothing to rank on, so the caller must ask for one.
    needs_scenario: bool = False

    @property
    def confidence(self) -> str:
        """How well the corpus matches the question -- 'none' when nothing matched.

        Used later by the answer composer to say "this is not in the manuals"
        instead of inventing something (faithfulness, review gap G5).
        """
        if not self.hits and not self.profiles:
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
        if not self.language:
            return False
        return any(hit.language != self.language for hit in self.hits) or any(
            prof.language != self.language for prof in self.profiles
        )

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
            "needs_scenario": self.needs_scenario,
            "notes": self.notes,
            "params": [
                {"parameter": p.parameter, "value": p.value, "citation": p.citation, **p.record}
                for p in self.params
            ],
            "profiles": [p.to_dict() for p in self.profiles],
            "hits": [hit.to_dict() for hit in self.hits],
        }


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------
class SearchEngine:
    """Hybrid retriever over the chunk corpus (+ parameter & profile indexes)."""

    def __init__(
        self,
        chunks: list[dict],
        params: ParamIndex | None = None,
        *,
        profiles: ProfileIndex | None = None,
        retriever: ChromaRetriever | None = None,
    ):
        self.chunks = chunks
        self.by_id = {chunk["id"]: chunk for chunk in chunks}
        self.params = params or ParamIndex([])
        self.profiles = (
            profiles
            if profiles is not None
            else ProfileIndex.from_chunks_and_params(chunks, self.params.records)
        )
        # Optional dense (vector) retriever. When None, the engine behaves
        # offline in keyword mode. Attach one to fuse Chroma's ranked list into
        # the same RRF fusion.
        self.retriever = retriever
        self.retriever_note: str | None = None
        self._dense_paused_until = 0.0
        self.params_note: str | None = None

        # Section title is repeated into the indexed text (a small but real
        # boost: the heading names the topic better than the body prose).
        docs = []
        for chunk in chunks:
            meta = chunk.get("metadata", {})
            heading = meta.get("section_title") or ""
            docs.append((chunk["id"], f"{heading} {heading} {heading} {chunk.get('text', '')}"))
        self.bm25 = BM25(docs)

        self.product_languages: dict[str, set[str]] = defaultdict(set)
        self.product_doc_type: dict[str, str] = {}
        self._alias_products: dict[str, set[str]] = defaultdict(set)
        for chunk in chunks:
            meta = chunk.get("metadata", {})
            prod = meta.get("product", "")
            self.product_languages[prod].add(meta.get("language", ""))
            if prod and prod not in self.product_doc_type:
                self.product_doc_type[prod] = meta.get("doc_type", "unknown")
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
        profiles_path: str | Path = "build/profiles.jsonl",
        vector: str | ChromaRetriever | None = None,
    ) -> "SearchEngine":
        """Build the engine from the pipeline's build directory."""
        chunks_file = Path(chunks_path)
        if not chunks_file.exists():
            raise SystemExit(
                f"{chunks_file} not found -- run 'python -m ingest.run --data data' first."
            )
        with chunks_file.open(encoding="utf-8") as fh:
            chunks = [json.loads(line) for line in fh if line.strip()]
        param_index = ParamIndex.from_jsonl(params_path)
        prof_file = Path(profiles_path)
        profile_index = (
            ProfileIndex.from_jsonl(prof_file)
            if prof_file.exists()
            else ProfileIndex.from_chunks_and_params(chunks, param_index.records)
        )
        engine = cls(chunks, param_index, profiles=profile_index)
        if not Path(params_path).exists():
            engine.params_note = (
                f"parameter index not found: {params_path}; "
                "exact parameter lookup disabled -- run python -m ingest.run"
            )
        if vector is None:
            return engine
        if isinstance(vector, str):
            if vector != "auto":
                raise ValueError(f"vector must be None, 'auto' or a retriever, got {vector!r}")
            try:
                engine.retriever = ChromaRetriever.connect(
                    corpus_ids=[chunk.get("id") for chunk in chunks]
                )
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

    def _matched_product_groups(self, query: str) -> list[list[str]]:
        """Non-overlapping product alias matches in `query`, longest alias first."""
        folded = fold(query)
        occupied: list[tuple[int, int]] = []
        groups: list[list[str]] = []
        for pattern, alias in self._alias_patterns:
            for match in pattern.finditer(folded):
                start, end = match.span()
                if any(os <= start and end <= oe for os, oe in occupied):
                    continue
                occupied.append((start, end))
                groups.append(sorted(self._alias_products[alias]))
        return groups

    def mentioned_products(self, query: str) -> list[str]:
        """Products named in the question (a code like "CC1" may mean several)."""
        found: list[str] = []
        for group in self._matched_product_groups(query):
            for product in group:
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

    def _resolve_products(self, query: str, expansion: list[str]) -> list[str]:
        """Resolve each product mention in `query` independently so 'BellaBot vs
        KettyBot Pro' keeps both products instead of dropping the 1-word name."""
        resolved: list[str] = []
        for group in self._matched_product_groups(query):
            for prod in self._disambiguate(group, expansion):
                if prod not in resolved:
                    resolved.append(prod)
        return resolved

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
        if self.retriever is None:
            return []
        if time.monotonic() < self._dense_paused_until:
            return []
        try:
            hits = self.retriever.search(query, k=k, allowed_ids=allowed)
            if (self.retriever_note or "").startswith(_PAUSED_NOTE):
                self.retriever_note = None           # the pause is over
            return hits
        except VectorStoreBusy as exc:
            # Temporary (rate limit, slow call): keep the retriever, answer from
            # keywords now, and try the vector store again after the pause.
            self._dense_paused_until = time.monotonic() + DENSE_PAUSE_SECONDS
            self.retriever_note = (
                f"{_PAUSED_NOTE} for {DENSE_PAUSE_SECONDS:.0f} s (keyword search answers): {exc}"
            )
            return []
        except VectorStoreUnavailable as exc:
            self.retriever = None
            self.retriever_note = f"dense retrieval disabled: {exc}"
            return []
        except Exception as exc:  # any retriever failure must not kill search
            self.retriever = None
            self.retriever_note = f"dense retrieval disabled: unexpected {type(exc).__name__}: {exc}"
            return []

    @staticmethod
    def _demote_figure_hits(dense_hits: list[DenseHit]) -> list[DenseHit]:
        """Figure chunks go last in the dense list unless a picture was asked for.

        476 of the 1,351 chunks are AI-written figure *descriptions*: long,
        generic prose ("the arrows show how to rotate the brush ...") that
        cosine similarity happily matches to almost any symptom or procedure
        question. They are context, not answers -- BM25 keeps them honest
        because a description rarely contains the term that was asked for, so
        the dense half has to be held to the same standard.
        """
        figures = [hit for hit in dense_hits if hit.metadata.get("chunk_type") == "figure"]
        if not figures:
            return dense_hits
        rest = [hit for hit in dense_hits if hit.metadata.get("chunk_type") != "figure"]
        return rest + figures

    def _lang_rank(self, lang: str, preference: list[str] | None) -> int:
        if not preference:
            return 0
        try:
            return preference.index(lang)
        except ValueError:
            return len(preference)

    def _rank_tier(
        self,
        tokens: list[str],
        expansion: list[str],
        *,
        allowed: set[str] | None,
        k: int,
        dense: list[DenseHit] | None = None,
        preference: list[str] | None = None,
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
            scores[hit.chunk_id] += 1.0 / (RRF_K + rank)
            sources[hit.chunk_id].append("vector")

        for chunk_id in list(scores):
            if chunk_id not in self.by_id and chunk_id not in dense_by_id:
                del scores[chunk_id]

        if len(tokens) > 1:
            query_set = set(tokens)
            for chunk_id in scores:
                _, meta = self._chunk_view(chunk_id, dense_by_id)
                title = meta.get("section_title") or ""
                title_tokens = set(tokenize(title))
                if title_tokens:
                    overlap = len(query_set & title_tokens) / len(query_set)
                    scores[chunk_id] *= 1 + 1.0 * overlap

        ranked = sorted(
            scores.items(),
            key=lambda item: (
                -round(item[1], 8),
                self._lang_rank(self._chunk_view(item[0], dense_by_id)[1].get("language", ""), preference),
                item[0],
            ),
        )[:candidate_k]
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

    def _rank_appearance_figures(
        self,
        allowed: set[str] | None,
        *,
        k: int,
        preference: list[str] | None = None,
        dense: list[DenseHit] | None = None,
    ) -> list[Hit]:
        """Rank ``chunk_type="figure"`` chunks when the user asks for a general
        picture/diagram of a product ("Show me a picture of the BellaBot")."""
        candidate_ids = allowed if allowed is not None else set(self.by_id)
        dense_by_id = {d.chunk_id: d for d in (dense or ())}
        dense_ranks = {d.chunk_id: rank for rank, d in enumerate(dense or (), start=1)}

        scored: list[tuple[str, float, list[str]]] = []
        for chunk_id in candidate_ids:
            chunk = self.by_id.get(chunk_id)
            if chunk is None:
                continue
            raw_app = appearance_figure_score(chunk)
            if raw_app < 0:
                continue
            score = max(0.005, (raw_app + 5.0) / 150.0)
            srcs = ["figure"]
            if chunk_id in dense_ranks:
                score += 0.5 / (RRF_K + dense_ranks[chunk_id])
                srcs.append("vector")
            scored.append((chunk_id, score, srcs))

        scored.sort(
            key=lambda item: (
                -round(item[1], 8),
                self._lang_rank(self.by_id[item[0]].get("metadata", {}).get("language", ""), preference),
                item[0],
            )
        )
        hits: list[Hit] = []
        for rank, (chunk_id, score, srcs) in enumerate(scored[: max(k * 3, 12)], start=1):
            text, meta = self._chunk_view(chunk_id, dense_by_id)
            hits.append(
                Hit(
                    chunk_id=chunk_id,
                    text=text,
                    metadata=meta,
                    score=score,
                    rank=rank,
                    sources=srcs,
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

    @staticmethod
    def _is_advisory_noise_chunk(meta: dict) -> bool:
        """Safety warnings (§1) and routine maintenance tables (§4/§6) should
        not masquerade as product recommendations on advisory queries."""
        sec_num = str(meta.get("section_number") or "")
        if sec_num.startswith("1"):
            return True
        if meta.get("chunk_type") == "table" and sec_num.startswith(("4", "6", "7")):
            return True
        return False

    def _product_fallback_hits(
        self,
        products: list[str],
        tiers: list[str | None],
        *,
        k: int,
        figure: bool = False,
    ) -> list[Hit]:
        """The named products' spec/overview chunks when the wording matched nothing.

        Profile cards already know which chunk carries a product's specifications
        and which carries its overview, so the fallback is a metadata lookup, not
        another search: no scoring, no guessing, and every hit still cites a real
        page. Accessories have no card -- for those take the first text chunks in
        document order, skipping boilerplate.

        ``tiers`` is the language policy's tier list, so the fallback never
        reaches a language the caller ruled out: with ``cross_lingual_fallback``
        off, an Italian FlashBot question must still find nothing, because there
        is no Italian FlashBot manual (that is the whole point of the policy).
        """
        allowed_langs = [lang for lang in tiers if lang] or None  # None = any
        wanted: list[str] = []

        def _add(chunk_id: str | None) -> None:
            if not chunk_id or chunk_id in wanted or chunk_id not in self.by_id:
                return
            if allowed_langs is not None:
                lang = self.by_id[chunk_id].get("metadata", {}).get("language")
                if lang not in allowed_langs:
                    return
            wanted.append(chunk_id)

        chunk_type = "figure" if figure else "text"
        card_keys = ("figure_chunk_id",) if figure else ("spec_chunk_id", "overview_chunk_id")

        for prod in products:
            cards = self.profiles.by_product.get(prod) or {}
            langs = [lang for lang in (allowed_langs or list(cards)) if lang in cards]
            record = cards[langs[0]] if langs else {}
            before = len(wanted)
            for key in card_keys:
                _add(record.get(key))
            if len(wanted) > before:
                continue
            candidates: list[str] = []
            for lang in (allowed_langs or [None]):
                allowed = self._allowed_ids(language=lang, products=[prod], chunk_type=chunk_type) or set()
                candidates.extend(allowed)
                if candidates and allowed_langs is not None:
                    break  # same-language-first, exactly like the tier loop
            if figure:
                candidates.sort(
                    key=lambda cid: (
                        -appearance_figure_score(self.by_id[cid]),
                        cid,
                    )
                )
            else:
                candidates.sort()
            picked = 0
            for chunk_id in candidates:
                if picked >= 2:
                    break
                if not figure and self._is_advisory_noise_chunk(self.by_id[chunk_id].get("metadata", {})):
                    continue
                _add(chunk_id)
                picked += 1

        hits: list[Hit] = []
        for chunk_id in wanted[: max(k, 2)]:
            chunk = self.by_id[chunk_id]
            hits.append(
                Hit(
                    chunk_id=chunk_id,
                    text=chunk.get("text", ""),
                    metadata=chunk.get("metadata", {}),
                    score=0.0,
                    rank=len(hits) + 1,
                    sources=["product-fallback"],
                )
            )
        return hits

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
        """Retrieve the best chunks (and parameter/profile rows) for a question."""
        query = (query or "").strip()
        tokens = tokenize(query)
        detected = language or detect_language(query)
        products = (
            [product]
            if product
            else self._resolve_products(query, expand_tokens(tokenize(query)))
        )
        available = self.languages_for(products)
        routed: Route = classify(query, detected)

        # Two product mentions are themselves the comparison signal: a cue list
        # can never carry every plural/verb form ("what are the differences
        # between A and B", "A und B im Vergleich"), and routing those to
        # `default` leaves BM25 with nothing but framing words to score -- which
        # used to return zero hits for two robots that are both in the corpus.
        if routed.kind == "default" and product is None:
            mentions = self._matched_product_groups(query)
            if len({tuple(group) for group in mentions}) >= 2:
                routed = Route("advisory", f"comparison: {len(mentions)} products named")

        if not tokens:
            return SearchResult(query, detected, routed.kind, routed.reason, "empty query")

        notes: list[str] = []
        if self.params_note:
            notes.append(self.params_note)
        if detected and products and detected not in available:
            notes.append(
                f"no {detected.upper()} manual for {', '.join(products)} "
                f"(available: {', '.join(sorted(available)).upper()})"
            )

        effective_chunk_type = chunk_type or ("figure" if routed.is_figure else None)
        explicit_accessory = (
            doc_type == "accessory"
            or any(self.product_doc_type.get(p) == "accessory" for p in products)
            or bool(set(tokens) & ACCESSORY_QUERY_WORDS)
        )
        has_robot_corpus = "robot" in self.product_doc_type.values()
        effective_doc_type = doc_type or (
            "robot" if (routed.is_advisory and not explicit_accessory and has_robot_corpus) else None
        )

        template = dict(
            products=products,
            doc_type=effective_doc_type,
            chunk_type=effective_chunk_type,
        )
        if follow_user_language:
            policy: LanguagePlan = plan(detected, available)
            if not cross_lingual_fallback:
                policy.tiers = [detected] if detected else [None]
                policy.cross_lingual = False
        else:
            policy = LanguagePlan(user_language=detected, tiers=[None], available=available)

        preference = ([detected] if detected else []) + [
            lang for lang in LANGUAGES if lang != detected
        ]

        # The product name is enforced by the metadata filter, so its tokens must
        # not dominate BM25 (every hit already matches the product). Include
        # translated forms of product-type words ("Charging Station" <-> "stazione di ricarica").
        raw_name_tokens = [tok for prod in products for tok in tokenize(prod)]
        name_tokens = set(expand_tokens(raw_name_tokens)) if raw_name_tokens else set()
        scoring_tokens = [tok for tok in tokens if tok not in set(raw_name_tokens)] or tokens
        advisory_pure_comparison = False
        if routed.is_advisory:
            advisory_content = [tok for tok in scoring_tokens if tok not in ADVISORY_FRAMING_WORDS]
            if advisory_content:
                scoring_tokens = advisory_content
            elif products:
                advisory_pure_comparison = True

        figure_topic_tokens = (
            [tok for tok in scoring_tokens if tok not in FIGURE_META_WORDS and tok not in name_tokens]
            if routed.is_figure
            else scoring_tokens
        )
        active_tokens = figure_topic_tokens if routed.is_figure else scoring_tokens
        expansion = expand_tokens(active_tokens) if active_tokens else expand_tokens(scoring_tokens)

        tiers_used: list[str] = []
        hits: list[Hit] = []
        min_hits = 1 if routed.is_figure else max(2, k // 4)
        best_top = 0.0

        for tier in policy.tiers:
            allowed = self._allowed_ids(language=tier, **template)
            if allowed is not None and not allowed:
                tiers_used.append(f"{tier or 'any'}:0")
                continue
            dense_hits = self._dense_hits(query, allowed, k=max(k * 3, 12))
            if dense_hits and not routed.is_figure:
                dense_hits = self._demote_figure_hits(dense_hits)

            if routed.is_figure and not figure_topic_tokens:
                tier_hits = self._rank_appearance_figures(
                    allowed, k=max(k * 2, 12), preference=preference, dense=dense_hits
                )
            else:
                tier_hits = self._rank_tier(
                    active_tokens,
                    expansion,
                    allowed=allowed,
                    k=max(k * 2, 12),
                    dense=dense_hits,
                    preference=preference,
                )
                if routed.is_figure and tier_hits:
                    for hit in tier_hits:
                        chunk = self.by_id.get(hit.chunk_id)
                        if chunk is not None:
                            app_bonus = max(0.0, appearance_figure_score(chunk))
                            hit.score *= 1.0 + 0.025 * app_bonus
                    tier_hits.sort(
                        key=lambda h: (
                            -round(h.score, 8),
                            self._lang_rank(h.language, preference),
                            h.chunk_id,
                        )
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
            if top > best_top * 1.25:
                hits, best_top = tier_hits, top
            break

        # Nothing matched the question's wording, but the question did name
        # products we have: answering "not in the manuals" there is the worst
        # possible reply -- the corpus holds those products' specification,
        # overview and profile sections. Fall back to them and say so.
        if not hits and products:
            hits = self._product_fallback_hits(
                products, policy.tiers, k=max(k, 3), figure=routed.is_figure
            )
            if hits:
                tiers_used.append("product-fallback")
                notes.append(
                    "no passage matched the question's wording -- showing the "
                    "named product's specification and overview sections"
                )

        if self.retriever_note:
            notes.append(self.retriever_note)
        elif self.retriever is not None:
            provider = getattr(self.retriever, "provider", "vector")
            notes.append(f"dense retrieval on ({provider})")

        # -- structured product profile cards (gap G2) ----------------------
        profiles: list[ProfileHit] = []
        if (
            routed.is_advisory
            and not explicit_accessory
            and not products
            and not has_scenario_signal(query)
        ):
            # "Which robot is best?" -- every card would score identically, so a
            # ranking here is a guess. Return no robots; the composer asks back.
            notes.append("advisory question names no scenario -- asking which one")
            return SearchResult(
                query=query,
                language=detected,
                route=routed.kind,
                route_reason=routed.reason,
                policy=policy.describe(),
                notes=notes,
                tiers_used=tiers_used,
                products=products,
                coverage=0.0,
                needs_scenario=True,
            )
        if routed.is_advisory and not explicit_accessory and self.profiles.records:
            profiles = self.profiles.search(
                query,
                k=max(k, 6),
                language=detected,
                products=products or None,
                follow_user_language=follow_user_language,
                cross_lingual_fallback=cross_lingual_fallback,
                extra_tokens=expansion,
            )
            if profiles:
                fused_advisory: dict[str, float] = defaultdict(float)
                for hit in hits:
                    penalty = 0.35 if self._is_advisory_noise_chunk(hit.metadata) else 1.0
                    fused_advisory[hit.chunk_id] += hit.score * penalty

                known_ids = {hit.chunk_id for hit in hits}
                profile_sourced: set[str] = set()
                for p_rank, prof in enumerate(profiles, start=1):
                    rec = prof.record
                    ov_cid = rec.get("overview_chunk_id")
                    spec_cid = rec.get("spec_chunk_id")
                    ov_sec = str(
                        self.by_id.get(ov_cid or "", {}).get("metadata", {}).get("section") or ""
                    )
                    ov_is_safety_merged = ov_sec.startswith("1.")
                    pairs = (
                        ((spec_cid, 1.15), (ov_cid, 0.35))
                        if ov_is_safety_merged
                        else ((ov_cid or spec_cid, 1.15), (spec_cid, 0.7))
                    )
                    for cid, weight in pairs:
                        if not cid or cid not in self.by_id:
                            continue
                        fused_advisory[cid] += weight / (RRF_K + p_rank)
                        profile_sourced.add(cid)
                        if cid not in known_ids:
                            chunk = self.by_id[cid]
                            hits.append(
                                Hit(
                                    chunk_id=cid,
                                    text=chunk.get("text", ""),
                                    metadata=chunk.get("metadata", {}),
                                    score=0.0,
                                    rank=len(hits) + 1,
                                    sources=["profile"],
                                )
                            )
                            known_ids.add(cid)

                for hit in hits:
                    hit.score = fused_advisory[hit.chunk_id]
                    if hit.chunk_id in profile_sourced and "profile" not in hit.sources:
                        hit.sources.append("profile")
                hits.sort(
                    key=lambda hit: (
                        -round(hit.score, 8),
                        self._lang_rank(hit.language, preference),
                        hit.chunk_id,
                    )
                )

        # -- structured parameter index (gap G1) ----------------------------
        params: list[ParamHit] = []
        parameter_shaped = any(concepts_for(token) for token in scoring_tokens)
        if not routed.is_figure and (routed.is_lookup or parameter_shaped or any(c.isdigit() for c in query)):
            params = self.params.search(
                query,
                k=5,
                language=policy.tiers[0] if follow_user_language else None,
                products=products,
                extra_tokens=expansion,
            )
            if not routed.is_lookup:
                params = [
                    p for p in params
                    if p.record.get("value_number") is not None or p.record.get("unit")
                ]
            if routed.is_advisory and profiles:
                top_profile_products = {p.product for p in profiles[:4]}
                params = [p for p in params if p.record.get("product") in top_profile_products]
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
                hits.sort(
                    key=lambda hit: (
                        -round(hit.score, 8),
                        self._lang_rank(hit.language, preference),
                        hit.chunk_id,
                    )
                )

        if dedupe_translations:
            hits = self._dedupe_translations(hits, preference)
        if routed.is_advisory or (routed.is_figure and not products and not figure_topic_tokens):
            per_prod = 1 if routed.is_figure else 2
            hits = self._diversify(hits, k, per_product=per_prod)
        hits = hits[:k]
        for rank, hit in enumerate(hits, start=1):
            hit.rank = rank

        cross_langs = {
            hit.language.upper() for hit in hits if detected and hit.language and hit.language != detected
        } | {
            prof.language.upper() for prof in profiles if detected and prof.language and prof.language != detected
        }
        if detected and cross_langs:
            notes.append(f"answer language {detected.upper()}; sources also in {', '.join(sorted(cross_langs))}")

        coverage = 0.0
        if routed.is_figure and not figure_topic_tokens:
            if hits:
                coverage = 1.0 if products else 0.8
        elif advisory_pure_comparison and profiles:
            matched_prods = {p.product for p in profiles}
            coverage = sum(1 for p in products if p in matched_prods) / max(len(products), 1)
        elif hits and active_tokens:
            evidence_parts = [hit.text + " " + hit.metadata.get("section_title", "") for hit in hits[:3]]
            if profiles:
                evidence_parts.extend(prof.record.get("card_text", "") + " " + " ".join(prof.record.get("environments", ())) for prof in profiles[:3])
            hit_tokens = set(tokenize(" ".join(evidence_parts)))
            unique_tokens = list(dict.fromkeys(active_tokens))
            covered = sum(
                1
                for token in unique_tokens
                if token in hit_tokens or any(form in hit_tokens for form in expand_tokens([token]))
            )
            coverage = covered / len(unique_tokens)
        elif profiles and not active_tokens:
            coverage = 0.8

        return SearchResult(
            query=query,
            language=detected,
            route=routed.kind,
            route_reason=routed.reason,
            policy=policy.describe(),
            hits=hits,
            params=params,
            profiles=profiles,
            notes=notes,
            tiers_used=tiers_used,
            products=products,
            coverage=coverage,
        )
