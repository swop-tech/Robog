"""Step 4 of data processing: deterministic **product profile cards** (gap G2).

Advisory questions ("Which robot fits my restaurant?", "Welcher Roboter eignet
sich für ein Hotel?", "Quale robot è adatto per pulire un supermercato?")
cannot be answered reliably by guessing from scattered chunks: safety bullets
and wheel-cleaning tables outrank actual product overviews, and no single chunk
holds both positioning prose and the core specification table.

This module deterministically extracts one compact, citation-backed structured
card per ``(product, language)`` from ``chunks.jsonl`` + ``params.jsonl``:

* ``category``: ``"delivery"`` | ``"industrial_delivery"`` | ``"cleaning"``
* ``dimensions``, ``weight``, ``payload``, ``trays_or_compartments``
* ``battery``, ``runtime``, ``battery_runtime``, ``charging_time``, ``speed``
* ``min_passage_width``, ``navigation``
* ``cleaning_efficiency``, ``tank_or_dustbin`` (for cleaning robots)
* ``special_features``, ``environments``, ``key_use_case``
* ``image`` (representative appearance figure in that language)
* ``chunk_ids``, ``pages``, ``citation`` (every value stays traceable)

No LLM is called at build time (zero baked-in hallucination risk, 100% offline,
stdlib only). Every number on a card comes verbatim from the manual's own
specification rows or overview text.

CLI:
    python -m ingest.profiles --chunks build/chunks.jsonl \\
        --params build/params.jsonl --out build/profiles.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

_SPECIAL_FOLDS = str.maketrans({"ß": "ss", "ø": "o", "ł": "l", "æ": "ae", "œ": "oe"})


def _fold(text: str) -> str:
    text = (text or "").translate(_SPECIAL_FOLDS)
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.lower()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _fold(name)).strip("_")


# --------------------------------------------------------------------------
# Filtering noise out of table rows and early prose chunks
# --------------------------------------------------------------------------
_SPEC_SECTION_CUES = (
    "spec", "technische daten", "produktspezifikation", "specifiche",
    "scheda tecnica", "dati tecnici",
)

_MAINTENANCE_VALUE_CUES = (
    "power-off", "powered off", "weekly", "one week", "monthly", "one month",
    "six months", "half a month", "wipe the surface", "clean cloth",
    "ausschalten", "ausgeschaltet", "wochentlich", "einmal pro woche",
    "monatlich", "halbjahrlich", "ein halber monat", "sauberen tuch",
    "spegnimento", "spento", "settimanalmente", "ogni settimana",
    "una settimana", "meta mese", "panno pulito", "2-3 days", "2-3 tage", "2-3 giorni",
)

_LED_NOISE_CHARS = ("🔴", "🔵", "🟢", "⚪", "●", "○")

_COPYRIGHT_CUES = (
    "copyright", "all rights reserved", "alle rechte vorbehalten",
    "tutti i diritti riservati", "imitate, copy, transcribe",
    "kopieren, reproduzieren", "imitare, copiare, trascrivere",
    "waste electrical and electronic", "federal communications commission",
    "safety instructions", "sicherheitsanweisungen", "istruzioni di sicurezza",
)

_OVERVIEW_TITLE_CUES = (
    "overview", "introduction", "product introduction", "product overview",
    "uberblick", "einfuhrung", "ubersicht", "produktvorstellung",
    "panoramica", "introduzione",
)

_APPEARANCE_SECTION_CUES = (
    "appearance", "exterior", "overview", "components",
    "aussehen", "erscheinungsbild", "aussenansicht", "uberblick",
    "bestandteile", "komponenten", "gerat kennenlernen",
    "aspetto", "panoramica", "componenti",
)

_TIME_UNIT_RE = re.compile(
    r"\b(?:\d+(?:[.,]\d+)?)\s*(?:h|hr|hrs|hours?|std\.?|stunden?|ore|ora|min|mins|min\.|minuti)\b"
    r"|\d+\s*h\b",
    re.IGNORECASE,
)
_SERVICE_LIFE_RE = re.compile(r"\b(?:years?|jahre?|anni|anno)\b", re.IGNORECASE)
_BATTERY_UNIT_RE = re.compile(r"\b(?:ah|mah|wh|kwh)\b", re.IGNORECASE)
_AREA_RATE_RE = re.compile(r"(?:㎡|m²|m\s*2)\s*/\s*(?:h|std|ora)", re.IGNORECASE)
_LOAD_KG_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*kg\b", re.IGNORECASE)
_PACKING_LIST_RE = re.compile(
    r"(?:×\s*\d+|\bx\s*[12]\b|\b[12]\s*[x×]\b)",
    re.IGNORECASE,
)


def appearance_figure_score(chunk: dict) -> float:
    """Score a ``chunk_type == 'figure'`` chunk for how well it serves as a
    product's primary overview / component diagram.

    Shared concept with the ``figure`` route in ``retrieve/engine.py``:
    Section 2 appearance/overview diagrams with printed ``Labels:`` callouts
    rank ahead of safety clearance drawings, button close-ups, and WEEE icons.
    """
    meta = chunk.get("metadata", {})
    if meta.get("chunk_type") != "figure" or not meta.get("image"):
        return -1.0

    text = chunk.get("text", "")
    folded_text = _fold(text)
    sec_num = str(meta.get("section_number") or "")
    sec_title = _fold(f"{meta.get('section_title', '')} {meta.get('section', '')}")

    score = 1.0
    if sec_num.startswith("2"):
        score += 4.0
    if any(cue in sec_title for cue in _APPEARANCE_SECTION_CUES):
        score += 5.0
    if "Labels:" in text:
        label_part = text.split("Labels:", 1)[1]
        n_labels = len([item for item in label_part.split(";") if item.strip()])
        score += 3.0 + min(n_labels, 15) * 0.15
    if any(w in folded_text for w in ("labeled", "components", "parts", "front", "exterior", "overview")):
        score += 1.5

    # Penalise compliance icons and environmental clearance diagrams.
    if "weee" in folded_text or sec_num.startswith(("4", "5", "6", "7", "8")):
        score -= 4.0
    if sec_num.startswith("1"):
        score -= 2.0

    # Slight preference for the first figure in the section (fig01 = full view).
    m = re.search(r"-(\d{4})$", chunk.get("id", ""))
    if m:
        score += max(0.0, 0.5 - int(m.group(1)) * 0.01)
    return round(score, 4)


def _is_spec_row(row: dict) -> bool:
    """Keep specification / packing rows, drop maintenance & LED status tables."""
    val = str(row.get("value") or "").strip()
    param = str(row.get("parameter") or "").strip()
    if not val or not param:
        return False
    if any(ch in val or ch in param for ch in _LED_NOISE_CHARS):
        return False
    folded_val = _fold(val)
    if any(cue in folded_val for cue in _MAINTENANCE_VALUE_CUES):
        return False
    # Numbered interface pinout rows ("1 = Power supply interface...")
    if param.isdigit():
        return False
    sec_num = str(row.get("section_number") or "")
    sec_title = _fold(f"{row.get('section', '')}")
    if sec_num.startswith("2") or any(cue in sec_title for cue in _SPEC_SECTION_CUES):
        return True
    return False


def _normalize_spec_rows(rows: list[dict]) -> list[dict]:
    """Repair PDF-table line wraps without altering any verbatim values."""
    normalized: list[dict] = []
    for row in rows:
        item = dict(row)
        p_fold = _fold(item.get("parameter", ""))
        v_raw = str(item.get("value", "")).strip()
        v_fold = _fold(v_raw)

        # Wrapped continuation in SH1 IT:
        # "Durata di funzionamento della = Modalità normale: 70 minuti"
        # followed by "batteria = Modalità ECO: 100 minuti"
        if (
            p_fold in ("batteria", "battery")
            and not _BATTERY_UNIT_RE.search(v_raw)
            and _TIME_UNIT_RE.search(v_raw)
            and normalized
            and any(k in _fold(normalized[-1]["parameter"]) for k in ("durata di funzionamento", "operation time"))
        ):
            normalized[-1] = dict(
                normalized[-1],
                parameter=f"{normalized[-1]['parameter']} {item['parameter']}".strip(),
                value=f"{normalized[-1]['value']}; {v_raw}",
            )
            continue

        # Wrapped continuation in CC1 Pro IT:
        # "(strofinatura) = Serbatoio di recupero: 15 litri (3,96 galloni)"
        if p_fold.startswith("(") and ("serbatoio" in v_fold or "tank" in v_fold) and normalized:
            normalized[-1] = dict(
                normalized[-1],
                value=f"{normalized[-1]['value']}; {v_raw}",
            )
            continue

        normalized.append(item)
    return normalized


def _clean_value(value: str) -> str:
    """Clean HTML entity remnants while keeping numbers/units untouched."""
    cleaned = value.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def _dedupe_repeated_columns(value: str) -> str:
    """``'3 kg, 3 kg, 3 kg'`` -> ``'3 kg'`` (multi-column identical specs)."""
    parts = [p.strip() for p in value.split(",") if p.strip()]
    if len(parts) >= 2 and len(set(parts)) == 1:
        return parts[0]
    return value


def _match_rows(
    rows: list[dict],
    *,
    any_phrases: tuple[str, ...] = (),
    exclude_phrases: tuple[str, ...] = (),
    value_pred=None,
) -> list[dict]:
    matched: list[dict] = []
    for row in rows:
        p = _fold(row.get("parameter", ""))
        v = str(row.get("value", ""))
        if exclude_phrases and any(ex in p for ex in exclude_phrases):
            continue
        if any_phrases and not any(ph in p for ph in any_phrases):
            continue
        if value_pred is not None and not value_pred(v, row):
            continue
        matched.append(row)
    return matched


def _join_unique_values(rows: list[dict], *, limit: int = 3) -> str | None:
    if not rows:
        return None
    seen: list[str] = []
    for row in rows:
        val = _dedupe_repeated_columns(_clean_value(str(row.get("value", ""))))
        if val and val not in seen:
            seen.append(val)
        if len(seen) >= limit:
            break
    return "; ".join(seen) if seen else None


# --------------------------------------------------------------------------
# Prose overview & mode extraction
# --------------------------------------------------------------------------
def _is_overview_candidate(para: str, product: str) -> bool:
    s = para.strip()
    if len(s) < 45:
        return False
    if s.startswith(("-", "*", "|", "(")):
        return False
    folded = _fold(s)
    if any(cue in folded for cue in _COPYRIGHT_CUES):
        return False
    if " - " in s and ("safety" in folded or "garanzia" in folded or "warranty" in folded or "garantie" in folded):
        return False
    if len(_PACKING_LIST_RE.findall(s)) >= 2 or (_PACKING_LIST_RE.search(s) and len(s) < 260):
        return False
    prod_tokens = [t for t in re.findall(r"[a-z0-9]+", _fold(product)) if t not in {"pudu", "pro"}]
    if any(tok in folded for tok in prod_tokens):
        return True
    return any(
        w in folded
        for w in (
            "delivery robot", "cleaning robot", "sweeping robot", "scrub",
            "servierroboter", "lieferroboter", "reinigungsroboter", "kehrroboter", "wisch-",
            "robot per", "robot di consegna", "lavasciuga", "beeper gateway", "call button",
        )
    )


def _extract_overview(text_chunks: list[dict], product: str) -> tuple[str | None, dict | None]:
    """Find the manual's own product-overview prose paragraph (and its chunk)."""
    # Pass 1: chunk whose section heading explicitly names Overview / Introduction.
    overview_paras: list[str] = []
    overview_chunk: dict | None = None
    for chunk in text_chunks:
        meta = chunk.get("metadata", {})
        sec = _fold(f"{meta.get('section_title', '')} {meta.get('section', '')}")
        sec_num = str(meta.get("section_number") or "")
        if any(cue in sec for cue in _OVERVIEW_TITLE_CUES) and not any(
            skip in sec for skip in ("packing", "packaging", "shipping", "packliste", "elenco", "mode", "modus", "modalita")
        ):
            for para in chunk.get("text", "").split("\n\n"):
                if _is_overview_candidate(para, product):
                    overview_paras.append(re.sub(r"\s+", " ", para.strip()))
                    overview_chunk = overview_chunk or chunk
            if overview_paras:
                return " ".join(overview_paras[:2]), overview_chunk

        # Handle unnumbered section "2 Product introduction" if section_number=="2"
        if sec_num == "2" and chunk.get("metadata", {}).get("chunk_type") == "text":
            for para in chunk.get("text", "").split("\n\n"):
                if _is_overview_candidate(para, product):
                    return re.sub(r"\s+", " ", para.strip()), chunk

    # Pass 2: in several manuals (KettyBot, CC1, MT1, SH1, T300), the short
    # 2.1 Overview paragraph sits at the end of an early chunk right after 1.3
    # safety bullets. Inspect \n\n-separated paragraphs of the first 5 chunks.
    for chunk in text_chunks[:6]:
        for para in chunk.get("text", "").split("\n\n"):
            if _is_overview_candidate(para, product):
                return re.sub(r"\s+", " ", para.strip()), chunk

    return None, None


