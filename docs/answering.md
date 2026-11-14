# Answer composer + evidence validation — grounded, multilingual, demo-proof

> Phase 3 of [`pipeline-review.md`](pipeline-review.md) §6: the box that turns retrieved
> passages into an answer, and the box that checks the answer before anyone sees it.
> Implements **`G5`** (evidence validation) and **Bonus 02** (multilingual answers),
> building on the retrieval engine of [`retrieval.md`](retrieval.md).

## What it does

```
question (EN/DE/IT)
   │
   ├─ 1. retrieve                    retrieve/engine.py    chunks + params + profiles (Bonus 01 export)
   ├─ 2. number the evidence         answer/evidence.py    [1] … [n], each with its citation trail
   ├─ 3. generate                    answer/llm.py         gemini-2.5-flash  OR  offline formatter
   ├─ 4. validate                    answer/validator.py   citation + numeric + quote checks  (G5)
   ├─ 5. repair or regenerate        answer/composer.py    strip bad numbers, re-ask once, else offline
   └─ 6. answer                      answer/composer.py    text + citations + figures + validation report
```

```bash
python -m ingest.run --data data                             # build the indexes first
python -m answer "Wieviel Zuladung hat der FlashBot?"        # offline, no API key
python -m answer "Zeig mir ein Bild vom FlashBot" --show-evidence
python -m answer "Qual è la velocità massima del FlashBot?" --provider gemini
python -m answer "Which robot fits my restaurant?" --json    # the shape POST /ask will return
python -m answer.evaluate --out build/answer-report.md       # all 28 benchmark questions
```

```python
from retrieve.engine import SearchEngine
from answer import AnswerComposer

composer = AnswerComposer(SearchEngine.from_build())      # Gemini if configured, else offline
answer = composer.answer("Quanto pesa il FlashBot?")

answer.text          # Italian answer, cites [1] [2], notes that the sources are DE/EN
answer.citations     # [{n, citation, page, excerpt, image}, …]
answer.figures       # [{image, product, caption, labels}] for the UI
answer.validation    # ok / verified / citation_rate / numeric_rate / quote_rate / issues
answer.grounded      # nothing had to be dropped, every claim is supported
```

## 1. Pluggable LLM, offline by default

| Path | When | Cost |
|---|---|---|
| `gemini-2.5-flash` via `google-genai` | `GEMINI_API_KEY` present (already used by the embedding step) | free AI Studio tier |
| **deterministic grounded formatter** (`answer/formatter.py`) | no key, no `google-genai`, quota error, timeout, malformed response, **or a failed validation with no good retry** | free, instant, offline |

No LlamaIndex: the prompt, the evidence numbering and the verification are ours, so the
framework would only add indirection. The model call is one `generate_content` request.

`build_generator()` decides once per composer and reports it (`provider`, `model`, `offline`,
`reason`), and the CLI/answer JSON say *why* it is offline (`GEMINI_API_KEY not set`,
`google-genai not installed`, or the API error). A live demo can never die because the Wi-Fi,
the quota or the key did — it degrades to a slightly less fluent answer with the same citations.

The fallback is not a stub. It renders, in the question's language:

* the exact parameter rows the retriever found — value quoted verbatim with manual, §section, page;
* the top cited passages as excerpts (tables as `row: value`, figures with their label legend);
* product profile cards side by side for advisory questions;
* a note when the evidence had to come from another language.

### 1a. Asking back instead of ranking (advisory without a scenario)

An advisory question that names no use case, environment or number — *"Which robot is best?"*,
*"Welcher Roboter ist am besten?"* — cannot be ranked: all 13 profile cards score the same. The
retriever now returns `needs_scenario: true` with no robots, and the composer answers in the
question's language by asking for the use case (restaurant, hotel/elevator, cleaning, factory load,
or a kg / cm value), the same way a lookup with no robot or parameter asks back. Questions that name a
scenario or a value are ranked as before. The check is `retrieve.profiles.has_scenario_signal`.

### 1b. Hard constraints before the shortlist

Advisory answers now apply facts stated in the question before they show any robot:

* a load in kg keeps only robots whose payload reaches it (*300 kg* → PUDU T300 only; an impossible
  load gets an explicit "no robot meets this" answer, not a substitute);
* a passage in cm keeps only robots that fit through it;
* a use case keeps only robots of that kind (cleaning / industrial / delivery).

The shortlist is capped at four robots, and when two or more have a printed passage width the answer
adds one line naming the narrowest, citing its card. Only figures printed on a card are used — no sums
or per-tray arithmetic — so the number check can verify every value.

