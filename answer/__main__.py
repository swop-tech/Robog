"""Command line answering -- `python -m answer "Wieviel Zuladung hat der FlashBot?"`.

Shows the whole answering phase in a terminal: the answer in the question's
language, the citations the answer uses, the evidence validator's verdict, and
(optionally) the raw JSON that the future ``POST /ask`` will return.

The offline formatter is the default so the command always works::

    python -m answer "Qual è la velocità massima del FlashBot?"        # offline
    python -m answer "Zeig mir ein Bild vom FlashBot" --provider gemini
    python -m answer "Which robot fits my restaurant?" --json

If the answers read like pasted manual passages, the offline formatter is
writing them. ``--check`` makes one real call to the model and says why::

    python -m answer --check
"""

from __future__ import annotations

import argparse
import json
import sys

from retrieve.engine import SearchEngine
from retrieve.env import load_dotenv

from .composer import AnswerComposer
from .llm import DEFAULT_LLM_MODEL, check_llm


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Answer a question from the PUDU manuals, grounded and validated."
    )
    parser.add_argument("question", nargs="?", help="the question, in EN, DE or IT")
    parser.add_argument(
        "--check",
        action="store_true",
        help="test the LLM connection with one real call and explain any failure",
    )
    parser.add_argument("--k", default=5, type=int, help="chunks to retrieve (default: 5)")
    parser.add_argument("--language", default=None, help="force the question language (en|de|it)")
    parser.add_argument("--product", default=None, help="restrict retrieval to one product")
    parser.add_argument(
        "--provider",
        default=None,
        help="answer generator: gemini | offline (default: ROBOG_LLM_PROVIDER or auto)",
    )
    parser.add_argument(
        "--model", default=None, help=f"LLM model ID (default: {DEFAULT_LLM_MODEL})"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--show-evidence", action="store_true", help="print the numbered evidence")
    parser.add_argument("--chunks", default="build/chunks.jsonl")
    parser.add_argument("--params", default="build/params.jsonl")
    parser.add_argument("--profiles", default="build/profiles.jsonl")
    args = parser.parse_args(argv)

    if args.check:
        ok, lines = check_llm(args.model)
        print("\n".join(lines))
        return 0 if ok else 1
    if not args.question:
        parser.error("a question is required (or use --check)")

    engine = SearchEngine.from_build(args.chunks, args.params, profiles_path=args.profiles)
    composer = AnswerComposer(
        engine, provider=args.provider, model=args.model, k=args.k
    )
    answer = composer.answer(args.question, product=args.product, language=args.language)

    if args.json:
        print(json.dumps(answer.to_dict(), ensure_ascii=False, indent=2))
        return 0 if answer.validation and answer.validation.ok else 1

    print()
    print(f"Q: {answer.question}")
    print(
        f"Language: {answer.language}  |  Route: {answer.route}  |  "
        f"Provider: {answer.provider}{f' ({answer.model})' if answer.model else ''}"
    )
    print()
    print(answer.text)
    if answer.citations:
        print("\nCitations:")
        for citation in answer.citations:
            print(f"  [{citation['n']}] {citation['citation']}")
    if answer.figures:
        print("\nFigures:")
        for figure in answer.figures:
            print(f"  • {figure['product']}: {figure['image']}  ({figure['caption']})")
    report = answer.validation
    if report is not None:
        print("\nEvidence validation (G5):")
        print(
            f"  citations {report.sentences_cited}/{report.sentences_checked} sentences"
            f"  |  numbers {report.numbers_verified}/{report.numbers_checked} verified"
            f"  |  quotes {report.quotes_verified}/{report.quotes_checked} verbatim"
        )
        if report.issues:
            for issue in report.issues:
                print(f"  - {issue.severity.upper()} {issue.check}: {issue.message} [{issue.action}]")
        elif not report.checks_run:
            # 0/0 passes every check vacuously -- saying "all checks passed" here
            # would certify an answer that was never grounded in anything.
            print("  nothing to verify: no passage of the manuals answered this")
        else:
            print("  all checks passed")
    if answer.fallback_reason:
        print(f"\nWritten by the offline formatter because: {answer.fallback_reason}")
        print("  (it can only quote the manuals; run `python -m answer --check`)")
    if answer.notes:
        print("\nNotes:")
        for note in answer.notes:
            print(f"  - {note}")
    if args.show_evidence and answer.evidence:
        print("\nEvidence:")
        for ref in answer.evidence.refs:
            print(f"  [{ref.n}] ({ref.kind}, {ref.language or '?'}) {ref.citation}")
            print(f"      {ref.text[:200].replace(chr(10), ' ')}")
    print()
    return 0 if answer.grounded else 1


if __name__ == "__main__":
    sys.exit(main())
