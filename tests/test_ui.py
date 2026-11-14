"""Tests for the zero-build web chat served by FastAPI (``ui/`` + ``GET /``).

Run from the repository root:

    python -m unittest tests.test_ui -v

The chat is the required interface (``docs/pipeline-review.md`` §9, step 7).
These tests pin down how it is served, without a browser, an API key or the
real corpus:

* ``GET /`` is content-negotiated -- a browser (``Accept: text/html``) gets the
  chat page; an API client (``*/*`` / JSON) keeps the endpoint index it was
  written against, so the Bonus 01 contract is unchanged;
* the page showcases all four winning scenarios as starter prompts;
* ``/ui`` serves the static assets (HTML, CSS, one ES module) and stays out of
  ``/openapi.json`` (a Starlette mount, not an API route);
* the UI keeps serving when the search index is missing (degraded, not dead).

The render itself (citations, figures, verification badge, the live ``/search``
drawer) is exercised end-to-end against a running API in the browser; here we
test the serving contract that the browser depends on.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"

try:  # the API package is optional: the rest of the repo runs without FastAPI
    from fastapi.testclient import TestClient

    from api.app import create_app
    from api.config import Settings
    from api.service import RobogService

    HAVE_API = True
except ModuleNotFoundError:  # pragma: no cover - depends on the environment
    HAVE_API = False

BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

#: The four scenarios the starter chips must showcase (the judging rubric).
STARTER_PROMPTS = (
    "Wieviel Zuladung hat der FlashBot?",
    "Which robot fits a busy restaurant best?",
    "Qual è la velocità massima del FlashBot?",
    "Show me the component diagram of BellaBot",
)


@unittest.skipUnless(HAVE_API, "FastAPI is not installed (pip install -r requirements-api.txt)")
class ChatUiTest(unittest.TestCase):
    """Serving contract for the web chat, on a degraded (index-less) service.

    Serving the page needs no retrieval index, so the fixture is an empty build
    directory: fast, corpus-free, and it proves the UI survives a missing index.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="robog-ui-"))
        cls.settings = Settings(
            data_dir=ROOT / "data",
            chunks_path=cls.tmp / "build" / "chunks.jsonl",
            params_path=cls.tmp / "build" / "params.jsonl",
            profiles_path=cls.tmp / "build" / "profiles.jsonl",
        )
        cls.service = RobogService.from_settings(cls.settings)
        cls.client = TestClient(create_app(cls.settings, service=cls.service))
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        shutil.rmtree(cls.tmp, True)

    # -- content negotiation at GET / --------------------------------------
    def test_browser_gets_the_chat_page(self) -> None:
        response = self.client.get("/", headers={"Accept": BROWSER_ACCEPT})
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("<title>Robog", response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-cache")

    def test_api_client_keeps_the_json_index(self) -> None:
        # Default TestClient Accept is */* -- the contract api/client.py uses.
        payload = self.client.get("/").json()
        self.assertIn("GET /search", payload["endpoints"])
        self.assertIn("POST /ask", payload["endpoints"])
        self.assertIn("chat", payload)  # the index now points at the UI too

    def test_explicit_json_accept_stays_json(self) -> None:
        response = self.client.get("/", headers={"Accept": "application/json"})
        self.assertIn("application/json", response.headers["content-type"])
        self.assertIn("endpoints", response.json())

    def test_root_html_matches_the_static_index(self) -> None:
        root = self.client.get("/", headers={"Accept": BROWSER_ACCEPT}).text
        static = self.client.get("/ui/index.html").text
        self.assertEqual(root, static)

    # -- the four winning scenarios ----------------------------------------
    def test_page_showcases_all_four_starter_prompts(self) -> None:
        page = self.client.get("/", headers={"Accept": BROWSER_ACCEPT}).text
        for prompt in STARTER_PROMPTS:
            self.assertIn(prompt, page)
        # the rubric features the UI must wire up: the citation modal, the live
        # /search drawer and the figure lightbox are containers in the page; the
        # verification badge and the Labels legend are rendered by app.js.
        for marker in ("citation-overlay", 'id="drawer"', "lightbox", "Raw <code>/search</code>"):
            self.assertIn(marker, page)
        js = self.client.get("/ui/app.js").text
        for marker in ("verify-badge", "Labels", "figure-labels", "cross-badge", "openDrawer"):
            self.assertIn(marker, js)
        css = self.client.get("/ui/styles.css").text
        for marker in (".verify-badge", ".figure-labels", ".label-chip"):
            self.assertIn(marker, css)

    def test_page_links_the_zero_build_assets(self) -> None:
        page = self.client.get("/", headers={"Accept": BROWSER_ACCEPT}).text
        self.assertIn('/ui/styles.css', page)
        self.assertIn('/ui/app.js', page)
        self.assertIn('type="module"', page)
        # no bundler, no CDN, no framework: assets are local and self-contained
        self.assertNotIn("http://", page.replace('http://www.w3.org', ''))
        self.assertNotIn("https://", page)

    # -- static assets -----------------------------------------------------
    def test_static_assets_serve_with_correct_types(self) -> None:
        cases = {
            "/ui/": "text/html",
            "/ui/index.html": "text/html",
            "/ui/styles.css": "text/css",
            "/ui/app.js": "javascript",
        }
        for path, ctype in cases.items():
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn(ctype, response.headers["content-type"], path)

    def test_app_js_calls_the_service_endpoints(self) -> None:
        js = self.client.get("/ui/app.js").text
        for endpoint in ('"/ask"', "/search", "/health"):
            self.assertIn(endpoint, js)
        # figures are normalised to same-origin paths (proxy-safe)
        self.assertIn("mediaUrl", js)

    def test_static_mount_is_not_an_openapi_route(self) -> None:
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertEqual(
            sorted(paths),
            ["/", "/ask", "/health", "/media/{doc_id}/{filename}", "/products", "/search"],
        )
        self.assertFalse(any(p.startswith("/ui") for p in paths))

    def test_static_traversal_is_blocked(self) -> None:
        for path in ("/ui/../api/config.py", "/ui/..%2fapi%2fconfig.py", "/ui/%2e%2e/README.md"):
            self.assertIn(self.client.get(path).status_code, (400, 404), path)

    # -- degraded, not dead ------------------------------------------------
    def test_ui_serves_without_a_search_index(self) -> None:
        self.assertFalse(self.service.ready)  # the fixture has no built index
        self.assertEqual(self.client.get("/", headers={"Accept": BROWSER_ACCEPT}).status_code, 200)
        self.assertEqual(self.client.get("/ui/app.js").status_code, 200)
        # while the retrieval routes still answer 503 with the fix
        self.assertEqual(self.client.get("/search", params={"q": "payload"}).status_code, 503)


if __name__ == "__main__":
    unittest.main()
