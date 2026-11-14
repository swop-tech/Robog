"""Run the Robog API: ``python -m api`` (``--help`` for every knob).

    python -m ingest.run --data data     # once: build/chunks.jsonl + params + profiles
    python -m api                        # http://0.0.0.0:8000, docs at /docs
    python -m api --port 8080 --vector auto
    ROBOG_API_PORT=8080 python -m api    # the same via the environment

The engine is built once at startup and shared by every request; the offline
answer formatter needs no API key, so the service demos without one
(``ROBOG_LLM_PROVIDER=gemini`` + ``GEMINI_API_KEY`` switch on ``gemini-2.5-flash``).
"""

from __future__ import annotations

import argparse
import os
import sys

from answer.llm import DEFAULT_LLM_MODEL
from retrieve.env import load_dotenv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve retrieval + grounded answering over HTTP (Bonus 01).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default=None, help="bind address (default: ROBOG_API_HOST or 0.0.0.0)")
    parser.add_argument("--port", default=None, type=int, help="port (default: ROBOG_API_PORT or 8000)")
    parser.add_argument("--data", default=None, help="manuals + figures folder (default: data)")
    parser.add_argument("--build", default=None, help="build folder with the indexes (default: build)")
    parser.add_argument("--chunks", default=None, help="chunks.jsonl (default: <build>/chunks.jsonl)")
    parser.add_argument("--params", default=None, help="params.jsonl (default: <build>/params.jsonl)")
    parser.add_argument("--profiles", default=None, help="profiles.jsonl (default: <build>/profiles.jsonl)")
    parser.add_argument(
        "--vector",
        default=None,
        choices=["off", "auto"],
        help="auto: fuse dense results from Chroma when an index is built",
    )
    parser.add_argument(
        "--provider",
        default=None,
        choices=["auto", "gemini", "offline"],
        help="answer generator (default: ROBOG_LLM_PROVIDER or auto)",
    )
    parser.add_argument("--model", default=None, help=f"LLM model ID (default: {DEFAULT_LLM_MODEL})")
    parser.add_argument("--k", default=None, type=int, help="default hits per /search (default: 8)")
    parser.add_argument("--max-age", default=None, type=int, help="media Cache-Control seconds")
    parser.add_argument("--cors", default=None, help="comma-separated allowed origins (default: *)")
    parser.add_argument("--reload", action="store_true", help="dev server with autoreload")
    parser.add_argument("--workers", default=1, type=int, help="uvicorn workers")
    parser.add_argument("--log-level", default="info", help="uvicorn log level")
    args = parser.parse_args(argv)

    load_dotenv()

    # Flags win over the environment; Settings.from_env() reads these.
    _set("ROBOG_DATA_DIR", args.data)
    _set("ROBOG_BUILD_DIR", args.build)
    _set("ROBOG_CHUNKS", args.chunks)
    _set("ROBOG_PARAMS", args.params)
    _set("ROBOG_PROFILES", args.profiles)
    _set("ROBOG_API_VECTOR", args.vector)
    _set("ROBOG_LLM_PROVIDER", args.provider)
    _set("ROBOG_LLM_MODEL", args.model)
    _set("ROBOG_API_DEFAULT_K", None if args.k is None else str(args.k))
    _set("ROBOG_API_MEDIA_MAX_AGE", None if args.max_age is None else str(args.max_age))
    _set("ROBOG_API_CORS_ORIGINS", args.cors)

    try:
        import uvicorn
    except ModuleNotFoundError:
        print("uvicorn is not installed -- pip install -r requirements-api.txt", file=sys.stderr)
        return 1

    from .app import create_app
    from .config import Settings

    settings = Settings.from_env()
    if not settings.chunks_path.exists():
        print(
            f"warning: {settings.chunks_path} not found -- /search, /ask and /products will "
            f"answer 503 until you run: python -m ingest.run --data {settings.data_dir}",
            file=sys.stderr,
        )

    host, port = args.host or settings.host, args.port or settings.port
    print(f"Robog API on http://{host}:{port}  (docs: http://{host}:{port}/docs)")
    print(f"  chat    http://{host}:{port}/  (open in a browser)")
    print(f"  data    {settings.data_dir}")
    print(f"  indexes {settings.chunks_path.parent}")
    print(f"  client  python -m api.client --port {port} \"Wieviel Zuladung hat der FlashBot?\"")

    if args.reload or args.workers > 1:
        # Reload/workers re-import the app in a subprocess: pass the factory.
        uvicorn.run(
            "api.app:create_app",
            factory=True,
            host=host,
            port=port,
            reload=args.reload,
            workers=args.workers,
            log_level=args.log_level,
        )
    else:
        uvicorn.run(create_app(settings), host=host, port=port, log_level=args.log_level)
    return 0


def _set(name: str, value: str | None) -> None:
    if value:
        os.environ[name] = value


if __name__ == "__main__":
    sys.exit(main())
