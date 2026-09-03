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
import io
import os
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel

from . import config
from .cache import TTLCache
from .intent_store import IntentStore

# What a hill preference means as a number, in metres of climb.
# "Un po' di dislivello" is not a number, it is a feeling, and the same words
# mean different climbs on foot and on a bike: 150 m is a hilly hour's run and
# barely a ripple over 40 km of riding. These are the running figures, kept as
# the module's defaults because the sport is often not stated.
FLAT_TARGET_M = 40.0
MILD_TARGET_M = 150.0
HILLY_TARGET_M = 600.0   # a long run's climb, from the calibration below

CLIMB_TARGETS = {
    "running": {"flat": FLAT_TARGET_M, "mild": MILD_TARGET_M, "hilly": HILLY_TARGET_M},
    "cycling": {"flat": 80.0, "mild": 500.0, "hilly": 1200.0},
}

# "Un giro corto" is a real request and a useless one until you know who is
# asking: 30 km is a short ride and 5 km is a short run, and both double for
# someone who trains. Calibrated from a rider's own figures rather than
# invented — note the internal check that falls out of them, that a trained
# person's "medium" lands on an untrained person's "long".
#
# Each cell is (distance_km, climb_low_m, climb_high_m); the target taken is
# the middle of the climb range.
RIDE_SIZES = {
    "cycling": {
        "normal":  {"small": (30.0, 300.0, 500.0),
                    "medium": (60.0, 500.0, 700.0),
                    "large": (100.0, 900.0, 1100.0)},
        "trained": {"small": (70.0, 500.0, 700.0),
                    "medium": (100.0, 700.0, 1000.0),
                    # Both figures are open-ended ("more than"); these are the
                    # smallest numbers that honour the ">".
                    "large": (130.0, 1100.0, 1500.0)},
    },
    "running": {
        "normal":  {"small": (5.0, 80.0, 120.0),
                    "medium": (15.0, 150.0, 300.0),
                    "large": (20.0, 500.0, 700.0)},
        "trained": {"small": (10.0, 130.0, 170.0),
                    "medium": (22.0, 500.0, 700.0),
                    "large": (40.0, 900.0, 1100.0)},
    },
}

# "lungo il fiume" is "along the river", not a long ride — the article after it
# is what separates the two, and getting this wrong would turn every riverside
# route into a century.
SIZE_WORDS = {
    "small": [r"\bcort[oaie]\b", r"\bbrev[ei]\b", r"\bpiccol[oaie]\b",
              r"\bgiretto\b", r"\bcorsetta\b", r"\bpedalatina\b",
              r"\bsgranchir\w*\b", r"\bshort\b", r"\bquick\b",
              r"\blittle\b", r"\beasy\b"],
    "medium": [r"\bmedi[oa]\b", r"\bnormale\b", r"\bmedium\b",
               r"\bmoderate\b", r"\bdecent\b"],
    # \b closes the alternation too: without it the "i" branch matched the "i"
    # of "in", and "un giro lungo in bici" stopped being a long ride.
    "large": [r"\blung[oaie]\b(?!\s+(?:il|lo|la|i|gli|le|un|una)\b|\s+l')",
              r"\bgrande\b", r"\bimpegnativ[oaie]\b", r"\btost[oaie]\b",
              r"\blong\b(?!\s+(?:the|a|an)\b)", r"\bbig\b", r"\btough\b",
              r"\bchallenging\b", r"\bhard\b"],
}

# Said outright, it changes every number above. Negatives are tested first,
# because "non sono allenato" contains "allenato".
UNTRAINED_PATTERNS = [
    r"\b(?:non|poco|per niente)\s+(?:\S+\s+){0,2}?allenat[oai]\b",
    r"\bprincipiant[ei]\b", r"\bfuori forma\b", r"\bsedentari[oa]\b",
    r"\bbeginner\b", r"\bunfit\b", r"\bout of shape\b",
    r"\bnot\s+(?:\S+\s+){0,2}?(?:fit|trained)\b",
]
TRAINED_PATTERNS = [
    r"\ballenat[oai]\b", r"\bin forma\b", r"\batlet[ai]\b",
    r"\besperto\b", r"\bagonist\w*\b",
    r"\btrained\b", r"\bfit\b", r"\bexperienced\b", r"\bathletic\b",
]

