# Robog — judge briefing

> NO! Hackathon · Telmekon Challenge · Team Robog.
> Use this sheet before the demo. Everything in it is true of the repository
> (see `README.md`, `docs/retrieval.md`, `docs/answering.md`, `docs/api.md`). If a claim
> is not in the "Implemented and measured" rows of the table at the end, do not say it is
> done — say it is "pending the live run".

---

## 30 seconds

> "Manuals are where support answers go to die. We take our 22 robot and accessory
> manuals, split them into 1,351 cited chunks — text, tables, figures — and index them
> twice: exact keyword search for codes and values, and semantic vector search for meaning.
> A language policy answers in English, German or Italian. Every single claim the bot makes
> is tied to a manual title, section and page number. If the evidence isn't there, the bot
> says so — it does not invent specifications."

## 1 minute (technical)

> "Ingestion runs once: clean the markdown, chunk it, tag every chunk with product, language,
> page, section and type; tables also become a structured parameter index — 2,039 rows —
> so 'payload of the FlashBot' is a lookup, not a guess.
>
> At question time the engine detects the language, routes the question (lookup vs advisory),
> then votes with three retrievers: BM25 for exact words, a small EN/DE/IT domain lexicon for
> terms like payload/Zuladung/carico, and dense embeddings in ChromaDB for paraphrase. The
> votes fuse with Reciprocal Rank Fusion (1/(60+rank)), duplicates across the three parallel
> manual translations are dropped, and the top chunks reach the answer stage carrying their
> citations. The keyword side passes all 28 EN/DE/IT benchmark questions with an MRR of
> 0.95, including the Italian-question/no-Italian-manual cases. An answer composer then
> writes the reply — Gemini 2.5 Flash when a key is set, a deterministic formatter when
> not — and an evidence validator checks every sentence for a citation and every number
> against the cited chunk before anything is shown. All of it is one FastAPI service with
> a web chat in front. The dense side is implemented, fused and tested; its measured
> numbers with real Gemini embeddings are the one thing we fill in after the live run."

## Architecture walkthrough (point, don't read)

```
PDFs ──► markdown ──► ingest/ ──► build/chunks.jsonl  (1,351 chunks, cited)
  │                        │
  │                        ├──► build/params.jsonl   (2,039 parameter rows)
  │                        ├──► build/profiles.jsonl (38 product profile cards)
  │                        └──► embed_index ──► build/chroma/  (ChromaDB, "robog"; built per machine)
  ▼
QUESTION ──► retrieve/
              lang.py      which language? (deterministic)
              router.py    lookup | advisory | default
              bm25.py      exact-word ranking
              lexicon.py   payload ≈ Zuladung ≈ carico
              params.py    exact spec values, verbatim, with page
              profiles.py  product cards for "which robot fits…" questions
              vector.py    ChromaDB nearest neighbours (policy-filtered)
              engine.py    RRF fusion → dedupe → diversify → top-k
                           ──► SearchResult (JSON contract)
  ▼
answer/   composer (Gemini 2.5 Flash | offline formatter) + evidence validator
  ▼
api/      FastAPI: /search · /ask · /products · /media · /health
  ▼
ui/       zero-build web chat at GET /  (citations, figures, verification badge)
```

Numbers to keep in your head: **63 manuals → 1,351 chunks · 22 products · 3 languages ·
2,039 parameter rows · 38 profile cards · 0 validation errors · 28-question benchmark ·
Pass@8 100% keyword · MRR 0.95 · 28/28 answers grounded and verified (offline composer) ·
295 tests with `pip install -r requirements-api.txt` and `chromadb` installed; without them the API and real-Chroma suites skip — except `tests/test_api.py`, which cannot even be imported, so `unittest discover` reports one collection error. Install the API requirements first).**

## Demo sequence (rehearse twice)

```bash
python -m ingest.run --data data                        # 3 s — dataset is real, not staged
python -m unittest discover -q                          # the proof: 295 tests
python -m retrieve.evaluate                             # the benchmark table, live
python -m retrieve "Wieviel Zuladung hat der FlashBot?" # German, exact answer: 20 kg, p. 40
python -m retrieve "Qual è la velocità massima del FlashBot?"   # IT question → cross-lingual fallback, says so
python -m retrieve "Which robot is best for a busy restaurant?"  # advisory → multiple products compared
python -m retrieve "Wieviel Zuladung hat der FlashBot?" --vector # keyword now + dense once DB exists
python -m answer.evaluate                               # 28/28 grounded + verified, no key needed
uvicorn api.app:app                                     # the product: chat at http://localhost:8000
curl -s localhost:8000/health                           # vector_active / provider — show it, don't assert it
```

Order matters: the audience sees the tests and the benchmark **before** the chat.
`/health` decides what you may say: `vector_active: true` → "hybrid, with Gemini
embeddings"; `false` → read out `vector_detail` and show the keyword path — never fake a
dense demo. Same for `provider`: `gemini` → "Gemini writes, our validator checks";
`offline` → "the deterministic formatter, by design the floor".

## Likely judge questions — accurate answers

**"Why does a chatbot need its own vector database?"**
Grounding. A generic LLM invents torque values. Ours answers from 1,351 cited chunks
and says "not in the manuals" when the evidence is missing — the failure mode is honesty,
not confidence.

