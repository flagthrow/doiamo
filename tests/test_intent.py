"""Reading a sentence into a search.

The rule reader is the primary path: free, instant, offline. These pin its
behaviour so the model is only ever asked for what it genuinely cannot do.
"""
import pytest

from backend.intent import (
    CLIMB_TARGETS,
    RIDE_SIZES,
    FLAT_TARGET_M,
    HILLY_TARGET_M,
    Intent,
    interpret,
    is_thin,
    read_detailed,
    normalise,
    read,
    summarise,
)


def parsed(sentence):
    return {k: v for k, v in read(sentence).model_dump().items() if v is not None}


# --- the sentence this was built for ---------------------------------------

def test_the_motivating_sentence():
    assert parsed(
        "voglio correre 10 km ma non voglio fare troppo dislivello "
        "e voglio rimanere in città"
    ) == {
        "sport": "running",
        "distance_km": 10.0,
        "elevation_gain_m": FLAT_TARGET_M,
        "surface": "asphalt",
    }


# --- normalisation ---------------------------------------------------------

@pytest.mark.parametrize("written", ["città", "citta'", "citta’", "CITTÀ", "Citta"])
def test_the_many_ways_people_write_citta(written):
    assert read("correre 5 km in " + written).surface == "asphalt"


def test_normalise_strips_accents_and_settles_apostrophes():
    assert normalise("Città  è   BELLA") == "citta e bella"


# --- distance --------------------------------------------------------------

@pytest.mark.parametrize("sentence,km", [
    ("corsa di 10 km", 10.0),
    ("10km di corsa", 10.0),
    ("un giro di 42,2 chilometri", 42.2),
    ("a flat 5k run", 5.0),
    ("a 21 km race", 21.0),
])
def test_distance_is_read_however_it_is_written(sentence, km):
    assert read(sentence).distance_km == km


@pytest.mark.parametrize("sentence,km", [
    ("voglio correre un ora", 10.0),          # running, ~10 km/h
    ("mezz'ora di corsa", 5.0),
    ("30 minuti di corsa", 5.0),
    ("due ore in bici", 40.0),                # cycling, ~20 km/h
])
def test_a_duration_becomes_a_distance_at_the_right_pace(sentence, km):
    assert read(sentence).distance_km == km


def test_no_distance_when_none_is_given():
    assert read("un giro tranquillo in citta").distance_km is None


# --- hills -----------------------------------------------------------------

@pytest.mark.parametrize("sentence", [
    "correre 10 km con poco dislivello",
    "10 km ma non voglio fare troppo dislivello",
    "10 km senza dislivello",
    "un anello di 8 km evitando le salite",
    "10 km pianeggianti",
    "a flat 10 km run",
    "10 km avoiding hills",
])
def test_wanting_it_flat(sentence):
    assert read(sentence).elevation_gain_m == FLAT_TARGET_M


@pytest.mark.parametrize("sentence", [
    "40 km in bici con tanta salita",
    "un giro con molto dislivello",
    "voglio andare in collina",
    "a hilly 20 km ride",
    "40 km with lots of climbing",
])
def test_wanting_the_hills(sentence):
    # The tier is the assertion, not the number: the same words mean a bigger
    # climb on a bike than on foot, so the figure follows the sport.
    parsed = read(sentence)
    tiers = CLIMB_TARGETS[parsed.sport or "running"]
    assert parsed.elevation_gain_m == tiers["hilly"]


@pytest.mark.parametrize("sentence,sport", [
    ("una corsa con un po' di dislivello", "running"),
    ("un giro in bici con un po' di dislivello", "cycling"),
])
def test_the_same_words_scale_with_the_sport(sentence, sport):
    parsed = read(sentence)
    assert parsed.sport == sport
    assert parsed.elevation_gain_m == CLIMB_TARGETS[sport]["mild"]


def test_a_bike_ride_asks_more_of_the_word_flat_than_a_run():
    assert (
        CLIMB_TARGETS["cycling"]["flat"] > CLIMB_TARGETS["running"]["flat"]
    )
    assert (
        CLIMB_TARGETS["cycling"]["hilly"] > CLIMB_TARGETS["running"]["hilly"]
    )


