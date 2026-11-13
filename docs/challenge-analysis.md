# Telmekom Challenge 2026 — Analysis & Suggested Path

> Source: `Telmekom_Challenge26.pdf` (14 pages). Repo: `Robog`, branch `arena/cb500270-robog`.

## 1. The challenge in one line

Turn buried robot documentation (PDF user manuals, datasheets, product sheets, specifications,
accessories guides — provided as **curated markdown files** distilled from the PDFs, plus
**images with transcriptions**) into a system **anyone can talk to**, giving **accurate, clear,
source-grounded** answers.

## 2. How we are judged

| Criterion | What it means in practice |
|---|---|
| **Quality of the answers** | Precise values for technicians; clear, coherent recommendations for non-technical users |
| **Ease of use** | At least one **working interface** real end users can talk to (REQUIRED) |
| **Faithfulness to the source** | Answers grounded in the provided data; nothing invented; ideally verifiable citations |

**Bonus 01 — Plug-and-play retrieval:** keep the retrieval engine separate from the interface and
expose it (API), so other frontends (web app, chatbot, voice assistant, existing tools) can use it.

**Bonus 02 — Multilingual:** users ask and receive answers in their own language, even when the
source is in another language.

## 3. Two user personas → two query modes

The brief explicitly names two audiences, and their questions are structurally different:

1. **Technicians — "lookup mode"**
   *"What's the value of this parameter?"*
   → Needs exact extraction: parameter names, values, units, conditions. Tolerance for error is
   near zero. Best served by structured/keyword retrieval with tight quoting.

2. **Non-technical users — "advisory mode"**
   *"Which robot fits my customer's needs?"*
   → Needs cross-document synthesis: comparison, translation of specs into benefits, plain
   language. Best served by semantic retrieval across the whole corpus + an LLM that composes.

A single system must route or blend both. This distinction is the key design insight of the brief.

## 4. The suggested paths in the brief (page 8)

The challenge explicitly offers two "good place to start" options — and says you may bring your own
idea as long as you can **defend** it.

### Option A — RAG (Retrieval-Augmented Generation)

Chunks the markdown corpus, embeds it, retrieves the top-k relevant chunks per question, and asks
an LLM to answer **only from those chunks**.

- ✅ Strong faithfulness story: answers carry citations back to the source chunk/file.
- ✅ Natural fit for lookup mode (parameter questions retrieve the exact spec chunk).
- ✅ Bonus 01 falls out almost for free: the retriever is a separate service with a `/search` API.
- ✅ Bonus 02 is solvable: multilingual embeddings + "answer in the user's language" instruction.
- ⚠️ Weak spot: advisory mode ("which robot fits...") needs information spread over many chunks;
  naive top-k retrieval can miss half the comparison material.
- ⚠️ Chunking quality and retrieval recall decide answer quality — garbage chunks, garbage answers.

### Option B — LLM-generated wikis

Pre-process the documentation once into a curated, LLM-generated wiki/knowledge base; users then
talk to (or browse) that wiki.