**"Why not just keyword search?"**
Users don't type manual vocabulary. BM25 nails `CC1`, `Notausschalter`, `20 kg`. Vectors
nail "the robot keeps bumping into tables". We fuse both — neither is sufficient alone.

**"Why not just vector search?"**
Numbers and rare codes anchor badly in embedding space. `1,2 m/s` and `25 Ah` are
exact-match facts; the parameter index returns them verbatim with page numbers.

**"Why Reciprocal Rank Fusion instead of learned weights?"**
No training data, no cost, no drift. RRF combines any number of ranked lists using
ranks only (1/(60+rank)), and it rewards agreement between retrievers. It is 15 lines
of code anyone can audit — appropriate for a hackathon and for a safety-adjacent product.

**"What happens if the answer isn't in the manuals?"**
The engine reports confidence from how much of the question was covered by hits, and the
composer refuses rather than improvises when the evidence is missing. Whatever the LLM
writes is then validated: uncited sentences and numbers that do not appear in the cited
chunk are removed or the answer falls back to the deterministic formatter
(`docs/answering.md`).

**"Does it actually support Italian?"**
Yes — detection, policy, lexicon and benchmark all cover EN/DE/IT. 19 of 22 products
have all three editions; FlashBot, GW1 and Pager lack Italian. Our policy answers in
Italian anyway, sourcing EN/DE manuals and *telling the user that is what happened*.

**"How accurate is it, really?"**
Retrieval, keyword-only, 28-question benchmark: Pass@8 100%, MRR 0.95, zero cross-language
duplicate passages, parameter index and profile cards hit on every question that needs
them. Answers, offline composer, same 28 questions: 100% grounded, 100% of numbers
verified against the cited chunk, expected value present in 100%. Both are harness
checks on a small set, not a user study. Hybrid numbers with real Gemini embeddings and
Gemini-written answers are measured only after the live run — until then, do not quote any.

**"What stops it hallucinating page numbers?"**
Pages are metadata, not generated text. Retrieval quotes them from the store; nothing
else writes citations.

**"Where did the data come from?"**
The Telmekon challenge corpus: 22 PUDU robot/accessory products, official EN/DE manuals
with an Italian partner edition, converted to markdown with figure extraction; 600 MB of
source PDFs, of which we ingest only the distilled text + figure references.

**"What still needs doing?"**
Run the production embedding job against the live Gemini API (one command), confirm
`/health` shows `vector_active: true` and `provider: gemini`, and record hybrid benchmark
numbers. Beyond that: auth/rate limiting, streaming answers, an answer cache, a Telegram
client on the same API.

**"Why is the database not in the repository?"**
It is a build artifact. `build/` is git-ignored; every teammate regenerates it in
minutes from `data/` — same input, same chunk ids, no drift, no giant binaries in git.

## What is implemented vs planned (say it exactly like this)

| Capability | Status | Proof / what is missing |
|---|---|---|
| Cleaning, chunking, metadata, validation gate (1,351 chunks, 0 errors) | ✅ Implemented and measured | `python -m ingest.run --data data` |
| Parameter index (2,039 rows) + product profile cards (38) | ✅ Implemented and measured | `build/params.jsonl`, `build/profiles.jsonl` |
| BM25 + lexicon + language policy + routing (lookup / advisory / figure) | ✅ Implemented and measured | `python -m retrieve.evaluate` → Pass@8 100%, MRR 0.95 (28 questions) |
| Dense retrieval + RRF fusion + filtered search, Gemini pacing/retry, resumable indexing | ✅ Implemented and tested with deterministic embeddings | `tests/test_vector.py` |
| Answer composer + evidence validator (EN/DE/IT, cross-lingual disclosure) | ✅ Implemented and measured (offline formatter) | `python -m answer.evaluate` → 28/28 grounded + verified |
| FastAPI service: `/search`, `/ask`, `/products`, `/media`, `/health` (Bonus 01) | ✅ Implemented and tested | `tests/test_api.py`, `docs/api.md` |
| Web chat with citations, figures, verification badge | ✅ Implemented and tested | `ui/`, `tests/test_ui.py` |
| Server fuses Chroma automatically and reports it | ✅ Implemented and tested | `GET /health` → `vector_active`, `vector_detail` |
| Production Chroma index built with real `gemini-embedding-2` vectors | ⏳ Pending the live run | per machine: `python -m ingest.embed_index --provider gemini` |
| Hybrid (dense + keyword) benchmark numbers | ⏳ Pending the live run | fill §7 of `gemini_chromadb_explanation.md` |
| Gemini-written answers exercised against the live API | ⏳ Pending the live run | `/health` → `provider.provider == "gemini"`, then ask |
| Auth, rate limiting, streaming, answer cache | ❌ Not built | listed in `docs/api.md` → Limits |
| Telegram bot variant | ❌ Not built | would be a second client of the same API |

The three ⏳ rows cannot be produced in CI or the hosted sandbox (no Google API egress):
they flip to ✅ only when someone has run the commands on a laptop and pasted the numbers.

## Team & ownership (if judges ask)

Data pipeline (Sujal) · retrieval engine (Gaurav) · judged as one system. Both are
checkable: `python -m unittest discover -q` and the benchmark are objective, on-screen proof.
