# Retrieval engine — hybrid search, routing, language policy

> Phase 2/3 core of [`pipeline-review.md`](pipeline-review.md) §6, built to the stack rules of
> [`stack-decisions.md`](stack-decisions.md): plain Python first, framework only where it pays.
> The keyword baseline runs offline without dependencies. An optional Chroma dense retriever
> is implemented, but requires an index and matching embedding provider.

## Keyword baseline and optional embeddings

A demo must not die on Wi-Fi, rate limits or a 470 MB model download. Everything below runs on
the chunk corpus we already produce (`ingest/`), so the retrieval behaviour — routing, language
policy, exact parameter values, citations — is settled *and measured* before a single vector
exists. The optional dense retriever contributes another ranked list to the same fusion. The evaluation
harness currently measures the keyword-only baseline, not dense retrieval.

## What it does

```
question (EN/DE/IT)
   │
   ├─ 1. language detection        retrieve/lang.py      deterministic, no model
   ├─ 2. query routing             retrieve/router.py    figure | advisory | lookup | default
   ├─ 3. domain lexicon            retrieve/lexicon.py   payload ≈ Zuladung ≈ carico
   │
   ├─ 4. BM25 over chunks          retrieve/bm25.py      strict pass + lexicon pass
   ├─ 5. parameter index           retrieve/params.py    build/params.jsonl → value + citation
   ├─ 6. product profile cards     retrieve/profiles.py  build/profiles.jsonl → 13-robot comparison
   ├─ 7. appearance/figure ranker  retrieve/engine.py    section 2.2/2.3 diagrams + callout labels
   │
   ├─ 8. reciprocal rank fusion (chunks + parameter chunks + profile overview/spec chunks)
   ├─ 9. translation dedupe + advisory/figure diversification
   └─ 10. citations + confidence   Hit.citation, Hit.labels, SearchResult.confidence
```

### 1. Language policy (Bonus 02)

Detected with stopwords, question words, domain words and character cues (`ä ö ü ß`,
`à è é ì ò ù`) — deliberately deterministic, so it can be unit-tested and cannot hallucinate.

| Tier | What happens | When |
|---|---|---|
| 1 | search **only the user's language** | 19/22 products have all three languages |
| 2 | search **all languages**, dedupe translations, prefer EN on ties | tier 1 empty or clearly weaker (score × 1.25) |

The plan skips tier 1 entirely when the product has no manual in the user's language
(Italian + FlashBot/GW1/Pager → straight to cross-lingual, with a note in the result).
The answer language is always the user's; `SearchResult.cross_lingual` tells the composer that
the evidence is in another language.

### 2. Routing (gaps G1, G2, G6)

| Route | Signal | Strategy |
|---|---|---|
| `figure` | "show me a picture", "what does X look like", "Zeig mir ein Bild", "Wie sieht X aus", "Mostrami una foto", "Com'è fatto" | filters `chunk_type="figure"`, strips visual framing words, ranks `§2.2/§2.3` labeled appearance diagrams first for overview queries (or BM25 + diagram bonus for specific callouts like emergency stop switch), and honors language policy so PNG pixel labels match the user's language |
| `advisory` | "which robot", "fits my", "Welcher Roboter eignet sich", "Quale robot è adatto", "A vs B" | scores all 13 robot profile cards (`ProfileIndex`: scenario tags + BM25 + numeric kg/cm constraints), scopes chunks to `doc_type="robot"` unless accessories are requested, demotes `§1/§4/§6` safety/maintenance noise, fuses top profile overview/spec chunks via RRF, and diversifies up to 2 chunks per robot |
| `lookup` | "how much", "Wie hoch", "Quanto", a quoted value, or any word the lexicon knows | parameter index first, BM25 second, values quoted verbatim |
| `default` | everything else | plain hybrid search |

### 3. Domain lexicon

~30 parameter concepts with their EN/DE/IT forms (`payload / load capacity / Ladekapazität /
Zuladung / carico`). It fixes the vocabulary gap that BM25 cannot ("payload" never appears in the
manuals) **and** acts as a cross-lingual bridge: a German question expands to English terms,
which is how a German question finds an English-only manual.
Missing words fall back to a prefix match ("consuma" → "consumption").

## Keyword-only benchmark (28 questions)

`python -m retrieve.evaluate` (28 questions across EN/DE/IT: exact parameter lookups, visual/figure
requests, procedures, fleet-wide advisory & head-to-head comparisons, plus five Italian questions
requiring cross-lingual fallback to EN/DE manuals):

| Configuration | Pass@8 | MRR | Hits in question language | Cross-language duplicates | Param index hit | Profile card hit |
|---|---|---|---|---|---|---|
| `no_policy` — every language at once (naive RAG) | 100% | 0.92 | 52% | 18% | 100% | 100% |
| `hard_filter` — user's language only | 82% | 0.82 | 86% | 0% | 80% | 86% |
| **`policy` — same language first, fallback** | **100%** | **0.95** | **85%** | **0%** | **100%** | **100%** |

