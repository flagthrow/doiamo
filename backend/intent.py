"""Turn a sentence into a search.

    "voglio correre 10 km ma non voglio fare troppo dislivello
     e voglio rimanere in città"

Two readers, same output. The rule reader runs first because it is free,
instant, offline and testable, and the vocabulary of this domain is small —
a sport, a distance, a feeling about hills, a surface, maybe a place. Claude
is asked only for what the rules could not fill in, which is where the money
and the latency go.

Whatever is understood is handed back to the interface and shown, because a
box that silently reinterprets you is worse than a form.
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from typing import Dict, List, Optional

from pydantic import BaseModel

from .cache import TTLCache
from .intent_store import IntentStore

# What a hill preference means as a number, in metres of climb.
FLAT_TARGET_M = 40.0
HILLY_TARGET_M = 450.0

# Rough pace, for turning "un'ora" into a distance.
KMH = {"running": 10.0, "cycling": 20.0}

DEFAULT_SPORT = "running"

# Small numbers get spelled out far more often than large ones.
NUMBER_WORDS = {
    "mezza": 0.5, "mezz": 0.5, "un": 1, "una": 1, "due": 2, "tre": 3,
    "quattro": 4, "cinque": 5, "sei": 6,
    "half": 0.5, "one": 1, "an": 1, "a": 1, "two": 2, "three": 3, "four": 4,
}


class Intent(BaseModel):
    sport: Optional[str] = None
    mode: Optional[str] = None
    distance_km: Optional[float] = None
    elevation_gain_m: Optional[float] = None
    surface: Optional[str] = None
    sights: Optional[str] = None
    start_text: Optional[str] = None
    end_text: Optional[str] = None


def normalise(text: str) -> str:
    """Lowercase, strip accents, and settle the apostrophes.

    People type "città", "citta'" and "citta" for the same word, and a
    keyword table should not have to know that.
    """
    text = text.lower().replace("’", "'").replace("`", "'")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"(\w)'(\s|$)", r"\1 ", text)      # citta' -> citta
    return re.sub(r"\s+", " ", text).strip()


# Each entry: value -> the words that mean it. Order matters only within a
# group, where the more specific phrase has to be tested first.
SPORT_WORDS = {
    "cycling": ["bici", "bicicletta", "pedalare", "pedalando", "pedalo",
                "ciclismo", "in sella", "bike", "cycling", "cycle", "ride",
                "riding"],
    "running": ["correre", "correndo", "corsa", "corro", "corri", "corriamo",
                "running", "run", "jog", "jogging", "podismo", "camminare",
                "camminata", "walk", "walking"],
}
SURFACE_WORDS = {
    "trail": ["sterrato", "sentiero", "sentieri", "trail", "off road",
              "offroad", "ghiaia", "gravel", "dirt", "sterrati", "bosco"],
    "mixed": ["misto", "mixed", "un po' di tutto", "un po di tutto"],
    "asphalt": ["asfalto", "asfaltato", "citta", "urbano", "strada", "strade",
                "asphalt", "city", "urban", "road", "paved", "pavement"],
}
SIGHTS_WORDS = {
    "monuments": ["monumenti", "monumento", "storico", "storici", "centro storico",
                  "chiese", "monuments", "historic", "sightseeing", "landmarks"],
    "nature": ["verde", "parco", "parchi", "natura", "alberi", "prato",
               "green", "park", "parks", "nature", "trees"],
}
# The noun people use for climb, and the words that qualify it. Matching the
# qualifier anywhere in the run-up handles "non voglio fare troppo dislivello"
# as well as "poco dislivello", which a list of fixed phrases cannot.
CLIMB_NOUNS = r"(?:dislivello|salit[ae]|climb(?:ing)?|elevation|hills?)"
LOW_QUALIFIERS = r"(?:non|senza|niente|poc[oah]|meno|evita\w*|no|little|avoid\w*|without|not)"
HIGH_QUALIFIERS = r"(?:molto|molta|tanto|tanta|parecchi[oa]|con|lots? of|plenty|much)"

# Standalone words that settle it without a qualifier.
FLAT_WORDS = ["pianeggiant", "piatt", "in piano", "flat", "pianura"]
HILLY_WORDS = ["collin", "montagn", "hilly", "mountainous"]
LOOP_WORDS = ["anello", "circolare", "giro ad anello", "tornare al punto",
              "tornare a casa", "loop", "round trip", "circular",
              "back where i start", "same place"]


def _first_match(text: str, table: Dict[str, List[str]]) -> Optional[str]:
    """The value whose keyword appears earliest, longest keyword winning."""
    best_value, best_at, best_len = None, len(text) + 1, 0
    for value, words in table.items():
        for word in words:
            at = text.find(word)
            if at == -1:
                continue
            if at < best_at or (at == best_at and len(word) > best_len):
                best_value, best_at, best_len = value, at, len(word)
    return best_value


def _distance(text: str, sport: Optional[str]) -> Optional[float]:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:km|chilometri|kilometer|kilometre)", text)
    if match:
        return float(match.group(1).replace(",", "."))
    # "a flat 5k run"
    match = re.search(r"\b(\d+(?:[.,]\d+)?)k\b", text)
    if match:
        return float(match.group(1).replace(",", "."))

    # "un'ora", "due ore", "90 minuti" — a duration is a distance once you
    # know roughly how fast the person moves.
    pace = KMH.get(sport or DEFAULT_SPORT, KMH[DEFAULT_SPORT])
    hours = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:ore|ora|hours?|hrs?|h)\b", text)
    if hours:
        return round(float(hours.group(1).replace(",", ".")) * pace, 1)
    worded = re.search(r"\b(" + "|".join(NUMBER_WORDS) + r")\s+(?:ore|ora|hours?)\b", text)
    if worded:
        return round(NUMBER_WORDS[worded.group(1)] * pace, 1)
    if re.search(r"\b(un ora|un'ora|an hour|one hour)\b", text):
        return pace
    if re.search(r"\b(mezz ora|mezz'ora|half an hour)\b", text):
        return round(pace / 2, 1)
    minutes = re.search(r"(\d+)\s*(minuti|minutes|min)\b", text)
    if minutes:
        return round(int(minutes.group(1)) / 60 * pace, 1)
    return None


# Words that follow "in"/"a" but are not places.
NOT_A_PLACE = {
    "citta", "campagna", "montagna", "collina", "natura", "giro", "piano",
    "salita", "discesa", "mezzo", "zona", "centro", "periferia", "bici",
    "city", "town", "nature", "hills", "park", "the city", "the park",
}


def _climb(text: str) -> Optional[float]:
    """How the person feels about hills, as a target in metres."""
    for word in FLAT_WORDS:
        if word in text:
            return FLAT_TARGET_M
    for word in HILLY_WORDS:
        if word in text:
            return HILLY_TARGET_M

    # Up to six words may sit between the qualifier and the noun:
    # "non voglio fare troppo dislivello".
    gap = r"(?:\s+\S+){0,6}?\s+"
    if re.search(LOW_QUALIFIERS + gap + CLIMB_NOUNS, text):
        return FLAT_TARGET_M
    if re.search(HIGH_QUALIFIERS + gap + CLIMB_NOUNS, text):
        return HILLY_TARGET_M
    return None


def _places(text: str) -> Dict[str, Optional[str]]:
    """Pull a start and a destination out, conservatively.

    A wrong guess is worse than none: it sends the router somewhere the person
    never mentioned. So only explicit patterns count, and generic words are
    rejected.
    """
    def clean(value: str) -> Optional[str]:
        value = value.strip(" ,.;:!?").strip()
        if not value or value in NOT_A_PLACE or len(value) < 3:
            return None
        return value

    tail = r"(?:$|,|\.| e | in | con | ma | per )"
    pair = re.search(r"\bda (?:casa |qui )?(.+?) (?:a|fino a|verso) (.+?)" + tail, text)
    if pair:
        start, end = clean(pair.group(1)), clean(pair.group(2))
        if start and end:
            return {"start_text": start, "end_text": end}

    pair = re.search(r"\bfrom (.+?) to (.+?)(?:$|,|\.| and | on | with )", text)
    if pair:
        start, end = clean(pair.group(1)), clean(pair.group(2))
        if start and end:
            return {"start_text": start, "end_text": end}

    stop = r"(?:$|,|\.| e | tra | fra | con | ma | per | in | and | with | among | through )"
    start = None
    for pattern in (r"\bparto da (.+?)" + stop,
                    r"\bpartendo da (.+?)" + stop,
                    r"\bvicino (?:a|al|alla|ai|alle|allo) (.+?)" + stop,
                    r"\bzona (.+?)" + stop,
                    r"\bstarting (?:from|at) (.+?)" + stop,
                    r"\bnear (?:the )?(.+?)" + stop):
        found = re.search(pattern, text)
        if found:
            start = clean(found.group(1))
            if start:
                break
    return {"start_text": start, "end_text": None}


def read(sentence: str) -> Intent:
    """The rule reader. No network, no key, no cost."""
    text = normalise(sentence)
    if not text:
        return Intent()

    places = _places(text)

    # A place name is not a preference: "Parco Sempione" is where you start,
    # not a request for greenery. Take the names out before reading the rest.
    rest = text
    for name in (places["start_text"], places["end_text"]):
        if name:
            rest = rest.replace(name, " ")

    sport = _first_match(rest, SPORT_WORDS)
    surface = _first_match(rest, SURFACE_WORDS)
    sights = _first_match(rest, SIGHTS_WORDS)
    elevation = _climb(rest)
    mode = None
    if places["end_text"]:
        mode = "route"
    elif any(word in text for word in LOOP_WORDS):
        mode = "loop"

    return Intent(
        sport=sport,
        mode=mode,
        distance_km=_distance(rest, sport),
        elevation_gain_m=elevation,
        surface=surface,
        sights=sights,
        start_text=places["start_text"],
        end_text=places["end_text"],
    )


def summarise(intent: Intent, language: str = "it") -> List[str]:
    """What was understood, in the viewer's language, to be shown back."""
    it = language != "en"
    out: List[str] = []
    if intent.sport:
        out.append(("Corsa" if intent.sport == "running" else "Bici") if it
                   else ("Running" if intent.sport == "running" else "Cycling"))
    if intent.distance_km:
        out.append("{:g} km".format(intent.distance_km))
    if intent.elevation_gain_m is not None:
        flat = intent.elevation_gain_m <= FLAT_TARGET_M
        out.append(("poco dislivello" if flat else "molto dislivello") if it
                   else ("little climbing" if flat else "lots of climbing"))
    if intent.surface:
        labels = {"asphalt": ("asfalto", "asphalt"), "mixed": ("misto", "mixed"),
                  "trail": ("sterrato", "trail")}
        out.append(labels[intent.surface][0 if it else 1])
    if intent.sights:
        labels = {"monuments": ("monumenti", "monuments"),
                  "nature": ("verde", "green")}
        out.append(labels[intent.sights][0 if it else 1])
    if intent.mode == "loop":
        out.append("anello" if it else "loop")
    if intent.start_text:
        out.append(("da " if it else "from ") + intent.start_text)
    if intent.end_text:
        out.append(("a " if it else "to ") + intent.end_text)
    return out


