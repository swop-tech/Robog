"""Minimal zero-dependency ``.env`` loader for CLI scripts and services.

Phase 1 and the keyword retrieval baseline run on Python's standard library
alone, so loading ``GEMINI_API_KEY`` or ``ROBOG_*`` settings from a local
``.env`` file must not require ``python-dotenv``.

Rules:
* Existing environment variables always win (``os.environ.setdefault``), so
  explicit shell exports and test overrides behave predictably.
* Blank lines and ``#`` comment lines are ignored.
* Optional ``export `` prefixes and single/double quotes are stripped.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILE_VAR = "ROBOG_ENV_FILE"
_REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_dotenv(content: str) -> dict[str, str]:
    """Parse ``.env`` text into a ``{key: value}`` mapping."""
    parsed: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") and "=" in line:
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = value.replace('\\"', '"').replace("\\n", "\n")
        else:
            # Strip trailing inline comments ("value # comment") when unquoted.
            comment_at = value.find(" #")
            if comment_at != -1:
                value = value[:comment_at].rstrip()
        parsed[key] = value
    return parsed


def load_dotenv(path: str | Path | None = None, *, override: bool = False) -> Path | None:
    """Load variables from ``.env`` into ``os.environ`` if a file exists.

    Search order when ``path`` is omitted:
    1. ``$ROBOG_ENV_FILE`` (if set)
    2. ``./.env`` (current working directory)
    3. ``<repo_root>/.env``
    """
    candidates: list[Path] = []
    if path is not None:
        candidates.append(Path(path))
    elif os.environ.get(ENV_FILE_VAR):
        candidates.append(Path(os.environ[ENV_FILE_VAR]))
    else:
        cwd_env = Path.cwd() / ".env"
        root_env = _REPO_ROOT / ".env"
        candidates.append(cwd_env)
        if root_env != cwd_env:
            candidates.append(root_env)

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            data = parse_dotenv(candidate.read_text(encoding="utf-8"))
        except OSError:
            return None
        for key, value in data.items():
            if override or key not in os.environ:
                os.environ[key] = value
        return candidate
    return None
