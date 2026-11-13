"""Step 1 of data processing: clean raw markdown manuals.

Cleaning is deliberately conservative -- we only remove noise, never content:

1. Parse the YAML-like front matter (``title``, ``language``, ``source_pdf``...)
   into a metadata dict.
2. Remove HTML comments.
3. Normalize weird whitespace (non-breaking spaces, zero-width characters).
4. Strip trailing spaces and collapse runs of blank lines.

Page marker lines (e.g. ``*Page 3 (PDF page 4, left)*``) are NOT removed here --
the chunker reads them to attach page numbers to every chunk (used later for
citations such as "FlashBot manual, section 1.1, page 2").

This module uses only the Python standard library, so it runs anywhere.

Usage from code:
    from ingest.clean import load_manual
    manual = load_manual("data/manuals/Some_Manual/en.md")
    print(manual.meta["title"], len(manual.body))
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# --- Line 1: title: FlashBot User Manual  /  language: en -------------------
_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_NBSP_RE = re.compile(r"[\u00a0\u2007\u202f]")  # non-breaking spaces
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")


@dataclass
class Manual:
    """One cleaned manual file (e.g. the German version of the BellaBot manual)."""

    path: Path
    meta: dict = field(default_factory=dict)
    body: str = ""

    @property
    def language(self) -> str:
        """Language code from the front matter, falling back to the file name."""
        lang = str(self.meta.get("language", "")).strip().lower()
        if lang:
            return lang
        return self.path.stem.lower()  # en.md -> "en"

    @property
    def title(self) -> str:
        return str(self.meta.get("title", "")).strip() or self.path.parent.name


def parse_front_matter(text: str) -> tuple[dict, str]:
    """Split the leading YAML-ish block from the body.

    The manuals use a simple ``key: value`` block, so we parse it line by line
    instead of pulling in a YAML dependency.
    """
    meta: dict[str, str] = {}
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return meta, text
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip("'\"")
    return meta, text[match.end():]


def normalize(body: str) -> str:
    """Remove whitespace noise without touching real content."""
    body = _NBSP_RE.sub(" ", body)
    body = _ZERO_WIDTH_RE.sub("", body)
    body = _HTML_COMMENT_RE.sub("", body)

    lines = [line.rstrip() for line in body.splitlines()]
    out: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run <= 1:  # keep at most one blank line in a row
                out.append(line)
        else:
            blank_run = 0
            out.append(line)
    return "\n".join(out).strip() + "\n"


def load_manual(path: str | Path) -> Manual:
    """Read one markdown file and return it cleaned and parsed."""
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    meta, body = parse_front_matter(raw)
    return Manual(path=path, meta=meta, body=normalize(body))
