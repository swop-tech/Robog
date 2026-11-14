# Robog

Conversational access to robot documentation — hackathon project for the **Telmekom Challenge 2026**.

Turn buried robot docs (manuals, datasheets, specs, accessories guides) into accurate, clear,
source-grounded answers for both technicians and non-technical users.

- 🎯 **Brief vs. behaviour — what the judges ask, what the bot answers, what is still open:**
  [`docs/brief-alignment.md`](docs/brief-alignment.md)
- 📄 Challenge analysis & suggested path: [`docs/challenge-analysis.md`](docs/challenge-analysis.md)
- 🔍 Review of the team's pipeline diagram: [`docs/pipeline-review.md`](docs/pipeline-review.md)
- 🧰 Stack decisions (embeddings, RAG framework, vector DB): [`docs/stack-decisions.md`](docs/stack-decisions.md)
- 🧹 Data processing (clean, chunk, metadata, parameter index): [`docs/data-processing.md`](docs/data-processing.md)
- 🔎 Retrieval engine (routing, language policy, evaluation): [`docs/retrieval.md`](docs/retrieval.md)
- 💬 Answer composer + evidence validation (G5, Bonus 02): [`docs/answering.md`](docs/answering.md)
- 🔌 FastAPI backend (`/search`, `/ask`, `/products`, `/media`): [`docs/api.md`](docs/api.md)
- 🖥️ Web chat UI (`ui/`, served at `GET /`): [`ui/README.md`](ui/README.md)
- 🧠 Vector database from zero + the Gemini → Chroma run: [`docs/vector_database_explanation.md`](docs/vector_database_explanation.md), [`docs/gemini_chromadb_explanation.md`](docs/gemini_chromadb_explanation.md)
- 📦 Dataset layout: [`data/README.md`](data/README.md)
- 🎯 Approach: hybrid RAG + structured parameter index, retrieval engine exposed as an API
  (Bonus 01), multilingual ask/answer (Bonus 02), web chat as the required interface.

## Stack (all free)

| Layer | Choice |
|---|---|
| Data processing | Plain Python (`ingest/`), no dependencies |
| Retrieval | Plain Python (`retrieve/`): BM25 + parameter index + product profile cards + figure route + language policy, no dependencies |
| Embeddings | `gemini-embedding-2` (free tier) or `multilingual-e5-small` (offline) — pluggable |
| Vector DB | Chroma (embedded) |
| Answering | `gemini-2.5-flash` (free tier) **or** the deterministic offline formatter — pluggable |
| API | FastAPI + Uvicorn (`api/`), stdlib-only client for the plug-and-play demo |
| RAG framework | none needed: retrieval, prompting and evidence validation are ours (LlamaIndex optional) |
| Environment | Python 3.11+, VS Code, Git — any laptop, CPU-only |

## Data processing (phase 1 — done, tested on the real dataset)

```bash
# run the tests (stdlib only)
python -m unittest tests.test_ingest -v

# clean + chunk the manuals -> build/chunks.jsonl
#   + parameter index      -> build/params.jsonl
#   + product profiles     -> build/profiles.jsonl
#   + validation report    -> build/data-report.md   (non-zero exit on errors)
python -m ingest.run --data data
```

**On the real dataset: 63 markdown files → 1,351 chunks (706 text · 169 table · 476 figure),
2,039 structured parameter rows, and 38 localized product profile cards across all 13 robots.
Validation: 0 errors, 1 warning.**
Details, metadata schema and the quirks handled: [`docs/data-processing.md`](docs/data-processing.md).

Every chunk carries its full citation trail: `doc_id`, `product`, `doc_type`
(robot | accessory), `title`, `language`, `source_pdf`, `section`, `section_number`,
`page` / `pdf_page`, `chunk_type` (text | table | figure) and — for figures — `image`.
Figures and tables get their own chunks so answers can return exact parameter values
and the relevant image; tables additionally become rows in the parameter index
(`product → parameter → value/unit → source page`) for exact technician lookups.

## Retrieval engine (keyword baseline and optional dense layer)

```bash
python -m ingest.run --data data                          # build the local indexes first
python -m retrieve "Wieviel Zuladung hat der FlashBot?"   # standalone CLI (Bonus 01)
python -m retrieve "Show me a picture of the BellaBot"    # figure route (G6)
python -m retrieve "Which robot fits my restaurant?"      # advisory + product profile cards (G2)
python -m retrieve.evaluate                               # 28-question benchmark
python -m unittest discover -s tests -p "test_*.py"       # 295 tests
```

