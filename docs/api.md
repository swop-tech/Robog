# The API — retrieval + grounded answering over HTTP (G4 → Bonus 01)

`api/` turns the retrieval engine and the answer composer into a standalone
service. It re-implements nothing: `api/service.py` holds a `SearchEngine` and
an `AnswerComposer`, `api/app.py` adds validation/status codes/JSON, and
`api/media.py` is the path-traversal-safe figure resolver of §9a.

```bash
pip install -r requirements-api.txt
python -m ingest.run --data data        # once: build/chunks.jsonl + params + profiles
python -m api                           # http://0.0.0.0:8000 — chat at /, Swagger at /docs
uvicorn api.app:app --reload            # the same app (reads .env too)
python -m api --port 8080 --vector off  # keyword-only + a different port
python -m unittest tests.test_api -v    # 61 tests, no API key, no network
python -m unittest tests.test_ui -v     # 11 web-chat serving tests
```

No key needed: without `GEMINI_API_KEY` the composer answers with its
deterministic grounded formatter, and `/health` says so
(`"provider": {"provider": "offline", "reason": "GEMINI_API_KEY not set"}`).

### Live end-to-end check (Gemini + Chroma)

Dense retrieval is **on by default** (`ROBOG_API_VECTOR=auto`): if `build/chroma`
exists the server fuses it with BM25 and the parameter index, otherwise it runs
keyword-only. Either way `/health` tells you which one you got, so the check is:

```bash
cp .env.example .env                              # put your GEMINI_API_KEY in it
python -m ingest.run --data data                  # chunks + params + profiles
python -m ingest.embed_index --provider gemini    # ends: "Indexed 1351 chunks into build/chroma"
uvicorn api.app:app --reload                      # or: python -m api
curl -s localhost:8000/health
```

| `/health` field | Expect | If not |
|---|---|---|
| `vector_active` | `true` | `vector_detail` gives the reason (no store, provider mismatch, missing key/package) |
| `provider.provider` | `gemini` | `provider.reason` gives the reason; answers come from the offline formatter |
| `chunks` | `1351` | re-run `python -m ingest.run --data data` |

Then ask something: `/search` hits carry `"sources": [..., "vector"]` and
`notes` contains `dense retrieval on (gemini)`. If the embedding API fails at
question time the engine drops to keyword-only for the rest of the process and
`vector_active` flips to `false` with the error in `vector_detail` -- ask one
question, then re-check `/health`, before calling the demo ready.

## Why this is the Bonus 01 claim

The engine is a *service*, and the web chat is only its first client. A second
client proves it — `api/client.py` is ~40 lines of `urllib` and no dependencies:

```bash
python -m api.client "Wieviel Zuladung hat der FlashBot?"
# [de · lookup · offline]
# Laut Handbuch beträgt **Ladekapazität** beim FlashBot **20 kg (10 kg pro Tablett)** [1].
#   [1] FlashBot User Manual V1.0, §2.3 Technische Daten, p. 40, DE
#   verified: 1/1 Aussagen belegt · 2/2 Zahlen geprüft

python -m api.client --search "Zeig mir ein Bild vom BellaBot" --k 2
python -m api.client --products --language it --product FlashBot --parameter payload
```

The same three calls from `curl` are what the pitch demo runs.

## `GET /` — the web chat (the required interface)

`GET /` is content-negotiated, so one URL serves both audiences:

```bash
curl localhost:8000/                              # Accept: */*  -> the JSON endpoint index
curl -H 'Accept: text/html' localhost:8000/       # a browser    -> the zero-build chat page
```

A browser navigating to `/` sends `Accept: text/html,…` and gets `ui/index.html`;
every API client (`curl`, `api/client.py`, `Accept: */*` or `application/json`)
keeps the machine-readable endpoint index it was written against (now with a
`chat` key pointing at the page). The assets are a Starlette `StaticFiles` mount
at `/ui` (`html=True`), so they are cached and **stay out of `/openapi.json`** —
the documented paths are unchanged. The page keeps serving when the index is
missing; `/ask` then renders the `503` fix command inline.

