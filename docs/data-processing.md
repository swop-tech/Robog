# Data processing — clean, chunk, preserve source metadata

> Step 1 of the pipeline in [`pipeline-review.md`](pipeline-review.md) §6. Implemented in
> `ingest/` with the **standard library only** (per [`stack-decisions.md`](stack-decisions.md) §2),
> and verified against the real dataset that now lives in `data/`.

## One command

```bash
python -m ingest.run --data data          # -> build/chunks.jsonl, build/params.jsonl, build/profiles.jsonl, build/data-report.md
python -m unittest tests.test_ingest -v  # stdlib only
```

Exit code is non-zero if the validation gate finds an error, so this can run in CI before
embedding ever happens.

## What comes out

| File | Content |
|---|---|
| `build/chunks.jsonl` | One retrieval chunk per line: `{"id", "text", "metadata"}` — what phase 2 embeds and indexes |
| `build/params.jsonl` | **Structured parameter index** (gap G1): one row per table row — `product → parameter → value/unit → source page`, linked back to its chunk |
| `build/profiles.jsonl` | **Product profile cards** (gap G2): one structured card per `(robot, language)` — category, dimensions, weight, payload/tanks, battery/runtime, speed, min passage width, use-case summary, appearance diagram, and source citations |
| `build/data-report.md` | The validation report (counts, coverage, warnings/errors) |

`build/` is git-ignored — regenerate it from `data/` at any time.

### Current numbers (real dataset)

| Metric | Value |
|---|---|
| Chunks | **1,351** |
| Documents (manual × language file) | 63 files, 22 manuals |
| Text / table / figure chunks | 706 / 169 / 476 |
| Per language | en 466 · de 469 · it 416 |
| Robot vs accessory | 842 / 509 |
| Parameter rows | 2,039 (1,050 with a unit, 841 with a number) |
| Product profile cards | **38** across **13** robots (`delivery` 8 · `cleaning` 4 · `industrial_delivery` 1; 13 EN · 13 DE · 12 IT) |
| Chunk size (chars) | min 5 · mean 702 · max 2,832 (tables kept whole) |
| Cleaned markdown | ~1.0 MB → 1.6 MB of JSONL |

## Chunking rules (how the markdown becomes chunks)

The manuals have structure, so we use it instead of a fixed token window:

1. **Sections** come from the numbered headings (`# 2. Product Components`,
   `## 2.3 Appearance`). The breadcrumb (`2. Product Components > 2.3 Appearance`) and the
   parsed `section_number` / `section_title` travel with every chunk.
2. **Page markers** (`*Page 3 (PDF page 4, left)*`, `*Seite …*`, `*Pagina …*`) are parsed, not
   kept: they set `page` (printed) and `pdf_page`. That is what makes citations like
   *"PUDU SH1 manual, §6.6, p. 14"* verifiable in the PDF.
3. **Prose** is packed up to 2,200 characters (~550 tokens) — far below the 8,192-token limit of
   `gemini-embedding-2` — and split at sentence boundaries. No artificial overlap: duplicated
   text would break the numeric evidence check in phase 4.
4. **Tables** are always their own chunk (`chunk_type="table"`) and additionally fan out into
   `build/params.jsonl`, so a technician question can be answered from a structured row *and*
   read in the original table.
5. **Figures** are their own chunks (`chunk_type="figure"`, `image` path) built from the AI
   description, the printed `Labels:` legend and, if both are missing, the section + page — a
   figure chunk is never empty and can always be returned as "the relevant image".
6. **Front matter** (copyright pages before the first heading) gets `section="Front matter"`
   instead of an empty section.

## Metadata on every chunk ("preserve source metadata")

```json
{
  "id": "flashbot_user_manual_v1_0_multilanguage-en-0012",
  "text": "| Item | Description |\n|---|---|\n| Robot dimensions (L×W×H) | 53.8×53.4×105.2 cm | …",
  "metadata": {
    "doc_id": "flashbot_user_manual_v1_0_multilanguage-en",
    "source_file": "data/manuals/FlashBot_User_Manual_V1.0-multilanguage/en.md",
    "manual": "FlashBot_User_Manual_V1.0-multilanguage",
    "doc_type": "robot",
    "product": "FlashBot",
    "title": "FlashBot User Manual V1.0",
    "language": "en",
    "source_pdf": "FlashBot_User_Manual_V1.0-multilanguage.pdf",
    "reference_pdf": "en.pdf",
    "section": "2. Product Components > 2.3 Technical Specifications",
    "section_number": "2.3",
    "section_title": "Technical Specifications",
    "page": 5,
    "pdf_page": 5,
    "page_end": 5,
    "chunk_type": "table"
  }
}
```

* `product` (e.g. `FlashBot`, `PUDU SH1`) is what users actually ask about and is the key for the
  cross-document questions of gap G2.
* `doc_type` separates robots (`manuals/`) from accessories (`accessories/`).
* `chunk_type` ∈ `text | table | figure`; figures add `image` (path relative to the markdown file).
* All values are `str | int | float | bool` so Chroma can store them for metadata filtering
  (`where={"language": "de", "product": "PUDU SH1"}`).

## Cleaning is conservative

Only noise is removed, never content: HTML comments, non-breaking spaces, zero-width characters,
trailing whitespace, repeated blank lines, and the `---` rules that separate printed pages
(they used to leak in as dozens of junk chunks). Page markers stay in the pipeline stage that
needs them (the chunker), then disappear from the chunk text.

## Quirks of this dataset that the parser handles

| Quirk | Handling |
|---|---|
| `---` between printed pages | dropped in cleaning |
| Figure description *after* the prose that explains the figure (SH1) | look-ahead of up to 6 lines; only a real `Figure description (AI):` block is taken, never a `> **Warning:**` callout |
| Figure with neither AI description nor labels (17 cases) | fallback text `Figure from '<section>', page N` — never empty |
| Labels written as `* **1** Lidar` | rendered as `Labels: 1 Lidar; 2 Touchscreen` |
| Tables where parameter and value share one cell | split heuristically (`"Body weight 23 kg"` → `Body weight` / `23 kg`) |
| Rows that repeat the table header | skipped in the parameter index |
| Page markers with only the PDF page | `page = null`, `pdf_page` set (3 chunks) — reported as a warning, never guessed |
| `Vac` / `Vdc` and German decimal commas | units normalised, `14,3 kg` → `value_number = 14.3` |

## Validation gate

`ingest/validate.py` (run automatically by `ingest.run`) checks:

* required metadata present on every chunk;
* no empty chunks (an empty chunk can never be retrieved);
* no page markers, front matter, HTML comments or `---` rules in chunk text;
* every figure chunk points at an image file that exists;
* unique chunk ids, prose within the chunk budget;
* every `en.md` / `de.md` / `it.md` in `data/` produced chunks;
* every parameter row links back to a source chunk;
* every `(robot, language)` pair has a valid product profile card with category, `key_use_case`, citation, and valid `chunk_ids`.

Current status: **0 errors, 1 warning** (the 3 chunks whose page markers only name the PDF page).

The report also lists the three manuals **without Italian** (FlashBot, GW1, Pager) — those are the
live demo for Bonus 02 (ask in Italian, answer from EN/DE sources).

## Next step

Phase 2 — embeddings + vector index: `python -m ingest.embed_index --provider local|gemini`
(see [`stack-decisions.md`](stack-decisions.md) §1 and §3).
