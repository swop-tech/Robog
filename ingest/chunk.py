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
  ``ingest/params.py`` additionally turns the same tables into structured
  parameter rows (the parameter index of docs/pipeline-review.md, gap G1).
* No artificial overlap between chunks: every chunk carries full source
  metadata, and duplicated text would break numeric evidence checks later
  (the same number appearing in two chunks).

Every chunk keeps the full citation trail described in
docs/pipeline-review.md section 5: ``product``, ``manual``, ``doc_type``,
``language``, ``source_pdf``, ``section``, ``section_number``, ``section_title``,
``page`` / ``pdf_page``, ``chunk_type`` and -- for figures -- ``image``.

This module uses only the Python standard library.

Usage from code:
    from ingest.clean import load_manual
    from ingest.chunk import chunk_manual
    chunks = chunk_manual(load_manual("data/manuals/Some_Manual/en.md"))
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .clean import Manual

# Default chunk budget in characters (~550 tokens). Way below the 8192-token
# input limit of gemini-embedding-2 and small enough for precise retrieval.
DEFAULT_MAX_CHARS = 2200

# How many non-blank lines to look ahead for a figure's AI description. In some
# manuals (e.g. the SH1 one) the description sits *after* the prose that
# explains the figure instead of directly under the image.
FIGURE_DESC_LOOKAHEAD = 6

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SECTION_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\s*[.:)]?\s+(.+)$")
_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}.*\|?\s*$")
_BULLET_RE = re.compile(r"^[ \t]*[-*][ \t]+(.+?)\s*$")
_QUOTE_LINE_RE = re.compile(r"^\s*>\s?(.*)$")

# "Figure description (AI):" (EN) / "Bildbeschreibung (KI):" (DE) /
# "Descrizione della figura (IA):" (IT) -- with or without the marker suffix.
_FIG_DESC_RE = re.compile(
    r"^(?:Figure description(?:\s*\(AI\))?|Bildbeschreibung(?:\s*\(KI\))?|"
    r"Descrizione della figura(?:\s*\(IA\))?)\s*:?\s*(.*)$",
    re.IGNORECASE,
)
# "Labels:" (EN) / "Beschriftungen:" (DE) / "Etichette:" (IT)
_LABELS_HEADER_RE = re.compile(
    r"^(?:Labels?|Beschriftungen|Etichette)\s*:?$", re.IGNORECASE
)

# A blockquote that is a callout, not a figure description:
# "> **Warning:** do not block the Lidar."
_CALLOUT_RE = re.compile(r"^\s*>\s*(?:\*\*[^*]{1,60}\*\*|\*[^*]{1,60}\*)\s*[:：]?")

# Alt texts that carry no information ("![Image]", "![]", ...).
_GENERIC_ALT = {
    "", "image", "images", "figure", "fig", "figura", "abbildung", "abb",
    "image added from the pdf", "image (added from the pdf)",
}

# Section label used for content that appears before the first heading
# (copyright pages, cover notes).
FRONT_MATTER_SECTION = "Front matter"


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


def _clean_label(label: str) -> str:
    """``"**1** Emergency stop switch"`` -> ``"1 Emergency stop switch"``."""
    text = label.replace("**", "").replace("*", " ")
    return re.sub(r"\s+", " ", text).strip()


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


def _quote_block(lines: list[str], i: int) -> tuple[str, int]:
    """Read a markdown blockquote starting at ``i``; return its text and the
    index of the first line after it."""
    parts: list[str] = []
    n = len(lines)
    while i < n:
        m = _QUOTE_LINE_RE.match(lines[i])
        if not m:
            break
        parts.append(m.group(1).strip())
        i += 1
    text = " ".join(p for p in parts if p)
    return _strip_md_noise("> " + text) if text else "", i


def _figure_text(desc: str, alt: str, labels: list[str], meta: dict) -> str:
    """Build the searchable text of a figure chunk.

    The AI description is the main content; the ``Labels:`` list adds the
    printed callouts ("1 Emergency stop switch"). A few figures have neither,
    so the section breadcrumb + page keep them findable (and every figure chunk
    has text -- an empty chunk can never be retrieved).
    """
    text = desc.strip()
    alt_clean = alt.strip()
    if len(text) < 40 and alt_clean.lower() not in _GENERIC_ALT:
        text = (text + " " + alt_clean).strip()
    if len(text) < 40:
        where = meta.get("section") or FRONT_MATTER_SECTION
        page = meta.get("page")
        place = f"page {page}" if page else f"PDF page {meta.get('pdf_page')}"
        head = f"Figure from '{where}', {place}."
        text = f"{head} {text}".strip() if text else head
    if labels:
        text += "\nLabels: " + "; ".join(labels)
    return text.strip()