- ✅ Excellent for advisory mode: the generation step can synthesize comparison pages ("Robot X vs
  Robot Y", "Choosing a robot for warehouse use") that answer whole classes of questions well.
- ✅ Fast, cheap at query time; coherent, readable structure; doubles as a browsable artifact.
- ⚠️ Faithfulness risk is *baked in at build time*: hallucinations in the wiki are much harder to
  spot and defend than a wrong retrieval.
- ⚠️ "What's the value of parameter X?" must be answered from the *original* data with proof —
  a wiki paraphrase loses the exactness and the citation trail judges will look for.
- ⚠️ Stale the moment the source docs change.

### Verdict: Option C — **Hybrid (recommended, defensible)**

**RAG as the core engine**, plus a **curated structured layer** extracted from the markdown
(parameter/value tables per robot, product cards) and a small set of **generated overview pages**
for advisory questions.

Why this wins:

- Lookup mode → answered from the structured parameter index / exact chunks → maximum accuracy,
  always citable → nails "faithfulness" and "quality" for technicians.
- Advisory mode → semantic retrieval over docs + generated overview/comparison content → coherent
  plain-language answers → nails "quality" for non-technical users.
- Defense story: *"We use the simplest technique that each question type demands — exact data for
  exact questions, synthesis for advisory questions — and every claim remains traceable to a
  source chunk."* Judges ask "why this architecture?" — this is a clean answer.

## 5. Recommended architecture (maps 1:1 to judging + bonuses)

```
 [Web chat UI] ────┐
 [Telegram bot] ───┼───>  API layer (FastAPI)
 [Voice (later)] ──┘       POST /ask      → answer + citations
                           GET  /search   → raw retrieval results
                           GET  /products → structured product/param index
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │   Retrieval engine       │   ◄── BONUS 01: standalone,
                    │  hybrid: BM25 (exact     │       any frontend can call it
                    │  params/names) + dense   │
                    │  multilingual vectors    │   ◄── BONUS 02: cross-lingual
                    │  + structured param DB   │
                    └─────────────────────────┘
                                 │ top-k chunks + metadata
                                 ▼
                    Answer composer (LLM, forced to cite)
                                 │
                    Faithfulness check (numbers/units
                    verified against retrieved chunks)
                                 │
                              Answer
```

**Data layer:** markdown files kept as the single source of truth → chunked by heading structure
(file path + robot + section kept as metadata); image transcriptions ingested alongside; tables
parsed into a small structured index (robot → parameter → value, unit, source).

**Retrieval layer (the crown jewel):** hybrid retrieval — BM25/keyword for exact parameter names
and product codes, dense multilingual embeddings (e.g. `multilingual-e5-small`) for semantic and
cross-lingual questions, optional reranking. Query router: parameter-shaped questions hit the
structured index first; the rest go hybrid.

**Generation layer:** answer strictly from retrieved context, every factual claim must cite the
source file/section; answer in the user's language (language detected per message).

**Interface layer:** a clean web chat (streaming, citations shown inline, suggested questions for
both personas) is the REQUIRED interface. A Telegram bot is a cheap second interface proving the
"plug-and-play" API claim.

## 6. How this hits each judging criterion

- **Faithfulness:** chunk-level citations shown in the UI; prompt constrains the LLM to context
  only; numeric values post-checked against the source chunk before the answer is returned.
- **Answer quality:** hybrid retrieval + persona routing (lookup vs advisory); generated comparison
  content for cross-product questions; suggested questions demonstrating both personas.
- **Ease of use:** one obvious chat box; streaming; answers in the user's language; source snippets
  one click away so users can verify.

## 7. Bonus plan

- **Bonus 01:** retrieval engine runs as its own service with a documented `/search` endpoint;
  the web chat is just its first client. Demo: hit `/search` with curl during the pitch.
- **Bonus 02:** multilingual embeddings for retrieval + per-message language detection + "reply in
  the user's language" prompt policy. Demo: ask in German/Italian, answer in the same language,
  source in another.

## 8. Execution plan (hackathon-shaped)

1. **Milestone 0 — Data & baseline (half day):** ingest markdown + transcriptions; build the chunk
   store and structured param index; one-command `docker compose up`.
2. **Milestone 1 — Retrieval engine (day 1):** hybrid search service with `/search`; sanity-check
   recall on real questions from both personas (build a tiny eval set of ~20 Q/A from the docs).
3. **Milestone 2 — Answering (day 1–2):** `/ask` with citations + faithfulness check; the 20-question
   eval set becomes the demo script and the regression net.
4. **Milestone 3 — Interface (day 2):** web chat UI (streaming + citations + suggested questions);
   optional Telegram bot to prove plug-and-play.
5. **Milestone 4 — Bonuses & polish (day 2–3):** multilingual pass, comparison content for advisory
   mode, pitch deck: problem → architecture → live demo → why-this-architecture defense.

## 9. Open items / blockers

- [ ] **The markdown dataset was not attached to this session** — only the PDF challenge brief
      arrived. Need the markdown files (and image transcriptions) to start ingestion.
- [ ] **The photo describing "our steps" was not attached either** — please re-upload it so we can
      align this analysis with the team's intended plan.
- [ ] LLM provider & API key availability (cloud API vs local model) — decides the generation layer.
- [ ] Source language(s) of the markdown corpus (matters for Bonus 02 strategy).