# Staying in town is about where the route goes, not what it is paved with.
# Read as a surface preference it only set "asfalto", which was already the
# default — which is why adding the phrase to a sentence changed nothing.
# Negatives first: "fuori citta" contains "citta".
NOT_URBAN_PATTERNS = [
    r"\bfuori\s+citt", r"\blontano\s+dalla\s+citt", r"\bfuori\s+dal\s+centro\b",
    r"\bin\s+campagna\b", r"\bfuori\s+porta\b",
    r"\bout\s+of\s+(?:the\s+)?(?:town|city)\b",
    r"\baway\s+from\s+(?:the\s+)?city\b", r"\bcountryside\b",
]
URBAN_PATTERNS = [
    r"\b(?:rimanere|restare|resto|rimango|stare|sto)\s+(?:\S+\s+){0,2}?citt",
    r"\bin\s+citt", r"\bnon\s+uscire\s+dalla\s+citt",
    r"\bstay(?:ing)?\s+(?:\S+\s+){0,2}?(?:in|inside)\s+(?:the\s+)?(?:city|town)\b",
    r"\bin\s+(?:the\s+)?(?:city|town)\b", r"\burban\b",
]

# The middle of town, which is a sharper thing than the town. Tested before the
# urban patterns, because "in centro citta" is both and the narrower one wins.
CENTRE_PATTERNS = [
    r"\bcentro\s+storico\b", r"\bin\s+centro\b", r"\bnel\s+centro\b",
    r"\bper\s+il\s+centro\b", r"\bcentro\s+citt", r"\bcitta\s+vecchia\b",
    r"\bdowntown\b", r"\bold\s+town\b", r"\bcity\s+cent(?:re|er)\b",
    r"\btown\s+cent(?:re|er)\b", r"\bhistoric(?:al)?\s+cent(?:re|er)\b",
]

# Rough pace, for turning "un'ora" into a distance.
KMH = {"running": config.KMH_RUNNING, "cycling": config.KMH_CYCLING}

DEFAULT_SPORT = "running"

# Small numbers get spelled out far more often than large ones.
NUMBER_WORDS = {
    "mezza": 0.5, "mezz": 0.5, "un": 1, "una": 1, "due": 2, "tre": 3,
    "quattro": 4, "cinque": 5, "sei": 6,
    "half": 0.5, "one": 1, "an": 1, "a": 1, "two": 2, "three": 3, "four": 4,
}