Keyword retrieval with **no embedding model and no dependencies**: BM25 over the chunks, the
structured parameter index for exact values, structured product profile cards across all 13 robots
for advisory/comparison queries, a `figure` route for visual requests, a domain lexicon
(payload ≈ Zuladung ≈ carico), and a per-question language policy (user's language first,
cross-lingual fallback for the manuals without one). On the 28-question benchmark (EN/DE/IT,
lookup + figure + procedure + advisory):

| Configuration | Pass@8 | MRR | Hits in question language | Cross-language duplicates | Param index hit | Profile card hit |
|---|---|---|---|---|---|---|
| every language at once (naive RAG) | 100% | 0.92 | 52% | 18% | 100% | 100% |
| user's language only (hard filter) | 82% | 0.82 | 86% | 0% | 80% | 86% |
| **same language first, fallback** | **100%** | **0.95** | **85%** | **0%** | **100%** | **100%** |

Details: [`docs/retrieval.md`](docs/retrieval.md).

## Embedding + indexing (phase 3 — dense layer)

```bash
pip install chromadb google-genai                # or: pip install -r requirements-rag.txt
cp .env.example .env                             # add GEMINI_API_KEY=... (git-ignored)
python -m ingest.embed_index --provider gemini   # embeds 1,351 chunks via Gemini API -> build/chroma
python -m retrieve "robot won't start" --vector  # auto-detects Gemini provider & dims from Chroma

# Offline alternative (no API key; downloads multilingual-e5-small once):
python -m ingest.embed_index --provider local --reset
```

The dense path is optional and requires a compatible built index and embedding provider.
The local model must be downloaded before it can run offline. End-to-end embedding quality
has not been validated by the keyword-only benchmark above.

## Answer composer + evidence validation (phase 3 — G5 + Bonus 02)

```bash
python -m answer "Wieviel Zuladung hat der FlashBot?"          # offline, no API key needed
python -m answer "Qual è la velocità massima del FlashBot?"    # Italian answer, sources DE/EN
python -m answer "Zeig mir ein Bild vom FlashBot" --show-evidence
python -m answer "Which robot fits my restaurant?" --json       # the shape POST /ask will return
python -m answer.evaluate --out build/answer-report.md          # 28-question answer benchmark
python -m answer.evaluate --vector                              # same benchmark through the hybrid
```

`AnswerComposer` = retrieve → number the evidence → generate → **validate** → answer.
Generation is `gemini-2.5-flash` when `GEMINI_API_KEY` is set, and the deterministic grounded
formatter otherwise (no key, no network, no model download) — the same formatter that takes over
if the API fails or an answer cannot be repaired. The answer is always written in the question's
language and says so when the evidence came from another language.

Every answer is post-checked before it is returned (**G5**): every factual sentence must cite a
retrieved chunk (`[1]`, `[2]` …), every number/unit must occur in the evidence (invented values are
removed, value *and* unit compared), and quoted text must appear verbatim in the cited source.
Bad claims are repaired, the LLM gets one retry with the failures named, and otherwise the
formatter answers — an unvalidated answer never reaches a caller.

On the 28-question benchmark, the offline formatter scores **100%** grounded, **100%** claims
cited, **100%** numbers verified, **0** invented numbers, **100%** answer-language match and
**100%** cross-lingual disclosure. Details, checks and limits: [`docs/answering.md`](docs/answering.md).

## API (phase 4 — G4 → Bonus 01, + media serving)

```bash
pip install -r requirements-api.txt
python -m ingest.run --data data      # once: build/chunks.jsonl + params + profiles
python -m api                         # http://0.0.0.0:8000 — chat at /, Swagger at /docs
uvicorn api.app:app --reload          # same app; fuses build/chroma automatically when it exists
curl -s localhost:8000/health         # vector_active + provider: is this hybrid + Gemini, or the offline floor?

curl 'localhost:8000/search?q=Wieviel+Zuladung+hat+der+FlashBot%3F&k=3'   # Bonus 01
curl -X POST localhost:8000/ask -H 'content-type: application/json' \
     -d '{"question": "Qual è la velocità massima del FlashBot?"}'
curl 'localhost:8000/products?language=it&product=FlashBot&parameter=payload'
curl -OJ localhost:8000/media/flashbot_user_manual_v1_0_multilanguage-de/de-p021R-fig01.png

python -m api.client "Wieviel Zuladung hat der FlashBot?"   # 40-line second client
python -m unittest tests.test_api -v                        # 63 tests
```

