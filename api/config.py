"""Configuration for the Robog API: where the data lives, how big answers get.

Everything is overridable from the environment (``.env`` is loaded by
``retrieve.env.load_dotenv``), so the same service runs from a laptop, from the
repository root or inside a container without code changes:

=========================  =====================  ================================
Variable                   Default                Meaning
=========================  =====================  ================================
``ROBOG_DATA_DIR``         ``<repo>/data``        manuals + figures (media root)
``ROBOG_BUILD_DIR``        ``<repo>/build``       ``chunks``/``params``/``profiles``
``ROBOG_CHUNKS``           ``<build>/chunks.jsonl``
``ROBOG_PARAMS``           ``<build>/params.jsonl``
``ROBOG_PROFILES``         ``<build>/profiles.jsonl``
``ROBOG_API_VECTOR``       ``auto``               attach Chroma when built; ``off``
``ROBOG_API_DEFAULT_K``    ``8``                  hits per ``/search`` by default
``ROBOG_API_MAX_K``        ``20``                 upper bound a client may ask for
``ROBOG_API_MEDIA_MAX_AGE`` ``86400``             ``Cache-Control`` seconds
``ROBOG_API_CORS_ORIGINS`` ``*``                  comma-separated list of origins
``ROBOG_API_HOST``         ``0.0.0.0``            bind address for ``python -m api``
``ROBOG_API_PORT``         ``8000``               port for ``python -m api``
=========================  =====================  ================================

The answer generator keeps its own settings (``ROBOG_LLM_PROVIDER``,
``ROBOG_LLM_MODEL``) -- see ``answer/llm.py``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from retrieve.env import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

API_TITLE = "Robog API"
API_VERSION = "0.1.0"
API_DESCRIPTION = (
    "Hybrid retrieval (Bonus 01) + grounded, evidence-validated answering over the "
    "PUDU robot manuals, in EN/DE/IT.\n\n"
    "`GET /` serves the zero-build web chat to a browser (Accept: text/html); API clients "
    "get the JSON endpoint index. The chat is only the first client of the service.\n\n"
    "* `GET /search` - raw hybrid retrieval: `SearchResult.to_dict()` with resolved `image_url`s.\n"
    "* `POST /ask` - full RAG pipeline: answer, language, route, citations, figures, "
    "params, verification, cross_lingual.\n"
    "* `GET /products` - structured catalog, product profile cards, parameter lookup.\n"
    "* `GET /media/{doc_id}/{filename}` - the manuals' figures (path-traversal safe).\n"
)


def _resolve_path(value: str | None, default: Path) -> Path:
    """Absolute path from an env value (relative values resolve against CWD)."""
    if not value:
        return default
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name, "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration for one API process."""

    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    chunks_path: Path = field(default_factory=lambda: REPO_ROOT / "build" / "chunks.jsonl")
    params_path: Path = field(default_factory=lambda: REPO_ROOT / "build" / "params.jsonl")
    profiles_path: Path = field(default_factory=lambda: REPO_ROOT / "build" / "profiles.jsonl")
    #: ``None``/``off`` = keyword-only; ``auto`` = attach Chroma when it is built.
    vector: str | None = None
    media_prefix: str = "/media"
    media_max_age: int = 86400
    default_k: int = 8
    max_k: int = 20
    cors_origins: tuple[str, ...] = ("*",)
    host: str = "0.0.0.0"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "Settings":
        # ``uvicorn api.app:app`` never goes through ``python -m api``, so the
        # ``ROBOG_API_*`` values in ``.env`` have to be loaded here, not later.
        load_dotenv()
        data_dir = _resolve_path(os.environ.get("ROBOG_DATA_DIR"), REPO_ROOT / "data")
        build_dir = _resolve_path(os.environ.get("ROBOG_BUILD_DIR"), REPO_ROOT / "build")
        vector = (os.environ.get("ROBOG_API_VECTOR") or "auto").strip().lower()
        origins = os.environ.get("ROBOG_API_CORS_ORIGINS", "*")
        return cls(
            data_dir=data_dir,
            chunks_path=_resolve_path(os.environ.get("ROBOG_CHUNKS"), build_dir / "chunks.jsonl"),
            params_path=_resolve_path(os.environ.get("ROBOG_PARAMS"), build_dir / "params.jsonl"),
            profiles_path=_resolve_path(
                os.environ.get("ROBOG_PROFILES"), build_dir / "profiles.jsonl"
            ),
            vector=None if vector in ("", "off", "none", "0", "false") else vector,
            media_max_age=_env_int("ROBOG_API_MEDIA_MAX_AGE", 86400, minimum=0),
            default_k=_env_int("ROBOG_API_DEFAULT_K", 8),
            max_k=max(_env_int("ROBOG_API_MAX_K", 20), _env_int("ROBOG_API_DEFAULT_K", 8)),
            cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()) or ("*",),
            host=os.environ.get("ROBOG_API_HOST", "0.0.0.0") or "0.0.0.0",
            port=_env_int("ROBOG_API_PORT", 8000),
        )