def _extract_modes(chunks: list[dict]) -> tuple[list[str], list[dict]]:
    """Extract named operating modes from section 3 chunks/tables."""
    modes: list[str] = []
    used_chunks: list[dict] = []
    mode_heading_re = re.compile(
        r"^(?:[\wÀ-ÿ/-]+\s+){0,3}(?:mode|modus|modalit[àa])\s*\*?\s*:?$",
        re.IGNORECASE,
    )
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        sec = str(meta.get("section") or "")
        sec_title = str(meta.get("section_title") or "").strip().rstrip(":：").strip()
        if not (sec.startswith("3") or "mode" in _fold(sec) or "modus" in _fold(sec) or "modalita" in _fold(sec)):
            continue
        if mode_heading_re.match(sec_title) and _fold(sec_title) not in {
            "mode introduction", "cleaning mode", "reinigungsmodus",
            "introduzione alle modalita", "modalita di pulizia",
        }:
            clean_mode = sec_title.rstrip("*").strip()
            if clean_mode not in modes:
                modes.append(clean_mode)
                used_chunks.append(chunk)
        elif meta.get("chunk_type") == "table":
            for line in chunk.get("text", "").splitlines()[2:]:
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if cells and mode_heading_re.match(cells[0]):
                    clean_mode = cells[0].rstrip("*").strip()
                    if clean_mode not in modes:
                        modes.append(clean_mode)
                        used_chunks.append(chunk)
    return modes[:6], used_chunks