Limit: the offline formatter still cannot *judge* between robots. Once a scenario is given it lists the
matching cards; it does not say which is better.

## 2. Multilingual answering (Bonus 02)

The answer language is the **question's** language, detected deterministically by
`retrieve.lang` and reused from the retrieval result. The prompt says *answer in X, the evidence
may be in Y, quote it as is and say which language it is in*; the offline formatter does the same
with localized scaffolding (`answer/strings.py`, EN/DE/IT).

The demo case: Italian questions about **FlashBot / GW1 / Pager**, which have no Italian manual.

```
Q: Qual è la velocità massima del FlashBot?
Secondo il manuale, **Max. moving speed** del FlashBot è **1.2 m/s** [1].

Non esiste un manuale in italiano per FlashBot: la risposta si basa su fonti in inglese e tedesco. [1]
```

Three properties make that defensible rather than decorative:

1. the value is quoted from the EN/DE source, not translated (numbers and units stay exact);
2. the answer *names* the source languages, so a user knows why the wording is not Italian;
3. the note is generated from the retrieval notes and the evidence languages, not guessed —
   `cross_lingual` and `source_languages` are in the JSON, and the benchmark checks that the
   disclosure actually happens (`cross-lingual evidence disclosed`, 100%).

## 3. Evidence validation (G5)

Three deterministic checks run on **every** answer, whoever wrote it. They are regex + set
membership: no model, no network, milliseconds — so they can be *shown*, not just claimed.

| # | Check | Rule | On failure |
|---|---|---|---|
| 1 | **Citation** | every factual sentence carries `[n]` pointing at retrieved evidence | attach the best-matching ref (≥2 shared content words, or a shared measured value) — otherwise the claim is **dropped** |
| 2 | **Numeric** | every number/unit occurs in the evidence (chunks + parameter rows + profile cards), value **and** unit | the number is **removed** from the sentence and reported; the answer never ships an invented spec |
| 3 | **Quote** | text in `"…"` / `„…“` / `«…»` appears verbatim in the cited source (case/whitespace-insensitive, ellipsis-aware) | the text is **unquoted** and reported |

Details that matter in practice:

* **Citation markers inside a quotation are neutralised first**, otherwise an excerpt that quotes a
  paragraph containing our own `[n]` markers could never verify.
* **Locators are not claims**: `§2.3`, `p. 40`, `[1, 2]` and reference-list lines are masked before
  the numeric check, so page numbers do not count as facts.
* **Value vs. unit** is checked as a pair, which catches the classic slip `25 Ah` → `25 V`
  (the number exists, the unit does not — reported as *"value appears in [3] with unit Ah"*).
* **Decimal comma vs. point**: `25,6 Ah` (DE/IT) and `25.6 Ah` (EN) are the same measurement.
* **Glued numbers**: a unit attached to letters (`V1.0`, `T300`, `IP54`) is not a claim.
* A quotation that is really a bare measurement (`"20 kg"`) is treated as a numeric claim.

If the LLM's answer cannot be repaired, the composer **re-asks once** with the concrete failures
named in the prompt; if that also fails, the deterministic formatter answers and the response says
`answered by the offline formatter after failed validation`. The caller never receives an
unvalidated answer.

Each answer ships its own audit trail — this is the JSON contract for `POST /ask` (served by
`api/app.py`; the endpoint adds `params` and a compact `verification`, see [`api.md`](api.md)):

```json
{
  "answer": "Laut Handbuch beträgt **Ladekapazität** beim FlashBot **20 kg (10 kg pro Tablett)** [1].",
  "language": "de",
  "provider": "offline",
  "grounded": true,
  "verified": true,
  "citations": [{"n": 1, "citation": "FlashBot User Manual V1.0, §2.3, p. 40, DE", "page": 40}],
  "figures": [],
  "validation": {"ok": true, "citation_rate": 1.0, "numeric_rate": 1.0, "quote_rate": 1.0,
                 "numbers_checked": 2, "numbers_verified": 2, "issues": []},
  "cross_lingual": false,
  "source_languages": ["de"],
  "notes": ["offline formatter (GEMINI_API_KEY not set)"]
}
```

## 4. Measured, not assumed

`python -m answer.evaluate` answers the same 28 questions of `eval/questions.jsonl` and scores the
answers — the offline formatter by default (no key), or Gemini with `--provider gemini`:

