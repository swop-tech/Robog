"""Tests for the data-processing pipeline (standard library only).

Run from the repository root:

    python -m unittest tests.test_ingest -v

The fixture in ``tests/fixtures/sample_manual`` is a miniature manual; the
quirks taken from the real dataset (horizontal rules, figures whose AI
description comes after the prose, merged table cells) are covered explicitly
below so the parser keeps handling them.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from ingest.chunk import (
    FRONT_MATTER_SECTION,
    chunk_manual,
    doc_type_for,
    parse_page_marker,
    product_name,
)
from ingest.clean import load_manual, normalize, parse_front_matter
from ingest.params import (
    build_param_index,
    extract_number,
    extract_unit,
    parse_table_rows,
    split_merged_cell,
)
from ingest.validate import validate_chunks, validate_params

FIXTURES = Path(__file__).parent / "fixtures" / "sample_manual"
DATA = Path(__file__).parent.parent / "data"


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

    def test_horizontal_rules_are_dropped(self):
        # The real manuals use "---" between printed pages; it must not become
        # a chunk (it used to produce dozens of junk "---" chunks).
        body = normalize("First paragraph.\n\n---\n\nSecond paragraph.\n\n* * *\n\nThird.\n")
        self.assertNotIn("---", body)
        self.assertNotIn("* * *", body)
        self.assertIn("Second paragraph.", body)
        self.assertIn("Third.", body)

    def test_table_separator_survives(self):
        table = "| Parameter | Value |\n|---|---|\n| Payload | 30 kg |\n"
        self.assertIn("|---|---|", normalize(table))

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

    def test_italian_marker(self):
        m = parse_page_marker("*Pagina 4 (pagina PDF 5, destra)*")
        self.assertEqual(m, {"page": 4, "pdf_page": 5})

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

    def test_source_metadata_is_preserved(self):
        meta = self.chunks[0].metadata
        self.assertEqual(meta["product"], "FlashBot")
        self.assertEqual(meta["source_pdf"], "FlashBot_User_Manual_V1.0-multilanguage.pdf")
        self.assertEqual(meta["reference_pdf"], "en.pdf")
        self.assertTrue(meta["doc_id"].endswith("-en"))
        for chunk in self.chunks:
            self.assertIn(chunk.metadata["doc_type"], ("robot", "accessory", "unknown"))
            self.assertTrue(chunk.metadata["section"])

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


class TranslationGroupTests(unittest.TestCase):
    """The EN/DE/IT versions of a section must share one translation_group."""

    def test_parallel_manuals_agree(self):
        from ingest.chunk import translation_group

        en = translation_group(
            "FlashBot",
            {"section_number": "2.3", "section_title": "Technical Specifications", "chunk_type": "table"},
        )
        de = translation_group(
            "FlashBot",
            {"section_number": "2.3", "section_title": "Technische Daten", "chunk_type": "table"},
        )
        self.assertEqual(en, de)

    def test_different_chunk_types_do_not_collide(self):
        from ingest.chunk import translation_group

        table = translation_group("FlashBot", {"section_number": "2.3", "chunk_type": "table"})
        text = translation_group("FlashBot", {"section_number": "2.3", "chunk_type": "text"})
        self.assertNotEqual(table, text)


class RealWorldQuirkTests(unittest.TestCase):
    """Behaviours taken from the actual PUDU manuals in ``data/``."""

    def _chunks(self, body: str):
        path = FIXTURES / "en.md"
        manual = load_manual(path)
        manual.body = normalize(body)
        return chunk_manual(manual)

    def test_figure_without_description_is_never_empty(self):
        body = (
            "# 6. Repair\n\n## 6.6 Filter cleaning\n\n*Page 14 (PDF page 10, right)*\n\n"
            "![](images/en-p010R-fig01.png)\n\n"
        )
        figures = [c for c in self._chunks(body) if c.metadata["chunk_type"] == "figure"]
        self.assertEqual(len(figures), 1)
        self.assertTrue(figures[0].text.strip())
        self.assertIn("6.6 Filter cleaning", figures[0].text)
        self.assertIn("page 14", figures[0].text)

    def test_description_after_prose_is_attached(self):
        body = (
            "# 6.6 Filter cleaning\n\n![](images/en-p010R-fig01.png)\n\n"
            "Remove the vacuum motor filter and rinse it.\n\n"
            "> *Figure description (AI):* The figure shows the vacuum motor filter being removed.\n"
        )
        chunks = self._chunks(body)
        figure = [c for c in chunks if c.metadata["chunk_type"] == "figure"][0]
        self.assertIn("vacuum motor filter being removed", figure.text)
        # the description must not be duplicated into a text chunk
        texts = " ".join(c.text for c in chunks if c.metadata["chunk_type"] == "text")
        self.assertNotIn("Figure description", texts)
        self.assertIn("Remove the vacuum motor filter", texts)

    def test_warning_box_is_not_taken_as_description(self):
        body = (
            "# 1. Safety\n\n![](images/x.png)\n\n"
            "> **Warning:** do not block the Lidar.\n"
        )
        figure = [c for c in self._chunks(body) if c.metadata["chunk_type"] == "figure"][0]
        self.assertNotIn("do not block the Lidar", figure.text)
        self.assertTrue(figure.text.strip())

    def test_labels_are_cleaned(self):
        body = (
            "# 2.2 Appearance\n\n![](images/x.png)\n\n"
            "> *Figure description (AI):* Front view of the robot with all its parts numbered.\n\n"
            "*Labels:*\n\n* **1** Emergency stop switch\n* **2** Lidar\n"
        )
        figure = [c for c in self._chunks(body) if c.metadata["chunk_type"] == "figure"][0]
        self.assertIn("Labels: 1 Emergency stop switch; 2 Lidar", figure.text)
        self.assertNotIn("**", figure.text)

    def test_content_before_first_heading_gets_front_matter_section(self):
        body = "*Page 1 (PDF page 1, left)*\n\nCopyright © 2025 Pudu Technology.\n"
        chunks = self._chunks(body)
        self.assertEqual(chunks[0].metadata["section"], FRONT_MATTER_SECTION)

    def test_product_name(self):
        self.assertEqual(product_name("FlashBot User Manual V1.0", "x"), "FlashBot")
        self.assertEqual(product_name("PUDU SH1 User Manual V1.2", "x"), "PUDU SH1")
        self.assertEqual(
            product_name("PUDU CC1 Series Self-Cleaning Docking Station (Base) User Manual", "x"),
            "PUDU CC1 Series Self-Cleaning Docking Station (Base)",
        )
        self.assertEqual(product_name("", "fallback_name"), "fallback_name")

    def test_doc_type(self):
        self.assertEqual(doc_type_for(Path("data/manuals/X/en.md")), "robot")
        self.assertEqual(doc_type_for(Path("data/accessories/X/en.md")), "accessory")
        self.assertEqual(doc_type_for(Path("elsewhere/en.md")), "unknown")


class ParamIndexTests(unittest.TestCase):
    TABLE = (
        "| Parameter | Value |\n|---|---|\n"
        "| Load capacity | 20 kg (10 kg per tray) |\n"
        "| Charger power output | 29.4 V, 3.5 A |\n"
        "| Operating humidity | ≤ 90 % |\n"
    )

    def test_parse_table_rows(self):
        rows = parse_table_rows(self.TABLE)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["parameter"], "Load capacity")
        self.assertEqual(rows[0]["value"], "20 kg (10 kg per tray)")
        self.assertEqual(rows[0]["unit"], "kg")
        self.assertEqual(rows[0]["value_number"], 20.0)
        self.assertEqual(rows[1]["column"], "Value")

    def test_header_repeated_rows_are_skipped(self):
        table = "| Item | Type |\n|---|---|\n| Item | Type |\n| Robot | Standard |\n"
        rows = parse_table_rows(table)
        self.assertEqual([r["parameter"] for r in rows], ["Robot"])

    def test_merged_cell_is_split(self):
        self.assertEqual(split_merged_cell("Body weight 23 kg"), ("Body weight", "23 kg"))
        self.assertEqual(
            split_merged_cell("Body size (L, W, H) 618 x 374 x 546 mm"),
            ("Body size (L, W, H)", "618 x 374 x 546 mm"),
        )
        # model names must not be split into a value
        self.assertEqual(split_merged_cell("Product Name CC1 Seires"), ("Product Name CC1 Seires", ""))

    def test_units_and_numbers(self):
        self.assertEqual(extract_unit("14,3 kg"), "kg")
        self.assertEqual(extract_unit("404 x 234 x 497 mm"), "mm")
        self.assertEqual(extract_unit("≤ 90 %"), "%")
        self.assertEqual(extract_unit("Body size (L, W, H)"), "")
        self.assertEqual(extract_number("14,3 kg"), 14.3)
        self.assertEqual(extract_number("≤ 90 %"), 90.0)
        self.assertIsNone(extract_number("Ladestation CC1"))

    def test_build_param_index_links_back_to_chunk(self):
        chunks = [c.to_json() for c in chunk_manual(load_manual(FIXTURES / "en.md"))]
        params = build_param_index(chunks)
        self.assertTrue(params)
        table_chunk = [c for c in chunks if c["metadata"]["chunk_type"] == "table"][0]
        payload = [p for p in params if p["parameter"] == "Payload"]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["chunk_id"], table_chunk["id"])
        self.assertEqual(payload[0]["value"], "30 kg")
        self.assertEqual(payload[0]["page"], 2)
        self.assertEqual(payload[0]["product"], "FlashBot")


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.chunks = [c.to_json() for c in chunk_manual(load_manual(FIXTURES / "en.md"))]

    def test_clean_chunks_pass(self):
        report = validate_chunks(self.chunks, FIXTURES)
        # FIXTURES holds de.md too, but only en.md was chunked here -> coverage
        # is the one expected complaint; everything else must be clean.
        self.assertEqual([e for e in report.errors if "produced no chunks" not in e], [])

    def test_empty_chunk_is_an_error(self):
        self.chunks[0]["text"] = "   "
        report = validate_chunks(self.chunks)
        self.assertTrue(any("empty chunk" in e for e in report.errors))

    def test_leaked_noise_is_an_error(self):
        self.chunks[0]["text"] += "\n*Page 9 (PDF page 9, left)*"
        report = validate_chunks(self.chunks)
        self.assertTrue(any("page marker" in e for e in report.errors))

    def test_missing_image_is_an_error(self):
        figure = [c for c in self.chunks if c["metadata"]["chunk_type"] == "figure"][0]
        figure["metadata"]["image"] = "images/does-not-exist.png"
        report = validate_chunks(self.chunks, FIXTURES)
        self.assertTrue(any("image" in e for e in report.errors))

    def test_param_validation(self):
        params = build_param_index(self.chunks)
        report = validate_params(params)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.info["param_rows"], len(params))


@unittest.skipUnless((DATA / "manuals").exists(), "dataset not present")
class RealDatasetTests(unittest.TestCase):
    """Smoke test on the real manuals: the pipeline must stay clean."""

    @classmethod
    def setUpClass(cls):
        cls.chunks = chunk_manual(load_manual(DATA / "manuals" / "FlashBot_User_Manual_V1.0-multilanguage" / "en.md"))
        cls.chunks += chunk_manual(load_manual(DATA / "manuals" / "PUDU_SH1_User_Manual_V1.2-multilanguage" / "en.md"))

    def test_real_chunks_are_never_empty(self):
        for chunk in self.chunks:
            self.assertTrue(chunk.text.strip(), f"empty chunk {chunk.id}")

    def test_real_chunks_have_no_horizontal_rules(self):
        for chunk in self.chunks:
            self.assertNotEqual(chunk.text.strip(), "---")

    def test_real_metadata(self):
        self.assertEqual(self.chunks[0].metadata["doc_type"], "robot")
        self.assertEqual(self.chunks[0].metadata["product"], "FlashBot")
        for chunk in self.chunks:
            self.assertTrue(chunk.metadata["source_pdf"])
            self.assertTrue(chunk.metadata["language"])

    def test_validation_passes_on_real_data(self):
        report = validate_chunks([c.to_json() for c in self.chunks])
        self.assertEqual(report.errors, [])


if __name__ == "__main__":
    unittest.main()
