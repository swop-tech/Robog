"""Retrieval evaluation: does the engine find the right passage, image, parameter, and profile card?

The 28 questions in ``eval/questions.jsonl`` cover lookup, figure/visual,
procedural, and advisory/comparison personas across EN, DE, and IT -- including
questions that *must* be answered from a manual in another language (Italian +
FlashBot/Pager, which have no Italian edition).

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
    profile_hit: bool | None = None
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

    @property
    def profile_rate(self) -> float:
        with_expectation = [r for r in self.results if r.profile_hit is not None]
        return _mean([1.0 if r.profile_hit else 0.0 for r in with_expectation]) if with_expectation else 0.0


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def _matches(hit_text: str, keywords: list[str]) -> bool:
    text = _norm(hit_text)
    return any(_norm(keyword) in text for keyword in keywords)


def _check_profiles(profiles: list, expect: dict) -> bool | None:
    """Evaluate structured product-profile expectations when present."""
    profile_keys = (
        "expect_profile_min",
        "expect_profile_top",
        "expect_profile_any_of",
        "expect_profile_all_of",
        "expect_profile_category",
    )
    if not any(k in expect for k in profile_keys):
        return None
    if not profiles:
        return False

    names = [p.product for p in profiles]
    if "expect_profile_min" in expect and len(set(names)) < int(expect["expect_profile_min"]):
        return False
    if "expect_profile_top" in expect and names[0] != expect["expect_profile_top"]:
        return False
    if "expect_profile_any_of" in expect:
        if not any(prod in names for prod in expect["expect_profile_any_of"]):
            return False
    if "expect_profile_all_of" in expect:
        if not all(prod in names for prod in expect["expect_profile_all_of"]):
            return False
    if "expect_profile_category" in expect:
        if not any(p.category == expect["expect_profile_category"] for p in profiles[:3]):
            return False
    return True


def evaluate(engine: SearchEngine, questions: list[dict], k: int = 8, config: str = "policy") -> ConfigResult:
    options = CONFIGS[config]
    result = ConfigResult(config)

    for question in questions:
        expect = question.get("expect", {})
        search = engine.search(question["question"], k=k, **options)
        hits = search.hits
        profiles = list(getattr(search, "profiles", ()) or ())

        # rank of the first hit that satisfies the expectation
        rank = None
        has_hit_criteria = any(
            key in expect for key in ("keywords", "chunk_type", "product", "expect_image")
        )
        for hit in hits:
            if expect.get("chunk_type") and hit.chunk_type != expect["chunk_type"]:
                continue
            if expect.get("expect_image") and not hit.image:
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
        if has_hit_criteria:
            passed = bool(rank)
        elif min_products:
            passed = products >= min_products
            rank = 1 if (passed and hits) else None
        else:
            passed = bool(hits or profiles)
            rank = 1 if (passed and (hits or profiles)) else None

        if expect.get("expect_route"):
            passed = passed and (search.route == expect["expect_route"])

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
                _matches(f"{p.parameter} {p.value}", param_keywords)
                and (not expect.get("product") or p.record.get("product") == expect["product"])
                for p in search.params
            )

        profile_hit = _check_profiles(profiles, expect)

        # Passage relevance, exact-parameter, profile-card, and cross-language
        # requirements are separate checks; a hit alone must not pass them.
        if param_hit is not None:
            passed = passed and param_hit
        if profile_hit is not None:
            passed = passed and profile_hit
        if "expect_cross_lingual" in expect:
            passed = passed and search.cross_lingual == expect["expect_cross_lingual"]

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
                profile_hit=profile_hit,
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
        "| Configuration | Pass@k | MRR | Hits in question language | Cross-language duplicates | Param index hit | Profile card hit |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, result in results.items():
        lines.append(
            f"| `{name}` | {result.recall:.0%} | {result.mrr:.2f} | "
            f"{result.same_language_share:.0%} | {result.duplicate_share:.0%} | "
            f"{result.param_rate:.0%} | {result.profile_rate:.0%} |"
        )
    lines += [
        "",
        "Pass rate checks the annotated keywords/product/type and, where specified, "
        "route, image, parameter, product-profile, and cross-language expectations.",
        "",
        "*Cross-language duplicates*: share of returned chunks that repeat a passage "
        "already returned in another language -- exactly the top-k dilution the "
        "language policy exists to prevent.",
        "",
        "## Per question (policy)",
        "",
        "| id | question | lang | route | first matching hit | same-lang | confidence | ok |",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the Robog retrieval engine.")
    parser.add_argument("--chunks", default="build/chunks.jsonl")
    parser.add_argument("--params", default="build/params.jsonl")
    parser.add_argument("--profiles", default="build/profiles.jsonl")
    parser.add_argument("--questions", default=str(QUESTIONS_PATH))
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--out", default=None, help="optional file to write the markdown report to")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--vector", action="store_true",
        help="include dense retrieval via build/chroma (off by default to keep runs offline)",
    )
    args = parser.parse_args(argv)

    engine = SearchEngine.from_build(
        args.chunks,
        args.params,
        profiles_path=args.profiles,
        vector="auto" if args.vector else None,
    )
    if args.vector and engine.retriever_note:
        print(f"Note: {engine.retriever_note}")
    with Path(args.questions).open(encoding="utf-8") as fh:
        questions = [json.loads(line) for line in fh if line.strip()]

    results = {name: evaluate(engine, questions, k=args.k, config=name) for name in CONFIGS}
    report = render(results, k=args.k, verbose=args.verbose)
    print(report, end="")
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
