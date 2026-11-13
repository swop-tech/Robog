"""Step 3 of data processing: the structured **parameter index**.

Pure vector search is bad at "what is the value of X?" -- numbers, units and
model codes are exactly what embeddings blur (docs/pipeline-review.md, gap G1).
So in addition to keeping the markdown table as a normal ``chunk_type="table"``
chunk (good for the LLM to read), every table *row* is also emitted as a
structured record:

    {"product": "FlashBot", "parameter": "Load capacity", "value": "20 kg (10 kg per tray)",
     "unit": "kg", "value_number": 20, "page": 5, ...}

Each record points back at the chunk it came from (``chunk_id``) and carries
the same citation metadata, so a parameter answer is as checkable as any other.

Only the standard library is used. Rows for which the source table has a
header look like a normal parameter/value pair; tables with more columns keep
the extra cells in ``value`` (e.g. packing lists: "Item | Delivery type").

Usage from code:
    from ingest.params import parse_table_rows, build_param_index
    rows = parse_table_rows("| Parameter | Value |\\n|---|---|\\n| Payload | 30 kg |")

CLI:
    python -m ingest.params --chunks build/chunks.jsonl --out build/params.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

# Units that show up in PUDU manuals, longest first so "m/s" wins over "m".
_UNITS = [
    "m/s", "km/h", "kg", "g", "mg", "mm", "cm", "m", "Ah", "mAh", "kWh", "Wh",
    "Vac", "Vdc", "VAC", "VDC", "V", "mV", "A", "mA", "W", "kW", "Hz", "MHz",
    "GHz", "dBm", "dB", "°C",
    "℃", "%", "h", "min", "s", "L", "mL", "lm", "lx", "Pa", "bar", "kPa",
    "rpm", "Ω", "inch", "in", "ft", "lbs", "IP",
]
_UNIT_RE = re.compile(
    r"(?<![\w.])(" + "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True)) + r")(?![\w])"
)
# A unit almost always follows its number ("14,3 kg", "29,2 V"); matching that
# first avoids reading the "L" of "(L, W, H)" as a unit.
_UNIT_AFTER_NUMBER_RE = re.compile(
    r"\d\s*(" + "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True)) + r")(?![\w])"
)
# Numbers are only read from the *start* of a value ("14,3 kg", "≤ 90 %",
# "404 x 234 x 497 mm"); "CC1" in "Ladestation CC1" is a model name, not a value.
_NUMBER_RE = re.compile(
    r"^\s*(?:[≤≥<>~≈]|up to|max\.?|min\.?|bis zu)?\s*([-+]?\d+(?:[.,]\d+)?)"
)
_SEP_CELL_RE = re.compile(r"^:?-{2,}:?$")


def _split_row(line: str) -> list[str]:
    """Split a markdown table row into cleaned cells."""
    raw = line.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    cells = [c.strip().replace("<br>", " ").replace("<br/>", " ") for c in raw.split("|")]
    return [re.sub(r"\s+", " ", c) for c in cells]


# A merged cell looks like "Body weight 23 kg" / "Input voltage 100 ~ 240 Vac":
# the parameter and its value ended up in the same table cell.
_MERGED_CELL_RE = re.compile(
    r"^(?P<parameter>.*?[^\s\d])\s+(?P<value>[-+≤≥<>~]?\s?\d.*)$"
)


def split_merged_cell(text: str) -> tuple[str, str]:
    """``"Body weight 23 kg"`` -> ``("Body weight", "23 kg")``.

    A few source tables put parameter and value in one cell (and leave the
    value column empty); splitting them keeps the parameter index useful.
    """
    m = _MERGED_CELL_RE.match(text)
    if not m:
        return text, ""
    parameter, value = m.group("parameter").strip(), m.group("value").strip()
    # Guard against model names / section labels ("CC1 Pro ..."), which would
    # otherwise be split on their trailing number.
    if len(parameter) < 3 or not re.search(r"[A-Za-zÀ-ÿ]{3}", parameter):
        return text, ""
    return parameter, value


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(_SEP_CELL_RE.match(c) or c == "" for c in cells) and any(
        "-" in c for c in cells
    )


def parse_table_rows(table_markdown: str) -> list[dict]:
    """Turn one markdown table into rows of ``{parameter, value, column, ...}``.

    The first non-separator row is treated as the header. The first column is
    the parameter name, the second its value; any further columns are appended
    to the value (comma separated) so nothing is lost.

    Protocol/notice tables often have more than two columns where the value is
    split into several columns -- we keep the readable joined text.
    """
    lines = [ln for ln in table_markdown.splitlines() if ln.strip()]
    if not lines:
        return []

    rows = [_split_row(ln) for ln in lines]
    rows = [r for r in rows if r and not _is_separator(r)]
    if not rows:
        return []

    header, body = rows[0], rows[1:]
    width = max(len(r) for r in rows)
    if len(header) < width:  # tolerate ragged headers
        header = header + [""] * (width - len(header))

    out: list[dict] = []
    for index, cells in enumerate(body):
        cells = cells + [""] * (width - len(cells))
        cells = [c for c in cells]
        if not any(cells):
            continue
        parameter = cells[0]
        if not parameter:
            continue
        # A row that just repeats the header (some tables repeat it per page).
        if [c.strip().lower() for c in cells] == [h.strip().lower() for h in header]:
            continue
        value_parts = [c for c in cells[1:] if c]
        value = ", ".join(value_parts)
        column = header[1] if len(header) > 1 else ""
        if not value:
            parameter, value = split_merged_cell(parameter)
            if not value:
                continue  # a label with neither value nor merged value: skip
        out.append(
            {
                "parameter": parameter,
                "value": value,
                "column": column,
                "unit": extract_unit(value),
                "value_number": extract_number(value),
                "row_index": index,
            }
        )
    return out


def extract_unit(text: str) -> str:
    """Return the first known unit in a value string (``"29,4 V"`` -> ``"V"``)."""
    # Column labels such as "Body size (L, W, H)" must not read as "L".
    plain = re.sub(r"\([^)]*\)", " ", text)
    m = _UNIT_AFTER_NUMBER_RE.search(plain) or _UNIT_RE.search(plain)
    return m.group(1) if m else ""


def extract_number(text: str) -> float | None:
    """Return the first number in a value string, comma decimals normalised."""
    m = _NUMBER_RE.match(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:  # e.g. "1,2,3" -> not a number
        return None


def param_key(parameter: str) -> str:
    """Stable slug of a parameter name, for grouping across chunks/languages."""
    return re.sub(r"[^a-z0-9]+", "_", parameter.lower()).strip("_")


def build_param_index(chunks: list[dict]) -> list[dict]:
    """Build parameter records from the ``chunk_type == "table"`` chunks."""
    records: list[dict] = []
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        if meta.get("chunk_type") != "table":
            continue
        for row in parse_table_rows(chunk.get("text", "")):
            record = {
                "id": f"{chunk['id']}-p{row['row_index'] + 1:02d}",
                "chunk_id": chunk["id"],
                "param_key": param_key(row["parameter"]),
                **row,
                "product": meta.get("product", ""),
                "manual": meta.get("manual", ""),
                "doc_type": meta.get("doc_type", ""),
                "title": meta.get("title", ""),
                "language": meta.get("language", ""),
                "source_pdf": meta.get("source_pdf", ""),
                "source_file": meta.get("source_file", ""),
                "section": meta.get("section", ""),
                "section_number": meta.get("section_number"),
                "page": meta.get("page"),
                "pdf_page": meta.get("pdf_page"),
            }
            records.append(record)
    return records


def load_chunks(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the structured parameter index.")
    parser.add_argument("--chunks", default=Path("build/chunks.jsonl"), type=Path)
    parser.add_argument("--out", default=Path("build/params.jsonl"), type=Path)
    args = parser.parse_args()

    chunks = load_chunks(args.chunks)
    records = build_param_index(chunks)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    products = Counter(r["product"] for r in records)
    with_value = sum(1 for r in records if r["value"])
    with_unit = sum(1 for r in records if r["unit"])
    print(f"{len(records)} parameter rows -> {args.out}")
    print(f"  with a value: {with_value}   with a recognised unit: {with_unit}")
    print(f"  products: {len(products)}")
    if records:
        print(f"  example: {records[0]['product']} / {records[0]['parameter']} = {records[0]['value']}"
              f" (p. {records[0]['page']})")


if __name__ == "__main__":
    main()