# --------------------------------------------------------------------------
# Category, features, and scenario tags (100% deterministic from manual data)
# --------------------------------------------------------------------------
def _infer_category(
    doc_type: str,
    overview: str | None,
    cleaning_efficiency: str | None,
    tank_or_dustbin: str | None,
    payload: str | None,
    spec_rows: list[dict],
) -> str:
    if doc_type != "robot":
        return "accessory"

    folded_overview = _fold(overview or "")
    has_cleaning_spec = bool(cleaning_efficiency or tank_or_dustbin) or any(
        any(
            k in _fold(r.get("parameter", ""))
            for k in (
                "cleaning capabilit", "cleaning width", "working width", "squeegee", "brush speed",
                "reinigungsfahigkeit", "reinigungsbreite", "arbeitsbreite", "abstreifer",
                "capacita di pulizia", "larghezza di pulizia", "pista di lavaggio", "tergipavimenti",
            )
        )
        for r in spec_rows
    )
    if has_cleaning_spec or any(
        w in folded_overview
        for w in ("cleaning robot", "sweeping robot", "scrub", "reinigungsroboter", "kehrroboter", "wisch-", "robot per pulizie", "lavasciuga", "spazzamento")
    ):
        return "cleaning"

    max_kg = 0.0
    if payload:
        for m in _LOAD_KG_RE.finditer(payload):
            try:
                max_kg = max(max_kg, float(m.group(1).replace(",", ".")))
            except ValueError:
                pass
    if max_kg >= 100.0 or any(
        w in folded_overview
        for w in ("industrial", "material transfer", "industrieszenarien", "materialtransport", "industriali", "trasferimento di materiali")
    ):
        return "industrial_delivery"

    return "delivery"


