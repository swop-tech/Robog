"""Data-processing CLI: clean + chunk every manual into build/chunks.jsonl.

Run from the repository root:

    python -m ingest.run --data data --out build/chunks.jsonl

It finds every ``en.md`` / ``de.md`` / ``it.md`` under the data folder
(see data/README.md for the layout), cleans and chunks each one, and writes
one JSON chunk per line:

    {"id": "...", "text": "...", "metadata": {"product", "title", "language",
     "section", "section_number", "page", "pdf_page", "chunk_type", "image", ...}}

It also writes, next to the chunks:

* ``build/params.jsonl`` -- the structured parameter index (one row per table
  row: product -> parameter -> value/unit -> source page), see
  ``ingest/params.py`` and gap G1 in docs/pipeline-review.md.
* ``build/profiles.jsonl`` -- deterministic product profile cards per robot
  (category, dimensions, weight, payload, battery/runtime, speed, key use-case,
  representative figure, source citations), see ``ingest/profiles.py`` and gap G2.
* ``build/data-report.md`` -- the validation report (``ingest/validate.py``);
  the command exits non-zero if an error is found.

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
from .params import build_param_index
from .profiles import build_product_profiles
from .validate import load_jsonl, render_markdown, validate_chunks, validate_params, validate_profiles

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
    parser.add_argument("--params", default=None, type=Path,
                        help="parameter index output (default: <out dir>/params.jsonl)")
    parser.add_argument("--profiles", default=None, type=Path,
                        help="product profile cards output (default: <out dir>/profiles.jsonl)")
    parser.add_argument("--report", default=None, type=Path,
                        help="validation report (default: <out dir>/data-report.md)")
    parser.add_argument("--max-chars", default=DEFAULT_MAX_CHARS, type=int,
                        help=f"chunk budget in characters (default: {DEFAULT_MAX_CHARS})")
    parser.add_argument("--no-validate", action="store_true",
                        help="skip the validation report")
    args = parser.parse_args()

    args.params = args.params or args.out.parent / "params.jsonl"
    args.profiles = args.profiles or args.out.parent / "profiles.jsonl"
    args.report = args.report or args.out.parent / "data-report.md"

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

    # -- structured parameter index (gap G1) --------------------------------
    chunks = load_jsonl(args.out)
    params = build_param_index(chunks)
    with args.params.open("w", encoding="utf-8") as out:
        for record in params:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"  parameter index: {len(params)} rows -> {args.params}")

    # -- deterministic product profile cards (gap G2) -----------------------
    profiles = build_product_profiles(chunks, params)
    with args.profiles.open("w", encoding="utf-8") as out:
        for card in profiles:
            out.write(json.dumps(card, ensure_ascii=False) + "\n")
    unique_robots = len({card["product"] for card in profiles})
    print(f"  product profiles: {len(profiles)} cards ({unique_robots} robots) -> {args.profiles}")

    # -- validation gate -----------------------------------------------------
    if args.no_validate:
        return
    report = validate_chunks(chunks, args.data)
    validate_params(params, report)
    validate_profiles(profiles, chunks, report)
    args.report.write_text(render_markdown(report, str(args.out)), encoding="utf-8")
    print(f"  validation: {len(report.errors)} error(s), {len(report.warnings)} warning(s) "
          f"-> {args.report}")
    if report.errors:
        for error in report.errors:
            print(f"  ERROR: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
