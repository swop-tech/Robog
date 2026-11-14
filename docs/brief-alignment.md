# Brief alignment — what the challenge asks, what Robog actually answers

> Read this before the demo. Source: `Telmekom_Challenge26.pdf` (14 slides). Every claim
> below was measured in this repository, not copied from a slide; the commands are listed
> in §6 so anyone can re-run them.

## 1. What the brief asks for

**Mission.** "Build a system that turns buried documentation into accurate, clear,
source-grounded answers." Starting data: a curated collection of markdown files distilled
from the robots' PDFs, with images and transcriptions.

**Two users, one system.**

| Persona | Their question, verbatim from the brief | What they need |
|---|---|---|
| Technician | "What's the value of this parameter?" | accurate |
| Non-technical user | "Which robot fits my customer's needs?" | clear |

Both need an answer that is **accurate, clear, grounded in the data**.

**How they judge:** quality of the answers · ease of use · faithfulness to the source.

**Required:** answers grounded in data and faithful to the source · an architecture you can
defend · at least one working interface where end users ask questions and get answers.

**Bonus 01:** keep retrieval separate from the interface and expose it (API).
**Bonus 02:** users ask and are answered in their own language, even when the source is in
another one.

**Approach:** "Your architecture. Your call." RAG is a good place to start, LLM-generated
wikis are another, own ideas allowed — *pick it, build it, defend it.*

## 2. Scoreboard

| Brief criterion | What Robog does | Measured | Verdict |
|---|---|---|---|
| Grounded, faithful answers (required) | Every answer is post-checked (G5): each citation must point at a retrieved chunk, each number/unit must occur in the evidence, quotes must be verbatim; a wrong number or citation discards a generated answer, a sentence without a source is kept and reported as uncited (`ROBOG_ANSWER_STRICT=1` drops it instead) | 28-question benchmark: 100% grounded, 100% claims cited, 0 invented numbers | **met** |
| Quality of the answers (judged) | Offline formatter composes from evidence: exact parameter rows, profile-card comparisons, figure legends | accurate on lookups/comparisons; **terse on procedures, no persona register** — see §4.1 | **partly met** |
| Ease of use (judged) | Zero-build assistant UI at `GET /`: starter chips, inline citations, spec + product cards, rendered figures, verification badge, raw-`/search` drawer, light/dark, saved conversations, settings | works desktop + mobile; **follow-up turns still need the backend** — see §4.3 | **partly met** |
| Architecture you can defend (required) | Keyword hybrid (BM25 + parameter index + profile cards + figure route + language policy) with an optional dense layer (Chroma + Gemini/e5), RRF-fused | dense layer is **optional, unbuilt and unmeasured**; docs described a template pipeline that was never implemented — corrected in `gemini_chromadb_explanation.md` §2 | **defensible only with §4.4 closed** |
| Working interface (required) | FastAPI + web chat, `python -m api` | 341 tests pass; `/health`, `/`, `/search`, `/ask`, `/products`, `/media` | **met** |
| Bonus 01 — plug-and-play retrieval | `GET /search` returns `SearchResult.to_dict()` unchanged; `api/client.py` is a second client | 67 API tests | **met** |
| Bonus 02 — many languages | Ask in EN/DE/IT, answer in the asker's language, disclose cross-lingual evidence | 100% answer-language match, 100% disclosure | **met** |

## 3. The brief's two persona questions, asked literally

Run against this branch (`python -m answer "<question>"`):

