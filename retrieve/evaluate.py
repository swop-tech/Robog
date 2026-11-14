"""Retrieval evaluation: does the engine find the right passage, in the right language?

The 20 questions in ``eval/questions.jsonl`` cover both personas (lookup and
advisory), all three languages, figures, procedures, and three questions that
*must* be answered from a manual in another language (Italian + FlashBot/GW1/
Pager, which have no Italian edition).

Three configurations are compared, which is how the language policy is
justified with numbers instead of opinion:

* ``no_policy``   -- search every language at once (what a naive RAG does);
* ``hard_filter`` -- only the user's language (breaks on the Italian-less manuals);
* ``policy``      -- the user's language first, cross-lingual only when needed.

Run it (no dependencies, no embedding model):

    python -m retrieve.evaluate
    python -m retrieve.evaluate --k 5 --verbose
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from .engine import SearchEngine
from .text import fold

QUESTIONS_PATH = Path("eval/questions.jsonl")

CONFIGS = {
    "no_policy": dict(follow_user_language=False, dedupe_translations=False),
    "hard_filter": dict(follow_user_language=True, cross_lingual_fallback=False),
    "policy": dict(follow_user_language=True, cross_lingual_fallback=True),
}


def _norm(text: str) -> str:
    """Compare keyword expectations ignoring case, accents and decimal commas."""
    return fold(text).replace(",", ".")


@dataclass
class QuestionResult:
    qid: str
    question: str
    language: str
    route: str
    rank: int | None = None          # rank of the first hit matching the expectation
    same_language_share: float = 0.0
    duplicate_share: float = 0.0
    products: int = 0
    param_hit: bool | None = None
    cross_lingual: bool = False
    confidence: str = "none"
    passed: bool = False


@dataclass
class ConfigResult:
    name: str
    results: list[QuestionResult] = field(default_factory=list)

    # -- metrics -----------------------------------------------------------
    @property
    def recall(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    @property
    def mrr(self) -> float:
        ranks = [r.rank for r in self.results if r.rank]
        if not ranks:
            return 0.0
        return sum(1.0 / r for r in ranks) / len(self.results)

    @property
    def same_language_share(self) -> float:
        return _mean([r.same_language_share for r in self.results])

    @property
    def duplicate_share(self) -> float:
        return _mean([r.duplicate_share for r in self.results])

    @property
    def param_rate(self) -> float:
        with_expectation = [r for r in self.results if r.param_hit is not None]
        return _mean([1.0 if r.param_hit else 0.0 for r in with_expectation]) if with_expectation else 0.0


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def _matches(hit_text: str, keywords: list[str]) -> bool:
    text = _norm(hit_text)
    return any(_norm(keyword) in text for keyword in keywords)


def evaluate(engine: SearchEngine, questions: list[dict], k: int = 8, config: str = "policy") -> ConfigResult:
    options = CONFIGS[config]
    result = ConfigResult(config)

    for question in questions:
        expect = question.get("expect", {})
        search = engine.search(question["question"], k=k, **options)
        hits = search.hits

        # rank of the first hit that satisfies the expectation
        rank = None
        for hit in hits:
            if expect.get("chunk_type") and hit.chunk_type != expect["chunk_type"]:
                continue
            keywords = expect.get("keywords")
            if keywords and not _matches(hit.text + " " + hit.metadata.get("section_title", ""), keywords):
                continue
            if expect.get("product") and hit.product != expect["product"]:
                continue
            rank = hit.rank
            break

        products = len({hit.product for hit in hits})
        min_products = expect.get("min_products", 0)
        passed = bool(rank) if expect.get("keywords") or expect.get("chunk_type") else products >= min_products

        # cross-language duplicates: same translation group, different language
        seen_groups: dict[str, str] = {}
        duplicates = 0
        for hit in hits:
            group = hit.metadata.get("translation_group", hit.chunk_id)
            language = seen_groups.get(group)
            if language is not None and language != hit.language:
                duplicates += 1
            else:
                seen_groups[group] = hit.language

        param_hit = None
        if expect.get("expect_param"):
            param_keywords = expect.get("keywords", [])
            param_hit = any(
                _matches(f"{p.parameter} {p.value}", param_keywords) for p in search.params
            )

        result.results.append(
            QuestionResult(
                qid=question["id"],
                question=question["question"],
                language=search.language or "?",
                route=search.route,
                rank=rank,
                same_language_share=(
                    sum(1 for hit in hits if hit.language == search.language) / len(hits) if hits else 0.0
                ),
                duplicate_share=(duplicates / len(hits)) if hits else 0.0,
                products=products,
                param_hit=param_hit,
                cross_lingual=search.cross_lingual,
                confidence=search.confidence,
                passed=passed,
            )
        )
    return result


def render(results: dict[str, ConfigResult], k: int, verbose: bool = False) -> str:
    lines = [
        "# Retrieval evaluation",
        "",
        f"> {len(next(iter(results.values())).results)} questions, k={k}, "
        "no embedding model needed (`python -m retrieve.evaluate`).",
        "",
        "| Configuration | Recall@k | MRR | Hits in question language | Cross-language duplicates | Param index hit |",
        "|---|---|---|---|---|---|",
    ]
    for name, result in results.items():
        lines.append(
            f"| `{name}` | {result.recall:.0%} | {result.mrr:.2f} | "
            f"{result.same_language_share:.0%} | {result.duplicate_share:.0%} | {result.param_rate:.0%} |"
        )
    lines += [
        "",
        "*Cross-language duplicates*: share of returned chunks that repeat a passage "
        "already returned in another language -- exactly the top-k dilution the "
        "language policy exists to prevent.",
        "",
        "## Per question (policy)",
        "",
        "| id | question | lang | route | first correct hit | same-lang | confidence | ok |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results["policy"].results:
        mark = "✅" if r.passed else "❌"
        lines.append(
            f"| {r.qid} | {r.question[:58]} | {r.language} | {r.route} | "
            f"{r.rank if r.rank else '—'} | {r.same_language_share:.0%} | {r.confidence} | {mark} |"
        )
    if verbose:
        failed = [r for r in results["policy"].results if not r.passed]
        if failed:
            lines += ["", "### Misses", ""]
            for r in failed:
                lines.append(f"- **{r.qid}** {r.question} → rank {r.rank}, confidence {r.confidence}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate retrieval quality and the language policy.")
    parser.add_argument("--questions", default=QUESTIONS_PATH, type=Path)
    parser.add_argument("--chunks", default="build/chunks.jsonl", type=Path)
    parser.add_argument("--params", default="build/params.jsonl", type=Path)
    parser.add_argument("--k", default=8, type=int)
    parser.add_argument("--out", default=None, type=Path, help="write the markdown report here")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    with args.questions.open(encoding="utf-8") as fh:
        questions = [json.loads(line) for line in fh if line.strip()]

    engine = SearchEngine.from_build(args.chunks, args.params)
    results = {
        name: evaluate(engine, questions, k=args.k, config=name) for name in CONFIGS
    }
    report = render(results, args.k, args.verbose)
    print(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"Report written to {args.out}")


if __name__ == "__main__":
    main()
