"""Structured product profile lookup -- the advisory & comparison path (gap G2).

When a non-technical user asks *"Which robot fits my restaurant?"* or *"Quale
robot è adatto per pulire un supermercato?"*, scattered text chunks miss half
the fleet and let wheel-cleaning maintenance tables masquerade as cleaning
robots. ``build/profiles.jsonl`` holds one deterministic, citation-backed card
per ``(robot, language)`` across all 13 robots, so ``ProfileIndex`` can compare
every robot side-by-side while honoring the same EN/DE/IT language policy as
the chunk retriever.

Usage:
    from retrieve.profiles import ProfileIndex
    index = ProfileIndex.from_jsonl("build/profiles.jsonl")
    cards = index.search("Which robot fits my restaurant?", language="en", k=5)
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ingest.profiles import build_product_profiles

from .lang import LANGUAGES
from .text import fold, tokenize

# Framing words that appear in almost every advisory question and say nothing
# about *which* robot fits.
ADVISORY_FRAMING_WORDS: frozenset[str] = frozenset(
    """
    robot robots roboter modello model modelli gerat machine
    which what best suited suitable fit fits ideal right choose buy recommend suggest compare comparison difference versus vs
    welcher welches welche eignet passt passend besser empfehl empfiehl vergleich unterschied sollte
    quale quali adatto adatta meglio consigli consiglio confronto differenza differenze scegliere conviene ideale
    need customer customers my our use using fur per
    """.split()
)

# Scenario cue sets (folded tokens) across EN / DE / IT.
_CLEANING_CUES: frozenset[str] = frozenset(
    """
    clean cleaning cleaner cleaners sweep sweeping sweeper scrub scrubbing scrubber
    mop mopping vacuum vacuuming wash washing floor floors carpet carpets dust dirt stain
    reinigen reinigung reinigungsroboter kehren kehrroboter fegen schrubben wischen
    saugen staubsaugen boden hartboden teppich teppiche staub schmutz lavasciuga
    pulire pulizia pulizie spazzare spazzamento lavare lavaggio aspirare aspirazione
    pavimento pavimenti tappeto tappeti polvere sporco
    """.split()
)

_INDUSTRIAL_CUES: frozenset[str] = frozenset(
    """
    industrial industry factory factories warehouse warehouses material materials heavy lifting
    industrie industriell industrieszenarien fabrik fabriken lager werkstatt materialtransport schwerlast heben hebemodus
    industriale industriali fabbrica fabbriche magazzino magazzini materiali pesante pesanti sollevamento
    """.split()
)

_FOOD_RESTAURANT_CUES: frozenset[str] = frozenset(
    """
    restaurant restaurants cafe cafes bar dining waiter table tables food dish dishes meal meals plate plates tray trays catering hospitality bussing
    speisen essen getranke geschirr teller tablett tabletts ablage ablagen gastronomie restaurantbetrieb servieren servierroboter kuche
    ristorante ristoranti bar caffetteria cibo piatti piatto vassoio vassoi tavolo tavoli sala ristorazione cameriere
    """.split()
)

_HOTEL_BUILDING_CUES: frozenset[str] = frozenset(
    """
    hotel hotels office offices building buildings room rooms floor guest guests lobby
    buro burogebaude gast gaste zimmer etage stockwerk empfangshalle
    albergo alberghi hotel ufficio uffici edificio camera camere ospite ospiti
    """.split()
)

_ELEVATOR_ENCLOSED_CUES: frozenset[str] = frozenset(
    """
    elevator elevators lift lifts compartment compartments enclosed door doors secure privacy
    aufzug aufzuge fahrstuhl fachturen einzelfach doppelfach geschlossen tur turen
    ascensore ascensori scomparto scomparti chiuso chiusi porta porte
    """.split()
)

_RECEPTION_SCREEN_CUES: frozenset[str] = frozenset(
    """
    reception greeting greet welcome escort escorting marketing ad ads advertising screen display promotion
    empfang begrussung begleitmodus begleiten werbung werbebildschirm marketing
    accoglienza benvenuto accompagnare pubblicita pubblicitario schermo promozione
    """.split()
)

_NARROW_CUES: frozenset[str] = frozenset(
    """
    narrow tight compact small aisle aisles passage corridor crowded
    eng enge schmal kompakt durchgang gang uberfullt
    stretto stretti compatto corridoio passaggio affollato
    """.split()
)

_BUSSING_CUES: frozenset[str] = frozenset(
    """
    bussing return collect collecting collection dirty call calling pager notification large volume
    ruckgabe spulgut abramen geschirruckgabe anruf rufsystem
    ritorno raccolta sporchi chiamata chiamate notifiche
    """.split()
)

_RETAIL_SUPERMARKET_CUES: frozenset[str] = frozenset(
    """
    supermarket supermarkets retail shop store mall
    supermarkt einzelhandel geschaft kaufhaus
    supermercato supermercati negozio negozi centro commerciale
    """.split()
)

_KG_QUERY_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*kg\b", re.IGNORECASE)
_CM_QUERY_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*cm\b", re.IGNORECASE)
_TRAY_COUNT_WORDS = {
    "two": 2, "zwei": 2, "due": 2, "2": 2,
    "three": 3, "drei": 3, "tre": 3, "3": 3,
    "four": 4, "vier": 4, "quattro": 4, "4": 4,
}


def _card_max_kg(record: dict) -> float | None:
    payload = str(record.get("payload") or "")
    if not payload:
        return None
    nums = [float(m.group(1).replace(",", ".")) for m in _KG_QUERY_RE.finditer(payload)]
    if not nums:
        return None
    best = max(nums)
    # Per-layer tray specs ("10 kg/layer (Four)"): estimate total tray capacity
    # when the user asks for e.g. "40 kg total" across trays.
    folded = fold(payload)
    if any(u in folded for u in ("/layer", "/level", "/ablage", "/fach", "/ebene", "per vassoio", "/vassoio", "/livello")):
        trays = fold(str(record.get("trays_or_compartments") or ""))
        for word, count in _TRAY_COUNT_WORDS.items():
            if re.search(rf"\b{re.escape(word)}\b", trays):
                best = max(best, best * count)
                break
    return best


def _card_min_width_cm(record: dict) -> float | None:
    width = str(record.get("min_passage_width") or "")
    m = _CM_QUERY_RE.search(width)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


_DELIVERY_CUES: frozenset[str] = frozenset(
    """
    deliver delivery delivering deliveries liefern lieferung zustellung consegna consegnare
    """.split()
)

_SCENARIO_CUE_SETS: tuple[frozenset[str], ...] = (
    _CLEANING_CUES,
    _INDUSTRIAL_CUES,
    _FOOD_RESTAURANT_CUES,
    _HOTEL_BUILDING_CUES,
    _ELEVATOR_ENCLOSED_CUES,
    _RECEPTION_SCREEN_CUES,
    _NARROW_CUES,
    _BUSSING_CUES,
    _RETAIL_SUPERMARKET_CUES,
    _DELIVERY_CUES,
)


def has_scenario_signal(query: str) -> bool:
    """True when an advisory question names a use case, environment or number.

    "Which robot is best?" names none of these, so every card scores the same
    and any ranking would be arbitrary. Such a question must be answered by
    asking for the scenario, not by listing 13 robots as if they were a choice.
    """
    if _KG_QUERY_RE.search(query) or _CM_QUERY_RE.search(query):
        return True
    tokens = set(tokenize(query))
    return any(tokens & cues for cues in _SCENARIO_CUE_SETS)


@dataclass
class ProfileHit:
    record: dict
    score: float
    cross_lingual: bool = False

    @property
    def product(self) -> str:
        return self.record.get("product", "")

    @property
    def language(self) -> str:
        return self.record.get("language", "")

    @property
    def category(self) -> str:
        return self.record.get("category", "")

    @property
    def dimensions(self) -> str | None:
        return self.record.get("dimensions")

    @property
    def weight(self) -> str | None:
        return self.record.get("weight")

    @property
    def payload(self) -> str | None:
        return self.record.get("payload")

    @property
    def battery_runtime(self) -> str | None:
        return self.record.get("battery_runtime")

    @property
    def speed(self) -> str | None:
        return self.record.get("speed")

    @property
    def min_passage_width(self) -> str | None:
        return self.record.get("min_passage_width")

    @property
    def key_use_case(self) -> str:
        return self.record.get("key_use_case", "")

    @property
    def image(self) -> str | None:
        return self.record.get("image")

    @property
    def citation(self) -> str:
        return self.record.get("citation", "")

    @property
    def chunk_ids(self) -> list[str]:
        return list(self.record.get("chunk_ids") or ())

    def to_dict(self) -> dict:
        return {
            **self.record,
            "score": round(self.score, 4),
            "cross_lingual": self.cross_lingual,
        }


class ProfileIndex:
    """Deterministic retriever & catalog over the 13 robot profile cards."""

    def __init__(self, records: list[dict]):
        self.records = records
        self.by_product: dict[str, dict[str, dict]] = defaultdict(dict)
        self._tokens: list[list[str]] = []
        postings: dict[str, set[int]] = defaultdict(set)

        for idx, record in enumerate(records):
            product = record.get("product", "")
            lang = record.get("language", "")
            if product and lang:
                self.by_product[product][lang] = record

            env_words = " ".join(tag.replace("_", " ") for tag in record.get("environments", ()))
            searchable = " ".join(
                str(part or "")
                for part in (
                    record.get("product"),
                    record.get("category", "").replace("_", " "),
                    record.get("key_use_case"),
                    record.get("payload"),
                    record.get("trays_or_compartments"),
                    record.get("tank_or_dustbin"),
                    record.get("cleaning_efficiency"),
                    record.get("dimensions"),
                    record.get("weight"),
                    record.get("battery_runtime"),
                    record.get("speed"),
                    record.get("min_passage_width"),
                    record.get("navigation"),
                    " ".join(record.get("special_features", ())),
                    env_words,
                )
            )
            toks = tokenize(searchable)
            self._tokens.append(toks)
            for tok in set(toks):
                postings[tok].add(idx)

        self._record_index = {id(r): i for i, r in enumerate(records)}
        self.postings = dict(postings)
        self.n = len(records)
        self.avg_len = (sum(len(t) for t in self._tokens) / self.n) if self.n else 1.0
        self._df = {tok: len(docs) for tok, docs in self.postings.items()}

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "ProfileIndex":
        p = Path(path)
        if not p.exists():
            return cls([])
        with p.open(encoding="utf-8") as fh:
            return cls([json.loads(line) for line in fh if line.strip()])

    @classmethod
    def from_chunks_and_params(
        cls,
        chunks: list[dict],
        params: list[dict],
    ) -> "ProfileIndex":
        return cls(build_product_profiles(chunks, params))

    @property
    def products(self) -> list[str]:
        return sorted(self.by_product)

    def catalog(
        self,
        language: str | None = "en",
        *,
        cross_lingual_fallback: bool = True,
    ) -> list[ProfileHit]:
        """Return one localized profile card per robot (all 13 robots)."""
        out: list[ProfileHit] = []
        pref = ([language] if language else []) + [lg for lg in LANGUAGES if lg != language]
        for product in sorted(self.by_product):
            lang_map = self.by_product[product]
            if language and language in lang_map:
                out.append(ProfileHit(lang_map[language], score=1.0, cross_lingual=False))
                continue
            if not cross_lingual_fallback and language:
                continue
            for fallback in pref:
                if fallback in lang_map:
                    out.append(
                        ProfileHit(
                            lang_map[fallback],
                            score=1.0,
                            cross_lingual=bool(language and fallback != language),
                        )
                    )
                    break
        return out

    # -- scoring -----------------------------------------------------------
    def _lexical_score(
        self,
        record: dict,
        strict_tokens: set[str],
        synonym_tokens: set[str],
    ) -> float:
        idx = self._record_index.get(id(record))
        if idx is None:
            return 0.0
        tokens = self._tokens[idx]
        if not tokens:
            return 0.0
        length = len(tokens) or 1
        score = 0.0
        for tok, weight in [(t, 1.0) for t in strict_tokens] + [(t, 0.45) for t in synonym_tokens]:
            df = self._df.get(tok, 0)
            if not df:
                continue
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            tf = tokens.count(tok)
            if not tf:
                continue
            score += weight * idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * length / (self.avg_len or 1)))
        return score

    def _scenario_score(
        self,
        record: dict,
        all_query_tokens: set[str],
        raw_query: str,
        explicit_product: bool,
    ) -> tuple[float, bool]:
        """Return ``(scenario_score, matched_specific_capability)``."""
        category = record.get("category", "")
        envs = set(record.get("environments") or ())
        score = 0.0
        specific = False

        wants_cleaning = bool(all_query_tokens & _CLEANING_CUES)
        wants_industrial = bool(all_query_tokens & _INDUSTRIAL_CUES) and not wants_cleaning
        wants_food = bool(all_query_tokens & _FOOD_RESTAURANT_CUES)
        wants_hotel = bool(all_query_tokens & _HOTEL_BUILDING_CUES)
        wants_elevator = bool(all_query_tokens & _ELEVATOR_ENCLOSED_CUES)
        wants_reception = bool(all_query_tokens & _RECEPTION_SCREEN_CUES)
        wants_narrow = bool(all_query_tokens & _NARROW_CUES)
        wants_bussing = bool(all_query_tokens & _BUSSING_CUES)
        wants_retail = bool(all_query_tokens & _RETAIL_SUPERMARKET_CUES)

        # Numeric constraints ("carry 300 kg", "60 cm aisle")
        kg_matches = [float(m.group(1).replace(",", ".")) for m in _KG_QUERY_RE.finditer(raw_query)]
        if kg_matches and max(kg_matches) >= 80.0 and not wants_cleaning:
            wants_industrial = True

        if wants_cleaning:
            if category == "cleaning":
                score += 6.0
                if wants_retail and "supermarket" in envs:
                    score += 2.5
                    specific = True
                if wants_hotel and "hotel" in envs:
                    score += 2.0
                    specific = True
                if (all_query_tokens & _INDUSTRIAL_CUES) and "warehouse" in envs:
                    score += 2.5
                    specific = True
                if any(t in all_query_tokens for t in ("carpet", "teppich", "tappeto", "tappeti")) and "carpet" in envs:
                    score += 2.5
                    specific = True
            elif not explicit_product:
                score -= 3.0

        elif wants_industrial:
            if category == "industrial_delivery":
                score += 6.0
                specific = True
            elif "factory" in envs or "warehouse" in envs:
                score += 2.0

        else:
            # Delivery / hospitality / general robot selection
            if wants_food:
                if category == "delivery":
                    score += 4.5
                if "restaurant" in envs or "food_delivery" in envs:
                    score += 2.5
                # Open multi-tray robots are the primary restaurant servers unless
                # enclosed compartments/elevators were asked for.
                if not wants_elevator and "enclosed_compartments" not in envs and category == "delivery":
                    score += 1.0

            if wants_hotel:
                if "hotel" in envs:
                    score += 3.0
                elif category == "delivery":
                    score += 1.5

            if wants_retail and "supermarket" in envs:
                score += 2.5

            if not (wants_food or wants_hotel or wants_retail) and category == "delivery":
                # Broad "which robot should I choose?" defaults slightly toward
                # general-purpose service robots while keeping all 13 eligible.
                score += 1.0

        # Capability-specific boosts apply across scenarios
        if wants_elevator and ("elevator" in envs or "enclosed_compartments" in envs or "enclosed_hygiene" in envs):
            score += 4.0 if "elevator" in envs else 2.5
            specific = True
        if wants_reception and ("reception" in envs or "advertising_display" in envs):
            score += 3.0
            specific = True
        if wants_narrow and "narrow_aisles" in envs:
            score += 3.0
            specific = True
        if wants_bussing and "bussing_dish_return" in envs:
            score += 3.0
            specific = True

        if kg_matches:
            req_kg = max(kg_matches)
            cap_kg = _card_max_kg(record)
            if cap_kg is not None and cap_kg >= req_kg:
                score += 3.5
                specific = True

        cm_matches = [float(m.group(1).replace(",", ".")) for m in _CM_QUERY_RE.finditer(raw_query)]
        if cm_matches:
            req_cm = min(cm_matches)
            width_cm = _card_min_width_cm(record)
            if width_cm is not None and width_cm <= req_cm:
                score += 3.5
                specific = True

        return score, specific

    def search(
        self,
        query: str,
        *,
        k: int = 6,
        language: str | None = None,
        products: list[str] | None = None,
        follow_user_language: bool = True,
        cross_lingual_fallback: bool = True,
        extra_tokens: list[str] | None = None,
    ) -> list[ProfileHit]:
        """Rank structured robot profile cards for an advisory / comparison query."""
        if not self.records:
            return []

        raw_tokens = tokenize(query)
        if not raw_tokens and not products:
            return []

        wanted = set(products or ())
        product_name_tokens = {tok for prod in wanted for tok in tokenize(prod)}
        strict_tokens = {
            tok for tok in raw_tokens if tok not in ADVISORY_FRAMING_WORDS and tok not in product_name_tokens
        }
        synonym_tokens = {
            tok for tok in (extra_tokens or ())
            if tok not in strict_tokens and tok not in ADVISORY_FRAMING_WORDS and tok not in product_name_tokens
        }
        all_tokens = strict_tokens | synonym_tokens

        pref = ([language] if language else []) + [lg for lg in LANGUAGES if lg != language]
        same_lang_hits: list[tuple[ProfileHit, bool]] = []
        cross_lang_hits: list[tuple[ProfileHit, bool]] = []

        target_products = [p for p in sorted(self.by_product) if not wanted or p in wanted]
        for product in target_products:
            lang_map = self.by_product[product]
            explicit = product in wanted

            # Pick the language variant(s) allowed by the language policy.
            if follow_user_language and language:
                if language in lang_map:
                    chosen = [(lang_map[language], False)]
                elif cross_lingual_fallback:
                    fallback_lang = next((lg for lg in pref if lg in lang_map), None)
                    chosen = [(lang_map[fallback_lang], True)] if fallback_lang else []
                else:
                    chosen = []
            else:
                chosen = [(lang_map[lg], bool(language and lg != language)) for lg in pref if lg in lang_map]

            if not chosen:
                continue

            best_hit: ProfileHit | None = None
            best_specific = False
            for record, is_cross in chosen:
                lex = self._lexical_score(record, strict_tokens, synonym_tokens)
                scen, specific = self._scenario_score(record, all_tokens, query, explicit)
                total = lex + scen + (5.0 if explicit else 0.0)
                if total <= 0:
                    continue
                candidate = ProfileHit(record=record, score=round(total, 4), cross_lingual=is_cross)
                if best_hit is None or candidate.score > best_hit.score:
                    best_hit = candidate
                    best_specific = specific

            if best_hit is None:
                continue
            if best_hit.cross_lingual:
                cross_lang_hits.append((best_hit, best_specific))
            else:
                same_lang_hits.append((best_hit, best_specific))

        same_lang_hits.sort(key=lambda item: (-item[0].score, item[0].product))
        cross_lang_hits.sort(key=lambda item: (-item[0].score, item[0].product))

        # Two-tier language discipline: keep cross-lingual cards only when explicitly
        # requested, when same-language coverage is sparse, or when the cross-lingual
        # robot uniquely matches a specific capability (e.g. IT query about elevator -> FlashBot).
        combined: list[ProfileHit] = [hit for hit, _ in same_lang_hits]
        top_same = same_lang_hits[0][0].score if same_lang_hits else 0.0
        for hit, specific in cross_lang_hits:
            if (
                hit.product in wanted
                or len(same_lang_hits) < 2
                or not follow_user_language
                or (specific and hit.score >= top_same * 0.85)
            ):
                combined.append(hit)

        combined.sort(key=lambda hit: (-hit.score, hit.product))
        if combined and not wanted:
            cutoff = max(0.5, combined[0].score * 0.15)
            combined = [hit for hit in combined if hit.score >= cutoff]
        return combined[:k]