**Non-technical: "Which robot fits my customer's needs?"** → route `advisory`, 6 profile
cards compared, 8/8 claims cited, 72/72 numbers verified. Correct and grounded — but the
register is a technician's: `payload 10 kg/layer (Four), dimensions 565 mm×537 mm×1290 mm,
battery 25.6 Ah / 12–24 h` plus the manuals' marketing paragraph. The brief asks for
*clear* for this persona (§4.1).

**Technician: "What's the value of this parameter?"** → names neither robot nor parameter.
Before this branch it answered with a detergent-pH passage from a docking-station manual
and a green **"✓ Verified · 1/1 cited · 2/2 numbers"** badge — a confident non-answer, the
single most damaging thing a judge could see. Now it asks back, in the question's language:

> Which robot and which parameter do you mean? The manuals cover 13 robots and 9
> accessories, so name one (for example FlashBot, BellaBot, SH1, T300) and the value you
> need (payload, battery, speed, tank capacity, dimensions) and I will quote it with its page.

and `/ask` reports `verification.state = "no-evidence"`, `checked = false`, so the UI shows
a neutral badge instead of a green one (`0/0` passes every check vacuously).

## 4. What still does not answer "accordingly" — prioritised

### 4.1 No persona register (judged: *quality of the answers*)
The brief names two users; the system gives both the same output. A non-technical reader
gets millimetres and ampere-hours, a technician gets the same list. The router knows the
intent (`advisory` vs `lookup`) — the formatter does not use it to change register: no
plain-language lead ("For a busy restaurant, BellaBot … because …"), no spec tiering
(deciding specs first, rest behind a fold), no "ask me about your venue" follow-up when the
question is under-specified but advisory.

### 4.2 Symptom questions have no route (persona: technician)
Technicians mostly describe symptoms, not sections. Measured on this branch:

| Question | Route | Result |
|---|---|---|
| "the robot won't start, what should I check?" | `default` | passages from unrelated charging-station manuals (CC1, CC1 Pro, Charging Station Light), citations out of order, 4 claims repaired |
| "The robot keeps bumping into tables" | `default` | one loosely related delivery-mode row, plus a figure from an unrelated docking station |
| "how do i calibrate the lidar" | `default` | 4 of 5 hits are AI-written *figure descriptions* |
| "What does error code E102 mean?" | `default` | unrelated passages, still badged verified (0 error codes exist in the corpus: `grep -c "error code" data/**.md` → 0) |

The corpus does contain `Faults: Solutions` tables; nothing maps a symptom onto them. A
`troubleshooting` route (symptom lexicon → fault tables of the named product) plus a
relevance gate that refuses to badge an answer whose evidence does not address the question
would close this. Out-of-corpus questions (error codes, price, warranty terms) should say
so explicitly — the price question already does.

### 4.3 It is not a conversation (judged: *ease of use*)
`/ask` is stateless and `AskRequest` has no field for prior turns, so each question is still
answered on its own: no "and the Pro version?", no "in German please". The UI rewrite closed
the part that is closable client-side — conversations are kept, titled, filtered and restored from
`localStorage` (25 max, oldest dropped when the quota is hit), the sidebar lists them, and the
answer language / evidence depth / product scope / language policy are switchable per session — and it says plainly that this history lives in the browser only. What is *not* fixed
is the follow-up turn, which needs the backend: `docs/ui-backend-gaps.md` §2 specifies the
`/conversations` routes and §1 the streaming variant, and `ui/api.js::CAPABILITIES` keeps the
UI from pretending either exists.

### 4.4 The dense layer is unmeasured (required: *defend your architecture*)
`build/chroma` is not in the repo and cannot be built without a Gemini key or a
`multilingual-e5-small` download; `/health` reports `vector_active: false`, so every number
in the README is the **keyword floor**. `docs/retrieval.md` already says end-to-end
embedding quality is unvalidated. Two things changed on this branch to make it measurable
and safe:

- `python -m answer.evaluate --vector` now benchmarks the hybrid on *answers* (it prints
  `retrieval: hybrid (dense …) | keyword only`, so a run can never silently report the
  keyword floor as the hybrid).
- Indexes are stamped with a `corpus_digest` and `connect()` refuses a stale one — a
  dense-only hit is served from the store's own copy of the text, so a stale index could
  otherwise cite words that exist in no current manual.
- Figure chunks (476 of 1,351 — AI-written descriptions, verbose and semantically generic)
  are demoted to the end of the dense list on non-visual questions; BM25 already keeps them
  honest, the dense half now does too.

Still open: no reranker, no dense-quality judgement set, and 35% of the embedded corpus is
generated prose rather than manual text.

### 4.5 The benchmark measures presence, not usefulness
`eval/questions.jsonl` was written by the team and is graded by keyword/product/route
presence, so "100%" means *the expected string appeared*, not *the answer is good*.
`docs/answering.md` lists "no answer-quality human eval yet" as a known limit. A second,
relevance-judged set built from off-benchmark phrasings (§4.2's table is a start) is what
would make the numbers mean something to a judge.

## 5. Fixed on this branch

| Failure (measured before) | Fix | Test |
|---|---|---|
| "What are the **differences** between BellaBot and BellaBot Pro?" → **0 hits**, "I could not find this" (the cue list carried only the singular) | plural/verb comparison cues in EN/DE/IT, **and** two distinct product mentions now route `advisory` on their own | `test_comparison_plural_and_verb_forms`, `test_plural_comparison_of_two_named_products_is_advisory` |
| "Is the KettyBot waterproof?" → **0 hits** for a robot we have | product fallback: the named products' spec/overview chunks, language-policy-respecting, with a note that says the wording did not match | `test_product_fallback_when_the_wording_matches_nothing`, `test_product_fallback_respects_a_hard_language_filter` |
| Green "✓ Verified · 0/0" on an answer grounded in nothing | `ValidationReport.checks_run`, `verification.checked`/`.state` in `/ask`, neutral UI badge, honest CLI line | `test_no_evidence_report_reports_checks_not_run`, `test_ask_does_not_certify_an_answer_it_could_not_ground` |
| Brief's technician question answered with a detergent-pH passage | under-specified lookup gate: ask for robot + parameter, in the question's language | `test_under_specified_lookup_asks_for_the_robot_and_parameter` (+ IT) |
| `(industrial_delivery)` raw slug shown to end users | category label table + humanizing fallback for unknown slugs | `answer/formatter.py::_category_label` |
| A stale Chroma index could be cited as current | `corpus_digest` stamp + refusal in `connect()` | `CorpusFingerprintTests`, `test_corpus_digest_roundtrip_and_stale_refusal_on_real_chroma` |
| `docs/gemini_chromadb_explanation.md` described a template pipeline that does not exist | §2 rewritten to match the code, with the discrepancy stated openly | — |

## 6. Reproduce everything in this document

```bash
python -m ingest.run --data data                       # 63 files -> 1,351 chunks
python -m unittest discover -s tests -p "test_*.py"    # 341 tests
python -m retrieve.evaluate                            # keyword benchmark (Pass@8 100%, MRR 0.95)
python -m answer.evaluate                              # answer benchmark, keyword floor
python -m answer.evaluate --vector                     # same, through the hybrid (needs build/chroma)

# the brief's own persona questions
python -m answer "Which robot fits my customer's needs?"
python -m answer "What is the value of this parameter?"

# the off-benchmark phrasings of §4.2
python -m answer "the robot won't start, what should I check?"
python -m answer "What does error code E102 mean?"
python -m answer "how do i calibrate the lidar"

python -m api                                          # chat at /, Swagger at /docs
curl -s localhost:8000/health                          # vector_active: is the dense layer on?
```
