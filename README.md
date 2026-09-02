# Doiamo

*Find the right route. Distance, climb, surface — and the air you breathe.*

**doiamo** — from *dove andiamo?*, "where are we going?" Pronounced
*do-ya-mo*.

Two ways to ask:

- **Loop** — *"I'm here, I want 10 km, 300 m of climb, mostly asphalt."*
- **A to B** — *"Get me from Parco Sempione to Parco Lambro"*, ranked by which
  way there is the most direct, the flattest, the furthest from traffic.

Either way the candidates are ranked by what the route is like to run or ride,
not just by its shape.

Works anywhere there is a road. Routing, weather, air quality and points of
interest are all global feeds — Milan and Rome are the **launch focus**, meaning
where the first posts go, not a boundary in the product. Nothing is gated by
region and no one is told they are out of bounds.

Note that the air-quality layer gets *stronger* outside a single city: the CAMS
grid is about 11 km, so five loops in Milan differ by well under one EAQI point,
while a 60 km ride out into the hills crosses several cells.

## Positioning

Komoot plans routes; this ranks them. The overlap is real and they are better
at most of it — surface and waytype breakdowns, round trips, A-to-B, curated
community POIs, offline navigation. What they do not do:

- **Rank alternatives against visible criteria.** They give you a route and
  describe it. This gives you five and shows the score line by line.
- **Score traffic proximity and air as first-class axes.**
- **Target a distance on an A-to-B route.** "Get me to work, but make it 15 km."
- **Treat *when* as an input.** Their answer to "route me from A to B" is the
  same at 7am Sunday and 6pm Tuesday. Every factor here — traffic, air, heat,
  wind — swings on time of day. That is the widest gap and the cheapest to
  exploit, since it needs no data that is not already in hand.

## Run it

```bash
cp .env.example .env          # then paste a free ORS key into it
./run.sh                      # http://localhost:8000
```

