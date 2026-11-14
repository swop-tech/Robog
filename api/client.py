"""A ~40-line client: proof that the engine is a service, not a script (Bonus 01).

The web chat is only the first client. This one uses nothing but the standard
library, so it runs anywhere Python does -- including a Telegram bot handler,
where ``main()`` is what you would call per message::

    python -m api.client "Wieviel Zuladung hat der FlashBot?"
    python -m api.client --search "Zeig mir ein Bild vom BellaBot"
    python -m api.client --products --language it --product FlashBot --parameter payload
    python -m api.client --base http://localhost:8080 --json "Which robot fits my restaurant?"

The interesting part is ``ask()``: one POST, one JSON body, and the answer comes
back with its citations, its figures (already as fetchable URLs) and the
validator's verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def _get(base: str, path: str, params: dict | None = None, timeout: int = 60) -> dict:
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = f"{base.rstrip('/')}{path}" + (f"?{query}" if query else "")
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def ask(base: str, question: str, **options) -> dict:
    """POST /ask -- the whole RAG pipeline in one call."""
    body = json.dumps({"question": question, **options}).encode("utf-8")
    request = urllib.request.Request(
        f"{base.rstrip('/')}/ask",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def show_answer(payload: dict) -> None:
    print(f"\n[{payload['language']} · {payload['route']} · {payload['provider']}]")
    print(payload["answer"])
    for citation in payload.get("citations", []):
        print(f"  [{citation['n']}] {citation['citation']}")
    for figure in payload.get("figures", []):
        print(f"  figure: {figure.get('image_url') or figure.get('image')}")
    check = payload.get("verification", {})
    if check:
        print(f"  verified: {check.get('summary')}")


def show_search(payload: dict) -> None:
    print(f"\n{payload['query']}  [{payload['language']} · {payload['route']} · {payload['confidence']}]")
    for param in payload.get("params", []):
        print(f"  param: {param['parameter']} = {param['value']}  ({param['citation']})")
    for profile in payload.get("profiles", []):
        print(f"  card:  {profile['product']} [{profile.get('category')}] — {profile.get('key_use_case', '')[:120]}")
    for rank, hit in enumerate(payload.get("hits", []), start=1):
        print(f"  {rank}. [{hit['score']:.4f}] {hit['chunk_type']} {hit['citation']}")
        if hit.get("image_url"):
            print(f"     image: {hit['image_url']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Talk to a running Robog API.")
    parser.add_argument("question", nargs="?", help="the question (EN, DE or IT)")
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="API origin")
    parser.add_argument("--search", action="store_true", help="GET /search instead of POST /ask")
    parser.add_argument("--products", action="store_true", help="GET /products instead")
    parser.add_argument("--health", action="store_true", help="GET /health")
    parser.add_argument("--k", type=int, default=None)
    parser.add_argument("--language", default=None, choices=["en", "de", "it"])
    parser.add_argument("--product", default=None)
    parser.add_argument("--parameter", default=None)
    parser.add_argument("--provider", default=None, help="gemini | offline (per request)")
    parser.add_argument("--json", action="store_true", help="print the raw JSON response")
    args = parser.parse_args(argv)

    try:
        if args.health:
            payload = _get(args.base, "/health")
        elif args.products:
            payload = _get(
                args.base,
                "/products",
                {"language": args.language, "product": args.product, "parameter": args.parameter},
            )
        elif not args.question:
            parser.error("a question is required (or use --products / --health)")
        elif args.search:
            payload = _get(
                args.base,
                "/search",
                {"q": args.question, "k": args.k, "language": args.language, "product": args.product},
            )
        else:
            payload = ask(
                args.base,
                args.question,
                k=args.k,
                language=args.language,
                product=args.product,
                provider=args.provider,
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        print(f"HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"cannot reach {args.base}: {exc.reason} -- is 'python -m api' running?", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.search:
        show_search(payload)
    elif args.health or args.products:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        show_answer(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