# --- the model, for what the rules cannot reach ----------------------------

# The rules settle most sentences; the model only sees what they gave up on,
# which is a 250-token extraction into eight optional fields. Haiku is sized
# for that. Set INTENT_MODEL to trade up if the vague sentences disappoint.
MODEL = os.environ.get("INTENT_MODEL", "claude-haiku-4-5")

# What the calls have cost so far, so the bill is visible rather than inferred.
USAGE = {
    "calls": 0, "input_tokens": 0, "output_tokens": 0, "failures": 0,
    "last_error": None,
}

# $ per million tokens, for turning the counters into a number.
PRICES = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
}


def spend() -> Dict[str, object]:
    """Running total for this process, in dollars."""
    rates = PRICES.get(MODEL, PRICES["claude-haiku-4-5"])
    dollars = (
        USAGE["input_tokens"] / 1e6 * rates[0]
        + USAGE["output_tokens"] / 1e6 * rates[1]
    )
    # Probing costs nothing — the constructor only reads the environment — and
    # it is the one question worth answering here: is this switched on at all?
    configured = _client() is not None
    return dict(
        USAGE,
        model=MODEL,
        configured=configured,
        unavailable_because=None if configured else CLIENT_STATE["reason"],
        estimated_usd=round(dollars, 6),
    )