def _extract_special_features(
    spec_rows: list[dict],
    modes: list[str],
    overview: str | None,
) -> list[str]:
    features: list[str] = []

    def add(item: str) -> None:
        item = _clean_value(item)
        if item and item not in features:
            features.append(item)

    for row in spec_rows:
        p = _fold(row.get("parameter", ""))
        v = _clean_value(str(row.get("value", "")))
        v_fold = _fold(v)

        # Dual/advertising screen (BellaBot Pro, KettyBot, KettyBot Pro)
        if any(k in p for k in ("screen", "bildschirm", "display", "schermo")) and (
            "18.5" in v or "18,5" in v or "advertising" in v_fold or "werbe" in v_fold or "pubblicit" in v_fold
        ):
            add(f"{row['parameter']}: {v}")
        # Optional elevator / phone systems (FlashBot)
        elif any(k in p for k in ("elevator", "aufzug", "ascensore", "phone system", "telefonanlage")):
            add(f"{row['parameter']}: {v}")
        # Automatic door opening/closing (SwiftBot)
        elif any(k in p for k in ("automatic door", "automatisches offnen", "apertura e chiusura automatica")):
            add(f"{row['parameter']}: {v}")
        # Auto water refilling/draining (CC1, CC1 Pro)
        elif any(k in p for k in ("auto adding", "automatisches nachfullen", "svuotamento dell'acqua")):
            add(f"{row['parameter']}: {v}")
        # Cleaning capabilities (CC1, CC1 Pro, MT1)
        elif any(k in p for k in ("cleaning capabilit", "reinigungsfahigkeit", "capacita di pulizia")):
            add(f"{row['parameter']}: {v}")
        # Intelligent functions (SH1)
        elif any(k in p for k in ("intelligent function", "intelligente funktionen", "funzioni intelligenti")):
            add(f"{row['parameter']}: {v}")

    for mode in modes:
        add(mode)
    return features[:6]


