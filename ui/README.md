# The web chat (`ui/`) — the required interface

A fast, **zero-build** single-page chat served straight from FastAPI at `GET /`.
Plain HTML + CSS + one native ES module. No bundler, no `node_modules`, no
framework, no CDN — open the page and it runs.

```bash
pip install -r requirements-api.txt
python -m ingest.run --data data     # once: build the indexes
python -m api                        # http://0.0.0.0:8000  → the chat
```

| File | Role |
|---|---|
| `index.html` | the page: top bar + status, the four starter chips, the conversation, the composer, and the citation / drawer / lightbox containers |
| `styles.css` | the whole look — light, high-contrast, projector-safe, responsive, reduced-motion aware |
| `app.js` | one ES module: calls the API, renders the answer, wires every interaction |

## How it is served

`api/app.py` content-negotiates `GET /`:

* a **browser** (`Accept: text/html,…`) gets `ui/index.html`;
* an **API client** (`curl`, `api/client.py`, `Accept: */*` or JSON) keeps the
  machine-readable endpoint index — the Bonus 01 contract is unchanged.

The assets are a Starlette `StaticFiles` mount at `/ui` (`html=True`), so they
are cached and stay **out of `/openapi.json`**. The page references them by
absolute path (`/ui/styles.css`, `/ui/app.js`), so it works whether it is opened
at `/` or `/ui/`. The UI keeps serving when the search index is missing
(degraded, not dead) — `/ask` then shows the `503` fix command inline.

## What it shows (mapped to the judging rubric)

* **Starter chips for all four winning scenarios** — technician exact lookup
  (DE), non-technical advisory comparison (EN), cross-lingual Bonus 02 (IT→EN/DE),
  and visual/diagram lookup (EN). One click sends the question.
* **Inline clickable citations** — every `[n]` in the answer (and every entry in
  the *Sources* list) opens a modal with the exact **manual title, section, PDF
  page, language, product and source snippet**; a citation that owns a figure
  shows it too.
* **Inline rendered figures** with their **`Labels:` callout legend** as chips,
  click-to-zoom into a lightbox. Lookup answers also get an exact-value spec card.
* **Verification badge** — the G5 verdict at a glance (`✓ Verified · 2/2 cited ·
  13/13 numbers`), expandable to citation / numeric / quote rate bars and any
  issues. Cross-lingual answers carry a disclosure badge naming the source languages.
* **Raw `/search` JSON drawer** — a toggleable side panel showing the *unedited*
  `GET /search` response for the question, with the exact request URL, a
  **Re-run live** button and **Copy JSON**. This is Bonus 01 proven on stage: the
  chat is only the first client of the standalone retrieval service.

## Notes

* `POST /ask` and `GET /search` run in parallel, so the answer renders fast and
  the drawer is instant; **Re-run live** re-issues the real `/search` call.
* `image_url`s come back absolute from the API; the UI keeps only the path
  (`mediaUrl()`), so figures load same-origin behind any reverse proxy or preview.
* All source text is HTML-escaped before rendering; the markdown subset is
  `**bold**`, `- ` bullets and `[n]` citations — nothing executable.
* Keyboard: `Enter` sends, `Shift+Enter` is a new line, `Esc` closes the topmost
  overlay. Overlays are `role="dialog"`, focus moves to them and back on close.

## Tests

```bash
python -m unittest tests.test_ui -v        # 11 serving-contract tests, no browser/key/corpus
```

The render itself (all four scenarios, citations, figures, the verification
badge and the live drawer) is verified end-to-end against a running API.