class Intent(BaseModel):
    area: Optional[str] = None
    calories: Optional[float] = None
    mass_kg: Optional[float] = None
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
                "ciclismo", "in sella", "pedalatina", "bike", "cycling", "cycle", "ride",
                "riding"],
    "running": ["corsetta", "correre", "correndo", "corsa", "corro", "corri", "corriamo",
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
# \b on both sides is load-bearing: without it "no" matched inside "intorno"
# and read "intorno a L'Aquila con un po' di dislivello" as avoiding climb.
CLIMB_NOUNS = r"\b(?:dislivello|salit[ae]|climb(?:ing)?|elevation|hills?)\b"
LOW_QUALIFIERS = r"\b(?:non|senza|niente|poc[oah]|meno|evita\w*|no|little|avoid\w*|without|not)\b"
HIGH_QUALIFIERS = r"\b(?:molto|molta|tanto|tanta|parecchi[oa]|con|lots? of|plenty|much)\b"
# "un po' di dislivello" asks for some climb, not for none and not for 450 m.
MILD_QUALIFIERS = r"\b(?:un po|un poco|qualche|leggero|leggera|moderato|moderata|a bit of|a little|some|slight)\b"

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


def _calories(text: str) -> Optional[float]:
    """"bruciare 400 calorie". A number that is not attached to the word is
    not a calorie count — 400 on its own is far more likely to be metres."""
    match = re.search(
        r"(\d{2,5})\s*(?:kcal|cal\b|calorie|caloria|calories|chilocalorie)", text
    )
    if not match:
        match = re.search(
            r"(?:brucia\w*|consuma\w*|burn(?:ing)?|smaltire)\s+(?:\S+\s+){0,2}?(\d{2,5})",
            text,
        )
    if not match:
        return None
    value = float(match.group(1))
    # A run that burns 30 kcal or 20000 is not a run.
    return value if 50.0 <= value <= 10000.0 else None


def _mass(text: str) -> Optional[float]:
    """"peso 82 kg". Only where it is said outright — guessing someone's body
    mass from anything else would be both wrong and rude."""
    match = re.search(r"(\d{2,3})\s*(?:kg|chil[io]|kilos?|kilograms?)\b", text)
    if not match:
        match = re.search(r"\b(?:peso|pesa|weigh|weighing)\s+(\d{2,3})\b", text)
    if not match:
        return None
    value = float(match.group(1))
    return value if 30.0 <= value <= 250.0 else None


def _fitness(text: str) -> str:
    """"normal" unless they said otherwise. Negatives win: "non sono allenato"
    contains the word that would otherwise mean the opposite."""
    for pattern in UNTRAINED_PATTERNS:
        if re.search(pattern, text):
            return "normal"
    for pattern in TRAINED_PATTERNS:
        if re.search(pattern, text):
            return "trained"
    return "normal"


def _area(text: str) -> Optional[str]:
    """"centre", "urban", or None when they said nothing — and None, not
    either of them, when they asked for the opposite."""
    for pattern in NOT_URBAN_PATTERNS:
        if re.search(pattern, text):
            return None
    for pattern in CENTRE_PATTERNS:
        if re.search(pattern, text):
            return "centre"
    for pattern in URBAN_PATTERNS:
        if re.search(pattern, text):
            return "urban"
    return None


def _size(text: str) -> Optional[str]:
    """Which of small/medium/large they asked for, if any — earliest wins."""
    best, best_at = None, len(text) + 1
    for size, patterns in SIZE_WORDS.items():
        for pattern in patterns:
            match = re.search(pattern, text)
            if match and match.start() < best_at:
                best, best_at = size, match.start()
    return best


def _climb(text: str, sport: Optional[str] = None) -> Optional[float]:
    """How the person feels about hills, as a target in metres."""
    targets = CLIMB_TARGETS.get(sport or "running", CLIMB_TARGETS["running"])
    for word in FLAT_WORDS:
        if word in text:
            return targets["flat"]
    for word in HILLY_WORDS:
        if word in text:
            return targets["hilly"]

    # Up to six words may sit between the qualifier and the noun:
    # "non voglio fare troppo dislivello".
    gap = r"(?:\s+\S+){0,6}?\s+"
    # Mild first: "un po' di dislivello" also matches HIGH via "con", and the
    # more specific reading is the right one.
    if re.search(MILD_QUALIFIERS + gap + CLIMB_NOUNS, text):
        return targets["mild"]
    if re.search(LOW_QUALIFIERS + gap + CLIMB_NOUNS, text):
        return targets["flat"]
    if re.search(HIGH_QUALIFIERS + gap + CLIMB_NOUNS, text):
        return targets["hilly"]
    return None


def _places(text: str) -> Dict[str, Optional[str]]:
    """Pull a start and a destination out, conservatively.

    A wrong guess is worse than none: it sends the router somewhere the person
    never mentioned. So only explicit patterns count, and generic words are
    rejected.
    """
    def clean(value: str) -> Optional[str]:
        value = value.strip(" ,.;:!?").strip()
        # "starting from THE fontana di trevi" — the article is ours, not part
        # of the name, and Photon returns nothing at all for "the fontana di
        # trevi" while "fontana di trevi" finds it immediately.
        value = re.sub(r"^(?:the|il|lo|la|l'|i|gli|le|un|una|uno)\s+", "", value)
        value = value.strip(" ,.;:!?").strip()
        if not value or value in NOT_A_PLACE or len(value) < 3:
            return None
        return value

    # Italian glues the article to the preposition — da, dal, dalla, dallo,
    # dall'. Matching only bare "da" turned "partendo dal Parco Sempione" into
    # the place name "l parco sempione", which geocodes to nothing; "dal
    # Colosseo" only survived because Photon is forgiving.
    # The separator is part of the token: "dall'Arco" has no space after the
    # apostrophe, so a pattern expecting one misses it entirely.
    da = r"(?:d(?:a|al|alla|allo|ai|agli|alle)\s+|dall'\s*)"
    a_to = (r"(?:fino\s+a(?:l|lla|llo)?\s+|verso(?:\s+i[l]?|\s+la)?\s+"
            r"|a(?:l|lla|llo|i|gli|lle)?\s+|all'\s*)")

    tail = r"(?:$|,|\.| e | in | con | ma | per )"
    pair = re.search(
        r"\b" + da + r"(?:casa |qui )?(.+?)\s+" + a_to + r"(.+?)" + tail, text
    )
    if pair:
        start, end = clean(pair.group(1)), clean(pair.group(2))
        if start and end:
            return {"start_text": start, "end_text": end}

    pair = re.search(r"\bfrom (.+?) to (.+?)(?:$|,|\.| and | on | with )", text)
    if pair:
        start, end = clean(pair.group(1)), clean(pair.group(2))
        if start and end:
            return {"start_text": start, "end_text": end}

    # " at " ends a place name as surely as " in " does: without it, "starting
    # from the fontana di trevi at rome" was captured whole and geocoded to
    # nothing. The city is not lost — it is what the search is biased towards.
    stop = (r"(?:$|,|\.| e | tra | fra | con | ma | per | in "
            r"| and | with | among | through | at | for )")
    start = None
    for pattern in (r"\bparto\s+" + da + r"(.+?)" + stop,
                    r"\bpartendo\s+" + da + r"(.+?)" + stop,
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
    return read_detailed(sentence)[0]


def read_detailed(sentence: str) -> Tuple[Intent, bool]:
    """The parsed sentence, and whether its distance came from the size table
    rather than from the sentence itself.

    The difference decides whether the model is worth asking, so it cannot be
    thrown away: see is_thin.
    """
    text = normalise(sentence)
    if not text:
        return Intent(), False

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
    distance = _distance(rest, sport)
    elevation = _climb(rest, sport)

    # "Un giro corto" carries both a distance and a climb, but only for someone
    # who did not give either outright. A number they typed always wins, and so
    # does an explicit "senza salite" — the size word is the weakest signal in
    # the sentence, not the strongest.
    size = _size(rest)
    guessed_distance = False
    if size and (distance is None or elevation is None):
        by_sport = RIDE_SIZES.get(sport or "running", RIDE_SIZES["running"])
        km, low, high = by_sport[_fitness(rest)][size]
        if distance is None:
            distance = km
            guessed_distance = True
        if elevation is None:
            elevation = round((low + high) / 2.0)

    mode = None
    if places["end_text"]:
        mode = "route"
    elif any(word in text for word in LOOP_WORDS):
        mode = "loop"

    return Intent(
        area=_area(rest),
        calories=_calories(rest),
        mass_kg=_mass(rest),
        sport=sport,
        mode=mode,
        distance_km=distance,
        elevation_gain_m=elevation,
        surface=surface,
        sights=sights,
        start_text=places["start_text"],
        end_text=places["end_text"],
    ), guessed_distance


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
        # Three tiers, because the chip is what proves we read the sentence:
        # calling 150 m "molto dislivello" reads as a misunderstanding.
        gain = intent.elevation_gain_m
        tiers = CLIMB_TARGETS.get(intent.sport or "running", CLIMB_TARGETS["running"])
        # Banded against what counts as a lot for this sport, not against the
        # "some" target: a medium ride's 600 m sits well above cycling's "un
        # po'" figure of 500 and calling it "molto dislivello" overstates it.
        if gain <= tiers["flat"]:
            out.append("poco dislivello" if it else "little climbing")
        elif gain < tiers["hilly"] * 0.75:
            out.append("un po' di dislivello" if it else "some climbing")
        else:
            out.append("molto dislivello" if it else "lots of climbing")
    if intent.calories:
        # What they asked for. The kilometres below are our arithmetic, not
        # their request, and showing only those would hide the substitution.
        out.append("{:.0f} kcal".format(intent.calories))
    if intent.area == "centre":
        out.append("in centro" if it else "in the centre")
    elif intent.area == "urban":
        out.append("in citta" if it else "in town")
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
elevation_gain_m: metres of climb they want. The same words mean different \
climbs by sport: running, little or none is about 40, some is 150, lots is 450; \
cycling, little or none is about 80, some is 500, lots is 1200.

If they ask for a short, medium or long outing without giving numbers, use this \
table. The second figure in each pair is for someone who says they are trained.

cycling  short  30 km / 400 m    trained  70 km / 600 m
cycling  medium 60 km / 600 m    trained 100 km / 850 m
cycling  long  100 km / 1000 m   trained 130 km / 1300 m
running  short   5 km / 100 m    trained  10 km / 150 m
running  medium 15 km / 225 m    trained  22 km / 600 m
running  long   20 km / 600 m    trained  40 km / 1000 m

A number they gave always beats the table, and so does an explicit request \
about hills.
surface: "asphalt" for roads and cities, "trail" for dirt and paths, "mixed"
sights: "monuments" for historic things, "nature" for parks and greenery
area: "centre" if they ask for the middle of town, the centro storico or \
downtown; "urban" if they ask to stay in town generally. This is about where \
the route goes, not what it is paved with. Leave it null if they say nothing, \
and null — never either value — if they want to get out of town.
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


def is_thin(intent: Intent, guessed_distance: bool = False) -> bool:
    """True when the rules found too little to act on.

    A distance or a place is enough to build a search from; without either,
    the sentence is worth spending a model call on.
    """
    # A size word fills a distance in from the calibration table, which is our
    # default rather than something they said. Counting it as knowledge is what
    # stopped "una corsetta intorno a L'Aquila" from ever reaching the model —
    # and the place went with it, so the route came back around Milan.
    if guessed_distance and not intent.start_text:
        return True
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


def _parser_fingerprint() -> str:
    """What produced a cached answer: the prompt, the model, and the rules.

    The rules are half the parser, so hashing only the prompt left a fixed
    regex still serving its old reading from disk. Hashing this module's own
    source catches every rule change — at the cost of also invalidating on a
    comment edit, which is the cheap side of the trade: a miss costs one call,
    a stale hit costs a wrong answer that never expires.
    """
    parts = [SYSTEM, MODEL]
    try:
        with io.open(os.path.abspath(__file__), "r", encoding="utf-8") as handle:
            parts.append(handle.read())
    except OSError:
        pass    # A fingerprint of prompt and model alone still beats none.
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:8]


PARSER_VERSION = _parser_fingerprint()


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

    intent, guessed_distance = read_detailed(sentence)
    if not allow_model or not is_thin(intent, guessed_distance):
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