SYSTEM = """You turn a sentence about going for a run or a bike ride into search \
parameters. Reply only with the fields you are confident about; leave anything \
the sentence does not say as null. Do not invent a distance or a place.

sport: "running" or "cycling"
mode: "loop" if they want to end where they started, "route" if they name a destination
distance_km: number, in kilometres. A duration counts: running is about 10 km/h, \
cycling about 20 km/h.
elevation_gain_m: metres of climb they want. Little or no climbing is about 40; \
lots of climbing is about 450.
surface: "asphalt" for roads and cities, "trail" for dirt and paths, "mixed"
sights: "monuments" for historic things, "nature" for parks and greenery
start_text: the place they set off from, as written. Only a name you could
find on a map — a town, a district, a named park, a street, a landmark. Never a
description of where they want to be, like "fuori citta", "out of town", "in
città", "vicino a casa", "somewhere green". Those say what kind of route they
want, not where it starts; leave the field null.
end_text: the place they are heading to, if they name one. The same rule
applies: a name, never a description.

The sentence may be Italian or English."""


# Why the model path is unavailable, if it is. A feature that silently degrades
# to the rules looks identical to one that is working, which is exactly the
# thing you cannot afford not to know after a deploy.
CLIENT_STATE = {"reason": "not yet checked"}


