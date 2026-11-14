"""Command line search -- the retrieval engine as a standalone service (Bonus 01).

    python -m retrieve "Wieviel Zuladung hat der FlashBot?"
    python -m retrieve "Come si pulisce il filtro dell'SH1?" --k 3 --json

Prints ranked chunks with their citation (manual, §section, page, language),
the parameter rows for lookup questions, and the retrieval notes (language
policy, cross-lingual source, confidence). Any frontend can shell out to this
or call the same ``SearchEngine`` -- the chat UI is just the first client.
"""

from __future__ import annotations

import argparse
import json
import sys

from .engine import SearchEngine
from .vector import ChromaRetriever, VectorStoreUnavailable


def main() -> None:
    parser = argparse.ArgumentParser(description="Search the PUDU manuals.")
    parser.add_argument("query", help="the question, in EN, DE or IT")
    parser.add_argument("--k", default=5, type=int, help="number of chunks (default: 5)")
    parser.add_argument("--language", default=None, help="force the question language (en|de|it)")
    parser.add_argument("--product", default=None, help="restrict to one product")
    parser.add_argument("--no-language-policy", action="store_true",
                        help="search all languages at once (baseline)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--chunks", default="build/chunks.jsonl")
    parser.add_argument("--params", default="build/params.jsonl")
    parser.add_argument("--vector", action="store_true",
                        help="fuse dense results from Chroma (needs 'python -m ingest.embed_index' to have run; "
                             "falls back to keyword-only with a warning)")
    parser.add_argument("--chroma", default=None,
                        help="path to the persisted Chroma DB "
                             "(default: ROBOG_CHROMA_PATH or build/chroma)")
    parser.add_argument("--collection", default=None,
                        help="Chroma collection name (default: ROBOG_CHROMA_COLLECTION or robog)")
    parser.add_argument("--provider", default=None, choices=["local", "gemini"],
                        help="query embedding provider (default: ROBOG_EMBED_PROVIDER or local)")
    args = parser.parse_args()

    retriever = None
    if args.vector:
        try:
            retriever = ChromaRetriever.connect(args.chroma, args.collection, args.provider)
        except VectorStoreUnavailable as exc:
            # Demo-safe: keyword retrieval still works, the warning says why.
            print(f"warning: dense retrieval disabled: {exc}", file=sys.stderr)
    engine = SearchEngine.from_build(args.chunks, args.params, vector=retriever)
    result = engine.search(
        args.query,
        k=args.k,
        language=args.language,
        product=args.product,
        follow_user_language=not args.no_language_policy,
    )

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return

    print(f"\nQuery: {result.query}")
    print(f"Language: {result.language or 'auto'}  |  Route: {result.route} ({result.route_reason})")
    print(f"Policy: {result.policy}   |  Confidence: {result.confidence}")
    if result.notes:
        for note in result.notes:
            print(f"Note: {note}")

    if result.params:
        print("\nParameter index:")
        for param in result.params:
            print(f"  • {param.parameter} = {param.value}")
            print(f"    {param.citation}")

    print("\nChunks:")
    for hit in result.hits:
        kind = hit.chunk_type + (f" ({hit.image})" if hit.image else "")
        sources = "+".join(hit.sources) if hit.sources else "-"
        print(f"  {hit.rank}. [{hit.score:.4f}] {kind}  [{sources}]")
        print(f"     {hit.text[:300].replace(chr(10), ' ')}")
        print(f"     ↳ {hit.citation}")


if __name__ == "__main__":
    main()
