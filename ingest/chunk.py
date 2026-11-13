"""Step 2 of data processing: turn cleaned markdown into retrieval chunks.

Design decisions (see docs/pipeline-review.md section 5):

* Chunks follow the manual's own structure: numbered headings define sections
  and page markers give every chunk a page number for citations
  ("FlashBot manual, section 1.1, page 2").
* Figures (image + AI description + label list) become their own chunks with
  ``chunk_type="figure"`` and an ``image`` path, so the final answer can return
  the relevant image.
* Tables become their own chunks with ``chunk_type="table"`` -- exact parameter
  values ("what's the value of this parameter?") are much easier to find.
* No artificial overlap between chunks: every chunk carries full source
  metadata, and duplicated text would break numeric evidence checks later
  (the same number appearing in two chunks).

Each chunk is a dict: {"id", "text", "metadata"} -- JSONL-ready.

This module uses only the Python standard library.

Usage from code:
    from ingest.clean import load_manual
    from ingest.chunk import chunk_manual
    chunks = chunk_manual(load_manual("data/manuals/Some_Manual/en.md"))
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .clean import Manual

# Default chunk budget in characters (~550 tokens). Way below the 8192-token
# input limit of gemini-embedding-2 and small enough for precise retrieval.
DEFAULT_MAX_CHARS = 2200

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SECTION_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\s*[.:)]?\s+(.+)$")
_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}.*\|?\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")

# "Figure description (AI):" (EN) / "Bildbeschreibung (KI):" (DE) /
# "Descrizione della figura (IA):" (IT) -- with or without the marker suffix.
_FIG_DESC_RE = re.compile(
    r"^(?:Figure description(?:\s*\(AI\))?|Bildbeschreibung(?:\s*\(KI\))?|"
    r"Descrizione della figura(?:\s*\(IA\))?)\s*:?\s*(.*)$",
    re.IGNORECASE,
)
# "Labels:" (EN) / "Beschriftungen:" (DE) / "Etichette:" (IT)
_LABELS_HEADER_RE = re.compile(
    r"^(?:Labels?|Beschriftungen|Etichette)\s*:?\s*$", re.IGNORECASE
)


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"id": self.id, "text": self.text, "metadata": self.metadata}


# --------------------------------------------------------------------------
# Page markers: "*Page 3 (PDF page 4, left)*" / "*(PDF page 4, left)*" /
# German/Italian equivalents ("*Seite 3 (PDF-Seite 4, links)*", ...).
# The wording varies between manuals, so we recognise any short italic line
# that mentions a page word and pull the numbers out of it.
# --------------------------------------------------------------------------
_PAGE_WORDS = ("page", "seite", "pagina")
_SIDE_WORDS = {
    "left": "left", "links": "left", "link": "left", "linke": "left",
    "sinistra": "left", "gauche": "left",
    "right": "right", "rechts": "right", "rechte": "right",
    "destra": "right", "droite": "right",
}


def parse_page_marker(line: str) -> dict | None:
    """Return {'page': int|None, 'pdf_page': int} for a page marker line.

    ``page`` is the printed page number (used for citations), ``pdf_page`` the
    page in the original PDF. Pages without a printed number only have a PDF
    page. Returns None if the line is not a page marker.
    """
    s = line.strip()
    if not (s.startswith("*") and s.endswith("*") and len(s) <= 100):
        return None
    core = s.strip("*").strip()
    low = core.lower()
    if not any(w in low for w in _PAGE_WORDS):
        return None
    nums = [int(n) for n in re.findall(r"\d+", core)]
    if not nums:
        return None
    if len(nums) >= 2:
        return {"page": nums[0], "pdf_page": nums[1]}
    # Single number: it is the printed page unless the line mentions PDF.
    if "pdf" in low:
        return {"page": None, "pdf_page": nums[0]}
    return {"page": nums[0], "pdf_page": nums[0]}


def _strip_md_noise(line: str) -> str:
    """Reduce ``> *Figure description (AI): …*`` to its readable text."""
    text = line.lstrip().lstrip(">").strip()
    return text.replace("*", " ").strip()


def _is_table_start(lines: list[str], i: int) -> bool:
    """A table starts when a '|'-line is followed by a separator '|---|' line."""
    return (
        i + 1 < len(lines)
        and "|" in lines[i]
        and bool(_TABLE_SEP_RE.match(lines[i + 1]))
        and "-" in lines[i + 1]
    )


# --------------------------------------------------------------------------
# Parsing: markdown -> list of blocks ("para" | "table" | "figure")
# --------------------------------------------------------------------------
@dataclass
class _Block:
    kind: str          # "para" | "table" | "figure"
    text: str
    meta: dict = field(default_factory=dict)
    image: str | None = None


def _split_long_paragraph(text: str, max_chars: int) -> list[str]:
    """Split a very long paragraph at sentence boundaries under max_chars."""
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    parts, current = [], ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if len(candidate) > max_chars and current:
            parts.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


class _Parser:
    """Walks the cleaned markdown line by line and produces blocks."""

    def __init__(self, body: str):
        self.lines = body.splitlines()
        self.page: int | None = None
        self.pdf_page: int | None = None
        self.breadcrumb: list[tuple[int, str]] = []  # (heading level, title)
        self.blocks: list[_Block] = []
        self.para_buf: list[str] = []

    def section_meta(self) -> dict:
        titles = [title for _, title in self.breadcrumb]
        if not titles:
            section, number, title = "", None, ""
        else:
            section = " > ".join(titles)
            last = titles[-1]
            m = _SECTION_NUM_RE.match(last)
            number, title = (m.group(1), m.group(2)) if m else (None, last)
        return {
            "section": section,
            "section_number": number,
            "section_title": title,
            "page": self.page,
            "pdf_page": self.pdf_page,
        }

    def flush_para(self) -> None:
        if self.para_buf:
            meta = self.section_meta()
            self.blocks.append(_Block("para", "\n".join(self.para_buf), meta))
            self.para_buf = []

    def parse(self) -> list[_Block]:
        i, n = 0, len(self.lines)
        while i < n:
            line = self.lines[i]
            stripped = line.strip()

            # -- page marker: update state, never enters chunk text -----------
            if stripped and parse_page_marker(stripped) is not None:
                self.flush_para()
                marker = parse_page_marker(stripped)
                # Printed page may be unknown (markers that show only the PDF
                # page) -- keep it None rather than guessing.
                self.page = marker["page"]
                self.pdf_page = marker["pdf_page"]
                i += 1
                continue

            # -- blank line --------------------------------------------------
            if stripped == "":
                self.flush_para()
                i += 1
                continue

            # -- heading -----------------------------------------------------
            m = _HEADING_RE.match(stripped)
            if m:
                self.flush_para()
                level, title = len(m.group(1)), m.group(2).strip()
                while self.breadcrumb and self.breadcrumb[-1][0] >= level:
                    self.breadcrumb.pop()
                self.breadcrumb.append((level, title))
                i += 1
                continue

            # -- figure: image, then optional description and labels ---------
            img = _IMAGE_RE.match(stripped)
            if img:
                self.flush_para()
                i = self._parse_figure(img.group(1), img.group(2), i + 1)
                continue

            # -- table -------------------------------------------------------
            if _is_table_start(self.lines, i):
                self.flush_para()
                i = self._parse_table(i)
                continue

            # -- ordinary text line ------------------------------------------
            self.para_buf.append(stripped)
            i += 1

        self.flush_para()
        return self.blocks

    def _parse_figure(self, alt: str, image: str, i: int) -> int:
        n = len(self.lines)
        meta = self.section_meta()

        # description: "> *Figure description (AI): …*"
        while i < n and self.lines[i].strip() == "":
            i += 1
        desc = ""
        if i < n and self.lines[i].lstrip().startswith(">"):
            desc = _strip_md_noise(self.lines[i])
            m = _FIG_DESC_RE.match(desc)
            if m:
                desc = m.group(1).strip()
            i += 1

        # labels: "*Labels:" header, then a bulleted list
        labels: list[str] = []
        while i < n and self.lines[i].strip() == "":
            i += 1
        if i < n and _LABELS_HEADER_RE.match(_strip_md_noise(self.lines[i])):
            i += 1
            while i < n:
                bullet = _BULLET_RE.match(self.lines[i].strip())
                if bullet:
                    labels.append(bullet.group(1).strip())
                    i += 1
                elif self.lines[i].strip() == "":
                    i += 1  # blank lines inside / after the list are fine
                else:
                    break

        text = desc or alt
        if labels:
            text += "\nLabels: " + "; ".join(labels)
        figure_meta = dict(meta, chunk_type="figure", image=image)
        self.blocks.append(_Block("figure", text, figure_meta, image=image))
        return i

    def _parse_table(self, i: int) -> int:
        n = len(self.lines)
        meta = self.section_meta()
        rows: list[str] = []
        while i < n and "|" in self.lines[i] and self.lines[i].strip() != "":
            rows.append(self.lines[i].strip())
            i += 1
        self.blocks.append(_Block("table", "\n".join(rows), dict(meta, chunk_type="table")))
        return i


# --------------------------------------------------------------------------
# Packing: blocks -> chunks
# --------------------------------------------------------------------------
def chunk_manual(manual: Manual, max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    """Split one cleaned manual into retrieval chunks with source metadata."""
    blocks = _Parser(manual.body).parse()
    doc_id = _slug(manual.path.parent.name) + "-" + manual.language
    base_meta = {
        "source_file": str(manual.path),
        "manual": manual.path.parent.name,
        "title": manual.title,
        "language": manual.language,
    }

    chunks: list[Chunk] = []
    counter = 0

    def add(text: str, meta: dict) -> None:
        nonlocal counter
        counter += 1
        meta = dict(meta)
        meta.setdefault("chunk_type", "text")  # tables/figures set their own
        chunks.append(Chunk(f"{doc_id}-{counter:04d}", text, dict(base_meta, **meta)))

    # Pack consecutive "para" blocks up to max_chars; tables and figures
    # always become their own chunk.
    buf: list[_Block] = []
    buf_len = 0

    def flush_buf() -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        text = "\n\n".join(b.text for b in buf)
        meta = dict(buf[0].meta)
        meta["page_end"] = buf[-1].meta.get("page")
        add(text, meta)
        buf, buf_len = [], 0

    for block in blocks:
        if block.kind in ("table", "figure"):
            flush_buf()
            meta = dict(block.meta)
            meta["page_end"] = meta.get("page")
            add(block.text, meta)
            continue

        if buf_len + len(block.text) > max_chars and buf:
            flush_buf()
        for part in _split_long_paragraph(block.text, max_chars):
            if buf_len + len(part) > max_chars and buf:
                flush_buf()
            buf.append(_Block("para", part, block.meta))
            buf_len += len(part) + 2
    flush_buf()
    return chunks


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
