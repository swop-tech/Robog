"""Tests for the FastAPI backend (``api/``): ``/search``, ``/ask``, ``/products``, ``/media``.

Run from the repository root:

    python -m unittest tests.test_api -v

Nothing here needs an API key, a network connection or an embedding model: the
answer composer runs its deterministic offline formatter, and the service is
built from a tiny hand-written manual (``tests/fixtures/sample_manual``, EN +
DE + one PNG) that the real ingest pipeline turns into a temporary build
directory. The corpus tests at the bottom run against ``data/`` and are skipped
when the dataset is absent.

The tests cover what the endpoints promise:

* ``/search`` answers with ``SearchResult.to_dict()`` **plus** an absolute
  ``image_url`` on figure hits (Bonus 01, ``docs/pipeline-review.md`` §9a);
* ``/ask`` returns ``{answer, language, route, citations, figures, params,
  verification, cross_lingual}`` and never an unvalidated answer (G5);
* ``/products`` serves the catalog, the localized profile cards and the
  parameter lookup;
* ``/media/{doc_id}/{filename}`` serves only ``data/**/images/*.png`` and 404s
  every traversal attempt.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from api.media import MediaStore, is_within, safe_filename
from ingest.run import main as ingest_main

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "sample_manual"
DATA = ROOT / "data"

try:  # the API package is optional: the rest of the repo runs without FastAPI
    from fastapi.testclient import TestClient

    from api import client as api_client
    from api.app import create_app
    from api.config import Settings
    from api.service import RobogService

    HAVE_API = True
except ModuleNotFoundError:  # pragma: no cover - depends on the environment
    HAVE_API = False

# The keys §9a/§3 of the docs promise for the two main endpoints.
SEARCH_CONTRACT = {
    "query", "language", "route", "route_reason", "policy", "cross_lingual",
    "products", "tiers_used", "confidence", "coverage", "notes", "params",
    "profiles", "hits",
}
ASK_CONTRACT = {
    "answer", "language", "route", "citations", "figures", "params",
    "verification", "cross_lingual",
}


def build_index(data_dir: Path, out_dir: Path) -> Path:
    """Run the real ingest pipeline (``python -m ingest.run``) into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "chunks.jsonl"
    argv, sys.argv = sys.argv, [
        "ingest.run",
        "--data", str(data_dir),
        "--out", str(out),
        "--no-validate",
    ]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            ingest_main()
    finally:
        sys.argv = argv
    return out


def make_settings(data_dir: Path, chunks: Path, **overrides):
    return Settings(
        data_dir=data_dir,
        chunks_path=chunks,
        params_path=chunks.parent / "params.jsonl",
        profiles_path=chunks.parent / "profiles.jsonl",
        **overrides,
    )


