# Telmekon AI Assistant — the web interface (`ui/`)

The required interface for the Telmekon challenge: a production-style assistant
over the Pudu Robotics manuals, served straight out of FastAPI at `GET /`.

**Zero-build.** Plain HTML + one stylesheet + native ES modules. No bundler, no
framework, no CDN, no `node_modules` at runtime, no external font, no absolute
URL anywhere in the served page. Open it and it runs.

```bash
pip install -r requirements-api.txt
python -m ingest.run --data data     # once: build/chunks.jsonl + params + profiles
python -m api                        # http://0.0.0.0:8000  → the assistant
```

---

## Architecture

The old single-file `app.js` is split so each layer can be tested on its own.
Every module is dependency-free and side-effect-free except where noted.

| File | Role | Tests |
|---|---|---|
| `index.html` | structure only: sidebar, top bar, welcome, thread, composer, and the four overlay containers (citation modal, raw drawer, lightbox, settings) | `contract` |
| `styles.css` | the whole design system — tokens, `[data-theme]` light/dark, layout, components, responsive, reduced-motion, print | `contract` |
| `app.js` | **the only DOM-aware module**: events, focus, overlays, toasts, scroll, and the ask pipeline | `dom`, `live` |
| `api.js` | **the only module that touches the network**: `POST /ask`, `GET /search`, `GET /health`, `GET /products`, `mediaUrl()`, `ApiError`, `CAPABILITIES` | `api` |
| `render.js` | pure payload → HTML: answer card, spec cards, product cards, figures + Labels legend, verification verdict, sources, errors, sidebar, citation modal, drawer | `render` |
| `markdown.js` | the answer renderer: escaping, headings, lists, tables, code, callouts, `[n]` citation markers, JSON highlighting, `plainText()` | `markdown` |
| `store.js` | conversations + settings as reducers over plain state, persisted to `localStorage` with quota fallback | `store` |
| `theme.js` | `light` / `dark` / `system`, written to `<html data-theme>` + `color-scheme` | `store` |
| `icons.js` | inline SVG icon set (36 glyphs), injected at boot — no icon font, no sprite request | `contract` |
| `tests/` | `node --test`, no browser needed; `live.test.js` additionally drives a running server | — |

Data flows one way: `api.js` → `store.js` → `render.js` → DOM (via `app.js`).
Nothing in `render.js` or `store.js` knows a browser exists, which is why 174 of
the 209 tests run without any DOM at all.

## Design system

Light is the primary theme; dark is a real switcher (sidebar + Settings), and
`system` follows the OS live. Every colour is a token on `:root`, overridden
under `[data-theme="dark"]` — no component hardcodes a palette, so the two themes
cannot drift.

* **Palette**: navy/blue accent (`#1d4ed8` light, `#6ea1ff` dark) on soft neutral
  surfaces; green/amber/red reserved for status, never for decoration.
* **Shape**: 6–22 px radii, 1 px hairline borders, three shadow levels.
* **Motion**: 140–240 ms `cubic-bezier(.22,.61,.36,1)` on hovers, overlays and
  the drawer; a shimmer skeleton and an elapsed-time counter while an answer is
  in flight. All of it is disabled under `prefers-reduced-motion`.
* **Type**: system font stack, `0.735–2.35 rem` scale, tabular numerals for every
  number a technician reads off a spec card.
* **Responsive**: 2-col grid ≥ 900 px; below that the sidebar becomes an overlay
  drawer with a backdrop, spec/figure/profile grids collapse to one column, and
  the thread padding tightens. Nothing scrolls horizontally — wide content
  (tables, JSON, long tokens) scrolls inside its own container.
* **Accessible**: skip link, `role="dialog"` + `aria-modal` + focus trap + focus
  restore on every overlay, `aria-expanded`/`aria-pressed` on toggles, a polite
  live region that announces the answer, the cross-lingual disclosure and
  failures, labels on every icon-only control, and 4.5:1+ contrast in both themes.

## What it shows (mapped to the judging rubric)

* **The four winning scenarios** as starter chips — technician exact lookup (DE),
  non-technical advisory comparison (EN), cross-lingual Bonus 02 (IT), visual
  diagram lookup (EN). One click asks.
* **Exact values** as spec cards (`Ladekapazität · 20 kg (10 kg pro Tablett) ·
  FlashBot · §2.3 · p. 40 · DE`). A unit the value already contains is not
  repeated.
* **Product cards** for the advisory route: payload, dimensions, runtime,
  charging time, passage width, navigation, environments and the manual citation.
  Only the fields a profile actually carries are rendered.
* **Figures with their `Labels:` legend** as numbered chips, click-to-zoom into a
  lightbox that repeats the legend.
* **Clickable citations** — every `[n]` in the answer and every source card opens
  a modal with manual title, kind, section, PDF page, language, product, the
  source snippet and the full citation string.
* **The verification verdict** — `✓ Verified · 2/2 cited · 2/2 numbers`,
  expandable to citation/numeric/quote bars and the validator's issues. When the
  engine found no evidence (`checked: false`, `state: "no-evidence"`) the badge is
  **neutral grey** and reads *Not answered from the manuals*: 0/0 passing every
  check vacuously is not a pass.