class _Parser:
    """Walks the cleaned markdown line by line and produces blocks."""

    def __init__(self, body: str):
        self.lines = body.splitlines()
        self.page: int | None = None
        self.pdf_page: int | None = None
        self.breadcrumb: list[tuple[int, str]] = []  # (heading level, title)
        self.blocks: list[_Block] = []
        self.para_buf: list[str] = []
        self.consumed: set[int] = set()  # lines already taken by a figure

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
            if i in self.consumed:
                i += 1
                continue
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

    # -- figures -------------------------------------------------------------
    def _parse_figure(self, alt: str, image: str, i: int) -> int:
        n = len(self.lines)
        meta = self.section_meta()

        # 1) description directly under the image: "> *Figure description (AI): …*"
        #    A warning/note callout in the same place is ordinary text.
        desc = ""
        j = self._skip_blank(i)
        if j < n and _QUOTE_LINE_RE.match(self.lines[j]):
            quoted, end = _quote_block(self.lines, j)
            m = _FIG_DESC_RE.match(quoted)
            if m:
                desc = m.group(1).strip()
                i = end
            elif not _CALLOUT_RE.match(self.lines[j]):
                # a plain quote under the image is its description
                desc = quoted
                i = end

        # 2) otherwise look a few lines ahead (some manuals put the prose
        #    between the image and its description)
        if not desc.strip():
            found = self._find_figure_desc(i)
            if found is not None:
                desc, start, end = found
                self.consumed.update(range(start, end))

        # 3) labels: "*Labels:" header, then a bulleted list
        labels: list[str] = []
        j = self._skip_blank(i)
        if j < n and _LABELS_HEADER_RE.match(_strip_md_noise(self.lines[j])):
            j += 1
            while j < n:
                bullet = _BULLET_RE.match(self.lines[j])
                if bullet:
                    label = _clean_label(bullet.group(1))
                    if label:
                        labels.append(label)
                    j += 1
                elif self.lines[j].strip() == "":
                    j += 1  # blank lines inside / after the list are fine
                else:
                    break
            i = j

        text = _figure_text(desc, alt, labels, meta)
        figure_meta = dict(meta, chunk_type="figure", image=image)
        self.blocks.append(_Block("figure", text, figure_meta, image=image))
        return i

    def _skip_blank(self, i: int) -> int:
        n = len(self.lines)
        while i < n and self.lines[i].strip() == "":
            i += 1
        return i

    def _find_figure_desc(self, i: int) -> tuple[str, int, int] | None:
        """Look ahead for ``> *Figure description …*`` (stops at structure)."""
        n = len(self.lines)
        seen = 0
        j = i
        while j < n and seen < FIGURE_DESC_LOOKAHEAD:
            line = self.lines[j].strip()
            if line == "":
                j += 1
                continue
            if line.startswith("#") or _IMAGE_RE.match(line) or _is_table_start(self.lines, j):
                return None
            if _QUOTE_LINE_RE.match(self.lines[j]):
                text, end = _quote_block(self.lines, j)
                m = _FIG_DESC_RE.match(text)
                if m:  # only take a real figure description, not a warning box
                    return m.group(1).strip(), j, end
            seen += 1
            j += 1
        return None

    # -- tables --------------------------------------------------------------
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
def product_name(title: str, manual: str) -> str:
    """``"FlashBot User Manual V1.0"`` -> ``"FlashBot"``.

    The product name is what users ask about ("does the FlashBot fit in a
    lift?"), so it is worth carrying on every chunk.
    """
    name = re.sub(r"\s+User\s+Manual(\s+V[\d.]+)?\s*$", "", title, flags=re.IGNORECASE)
    name = re.sub(r"\s+V\d+(\.\d+)*\s*$", "", name).strip()
    return name or manual


def translation_group(product: str, meta: dict) -> str:
    """Stable key shared by the EN/DE/IT versions of the same passage.

    The manuals are parallel translations, so the same section holds the same
    content in three languages. Retrieval can then collapse cross-language
    duplicates ("the same paragraph three times") while keeping several chunks
    of the *same* language. Approximate by design: it groups a section, not a
    sentence (translations split paragraphs differently).
    """
    anchor = meta.get("section_number") or meta.get("section_title") or FRONT_MATTER_SECTION
    kind = meta.get("chunk_type") or "text"
    return f"{_slug(product)}|{_slug(str(anchor))}|{kind}"


def doc_id_for(manual_dir: str | Path, language: str) -> str:
    """The document id of one language file: folder slug + language.

    Public because the API's media endpoint has to rebuild a chunk's ``doc_id``
    from a path on disk (``api/media.py``); keeping the rule in one place is
    what stops the two from drifting apart.
    """
    return f"{_slug(Path(manual_dir).name)}-{str(language or '').strip().lower()}"


def doc_type_for(path: Path) -> str:
    """``manuals/`` folders hold robots, ``accessories/`` folders accessories."""
    parts = [p.lower() for p in path.parts]
    if "accessories" in parts:
        return "accessory"
    if "manuals" in parts:
        return "robot"
    return "unknown"


def chunk_manual(manual: Manual, max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    """Split one cleaned manual into retrieval chunks with source metadata."""
    blocks = _Parser(manual.body).parse()
    doc_id = doc_id_for(manual.path.parent, manual.language)
    base_meta = {
        "doc_id": doc_id,
        "source_file": str(manual.path),
        "manual": manual.path.parent.name,
        "doc_type": doc_type_for(manual.path),
        "product": product_name(manual.title, manual.path.parent.name),
        "title": manual.title,
        "language": manual.language,
        "source_pdf": str(manual.meta.get("source_pdf", "")),
        "reference_pdf": str(manual.meta.get("reference_pdf", "")),
    }

    chunks: list[Chunk] = []
    counter = 0

    def add(text: str, meta: dict) -> None:
        nonlocal counter
        text = text.strip()
        if not text:  # never emit an empty chunk: it cannot be retrieved
            return
        counter += 1
        meta = dict(meta)
        meta.setdefault("chunk_type", "text")  # tables/figures set their own
        meta.setdefault("translation_group", translation_group(base_meta["product"], meta))
        if not meta.get("section"):
            meta["section"] = FRONT_MATTER_SECTION
            meta["section_title"] = meta.get("section_title") or FRONT_MATTER_SECTION
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
