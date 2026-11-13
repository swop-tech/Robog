"""Domain lexicon: the parameter vocabulary of this corpus, in EN/DE/IT.

BM25 is exact by nature, which is good for values and bad for synonyms: the
manual says "Load capacity", the user says "payload" (or "Zuladung", or
"carico"). A tiny curated concept table closes that gap *and* acts as a cheap
cross-lingual bridge: a German question expands to the English term, which is
how a DE question finds a manual that only exists in EN.

This is not a general thesaurus -- it is the ~40 concepts this documentation
actually exposes, written from the manuals. Everything else (paraphrases,
advisory language) is the embedding layer's job later.

Usage:
    from retrieve.lexicon import expand_tokens
    expand_tokens(["zuladung"])   # -> ["payload", "load", "capacity", "carico", ...]
"""

from __future__ import annotations

from .text import fold, tokenize

# concept -> surface forms (all three languages, plus the inflections that show
# up in real questions). Keep forms short; they are tokenised on load.
CONCEPTS: dict[str, tuple[str, ...]] = {
    "payload": (
        "payload", "load capacity", "max load", "loading capacity",
        "zuladung", "nutzlast", "tragfahigkeit", "tragfähigkeit", "traglast",
        "ladekapazität", "ladekapazitat", "ladegewicht",
        "carico", "portata", "portata massima", "capacità di carico",
    ),
    "weight": (
        "weight", "net weight", "gross weight", "weighs",
        "gewicht", "netto-gewicht", "bruttogewicht", "wiegt",
        "peso", "pesa", "pesano", "peso netto",
    ),
    "dimensions": (
        "dimensions", "size", "measurements", "width", "height", "depth",
        "abmessungen", "masse", "maße", "große", "größe", "breite", "hohe", "höhe", "tiefe",
        "dimensioni", "misure", "larghezza", "altezza", "profondita", "profondità",
    ),
    "battery": (
        "battery", "battery capacity", "accumulator", "charge", "charging",
        "akku", "batterie", "akkukapazität", "akkukapazitat", "kapazität", "laden", "ladevorgang",
        "batteria", "capacità batteria", "carica", "ricarica",
    ),
    "runtime": (
        "runtime", "run time", "operating time", "battery life", "endurance", "autonomy",
        "laufzeit", "akkulaufzeit", "betriebszeit", "ausdauer",
        "autonomia", "durata", "durata della batteria", "tempo di funzionamento",
    ),
    "charging_time": (
        "charging time", "charge time", "time to charge",
        "ladezeit", "ladedauer",
        "tempo di ricarica", "tempo di carica",
    ),
    "speed": (
        "speed", "max speed", "velocity", "fast",
        "geschwindigkeit", "maximale geschwindigkeit", "schnell",
        "velocità", "velocita", "rapido",
    ),
    "voltage": (
        "voltage", "volt", "input voltage", "output voltage",
        "spannung", "eingangsspannung", "ausgangsspannung",
        "tensione", "tensione di ingresso", "tensione di uscita",
    ),
    "power": (
        "power", "watt", "power output", "power input", "consumption",
        "leistung", "ausgangsleistung", "eingangsleistung", "verbrauch", "leistungsaufnahme",
        "potenza", "potenza in uscita", "consumo",
    ),
    "temperature": (
        "temperature", "operating temperature", "working temperature", "storage temperature",
        "temperatur", "betriebstemperatur", "lagertemperatur", "umgebungstemperatur",
        "temperatura", "temperatura di esercizio", "temperatura di stoccaggio",
    ),
    "humidity": (
        "humidity", "relative humidity", "moisture",
        "luftfeuchtigkeit", "feuchtigkeit", "luftfeuchte",
        "umidità", "umidita", "umidità relativa",
    ),
    "water_tank": (
        "water tank", "clean water tank", "dirty water tank", "recovery tank", "solution tank",
        "wassertank", "frischwassertank", "schmutzwassertank",
        "serbatoio", "serbatoio dell'acqua", "serbatoio acqua pulita", "serbatoio acqua sporca",
    ),
    "filter": (
        "filter", "vacuum filter", "air filter",
        "filter", "luftfilter", "schmutzfilter",
        "filtro", "filtro dell'aria", "filtro aspirazione",
    ),
    "brush": (
        "brush", "disc brush", "roller brush", "side brush",
        "burste", "bürste", "burgsten", "scheibenbürste",
        "spazzola", "spazzola a disco", "spazzola laterale",
    ),
    "wheel": (
        "wheel", "drive wheel", "caster", "auxiliary wheel",
        "rad", "antriebsrad", "lenkrad", "hilfsrad",
        "ruota", "ruota motrice", "ruota ausiliaria",
    ),
    "sensor": (
        "sensor", "lidar", "laser", "camera", "rgbd", "ultrasonic",
        "sensor", "laserscanner", "kamera", "ultraschall",
        "sensore", "laser", "telecamera", "a ultrasuoni",
    ),
    "emergency_stop": (
        "emergency stop", "e-stop", "stop button", "safety switch",
        "notaus", "not-aus", "notausschalter", "sicherheitsschalter",
        "arresto di emergenza", "pulsante di emergenza", "interruttore di emergenza",
    ),
    "capacity_trays": (
        "tray", "trays", "shelf", "compartment", "compartments", "plate",
        "tablett", "tabletts", "fach", "fächer", "facher", "ablage", "etage",
        "vassoio", "vassoi", "scomparto", "scomparti", "piano",
    ),
    "network": (
        "wifi", "wi-fi", "network", "signal", "router", "bluetooth",
        "netzwerk", "wlan", "signal", "router", "bluetooth",
        "rete", "segnale", "router", "bluetooth",
    ),
    "error": (
        "error", "fault", "failure", "problem", "troubleshooting", "alarm",
        "fehler", "störung", "storung", "problem", "fehlerbehebung", "alarm",
        "errore", "guasto", "problema", "risoluzione dei problemi", "allarme",
    ),
    "charging_station": (
        "charging station", "charger", "charging dock", "charging pile",
        "ladestation", "ladegerät", "ladegerat", "ladestation",
        "stazione di ricarica", "caricabatterie", "charger",
    ),
    "cleaning": (
        "cleaning", "clean", "wash", "maintenance", "service",
        "reinigung", "reinigen", "wartung", "pflege",
        "pulizia", "pulire", "manutenzione",
    ),
    "installation": (
        "installation", "install", "setup", "mounting", "assemble",
        "installation", "montage", "aufbau", "zusammenbau",
        "installazione", "installare", "montaggio", "assemblaggio",
    ),
    "power_on": (
        "switch on", "power on", "turn on", "start up", "power switch", "on switch",
        "einschalten", "anschalten", "schalte", "schalten", "netztaste", "einschaltknopf",
        "accendere", "accensione", "accende", "pulsante di accensione", "tasto di accensione",
    ),
    "power_off": (
        "switch off", "power off", "turn off", "shut down", "power switch",
        "ausschalten", "abschalten", "ausschalte", "ausknopf",
        "spegnere", "spegnimento", "spegne", "pulsante di spegnimento",
    ),
    "warranty": (
        "warranty", "guarantee", "service life",
        "garantie", "gewährleistung", "lebensdauer",
        "garanzia", "durata di vita",
    ),
}