* **Cross-lingual disclosure** — *Answer in Italiano · sources DE, EN*, also
  announced to screen readers.
* **Raw `/search` drawer (Bonus 01)** — the unedited retrieval response with the
  exact URL it came from, a `POST /ask` tab showing the request the UI sent and
  the response it got, **Re-run live** (a real second request) and **Copy JSON**.
* **Live status** — `GET /health` drives the pill (`ready · offline · keyword`),
  its tooltip (chunks / parameters / products / figures / vector detail) and a
  banner with the server's own fix command when the index is missing.

## Honesty rules this UI is built under

1. **Only real endpoints.** `ui/tests/contract.test.js` fails if `ui/` mentions a
   path outside `/ask`, `/search`, `/health`, `/products`, `/media`, `/docs`, `/ui`.
2. **No fabricated content.** No hardcoded answers, no placeholder citations, no
   simulated success. A missing field renders as nothing; a `[n]` marker with no
   citation behind it opens a dialog that says the response has no such source.
3. **Missing backend features are labelled, not imitated.** `CAPABILITIES` in
   `api.js` is rendered in Settings under *Not supported by the API yet*
   (streaming, server-side history, feedback, regenerate). The exact request each
   one needs is written up in [`../docs/ui-backend-gaps.md`](../docs/ui-backend-gaps.md).
4. **Local state says it is local.** Conversations, settings and feedback live in
   `localStorage` under `telmekon.ui.v1`. `/ask` accepts up to 8 recent user
   questions as temporary follow-up context but does not persist conversation
   history. The sidebar footnote, the empty state, the feedback toast and Settings
   all say so. A
   restored conversation re-renders what the API returned before; its raw
   `/search` pane stays empty until **Re-run live** is pressed.
5. **No secrets in the browser.** The Gemini key is read server-side from `.env`
   (`answer/llm.py`); the UI only ever sees `"gemini"` / `"offline"` in
   `payload.provider`. `contract.test.js` fails on anything key-shaped in `ui/`.
6. **Every failure path is rendered from the API's own words** — 503 shows
   `reason` + `fix`, 422 shows the length rule, a timeout says how long it waited,
   an unreachable server names `python -m api`, and a non-JSON body says so.

## Serving contract (unchanged)

`api/app.py` content-negotiates `GET /`: a browser (`Accept: text/html,…`) gets
`ui/index.html`; an API client (`*/*` or JSON) keeps the machine-readable endpoint
index, so the Bonus 01 contract is untouched. Assets come from a Starlette
`StaticFiles` mount at `/ui` (`html=True`), which stays out of `/openapi.json`.
The page references them by absolute path (`/ui/styles.css`, `/ui/app.js`) so it
works at `/` and at `/ui/`. The UI keeps serving when the index is missing —
degraded, not dead.

## Tests

```bash
# serving contract, no browser / key / corpus needed
python -m unittest tests.test_ui -v                    # 13 tests

# the frontend, no browser needed (209 tests: markdown, render, api, store, DOM)
node --test ui/tests/*.test.js

# the DOM suite needs jsdom, a dev-only tool that is NOT a project dependency;
# without them those 28 tests skip with a reason and the rest still pass
npm install --no-save jsdom

# end to end against a running server: boots index.html in jsdom, clicks all four
# starter scenarios, asserts the DOM shows what the API really returned
python -m api &
ROBOG_LIVE_API=http://localhost:8000 node --test ui/tests/live.test.js
```

What each suite is for:

* `markdown.test.js` — escaping, the markdown subset, and the corpus-specific
  traps (`84*60*49 cm` must not become italics).
* `render.test.js` — payload → HTML against fixtures shaped like `_ask_payload`,
  `_param_row` and `_verification`; every missing-field and hostile-input case.
* `api.test.js` — what goes on the wire: only declared `AskRequest` fields, only
  real endpoints, and a normalised `ApiError` for each failure mode (HTTP,
  non-JSON, empty answer, timeout, no server).
* `store.test.js` — reducers, `AskRequest` mapping, persistence round-trip,
  corrupt storage, quota exhaustion, and the theme resolution.
* `contract.test.js` — the cross-file wiring a zero-build app cannot check at
  compile time: every `$("id")` exists in the page, every class a template emits
  is styled, every icon exists, the pinned markers `tests/test_ui.py` greps for
  are present, and nothing key-shaped is committed.
* `dom.test.js` — the app in jsdom: boot, submit, pending state, answer card,
  citation modal, verification expansion, lightbox, drawer tabs + re-run,
  feedback, theme switch, settings, scope chips, new chat, restore, filter,
  delete + undo, 503, unreachable API, no-evidence badge, keyboard shortcuts.
* `live.test.js` — the same, against the real service and the real corpus.

There is no browser in this sandbox, so nothing here claims pixel-verified
layout; `dom.test.js` + `live.test.js` verify behaviour and rendered content in a
real DOM implementation, and `contract.test.js` verifies the HTML/CSS/JS agree.