The retrieval engine and the answer composer as a service, re-implementing nothing:
`GET /search` answers with `SearchResult.to_dict()` **unchanged** plus an absolute `image_url` per
figure hit; `POST /ask` returns `{answer, language, route, citations, figures, params, verification,
cross_lingual}` plus the full audit trail; `GET /products` serves the catalog, the localized profile
cards and the parameter lookup; `GET /media/{doc_id}/{filename}` serves only `data/**/images/*.png`
(path-traversal safe, cached). A missing index is a `503` with the command to run, never a crash —
and `/media` keeps working without one. Details, the full JSON contracts and the configuration:
[`docs/api.md`](docs/api.md).

**Images are now viewable over HTTP.** All 476 figures are indexed as their own chunks with a
resolvable `image` path and parsed `labels` legend, the `figure` route (`G6`) selects
language-appropriate overview/callout diagrams in EN, DE, and IT (falling back to EN when a manual
lacks Italian), and every figure hit now carries a fetchable `image_url` — the §9a gap is closed,
and the web chat renders them inline.

## Web chat UI (phase 5 — the required interface)

```bash
python -m api                         # then open http://0.0.0.0:8000 in a browser
python -m unittest tests.test_ui -v   # 13 serving-contract tests (no browser, key or corpus)
```

A fast, **zero-build** assistant UI (`ui/`: plain HTML + CSS + native ES modules, no bundler, no
framework, no CDN) served straight from FastAPI at `GET /`. Light theme primary with a working dark
switcher, responsive down to a phone, keyboard-navigable, and split into testable layers: `api.js`
(the only module that talks to the network), `store.js` (conversations + settings), `render.js` and
`markdown.js` (payload → HTML), `app.js` (the DOM). The route is content-negotiated: a
browser gets the chat page, an API client (`curl`, `api/client.py`, `Accept: */*`) keeps the JSON
endpoint index — so Bonus 01's contract is unchanged. The assets are a `/ui` static mount that stays
out of `/openapi.json`, and the page keeps serving when the index is missing (degraded, not dead).

Built around the judging rubric:

- **Starter chips for all 4 winning scenarios** — technician exact lookup (`"Wieviel Zuladung hat der
  FlashBot?"`), non-technical advisory comparison (`"Which robot fits a busy restaurant best?"`),
  cross-lingual Bonus 02 (`"Qual è la velocità massima del FlashBot?"`, IT question / EN-DE manual),
  and visual/diagram lookup (`"Show me the component diagram of BellaBot"`).
- **Inline clickable citations** — every `[n]` opens the exact manual title, section, PDF page,
  language and source snippet (plus the figure when the source owns one).
- **Inline rendered figures** with their `Labels:` callout legend, click-to-zoom; lookup answers also
  get an exact-value spec card.
- **Verification badge** showing the citation & numeric checks (`✓ Verified · 2/2 cited · 13/13
  numbers`), expandable to rate bars and issues, plus a cross-lingual disclosure badge.
- **Toggleable "Raw `/search` JSON" drawer** with the exact request URL, a `POST /ask` tab showing the
  request sent and the response received, **Re-run live** and **Copy JSON** — Bonus 01 proven on stage:
  the chat is only the first client of the retrieval service.
- **Product cards** for the advisory persona (payload, dimensions, runtime, charging time, passage
  width, environments, citation) rendered from `profiles[]` — only the fields a profile really carries.
- **Saved conversations, settings and a live status pill**: history and preferences live in the browser
  (`POST /ask` is stateless), evidence depth / answer language / product scope / provider / language
  policy map onto real `AskRequest` fields, and `GET /health` drives the pill, its tooltip and a banner
  carrying the server's own fix command when the index is missing.
- **Nothing simulated**: features the backend does not have (streaming, server-side history, feedback,
  a regenerate endpoint) are listed in Settings as unsupported and specified in
  [`docs/ui-backend-gaps.md`](docs/ui-backend-gaps.md); every error path renders the API's own words.

```bash
node --test ui/tests/*.test.js                                   # 202 frontend tests, no browser
npm install --no-save jsdom                                      # dev-only: enables the 28 DOM tests
ROBOG_LIVE_API=http://localhost:8000 node --test ui/tests/live.test.js   # end to end, real corpus
```

Details: [`ui/README.md`](ui/README.md). This closes the last open item in
[`docs/pipeline-review.md`](docs/pipeline-review.md) §9.