def _derive_environments(
    category: str,
    overview: str | None,
    payload: str | None,
    trays: str | None,
    min_width: str | None,
    features: list[str],
    modes: list[str],
    spec_rows: list[dict],
) -> list[str]:
    """Canonical scenario tags deterministically derived from manual evidence."""
    blob = _fold(
        " ".join(
            [overview or "", payload or "", trays or "", " ".join(features), " ".join(modes)]
            + [f"{r.get('parameter', '')} {r.get('value', '')}" for r in spec_rows]
        )
    )
    tags: list[str] = []

    def add(tag: str) -> None:
        if tag not in tags:
            tags.append(tag)

    if category == "cleaning":
        add("cleaning")
        if any(w in blob for w in ("scrub", "schrubb", "strofin", "lavagg", "lavasciuga", "solution tank", "losungstank", "serbatoio della soluzione")):
            add("scrubbing")
            add("hard_floor")
        if any(w in blob for w in ("sweep", "kehr", "fegen", "spazz")):
            add("sweeping")
        if any(w in blob for w in ("carpet", "teppich", "tappet")):
            add("carpet")
        if any(w in blob for w in ("mop", "wisch", "polvere")):
            add("mopping")
        if any(w in blob for w in ("warehouse", "factor", "airport", "large-scale", "lager", "fabrik", "flughafen", "grosse umgebungen", "magazzin", "fabbric", "aeroport", "grandi dimensioni")):
            for t in ("warehouse", "factory", "large_area"):
                add(t)
        if any(w in blob for w in ("office", "hotel", "retail", "store", "small and medium", "buro", "geschaft", "kleiner und mittlerer", "uffici", "albergh", "negozi", "piccole e medie", "piccole o medie")):
            for t in ("office", "hotel", "retail", "supermarket"):
                add(t)

    elif category == "industrial_delivery":
        for t in ("industrial", "warehouse", "factory", "heavy_load", "material_transport"):
            add(t)
        if any(w in blob for w in ("lift", "hebe", "sollevamento")):
            add("lifting")

    elif category == "delivery":
        add("indoor_delivery")
        # Open-tray / dish-return service robots fit restaurants, cafes & dining
        if any(w in blob for w in ("tray", "ablage", "tablett", "vasso", "food", "speisen", "cibo", "dish", "spulgut", "piatti", "servierroboter")):
            for t in ("restaurant", "food_delivery", "hospitality"):
                add(t)
        if any(w in blob for w in ("dish-return", "spulgut", "ritorno piatti", "120 l", "120 litri", "calling", "anruf", "chiamate")):
            add("bussing_dish_return")
        if any(w in blob for w in ("compartment door", "dual-compartment", "fachturen", "einzelfach", "elevator", "aufzug", "ascensore")):
            for t in ("enclosed_compartments", "elevator", "hotel", "office"):
                add(t)
        if any(w in blob for w in ("hotel", "albergh", "escort", "begleit", "guiding", "fuhrungsmodus", "modalita guida")):
            for t in ("hotel", "guest_escorting"):
                add(t)
        if any(w in blob for w in ("reception", "empfang", "accoglienza", "advertising", "werbebildschirm", "pubblicitario")):
            for t in ("reception", "advertising_display"):
                add(t)
        if any(w in blob for w in ("supermarket", "supermarkt", "supermercat")):
            add("supermarket")
        if any(w in blob for w in ("factor", "fabrik", "fabbric")):
            add("factory")
        if any(w in blob for w in ("automatic door", "automatisches offnen", "apertura e chiusura automatica")):
            add("enclosed_hygiene")

    # Narrow-aisle capability (<= 65 cm minimum passage width)
    if min_width:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*cm", min_width, re.IGNORECASE)
        if m and float(m.group(1).replace(",", ".")) <= 65.0:
            add("narrow_aisles")

    return tags


def _fallback_use_case(
    product: str,
    language: str,
    category: str,
    payload: str | None,
    trays: str | None,
    modes: list[str],
    features: list[str],
) -> str:
    """Deterministic fallback strictly from verbatim specs & modes when a
    manual (FlashBot) has no prose Overview paragraph."""
    parts: list[str] = []
    if language == "de":
        label = {"cleaning": "Reinigungsroboter", "industrial_delivery": "Industrie-Transportroboter"}.get(
            category, "Lieferroboter"
        )
        parts.append(f"{product} ({label})")
    elif language == "it":
        label = {"cleaning": "robot per pulizie", "industrial_delivery": "robot di trasporto industriale"}.get(
            category, "robot di consegna"
        )
        parts.append(f"{product} ({label})")
    else:
        label = {"cleaning": "cleaning robot", "industrial_delivery": "industrial transport robot"}.get(
            category, "delivery robot"
        )
        parts.append(f"{product} ({label})")

    details = [x for x in ([payload, trays] + modes + features) if x]
    if details:
        parts.append("; ".join(dict.fromkeys(details[:6])))
    return " — ".join(parts)


def _format_citation(title: str, pages: list[int], language: str) -> str:
    parts = [title.strip()] if title else []
    if pages:
        if len(pages) == 1:
            parts.append(f"p. {pages[0]}")
        else:
            parts.append(f"p. {pages[0]}–{pages[-1]}")
    if language:
        parts.append(language.upper())
    return ", ".join(parts)


