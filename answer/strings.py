"""Localized scaffolding for the answer composer (EN / DE / IT).

Bonus 02 says the answer is always written in the language of the question,
even when every retrieved passage is in another language. That means the
*deterministic* parts of an answer -- the "passages from the manuals" heading,
the excerpt label, the note that the evidence is in EN/DE, the "I could not
find this" message -- also need translations. Keeping them in one table makes
the offline formatter readable and the wording reviewable.

Nothing here is generated: these are the only sentences Robog itself writes.
Everything factual is quoted or paraphrased from the manuals (see
``answer/formatter.py``).
"""

from __future__ import annotations

LANGUAGES = ("en", "de", "it")

#: Name of a language as shown in the UI / a note, in its own language.
LANGUAGE_LABELS = {"en": "English", "de": "Deutsch", "it": "Italiano"}

#: Language names as written *inside* an answer in a given language.
LANGUAGE_NAMES: dict[str, dict[str, str]] = {
    "en": {"en": "English", "de": "German", "it": "Italian"},
    "de": {"en": "Englisch", "de": "Deutsch", "it": "Italienisch"},
    "it": {"en": "inglese", "de": "tedesco", "it": "italiano"},
}

_JOINERS = {"en": " and ", "de": " und ", "it": " e "}


def language_name(code: str, *, in_language: str = "en") -> str:
    """``("de", in_language="it")`` -> ``"tedesco"``."""
    names = LANGUAGE_NAMES.get(in_language) or LANGUAGE_NAMES["en"]
    return names.get(code) or LANGUAGE_LABELS.get(code, code.upper())


def join_languages(codes, *, in_language: str = "en") -> str:
    """``["en", "de"]`` -> ``"English and German"`` (localized joiner)."""
    names = [language_name(code, in_language=in_language) for code in codes if code]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    joiner = _JOINERS.get(in_language, _JOINERS["en"])
    return joiner.join([", ".join(names[:-1]), names[-1]])