The chat (`ui/`: plain HTML + CSS + one ES module — no bundler, framework or CDN)
is built around the rubric: starter chips for all four winning scenarios, inline
clickable citations (manual title, section, PDF page, source snippet), inline
figures with their `Labels:` legend, a verification badge (citation + numeric
checks) and a toggleable **Raw `/search` JSON** drawer with a *Re-run live* button
that proves Bonus 01 on stage. It calls `POST /ask` and `GET /search` in parallel
and normalises every `image_url` to a same-origin path, so figures load behind any
reverse proxy. Full details: [`../ui/README.md`](../ui/README.md).

## `GET /search` — standalone hybrid retrieval

`SearchResult.to_dict()` **unchanged**, plus an absolute `image_url` on every
hit that has an `image`, plus a small `media` block. A client written against
`python -m retrieve --json` works against this endpoint without changes.

```bash
curl 'localhost:8000/search?q=Zeig+mir+ein+Bild+vom+FlashBot&k=1'
```

```json
{
  "query": "Zeig mir ein Bild vom FlashBot",
  "language": "de",
  "route": "figure",
  "route_reason": "figure cue 'zeig mir ein bild'",
  "policy": "language: de first, cross-lingual fallback",
  "cross_lingual": false,
  "products": ["FlashBot"],
  "tiers_used": ["de:17"],
  "confidence": "high",
  "coverage": 1.0,
  "notes": [],
  "params": [],
  "profiles": [],
  "hits": [{
    "chunk_id": "flashbot_user_manual_v1_0_multilanguage-de-0005",
    "score": 0.138,
    "citation": "FlashBot User Manual V1.0, §2.2 Aussehen und Bestandteile, p. 38, DE",
    "language": "de", "product": "FlashBot", "chunk_type": "figure",
    "image": "images/de-p021R-fig01.png",
    "image_url": "http://localhost:8000/media/flashbot_user_manual_v1_0_multilanguage-de/de-p021R-fig01.png",
    "labels": ["1 Notausschalter", "2 Abwärts gerichteter RGBD-Sensor", "…"],
    "sources": ["figure"]
  }],
  "media": {"prefix": "/media", "documents": 63, "note": "fetch a hit's image_url to get the PNG"}
}
```

| Query parameter | Default | Effect |
|---|---|---|
| `q` | *required* | the question, in EN / DE / IT |
| `k` | 5 | chunks to return (1…50; the service caps at `ROBOG_API_MAX_K`) |
| `language` | detected | force `en` \| `de` \| `it` |
| `product` | — | restrict to one product (metadata filter) |
| `doc_type` | — | `robot` \| `accessory` |
| `chunk_type` | — | `text` \| `table` \| `figure` |
| `follow_user_language` | `true` | `false` = the naive all-languages baseline |
| `cross_lingual_fallback` | `true` | `false` = hard filter, no fallback |
| `dedupe_translations` | `true` | drop a passage already returned in a preferred language |

## `POST /ask` — the full RAG pipeline

```bash
curl -X POST localhost:8000/ask -H 'content-type: application/json' \
     -d '{"question": "Zeig mir ein Bild vom FlashBot", "k": 3}'
```

The response leads with the contract from `docs/answering.md` §3 — `answer`,
`language`, `route`, `citations`, `figures`, `params`, `verification`,
`cross_lingual` — and keeps the rest of `Answer.to_dict()` as the audit trail
(`provider`, `model`, `grounded`, `verified`, `source_languages`,
`language_instruction`, `validation`, `notes`, `latency_ms`; `search` and
`evidence` only when asked for):