def _format_card_text(card: dict) -> str:
    """Compact human- and LLM-readable summary of the structured card."""
    lines = [f"{card['product']} [{card['category']}]"]
    if card.get("key_use_case"):
        lines.append(f"Use case: {card['key_use_case']}")
    specs = []
    for label, key in (
        ("Payload", "payload"),
        ("Trays/compartments", "trays_or_compartments"),
        ("Tanks/dustbin", "tank_or_dustbin"),
        ("Cleaning efficiency", "cleaning_efficiency"),
        ("Dimensions", "dimensions"),
        ("Weight", "weight"),
        ("Battery/runtime", "battery_runtime"),
        ("Charging", "charging_time"),
        ("Speed", "speed"),
        ("Min width", "min_passage_width"),
        ("Navigation", "navigation"),
    ):
        if card.get(key):
            specs.append(f"{label}: {card[key]}")
    if specs:
        lines.append("Specs: " + " | ".join(specs))
    if card.get("special_features"):
        lines.append("Features: " + "; ".join(card["special_features"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Public builder
# --------------------------------------------------------------------------
def build_product_profiles(
    chunks: list[dict],
    params: list[dict],
    *,
    include_accessories: bool = False,
) -> list[dict]:
    """Build deterministic product profile cards from chunks + parameter rows."""
    chunks_by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    langs_by_product: dict[str, set[str]] = defaultdict(set)

    for chunk in chunks:
        meta = chunk.get("metadata", {})
        product = meta.get("product", "")
        lang = meta.get("language", "")
        doc_type = meta.get("doc_type", "")
        if not product or not lang:
            continue
        if not include_accessories and doc_type == "accessory":
            continue
        chunks_by_pair[(product, lang)].append(chunk)
        langs_by_product[product].add(lang)

    params_by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in params:
        product = row.get("product", "")
        lang = row.get("language", "")
        if (product, lang) in chunks_by_pair:
            params_by_pair[(product, lang)].append(row)

    profiles: list[dict] = []
    for (product, lang) in sorted(chunks_by_pair):
        pair_chunks = chunks_by_pair[(product, lang)]
        first_meta = pair_chunks[0].get("metadata", {})
        doc_type = first_meta.get("doc_type", "robot")
        title = first_meta.get("title", product)

        raw_spec_rows = [r for r in params_by_pair[(product, lang)] if _is_spec_row(r)]
        spec_rows = _normalize_spec_rows(raw_spec_rows)

        # -- core specs -----------------------------------------------------
        dim_rows = _match_rows(
            spec_rows,
            any_phrases=(
                "robot dimensions", "overall dimensions", "product dimensions",
                "body size", "dimensions",
                "masse des roboters", "gesamtabmessung", "abmessungen des produkts", "abmessungen",
                "dimensioni del robot", "dimensioni complessive", "dimensioni del prodotto",
                "dimensione del robot",
            ),
            exclude_phrases=(
                "tray", "loading area", "chassis", "rahmen", "telaio",
                "ablage", "vasso", "ladebereich", "without brush", "ohne",
            ),
        )
        dimensions = _join_unique_values(dim_rows, limit=2)

        weight_rows = _match_rows(
            spec_rows,
            any_phrases=(
                "robot weight", "overall weight", "product weight", "body weight", "weight",
                "gewicht des roboters", "gesamtgewicht", "gewicht des produkts", "gewicht",
                "peso del robot", "peso complessivo", "peso del prodotto", "peso",
            ),
            exclude_phrases=("without brush", "ohne"),
        )
        weight = _join_unique_values(weight_rows, limit=2)

        tray_rows = _match_rows(
            spec_rows,
            any_phrases=(
                "no. of trays", "number of trays", "number of compartment doors",
                "anzahl ablagen", "anzahl der ablagen", "anzahl fachturen",
                "num. di vassoi", "numero di vassoi",
            ),
        )
        trays_or_compartments = _join_unique_values(tray_rows, limit=1)

        direct_load_rows = _match_rows(
            spec_rows,
            any_phrases=(
                "load capacity", "payload", "max. load", "max load",
                "ladekapazitat", "zuladung", "nutzlast", "max. last",
                "carico massimo", "portata massima", "capacita di carico",
            ),
        )
        tray_load_rows = _match_rows(
            spec_rows,
            any_phrases=(
                "tray load", "tray supporting capacity",
                "ablagelast", "tablettlast", "tragfahigkeit der ablagen",
                "carico dei vassoi", "capacita di supporto vassoio",
            ),
        )
        load_rows = direct_load_rows or tray_load_rows
        raw_load = _join_unique_values(load_rows, limit=1)
        if raw_load and trays_or_compartments and not direct_load_rows:
            payload: str | None = f"{raw_load} ({trays_or_compartments})"
        else:
            payload = raw_load

        battery_rows = _match_rows(
            spec_rows,
            any_phrases=(
                "battery capacity", "battery", "akkukapazitat", "batteriekapazitat",
                "kapazitat der batterie", "akkuleistung", "akku", "capacita della batteria",
            ),
            exclude_phrases=("battery life", "battery type", "akku-typ", "tipo di batteria"),
            value_pred=lambda v, _r: bool(_BATTERY_UNIT_RE.search(v)),
        )
        battery = _join_unique_values(battery_rows, limit=1)

        runtime_rows = _match_rows(
            spec_rows,
            any_phrases=(
                "battery life", "runtime", "operation time of batteries",
                "akkulaufzeit", "batterielebensdauer", "batterielaufzeit",
                "betriebsdauer der batterien", "laufzeit",
                "durata della batteria", "autonomia", "durata di funzionamento",
            ),
            value_pred=lambda v, _r: bool(_TIME_UNIT_RE.search(v)) and not bool(_SERVICE_LIFE_RE.search(v)),
        )
        if not runtime_rows:
            # PUDU CC1 EN places per-mode battery hours in "Cleaning capabilities".
            runtime_rows = _match_rows(
                spec_rows,
                any_phrases=("cleaning capabilit", "capacita di pulizia"),
                value_pred=lambda v, _r: bool(_TIME_UNIT_RE.search(v)),
            )
        runtime = _join_unique_values(runtime_rows, limit=2)

        if battery and runtime:
            battery_runtime: str | None = f"{battery} / {runtime}"
        else:
            battery_runtime = battery or runtime

        charge_rows = _match_rows(
            spec_rows,
            any_phrases=(
                "charging time", "charge time", "aufladezeit", "ladezeit",
                "tempo di carica", "tempo di ricarica",
            ),
        )
        charging_time = _join_unique_values(charge_rows, limit=1)

        speed_rows = _match_rows(
            spec_rows,
            any_phrases=(
                "cruise speed", "max. moving speed", "moving speed", "speed",
                "rundfahrtgeschwindigkeit", "fahrgeschwindigkeit",
                "max. bewegungsgeschwindigkeit", "geschwindigkeit",
                "velocita di crociera", "velocita",
            ),
            exclude_phrases=("brush", "burste", "spazzola"),
        )
        speed = _join_unique_values(speed_rows, limit=1)

        width_rows = _match_rows(
            spec_rows,
            any_phrases=(
                "min. travel width", "minimum passing width", "min. path clearance",
                "mindestdurchgangsbreite", "minimale durchfahrbreite",
                "min. pfadabstand", "min. wegbreite",
                "geringste durchgangsbreite", "minima larghezza", "larghezza minima",
                "minimo distanziamento", "spazio minimo", "esigenza di spazio",
            ),
            exclude_phrases=("height", "hohe", "altezza"),
        )
        min_passage_width = _join_unique_values(width_rows, limit=1)

        nav_rows = _match_rows(
            spec_rows,
            any_phrases=(
                "navigation", "positioning mode",
                "navigationsmethode", "positionierungsmodus",
                "metodo di navigazione", "navigazione", "modalita di posizionamento",
            ),
        )
        navigation = _join_unique_values(nav_rows, limit=1)

        eff_rows = _match_rows(
            spec_rows,
            any_phrases=(
                "cleaning efficiency", "productivity", "cover cleaning",
                "reinigungseffizienz", "reinigungsleistung", "flachenreinigung",
                "efficienza di pulizia", "produttivita", "pulizia superficiale",
            ),
            value_pred=lambda v, _r: bool(_AREA_RATE_RE.search(v)),
        )
        if not eff_rows:
            eff_rows = [r for r in spec_rows if _AREA_RATE_RE.search(str(r.get("value", "")))]
        cleaning_efficiency = _join_unique_values(eff_rows, limit=1)

        tank_rows = _match_rows(
            spec_rows,
            any_phrases=(
                "water tank capacity", "solution tank capacity", "recovery tank capacity",
                "dustbin capacity", "mobile water tank capacity",
                "fassungsvermogen des wassertanks", "kapazitat des losungstanks",
                "kapazitat des schmutzwassertanks", "kapazitat der staubbox",
                "staubbehalterkapazitat", "fassungsvermogen des staubbehalters",
                "capacita del serbatoio", "capacita del cestino della polvere",
                "capacita del contenitore della polvere",
            ),
        )
        tank_parts: list[str] = []
        for r in tank_rows:
            val = _clean_value(str(r.get("value", "")))
            val_fold = _fold(val)
            p_clean = _clean_value(str(r.get("parameter", "")))
            if any(lbl in val_fold for lbl in ("tank", "serbatoio", "dustbin", "staub")):
                part = val
            else:
                part = f"{p_clean}: {val}"
            if part not in tank_parts:
                tank_parts.append(part)
        tank_or_dustbin = "; ".join(tank_parts[:3]) if tank_parts else None

        # -- overview, modes, category & scenario tags ----------------------
        text_chunks = [c for c in pair_chunks if c.get("metadata", {}).get("chunk_type") == "text"]
        overview, overview_chunk = _extract_overview(text_chunks, product)
        modes, mode_chunks = _extract_modes(pair_chunks)

        category = _infer_category(
            doc_type=doc_type,
            overview=overview,
            cleaning_efficiency=cleaning_efficiency,
            tank_or_dustbin=tank_or_dustbin,
            payload=payload,
            spec_rows=spec_rows,
        )
        special_features = _extract_special_features(spec_rows, modes, overview)
        environments = _derive_environments(
            category=category,
            overview=overview,
            payload=payload,
            trays=trays_or_compartments,
            min_width=min_passage_width,
            features=special_features,
            modes=modes,
            spec_rows=spec_rows,
        )

        if overview:
            key_use_case = overview
            if modes and not any(_fold(m) in _fold(overview) for m in modes):
                key_use_case = f"{overview} ({', '.join(modes)})"
        else:
            key_use_case = _fallback_use_case(
                product, lang, category, payload, trays_or_compartments, modes, special_features
            )

        # -- representative appearance figure -------------------------------
        fig_chunks = [c for c in pair_chunks if c.get("metadata", {}).get("chunk_type") == "figure"]
        best_fig = max(fig_chunks, key=appearance_figure_score, default=None)
        if best_fig is not None and appearance_figure_score(best_fig) < 0:
            best_fig = None

        # -- source chunk links & verifiable citation -----------------------
        used_spec_rows = (
            dim_rows + weight_rows + tray_rows + load_rows + battery_rows
            + runtime_rows + charge_rows + speed_rows + width_rows + nav_rows
            + eff_rows + tank_rows
        )
        spec_chunk_ids = list(dict.fromkeys(r["chunk_id"] for r in used_spec_rows if r.get("chunk_id")))
        spec_chunk_id = spec_chunk_ids[0] if spec_chunk_ids else None
        overview_chunk_id = overview_chunk["id"] if overview_chunk else None
        figure_chunk_id = best_fig["id"] if best_fig else None

        chunk_ids: list[str] = []
        for cid in (
            ([overview_chunk_id] if overview_chunk_id else [])
            + spec_chunk_ids
            + [c["id"] for c in mode_chunks[:2]]
            + ([figure_chunk_id] if figure_chunk_id else [])
        ):
            if cid and cid not in chunk_ids:
                chunk_ids.append(cid)
        if not chunk_ids and pair_chunks:
            chunk_ids.append(pair_chunks[0]["id"])

        chunk_lookup = {c["id"]: c for c in pair_chunks}
        pages = sorted(
            {
                int(p)
                for cid in chunk_ids
                if cid in chunk_lookup
                for p in [
                    chunk_lookup[cid]["metadata"].get("page")
                    or chunk_lookup[cid]["metadata"].get("pdf_page")
                ]
                if p is not None
            }
        )

        card = {
            "id": f"profile-{_slug(product)}-{lang}",
            "product": product,
            "manual": first_meta.get("manual", ""),
            "doc_type": doc_type,
            "title": title,
            "language": lang,
            "available_languages": sorted(langs_by_product[product]),
            "source_pdf": first_meta.get("source_pdf", ""),
            "category": category,
            "dimensions": dimensions,
            "weight": weight,
            "payload": payload,
            "trays_or_compartments": trays_or_compartments,
            "battery": battery,
            "runtime": runtime,
            "battery_runtime": battery_runtime,
            "charging_time": charging_time,
            "speed": speed,
            "min_passage_width": min_passage_width,
            "navigation": navigation,
            "cleaning_efficiency": cleaning_efficiency,
            "tank_or_dustbin": tank_or_dustbin,
            "special_features": special_features,
            "environments": environments,
            "key_use_case": key_use_case,
            "image": best_fig["metadata"].get("image") if best_fig else None,
            "overview_chunk_id": overview_chunk_id,
            "spec_chunk_id": spec_chunk_id,
            "figure_chunk_id": figure_chunk_id,
            "chunk_ids": chunk_ids,
            "pages": pages,
            "citation": _format_citation(title, pages, lang),
        }
        card["card_text"] = _format_card_text(card)
        profiles.append(card)

    return profiles


def _load_jsonl(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic product profile cards.")
    parser.add_argument("--chunks", default=Path("build/chunks.jsonl"), type=Path)
    parser.add_argument("--params", default=Path("build/params.jsonl"), type=Path)
    parser.add_argument("--out", default=Path("build/profiles.jsonl"), type=Path)
    parser.add_argument("--include-accessories", action="store_true")
    args = parser.parse_args()

    chunks = _load_jsonl(args.chunks)
    params = _load_jsonl(args.params)
    profiles = build_product_profiles(
        chunks, params, include_accessories=args.include_accessories
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for card in profiles:
            fh.write(json.dumps(card, ensure_ascii=False) + "\n")

    products = sorted({c["product"] for c in profiles})
    print(f"{len(profiles)} product profile cards ({len(products)} products) -> {args.out}")


if __name__ == "__main__":
    main()
