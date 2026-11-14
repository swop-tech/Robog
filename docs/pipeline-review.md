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
| BM25 + parameter index + routing (G1 partial) | ✅ done | `retrieve/` — 20-question benchmark: Pass@8 100%, MRR 0.90 |
| Language policy (G3, Bonus 02 demo) | ✅ done for retrieval | policy beats naive (68%) and hard filter (85%) — 85% same-language hits, 0% duplicates |
| Eval set (§7 script) | ✅ done | `eval/questions.jsonl` — 20 Q, both personas, EN/DE/IT, 3 no-Italian-source |
| Dense retrieval (G3/G1 full) | ⚠️ code only | `ingest/embed_index.py` exists; never run/validated end-to-end |
| Product profile cards (G2 advisory) | ❌ missing | router diversifies chunks, but no cross-document profiles yet |
| Backend API (G4 → Bonus 01) | ❌ missing | no FastAPI; `SearchResult.to_dict()` is only the contract |
| Answer composer + evidence validation (G5) | ❌ missing | citation/numeric/quote checks designed (§4 G5), not implemented |
| Chat UI incl. figure display | ❌ missing | plugin image path exists in the hits, nothing renders it |

**Net:** phases 1–2 (data + retrieval) are finished and measured; phase 3 (API, answers,
UI) is untouched. The scorecard in §7 still describes the target, not the current state.

## 9. Next steps (in order)

1. ~~**Get the actual dataset into the workspace**~~ ✅ `data/`, ingestion validated —
   see [`data-processing.md`](data-processing.md).
2. ~~**Retrieval engine + benchmark**~~ ✅ hybrid keyword retrieval, router, language policy —
   see [`retrieval.md`](retrieval.md).
3. ~~**~20-question eval set**~~ ✅ `eval/questions.jsonl` (doubles as the demo script).
4. **FastAPI backend** (G4 → Bonus 01): `GET /search`, `POST /ask`, `GET /products` over
   `SearchEngine`; `/search` returns `to_dict()` unchanged. Second client (curl/Telegram) to prove
   plug-and-play.
5. **Answer composer + evidence validation** (G5): answer in the user's language, citation-required
   prompt, numeric check (every number must occur in the retrieved chunks), quote check, and the
   cited snippet + page + figure returned with the answer.
6. **Chat UI**: streaming answer, clickable citations, inline figure.
7. **Product profile cards** (G2): one card per robot for comparison/advisory questions.
8. **Dense layer validation**: run `embed_index` + fusion on the same 20 questions, then extend the
   benchmark with paraphrase cases.
9. **Pitch narrative**: *"the right technique per question type, every claim checkable."*