```json
{
  "answer": "- **Abbildung** (§2.2, S. 38) — Quelle: FlashBot User Manual V1.0, §2.2 … [1]",
  "language": "de",
  "route": "figure",
  "citations": [{"n": 1, "kind": "chunk",
                 "citation": "FlashBot User Manual V1.0, §2.2 Aussehen und Bestandteile, p. 38, DE",
                 "language": "de", "product": "FlashBot", "page": 38, "section": "2.2",
                 "image": "images/de-p021R-fig01.png",
                 "image_url": "http://localhost:8000/media/flashbot_user_manual_v1_0_multilanguage-de/de-p021R-fig01.png"}],
  "figures": [{"image": "images/de-p021R-fig01.png", "language": "de", "product": "FlashBot",
               "caption": "FlashBot User Manual V1.0, §2.2 …, p. 38, DE",
               "labels": ["1 Notausschalter", "…"], "image_url": "http://localhost:8000/media/…"}],
  "params": [],
  "verification": {"grounded": true, "verified": true,
                   "summary": "2/2 Aussagen belegt · 12/12 Zahlen geprüft",
                   "citation_rate": 1.0, "numeric_rate": 1.0, "quote_rate": 1.0,
                   "sentences_checked": 2, "sentences_cited": 2,
                   "numbers_checked": 12, "numbers_verified": 12, "numbers_unverified": 0},
  "cross_lingual": false,
  "provider": "offline", "grounded": true, "verified": true,
  "source_languages": ["de"], "notes": ["offline formatter (GEMINI_API_KEY not set)"],
  "latency_ms": 9
}
```

Body fields: `question` (required, ≤2000 chars), `k`, `language`, `product`,
`provider` (`gemini` | `offline` | `auto`, per request — generators are cached),
`model`, `follow_user_language`, `cross_lingual_fallback`, `include_search`,
`include_evidence`.

`params` are the structured rows behind a lookup answer
(`parameter`, `value`, `unit`, `value_number`, `page`, `section`, `chunk_id`,
`citation`), so a UI can show "20 kg — §2.3, p. 40" without re-deriving it.
`verification` is the G5 verdict in one object; the full report with every
issue is in `validation`.

## `GET /products` — catalog, profile cards, parameter lookup

```bash
curl 'localhost:8000/products?language=it'                                   # 22 products, 13 with cards
curl 'localhost:8000/products?language=it&product=FlashBot&parameter=payload' # cross-lingual card + rows
curl 'localhost:8000/products?q=payload%20FlashBot&language=en'               # the index's own ranker
```

Each entry: `product`, `doc_type`, `languages`, `documents` (its `doc_id`s),
the localized `profile` card (`category`, `payload`, `dimensions`, `weight`,
`battery_runtime`, `speed`, `min_passage_width`, `environments`,
`key_use_case`, `citation`, `image_url`, `cross_lingual` — `null` for
accessories, which have no card) and its `parameters` rows. `parameters_total`
and `profiles_total` report the index sizes (2,039 rows, 38 cards).

| Query parameter | Default | Effect |
|---|---|---|
| `language` | `en` | card + row language; EN, then DE/IT, when a manual has none |
| `product` | — | name, model code or substring (`FlashBot`, `CC1`) |
| `parameter` | — | filter rows by parameter name, **lexicon-aware** |
| `q` | — | free-text parameter search (`ParamIndex.search`, product-aware) |
| `doc_type` | — | `robot` \| `accessory` |
| `include_params` / `params_per_product` | `true` / 20 | row payload per product |

`?parameter=payload` finds the manuals that print **Load capacity** /
**Ladekapazität** / **Carico** because the filter runs through the same domain
lexicon as retrieval.

## `GET /media/{doc_id}/{filename}` — the figures, finally viewable

Closes the §9a gap: the 476 figure chunks had resolvable `image` paths and
nothing could fetch them. Now `/search`, `/ask` and `/products` return
`image_url`s that a browser can render directly.

```bash
curl -OJ localhost:8000/media/flashbot_user_manual_v1_0_multilanguage-de/de-p021R-fig01.png
# HTTP/1.1 200 OK · content-type: image/png · cache-control: public, max-age=86400
# x-content-type-options: nosniff · content-length: 146269
```

`doc_id` is the chunk's `metadata.doc_id` (the manual folder's slug + language);
`filename` is the figure's file name. `HEAD` is served too, and OpenAPI
documents the route at `/docs`.

**Only `data/**/images/*.png` is servable.** Four independent checks, all in
`api/media.py`, each returning a plain 404 (no path, no stack, no hint):