def _client():
    """None when no credentials are configured, rather than raising."""
    try:
        import anthropic
    except ImportError:
        CLIENT_STATE["reason"] = "the anthropic package is not installed"
        return None
    # An identity-linked key is not scoped to a workspace, so the API cannot
    # tell which one to bill and rejects the call until you name it. A key made
    # inside a workspace carries its own and needs nothing here.
    workspace = (os.environ.get("ANTHROPIC_WORKSPACE_ID") or "").strip()
    try:
        client = anthropic.Anthropic(
            default_headers={"anthropic-workspace-id": workspace} if workspace else None
        )
    except Exception as exc:
        CLIENT_STATE["reason"] = str(exc) or "the Anthropic client could not be built"
        return None
    # The constructor accepts a missing key without complaint and only fails at
    # request time — which turns "no key" into a silent per-call failure rather
    # than a state you can see. Ask it up front.
    if not (getattr(client, "api_key", None) or getattr(client, "auth_token", None)):
        CLIENT_STATE["reason"] = "ANTHROPIC_API_KEY is not set"
        return None
    CLIENT_STATE["reason"] = None
    return client


def is_thin(intent: Intent) -> bool:
    """True when the rules found too little to act on.

    A distance or a place is enough to build a search from; without either,
    the sentence is worth spending a model call on.
    """
    return intent.distance_km is None and not intent.start_text