| Metric | Offline formatter | What it proves |
|---|---|---|
| answered | 100% | no question leaves the pipeline without an answer |
| grounded (all three checks passed) | 100% | nothing unverifiable survives |
| fully verified (no repair needed) | 100% | the formatter's own output is clean, not just fixed up |
| claims cited | 100% | every factual sentence has a source |
| numbers verified against evidence | 100% | no invented specification |
| quotes verified verbatim | 100% | quotations are real |
| answer in the question's language | 100% | Bonus 02 |
| cross-lingual evidence disclosed | 100% | the Italian/DE-EN case says so explicitly |
| expected value present (lookup/advisory) | 100% | the answer contains the value the benchmark asks for |
| unverified numbers surviving | 0 | the numeric check holds end to end |

Re-run and regenerate: `python -m answer.evaluate --out build/answer-report.md`
(the report lists every question, its route, its check counters and any answer that failed).

## 5. Layout

| File | Role |
|---|---|
| `answer/evidence.py` | numbers the evidence, normalises numbers/units, collects profile/param/chunk refs |
| `answer/validator.py` | the three checks + the repair actions (`validate_answer`) |
| `answer/formatter.py` | deterministic grounded answer, no model (the fallback and the baseline) |
| `answer/llm.py` | `GeminiGenerator` / `OfflineGenerator`, `build_generator`, provider metadata |
| `answer/prompt.py` | the grounding prompt (numbered evidence in, cited answer out) |
| `answer/composer.py` | retrieve → evidence → generate → validate → (re-ask | fall back) → `Answer` |
| `answer/evaluate.py` | answer-level benchmark over the 28 questions |
| `answer/strings.py` | the localized sentences Robog writes itself (EN/DE/IT) |
| `tests/test_answer.py` | 78 tests: checks, formatter, generators, composer, evaluator, real corpus |

## 6. Known limits (and where they go)

| Limit | Next step |
|---|---|
| The offline formatter quotes rather than explains — good for lookups and comparisons, terse for procedures | the LLM path handles prose; the formatter is the floor, not the target |
| **One register for two personas**: the brief's non-technical user gets `565 mm×537 mm×1290 mm` and `25.6 Ah`, the technician gets the same list | use the route (and an advisory/lookup cue) to pick the register: plain-language recommendation first for advisory, spec sheet for lookup |
| A lookup that names neither robot nor parameter used to be answered with whatever BM25 returned for the words "value"/"parameter" | the composer now asks back for robot + parameter, in the question's language, and reports `checks_run = false` |
| `verified` is vacuously true for an answer grounded in nothing (0/0 claims) | `ValidationReport.checks_run` + `verification.state` in `/ask`; the UI renders a neutral badge instead of a green one |
| Grounding is not relevance: junk evidence can still be fully cited and numerically clean ("error code E102" → passages about the power switch, all checks passed) | a relevance gate — does the cited evidence address the question? — before an answer is allowed to look verified |
| Evidence is capped at the top ~5 chunks / 3 parameter rows / all profile cards | tune per route once `/ask` has real traffic |
| A quote is verified against *the refs the sentence cites*; a sentence citing nothing falls back to the whole evidence set | acceptable: the citation check already forces a citation (or drops the claim) |
| Numeric check is membership, not dimensional analysis (`20 kg` vs `20 cm` is caught, `20 kg` vs `20 kg/m²` is not) | unit-pair comparison covers the corpus' units; extend `UNITS` when new manuals arrive |
| No answer-quality human eval yet; the 28-question benchmark grades keyword presence in answers the same repo wrote | a second, relevance-judged set from off-benchmark phrasings — [`brief-alignment.md`](brief-alignment.md) §4.2/§4.5 |
| Gemini path not exercised against the live API in CI (no key) | tested through a fake client; run `--provider gemini` before the demo and keep the offline mode as the fallback |
| Dense layer unmeasured on answers | `python -m answer.evaluate --vector` prints `retrieval: hybrid (dense …) | keyword only`, so a keyword-only run can no longer be reported as the hybrid |

## Next

~~FastAPI (`/search`, `/ask`, `/products`, `/media/{doc_id}/{filename}`)~~ — done, see
[`api.md`](api.md): `POST /ask` answers with `Answer.to_dict()` (the contract above, plus `params`
and a compact `verification`), `GET /search` with `SearchResult.to_dict()`, and every citation and
figure carries a fetchable `image_url`. The web chat (`ui/`, served at `GET /`) now renders all of
it — clickable citations, inline figures with their `Labels:` legend, the verification badge and a
live Raw `/search` JSON drawer; see [`../ui/README.md`](../ui/README.md). Streaming remains future polish.
