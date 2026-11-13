"""Tests for the clean + chunk pipeline (standard library only).

Run from the repository root:

    python -m unittest tests.test_ingest -v
"""

from __future__ import annotations

import unittest
from pathlib import Path

from ingest.chunk import chunk_manual, parse_page_marker
from ingest.clean import load_manual, normalize, parse_front_matter

FIXTURES = Path(__file__).parent / "fixtures" / "sample_manual"


class CleanTests(unittest.TestCase):
    def test_front_matter(self):
        meta, body = parse_front_matter(
            "---\ntitle: FlashBot User Manual\nlanguage: en\n---\n\n# 1. Intro\n"
        )
        self.assertEqual(meta["title"], "FlashBot User Manual")
        self.assertEqual(meta["language"], "en")
        self.assertTrue(body.startswith("\n# 1. Intro"))

    def test_normalize_removes_noise_keeps_content(self):
        body = normalize("Hello\xa0world <!-- drop me -->\n\n\n\nSecond\u200b line   \n")
        self.assertEqual(body, "Hello world\n\nSecond line\n")

    def test_load_manual(self):
        manual = load_manual(FIXTURES / "en.md")
        self.assertEqual(manual.language, "en")
        self.assertEqual(manual.title, "FlashBot User Manual")
        self.assertNotIn("internal note", manual.body)


class PageMarkerTests(unittest.TestCase):
    def test_full_marker(self):
        m = parse_page_marker("*Page 3 (PDF page 4, left)*")
        self.assertEqual(m, {"page": 3, "pdf_page": 4})

    def test_german_marker(self):
        m = parse_page_marker("*Seite 1 (PDF-Seite 2, links)*")
        self.assertEqual(m, {"page": 1, "pdf_page": 2})

    def test_pdf_only_marker(self):
        m = parse_page_marker("*(PDF-Seite 3)*")
        self.assertEqual(m, {"page": None, "pdf_page": 3})

    def test_italic_text_is_not_a_marker(self):
        self.assertIsNone(parse_page_marker("*this is emphasis about a page turn*"))
        self.assertIsNone(parse_page_marker("plain text"))


class ChunkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks = chunk_manual(load_manual(FIXTURES / "en.md"))

    def test_chunk_ids_are_unique(self):
        ids = [c.id for c in self.chunks]
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_page_marker_text_leaks_into_chunks(self):
        for c in self.chunks:
            self.assertNotIn("PDF page", c.text)

    def test_section_metadata(self):
        specs = [c for c in self.chunks if c.metadata.get("section_number") == "1.1"]
        self.assertTrue(specs)
        self.assertEqual(specs[0].metadata["section_title"], "Specifications")
        self.assertIn("1. Product Introduction", specs[0].metadata["section"])

    def test_table_chunk(self):
        tables = [c for c in self.chunks if c.metadata["chunk_type"] == "table"]
        self.assertEqual(len(tables), 1)
        self.assertIn("30 kg", tables[0].text)
        self.assertEqual(tables[0].metadata["page"], 2)

    def test_figure_chunk(self):
        figures = [c for c in self.chunks if c.metadata["chunk_type"] == "figure"]
        self.assertEqual(len(figures), 1)
        fig = figures[0]
        self.assertEqual(fig.metadata["image"], "images/fig_1.png")
        self.assertIn("tray compartments", fig.text)
        self.assertIn("Lidar", fig.text)
        self.assertEqual(fig.metadata["page"], 2)

    def test_text_chunk_metadata(self):
        texts = [c for c in self.chunks if c.metadata["chunk_type"] == "text"]
        self.assertTrue(texts)
        intro = [c for c in texts if "intelligent delivery robot" in c.text][0]
        self.assertEqual(intro.metadata["language"], "en")
        self.assertEqual(intro.metadata["title"], "FlashBot User Manual")
        self.assertEqual(intro.metadata["page"], 1)

    def test_chunks_respect_max_chars(self):
        small = chunk_manual(load_manual(FIXTURES / "en.md"), max_chars=150)
        for c in small:
            self.assertLessEqual(len(c.text), 150 + 1, f"chunk {c.id} too long")

    def test_german_metadata(self):
        chunks = chunk_manual(load_manual(FIXTURES / "de.md"))
        figures = [c for c in chunks if c.metadata["chunk_type"] == "figure"]
        self.assertEqual(figures[0].metadata["language"], "de")
        self.assertIn("Fächern", figures[0].text)
        # marker with only a PDF page: printed page honestly unknown
        tables = [c for c in chunks if c.metadata["chunk_type"] == "table"]
        self.assertIsNone(tables[0].metadata["page"])
        self.assertEqual(tables[0].metadata["pdf_page"], 3)


if __name__ == "__main__":
    unittest.main()