# ---------------------------------------------------------------------------
# media resolver: pure unit tests, no corpus and no FastAPI
# ---------------------------------------------------------------------------
class MediaStoreTest(unittest.TestCase):
    """``doc_id`` -> ``images/`` mapping and the four traversal checks."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="robog-media-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.images = self.tmp / "data" / "manuals" / "BellaBot_User_Manual-multilanguage" / "images"
        self.images.mkdir(parents=True)
        (self.images / "en-p003R-fig01.png").write_bytes(b"\x89PNG\r\n")
        (self.images / "en-p003R-fig01.png.txt").write_text("not a figure")
        manual_dir = self.images.parent
        (manual_dir / "en.md").write_text("---\nlanguage: en\n---\n", encoding="utf-8")
        (manual_dir / "de.md").write_text("---\nlanguage: de\n---\n", encoding="utf-8")
        # A secret outside the data root, and a symlink trying to reach it.
        (self.tmp / "secret.env").write_text("GEMINI_API_KEY=nope")
        try:
            os.symlink(self.tmp / "secret.env", self.images / "evil.png")
            self.have_symlinks = True
        except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
            self.have_symlinks = False
        self.store = MediaStore.build(self.tmp / "data")

    def test_scans_documents_and_languages(self) -> None:
        self.assertEqual(
            self.store.doc_ids,
            ["bellabot_user_manual_multilanguage-de", "bellabot_user_manual_multilanguage-en"],
        )
        self.assertEqual(self.store.files_for("bellabot_user_manual_multilanguage-en"),
                         ["en-p003R-fig01.png"])  # the .png.txt twin is not servable
        self.assertEqual(self.store.file_count(), 1)  # one folder, two languages

    def test_resolve_returns_the_file(self) -> None:
        path = self.store.resolve("bellabot_user_manual_multilanguage-en", "en-p003R-fig01.png")
        self.assertIsNotNone(path)
        self.assertEqual(path.read_bytes(), b"\x89PNG\r\n")

    def test_rejects_traversal_filenames(self) -> None:
        for name in ("..", "../secret.env", "../../secret.env", "..%2f..%2fsecret.env",
                     "a/../../secret.env", "/etc/passwd", "C:\\secret.env", "",
                     "   ", ".hidden.png", "sub/en-p003R-fig01.png", "evil\x00.png"):
            self.assertIsNone(safe_filename(name), f"{name!r} should not be a safe filename")
            self.assertIsNone(
                self.store.resolve("bellabot_user_manual_multilanguage-en", name),
                f"{name!r} must not resolve",
            )

    def test_rejects_unknown_doc_and_non_png(self) -> None:
        self.assertIsNone(self.store.resolve("no-such-doc", "en-p003R-fig01.png"))
        self.assertIsNone(self.store.resolve("bellabot_user_manual_multilanguage-en", "en.md"))
        self.assertIsNone(self.store.resolve("bellabot_user_manual_multilanguage-en",
                                             "en-p003R-fig01.png.txt"))
        self.assertIsNone(self.store.resolve("bellabot_user_manual_multilanguage-en",
                                             "missing.png"))

    def test_symlink_cannot_escape_the_images_folder(self) -> None:
        if not self.have_symlinks:
            self.skipTest("symlinks are not supported here")
        self.assertIsNone(self.store.resolve("bellabot_user_manual_multilanguage-en", "evil.png"))

    def test_is_within(self) -> None:
        root = self.tmp / "data"
        self.assertTrue(is_within(root / "a" / "b.png", root))
        self.assertTrue(is_within(root, root))
        self.assertFalse(is_within(self.tmp / "secret.env", root))

    def test_url_for_quotes_and_checks_existence(self) -> None:
        doc = "bellabot_user_manual_multilanguage-en"
        self.assertEqual(
            self.store.url_for(doc, "images/en-p003R-fig01.png"),
            "/media/bellabot_user_manual_multilanguage-en/en-p003R-fig01.png",
        )
        self.assertEqual(
            self.store.url_for(doc, "images/en-p003R-fig01.png", base="https://robog.example/"),
            "https://robog.example/media/bellabot_user_manual_multilanguage-en/en-p003R-fig01.png",
        )
        self.assertEqual(
            self.store.url_for("PUDU CC1 (Base)", "images/x.png", base=""),
            None,  # unknown doc_id -> no URL rather than a broken one
        )
        self.assertIsNone(self.store.url_for(doc, None))
        self.assertIsNone(self.store.url_for(None, "images/en-p003R-fig01.png"))
        self.assertIsNone(self.store.url_for(doc, "images/nope.png"))

    def test_chunk_metadata_wins_over_the_filesystem_guess(self) -> None:
        chunk = {
            "id": "x-0001",
            "text": "figure",
            "metadata": {
                "doc_id": "custom-doc-id",
                "source_file": str(self.images.parent / "en.md"),
                "image": "images/en-p003R-fig01.png",
            },
        }
        self.assertEqual(self.store.register_chunks([chunk]), 1)
        self.assertIsNotNone(self.store.resolve("custom-doc-id", "en-p003R-fig01.png"))

    def test_build_without_a_data_dir_is_empty_not_broken(self) -> None:
        store = MediaStore.build(self.tmp / "does-not-exist")
        self.assertEqual(store.doc_ids, [])
        self.assertEqual(store.file_count(), 0)
        self.assertIsNone(store.resolve("anything", "x.png"))


# ---------------------------------------------------------------------------
# the endpoints, on the tiny fixture corpus
# ---------------------------------------------------------------------------
@unittest.skipUnless(HAVE_API, "FastAPI is not installed (pip install -r requirements-api.txt)")
class ApiTest(unittest.TestCase):
    """Contract tests against ``tests/fixtures/sample_manual`` (EN + DE + PNG)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="robog-api-"))
        chunks = build_index(FIXTURE, cls.tmp / "build")
        cls.settings = make_settings(FIXTURE, chunks, media_max_age=3600)
        cls.service = RobogService.from_settings(cls.settings)
        cls.client = TestClient(create_app(cls.settings, service=cls.service))
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        shutil.rmtree(cls.tmp, True)

    # -- meta ---------------------------------------------------------------
    def test_index_lists_the_endpoints(self) -> None:
        payload = self.client.get("/").json()
        self.assertIn("GET /search", payload["endpoints"])
        self.assertIn("POST /ask", payload["endpoints"])
        self.assertIn("GET /products", payload["endpoints"])
        self.assertIn("GET /media/{doc_id}/{filename}", payload["endpoints"])

    def test_health_reports_the_indexes(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["chunks"], 7)
        self.assertEqual(payload["params"], 5)
        self.assertEqual(payload["media_documents"], 2)
        self.assertEqual(payload["media_files"], 1)
        self.assertEqual(payload["provider"]["provider"], "offline")
        self.assertIsNone(payload["detail"])

    def test_openapi_documents_every_route(self) -> None:
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertEqual(
            sorted(paths),
            ["/", "/ask", "/health", "/media/{doc_id}/{filename}", "/products", "/search"],
        )

    # -- GET /search --------------------------------------------------------
    def test_search_returns_the_to_dict_contract(self) -> None:
        payload = self.client.get("/search", params={"q": "What is the payload of the FlashBot?"}).json()
        self.assertTrue(SEARCH_CONTRACT <= set(payload), sorted(SEARCH_CONTRACT - set(payload)))
        self.assertEqual(payload["query"], "What is the payload of the FlashBot?")
        self.assertEqual(payload["language"], "en")
        self.assertEqual(payload["route"], "lookup")
        self.assertIn("media", payload)

    def test_search_lookup_returns_parameter_rows(self) -> None:
        payload = self.client.get("/search", params={"q": "What is the payload of the FlashBot?"}).json()
        values = {(row["parameter"], row["value"]) for row in payload["params"]}
        self.assertIn(("Payload", "30 kg"), values)
        for row in payload["params"]:
            self.assertTrue(row["citation"])
            self.assertIn("p. 2", row["citation"])

    def test_search_figure_hits_carry_an_absolute_image_url(self) -> None:
        payload = self.client.get(
            "/search", params={"q": "Show me a picture of the FlashBot", "k": 3}
        ).json()
        self.assertEqual(payload["route"], "figure")
        self.assertTrue(payload["hits"], "the figure route must return at least one figure")
        top = payload["hits"][0]
        self.assertEqual(top["chunk_type"], "figure")
        self.assertEqual(top["image"], "images/fig_1.png")
        self.assertEqual(
            top["image_url"], "http://testserver/media/sample_manual-en/fig_1.png"
        )
        # The URL is not decoration: it serves the PNG.
        fetched = self.client.get(top["image_url"].replace("http://testserver", ""))
        self.assertEqual(fetched.status_code, 200)

    def test_search_detects_the_question_language(self) -> None:
        payload = self.client.get("/search", params={"q": "Wieviel Zuladung hat der Roboter?"}).json()
        self.assertEqual(payload["language"], "de")
        self.assertIn("de", payload["policy"])
        # The German tier is tried first, then the fallback (Bonus 02).
        self.assertTrue(payload["tiers_used"][0].startswith("de:"), payload["tiers_used"])

    def test_search_honours_filters_and_limits(self) -> None:
        payload = self.client.get(
            "/search",
            params={"q": "Wieviel Zuladung hat der Roboter?", "k": 1, "language": "de",
                    "chunk_type": "table"},
        ).json()
        self.assertEqual(len(payload["hits"]), 1)
        self.assertEqual({hit["chunk_type"] for hit in payload["hits"]}, {"table"})
        # follow_user_language=false is the naive all-languages baseline: one tier.
        baseline = self.client.get(
            "/search",
            params={"q": "Wieviel Zuladung hat der Roboter?", "follow_user_language": "false"},
        ).json()
        self.assertEqual(len(baseline["tiers_used"]), 1)
        self.assertTrue(baseline["tiers_used"][0].startswith("any:"), baseline["tiers_used"])

    def test_search_validates_its_parameters(self) -> None:
        self.assertEqual(self.client.get("/search", params={"q": ""}).status_code, 422)
        self.assertEqual(self.client.get("/search", params={}).status_code, 422)
        self.assertEqual(self.client.get("/search", params={"q": "x", "k": 0}).status_code, 422)
        self.assertEqual(self.client.get("/search", params={"q": "x", "k": 999}).status_code, 422)
        self.assertEqual(
            self.client.get("/search", params={"q": "x", "language": "fr"}).status_code, 422
        )
        self.assertEqual(
            self.client.get("/search", params={"q": "x", "chunk_type": "video"}).status_code, 422
        )

    # -- POST /ask ----------------------------------------------------------
    def test_ask_returns_the_documented_contract(self) -> None:
        response = self.client.post("/ask", json={"question": "Wieviel Zuladung hat der FlashBot?"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(ASK_CONTRACT <= set(payload), sorted(ASK_CONTRACT - set(payload)))
        self.assertEqual(payload["language"], "de")
        self.assertEqual(payload["route"], "lookup")
        self.assertIn("30 kg", payload["answer"])
        self.assertTrue(payload["grounded"])
        self.assertTrue(payload["verification"]["grounded"])
        self.assertTrue(payload["citations"])
        self.assertEqual(payload["provider"], "offline")

    def test_ask_params_and_citations_are_checkable(self) -> None:
        payload = self.client.post("/ask", json={"question": "What is the payload of the FlashBot?"}).json()
        self.assertIn(("Payload", "30 kg"), {(p["parameter"], p["value"]) for p in payload["params"]})
        for citation in payload["citations"]:
            self.assertIn("citation", citation)
            self.assertIn("page", citation)
            self.assertIn("image_url", citation)
        self.assertGreaterEqual(payload["verification"]["citation_rate"], 1.0)
        self.assertEqual(payload["verification"]["numbers_unverified"], 0)

    def test_ask_answers_in_the_question_language_and_discloses_cross_lingual(self) -> None:
        # The fixture's German manual has no "Max speed" row: the answer is
        # German, the evidence is English, and the response says so.
        payload = self.client.post(
            "/ask", json={"question": "Wie schnell fährt der FlashBot maximal?"}
        ).json()
        self.assertEqual(payload["language"], "de")
        self.assertTrue(payload["cross_lingual"])
        self.assertEqual(set(payload["source_languages"]) - {"de"}, {"en"})

    def test_ask_figure_route_returns_fetchable_figures(self) -> None:
        payload = self.client.post(
            "/ask", json={"question": "Show me a picture of the FlashBot"}
        ).json()
        self.assertEqual(payload["route"], "figure")
        self.assertTrue(payload["figures"])
        figure = payload["figures"][0]
        self.assertEqual(figure["image"], "images/fig_1.png")
        self.assertEqual(figure["image_url"], "http://testserver/media/sample_manual-en/fig_1.png")
        self.assertEqual(
            self.client.get("/media/sample_manual-en/fig_1.png").status_code, 200
        )

    def test_ask_includes_search_and_evidence_only_when_asked(self) -> None:
        lean = self.client.post("/ask", json={"question": "What is the payload of the FlashBot?"}).json()
        self.assertIsNone(lean["search"])
        self.assertIsNone(lean["evidence"])
        full = self.client.post(
            "/ask",
            json={
                "question": "What is the payload of the FlashBot?",
                "include_search": True,
                "include_evidence": True,
            },
        ).json()
        self.assertTrue(full["search"]["hits"])
        self.assertIn("image_url", full["search"]["hits"][0])
        self.assertTrue(full["evidence"]["refs"])

    def test_ask_per_request_provider_override(self) -> None:
        payload = self.client.post(
            "/ask", json={"question": "What is the payload of the FlashBot?", "provider": "offline"}
        ).json()
        self.assertEqual(payload["provider"], "offline")

    def test_ask_validates_its_body(self) -> None:
        self.assertEqual(self.client.post("/ask", json={}).status_code, 422)
        self.assertEqual(self.client.post("/ask", json={"question": ""}).status_code, 422)
        self.assertEqual(self.client.post("/ask", json={"question": "x" * 2001}).status_code, 422)
        self.assertEqual(self.client.post("/ask", json={"question": "x", "k": 0}).status_code, 422)
        self.assertEqual(
            self.client.post("/ask", json={"question": "x", "language": "fr"}).status_code, 422
        )
        self.assertEqual(
            self.client.post("/ask", json={"question": "x", "provider": "gpt"}).status_code, 422
        )
        self.assertEqual(
            self.client.post(
                "/ask", content=b"not json", headers={"content-type": "application/json"}
            ).status_code,
            422,
        )

    # -- GET /products ------------------------------------------------------
    def test_products_catalog_has_cards_and_parameters(self) -> None:
        payload = self.client.get("/products", params={"language": "en"}).json()
        self.assertEqual(payload["language"], "en")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["parameters_total"], 5)
        entry = next(p for p in payload["products"] if p["product"] == "FlashBot")
        self.assertEqual(entry["languages"], ["en"])
        self.assertTrue(entry["documents"])
        self.assertEqual(entry["profile"]["language"], "en")
        self.assertIn("image_url", entry["profile"])
        self.assertIn("Payload", {row["parameter"] for row in entry["parameters"]})
        self.assertEqual(entry["parameter_count"], 3)

    def test_products_parameter_lookup_is_lexicon_aware(self) -> None:
        # "payload" is the user's word; the German table prints "Nutzlast".
        payload = self.client.get(
            "/products", params={"language": "de", "parameter": "payload"}
        ).json()
        german = next(p for p in payload["products"] if "Nutzlast" in
                      {row["parameter"] for row in p["parameters"]})
        self.assertEqual(
            [row["value"] for row in german["parameters"] if row["parameter"] == "Nutzlast"],
            ["30 kg"],
        )

    def test_products_free_text_parameter_search(self) -> None:
        payload = self.client.get("/products", params={"q": "payload FlashBot", "language": "en"}).json()
        matches = payload["parameter_search"]["matches"]
        self.assertIn(("Payload", "30 kg"), {(m["parameter"], m["value"]) for m in matches})

    def test_products_filters(self) -> None:
        payload = self.client.get(
            "/products", params={"language": "de", "product": "FlashBot", "include_params": "false"}
        ).json()
        # "FlashBot" matches the fixture's EN product and its German twin by name.
        self.assertEqual(payload["count"], 2)
        self.assertTrue(all("flashbot" in p["product"].lower() for p in payload["products"]))
        self.assertTrue(all(p["parameters"] == [] for p in payload["products"]))
        self.assertEqual(payload["filters"]["product"], "FlashBot")
        self.assertEqual(
            self.client.get("/products", params={"product": "nope"}).json()["count"], 0
        )

    def test_products_validates_language(self) -> None:
        self.assertEqual(self.client.get("/products", params={"language": "fr"}).status_code, 422)
        self.assertEqual(
            self.client.get("/products", params={"params_per_product": 9999}).status_code, 422
        )

    # -- GET /media ---------------------------------------------------------
    def test_media_serves_the_png_with_cache_headers(self) -> None:
        response = self.client.get("/media/sample_manual-de/fig_1.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["cache-control"], "public, max-age=3600")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.content, (FIXTURE / "images" / "fig_1.png").read_bytes())

    def test_media_head_is_allowed(self) -> None:
        self.assertEqual(self.client.head("/media/sample_manual-en/fig_1.png").status_code, 200)

    def test_media_rejects_traversal_and_everything_else(self) -> None:
        attempts = [
            "/media/sample_manual-en/..%2f..%2f..%2f..%2fREADME.md",
            "/media/sample_manual-en/%2e%2e%2f%2e%2e%2fsecret.txt",
            "/media/sample_manual-en/....//....//README.md",
            "/media/sample_manual-en/..",
            "/media/sample_manual-en/.%2e",
            "/media/sample_manual-en/en.md",
            "/media/sample_manual-en/fig_1.png.txt",
            "/media/sample_manual-en/nope.png",
            "/media/sample_manual-en/.hidden.png",
            "/media/no-such-doc/fig_1.png",
            "/media/%2e%2e/fig_1.png",
        ]
        for url in attempts:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_media_cannot_read_the_repository(self) -> None:
        for url in (
            "/media/sample_manual-en/..%2f..%2f..%2f..%2f.env",
            "/media/sample_manual-en/..%2f..%2f..%2f..%2frequirements-rag.txt",
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 404)
            self.assertNotIn("GEMINI", response.text)

    # -- CORS ---------------------------------------------------------------
    def test_cors_headers_for_a_browser_client(self) -> None:
        response = self.client.get(
            "/health", headers={"Origin": "https://chat.example"}
        )
        self.assertEqual(response.headers.get("access-control-allow-origin"), "*")


# ---------------------------------------------------------------------------
# degraded mode: no build/chunks.jsonl
# ---------------------------------------------------------------------------
@unittest.skipUnless(HAVE_API, "FastAPI is not installed")
class DegradedModeTest(unittest.TestCase):
    """A missing index is a 503 with instructions, not a dead service."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="robog-empty-"))
        cls.settings = make_settings(FIXTURE, cls.tmp / "build" / "chunks.jsonl")
        cls.service = RobogService.from_settings(cls.settings)
        cls.client = TestClient(create_app(cls.settings, service=cls.service))
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        shutil.rmtree(cls.tmp, True)

    def test_service_is_not_ready(self) -> None:
        self.assertFalse(self.service.ready)
        self.assertIn("not found", self.service.error or "")

    def test_index_endpoints_answer_503_with_the_fix(self) -> None:
        for call in (
            lambda: self.client.get("/search", params={"q": "payload"}),
            lambda: self.client.post("/ask", json={"question": "payload?"}),
            lambda: self.client.get("/products"),
        ):
            response = call()
            self.assertEqual(response.status_code, 503)
            body = response.json()
            self.assertEqual(body["detail"], "search index unavailable")
            self.assertEqual(body["fix"], "python -m ingest.run --data data")

    def test_health_is_503_but_informative(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["chunks"], 0)
        self.assertEqual(payload["media_documents"], 2)  # media needs only data/

    def test_media_still_works_without_an_index(self) -> None:
        self.assertEqual(self.client.get("/media/sample_manual-en/fig_1.png").status_code, 200)

    def test_a_corrupt_index_is_reported_not_fatal(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="robog-corrupt-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "chunks.jsonl").write_text("{not json\n", encoding="utf-8")
        service = RobogService.from_settings(make_settings(FIXTURE, tmp / "chunks.jsonl"))
        self.assertFalse(service.ready)
        self.assertIn("JSON", service.error or "")
        self.assertEqual(len(service.media), 2)   # the filesystem scan still mapped the docs
        self.assertIsNotNone(service.resolve_media("sample_manual-en", "fig_1.png"))

    def test_from_env_reads_the_environment(self) -> None:
        for key, value in {
            "ROBOG_DATA_DIR": "somewhere/data",
            "ROBOG_API_DEFAULT_K": "3",
            "ROBOG_API_MAX_K": "7",
            "ROBOG_API_MEDIA_MAX_AGE": "60",
            "ROBOG_API_VECTOR": "auto",
            "ROBOG_API_CORS_ORIGINS": "https://a.example, https://b.example",
            "ROBOG_API_PORT": "9999",
        }.items():
            os.environ[key] = value
        try:
            settings = Settings.from_env()
            self.assertEqual(settings.data_dir.name, "data")
            self.assertTrue(settings.data_dir.is_absolute())
            self.assertEqual((settings.default_k, settings.max_k), (3, 7))
            self.assertEqual(settings.media_max_age, 60)
            self.assertEqual(settings.vector, "auto")
            self.assertEqual(settings.cors_origins, ("https://a.example", "https://b.example"))
            self.assertEqual(settings.port, 9999)
        finally:
            for key in ("ROBOG_DATA_DIR", "ROBOG_API_DEFAULT_K", "ROBOG_API_MAX_K",
                        "ROBOG_API_MEDIA_MAX_AGE", "ROBOG_API_VECTOR",
                        "ROBOG_API_CORS_ORIGINS", "ROBOG_API_PORT"):
                os.environ.pop(key, None)

    def test_dense_retrieval_is_on_by_default_and_can_be_switched_off(self) -> None:
        """``uvicorn api.app:app`` must fuse Chroma without any extra flag."""
        from unittest.mock import patch

        with patch("api.config.load_dotenv", return_value=None):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ROBOG_API_VECTOR", None)
                self.assertEqual(Settings.from_env().vector, "auto")
            for off in ("off", "none", "0", "false", "OFF"):
                with patch.dict(os.environ, {"ROBOG_API_VECTOR": off}, clear=False):
                    self.assertIsNone(Settings.from_env().vector)

    def test_from_env_loads_dotenv_before_reading_the_settings(self) -> None:
        """Plain uvicorn skips ``python -m api``; ``.env`` must still apply."""
        from unittest.mock import patch

        def fake_dotenv(*_args, **_kwargs):
            os.environ["ROBOG_API_PORT"] = "8123"

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ROBOG_API_PORT", None)
            with patch("api.config.load_dotenv", side_effect=fake_dotenv):
                self.assertEqual(Settings.from_env().port, 8123)

    def test_health_says_why_dense_retrieval_is_not_active(self) -> None:
        from unittest.mock import patch

        tmp = Path(tempfile.mkdtemp(prefix="robog-novec-"))
        try:
            data = FIXTURE
            chunks = build_index(data, tmp / "build")
            env = {"ROBOG_CHROMA_PATH": str(tmp / "no-chroma-here")}
            with patch.dict(os.environ, env, clear=False):
                on = RobogService.from_settings(make_settings(data, chunks, vector="auto"))
                off = RobogService.from_settings(make_settings(data, chunks))
            status = on.status().to_dict()
            self.assertTrue(status["ready"])  # keyword retrieval is unaffected
            self.assertEqual(status["vector"], "auto")
            self.assertFalse(status["vector_active"])
            self.assertIn("dense retrieval disabled", status["vector_detail"])
            status = off.status().to_dict()
            self.assertFalse(status["vector_active"])
            self.assertIsNone(status["vector_detail"])  # off on purpose: nothing to explain
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_health_reports_an_attached_vector_store(self) -> None:
        from unittest.mock import patch

        tmp = Path(tempfile.mkdtemp(prefix="robog-vec-"))
        try:
            data = FIXTURE
            chunks = build_index(data, tmp / "build")
            retriever = object()
            with patch("api.service.ChromaRetriever.connect", return_value=retriever):
                svc = RobogService.from_settings(make_settings(data, chunks, vector="auto"))
            self.assertIs(svc.engine.retriever, retriever)
            status = svc.status().to_dict()
            self.assertTrue(status["vector_active"])
            self.assertIsNone(status["vector_detail"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# the real corpus (skipped when data/ is absent)
# ---------------------------------------------------------------------------
@unittest.skipUnless(HAVE_API, "FastAPI is not installed")
@unittest.skipUnless(DATA.is_dir(), "the dataset in data/ is not present")
class CorpusApiTest(unittest.TestCase):
    """§9a's definition of done, on the real 63 manuals / 476 figures."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="robog-corpus-"))
        chunks = build_index(DATA, cls.tmp / "build")
        cls.settings = make_settings(DATA, chunks)
        cls.service = RobogService.from_settings(cls.settings)
        cls.client = TestClient(create_app(cls.settings, service=cls.service))
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        shutil.rmtree(cls.tmp, True)

    def test_health_sees_the_whole_corpus(self) -> None:
        payload = self.client.get("/health").json()
        self.assertEqual(payload["chunks"], 1351)
        self.assertEqual(payload["params"], 2039)
        self.assertEqual(payload["profiles"], 38)
        self.assertEqual(payload["media_documents"], 63)
        self.assertEqual(payload["media_files"], 476)

    def test_every_figure_hit_has_a_servable_image_url(self) -> None:
        payload = self.client.get(
            "/search", params={"q": "Show me a picture of the BellaBot", "k": 5}
        ).json()
        figures = [hit for hit in payload["hits"] if hit["image"]]
        self.assertTrue(figures, "the picture route must return figures")
        for hit in figures:
            self.assertIsNotNone(hit["image_url"], hit["chunk_id"])
            response = self.client.get(hit["image_url"].replace("http://testserver", ""))
            self.assertEqual(response.status_code, 200, hit["image_url"])
            self.assertEqual(response.headers["content-type"], "image/png")

    def test_the_figure_language_follows_the_question(self) -> None:
        german = self.client.get(
            "/search", params={"q": "Zeig mir ein Bild vom BellaBot", "k": 3}
        ).json()
        self.assertEqual(german["language"], "de")
        self.assertTrue(german["hits"][0]["image"].startswith("images/de-"), german["hits"][0])

        italian = self.client.get(
            "/search", params={"q": "Mostrami una foto del BellaBot", "k": 3}
        ).json()
        self.assertEqual(italian["language"], "it")
        self.assertTrue(italian["hits"][0]["image"].startswith("images/it-"), italian["hits"][0])

        # FlashBot has no Italian manual: the figure falls back to EN.
        fallback = self.client.get(
            "/search", params={"q": "Mostrami una foto del FlashBot", "k": 3}
        ).json()
        self.assertEqual(fallback["language"], "it")
        self.assertTrue(fallback["hits"][0]["image"].startswith("images/en-"), fallback["hits"][0])
        self.assertTrue(fallback["cross_lingual"])

    def test_ask_is_grounded_and_cites_pages(self) -> None:
        payload = self.client.post(
            "/ask", json={"question": "Wieviel Zuladung hat der FlashBot?"}
        ).json()
        self.assertEqual(payload["language"], "de")
        self.assertTrue(payload["grounded"])
        self.assertIn("20 kg", payload["answer"])
        self.assertTrue(all(c["page"] for c in payload["citations"]))
        self.assertEqual(payload["verification"]["numbers_unverified"], 0)

    def test_ask_advisory_route_returns_profile_cards(self) -> None:
        payload = self.client.post(
            "/ask", json={"question": "Which robot fits my restaurant?"}
        ).json()
        self.assertEqual(payload["route"], "advisory")
        self.assertTrue(payload["profiles"])
        self.assertIn("BellaBot", {card["product"] for card in payload["profiles"]})
        for card in payload["profiles"]:
            self.assertTrue(card["citation"])

    def test_products_covers_robots_and_accessories(self) -> None:
        payload = self.client.get("/products", params={"language": "en"}).json()
        self.assertEqual(payload["count"], 22)
        robots = [p for p in payload["products"] if p["doc_type"] == "robot"]
        self.assertEqual(len(robots), 13)
        self.assertTrue(all(p["profile"] for p in robots))
        accessories = [p for p in payload["products"] if p["doc_type"] == "accessory"]
        self.assertTrue(accessories)
        self.assertTrue(all(p["profile"] is None for p in accessories))

    def test_products_falls_back_cross_lingual_for_flashbot(self) -> None:
        payload = self.client.get(
            "/products", params={"language": "it", "product": "FlashBot"}
        ).json()
        card = payload["products"][0]["profile"]
        self.assertEqual(card["language"], "en")  # no Italian FlashBot manual
        self.assertTrue(card["cross_lingual"])

    def test_parameter_lookup_quotes_the_manual(self) -> None:
        payload = self.client.get(
            "/products", params={"language": "de", "product": "FlashBot", "parameter": "Zuladung"}
        ).json()
        rows = payload["products"][0]["parameters"]
        self.assertIn("Ladekapazität", {row["parameter"] for row in rows})
        for row in rows:
            self.assertIn("p. ", row["citation"])

    def test_media_serves_a_real_figure(self) -> None:
        payload = self.client.get(
            "/search", params={"q": "Zeig mir ein Bild vom BellaBot", "k": 1}
        ).json()
        hit = payload["hits"][0]
        response = self.client.get(hit["image_url"].replace("http://testserver", ""))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\x89PNG"))
        self.assertGreater(len(response.content), 1000)


# ---------------------------------------------------------------------------
# the stdlib client (Bonus 01's "second client") against a real uvicorn server
# ---------------------------------------------------------------------------
@unittest.skipUnless(HAVE_API, "FastAPI is not installed")
class ClientTest(unittest.TestCase):
    """``api/client.py`` over real HTTP: the plug-and-play proof."""

    @classmethod
    def setUpClass(cls) -> None:
        import threading
        import time

        import uvicorn

        cls.tmp = Path(tempfile.mkdtemp(prefix="robog-client-"))
        chunks = build_index(FIXTURE, cls.tmp / "build")
        cls.config = uvicorn.Config(
            create_app(make_settings(FIXTURE, chunks)),
            host="127.0.0.1",
            port=0,          # ephemeral: the test reads the port back
            log_level="warning",
        )
        cls.server = uvicorn.Server(cls.config)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.time() + 20
        while time.time() < deadline and not cls.server.started:
            time.sleep(0.05)
        if not cls.server.started:
            raise RuntimeError("the test API server did not start")
        port = cls.server.servers[0].sockets[0].getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        shutil.rmtree(cls.tmp, True)

    def test_get_helper_reaches_the_service(self) -> None:
        payload = api_client._get(self.base, "/health")
        self.assertTrue(payload["ready"])

    def test_ask_helper_returns_a_grounded_answer(self) -> None:
        payload = api_client.ask(self.base, "What is the payload of the FlashBot?")
        self.assertIn("30 kg", payload["answer"])
        self.assertTrue(payload["verification"]["grounded"])

    def test_main_renders_answer_citations_and_verification(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = api_client.main([
                "--base", self.base, "What is the payload of the FlashBot?"
            ])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("30 kg", text)
        self.assertIn("[1] FlashBot User Manual", text)
        self.assertIn("verified:", text)

    def test_main_renders_search_hits_with_image_urls(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = api_client.main([
                "--base", self.base, "--search", "Show me a picture of the FlashBot"
            ])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn(f"{self.base}/media/sample_manual-en/fig_1.png", text)
        self.assertIn("figure", text)

    def test_main_supports_products_json_and_health(self) -> None:
        for args in (["--base", self.base, "--products", "--language", "de", "--json"],
                     ["--base", self.base, "--health"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(api_client.main(args), 0)
            self.assertTrue(json.loads(out.getvalue()))

    def test_main_reports_a_dead_server_and_http_errors(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(api_client.main(["--base", "http://127.0.0.1:1", "hi?"]), 1)
        self.assertIn("cannot reach", err.getvalue())

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(
                api_client.main(["--base", self.base, "--search", "--k", "0", "hi?"]), 1
            )
        self.assertIn("HTTP 422", err.getvalue())

    def test_main_needs_a_question(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                api_client.main(["--base", self.base])


if __name__ == "__main__":
    unittest.main()