1. the filename is a single path component — no `/`, `\`, NUL, `.`, `..` or
   leading dot, checked on the *decoded* value, so `..%2f` and `%2e%2e%2f` are
   ordinary 404s;
2. the resolved path stays inside that document's `images/` folder — this is
   what stops a symlink inside `images/` from serving a file outside it;
3. the resolved path stays inside the data root;
4. the suffix is in `ALLOWED_SUFFIXES` (`{".png"}`) and the file exists.

`doc_id`s come from the chunk index (`doc_id` → `source_file`'s folder), refined
by a filesystem scan using the chunker's own `ingest.chunk.doc_id_for`, so an
id nobody ingested cannot be served. The media endpoint needs no built index:
it keeps working when `/search` answers 503.

Verified in `tests/test_api.py` against a corpus with a `secret.env` outside the
data root and a symlink inside `images/` pointing at it: every traversal form
(`..%2f`, `%2e%2e%2f`, raw `../../../`, `..`, `a/../../x`, `C:\`, NUL,
`.hidden.png`, a non-PNG name, an unknown `doc_id`) returns 404 and no response
body ever contains the secret.

## Configuration

Everything is environment-driven (`api/config.py`), so the same image runs on a
laptop or in a container; `python -m api` exposes the same knobs as flags.

| Variable | Default | Meaning |
|---|---|---|
| `ROBOG_DATA_DIR` | `<repo>/data` | manuals + figures (the media root) |
| `ROBOG_BUILD_DIR` | `<repo>/build` | where the indexes live |
| `ROBOG_CHUNKS` / `ROBOG_PARAMS` / `ROBOG_PROFILES` | `<build>/*.jsonl` | index files |
| `ROBOG_API_VECTOR` | `auto` | `auto` fuses Chroma results when `build/chroma` exists (keyword-only otherwise, `/health` says why); `off` never tries |
| `ROBOG_API_DEFAULT_K` / `ROBOG_API_MAX_K` | 8 / 20 | default and maximum `k` |
| `ROBOG_API_MEDIA_MAX_AGE` | 86400 | `Cache-Control` seconds for figures |
| `ROBOG_API_CORS_ORIGINS` | `*` | comma-separated origins for the chat UI |
| `ROBOG_API_HOST` / `ROBOG_API_PORT` | `0.0.0.0` / `8000` | bind address |
| `ROBOG_LLM_PROVIDER` / `ROBOG_LLM_MODEL` | `auto` / `gemini-2.5-flash` | the answer generator |

## Behaviour worth knowing

* **Degraded, not dead.** A missing `build/chunks.jsonl` makes `/search`,
  `/ask` and `/products` answer `503 {"detail": "search index unavailable",
  "fix": "python -m ingest.run --data data"}`; `/health` is 503 with the counts
  it does know, and `/media` still serves. Nothing crashes at startup.
* **The event loop stays free.** Routes are `async` and run retrieval,
  generation and validation in Starlette's threadpool, so one slow question
  cannot block `/health` or another client's figure.
* **Absolute, proxy-correct URLs.** `image_url`s are built from the request's
  own base URL, so they work behind a reverse proxy or a dev preview.
* **Validation is at the edge.** `q`/`question` length, `k` bounds, the
  `en|de|it` and `robot|accessory|text|table|figure` enums are pydantic/FastAPI
  constraints → `422`, before any retrieval runs.

## Limits (and where they go)

| Limit | Next step |
|---|---|
| No auth, no rate limit — it is a demo service on a trusted network | put it behind the platform's gateway before any public demo |
| `GET /ask` does not exist; asking is a POST with a JSON body | add an SSE/streaming variant for the chat UI |
| No answer cache: the same question re-retrieves and re-generates | add a small LRU keyed on the normalized question once the UI repeats questions |
| `?q=Zuladung BellaBot` finds nothing: BellaBot's DE table says *Ablagelast*, a form the payload lexicon does not list | add the missing surface forms to `retrieve/lexicon.py` and re-run the retrieval benchmark |
| The Gemini path is reachable (`provider=gemini`) but not exercised against the live API here | set `GEMINI_API_KEY` before the demo; the offline formatter stays the floor |

## Next

The web chat is shipped (`ui/`, served at `GET /`) — clickable citations, inline
figures with their `Labels:` legend, the verification badge and the live
`/search` drawer all render the contracts above. It closes the last open item in
`docs/pipeline-review.md` §9. Still open: an SSE/streaming variant of `/ask` (the
chat currently shows a loading state and renders the validated answer on
completion), and end-to-end validation of the optional dense layer.
