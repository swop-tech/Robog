# Team Pipeline Review — Analysis of the Suggested Path

> Reviewed against: `Telmekom_Challenge26.pdf`, the dataset `README.md` (PUDU manuals as
> Markdown, EN/DE/IT), and the team's pipeline diagram (screenshot).
> Companion to [`challenge-analysis.md`](challenge-analysis.md).

## 1. The suggested path (as drawn)

```
PDF / Markdown (robot manuals) + Images (with transcriptions)
        │
        ▼
Data processing — clean, chunk, preserve source metadata
        │
        ▼
Embedding model — convert text into searchable vectors
        │
        ▼
Vector index + metadata — searchable robot knowledge base
        │
User question (chat interface) ──► Retrieval engine — find relevant passages
        │
        ▼
LLM + evidence validation — generate a grounded answer
        │
        ▼
Final answer — answer + citations + relevant image
```

**Verdict up front:** this is a sound RAG pipeline and hits the brief's suggested approach.
Roughly 80% of what's needed to win is here — especially two differentiators most teams skip:
**"LLM + evidence validation"** and **"answer + citations + relevant image"**. The remaining 20%
is about technician-grade exactness, cross-product questions, and making the two bonuses explicit.

## 2. Box-by-box assessment

| Box | Assessment | Notes |
|---|---|---|
| PDF/Markdown + images | ✅ Perfect fit | The dataset README confirms: per-language `.md`, figures with **AI descriptions + label legends**, tables, page markers. This *is* "images with transcriptions" from the brief. |
| Data processing (clean, chunk, preserve metadata) | ✅ Right instinct | "Preserve source metadata" is the foundation of faithfulness — keep it sacred. Chunking strategy needs to exploit the markdown's structure (see §4). |
| Embedding model | ⚠️ Underspecified | Must be **multilingual** (EN/DE/IT, plus Bonus 02). See gap G3. |
| Vector index + metadata | ⚠️ Good but not sufficient | Pure vector search is weak on exact parameter values ("what's the value of X?") — the technician persona. See gap G1. |
| Retrieval engine | ✅ Great — and it's Bonus 01 | Keep it a standalone service and say so loudly. See gap G4. |
| LLM + evidence validation | ⭐ The strongest box | This is the "faithfulness to the source" judging criterion made concrete. Needs explicit rules (gap G5). |
| Final answer + citations + relevant image | ⭐ Wow factor | Returning the actual figure (from `images/`) with its label legend is a demo moment. Needs a figure index to work (§4). |

## 3. What this dataset gives us for free

The dataset is unusually well-suited to the challenge — lean into each of these:

1. **Parallel trilingual corpora** (EN/DE/IT per manual) → Bonus 02 becomes natural and demo-able:
   ask in German, answer in German, from a source in English.
2. **The data gap is a feature**: FlashBot, GW1 and Pager have **no Italian**. An Italian question
   about FlashBot must retrieve from EN/DE and answer in IT — that is *exactly* the Bonus 02 story
   ("ask in your language, source in another one") landing in our lap.
3. **Figure descriptions + label legends** (AI figure descriptions, `*Labels:*` lists) → we can
   index figures as first-class chunks and return "the relevant image" with answers.
