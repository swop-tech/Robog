"""Suggestions drawn from the data, for a question that names no robot or parameter.

"What is the value of this parameter?" should not end in "tell me which robot".
The parameter index already says which parameters the manuals document, for which
products, so the answer offers those -- and an example question that the system
can answer right away.
"""

from __future__ import annotations

from collections import defaultdict

from .strings import text

#: How many parameter names and robots to show.
TOP_PARAMETERS = 8
TOP_PRODUCTS = 12


def _ranked_parameters(records: list[dict], limit: int) -> list[tuple[str, int]]:
    """English parameter names, most products first (ties by name)."""
    products: dict[str, set[str]] = defaultdict(set)
    for record in records:
        if record.get("language") != "en":
            continue
        name = (record.get("parameter") or "").strip()
        if name:
            products[name].add(record.get("product", ""))
    ranked = sorted(products.items(), key=lambda item: (-len(item[1]), item[0]))
    return [(name, len(prods)) for name, prods in ranked[:limit]]


def _products(engine, limit: int) -> list[str]:
    """Robots first (they have profile cards), then accessories."""
    robots = sorted({p.get("product", "") for p in engine.profiles.records if p.get("product")})
    everything = sorted({r.get("product", "") for r in engine.params.records if r.get("product")})
    accessories = [p for p in everything if p not in robots]
    return (robots + accessories)[:limit]


def suggestion_text(engine, language: str) -> str:
    """Localized suggestion message built only from the parameter and profile indexes."""
    parameters = _ranked_parameters(engine.params.records, TOP_PARAMETERS)
    products = _products(engine, TOP_PRODUCTS)
    lines = [text("suggest_intro", language)]
    if parameters:
        names = ", ".join(name for name, _ in parameters)
        lines.append(text("suggest_parameters", language, names=names))
    if products:
        lines.append(text("suggest_products", language, names=", ".join(products)))
    if parameters and products:
        example_param = parameters[0][0]
        example_product = products[0]
        lines.append(
            text(
                "suggest_examples",
                language,
                question_a=f"What is the {example_param.lower()} of {example_product}?",
                question_b=f"What is the {parameters[min(1, len(parameters) - 1)][0].lower()}?",
            )
        )
    return "\n\n".join(lines)
