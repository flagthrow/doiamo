"""Photon geocoding: keyless, no daily allowance, throttled instead."""
import httpx
import pytest

from backend import geocoding
from backend.geocoding import PhotonGeocoder, _label


def feature(lat, lon, **props):
    return {"geometry": {"coordinates": [lon, lat]}, "properties": props}


def transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- label building --------------------------------------------------------

def test_label_prefers_a_name():
    label, region = _label({"name": "Parco Sempione", "city": "Milano",
                            "state": "Lombardia", "country": "Italia"})
    assert label == "Parco Sempione, Milano"
    assert region == "Lombardia, Italia"


def test_label_falls_back_to_street_and_number():
    label, _ = _label({"street": "Via Torino", "housenumber": "12", "city": "Milano"})
    assert label == "Via Torino 12, Milano"


def test_label_handles_a_street_with_no_number():
    label, _ = _label({"street": "Via Torino", "city": "Milano"})
    assert label == "Via Torino, Milano"


def test_label_does_not_repeat_the_city():
    label, _ = _label({"name": "Milano", "city": "Milano", "state": "Lombardia"})
    assert label == "Milano"


def test_label_uses_county_when_there_is_no_city():
    label, _ = _label({"name": "Rifugio", "county": "Sondrio"})
    assert label == "Rifugio, Sondrio"


def test_region_is_none_when_nothing_is_known():
    _, region = _label({"name": "Somewhere"})
    assert region is None


# --- search ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_maps_photon_features_to_results():
    payload = {"features": [
        feature(45.4725, 9.1745, name="Parco Sempione", city="Milano",
                state="Lombardia", country="Italia"),
    ]}
    async with transport(lambda r: httpx.Response(200, json=payload)) as client:
        rows = await PhotonGeocoder(client=client).search("sempione")

    assert rows == [{"label": "Parco Sempione, Milano", "lat": 45.4725,
                     "lon": 9.1745, "region": "Lombardia, Italia"}]


@pytest.mark.asyncio
async def test_the_same_place_is_listed_once():
    """Photon returns a big park as a node, a way and a street, hundreds of
    metres apart. Three identical lines in a dropdown help nobody."""
    payload = {"features": [
        feature(45.4725, 9.1745, name="Parco Sempione", city="Milano"),
        feature(45.4740, 9.1760, name="Parco Sempione", city="Milano"),
        feature(45.4710, 9.1730, name="Parco Sempione", city="Milano"),
    ]}
    async with transport(lambda r: httpx.Response(200, json=payload)) as client:
        rows = await PhotonGeocoder(client=client).search("sempione")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_same_name_in_different_cities_stays_distinct():
    payload = {"features": [
        feature(41.8902, 12.4922, name="Colosseo", city="Roma"),
        feature(45.4800, 9.2000, name="Colosseo", city="Milano"),
    ]}
    async with transport(lambda r: httpx.Response(200, json=payload)) as client:
        rows = await PhotonGeocoder(client=client).search("colosseo")
    assert [r["label"] for r in rows] == ["Colosseo, Roma", "Colosseo, Milano"]


@pytest.mark.asyncio
async def test_focus_point_is_sent_when_given():
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"features": []})

    async with transport(handler) as client:
        await PhotonGeocoder(client=client).search("duomo", near=(45.46, 9.19))

    assert seen["params"]["lat"] == "45.46"
    assert seen["params"]["lon"] == "9.19"
    assert "lang" not in seen["params"]     # Photon supports only de/en/fr


@pytest.mark.asyncio
async def test_repeated_typing_is_served_from_cache():
    """Autocomplete resends prefixes constantly, especially on backspace."""
    hits = []

    def handler(request):
        hits.append(1)
        return httpx.Response(200, json={"features": [
            feature(45.47, 9.17, name="Sempione", city="Milano")]})

    async with transport(handler) as client:
        geocoder = PhotonGeocoder(client=client)
        await geocoder.search("sempione", near=(45.46, 9.19))
        await geocoder.search("Sempione", near=(45.46, 9.19))     # case
        await geocoder.search("  sempione ", near=(45.46, 9.19))  # whitespace

    assert len(hits) == 1


@pytest.mark.asyncio
async def test_a_short_query_never_reaches_the_network():
    hits = []
    async with transport(lambda r: hits.append(1) or httpx.Response(200, json={})) as client:
        assert await PhotonGeocoder(client=client).search("a") == []
    assert hits == []


@pytest.mark.asyncio
async def test_a_failure_returns_nothing_rather_than_raising():
    async with transport(lambda r: httpx.Response(503, text="busy")) as client:
        assert await PhotonGeocoder(client=client).search("duomo") == []


@pytest.mark.asyncio
async def test_features_without_coordinates_are_skipped():
    payload = {"features": [
        {"geometry": {"coordinates": []}, "properties": {"name": "Broken"}},
        feature(45.47, 9.17, name="Good", city="Milano"),
    ]}
    async with transport(lambda r: httpx.Response(200, json=payload)) as client:
        rows = await PhotonGeocoder(client=client).search("xy")
    assert [r["label"] for r in rows] == ["Good, Milano"]


# --- naming a place vs. typing near one ------------------------------------

def test_settlement_outranks_a_nearby_business_of_the_same_name():
    """Searching "L'Aquila" from Milan must reach Abruzzo, not a restaurant
    called L'Aquila d'Oro two kilometres away."""
    city = {"name": "L'Aquila", "osm_key": "place", "osm_value": "city"}
    hotel = {"name": "L'Aquila d'Oro", "osm_key": "amenity", "osm_value": "restaurant"}

    assert geocoding._tier(city, "l'aquila") > geocoding._tier(hotel, "l'aquila")


def test_tier_folds_case_accents_and_curly_apostrophes():
    feature = {"name": "L’Aquila", "osm_key": "place", "osm_value": "city"}
    assert geocoding._tier(feature, "l'aquila") == 3


def test_an_exact_match_beats_a_prefix_match():
    exact = {"name": "Monza", "osm_key": "place", "osm_value": "city"}
    prefix = {"name": "Monza e della Brianza", "osm_key": "place", "osm_value": "county"}
    assert geocoding._tier(exact, "monza") > geocoding._tier(prefix, "monza")


def test_within_a_tier_photons_own_order_wins():
    """Photon ranks by importance. Re-ordering by distance from the map centre
    sent "Colosseo" to a metro stop in Sesto San Giovanni, because the search
    happened to be biased towards Milan."""
    features = [
        {"geometry": {"coordinates": [9.24, 45.53]},
         "properties": {"name": "Colosseo", "city": "Sesto San Giovanni",
                        "osm_key": "railway", "osm_value": "station"}},
        {"geometry": {"coordinates": [12.49, 41.89]},
         "properties": {"name": "Colosseo", "city": "Roma",
                        "osm_key": "historic", "osm_value": "monument"}},
    ]
    folded = geocoding._fold("colosseo")
    # Same tier: both are exact name matches on non-settlements.
    assert geocoding._tier(features[0]["properties"], folded) == \
           geocoding._tier(features[1]["properties"], folded)
