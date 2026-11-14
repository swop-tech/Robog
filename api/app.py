"""The FastAPI service: ``/search``, ``/ask``, ``/products``, ``/media``.

Run it::

    python -m ingest.run --data data          # build the indexes once
    python -m api                             # http://0.0.0.0:8000  (docs at /docs)

and talk to it from anything -- this is the Bonus 01 claim: the retrieval
engine is a service, and the web chat is only its first client::

    curl 'localhost:8000/search?q=Wieviel+Zuladung+hat+der+FlashBot%3F&k=3'
    curl -X POST localhost:8000/ask -H 'content-type: application/json' \
         -d '{"question": "Qual è la velocità massima del FlashBot?"}'
    curl 'localhost:8000/products?language=it&product=FlashBot&parameter=payload'
    curl -OJ localhost:8000/media/flashbot_user_manual_v1_0-multilanguage-it/it-p026L-fig01.png

Design notes:

* **Thin routes.** Validation, status codes and JSON live here; retrieval,
  generation and validation live in ``retrieve/`` and ``answer/``. The routes
  are ``async`` and push the CPU-bound work into Starlette's threadpool, so a
  slow question cannot block the event loop.
* **The contract is the CLI's.** ``/search`` answers with
  ``SearchResult.to_dict()`` and ``/ask`` with ``Answer.to_dict()`` -- keys are
  added (``image_url``, ``params``, ``verification``), never renamed.
* **Degraded, not dead.** A missing ``build/chunks.jsonl`` yields HTTP 503 with
  the command to run; ``/media`` keeps serving because it only needs ``data/``.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from answer.llm import DEFAULT_LLM_MODEL
from retrieve.lang import LANGUAGES

from .config import API_DESCRIPTION, API_TITLE, API_VERSION, Settings
from .service import MAX_QUESTION_CHARS, RobogService, ServiceUnavailable

#: The corpus is EN/DE/IT (``retrieve.lang.LANGUAGES``).
Language = Literal["en", "de", "it"]
DocType = Literal["robot", "accessory"]
ChunkType = Literal["text", "table", "figure"]
Provider = Literal["gemini", "offline", "auto"]

_STARTED_AT = time.time()
_LANG_HELP = f"question language ({' | '.join(LANGUAGES)}); default: detected from the text"


class AskRequest(BaseModel):
    """Body of ``POST /ask``."""

    question: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARS)
    k: Optional[int] = Field(None, ge=1, le=50, description="chunks to retrieve")
    language: Optional[Language] = Field(None, description=_LANG_HELP)
    product: Optional[str] = Field(None, description="restrict retrieval to one product")
    provider: Optional[Provider] = Field(
        None, description="override the answer generator for this request"
    )
    model: Optional[str] = Field(None, description=f"LLM model ID (default {DEFAULT_LLM_MODEL})")
    follow_user_language: bool = Field(True, description="false = naive all-languages baseline")
    cross_lingual_fallback: bool = True
    include_search: bool = Field(False, description="embed the raw retrieval result")
    include_evidence: bool = Field(False, description="embed the numbered evidence")


class ApiState:
    """What lives on ``app.state`` for the lifetime of the process."""

    def __init__(self, settings: Settings, service: RobogService):
        self.settings = settings
        self.service = service


def create_app(
    settings: Settings | None = None,
    *,
    service: RobogService | None = None,
) -> FastAPI:
    """Build the FastAPI app (and, unless one is injected, the engine)."""
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.api = ApiState(
            settings, service if service is not None else RobogService.from_settings(settings)
        )
        yield

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # -- dependencies ------------------------------------------------------
    def get_api(request: Request) -> ApiState:
        return request.app.state.api

    def get_service(api: ApiState = Depends(get_api)) -> RobogService:
        return api.service

    # -- errors ------------------------------------------------------------
    @app.exception_handler(ServiceUnavailable)
    async def _unavailable(request: Request, exc: ServiceUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "search index unavailable",
                "reason": str(exc),
                "fix": "python -m ingest.run --data data",
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})

    # -- index + health ----------------------------------------------------
    @app.get("/", tags=["meta"], summary="Endpoint index")
    async def index(request: Request) -> dict:
        base = _base_url(request)
        return {
            "name": API_TITLE,
            "version": API_VERSION,
            "docs": f"{base}/docs",
            "endpoints": {
                "GET /search": f"{base}/search?q=Which robot fits my restaurant%3F&k=5",
                "POST /ask": f'{base}/ask  (JSON body: {{"question": "..."}})',
                "GET /products": f"{base}/products?language=en",
                "GET /media/{doc_id}/{filename}": f"{base}/media/<doc_id>/<figure>.png",
                "GET /health": f"{base}/health",
            },
            "example": f"curl '{base}/search?q=Wieviel+Zuladung+hat+der+FlashBot%3F&k=3'",
        }

    @app.get("/health", tags=["meta"], summary="Index + provider status")
    async def health(svc: RobogService = Depends(get_service)) -> JSONResponse:
        status = await run_in_threadpool(svc.status)
        payload = status.to_dict()
        payload["uptime_s"] = int(time.time() - _STARTED_AT)
        return JSONResponse(status_code=200 if status.ready else 503, content=payload)

    # -- GET /search (Bonus 01) -------------------------------------------
    @app.get(
        "/search",
        tags=["retrieval"],
        summary="Standalone hybrid retrieval (Bonus 01)",
        description=(
            "BM25 + parameter index + product profile cards + figure route, under the "
            "per-question language policy. Returns `SearchResult.to_dict()` with an "
            "absolute `image_url` on every figure hit."
        ),
    )
    async def search(
        request: Request,
        q: str = Query(..., min_length=1, max_length=MAX_QUESTION_CHARS, description="the question"),
        k: int = Query(5, ge=1, le=50, description="number of chunks"),
        language: Optional[Language] = Query(None, description=_LANG_HELP),
        product: Optional[str] = Query(None, description="restrict to one product"),
        doc_type: Optional[DocType] = Query(None, description="robots or accessories"),
        chunk_type: Optional[ChunkType] = Query(None, description="text | table | figure"),
        follow_user_language: bool = Query(True, description="false = all languages at once"),
        cross_lingual_fallback: bool = Query(True),
        dedupe_translations: bool = Query(True),
        svc: RobogService = Depends(get_service),
    ) -> dict:
        return await run_in_threadpool(
            lambda: svc.search(
                q,
                k=k,
                language=language,
                product=product,
                doc_type=doc_type,
                chunk_type=chunk_type,
                follow_user_language=follow_user_language,
                cross_lingual_fallback=cross_lingual_fallback,
                dedupe_translations=dedupe_translations,
                base=_base_url(request),
            )
        )

    # -- POST /ask (full RAG pipeline) ------------------------------------
    @app.post(
        "/ask",
        tags=["answering"],
        summary="Grounded, evidence-validated answer",
        description=(
            "retrieve -> number the evidence -> generate -> validate (G5) -> answer. "
            "The answer is written in the question's language (Bonus 02) and never "
            "reaches the caller unvalidated."
        ),
    )
    async def ask(
        request: Request,
        body: AskRequest,
        svc: RobogService = Depends(get_service),
    ) -> dict:
        return await run_in_threadpool(
            lambda: svc.ask(
                body.question,
                k=body.k,
                language=body.language,
                product=body.product,
                provider=body.provider,
                model=body.model,
                follow_user_language=body.follow_user_language,
                cross_lingual_fallback=body.cross_lingual_fallback,
                include_search=body.include_search,
                include_evidence=body.include_evidence,
                base=_base_url(request),
            )
        )

    # -- GET /products -----------------------------------------------------
    @app.get(
        "/products",
        tags=["catalog"],
        summary="Structured catalog, profile cards, parameter lookup",
        description=(
            "One entry per product: languages, documents, the localized profile card "
            "and its parameter-index rows. `?parameter=` filters rows, `?q=` runs the "
            "parameter index's own ranker."
        ),
    )
    async def products(
        request: Request,
        language: Language = Query("en", description="language of the profile cards"),
        product: Optional[str] = Query(None, description="name, model code or substring"),
        parameter: Optional[str] = Query(None, description="filter parameter rows by name"),
        q: Optional[str] = Query(None, max_length=400, description="free-text parameter search"),
        doc_type: Optional[DocType] = Query(None, description="robots or accessories"),
        include_params: bool = Query(True),
        params_per_product: int = Query(20, ge=0, le=500),
        k: int = Query(10, ge=1, le=50, description="rows for the free-text search"),
        svc: RobogService = Depends(get_service),
    ) -> dict:
        return await run_in_threadpool(
            lambda: svc.catalog(
                language=language,
                product=product,
                parameter=parameter,
                q=q,
                doc_type=doc_type,
                include_params=include_params,
                params_per_product=params_per_product,
                k=k,
                base=_base_url(request),
            )
        )

    # -- GET /media/{doc_id}/{filename} -----------------------------------
    async def media(doc_id: str, filename: str, api: ApiState = Depends(get_api)) -> Any:
        path = api.service.resolve_media(doc_id, filename)
        if path is None:
            return JSONResponse(status_code=404, content={"detail": "no such figure"})
        return FileResponse(
            path,
            media_type="image/png",
            headers={
                "Cache-Control": f"public, max-age={api.settings.media_max_age}",
                "X-Content-Type-Options": "nosniff",
            },
        )

    app.add_api_route(
        "/media/{doc_id}/{filename}",
        media,
        methods=["GET"],
        tags=["media"],
        summary="A manual's figure (PNG), path-traversal safe",
        description=(
            "Serves only `data/**/images/*.png`. Anything else -- unknown `doc_id`, "
            "traversal, a non-PNG name, a missing file -- is a 404."
        ),
    )
    # HEAD is how a browser probes a cached image; it needs no schema entry.
    app.add_api_route(
        "/media/{doc_id}/{filename}", media, methods=["HEAD"], include_in_schema=False
    )

    return app


def _base_url(request: Request) -> str:
    """Origin the client used, so ``image_url``s are absolute and proxy-correct."""
    return str(request.base_url).rstrip("/")


def __getattr__(name: str):
    """``api.app.app`` builds the ASGI app on first use (``uvicorn api.app:app``).

    Deliberately lazy: importing ``create_app`` (tests, the CLI client) must not
    read 1,351 chunks and build a BM25 index as a side effect.
    """
    if name == "app":
        built = create_app()
        globals()["app"] = built
        return built
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