The only credential needed is an [OpenRouteService](https://openrouteservice.org/dev/#/signup)
key (free). Weather and air quality come from Open-Meteo, which needs no key.

```bash
./.venv/bin/python -m pytest -q     # 173 tests, no network needed
```

## Working without an API key

Routing is the only metered part of the app. Everything else — weather, air
quality, points of interest, place search — runs on free keyless APIs. So the
whole results page can be worked on with the routing faked:

```bash
./dev.sh          # http://127.0.0.1:8001
```

That serves the real application with `tools/offline_engine.py` in place of
openrouteservice. Routes are invented, but staircased onto a rough street grid
rather than drawn as smooth curves — a perfect circle on a map looks obviously
wrong and hides the layout problems real routes would show. Loops start and end
exactly on the point you asked for, as ORS round trips do.

Everything else on that page is genuine: live weather and air quality, real
Overpass POIs, and place search through Nominatim. Costs no quota, so it is
also the right way to develop when the daily allowance is gone.

## The two modes

|  | Loop | A to B |
|---|---|---|
| What the router is asked | ~12 round trips of the target length, varying seed and waypoint count | one call, returning up to 3 alternatives |
| Distance | the constraint you set | optional. Leave it on *as direct as possible* and you get the direct alternatives; set one and it becomes a **detour search** |
| Climb | optional target; dropped from the weights if you don't set one | optional target, otherwise scored against the other candidates — the flattest way there wins |
| Cost per search | ~12 routing calls | 1 direct, 8 with a distance target |

**The detour search** is how *"get me to work, but make it 15 km"* works. Every
point on an ellipse with foci A and B has `|AV| + |VB|` equal to the major axis,
so routing through one lengthens the trip by a predictable amount. Sampling
around that ellipse gives detours of about the right size pointing in different
directions — the A-to-B answer to the loop search's seeds. `test_route_mode.py`
pins the geometry to within 60 m.

A target distance is scored identically in both modes, and so is a target
climb; what changes is only whether there *is* one.

Start and finish are typed as place names, dropped on the map with the picker,
or taken from the browser's location. Typing in a field clears the point it had resolved to, so the text and
the coordinates can never disagree.

## How a search works

1. Ask the router for ~12 loops from the start point, varying both the seed and
   the waypoint count so the candidates differ in shape, not just in direction.
2. Measure each one: real distance, smoothed elevation gain, surface mix,
   road-class mix, headwind share.
3. Score them against the request and against live conditions.
4. Drop near-duplicates, return the best five, hand back GPX.

## What the score is made of

Weights differ by sport, because the sports differ. A cyclist weights traffic
proximity far more than a runner; a runner weights surface, because that is
impact load on the legs.

Loop mode:

| Factor | Running | Cycling | Varies between routes? |
|---|---|---|---|
| Distance match | 0.20 | 0.18 | yes |
| Climb match | 0.20 | 0.18 | yes |
| Surface match | 0.25 | 0.18 | yes |
| Traffic distance | 0.20 | 0.31 | yes |
| Headwind | 0.05 | 0.10 | yes |
| Air quality | 0.10 | 0.05 | **usually not — see below** |

A-to-B mode, where distance and climb are relative rather than targets:

| Factor | Running | Cycling |
|---|---|---|
| Directness | 0.22 | 0.20 |
| Less climb | 0.13 | 0.13 |
| Surface match | 0.25 | 0.16 |
| Traffic distance | 0.25 | 0.36 |
| Headwind | 0.05 | 0.10 |
| Air quality | 0.10 | 0.05 |

These are hand-tuned starting points. They are meant to be replaced the moment
there is real feedback ("this route was awful") to fit them against.

### The honest bit about air quality

The CAMS air-quality grid is about 11 km wide. A 10 km loop in Milan usually
sits inside one cell, so every candidate gets the *same* AQI and the layer
cannot separate them.

So the app measures whether it varies before it uses it. Air quality only enters
the ranking when the spread across candidates clears a threshold; otherwise it
is shown as city-wide context and the weight is redistributed. The UI says which
of the two happened.

What *does* vary street by street is **traffic proximity**, derived from the OSM
road class of every segment. That is the layer doing the real work of "which of
these is the healthier route", and it needs no air-quality API at all.

### Elevation gain

Summing every positive difference between consecutive SRTM samples reports
hundreds of metres of climb on flat ground, because the DEM noise floor is a
couple of metres and a 10 km route has thousands of samples. Gain here is
smoothed with a moving average, then accumulated with a 3 m hysteresis
threshold. `tests/test_geo.py` pins this: same input, naive method says 300 m+,
this one says under 20 m.

## Place search

Geocoding goes to [Photon](https://photon.komoot.io), not openrouteservice.
Photon is keyless and has no daily allowance — it throttles above roughly five
requests a second instead — and it is built for type-ahead, which matters
because autocomplete spends a request per keystroke batch. The ORS geocoder is
capped at 1000/day, which would have run out well before the routing quota did.
Moving off it leaves the whole ORS allowance for routing.

Nominatim was the other candidate and is rejected on purpose: its usage policy
is one request per second, which an autocomplete field violates immediately.

Results are deduplicated **on the visible label**, not on position — Photon
returns a large park as a node, a way and a street, sometimes hundreds of metres
apart, and three identical lines in a dropdown help nobody. The city is part of
the label, so "Colosseo, Roma" and "Colosseo, Milano" stay distinct. Repeated
prefixes are cached, which absorbs most of the typing.

## The results page

The results are the page; **the map opens on demand**. A route list is what you
read first, and on a phone a permanently visible map costs the half of the
screen the cards need. Each card carries its own map button, which opens a
full-screen view focused on that route with its points of interest.

## Map views

Two basemaps, switchable from the control on the map and remembered per
browser. Both are keyless — the good-looking styled providers (CARTO, Stadia,
Thunderforest) all gate on an API key now.

| View | Source | Why |
|---|---|---|
| **Pulita / Clean** (default) | Esri Light/Dark Gray Canvas | Land, water and road geometry and nothing else, so the route is the only loud thing |
| **Satellite** | Esri World Imagery | Shows what a road actually is — tree cover, a park's paths, whether that "track" is a farm lane |

Dark mode uses Esri's own dark canvas rather than an inverted light one.
Alternative loops are drawn dashed; over imagery they switch to white with a
dark casing, since muted ink disappears into aerial photography.

## Points of interest

A second request (`POST /api/pois`) fetches what is worth knowing about along
the candidates, from OpenStreetMap via Overpass: **water** (`drinking_water`,
`water_point` — Milan's *vedovelle*, Rome's *nasoni*), **toilets**,
**viewpoints** (`tourism=viewpoint`, `natural=peak`), **monuments**
(`historic=monument|memorial|castle|ruins|archaeological_site`,
`tourism=attraction`) and **bike repair stands**.

It is deliberately a separate call: Overpass takes several seconds, so the
routes are already on screen when the POIs arrive. One query covers every
candidate at once — Overpass's `around` filter takes a polyline — and each
result is then matched back to the routes it actually serves. A ten-kilometre
loop through central Milan turns up around 16 drinking fountains and 17
monuments.

POIs are queried as `nwr`, not `node`: a park, a palazzo or a large monument is
a way or a relation in OSM, and asking only for nodes drops about a third of
everything worth seeing.

### POIs in the score

They arrive after the routes are already on screen, so they **adjust** a score
rather than being one of its terms. The list re-orders when they land.

The distinction that matters is **universal goods versus preferences**:

- **Water is universal.** Nobody wants fewer fountains, and running dry at
  20 km is bad however good the alternatives are — so it is scored on an
  absolute saturating scale, one fountain every 3 km being full marks.
- **Monuments versus greenery is not a quality axis at all.** It is a
  destination axis: a runner heading for parks and one touring the centro
  storico want opposite routes. They are scored separately and comparatively,
  and the viewer says which they want.

An earlier version summed monuments, art, viewpoints and green into one
"scenery" figure. That gave a route with forty monuments and no trees the same
score as one with forty parks and no monuments, and served neither of the
people who asked — a composite of preferences that point in opposite directions
is a score of nothing.

A stated preference has to be worth stating, so it moves 25% of the final
figure; with sights turned off only water is left and it moves 12%. "Anything"
takes the better of the two axes rather than the average, so a route full of
parks is not marked down for having no statues.

### Where the POIs come from

Two sources, chosen per request:

| Source | When | Speed |
|---|---|---|
| **Local SQLite** | the corridor sits inside a built extract | ~0.02–0.7 s |
| **Overpass** | anywhere else | 1–13 s, and fails often |

Overpass turned out to be **96% of the time a lookup took** — not the network
(126 KB), not our code (0.2 s), but their server working a planet-scale index
on every request. That is not fixable from this side, so the launch regions are
extracted once instead:

```bash
./.venv/bin/pip install -r requirements-tools.txt          # pyosmium
mkdir -p data && cd data
curl -O https://download.geofabrik.de/europe/italy/nord-ovest-latest.osm.pbf   # Milan
curl -O https://download.geofabrik.de/europe/italy/centro-latest.osm.pbf       # Rome
cd ..
./.venv/bin/python -m tools.build_poi_db data/pois.sqlite \
    data/nord-ovest-latest.osm.pbf data/centro-latest.osm.pbf
```

`pyosmium` is in `requirements-tools.txt`, not `requirements.txt`: it needs a
build toolchain and has no business in a deployment image.

Several extracts go into one database, each keeping its own coverage. North-west
Italy alone is **139,485 POIs in 19 MB**, about 13 minutes to build. Re-run it
when you want fresher data; drinking fountains do not move weekly. `data/` is
gitignored — the database is rebuildable, not source.

**Coverage is a grid of cells that hold data, not a bounding box.** A box cannot
describe a region: the north-west extract's rectangle spans Bologna, Verona and
Parma, none of which are in it. The first version claimed all three and returned
zero POIs, silently, with no fallback. A point is now covered only if its own
~11 km cell holds data — deliberately strict, because a route through an empty
cell falling back to Overpass costs time, while claiming it costs the truth.

Measured on the same loops, before and after:

| Loop | Overpass | Local | POIs found |
|---|---|---|---|
| 2 km | 1.3 s | **0.02 s** | 217 → 224 |
| 10 km | 4.3 s | **0.10 s** | 284 → 587 |
| 25 km | 13.1 s | **0.65 s** | 232 → 755 |

More POIs as well as faster, because nothing is truncated or dropped by a busy
server.

Matching POIs to routes is grid-hashed rather than scanned: at 40 km the naive
version compared thousands of POIs against a thousand points on each of five
routes, which was ten seconds of pure arithmetic. The grid cells have to be the
match radius in *metres* on both axes — a degree of longitude is only ~70% of a
degree of latitude in Milan, and a single degree-based cell size silently
dropped POIs lying due east.

A route that runs off the edge of the extract falls back to Overpass rather
than half-answering: losing the POIs on the far side without saying so would be
worse than being slow.

### Overpass is unreliable, and that shapes the design

Measured from a laptop, the main instance answers roughly **two requests in
three**, failing with a server-side timeout rather than a rate limit. So:

- **Cost is `points x clauses`, not query length.** Overpass evaluates the
  `around` polyline once per clause, so the query is a single broad clause and
  `classify()` filters the superset locally. Eleven clauses over a 90-point
  corridor was 17 KB and timed out every time; one clause over a thinned
  corridor is ~3 KB and returns in 5 s.
- **The corridor is sampled by distance, not by a fixed count.** It is a string
  of circles along the route, and if the spacing exceeds twice the radius the
  gaps between them are never searched. A fixed 30 points per route put 75 m
  circles 300 m apart on a 10 km loop — half of it was invisible.
- **Retries share one 25-second budget.** Retrying makes failure rarer but
  slower, and the two multiply: three rounds over two mirrors at 25s each was
  150 seconds of someone watching a spinner. Attempts now run until the budget
  is spent, alternating mirrors.
- **Successful results are cached**, so a retry or a reload does not re-roll the
  dice. Empty successes are not cached.
- **Regional instances are excluded.** `overpass.osm.ch` answers every request
  but holds only Swiss data, so an Italian query gets a cheerful, empty, wrong
  answer — worse than an error.
- `406` is the answer to a default library user agent, so the client sends a
  real one; and a failure returns `available: false` (or `expired: true` when
  the route cache has gone), which the UI now says out loud instead of showing
  an empty map.

## Layout

```
backend/
  main.py          FastAPI: /api/search, /api/pois, /api/geocode, /api/gpx/{id}, /api/options
  geocoding.py     Photon place search — keyless, no daily allowance
  poi.py           Overpass lookup for water, toilets, viewpoints, monuments
  candidates.py    generation, filtering, dedupe, multi-objective scoring
  geo.py           distance, bearing, smoothed elevation gain, headwind
  health.py        Open-Meteo weather + air quality, batched by grid cell
  gpx.py           GPX 1.1 writer
  routing/
    base.py        the RoutingEngine seam
    ors.py         OpenRouteService adapter (throttled for the free tier)
web/               hero + results views, Leaflet, Italian by default, EN toggle
```

## Swapping the routing engine

The free ORS tier is 2000 directions/day. **A loop costs ~12 calls; an A-to-B
route costs 1** (8 if you set a distance target). So roughly 166 loop searches a
day, or 2000 A-to-B ones. That covers friends. It does not cover a Facebook
group, and it is easy to exhaust in an afternoon of testing.

### The search cache

Routing is the expensive, slow-changing half of a search; weather and air are
the cheap, fast-changing half. So `/api/search` caches **the router's answer**
and always recomputes the scoring against live conditions. A response says
`from_cache: true` when the geometry was reused.

The key deliberately ignores anything the router never sees — the climb target
only affects scoring, so *"10 km, 300 m up"* and *"10 km, don't care"* share one
entry. Start points are bucketed to ~110 m, so two people on opposite corners of
the same piazza do not pay twice. Sport and surface *are* in the key, because
both pick the routing profile.

A nice side effect: a spent quota stops new searches, not ones already made.

### A ceiling you set, not one you discover

`ORS_DAILY_BUDGET` (default 1800, under the free tier's 2000) caps routing
calls per UTC day. A search reserves its whole cost up front and is refused
before any call is made, so the budget cannot be overshot by a search already
in flight. The count is persisted — a process restart must not hand out an
allowance the upstream service does not agree exists — and `/api/healthz`
reports what is left.

Set it to `0` to disable the guard.

Note also that `run.sh` no longer passes `--reload` unless `DEV_RELOAD=1`.
Reload restarts on every file save, which clears the in-memory route and search
caches, so the same search pays full price again. That, rather than the app
itself, is where a day's allowance tends to go during development.

When it runs out ORS answers `403 Quota exceeded` — the same status it uses for
a bad key, so the two are told apart explicitly rather than sending you off to
check a key that is fine. Lower `CANDIDATE_SEEDS` to trade loop variety for
calls.

When it runs out, implement `RoutingEngine.round_trips` against a self-hosted
GraphHopper and point `main.py` at it. Nothing in the scoring or the API changes.
Rome and Milan OSM extracts fit comfortably on a small VPS, and self-hosting also
removes the per-request throttle, which is what currently caps how many
candidates a search can afford to consider.

## Running it locally

```bash
./run.sh                      # http://localhost:8000
```

Then check it has what it needs:

```bash
curl -s localhost:8000/api/healthz
```

- `routing_configured: true` — `ORS_API_KEY` is set
- `poi_source: "local"` with a `poi_local_count` — the POI database was found.
  `"overpass"` with a count of 0 means it was not, and everything will be slow
- `routing_budget.remaining` — calls left today

The POI database path resolves against the project root, not the working
directory, so it is found however the server is started.

## Deploying

`railpack.json` sets the start command, because the app object is at
`backend.main:app` rather than the root `main.py` that Railpack looks for by
default. `railway.json` points the health check at `/api/healthz`, which
reports whether routing is configured and whether the local POI database was
found.

Set these as environment variables on the platform — `.env` is local only:

| Variable | Why |
|---|---|
| `ORS_API_KEY` | routing. Without it `/api/search` answers a clear 503 |
| `CANDIDATE_SEEDS` | `6` halves the ORS cost of a loop search |
| `POI_DB` | path to the POI database, if it is not at `data/pois.sqlite` |

The app runs without either: no key gives a readable error, and no database
falls back to Overpass. It just runs slower.

### Getting the POI database into the container

`data/` is gitignored — 19 MB of derived data is not source, and the 576 MB
extract certainly is not. Do **not** build it during deployment: that needs the
extract, pyosmium with a build toolchain, and about thirteen minutes.

Build it locally and get the SQLite file in one of these ways:

- publish it as a release asset and `curl` it during the image build (cleanest);
- commit it, accepting 19 MB in the repository;
- upload it once to a persistent volume and point `POI_DB` at the mount.

Note that the POI database is **derived from OpenStreetMap and therefore
ODbL**, not MIT like the code. If you distribute the file, say so and attribute
OpenStreetMap.

## Licence and non-commercial use

The code is MIT (see `LICENSE`).

**As it stands today**, the hosted service is free: no accounts, no advertising,
no payment, no revenue of any kind. That is a description of the present state,
not a forecast — where this ends up is genuinely open.

It matters because the public [openrouteservice](https://openrouteservice.org)
API is used under a plan conditional on non-commercial use. So the commitment
that can honestly be made is about what happens *if* that changes, rather than
about whether it will:

- If the service starts earning money in any form, the routing moves off the
  public API first — to a self-hosted openrouteservice or GraphHopper instance
  over OpenStreetMap data. ODbL permits commercial use, with share-alike on
  derived databases, so there is no conflict.
- If that day comes, HeiGIT get told rather than left to notice.

`RoutingEngine` in `backend/routing/base.py` is the seam that makes the first
of those cheap: three methods, and nothing above it knows which engine is
underneath. Keeping that swap cheap is the practical version of the promise.

## Disclaimer

**Routes are generated automatically and are not checked by a human. Look at
one before you follow it.**

- Roads and paths come from OpenStreetMap and may be wrong, out of date,
  closed, private, or unsafe to run or ride on. The scoring measures the map,
  not reality.
- "Away from traffic" is inferred from the road class in OpenStreetMap. It is
  not a safety rating, and it says nothing about how anyone is driving today.
- Air quality is modelled on a grid roughly 11 km wide — it is a regional
  figure, not a measurement of the air on your street.
- Elevation comes from a digital elevation model and is approximate.
- Points of interest, **drinking water included**, come from OpenStreetMap
  contributors. A fountain shown here may not exist, may be dry, switched off,
  or not drinkable. Do not plan your hydration on it. Carry water.
- Nothing here is medical, fitness or safety advice. "Healthy" refers to how a
  route scores against the factors listed above, and nothing more.
- You are responsible for your own safety, for obeying traffic law, and for
  judging whether a route suits your ability and the conditions on the day.

Provided as is, without warranty of any kind, as set out in `LICENSE`.

## Data

OpenStreetMap contributors (ODbL), OpenRouteService, Open-Meteo.