# token -> {concept: weight} ; multi-word forms contribute their tokens too.
_FORM_TO_CONCEPT: dict[str, dict[str, float]] = {}
_CONCEPT_FORMS: dict[str, set[str]] = {}

# Modifiers that say nothing about *which* parameter is meant.
GENERIC_TOKENS = {"max", "maximum", "min", "minimum", "maximale", "massima", "massimo"}

for _concept, _forms in CONCEPTS.items():
    for _form in _forms:
        _tokens = [t for t in tokenize(_form) if t not in GENERIC_TOKENS]
        if not _tokens:
            continue
        _CONCEPT_FORMS.setdefault(_concept, set()).update(_tokens)
        # A multi-word form gives weaker evidence per token than a single word.
        _weight = 1.0 / len(_tokens)
        for _token in _tokens:
            _FORM_TO_CONCEPT.setdefault(_token, {})
            _FORM_TO_CONCEPT[_token][_concept] = max(
                _FORM_TO_CONCEPT[_token].get(_concept, 0.0), _weight
            )


def concepts_for(token: str) -> list[str]:
    return list(_FORM_TO_CONCEPT.get(fold(token), {}))


def expand_tokens(tokens: list[str], *, max_per_token: int = 12) -> list[str]:
    """Add the other surface forms of every concept the query touches.

    Returns the original tokens first, then the expansion (deduplicated), so a
    caller can give the strict matches more weight than the expanded ones.
    """
    expanded: list[str] = list(dict.fromkeys(tokens))
    seen = set(expanded)
    for token in tokens:
        weighted = _FORM_TO_CONCEPT.get(fold(token))
        if not weighted:
            continue
        for concept in sorted(weighted, key=lambda c: -weighted[c]):
            added = 0
            # Short forms are usually the base words ("peso" before "netto-gewicht").
            forms = sorted(_CONCEPT_FORMS.get(concept, ()), key=lambda t: (len(t), t))
            for form_token in forms:
                if form_token in seen:
                    continue
                expanded.append(form_token)
                seen.add(form_token)
                added += 1
                if added >= max_per_token:
                    break
    return expanded
