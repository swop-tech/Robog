# Robog — judge briefing

> NO! Hackathon · Telmekon Challenge · Team Robog.
> Use this sheet before the demo. Everything in it is true of the repository
> (see `README.md`, `docs/pipeline-review.md`, `docs/retrieval.md`). If a claim
> is not listed under "What is implemented", do not say it is — say it is "designed".

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
> citations. The keyword side already passes all 20 EN/DE/IT benchmark questions with an
> MRR of 0.90, including the Italian-question/no-Italian-manual cases. The dense side is
> implemented, fused, hardened and tested; the production embedding run is our next step.
> The answer composer and chat UI are the next phase and are not built yet — what you see
> is a working, measured retrieval engine with an answer contract."

## Architecture walkthrough (point, don't read)

```
PDFs ──► markdown ──► ingest/ ──► build/chunks.jsonl  (1,351 chunks, cited)
  │                        │
  │                        ├──► build/params.jsonl   (2,039 parameter rows)
  │                        └──► (planned) embed ──► build/chroma/  (ChromaDB, "robog")
  ▼
QUESTION ──► retrieve/
              lang.py      which language? (deterministic)
              router.py    lookup | advisory | default
              bm25.py      exact-word ranking
              lexicon.py   payload ≈ Zuladung ≈ carico
              params.py    exact spec values, verbatim, with page
              vector.py    ChromaDB nearest neighbours (policy-filtered)
              engine.py    RRF fusion → dedupe → diversify → top-k
                           ──► SearchResult (JSON contract)
  ▼
(next phase) answer composer + API + chat UI  — NOT BUILT, Sujal's stage
```

Numbers to keep in your head: **63 manuals → 1,351 chunks · 22 products · 3 languages ·
2,039 parameter rows · 0 validation errors · 20-question benchmark · Pass@8 100% keyword ·
MRR 0.90 · 97 tests (1 real-Chroma integration test skips until ChromaDB is installed).**

## Demo sequence (rehearse twice)

```bash
python -m ingest.run --data data                        # 3 s — dataset is real, not staged
python -m unittest discover -q                          # the proof: 97 tests
python -m retrieve.evaluate                             # the benchmark table, live
python -m retrieve "Wieviel Zuladung hat der FlashBot?" # German, exact answer: 20 kg, p. 40
python -m retrieve "Qual è la velocità massima del FlashBot?"   # IT question → cross-lingual fallback, says so
python -m retrieve "Which robot is best for a busy restaurant?"  # advisory → multiple products compared
python -m retrieve "Wieviel Zuladung hat der FlashBot?" --vector # keyword now + dense once DB exists
```

Order matters: the audience sees the tests and the benchmark **before** the chat-like CLI.
If `ingest.embed_index` has run, the `--vector` demo is the finale: same question,
`[bm25+vector]` source tags on the chunks. If it hasn't, say so plainly and show the
keyword path — never fake a dense demo.

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
The engine reports confidence from how much of the question was covered by hits. The
answer layer (next phase) is specified to refuse rather than improvise, citing the
absence of evidence. We never claim this layer exists today.

**"Does it actually support Italian?"**
Yes — detection, policy, lexicon and benchmark all cover EN/DE/IT. 19 of 22 products
have all three editions; FlashBot, GW1 and Pager lack Italian. Our policy answers in
Italian anyway, sourcing EN/DE manuals and *telling the user that is what happened*.

**"How accurate is it, really?"**
Keyword-only on our 20-question benchmark: Pass@8 100%, MRR 0.90, zero cross-language
duplicate passages, parameter index correct on 10/10 spec questions. That is a floor,
not a ceiling — and it is not a measure of answer correctness, which requires the
composer. Dense embeddings are implemented and tested but not yet validated end to end.

**"What stops it hallucinating page numbers?"**
Pages are metadata, not generated text. Retrieval quotes them from the store; nothing
else writes citations.

**"Where did the data come from?"**
The Telmekon challenge corpus: 22 PUDU robot/accessory products, official EN/DE manuals
with an Italian partner edition, converted to markdown with figure extraction; 600 MB of
source PDFs, of which we ingest only the distilled text + figure references.

**"What still needs doing?"**
Run the production embedding job (one command, free, offline-capable) and validate dense
hybrid numbers; then FastAPI surface (Bonus 01); then the answer composer with
citation/numeric verification; then the chat UI and figure display. The retrieval
contract (`SearchResult.to_dict()`) is already stable for those to plug into.

**"Why is the database not in the repository?"**
It is a build artifact. `build/` is git-ignored; every teammate regenerates it in
minutes from `data/` — same input, same chunk ids, no drift, no giant binaries in git.

## What is implemented vs planned (say it exactly like this)

**Implemented and measured:** dataset investigation, cleaning/chunking/metadata with
validation gate (0 errors), 1,351 chunks, 2,039-row parameter index, BM25 + lexicon +
language policy + routing, 20-question EN/DE/IT benchmark, dense retrieval + RRF fusion +
filtered-search hardening (wired and integration-tested), 97-test suite, citations with
section/page/language, JSON contract for the API, this repository, this documentation.

**Implemented but not yet validated with the real embedding run:** dense/hybrid retrieval
end to end (code complete, fusion live, tested with deterministic embeddings and a real
ChromaDB — not with the production model).

**Not implemented yet (this is the roadmap, not a euphemism):** production ChromaDB on
the team's machines (one free command), LLM answer generation + evidence validation,
FastAPI service, chat web UI, figure display in the UI, product profile cards for
advisory questions, Telegram bot variant.

## Team & ownership (if judges ask)

Data pipeline (Sujal) · retrieval engine (Gaurav) · judged as one system. Both are
checkable: `python -m unittest discover -q` and the benchmark are objective, on-screen proof.
