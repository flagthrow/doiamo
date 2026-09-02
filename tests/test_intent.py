"""Reading a sentence into a search.

The rule reader is the primary path: free, instant, offline. These pin its
behaviour so the model is only ever asked for what it genuinely cannot do.
"""
import pytest

from backend.intent import (
    FLAT_TARGET_M,
    HILLY_TARGET_M,
    Intent,
    interpret,
    is_thin,
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
    assert read(sentence).elevation_gain_m == HILLY_TARGET_M


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
