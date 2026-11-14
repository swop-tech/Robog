"""The API's service layer: ``SearchEngine`` + ``AnswerComposer`` + media URLs.

The routes in ``api/app.py`` stay thin (validation, status codes, JSON) and
delegate here. This is the only place that knows

* how the engine and the composer are built (and that a missing
  ``build/chunks.jsonl`` is a 503 with instructions, not a crash);
* how a corpus ``image`` path becomes an ``image_url`` a browser can fetch
  (``api/media.py``);
* which shape ``/search``, ``/ask`` and ``/products`` answer in -- the JSON
  contract documented in ``docs/retrieval.md`` §"to_dict() is the contract" and
  ``docs/answering.md`` §3, extended with ``image_url`` per
  ``docs/pipeline-review.md`` §9a.

``SearchResult.to_dict()`` and ``Answer.to_dict()`` are returned *as they are*,
with keys added, never removed or renamed -- a client written against the CLI's
``--json`` output works unchanged against the HTTP API.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from answer.composer import Answer, AnswerComposer
from retrieve.engine import SearchEngine
from retrieve.env import load_dotenv
from retrieve.lexicon import expand_tokens
from retrieve.params import ParamHit
from retrieve.text import fold, tokenize
from retrieve.vector import ChromaRetriever, VectorStoreUnavailable

from .config import Settings
from .media import MediaStore

# A question longer than this is a paste, not a question.
MAX_QUESTION_CHARS = 2000


@dataclass
class ServiceStatus:
    """What ``GET /health`` reports."""

    ready: bool
    chunks: int
    params: int
    profiles: int
    products: int
    media_documents: int
    media_files: int
    provider: dict
    vector: str | None
    detail: str | None = None
    #: True only when a Chroma index is actually attached and being fused.
    vector_active: bool = False
    #: Why dense retrieval is not active (``None`` when it is, or when it is off).
    vector_detail: str | None = None

    def to_dict(self) -> dict:
        return {
            "ready": self.ready,
            "chunks": self.chunks,
            "params": self.params,
            "profiles": self.profiles,
            "products": self.products,
            "media_documents": self.media_documents,
            "media_files": self.media_files,
            "provider": self.provider,
            "vector": self.vector,
            "vector_active": self.vector_active,
            "vector_detail": self.vector_detail,
            "detail": self.detail,
        }


class RobogService:
    """Engine + composer + media index behind one object."""

    def __init__(
        self,
        settings: Settings,
        *,
        engine: SearchEngine | None = None,
        media: MediaStore | None = None,
        error: str | None = None,
    ):
        self.settings = settings
        self.engine = engine
        self.media = media if media is not None else MediaStore.build(settings.data_dir)
        if engine is not None:
            # The chunk index is authoritative about which doc_id owns a folder.
            self.media.register_chunks(engine.chunks)
        self.error = error
        self._composer = (
            AnswerComposer(engine, k=settings.default_k) if engine is not None else None
        )
        self._composers: dict[tuple[str | None, str | None], AnswerComposer] = {}
        self._lock = threading.Lock()

    # -- construction ------------------------------------------------------
    @classmethod
    def from_settings(cls, settings: Settings) -> "RobogService":
        """Build the engine from the pipeline's build directory (never raises).

        A missing or broken index is not fatal: the media endpoint only needs
        ``data/``, so it keeps serving while the other routes answer 503.
        """
        load_dotenv()
        media = MediaStore.build(settings.data_dir)
        retriever, vector_note = _connect_vector(settings)
        try:
            engine = SearchEngine.from_build(
                settings.chunks_path,
                settings.params_path,
                profiles_path=settings.profiles_path,
                vector=retriever,
            )
            if vector_note:
                engine.retriever_note = vector_note
        except SystemExit as exc:  # from_build exits when chunks.jsonl is missing
            engine, error = None, str(exc)
        except Exception as exc:  # a broken index must not take the API down
            engine, error = None, f"{type(exc).__name__}: {exc}"
        if engine is None:
            # Without the engine the chunk metadata is still the best map of
            # doc_id -> manual folder, so read it just for that.
            media.register_chunks(_read_chunk_metadata(settings.chunks_path))
            return cls(settings, engine=None, media=media, error=error)
        return cls(settings, engine=engine, media=media)

    @property
    def ready(self) -> bool:
        return self.engine is not None

    def require_engine(self) -> SearchEngine:
        if self.engine is None:
            raise ServiceUnavailable(self.error or "search index not built")
        return self.engine

    def composer_for(self, provider: str | None = None, model: str | None = None) -> AnswerComposer:
        """The shared composer, or a cached one for an explicit provider."""
        if self._composer is None:
            raise ServiceUnavailable(self.error or "search index not built")
        if not provider and not model:
            return self._composer
        key = (provider, model)
        with self._lock:
            composer = self._composers.get(key)
            if composer is None:
                assert self.engine is not None
                composer = AnswerComposer(
                    self.engine,
                    provider=provider,
                    model=model,
                    k=self.settings.default_k,
                )
                self._composers[key] = composer
            return composer

    def provider_info(self) -> dict:
        if self._composer is None:
            return {"provider": None, "model": None, "offline": True, "reason": self.error}
        return self._composer.provider_info

    # -- media -------------------------------------------------------------
    def image_url(self, doc_id: str | None, image: str | None, base: str = "") -> str | None:
        return self.media.url_for(doc_id, image, prefix=self.settings.media_prefix, base=base)

    def resolve_media(self, doc_id: str, filename: str) -> Path | None:
        return self.media.resolve(doc_id, filename)

    # -- GET /search -------------------------------------------------------
    def search(
        self,
        q: str,
        *,
        k: int | None = None,
        language: str | None = None,
        product: str | None = None,
        doc_type: str | None = None,
        chunk_type: str | None = None,
        follow_user_language: bool = True,
        cross_lingual_fallback: bool = True,
        dedupe_translations: bool = True,
        base: str = "",
    ) -> dict:
        """Hybrid retrieval as ``SearchResult.to_dict()`` + ``image_url`` per hit."""
        engine = self.require_engine()
        result = engine.search(
            q,
            k=min(k or self.settings.default_k, self.settings.max_k),
            language=language,
            product=product,
            doc_type=doc_type,
            chunk_type=chunk_type,
            follow_user_language=follow_user_language,
            cross_lingual_fallback=cross_lingual_fallback,
            dedupe_translations=dedupe_translations,
        )
        payload = result.to_dict()
        for hit in payload.get("hits", []):
            hit["image_url"] = self.image_url(
                hit.get("metadata", {}).get("doc_id"), hit.get("image"), base
            )
        for profile in payload.get("profiles", []):
            profile["image_url"] = self.image_url(
                _profile_doc_id(profile), profile.get("image"), base
            )
        payload["media"] = {
            "prefix": self.settings.media_prefix,
            "documents": len(self.media),
            "note": "fetch a hit's image_url to get the PNG (GET /media/{doc_id}/{filename})",
        }
        return payload

    # -- POST /ask ---------------------------------------------------------
    def ask(
        self,
        question: str,
        *,
        k: int | None = None,
        language: str | None = None,
        product: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        follow_user_language: bool = True,
        cross_lingual_fallback: bool = True,
        include_search: bool = False,
        include_evidence: bool = False,
        base: str = "",
    ) -> dict:
        """The full RAG pipeline: retrieve -> generate -> validate -> answer."""
        composer = self.composer_for(provider, model)
        answer = composer.answer(
            question,
            k=min(k, self.settings.max_k) if k else None,
            language=language,
            product=product,
            follow_user_language=follow_user_language,
            cross_lingual_fallback=cross_lingual_fallback,
        )
        return self._ask_payload(
            answer, base=base, include_search=include_search, include_evidence=include_evidence
        )

    def _ask_payload(
        self,
        answer: Answer,
        *,
        base: str = "",
        include_search: bool = False,
        include_evidence: bool = False,
    ) -> dict:
        raw = answer.to_dict()
        resolver = _ImageResolver(answer, self.engine)
        citations = []
        for citation in raw.get("citations", []):
            item = dict(citation)
            item["image_url"] = self.image_url(
                resolver.doc_id_for_ref(citation.get("n"), citation.get("image"),
                                         citation.get("product")),
                citation.get("image"),
                base,
            )
            citations.append(item)
        figures = []
        for figure in raw.get("figures", []):
            item = dict(figure)
            item["image_url"] = self.image_url(
                resolver.doc_id_for_image(figure.get("image"), figure.get("product")),
                figure.get("image"),
                base,
            )
            figures.append(item)

        result = answer.result
        params = [_param_row(hit) for hit in (result.params if result else [])]
        profiles = []
        for profile in (result.profiles if result else []):
            card = profile.to_dict()
            card["image_url"] = self.image_url(
                _profile_doc_id(card), card.get("image"), base
            )
            profiles.append(card)

        payload: dict = {
            "question": answer.question,
            "answer": raw["answer"],
            "language": raw["language"],
            "route": raw["route"],
            "citations": citations,
            "figures": figures,
            "params": params,
            "profiles": profiles,
            "verification": _verification(answer),
            "cross_lingual": raw["cross_lingual"],
            # -- audit trail (unchanged from Answer.to_dict()) ---------------
            "provider": raw["provider"],
            "model": raw["model"],
            "grounded": raw["grounded"],
            "verified": raw["verified"],
            "source_languages": raw["source_languages"],
            "language_instruction": raw["language_instruction"],
            "validation": raw["validation"],
            "notes": raw["notes"],
            "latency_ms": raw["latency_ms"],
        }
        if include_search:
            search = raw.get("search") or {}
            for hit in search.get("hits", []):
                hit["image_url"] = self.image_url(
                    hit.get("metadata", {}).get("doc_id"), hit.get("image"), base
                )
            payload["search"] = search
        else:
            payload["search"] = None
        payload["evidence"] = raw.get("evidence") if include_evidence else None
        return payload

    # -- GET /products -----------------------------------------------------
    def catalog(
        self,
        *,
        language: str = "en",
        product: str | None = None,
        parameter: str | None = None,
        q: str | None = None,
        doc_type: str | None = None,
        include_params: bool = True,
        params_per_product: int = 20,
        k: int = 10,
        base: str = "",
    ) -> dict:
        """Structured catalog: profile cards + the parameter index per product."""
        engine = self.require_engine()
        wanted = product.strip() if product and product.strip() else None
        needle = parameter.strip() if parameter and parameter.strip() else None
        free = q.strip() if q and q.strip() else None

        products = [
            entry
            for entry in self._product_entries()
            if (not doc_type or entry["doc_type"] == doc_type)
            and (not wanted or _matches_product(entry["product"], wanted))
        ]

        # A free-text parameter question is answered by the parameter index's
        # own ranker (same code path as the `lookup` route): product names in
        # the query become filters, and the domain lexicon supplies the other
        # surface forms of a concept ("payload" -> "Load capacity" / "Zuladung").
        param_hits: list[dict] = []
        if free:
            mentioned = engine.mentioned_products(free)
            hits = engine.params.search(
                free,
                k=k,
                language=language,
                products=[p["product"] for p in products] if wanted else (mentioned or None),
                extra_tokens=expand_tokens(tokenize(free)),
            )
            param_hits = [_param_row(hit) for hit in hits]

        for entry in products:
            card = self._profile_card(entry["product"], language, base)
            entry["profile"] = card
            rows = self._param_rows(
                entry["product"],
                language=language,
                needle=needle,
                limit=None if needle else params_per_product,
            )
            entry["parameters"] = rows if include_params else []
            entry["parameter_count"] = self._param_count(entry["product"], language)

        return {
            "language": language,
            "count": len(products),
            "products": products,
            "available_languages": sorted(engine.languages_for()),
            "parameters_total": len(engine.params.records),
            "profiles_total": len(engine.profiles.records),
            "parameter_search": {
                "query": free,
                "matches": param_hits,
            }
            if free
            else None,
            "filters": {
                "product": wanted,
                "parameter": needle,
                "doc_type": doc_type,
                "include_params": include_params,
                "params_per_product": None if needle else params_per_product,
            },
        }

    # -- GET /health -------------------------------------------------------
    def status(self) -> ServiceStatus:
        engine = self.engine
        return ServiceStatus(
            ready=engine is not None,
            chunks=len(engine.chunks) if engine else 0,
            params=len(engine.params.records) if engine else 0,
            profiles=len(engine.profiles.records) if engine else 0,
            products=len(engine.product_languages) if engine else 0,
            media_documents=len(self.media),
            media_files=self.media.file_count(),
            provider=self.provider_info(),
            vector=self.settings.vector,
            detail=self.error,
            vector_active=bool(engine is not None and engine.retriever is not None),
            vector_detail=(
                (getattr(engine, "retriever_note", None) or None)
                if engine is not None and engine.retriever is None and self.settings.vector
                else None
            ),
        )

    # -- catalog helpers ---------------------------------------------------
    def _product_entries(self) -> list[dict]:
        """One catalog entry per product in the corpus (robots + accessories)."""
        engine = self.engine
        if engine is None:
            return []
        documents: dict[str, set[str]] = defaultdict(set)
        for chunk in engine.chunks:
            meta = chunk.get("metadata", {})
            if meta.get("product"):
                documents[meta["product"]].add(meta.get("doc_id", ""))
        entries = []
        for product in sorted(documents):
            languages = sorted(lang for lang in engine.product_languages.get(product, ()) if lang)
            entries.append(
                {
                    "product": product,
                    "doc_type": engine.product_doc_type.get(product, "unknown"),
                    "languages": languages,
                    "documents": sorted(d for d in documents[product] if d),
                }
            )
        return entries

    def _profile_card(self, product: str, language: str, base: str = "") -> dict | None:
        """The localized profile card of one product (cross-lingual fallback)."""
        engine = self.engine
        if engine is None:
            return None
        hits = engine.profiles.catalog(language=language)
        for hit in hits:
            if hit.product != product:
                continue
            card = hit.to_dict()
            card["image_url"] = self.image_url(_profile_doc_id(card), card.get("image"), base)
            return card
        return None

    def _param_rows(
        self,
        product: str,
        *,
        language: str = "en",
        needle: str | None = None,
        limit: int | None = 20,
    ) -> list[dict]:
        """Parameter rows of one product, in the requested language."""
        engine = self.engine
        if engine is None:
            return []
        rows = engine.params.by_product.get(product, [])
        by_lang: dict[str, list[int]] = defaultdict(list)
        for index in rows:
            by_lang[engine.params.records[index].get("language", "")].append(index)
        # The corpus' language policy: the user's language first, then EN, then
        # whatever the manual has (the FlashBot/GW1/Pager manuals have no IT).
        chosen = by_lang.get(language)
        if not chosen:
            for fallback in ("en", "de", "it"):
                if fallback != language and by_lang.get(fallback):
                    chosen = by_lang[fallback]
                    break
        records = [engine.params.records[index] for index in (chosen or [])]
        if needle:
            folded = fold(needle)
            wanted_tokens = set(expand_tokens(tokenize(needle)))
            records = [
                record
                for record in records
                if _matches_parameter(record, folded, wanted_tokens)
            ]
        records.sort(
            key=lambda r: (
                str(r.get("section_number") or "9999"),
                str(r.get("parameter") or ""),
            )
        )
        if limit is not None and limit > 0:
            records = records[:limit]
        return [_param_row(ParamHit(record=record, score=1.0)) for record in records]

    def _param_count(self, product: str, language: str = "en") -> int:
        engine = self.engine
        if engine is None:
            return 0
        indexes = engine.params.by_product.get(product, [])
        same = [
            i for i in indexes if engine.params.records[i].get("language", "") == language
        ]
        return len(same or indexes)


class ServiceUnavailable(Exception):
    """The index is not built -- the routes turn this into HTTP 503."""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _read_chunk_metadata(path: Path) -> list[dict]:
    """chunks.jsonl reduced to what the media index needs (doc_id -> folder).

    Only used when the engine itself failed to build -- normally the engine's
    own chunks are the source of truth.
    """
    import json

    if not Path(path).exists():
        return []
    chunks: list[dict] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                meta = json.loads(line).get("metadata", {})
                chunks.append({"metadata": meta})
    except (OSError, ValueError):
        return []
    return chunks


def _connect_vector(settings: Settings) -> tuple[ChromaRetriever | None, str | None]:
    """``(retriever, note)``: the dense retriever, or why there is none.

    Never raises -- keyword retrieval keeps working; ``/health`` reports the
    note as ``vector_detail`` so a keyword-only server is never a silent one.
    """
    if not settings.vector:
        return None, None
    try:
        return ChromaRetriever.connect(), None
    except VectorStoreUnavailable as exc:
        return None, f"dense retrieval disabled: {exc}"
    except Exception as exc:
        return None, f"dense retrieval disabled: unexpected {type(exc).__name__}: {exc}"


def _param_row(hit: ParamHit) -> dict:
    """A parameter index row as the API returns it (index row + citation)."""
    record = dict(hit.record)
    return {
        "parameter": record.get("parameter", ""),
        "value": record.get("value", ""),
        "unit": record.get("unit", ""),
        "value_number": record.get("value_number"),
        "column": record.get("column", ""),
        "product": record.get("product", ""),
        "language": record.get("language", ""),
        "page": record.get("page") or record.get("pdf_page"),
        "section": record.get("section", ""),
        "section_number": record.get("section_number", ""),
        "chunk_id": record.get("chunk_id", ""),
        "citation": hit.citation,
    }


def _profile_doc_id(card: dict) -> str | None:
    """A profile card names its manual folder and language -> its doc_id."""
    from ingest.chunk import doc_id_for

    manual = card.get("manual")
    language = card.get("language")
    if not manual or not language:
        return None
    return doc_id_for(manual, language)


def _matches_product(product: str, wanted: str) -> bool:
    """``?product=`` accepts the full name, a model code or a substring."""
    folded_product, folded_wanted = fold(product), fold(wanted)
    if folded_wanted in (folded_product, folded_product.replace("_", " ")):
        return True
    return folded_wanted in folded_product or folded_product in folded_wanted


def _matches_parameter(record: dict, folded: str, tokens: set[str]) -> bool:
    """Does this row name the parameter the caller asked for?

    ``tokens`` is the lexicon-expanded query, so ``?parameter=payload`` finds
    the manuals that print ``Load capacity`` / ``Ladekapazität`` / ``Carico``.
    """
    text = f"{record.get('parameter', '')} {record.get('column', '')}"
    if folded and folded in fold(text):
        return True
    return bool(tokens & set(tokenize(text)))


def _verification(answer: Answer) -> dict:
    """The compact G5 verdict a client shows next to the answer."""
    report = answer.validation
    if report is None:
        return {
            "grounded": False,
            "verified": False,
            "summary": "answer not validated",
            "issues": [],
        }
    try:
        summary = report.summary(answer.language)
    except Exception:  # a missing localized string must not break /ask
        summary = f"{report.sentences_cited}/{report.sentences_checked} claims cited"
    checks_run = bool(getattr(report, "checks_run", True))
    if not checks_run:
        state = "no-evidence"      # 0/0 passes every check vacuously -- not a pass
    elif not report.ok:
        state = "failed"
    elif report.verified:
        state = "verified"
    else:
        state = "repaired"
    return {
        "grounded": answer.grounded,
        "verified": answer.verified,
        "checked": checks_run,
        "state": state,
        "summary": summary,
        "citation_rate": round(report.citation_rate, 3),
        "numeric_rate": round(report.numeric_rate, 3),
        "quote_rate": round(report.quote_rate, 3),
        "sentences_checked": report.sentences_checked,
        "sentences_cited": report.sentences_cited,
        "numbers_checked": report.numbers_checked,
        "numbers_verified": report.numbers_verified,
        "numbers_unverified": report.numbers_checked - report.numbers_verified,
        "quotes_checked": report.quotes_checked,
        "quotes_verified": report.quotes_verified,
        "issues": [issue.to_dict() for issue in report.issues],
    }


class _ImageResolver:
    """Which document owns an evidence image (filenames repeat across manuals).

    ``images/en-p006R-fig01.png`` exists in several manuals, so an image path
    alone is ambiguous: the doc_id comes from the chunk that produced the
    reference, with ``(image, product)`` and then a unique-path lookup as
    fallbacks.
    """

    def __init__(self, answer: Answer, engine: SearchEngine | None):
        self._by_ref: dict[int, str] = {}
        self._by_image_product: dict[tuple[str, str], str] = {}
        self._by_image: dict[str, set[str]] = defaultdict(set)

        for ref in answer.evidence.refs if answer.evidence else []:
            doc_id = _doc_id_of_chunk(engine, getattr(ref, "chunk_id", None))
            if doc_id:
                self._by_ref[ref.n] = doc_id
                if ref.image:
                    self._note(ref.image, getattr(ref, "product", "") or "", doc_id)
        for hit in answer.result.hits if answer.result else []:
            meta = getattr(hit, "metadata", {}) or {}
            doc_id = meta.get("doc_id")
            image = getattr(hit, "image", None)
            if doc_id and image:
                self._note(image, getattr(hit, "product", "") or "", doc_id)

    def _note(self, image: str, product: str, doc_id: str) -> None:
        self._by_image[image].add(doc_id)
        if product:
            self._by_image_product.setdefault((image, product), doc_id)

    def doc_id_for_ref(self, n: int | None, image: str | None, product: str | None) -> str | None:
        if n is not None and n in self._by_ref:
            return self._by_ref[n]
        return self.doc_id_for_image(image, product)

    def doc_id_for_image(self, image: str | None, product: str | None) -> str | None:
        if not image:
            return None
        if product and (image, product) in self._by_image_product:
            return self._by_image_product[(image, product)]
        doc_ids = self._by_image.get(image)
        if doc_ids and len(doc_ids) == 1:
            return next(iter(doc_ids))
        # Ambiguous (the same filename exists in several manuals) -> no URL
        # rather than a wrong picture.
        return None


def _doc_id_of_chunk(engine: SearchEngine | None, chunk_id: str | None) -> str | None:
    if engine is None or not chunk_id:
        return None
    chunk = engine.by_id.get(chunk_id)
    if chunk is None:
        return None
    return chunk.get("metadata", {}).get("doc_id")
