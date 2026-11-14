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
   ├─ 1. language detection        retrieve/lang.py     deterministic, no model
   ├─ 2. query routing            retrieve/router.py    lookup | advisory | default
   ├─ 3. domain lexicon           retrieve/lexicon.py   payload ≈ Zuladung ≈ carico
   │
   ├─ 4. BM25 over chunks         retrieve/bm25.py      strict pass + lexicon pass
   ├─ 5. parameter index          retrieve/params.py    build/params.jsonl → value + citation
   │
   ├─ 6. reciprocal rank fusion (chunks + parameter chunks)
   ├─ 7. translation dedupe + advisory diversification
   └─ 8. citations + confidence   Hit.citation, SearchResult.confidence
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

### 2. Routing (gap G2)

| Route | Signal | Strategy |
|---|---|---|
| `lookup` | "how much", "Wie hoch", "Quanto", a quoted value, or any word the lexicon knows | parameter index first, BM25 second, values quoted verbatim |
| `advisory` | "which robot", "Welcher Roboter eignet sich", "Quale robot consiglia", "A vs B" | broader search, max 2 chunks per product so the answer can compare |
| `default` | everything else | plain hybrid search |

### 3. Domain lexicon

~30 parameter concepts with their EN/DE/IT forms (`payload / load capacity / Ladekapazität /
Zuladung / carico`). It fixes the vocabulary gap that BM25 cannot ("payload" never appears in the
manuals) **and** acts as a cross-lingual bridge: a German question expands to English terms,
which is how a German question finds an English-only manual.
Missing words fall back to a prefix match ("consuma" → "consumption").

## Small keyword-only benchmark

`python -m retrieve.evaluate` (20 questions: both personas, EN/DE/IT, figures, procedures, three
Italian questions on manuals that have no Italian):

| Configuration | Pass@8 | MRR | Hits in question language | Cross-language duplicates | Param index hit |
|---|---|---|---|---|---|
| `no_policy` — every language at once (naive RAG) | 100% | 0.88 | 68% | 12% | 100% |
| `hard_filter` — user's language only | 85% | 0.78 | 85% | 0% | 80% |
| **`policy` — same language first, fallback** | **100%** | **0.90** | **85%** | **0%** | **100%** |

* These are automated keyword/product checks, not proof of answer correctness. Advisory cases
  check product diversity only. Parameter and cross-language expectations are checked when annotated.
* A hard language filter loses **15 points of pass rate**: the three Italian questions about
  Italian-less manuals return nothing. That is the Bonus 02 case, not an edge case.
* No policy costs **17 points of same-language context** and fills 12% of the slots with the same
  passage in another language — the dilution argument, quantified.
* The parameter index matches the annotated keywords for 10/10 specification cases. This does
  not independently verify that every returned parameter value or citation is correct.

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
| No paraphrase matching ("the robot won't start" vs "power-on failure") | dense retriever (multilingual-e5 / gemini-embedding) as another ranked list in the fusion |
| Advisory answers need cross-document synthesis, not just spread-out chunks | product profile cards (gap G2) on top of the same engine |
| A large troubleshooting table can outrank the focused procedure on procedural questions | acceptable while the composer reads the top-k; a dense retriever helps here too |
| `confidence` is recall-based, not calibrated | refine together with the answer composer + evidence validation (G5) |
| Lexicon covers parameters, not procedures | grows from eval misses; keep it small and explainable |

## Next

FastAPI (`/search`, `/ask`, `/products`) on top of `SearchEngine` → chat UI → answer composer with
citation/numeric verification (G5). Dense retrieval is optional behind `SearchEngine`; it still needs end-to-end validation with
a real embedding model and a larger, relevance-judged benchmark.