def test_silence_about_hills_sets_no_target():
    """A target nobody asked for would score every route against it."""
    assert read("correre 10 km in citta").elevation_gain_m is None


# --- sport, surface, sights ------------------------------------------------

@pytest.mark.parametrize("sentence,sport", [
    ("voglio correre", "running"), ("una corsa leggera", "running"),
    ("un giro in bici", "cycling"), ("pedalare un po", "cycling"),
    ("I want to run", "running"), ("a bike ride", "cycling"),
])
def test_sport(sentence, sport):
    assert read(sentence).sport == sport


@pytest.mark.parametrize("sentence,surface", [
    ("correre sull'asfalto", "asphalt"), ("restare in citta", "asphalt"),
    ("un giro sullo sterrato", "trail"), ("nei sentieri", "trail"),
    ("un percorso misto", "mixed"), ("on trails", "trail"),
])
def test_surface(sentence, surface):
    assert read(sentence).surface == surface


@pytest.mark.parametrize("sentence,sights", [
    ("correre tra i monumenti", "monuments"),
    ("un giro nel centro storico", "monuments"),
    ("correre nel verde", "nature"),
    ("a run through the parks", "nature"),
])
def test_sights(sentence, sights):
    assert read(sentence).sights == sights


# --- places ----------------------------------------------------------------

def test_a_journey_names_both_ends():
    result = read("da Parco Sempione a Porta Romana in bici")
    assert result.start_text == "parco sempione"
    assert result.end_text == "porta romana"
    assert result.mode == "route"


def test_from_and_to_in_english():
    result = read("from Parco Sempione to Porta Romana")
    assert (result.start_text, result.end_text) == ("parco sempione", "porta romana")


def test_a_starting_point_alone():
    assert read("mezz'ora di corsa vicino al Duomo").start_text == "duomo"
    assert read("parto da Porta Genova").start_text == "porta genova"


def test_a_place_name_is_not_also_a_preference():
    """"Parco Sempione" is where you set off, not a request for greenery."""
    assert read("da Parco Sempione a Porta Romana").sights is None


def test_generic_words_are_not_treated_as_places():
    """A wrong place is worse than none — it routes somewhere never mentioned."""
    for sentence in ("correre vicino alla citta", "a run near the park"):
        assert read(sentence).start_text is None


def test_a_place_does_not_swallow_the_rest_of_the_sentence():
    assert read("corsa vicino al Duomo tra i monumenti").start_text == "duomo"


# --- mode ------------------------------------------------------------------

def test_asking_to_come_back_is_a_loop():
    assert read("un anello di 8 km").mode == "loop"
    assert read("a 10 km loop").mode == "loop"


def test_naming_a_destination_is_a_route():
    assert read("da Como a Lecco in bici").mode == "route"


# --- when to spend a model call -------------------------------------------

def test_a_sentence_with_a_distance_needs_no_model():
    assert is_thin(read("correre 10 km in citta")) is False


def test_a_sentence_with_a_place_needs_no_model():
    assert is_thin(read("una corsa vicino al Duomo")) is False


def test_a_vague_sentence_is_worth_a_model_call():
    assert is_thin(read("qualcosa di tranquillo per stasera")) is True


def test_the_model_is_not_called_when_the_rules_suffice(monkeypatch):
    called = []

    def spy(sentence, client=None):
        called.append(sentence)
        return Intent(sport="cycling")

    monkeypatch.setattr("backend.intent.ask_model", spy)
    result = interpret("correre 10 km in citta")
    assert called == []
    assert result.sport == "running"      # the rules' answer, not the spy's


def test_the_model_only_fills_gaps_the_rules_left(monkeypatch):
    """A deterministic match beats a guess, so rules win where both spoke."""
    monkeypatch.setattr(
        "backend.intent.ask_model",
        lambda sentence, client=None: Intent(sport="cycling", distance_km=99.0,
                                             surface="trail"),
    )
    # Thin: the rules get the sport but no distance and no place.
    sentence = "qualcosa di tranquillo correndo"
    assert is_thin(read(sentence)) is True

    result = interpret(sentence)
    assert result.sport == "running"      # the rules matched it; they win
    assert result.distance_km == 99.0     # the rules left it empty; model fills
    assert result.surface == "trail"


