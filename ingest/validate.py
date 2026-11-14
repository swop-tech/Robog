"""Validation of the data-processing output (the QA gate before embedding).

"Preserve source metadata" is only worth something if it is *checkable*, so
this module inspects ``build/chunks.jsonl`` (and optionally
``build/params.jsonl``) against the dataset on disk and writes a report:

    python -m ingest.validate --chunks build/chunks.jsonl --data data \
        --report build/data-report.md

It checks, among others:

* every chunk has the citation metadata it promises (product, language, page,
  section, chunk_type, source_pdf, ...);
* no chunk is empty (an empty chunk can never be retrieved);
* cleaning actually removed the noise (page markers, front matter, ``---``
  rules never appear inside chunk text);
* every ``chunk_type="figure"`` chunk points at an image file that exists;
* chunk ids are unique and none exceeds the chunk budget on prose;
* every manual/language pair in the dataset is represented, and the manuals
  without Italian are reported (the Bonus 02 cross-lingual demo story).

Exit code is 1 if any ERROR is found, so it can gate the pipeline.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

LANGUAGES = ("en", "de", "it")
CHUNK_TYPES = ("text", "table", "figure")

# Metadata every chunk must carry (pipeline-review.md section 5).
REQUIRED_META = (
    "doc_id", "manual", "product", "doc_type", "title", "language",
    "source_pdf", "section", "section_title", "pdf_page", "chunk_type",
    "source_file",
)

# A prose chunk above this is suspicious (tables may legitimately be longer).
MAX_PROSE_CHARS = 2200

_NOISE_PATTERNS = {
    "page marker": re.compile(r"\*?\b(?:Page|Seite|Pagina)\s+\d+.*(?:PDF|Seite|pagina)", re.IGNORECASE),
    "front matter": re.compile(r"^\s*-{3}\s*$|source_pdf:", re.MULTILINE),
    "horizontal rule": re.compile(r"^\s*(?:[-*_]\s*){3,}$", re.MULTILINE),
    "html comment": re.compile(r"<!--"),
}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def load_jsonl(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _dataset_languages(data_dir: Path) -> dict[str, list[str]]:
    """Map each manual folder -> the languages it actually has."""
    found: dict[str, list[str]] = defaultdict(list)
    if not data_dir.exists():
        return {}
    for path in sorted(data_dir.rglob("*.md")):
        if path.name in {f"{lang}.md" for lang in LANGUAGES}:
            found[path.parent.name].append(path.stem)
    return {manual: sorted(langs) for manual, langs in found.items()}


def validate_chunks(chunks: list[dict], data_dir: Path | None = None) -> Report:
    report = Report()
    if not chunks:
        report.error("no chunks found -- run 'python -m ingest.run' first")
        return report

    # -- counts ------------------------------------------------------------
    by_type = Counter(c["metadata"].get("chunk_type", "?") for c in chunks)
    by_language = Counter(c["metadata"].get("language", "?") for c in chunks)
    by_doc_type = Counter(c["metadata"].get("doc_type", "?") for c in chunks)
    products = sorted({c["metadata"].get("product", "") for c in chunks})
    report.info.update(
        chunks=len(chunks),
        by_type=dict(by_type),
        by_language=dict(by_language),
        by_doc_type=dict(by_doc_type),
        products=products,
        documents=len({c["metadata"].get("doc_id") for c in chunks}),
    )

    # -- required metadata -------------------------------------------------
    for key in REQUIRED_META:
        missing = [c["id"] for c in chunks if c["metadata"].get(key) in (None, "")]
        if missing:
            report.warn(f"{len(missing)} chunk(s) without '{key}', e.g. {missing[0]}")
    # section_number is legitimately absent for unnumbered headings.
    missing_numbers = sum(1 for c in chunks if not c["metadata"].get("section_number"))
    report.info["chunks_without_section_number"] = missing_numbers

    # -- pages -------------------------------------------------------------
    no_page = [c for c in chunks if c["metadata"].get("page") in (None, "")]
    if no_page:
        report.warn(
            f"{len(no_page)} chunk(s) have no printed page (marker only named the PDF "
            f"page) -- citations fall back to pdf_page, e.g. {no_page[0]['id']}"
        )

    # -- chunk ids ---------------------------------------------------------
    ids = [c["id"] for c in chunks]
    duplicates = [i for i, n in Counter(ids).items() if n > 1]
    if duplicates:
        report.error(f"duplicate chunk ids: {duplicates[:5]}")

    # -- empty chunks ------------------------------------------------------
    empty = [c["id"] for c in chunks if not c.get("text", "").strip()]
    if empty:
        report.error(f"{len(empty)} empty chunk(s) -- they can never be retrieved: {empty[:5]}")

    # -- cleaning: noise must not survive into chunk text -------------------
    for label, pattern in _NOISE_PATTERNS.items():
        hits = [c["id"] for c in chunks if pattern.search(c.get("text", ""))]
        if hits:
            report.error(f"{len(hits)} chunk(s) still contain a {label}, e.g. {hits[0]}")

    # -- figures -----------------------------------------------------------
    figures = [c for c in chunks if c["metadata"].get("chunk_type") == "figure"]
    bad_image = []
    for c in figures:
        image = c["metadata"].get("image")
        if not image:
            bad_image.append(c["id"])
            continue
        if data_dir and not (Path(c["metadata"]["source_file"]).parent / image).exists():
            bad_image.append(c["id"])
    if bad_image:
        report.error(f"{len(bad_image)} figure chunk(s) without an existing image: {bad_image[:5]}")
    no_labels = sum(1 for c in figures if "Labels:" not in c.get("text", ""))
    report.info["figures"] = len(figures)
    report.info["figures_with_labels"] = len(figures) - no_labels

    # -- chunk size --------------------------------------------------------
    lengths = [len(c.get("text", "")) for c in chunks]
    long_prose = [
        c for c in chunks
        if c["metadata"].get("chunk_type") == "text" and len(c.get("text", "")) > MAX_PROSE_CHARS
    ]
    if long_prose:
        report.warn(f"{len(long_prose)} prose chunk(s) exceed {MAX_PROSE_CHARS} characters")
    long_tables = [
        c for c in chunks
        if c["metadata"].get("chunk_type") == "table" and len(c.get("text", "")) > MAX_PROSE_CHARS
    ]
    report.info.update(
        chars_min=min(lengths),
        chars_max=max(lengths),
        chars_mean=round(sum(lengths) / len(lengths)),
        tables_over_budget=len(long_tables),
    )

    # -- dataset coverage --------------------------------------------------
    if data_dir is not None:
        coverage = _dataset_languages(data_dir)
        seen = {(c["metadata"].get("manual"), c["metadata"].get("language")) for c in chunks}
        missing = [
            f"{manual}/{lang}"
            for manual, langs in coverage.items()
            for lang in langs
            if (manual, lang) not in seen
        ]
        if missing:
            report.error(f"{len(missing)} manual/language file(s) produced no chunks: {missing[:5]}")
        report.info["documents_in_dataset"] = len(coverage)
        report.info["missing_languages"] = {
            manual: [lang for lang in LANGUAGES if lang not in langs]
            for manual, langs in coverage.items()
            if len(langs) < len(LANGUAGES)
        }

    # -- parameters --------------------------------------------------------
    return report


def validate_params(params: list[dict], report: Report | None = None) -> Report:
    report = report or Report()
    if not params:
        report.warn("no parameter rows (build/params.jsonl missing or empty)")
        return report
    report.info["param_rows"] = len(params)
    report.info["param_rows_with_unit"] = sum(1 for p in params if p.get("unit"))
    report.info["param_rows_with_number"] = sum(1 for p in params if p.get("value_number") is not None)
    no_value = [p["id"] for p in params if not p.get("value")]
    if no_value:
        report.warn(f"{len(no_value)} parameter row(s) have no value, e.g. {no_value[0]}")
    no_link = [p["id"] for p in params if not p.get("chunk_id")]
    if no_link:
        report.error(f"{len(no_link)} parameter row(s) without a source chunk")
    return report


VALID_PROFILE_CATEGORIES = ("delivery", "industrial_delivery", "cleaning", "accessory")


def validate_profiles(
    profiles: list[dict],
    chunks: list[dict] | None = None,
    report: Report | None = None,
) -> Report:
    report = report or Report()
    if not profiles:
        report.warn("no product profile cards (build/profiles.jsonl missing or empty)")
        return report

    unique_products = sorted({p.get("product", "") for p in profiles if p.get("product")})
    by_category = Counter(
        p.get("category", "?")
        for p in {card.get("product"): card for card in profiles}.values()
    )
    report.info["profile_cards"] = len(profiles)
    report.info["profile_products"] = len(unique_products)
    report.info["profiles_by_category"] = dict(by_category)

    bad_category = [
        p.get("id", "?") for p in profiles if p.get("category") not in VALID_PROFILE_CATEGORIES
    ]
    if bad_category:
        report.error(f"{len(bad_category)} profile card(s) with invalid category: {bad_category[:5]}")

    missing_use_case = [p.get("id", "?") for p in profiles if not str(p.get("key_use_case") or "").strip()]
    if missing_use_case:
        report.error(f"{len(missing_use_case)} profile card(s) without key_use_case: {missing_use_case[:5]}")

    missing_citation = [p.get("id", "?") for p in profiles if not str(p.get("citation") or "").strip()]
    if missing_citation:
        report.error(f"{len(missing_citation)} profile card(s) without citation: {missing_citation[:5]}")

    if chunks is not None:
        chunk_ids = {c["id"] for c in chunks}
        dangling = [
            p.get("id", "?")
            for p in profiles
            if not p.get("chunk_ids") or any(cid not in chunk_ids for cid in p.get("chunk_ids", ()))
        ]
        if dangling:
            report.error(f"{len(dangling)} profile card(s) with missing/unknown chunk_ids: {dangling[:5]}")

        expected_pairs = {
            (c["metadata"].get("product"), c["metadata"].get("language"))
            for c in chunks
            if c.get("metadata", {}).get("doc_type") == "robot"
        }
        seen_pairs = {(p.get("product"), p.get("language")) for p in profiles}
        missing_pairs = sorted(f"{prod}/{lang}" for prod, lang in (expected_pairs - seen_pairs))
        if missing_pairs:
            report.error(f"{len(missing_pairs)} robot/language pair(s) missing a profile card: {missing_pairs[:5]}")

    return report


def _fmt_counts(counts: dict, order=()) -> str:
    """Render a Counter dict in a stable, readable order."""
    keys = [k for k in order if k in counts] + sorted(k for k in counts if k not in order)
    return " · ".join(f"{k} {counts[k]:,}" for k in keys)


def render_markdown(report: Report, chunks_path: str = "build/chunks.jsonl") -> str:
    info = report.info
    lines = [
        "# Data-processing report",
        "",
        f"> Generated by `python -m ingest.validate` from `{chunks_path}`.",
        "",
        f"**Status: {'❌ FAILED' if report.errors else '✅ PASSED'}** — "
        f"{len(report.errors)} error(s), {len(report.warnings)} warning(s).",
        "",
        "## Counts",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Chunks | {info.get('chunks', 0):,} |",
        f"| Documents (manual × language) | {info.get('documents', 0)} |",
        f"| Text / table / figure chunks | {_fmt_counts(info.get('by_type', {}), CHUNK_TYPES)} |",
        f"| Chunks per language | {_fmt_counts(info.get('by_language', {}), LANGUAGES)} |",
        f"| Robot vs accessory chunks | {_fmt_counts(info.get('by_doc_type', {}), ('robot', 'accessory', 'unknown'))} |",
        f"| Character length (min / mean / max) | {info.get('chars_min')} / {info.get('chars_mean')} / {info.get('chars_max')} |",
        f"| Parameter rows | {info.get('param_rows', 0):,} ({info.get('param_rows_with_unit', 0):,} with a unit, {info.get('param_rows_with_number', 0):,} with a number) |",
        f"| Product profile cards | {info.get('profile_cards', 0):,} ({info.get('profile_products', 0)} robots: {_fmt_counts(info.get('profiles_by_category', {}), ('delivery', 'cleaning', 'industrial_delivery'))}) |",
        "",
        "## Figures",
        "",
        f"- {info.get('figures', 0)} figure chunks, {info.get('figures_with_labels', 0)} with a printed label legend.",
        "",
        "## Metadata",
        "",
        f"- Chunks without `section_number` (unnumbered headings): {info.get('chunks_without_section_number', 0)}",
        "",
    ]
    if info.get("products"):
        lines += ["### Products covered", "", ", ".join(info["products"]), ""]
    if info.get("missing_languages"):
        lines += [
            "### Manuals without a full language set",
            "",
            "Used deliberately for the cross-lingual demo (ask in Italian, source in EN/DE):",
            "",
        ]
        for manual, missing in info["missing_languages"].items():
            lines.append(f"- `{manual}` — missing {', '.join(missing).upper()}")
        lines.append("")
    if report.errors:
        lines += ["## Errors", ""] + [f"- ❌ {e}" for e in report.errors] + [""]
    if report.warnings:
        lines += ["## Warnings", ""] + [f"- ⚠️ {w}" for w in report.warnings] + [""]
    if not report.errors and not report.warnings:
        lines += ["No errors or warnings. ✅", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the data-processing output.")
    parser.add_argument("--chunks", default=Path("build/chunks.jsonl"), type=Path)
    parser.add_argument("--params", default=Path("build/params.jsonl"), type=Path)
    parser.add_argument("--profiles", default=Path("build/profiles.jsonl"), type=Path)
    parser.add_argument("--data", default=Path("data"), type=Path)
    parser.add_argument("--report", default=Path("build/data-report.md"), type=Path)
    args = parser.parse_args()

    chunks = load_jsonl(args.chunks)
    report = validate_chunks(chunks, args.data)
    validate_params(load_jsonl(args.params), report)
    if args.profiles.exists():
        validate_profiles(load_jsonl(args.profiles), chunks, report)

    markdown = render_markdown(report, str(args.chunks))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"Report written to {args.report}")
    raise SystemExit(1 if report.errors else 0)


if __name__ == "__main__":
    main()