STRINGS: dict[str, dict[str, str]] = {
    # -- offline (deterministic) formatter ---------------------------------
    "no_evidence": {
        "en": (
            "I could not find this in the available PUDU manuals, so I will not guess. "
            "Try naming the robot (for example BellaBot, FlashBot, T300) or asking for a "
            "parameter such as payload, battery or speed."
        ),
        "de": (
            "Dazu habe ich in den verfügbaren PUDU-Handbüchern nichts gefunden – ich rate nicht. "
            "Nennen Sie am besten den Roboter (z. B. BellaBot, FlashBot, T300) oder einen "
            "Parameter wie Zuladung, Akku oder Geschwindigkeit."
        ),
        "it": (
            "Non ho trovato questa informazione nei manuali PUDU disponibili, quindi non "
            "invento nulla. Prova a indicare il robot (per esempio BellaBot, FlashBot, T300) "
            "o un parametro come carico, batteria o velocità."
        ),
    },
    "param_lead": {
        "en": "According to the manual, **{parameter}** of {product} is **{value}** [{ref}].",
        "de": "Laut Handbuch beträgt **{parameter}** beim {product} **{value}** [{ref}].",
        "it": "Secondo il manuale, **{parameter}** del {product} è **{value}** [{ref}].",
    },
    "param_lead_no_product": {
        "en": "According to the manual, **{parameter}** is **{value}** [{ref}].",
        "de": "Laut Handbuch beträgt **{parameter}** **{value}** [{ref}].",
        "it": "Secondo il manuale, **{parameter}** è **{value}** [{ref}].",
    },
    "excerpts_header": {
        "en": "**Passages from the manuals**",
        "de": "**Belegstellen aus den Handbüchern**",
        "it": "**Passaggi dai manuali**",
    },
    "excerpt_label": {
        "en": "Excerpt",
        "de": "Auszug",
        "it": "Estratto",
    },
    "excerpt": {
        "en": "- “{snippet}” [{ref}]",
        "de": "- „{snippet}“ [{ref}]",
        "it": "- «{snippet}» [{ref}]",
    },
    "profiles_header": {
        "en": "**Matching robots**",
        "de": "**Passende Roboter**",
        "it": "**Robot adatti**",
    },
    "profile_line": {
        "en": "- **{product}** ({category}): {specs} — {summary} [{ref}]",
        "de": "- **{product}** ({category}): {specs} — {summary} [{ref}]",
        "it": "- **{product}** ({category}): {specs} — {summary} [{ref}]",
    },
    "figure_note": {
        "en": "- **Figure** ({title}) — source: {citation} [{ref}]",
        "de": "- **Abbildung** ({title}) — Quelle: {citation} [{ref}]",
        "it": "- **Figura** ({title}) — fonte: {citation} [{ref}]",
    },
    "figure_labels": {
        "en": "Labels: {labels} [{ref}]",
        "de": "Beschriftungen: {labels} [{ref}]",
        "it": "Etichette: {labels} [{ref}]",
    },
    "page_label": {
        "en": "p. {page}",
        "de": "S. {page}",
        "it": "p. {page}",
    },
    # -- language transparency (Bonus 02) ----------------------------------
    "cross_lingual_note": {
        "en": "Note: the evidence is in {languages}; the wording above is quoted from those manuals. [{refs}]",
        "de": "Hinweis: Die Belege sind in {languages}; die Zitate oben stammen aus diesen Handbüchern. [{refs}]",
        "it": "Nota: le fonti sono in {languages}; le citazioni sopra sono tratte da quei manuali. [{refs}]",
    },
    "no_own_language_note": {
        "en": (
            "There is no {lang_name} manual for {product}, so this answer is composed from "
            "{languages} sources. [{refs}]"
        ),
        "de": (
            "Für {product} gibt es kein Handbuch auf {lang_name}; die Antwort stützt sich auf "
            "{languages} Quellen. [{refs}]"
        ),
        "it": (
            "Non esiste un manuale in {lang_name} per {product}: la risposta si basa su fonti in "
            "{languages}. [{refs}]"
        ),
    },
    "excerpt_plain": {
        "en": "- {snippet} [{ref}]",
        "de": "- {snippet} [{ref}]",
        "it": "- {snippet} [{ref}]",
    },
    "validation_summary": {
        "en": "{cited}/{sentences} claims cited · {verified}/{numbers} numbers verified",
        "de": "{cited}/{sentences} Aussagen belegt · {verified}/{numbers} Zahlen geprüft",
        "it": "{cited}/{sentences} affermazioni citate · {verified}/{numbers} numeri verificati",
    },
    "validation_no_checks": {
        "en": "Nothing to verify — no passage of the manuals answered this question.",
        "de": "Nichts zu prüfen — keine Passage der Handbücher hat diese Frage beantwortet.",
        "it": "Nulla da verificare — nessun passaggio dei manuali ha risposto alla domanda.",
    },
    "needs_robot_and_parameter": {
        "en": (
            "Which robot and which parameter do you mean? The manuals cover 13 robots and "
            "9 accessories, so name one (for example FlashBot, BellaBot, SH1, T300) and the "
            "value you need (payload, battery, speed, tank capacity, dimensions) and I will "
            "quote it with its page."
        ),
        "de": (
            "Welchen Roboter und welchen Parameter meinen Sie? Die Handbücher umfassen 13 "
            "Roboter und 9 Zubehörteile. Nennen Sie eines (zum Beispiel FlashBot, BellaBot, "
            "SH1, T300) und den gesuchten Wert (Zuladung, Akku, Geschwindigkeit, "
            "Tankinhalt, Abmessungen), dann zitiere ich ihn mit Seite."
        ),
        "it": (
            "Quale robot e quale parametro intendi? I manuali coprono 13 robot e 9 accessori: "
            "indicane uno (ad esempio FlashBot, BellaBot, SH1, T300) e il valore che ti serve "
            "(carico, batteria, velocità, capacità del serbatoio, dimensioni) e lo citerò con la pagina."
        ),
    },
    "provider_offline_note": {
        "en": "Answer composed by the offline formatter (no LLM call).",
        "de": "Antwort vom Offline-Formatierer erstellt (kein LLM-Aufruf).",
        "it": "Risposta composta dal formatter offline (nessuna chiamata LLM).",
    },
    "provider_fallback_note": {
        "en": "The LLM was unavailable, so the answer was composed by the offline formatter.",
        "de": "Das LLM war nicht erreichbar, deshalb wurde die Antwort offline zusammengestellt.",
        "it": "L'LLM non era disponibile, quindi la risposta è stata composta offline.",
    },
}


def text(key: str, lang: str | None, **kwargs) -> str:
    """Look up a localized template and fill it in (falls back to English)."""
    table = STRINGS.get(key, {})
    template = table.get(lang or "en") or table.get("en") or ""
    return template.format(**kwargs) if kwargs else template