* Checks enforce annotated keywords, product, chunk type, route (`figure`, `advisory`), image path
  presence, exact parameter matches, structured product-profile card matches, and cross-language
  expectations.
* A hard language filter loses **18 points of pass rate**: Italian lookup, figure, and elevator
  advisory questions needing FlashBot/Pager fail without fallback.
* No policy drops same-language hits from **85% → 52%** and fills **18%** of top-k slots with
  duplicate translations of passages already returned.
* Both the parameter index (`100%`) and product profile index (`100%`) satisfy all annotated
  specification and advisory expectations under `policy`.

Re-run and regenerate the report: `python -m retrieve.evaluate --out build/eval-report.md`.

## Using it

```bash
# CLI: the retrieval engine as a standalone service (Bonus 01)
python -m retrieve "Wieviel Zuladung hat der FlashBot?"
python -m retrieve "Quanto pesa il FlashBot?" --k 3 --json
python -m retrieve "filtro SH1" --language it --no-language-policy
```

```python
from retrieve.engine import SearchEngine

engine = SearchEngine.from_build()                    # build/chunks.jsonl + build/params.jsonl
result = engine.search("Qual è la velocità massima del FlashBot?", k=5)

result.language          # 'it'  — detected
result.route             # 'lookup'
result.confidence        # 'medium'
result.cross_lingual     # True  — evidence comes from DE/EN
result.notes             # ['no IT manual for FlashBot (available: DE, EN)', ...]
for hit in result.hits:
    print(hit.rank, hit.score, hit.citation)          # 'FlashBot User Manual V1.0, §2.3 …, p. 40, DE'
result.params[0].value   # '1,2 m/s' — exact value from the table
result.to_dict()         # JSON-ready, this is the shape GET /search will return
```

`to_dict()` is the contract for the FastAPI `/search` endpoint: the chat UI is only the first
client, a Telegram bot or a curl call is the second (Bonus 01).

## Known limits (and where they go)

| Limit | Next step |
|---|---|
| No paraphrase matching ("the robot won't start" vs "power-on failure") | dense retriever (multilingual-e5 / gemini-embedding) as another ranked list in the fusion — measurable now with `python -m answer.evaluate --vector` |
| **Symptom questions have no route**: "the robot keeps bumping into tables" retrieves whatever shares those words, across unrelated products, although the corpus holds `Faults: Solutions` tables | a `troubleshooting` route: symptom lexicon → the named product's fault tables, then a relevance gate in the composer |
| A large troubleshooting table can outrank the focused procedure on procedural questions | acceptable while the composer reads the top-k; a dense retriever helps here too |
| `confidence` is recall-based, not calibrated — it called junk hits for "What does error code E102 mean?" *medium* | `ValidationReport.checks_run` stops a vacuous 0/0 from being shown as a pass; a real relevance check (does the evidence address the question?) is still missing |
| Lexicon covers parameters and core scenarios, not every procedure | grows from eval misses; keep it small and explainable |
| Cue lists are literal, so an unlisted inflection changes the route ("difference between" was there, "differences between" was not) | two distinct product mentions now route `advisory` on their own; longer term an embedding-based or LLM router removes the cue list |
| A question that names products we have could still retrieve nothing when only framing words remain to score | `_product_fallback_hits`: the named products' spec/overview chunks (or figure, on the figure route), within the language policy, with a note that says the wording did not match |
| Figure chunks are 476 of 1,351 and their text is generated description prose — cosine-similar to nearly everything | `_demote_figure_hits` puts them last in the dense list on non-visual questions; BM25 already keeps them honest |
| Figure hits carry `image` + `labels` and `figure` routing selects language-appropriate PNGs | served over HTTP by `GET /media/{doc_id}/{filename}` ([`api.md`](api.md)); the web chat renders them inline |
| A Chroma index outlives the `chunks.jsonl` it was embedded from, and a dense-only hit is served from the store's own text | `corpus_digest` stamped at index time; `connect()` refuses a stale index (see [`gemini_chromadb_explanation.md`](gemini_chromadb_explanation.md) §2) |

## Next

~~Answer composer with citation/numeric/quote verification (G5)~~ — done, see
[`answering.md`](answering.md): `answer/AnswerComposer` consumes `SearchResult` unchanged, so
`result.to_dict()` stays the `/search` contract — the FastAPI backend ([`api.md`](api.md)) now
serves it unchanged at `GET /search`, adding only an absolute `image_url` per figure hit. Next: the
chat UI. Dense retrieval is optional behind `SearchEngine` (attach it to the API with
`ROBOG_API_VECTOR=auto`); it still needs end-to-end validation with a real embedding model and a
larger, relevance-judged benchmark.