4. **Page markers** (`*Page 3 (PDF page 4, left)*`) → precise, verifiable citations ("Manual X,
   §4.2, p. 12"). Judges can check any claim in seconds.
5. **Section-numbered headings + YAML headers** → clean chunk boundaries and free metadata
   (title, language, source_pdf).
6. **Tables (metric values)** → the raw material for a structured parameter index (gap G1).

## 4. Gap analysis — five concrete fixes

### G1 — Technician lookups need exactness (biggest gap)

*"What's the value of this parameter?"* is a needle-in-a-haystack problem for pure vector search:
numbers, units and model codes are exactly what embeddings blur. Fixes (cheap → stronger):

- **Hybrid retrieval**: run BM25/keyword search (FTS5 in SQLite, or `rank-bm25`) **in parallel**
  with vector search and fuse the results (reciprocal rank fusion). One afterthought, huge gain.
- **Structured parameter index**: parse markdown tables into `robot → parameter → value, unit,
  source` rows; route parameter-shaped questions to it first.
- The LLM must be allowed to quote the value verbatim from the chunk — no paraphrasing numbers.

### G2 — Advisory questions span documents

*"Which robot fits my customer's needs?"* compares robots; top-k chunks from one manual can't
answer it. Fixes:

- Build **product profile cards** per robot (one per manual: positioning, key specs, payload/
  runtime/use-case bullets) — either extracted once at ingest or generated and then
  fact-checked against the manual.
- A light **query router**: parameter-shaped → structured index; comparison/advisory-shaped →
  profile cards + multi-document retrieval; otherwise → standard hybrid RAG.
- This mirrors the brief's own "LLM-generated wikis" option as a *layer*, not the whole system —
  the defense: "the right technique per question type, everything still traceable."

### G3 — Make multilingual explicit (Bonus 02)

- Use **multilingual embeddings** (e.g. `intfloat/multilingual-e5-small` — fast on CPU, good
  DE/IT/EN quality; `bge-m3` if we want stronger cross-lingual).
- Language policy per message: detect the question's language → prefer same-language sources →
  fall back cross-lingual → **always answer in the user's language**.
- The no-Italian manuals give us the perfect live demo of the cross-lingual fallback.

### G4 — Claim Bonus 01 explicitly

The diagram already has a "Retrieval engine" box — formalize it:

- Retrieval + answering as one backend service (FastAPI) exposing:
  - `GET /search` — raw hybrid retrieval (chunks + metadata + images) for any frontend
  - `POST /ask` — grounded answer + citations
  - `GET /products` — the product/parameter index
- The chat interface is just the first client. Second client (Telegram bot or a curl demo in the
  pitch) proves plug-and-play.

### G5 — Make "evidence validation" real and defensible

Define it as deterministic checks after generation (before the answer is shown):

1. **Citation-required prompting**: every factual sentence must carry a source reference
   (file + section + page from our metadata).
2. **Numeric verification**: every number/unit in the answer must occur in the retrieved chunks
   (regex extract → set membership). Fail → regenerate or flag.
3. **Quote check**: verbatim quotes in the answer must exist in the cited chunk.
4. Show the evidence in the UI: cited snippet + page + figure, so users verify with one click.

This turns "faithfulness to the source" from a claim into a mechanism we can demo.

## 5. Chunking & metadata plan for *this* markdown format

- **Chunk by section** (top-level `#`/`##` numbered headings), not by fixed token windows —
  the manual's own structure is the best semantic unit. Overlap only within long sections.
- **Chunk metadata** (the "preserve source metadata" box): `manual`, `product`, `language`,
  `section_number`, `section_title`, `page`, `source_pdf`, `chunk_type` (text | table | figure).
- **Figures as chunks**: index the AI description + `Labels:` list as its own chunk carrying the
  image path → retrieval can answer "which figure shows X?" and attach the image.
- **Tables**: keep the markdown table in the text chunk *and* emit structured rows to the
  parameter index (both paths lead back to the same source citation).
- **YAML header** becomes the document-level record (title, language, source_pdf, generated).

## 6. Revised pipeline (minimal delta from the team's drawing)

```
Markdown + images (per-language manuals, 13 robots + 9 accessories)
        │
        ▼
Data processing ── clean, section-chunk, preserve metadata ──► figure chunks + param index (NEW)
        │
        ▼
Multilingual embedding model (explicit: e.g. multilingual-e5)      [G3]
        │
        ▼
Hybrid index: vectors + BM25 + metadata  (was: vector only)        [G1]
        │
User question (chat interface) ──► Query router (lookup | advisory | default)  [G2]
        │                                │
        │                                ▼
        │                     Retrieval engine — standalone API     [G4 → BONUS 01]
        ▼
LLM (answer in user's language)                                   [G3 → BONUS 02]
        │
        ▼
Evidence validation — citations, numeric check, quote check        [G5]
        │
        ▼
Final answer — answer + citations + relevant image
```

## 7. Judgment-criteria scorecard for the suggested path

| Criterion | Suggested path as drawn | After the five fixes |
|---|---|---|
| Answer quality | Good for single-doc Q&A; shaky on comparisons & exact params | Strong across both personas |
| Ease of use | Chat UI planned; unspecified details | Web chat + streaming + citations + figures |
| Faithfulness | ⭐ Evidence validation already planned | Demo-able mechanism (checks 1–4) |
| Bonus 01 | Implicit in the retrieval-engine box | Explicit standalone API + second client |
| Bonus 02 | Not addressed | Multilingual embeddings + language policy + the no-IT-manuals demo |

## 8. Status — where the pipeline actually stands

Checked against the repo at this commit (`ingest/`, `retrieve/`, `eval/`, `docs/`).

| Pipeline box / fix | State | Evidence |
|---|---|---|
| Dataset in the workspace | ✅ done | `data/` — 22 manuals, 63 markdown files (EN/DE/IT) |
| Data processing (clean, chunk, metadata) | ✅ done | `python -m ingest.run --data data` → 1,351 chunks (706 text · 169 table · 476 figure), 0 errors · 1 warning |
| Figures as chunks + parameter index | ✅ done | 476 figure chunks with `image`; `build/params.jsonl` 2,039 rows |
| BM25 + parameter index + routing (G1) | ✅ done | `retrieve/` — 28-question benchmark: Pass@8 100%, MRR 0.95 |
| Language policy (G3, Bonus 02 demo) | ✅ done for retrieval | policy beats naive (52% same-lang, 18% dupes) and hard filter (82% pass) — 85% same-language hits, 0% duplicates |
| Eval set (§7 script) | ✅ done | `eval/questions.jsonl` — 28 Q across lookup, figure, procedure, and advisory (EN/DE/IT + 5 cross-lingual) |
| Dense retrieval (G3/G1 full) | ⚠️ code only | `ingest/embed_index.py` exists; never run/validated end-to-end |
| Product profile cards (G2 advisory) | ✅ done | `ingest/profiles.py` → `build/profiles.jsonl` (38 cards across 13 robots) + `retrieve/profiles.py` (`ProfileIndex`, `SearchResult.profiles`) |
| Picture intent & language-aware figure selection (G6 retrieval) | ✅ done | `route="figure"` in EN/DE/IT, overview diagram ranker, `Hit.labels`, EN-first cross-lingual fallback |
| Backend API (G4 → Bonus 01) | ✅ done | `api/` — FastAPI `GET /search` (`to_dict()` unchanged + `image_url`), `POST /ask`, `GET /products`, `GET /health`, `/docs`; 57 tests + a stdlib second client → [`api.md`](api.md) |
| Answer composer + evidence validation (G5) | ✅ done | `answer/` — `gemini-2.5-flash` + offline formatter, three post-checks (citation/numeric/quote) with repair; 28-question answer benchmark: 100% grounded, 0 invented numbers, 100% answer-language match, 100% cross-lingual disclosure → [`answering.md`](answering.md) |
| Multilingual answers (Bonus 02, answering half) | ✅ done | answer language = question language, evidence languages disclosed, values quoted from EN/DE sources for Italian questions → [`answering.md`](answering.md) |
| HTTP media serving (G6 display) | ✅ done | `GET /media/{doc_id}/{filename}` serves all 476 PNGs, path-traversal safe + cached; hits/citations/figures carry `image_url` |
| Chat UI | ❌ missing | the last open item: streaming answers, clickable citations, inline figures |

**Net:** data, retrieval, answering (composer + validation) and the API — including HTTP media
serving — are finished and measured; **the chat UI is what remains**. Images are indexed, returned
with an absolute `image_url` and served by `GET /media/...`; the §9a gap is closed except for UI
rendering.

## 9. Next steps (in order)

1. ~~**Get the actual dataset into the workspace**~~ ✅ `data/`, ingestion validated —
   see [`data-processing.md`](data-processing.md).
2. ~~**Retrieval engine + benchmark**~~ ✅ hybrid keyword retrieval, router, language policy —
   see [`retrieval.md`](retrieval.md).
3. ~~**~20-question eval set**~~ ✅ `eval/questions.jsonl` (doubles as the demo script).
4. ~~**Product profile cards (G2) + picture intent (`figure` route, G6 retrieval)**~~ ✅
   38 localized robot cards in `build/profiles.jsonl`, `ProfileIndex` fleet comparison, and
   multilingual `figure` routing (`100%` Pass@8, `0.95` MRR on 28-question eval).
5. ~~**FastAPI backend** (G4 → Bonus 01)~~ ✅ `api/`: `GET /search`, `POST /ask`, `GET /products`
   over `SearchEngine`; `/search` returns `to_dict()` unchanged plus `image_url`. Second client
   (`api/client.py`, stdlib only) proves plug-and-play. Includes the HTTP media endpoint
   (`GET /media/{doc_id}/{filename}`) from §9a → [`api.md`](api.md).
6. ~~**Answer composer + evidence validation** (G5)~~ ✅ `answer/` — pluggable Gemini/offline
   generation, citation + numeric + quote checks with repair and one regeneration, localized
   answer scaffolding (EN/DE/IT), and an answer-level benchmark over the 28 questions.
7. **Chat UI**: streaming answer, clickable citations, inline figure (needs §9a's `image_url`).
8. **Dense layer validation**: run `embed_index` + fusion on the 28 questions, then extend the
   benchmark with paraphrase cases.
9. **Pitch narrative**: *"the right technique per question type, every claim checkable."*

## 9a. Deferred: making images actually usable (G6)

Parked on purpose (decided after the review), **must not be dropped** — the brief explicitly asks
for "answer + citation + relevant image", and today we only have the retrieval side of it.
State of play when this was written (`main`, `124e29d`): 476 figure chunks, 476/476 `image` paths
resolve on disk, hits carry `"image": "images/en-p003R-fig01.png"` — and nothing could display or
request them. That is now fixed (see the status at the end of this section).

**Three pieces, none of them big (belongs with step 4, the API):**

1. ~~**Media endpoint**~~ ✅ Implemented in `api/media.py` + `api/app.py`:
   `GET /media/{doc_id}/{filename}` serves only `data/**/images/*.png` (four traversal checks,
   symlink-safe, `Cache-Control: public, max-age=86400`), and `/search`, `/ask` and `/products`
   return an absolute `image_url` next to the bare `image` path — see [`api.md`](api.md).
2. ~~**Picture intent (`figure` route)**~~ ✅ Implemented in `retrieve/router.py` + `retrieve/engine.py`:
   `"Show me a picture of the BellaBot"`, `"Zeig mir ein Bild vom FlashBot"`, `"Mostrami una foto..."`,
   and `"What does X look like?"` route to `figure`, filter `chunk_type="figure"`, rank `§2.2/§2.3`
   labeled appearance diagrams first, and return parsed `labels`.
3. ~~**Language-correct image choice**~~ ✅ Implemented via `plan()` + `preference` tie-breaking:
   the 476 PNGs are all distinct with localized labels baked into the pixels, so figure hits return
   the user's language first and prefer `EN` over `DE` when falling back from `IT`.

**Explicitly *not* needed:** a vision model. The dataset already ships AI figure descriptions +
`Labels:` legends ("images with transcriptions"), so we rank and serve text we already have; the
figure chunk stays the retrieval unit.

**Definition of done:** `/search` returns `image_url` for figure hits, the picture route answers
"show me a picture of the BellaBot" with ≥1 image, a DE/IT question gets the matching-language
figure, and one eval question (e.g. `L09`/`L10`) asserts an image path exists in the top hit.

**Status: met** — `tests/test_api.py::CorpusApiTest` asserts all of it over HTTP on the real corpus:
every figure hit has an `image_url` that answers `200 image/png`, `"Zeig mir ein Bild vom BellaBot"`
returns a `de-…` figure and `"Mostrami una foto del BellaBot"` an `it-…` one, and Italian + FlashBot
(no IT manual) falls back to `en-…` with `cross_lingual: true`. Only UI rendering is left.
