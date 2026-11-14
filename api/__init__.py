"""Robog's standalone backend: retrieval + grounded answering over HTTP.

This package is **Bonus 01**: the retrieval engine (``retrieve/``) and the
answer composer (``answer/``) exposed as a small FastAPI service, so any client
-- the web chat (served at ``GET /``), a Telegram bot, ``curl``, the CLI client
in ``api/client.py`` -- talks to the same endpoints:

==============================  =======================================================
``GET  /``                      the zero-build web chat (browser) / endpoint index (API client)
``GET  /search``                hybrid retrieval: ``SearchResult.to_dict()`` + ``image_url``
``POST /ask``                   full RAG pipeline: answer, citations, figures, params,
                                verification, cross-lingual disclosure
``GET  /products``              structured catalog, profile cards, parameter lookup
``GET  /media/{doc_id}/{file}`` the manuals' figures, path-traversal safe
==============================  =======================================================

Quick start::

    pip install -r requirements-api.txt
    python -m ingest.run --data data
    python -m api                        # http://localhost:8000 — chat at /, docs at /docs
    python -m api.client "Wieviel Zuladung hat der FlashBot?"

Nothing here re-implements retrieval or answering: ``api/service.py`` holds the
``SearchEngine`` + ``AnswerComposer`` and turns their dataclasses into JSON.
"""

from __future__ import annotations

try:
    from .app import create_app
except ModuleNotFoundError as exc:  # pragma: no cover - depends on the environment
    raise ModuleNotFoundError(
        "the Robog API needs FastAPI and Uvicorn: pip install -r requirements-api.txt"
    ) from exc

from .config import Settings
from .media import MediaStore
from .service import RobogService, ServiceUnavailable

__all__ = [
    "create_app",
    "Settings",
    "MediaStore",
    "RobogService",
    "ServiceUnavailable",
]