def ask_model(sentence: str, client=None) -> Optional[Intent]:
    """Read the sentence with Claude. None if unavailable or it fails."""
    client = client or _client()
    if client is None:
        return None
    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM,
            messages=[{"role": "user", "content": sentence}],
            output_format=Intent,
        )
        usage = getattr(response, "usage", None)
        USAGE["calls"] += 1
        USAGE["last_error"] = None
        USAGE["input_tokens"] += getattr(usage, "input_tokens", 0) or 0
        USAGE["output_tokens"] += getattr(usage, "output_tokens", 0) or 0
        return response.parsed_output
    except Exception as exc:
        # A swallowed failure is indistinguishable from a sentence the rules
        # simply handled, so keep the last one where healthz can show it.
        USAGE["failures"] += 1
        USAGE["last_error"] = "{}: {}".format(type(exc).__name__, exc)[:300]
        return None


# Phrasings converge hard: "voglio correre 10 km" is not a sentence one person
# writes, it is the sentence. Caching on the normalised text means the head of
# that distribution is read once and never paid for again — and normalisation
# already folds case, accents and spacing together, so "Voglio Correre 10 KM"
# is the same key.
_CACHE = TTLCache(2000, 7 * 24 * 3600)
CACHE_STATS = {"hits": 0, "misses": 0}

# Memory is fast but forgets on every deploy — which is exactly when a launch
# is busiest. Disk keeps it, and keeps the record of what the rules missed.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_PATH = os.environ.get("INTENT_DB") or os.path.join(_ROOT, "data", "intents.sqlite")
STORE = IntentStore(STORE_PATH)


def cache_stats() -> Dict[str, object]:
    total = CACHE_STATS["hits"] + CACHE_STATS["misses"]
    return dict(
        CACHE_STATS,
        hit_rate=round(CACHE_STATS["hits"] / total, 3) if total else None,
        store=STORE.stats(),
    )


# A cached answer is only as good as the prompt and model that produced it, so
# the key carries a fingerprint of both. Editing the prompt to fix a bad reading
# and then being served the bad reading from disk is a trap worth designing out.
PARSER_VERSION = hashlib.sha256(
    (SYSTEM + "\x00" + MODEL).encode("utf-8")
).hexdigest()[:8]


def cache_key(sentence: str) -> str:
    return PARSER_VERSION + ":" + normalise(sentence)


def interpret(sentence: str, client=None, allow_model: bool = True) -> Intent:
    """Rules first, then the model for the gaps.

    The rules are free and instant and settle most sentences, so the model is
    asked only when they came back thin — and even then it only fills fields
    the rules left empty, because a deterministic match is worth more than a
    guess.
    """
    key = cache_key(sentence)
    cached = _CACHE.get(key)
    if cached is not None:
        CACHE_STATS["hits"] += 1
        return cached

    stored = STORE.get(key)
    if stored is not None:
        CACHE_STATS["hits"] += 1
        answer = Intent(**stored)
        _CACHE.set(key, answer)          # promote it, so the next read is free
        return answer
    CACHE_STATS["misses"] += 1

    intent = read(sentence)
    if not allow_model or not is_thin(intent):
        _CACHE.set(key, intent)
        STORE.put(key, sentence, intent.model_dump(), rules_failed=False)
        return intent

    guess = ask_model(sentence, client=client)
    if guess is None:
        # Not served later — a failure is usually the credential or the
        # network, and the next request deserves a fresh attempt. But the
        # sentence is still recorded: the rules missed it, and that is the
        # list worth reading whether or not a model was there to cover.
        STORE.put(key, sentence, intent.model_dump(),
                  rules_failed=True, answered=False)
        return intent

    merged = intent.model_dump()
    for field, value in guess.model_dump().items():
        if merged.get(field) is None and value is not None:
            merged[field] = value
    answer = Intent(**merged)
    _CACHE.set(key, answer)
    # Recorded as a gap: the rules should have handled this and did not.
    STORE.put(key, sentence, answer.model_dump(), rules_failed=True)
    return answer