def test_a_model_failure_leaves_the_rules_answer(monkeypatch):
    monkeypatch.setattr("backend.intent.ask_model", lambda s, client=None: None)
    result = interpret("qualcosa di tranquillo")
    assert result == read("qualcosa di tranquillo")


def test_nothing_is_read_from_nothing():
    assert read("") == Intent()
    assert read("   ") == Intent()


# --- what the viewer is told -----------------------------------------------

def test_the_reading_is_shown_back_in_italian():
    said = summarise(read("correre 10 km senza dislivello in citta"), "it")
    assert said == ["Corsa", "10 km", "poco dislivello", "asfalto"]


def test_the_reading_is_shown_back_in_english():
    said = summarise(read("run 10 km with lots of climbing on trails"), "en")
    assert "Running" in said and "lots of climbing" in said and "trail" in said


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A fresh store per test; the module-level one points at real data."""
    import backend.intent as module
    from backend.intent_store import IntentStore

    fresh = IntentStore(str(tmp_path / "intents.sqlite"))
    monkeypatch.setattr(module, "STORE", fresh)
    module._CACHE.clear()
    module.CACHE_STATS.update(hits=0, misses=0)
    return fresh


# --- caching ---------------------------------------------------------------

def test_the_same_sentence_is_read_once(store, monkeypatch):
    """Phrasings converge: "voglio correre 10 km" is not a sentence one person
    writes, it is the sentence. The second person to type it costs nothing."""
    import backend.intent as module

    module._CACHE.clear()
    calls = []
    monkeypatch.setattr(module, "ask_model",
                        lambda s, client=None: calls.append(s) or Intent(distance_km=7.0))

    first = module.interpret("qualcosa di tranquillo per stasera")
    second = module.interpret("qualcosa di tranquillo per stasera")

    assert first == second
    assert len(calls) == 1


def test_the_cache_key_folds_case_accents_and_spacing(store, monkeypatch):
    import backend.intent as module

    module._CACHE.clear()
    calls = []
    monkeypatch.setattr(module, "ask_model",
                        lambda s, client=None: calls.append(s) or Intent(distance_km=7.0))

    module.interpret("qualcosa di tranquillo in città")
    module.interpret("Qualcosa  di  Tranquillo in CITTA'")

    assert len(calls) == 1


def test_a_model_failure_is_not_cached(store, monkeypatch):
    """A failure is usually the credential or the network. The next request
    deserves a fresh attempt, not a cached shrug."""
    import backend.intent as module

    module._CACHE.clear()
    attempts = []
    monkeypatch.setattr(module, "ask_model",
                        lambda s, client=None: attempts.append(s) or None)

    module.interpret("consigliami qualcosa")
    module.interpret("consigliami qualcosa")

    assert len(attempts) == 2


def test_cache_stats_report_the_hit_rate(store):
    import backend.intent as module

    module._CACHE.clear()
    module.CACHE_STATS.update(hits=0, misses=0)

    module.interpret("correre 10 km", allow_model=False)
    module.interpret("correre 10 km", allow_model=False)
    module.interpret("correre 10 km", allow_model=False)

    stats = module.cache_stats()
    assert (stats["hits"], stats["misses"]) == (2, 1)
    assert stats["hit_rate"] == round(2 / 3, 3)


# --- the durable store -----------------------------------------------------

def test_a_reading_survives_the_process(store, monkeypatch):
    """In-memory forgets on every deploy, which is when a launch is busiest."""
    import backend.intent as module

    calls = []
    monkeypatch.setattr(module, "ask_model",
                        lambda s, client=None: calls.append(s) or Intent(distance_km=7.0))

    module.interpret("qualcosa di tranquillo per stasera")
    module._CACHE.clear()                       # the deploy

    again = module.interpret("qualcosa di tranquillo per stasera")
    assert again.distance_km == 7.0
    assert len(calls) == 1                      # not paid for twice


def test_the_store_records_what_the_rules_missed(store, monkeypatch):
    import backend.intent as module

    monkeypatch.setattr(module, "ask_model",
                        lambda s, client=None: Intent(distance_km=7.0))

    module.interpret("correre 10 km in citta")          # rules handled it
    module.interpret("consigliami un bel giro")         # rules gave up

    gaps = [row["sentence"] for row in store.gaps()]
    assert gaps == ["consigliami un bel giro"]


def test_gaps_are_ranked_by_how_often_they_are_asked(store, monkeypatch):
    import backend.intent as module

    monkeypatch.setattr(module, "ask_model",
                        lambda s, client=None: Intent(distance_km=7.0))

    for _ in range(3):
        module._CACHE.clear()
        module.interpret("qualcosa di bello")
    module._CACHE.clear()
    module.interpret("un giro a caso")

    gaps = store.gaps()
    assert gaps[0]["sentence"] == "qualcosa di bello"
    assert gaps[0]["hits"] > gaps[1]["hits"]


def test_the_store_counts_what_it_saved(store):
    import backend.intent as module

    module.interpret("correre 10 km", allow_model=False)
    module._CACHE.clear()
    module.interpret("correre 10 km", allow_model=False)

    stats = store.stats()
    assert stats["sentences"] == 1
    assert stats["served_from_store"] == 1


def test_an_unwritable_path_is_not_fatal(tmp_path):
    """Losing persistence is a degradation; losing the feature is not."""
    from backend.intent_store import IntentStore

    broken = IntentStore("/System/nowhere/intents.sqlite")
    assert broken.available is False
    assert broken.get("k") is None
    assert broken.gaps() == []
    broken.put("k", "s", {}, False)              # must not raise


# --- cache keying ----------------------------------------------------------

def test_cache_key_changes_with_the_rules():
    """A regex fix is worthless if the old reading is still served from disk."""
    import io as _io
    import os as _os

    from backend import intent as module

    source = _io.open(_os.path.abspath(module.__file__), encoding="utf-8").read()
    assert module.PARSER_VERSION == module.hashlib.sha256(
        "\x00".join([module.SYSTEM, module.MODEL, source]).encode("utf-8")
    ).hexdigest()[:8]


def test_cache_key_changes_with_the_prompt():
    """A fixed prompt fixes nothing if the old answer is still served."""
    import hashlib

    from backend import intent as module

    original = module.PARSER_VERSION
    before = module.cache_key("un giro tranquillo")
    try:
        module.PARSER_VERSION = hashlib.sha256(b"a different prompt").hexdigest()[:8]
        assert module.cache_key("un giro tranquillo") != before
    finally:
        module.PARSER_VERSION = original


def test_cache_key_still_folds_case_and_spacing():
    from backend import intent as module

    assert module.cache_key("Voglio Correre 10 KM") == module.cache_key(
        "  voglio  correre 10 km "
    )


# --- qualifier boundaries --------------------------------------------------

def test_no_inside_intorno_does_not_flatten_the_route():
    """The bug: "intorno" contains "no", so an unanchored LOW qualifier read
    "intorno a L'Aquila con un po' di dislivello" as avoiding climb."""
    from backend import intent as module

    text = module.normalise("una corsetta intorno a l'aquila con un po' di dislivello")
    assert module._climb(text) == module.MILD_TARGET_M


def test_climb_qualifiers_only_match_whole_words():
    from backend import intent as module

    for sentence in ("giro intorno al lago", "raccontami un giro", "sono in zona"):
        assert module._climb(module.normalise(sentence)) is None


def test_a_bit_of_climb_is_neither_flat_nor_mountainous():
    from backend import intent as module

    mild = module._climb(module.normalise("un giro con un po' di dislivello"))
    assert module.FLAT_TARGET_M < mild < module.HILLY_TARGET_M


# --- how big is "a short ride" ---------------------------------------------
# Calibrated from a rider's figures: the same word means a different outing by
# sport and by training, and 30 km is a short ride where 5 km is a short run.

@pytest.mark.parametrize("sentence,sport,km,gain", [
    ("un giro corto in bici", "cycling", 30.0, 400.0),
    ("un giro medio in bici", "cycling", 60.0, 600.0),
    ("un giro lungo in bici", "cycling", 100.0, 1000.0),
    ("una corsa corta", "running", 5.0, 100.0),
    ("una corsa media", "running", 15.0, 225.0),
    ("una corsa lunga", "running", 20.0, 600.0),
])
def test_size_words_fill_in_both_numbers(sentence, sport, km, gain):
    parsed = read(sentence)
    assert parsed.sport == sport
    assert parsed.distance_km == km
    assert parsed.elevation_gain_m == gain


@pytest.mark.parametrize("sentence,km", [
    ("un giro corto in bici, sono ben allenato", 70.0),
    ("una corsa lunga, sono allenato", 40.0),
    ("a short ride, i am well trained", 70.0),
])
def test_saying_you_are_trained_moves_every_number(sentence, km):
    assert read(sentence).distance_km == km


def test_saying_you_are_not_trained_does_not_read_as_trained():
    """"non sono allenato" contains "allenato"."""
    assert read("un giro corto in bici, non sono allenato").distance_km == 30.0
    assert read("un giro corto in bici, sono principiante").distance_km == 30.0


def test_a_number_they_gave_beats_the_table():
    parsed = read("un giro lungo in bici di 40 km")
    assert parsed.distance_km == 40.0
    assert parsed.elevation_gain_m == 1000.0      # still long, still hilly


def test_an_explicit_wish_about_hills_beats_the_table():
    parsed = read("un giro lungo in bici ma senza salite")
    assert parsed.distance_km == 100.0
    assert parsed.elevation_gain_m == CLIMB_TARGETS["cycling"]["flat"]


@pytest.mark.parametrize("sentence", [
    "un giro in bici lungo il fiume",
    "una corsa lungo l'Adige",
    "a run along the canal",
])
def test_along_the_river_is_not_a_long_ride(sentence):
    """"lungo" is both "long" and "along"; the article separates them."""
    assert read(sentence).distance_km is None


def test_a_trained_persons_medium_is_an_untrained_persons_long():
    """Falls out of the calibration, and is worth holding on to."""
    for sport in ("running", "cycling"):
        trained_medium = RIDE_SIZES[sport]["trained"]["medium"][0]
        normal_large = RIDE_SIZES[sport]["normal"]["large"][0]
        assert trained_medium >= normal_large * 0.9


def test_a_medium_rides_climb_is_not_described_as_a_lot():
    """600 m is what the calibration calls a medium ride; the chip has to
    agree with the table it came from."""
    km, low, high = RIDE_SIZES["cycling"]["normal"]["medium"]
    parsed = Intent(sport="cycling", elevation_gain_m=(low + high) / 2.0)
    assert "un po' di dislivello" in summarise(parsed, "it")


def test_asking_for_lots_of_climb_is_described_as_a_lot():
    for sport in ("running", "cycling"):
        parsed = Intent(sport=sport, elevation_gain_m=CLIMB_TARGETS[sport]["hilly"])
        assert "molto dislivello" in summarise(parsed, "it")


def test_a_size_word_alone_still_leaves_the_sentence_thin():
    """The size table supplies a distance we chose, not one they gave. Counting
    it as knowledge stopped "una corsetta intorno a L'Aquila" reaching the
    model, and the place went with it — the route came back around Milan."""
    parsed, guessed = read_detailed("una corsetta intorno a l'aquila")
    assert guessed is True
    assert is_thin(parsed, guessed) is True


def test_a_distance_they_actually_gave_is_not_thin():
    parsed, guessed = read_detailed("correre 10 km in citta")
    assert guessed is False
    assert is_thin(parsed, guessed) is False


def test_a_named_place_is_never_thin_whatever_the_distance():
    parsed, guessed = read_detailed("un giro corto da Monza a Lecco")
    assert is_thin(parsed, guessed) is False


@pytest.mark.parametrize("sentence,sport", [
    ("una corsetta qui vicino", "running"),
    ("una pedalatina tranquilla", "cycling"),
])
def test_a_diminutive_names_the_activity_as_well_as_its_size(sentence, sport):
    assert read(sentence).sport == sport
