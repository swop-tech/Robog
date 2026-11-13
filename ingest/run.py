"""Data-processing CLI: clean + chunk every manual into build/chunks.jsonl.

Run from the repository root:

    python -m ingest.run --data data --out build/chunks.jsonl

It finds every ``en.md`` / ``de.md`` / ``it.md`` under the data folder
(see data/README.md for the layout), cleans and chunks each one, and writes
one JSON chunk per line:

    {"id": "...", "text": "...", "metadata": {"title", "language", "section",
     "page", "chunk_type", "image", ...}}

Embedding + indexing happens afterwards (phase 2):

    python -m ingest.embed_index --chunks build/chunks.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .chunk import DEFAULT_MAX_CHARS, chunk_manual
from .clean import load_manual

MANUAL_LANG_FILES = {"en.md", "de.md", "it.md"}


def discover_manuals(data_dir: Path) -> list[Path]:
    """Find every language file of every manual under data_dir."""
    found = [p for p in data_dir.rglob("*.md") if p.name in MANUAL_LANG_FILES]
    return sorted(found)


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean + chunk the robot manuals.")
    parser.add_argument("--data", default="data", type=Path, help="data folder (default: data)")
    parser.add_argument("--out", default=Path("build/chunks.jsonl"), type=Path,
                        help="output JSONL file (default: build/chunks.jsonl)")
    parser.add_argument("--max-chars", default=DEFAULT_MAX_CHARS, type=int,
                        help=f"chunk budget in characters (default: {DEFAULT_MAX_CHARS})")
    args = parser.parse_args()

    files = discover_manuals(args.data)
    if not files:
        print(f"No en.md / de.md / it.md files found under '{args.data}'.")
        print("Put the dataset in place first (see data/README.md).")
        return

    args.out.parent.mkdir(parents=True, exist_ok=True)
    total_chunks = 0
    by_type: Counter[str] = Counter()
    by_language: Counter[str] = Counter()
    no_pages: list[str] = []

    with args.out.open("w", encoding="utf-8") as out:
        for path in files:
            manual = load_manual(path)
            chunks = chunk_manual(manual, max_chars=args.max_chars)
            for chunk in chunks:
                out.write(json.dumps(chunk.to_json(), ensure_ascii=False) + "\n")
            total_chunks += len(chunks)
            by_type.update(c.metadata.get("chunk_type", "text") for c in chunks)
            by_language.update(manual.language for c in chunks)
            if not any(c.metadata.get("page") for c in chunks):
                no_pages.append(str(path))
            print(f"  {path}: {len(chunks)} chunks")

    print(f"\nDone. {len(files)} files -> {total_chunks} chunks -> {args.out}")
    print(f"  by type:     {dict(by_type)}")
    print(f"  by language: {dict(by_language)}")
    for path in no_pages:
        print(f"  WARNING: no page markers found in {path} (citations will lack pages)")


if __name__ == "__main__":
    main()
