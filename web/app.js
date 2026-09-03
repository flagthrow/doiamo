const state = {
  view: "home",
  mode: "route",   // both endpoints by default; the checkbox makes it a loop
  sport: stored("doiamo_sport", "running", ["running", "cycling"]),
  sportChosen: false,   // true once the person corrected the guess themselves
  // undefined = never asked; null = asked and refused. The difference is
  // what stops a refusal from re-prompting on every search.
  here: undefined,
  assumedSport: false,
  surface: "asphalt",
  sights: "both",
  area: "any",     // "urban" when the sentence asked to stay in town
  // Body mass is the biggest unknown in a calorie estimate, so it is
  // remembered once told and shown as a guess until then.
  massKg: storedNumber("doiamo_mass", 30, 250),
  massAssumed: true,
  // Which cards the reader opened, so a re-render does not fold them again.
  expanded: {},
  distanceKm: 10,
  // Whether the distance on the slider was chosen or is just our default.
  // Only a default follows the sport when the sport changes; a number somebody
  // set stays set.
  distanceChosen: false,
  // Filled from /api/options, so the domain figures live in one place.
  defaultDistance: { running: 10, cycling: 30 },
  distanceRange: { running: [2, 60], cycling: [5, 160] },
  gainM: 150,
  // Both targets are opt-in. A default climb target of 150 m is unreachable
  // in a flat city, which would score every candidate zero on that axis.
  gainAny: true,
  distanceAny: true,
  start: null,          // { lat, lon, label }
  end: null,
  activeField: "start", // which field a map click fills
  routes: [],
  activeId: null,
  pois: [],
  poiCounts: {},
  poiScores: {},
  poiFailed: false,
  poiExpired: false,
  poiKinds: [],
  monumentKinds: [],
  natureKinds: [],
  poiOff: {},   // kinds the viewer switched off
  poiLoading: false,
  mapStyle: "clean",
};

function token(name) {
  return getComputedStyle(document.body).getPropertyValue(name).trim();
}

const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

// ---------------------------------------------------------------- basemaps
// Every one of these is keyless — the styled providers that look best (CARTO,
// Stadia, Thunderforest) all gate on an API key now, and Esri's canvases are
// the only genuinely minimal ones that do not.
// Mirrors backend BIG_ROAD_WARN_SHARE: above this the card says so out loud.
const BIG_ROAD_WARN = 0.15;

const ESRI = "https://services.arcgisonline.com/ArcGIS/rest/services/";
const ESRI_CREDIT = "Esri";

const MAP_STYLES = {
  clean: {
    light: [
      { url: ESRI + "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", maxNativeZoom: 17 },
      { url: ESRI + "Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}", maxNativeZoom: 17 },
    ],
    dark: [
      { url: ESRI + "Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", maxNativeZoom: 17 },
      { url: ESRI + "Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}", maxNativeZoom: 17 },
    ],
    attribution: ESRI_CREDIT + ", HERE, Garmin, &copy; OpenStreetMap",
  },
  satellite: {
    light: [
      { url: ESRI + "World_Imagery/MapServer/tile/{z}/{y}/{x}", maxNativeZoom: 19 },
      { url: ESRI + "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", maxNativeZoom: 19 },
    ],
    attribution: ESRI_CREDIT + ", Maxar, Earthstar Geographics",
  },
};

const STYLE_ORDER = ["clean", "satellite"];

function stored(key, fallback, allowed) {
  try {
    const saved = localStorage.getItem(key);
    if (!allowed || allowed.indexOf(saved) !== -1) return saved || fallback;
  } catch (err) {
    /* private browsing */
  }
  return fallback;
}

// Milan is where this was written, not where it works. The hardcoded centre is
// only ever a first guess for someone we know nothing about; once a search has
// happened we know roughly where they are, and opening there beats opening in
// a city they may never have been to. Coarse on purpose — two decimals is
// about a kilometre, enough to frame a map and not a record of anybody's
// address.
function lastPlace() {
  const lat = storedNumber("doiamo_lat", -90, 90);
  const lon = storedNumber("doiamo_lon", -180, 180);
  return lat !== null && lon !== null ? [lat, lon] : null;
}

function rememberPlace(lat, lon) {
  remember("doiamo_lat", lat.toFixed(2));
  remember("doiamo_lon", lon.toFixed(2));
}

// localStorage only holds strings, and a body mass has to come back a number
// or every calorie estimate silently becomes NaN.
function storedNumber(key, low, high) {
  const raw = stored(key, null);
  const value = Number(raw);
  return raw && Number.isFinite(value) && value >= low && value <= high ? value : null;
}

function remember(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (err) {
    /* private browsing */
  }
}

state.mapStyle = stored("doiamo_map_style", "clean", STYLE_ORDER);

// Somewhere to stand before anyone has told us anything. Every path that
// learns better — a remembered search, a geolocation fix, a named place —
// overrides it.
const DEFAULT_CENTRE = [45.4642, 9.19];

const map = L.map("map", { zoomControl: true, attributionControl: true })
  .setView(DEFAULT_CENTRE, 13);

let basemapLayers = [];

function applyBasemap() {
  basemapLayers.forEach((layer) => map.removeLayer(layer));
  basemapLayers = [];

  const style = MAP_STYLES[state.mapStyle] || MAP_STYLES.clean;
  const specs = (darkQuery.matches && style.dark) || style.light;

  specs.forEach((spec, index) => {
    const layer = L.tileLayer(spec.url, {
      maxZoom: 19,
      maxNativeZoom: spec.maxNativeZoom,
      // Only the bottom layer carries the credit, or it is repeated per layer.
      attribution: index === 0 ? style.attribution : undefined,
    }).addTo(map);
    layer.setZIndex(index + 1);
    basemapLayers.push(layer);
  });
  document.getElementById("map").dataset.style = state.mapStyle;
}

function setMapStyle(id) {
  if (!MAP_STYLES[id]) return;
  state.mapStyle = id;
  remember("doiamo_map_style", id);
  applyBasemap();
  renderStyleSwitch();
  drawRoutes(false);
}

const StyleControl = L.Control.extend({
  options: { position: "topright" },
  onAdd: function () {
    const box = L.DomUtil.create("div", "map-styles");
    box.id = "mapStyles";
    L.DomEvent.disableClickPropagation(box);
    L.DomEvent.disableScrollPropagation(box);
    box.addEventListener("click", (e) => {
      const button = e.target.closest("button[data-style]");
      if (button) setMapStyle(button.dataset.style);
    });
    return box;
  },
});

function renderStyleSwitch() {
  const box = document.getElementById("mapStyles");
  if (!box) return;
  box.innerHTML = "";
  STYLE_ORDER.forEach((id) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.style = id;
    button.textContent = t("map_" + id);
    button.setAttribute("aria-pressed", String(id === state.mapStyle));
    box.appendChild(button);
  });
}

map.addControl(new StyleControl());
applyBasemap();

darkQuery.addEventListener("change", () => {
  applyBasemap();
  drawRoutes(false);
});


// ---------------------------------------------------------------- hero map
// The landing backdrop. Always the dark canvas regardless of theme — a cover
// that changes with the OS setting stops being an identity — and never
// interactive, so it reads as artwork rather than a map you failed to drag.
let heroMap = null;
let heroLine = null;

function heroLoopPoints(lat, lon, radiusDeg) {
  // A few harmonics so it wanders like a route instead of reading as a circle.
  const points = [];
  const lonScale = 1 / Math.cos((lat * Math.PI) / 180);
  for (let i = 0; i <= 220; i++) {
    const a = (2 * Math.PI * i) / 220;
    const r = radiusDeg * (
      1 + 0.19 * Math.sin(3 * a + 0.7) + 0.11 * Math.sin(5 * a + 2.1) + 0.05 * Math.cos(7 * a)
    );
    points.push([lat + r * Math.sin(a) * 0.72, lon + r * Math.cos(a) * lonScale]);
  }
  return points;
}

function drawHeroRoute() {
  if (!heroMap) return;
  if (heroLine) heroMap.removeLayer(heroLine);

  heroLine = L.polyline(heroLoopPoints(45.4705, 9.1830, 0.042), {
    color: "#2fd08f",
    weight: 5,
    opacity: 0.9,
    className: "hero-route",
  }).addTo(heroMap);

  const path = heroLine.getElement();
  if (!path || !path.getTotalLength) return;
  const length = path.getTotalLength();
  path.style.strokeDasharray = length;
  path.style.strokeDashoffset = length;
  // Force a reflow so re-entering the home view replays the draw.
  void path.getBoundingClientRect();
  path.style.animation = "none";
  void path.getBoundingClientRect();
  path.style.animation = "";
}

function initHeroMap() {
  const el = document.getElementById("heroMap");
  if (!el || heroMap) return;

  heroMap = L.map(el, {
    zoomControl: false,
    attributionControl: true,
    dragging: false,
    scrollWheelZoom: false,
    doubleClickZoom: false,
    touchZoom: false,
    boxZoom: false,
    keyboard: false,
    tap: false,
  }).setView(lastPlace() || DEFAULT_CENTRE, 13);

  [
    ESRI + "Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    ESRI + "Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
  ].forEach((url, index) => {
    L.tileLayer(url, {
      maxZoom: 19,
      maxNativeZoom: 17,
      attribution: index === 0 ? MAP_STYLES.clean.attribution : undefined,
    }).addTo(heroMap);
  });

  drawHeroRoute();
}

// ---------------------------------------------------------------- markers
const routeLayer = L.layerGroup().addTo(map);
const markers = { start: null, end: null };

function makeIcon(kind) {
  return L.divIcon({
    className: "",
    html: '<div class="' + kind + '-dot"></div>',
    iconSize: [16, 16],
    iconAnchor: [8, 8],
  });
}

function formatPoint(lat, lon) {
  return lat.toFixed(4) + ", " + lon.toFixed(4);
}

function setPoint(which, lat, lon, label, fly) {
  state[which] = { lat: lat, lon: lon, label: label || formatPoint(lat, lon) };

  if (markers[which]) {
    markers[which].setLatLng([lat, lon]);
  } else {
    markers[which] = L.marker([lat, lon], {
      draggable: true,
      icon: makeIcon(which),
      zIndexOffset: which === "start" ? 100 : 90,
    }).addTo(map);
    markers[which].on("dragend", () => {
      const p = markers[which].getLatLng();
      state[which] = { lat: p.lat, lon: p.lng, label: formatPoint(p.lat, p.lng) };
      syncPlaceInput(which);
    });
  }

  syncPlaceInput(which);
  if (fly) map.setView([lat, lon], Math.max(map.getZoom(), 13));
}

function clearPoint(which) {
  state[which] = null;
  if (markers[which]) {
    map.removeLayer(markers[which]);
    markers[which] = null;
  }
}

function syncPlaceInput(which) {
  const input = document.getElementById(which + "Input");
  const field = document.getElementById(which + "Field");
  if (!input) return;
  const point = state[which];
  input.value = point ? point.label : "";
  field.classList.toggle("resolved", Boolean(point));
}

map.on("click", (e) => {
  const which = state.mode === "route" ? state.activeField : "start";
  setPoint(which, e.latlng.lat, e.latlng.lng, null, false);
  updateMapHint();
});

// ---------------------------------------------------------------- geocoding
const suggestState = { start: { items: [], index: -1 }, end: { items: [], index: -1 } };
const timers = {};
const controllers = {};

function suggestBox(which) {
  return document.getElementById(which + "Suggest");
}

function hideSuggest(which) {
  const box = suggestBox(which);
  box.hidden = true;
  box.innerHTML = "";
  suggestState[which] = { items: [], index: -1 };
}

function renderSuggest(which, items, message) {
  const box = suggestBox(which);
  box.innerHTML = "";
  box.hidden = false;

  if (message) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = message;
    box.appendChild(li);
    suggestState[which] = { items: [], index: -1 };
    return;
  }

  suggestState[which] = { items: items, index: -1 };
  items.forEach((item, i) => {
    const li = document.createElement("li");
    li.setAttribute("role", "option");
    li.dataset.index = i;
    li.textContent = item.label;
    if (item.region) {
      const region = document.createElement("span");
      region.className = "region";
      region.textContent = item.region;
      li.appendChild(region);
    }
    li.addEventListener("mousedown", (e) => {
      e.preventDefault(); // keep focus so blur does not race the click
      choosePlace(which, i);
    });
    box.appendChild(li);
  });
}

function choosePlace(which, index) {
  const item = suggestState[which].items[index];
  if (!item) return;
  setPoint(which, item.lat, item.lon, item.label, true);
  hideSuggest(which);
  updateMapHint();
}

async function lookup(which, text) {
  if (controllers[which]) controllers[which].abort();
  const controller = new AbortController();
  controllers[which] = controller;

  renderSuggest(which, [], t("searching_place"));
  try {
    const centre = (state.view === "home" && heroMap ? heroMap : map).getCenter();
    const url =
      "/api/geocode?q=" + encodeURIComponent(text) +
      "&lat=" + centre.lat.toFixed(5) + "&lon=" + centre.lng.toFixed(5);
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) {
      hideSuggest(which);
      if (response.status === 503) message(t("keyMissing"), "warn");
      return;
    }
    const items = await response.json();
    if (!items.length) renderSuggest(which, [], t("noPlaces"));
    else renderSuggest(which, items);
  } catch (err) {
    if (err.name !== "AbortError") hideSuggest(which);
  }
}

function wirePlaceField(which) {
  const input = document.getElementById(which + "Input");

  input.addEventListener("focus", () => {
    state.activeField = which;
    updateMapHint();
  });

  input.addEventListener("input", () => {
    // Typing invalidates a previously picked point: the text and the
    // coordinates must never disagree.
    clearPoint(which);
    document.getElementById(which + "Field").classList.remove("resolved");

    const text = input.value.trim();
    clearTimeout(timers[which]);
    if (text.length < 2) {
      hideSuggest(which);
      return;
    }
    timers[which] = setTimeout(() => lookup(which, text), 280);
  });

  input.addEventListener("keydown", (e) => {
    const box = suggestBox(which);
    const items = suggestState[which].items;
    if (box.hidden || !items.length) {
      if (e.key === "Enter") {
        e.preventDefault();
        search();
      }
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      const next = (suggestState[which].index + step + items.length) % items.length;
      suggestState[which].index = next;
      [...box.children].forEach((li, i) =>
        li.setAttribute("aria-selected", String(i === next))
      );
    } else if (e.key === "Enter") {
      e.preventDefault();
      choosePlace(which, suggestState[which].index >= 0 ? suggestState[which].index : 0);
    } else if (e.key === "Escape") {
      hideSuggest(which);
    }
  });

  input.addEventListener("blur", () => setTimeout(() => hideSuggest(which), 120));
}


// ---------------------------------------------------------------- picker
// Dropping a pin needs a map, but the home view has no usable one — so the
// picker brings its own, built the first time it is opened.
let pickerMap = null;
let pickerFor = "start";

function openPicker(which) {
  pickerFor = which;
  const modal = document.getElementById("picker");
  document.getElementById("pickerTitle").textContent =
    which === "end" ? t("pickEndTitle") : t("pickStartTitle");
  modal.hidden = false;

  const existing = state[which] || state.start;
  const centre = existing
    ? [existing.lat, existing.lon]
    : [map.getCenter().lat, map.getCenter().lng];

  if (!pickerMap) {
    pickerMap = L.map("pickerMap", { zoomControl: true, attributionControl: true })
      .setView(centre, 15);
    const style = MAP_STYLES.clean;
    const specs = (darkQuery.matches && style.dark) || style.light;
    specs.forEach((spec, index) => {
      L.tileLayer(spec.url, {
        maxZoom: 19,
        maxNativeZoom: spec.maxNativeZoom,
        attribution: index === 0 ? style.attribution : undefined,
      }).addTo(pickerMap);
    });
    pickerMap.on("move", updatePickerCoords);
  } else {
    pickerMap.setView(centre, Math.max(pickerMap.getZoom(), 15));
  }

  // The map was display:none until a moment ago, so Leaflet has stale sizing.
  setTimeout(() => {
    pickerMap.invalidateSize({ animate: false });
    updatePickerCoords();
  }, 30);
}

function updatePickerCoords() {
  if (!pickerMap) return;
  const c = pickerMap.getCenter();
  document.getElementById("pickerCoords").textContent = formatPoint(c.lat, c.lng);
}

function closePicker() {
  document.getElementById("picker").hidden = true;
}

function confirmPicker() {
  if (pickerMap) {
    const c = pickerMap.getCenter();
    setPoint(pickerFor, c.lat, c.lng, null, state.view === "app");
  }
  closePicker();
}

// ---------------------------------------------------------------- controls
function paintSlider(input) {
  const min = Number(input.min);
  const max = Number(input.max);
  const pct = ((Number(input.value) - min) / (max - min)) * 100;
  input.style.setProperty("--fill", pct + "%");
}

function wireSegmented(id, key, onChange) {
  document.getElementById(id).addEventListener("click", (e) => {
    const button = e.target.closest("button[data-value]");
    if (!button) return;
    state[key] = button.dataset.value;
    button.parentElement.querySelectorAll("button").forEach((b) => {
      b.setAttribute("aria-pressed", String(b === button));
    });
    if (onChange) onChange();
  });
}

function applyMode() {
  document.body.dataset.mode = state.mode;
  document.getElementById("endField").hidden = state.mode !== "route";
  if (state.mode === "loop") {
    clearPoint("end");
    state.activeField = "start";
  }
  syncDistanceEnabled();
  updateMapHint();
}

function syncDistanceEnabled() {
  // A loop always needs a length. A route only takes one when you want a
  // detour rather than the direct way.
  const off = state.mode === "route" && state.distanceAny;
  document.getElementById("distance").disabled = off;
}

function updateMapHint() {
  const hint = document.getElementById("mapHint");
  if (state.view !== "app") {
    hint.hidden = true;
    return;
  }
  hint.hidden = false;
  hint.textContent =
    state.mode === "route" && state.activeField === "end"
      ? t("mapHintEnd")
      : t("mapHintStart");
}

function setDistance(km) {
  const input = document.getElementById("distance");
  // Widen the track before setting the value: an input clamps silently to its
  // own max, so a 100 km ride was landing on the slider as 60 while the search
  // still asked for 100.
  applyDistanceRange();
  km = Math.max(Number(input.min), Math.min(Number(input.max), km));
  state.distanceKm = km;
  input.value = km;
  document.getElementById("distanceValue").textContent = km;
  paintSlider(input);
}

// The slider could not express the calibration: a long ride is 100 km against
// a maximum of 60. The range follows the sport.
function applyDistanceRange() {
  const range = state.distanceRange[state.sport] || state.distanceRange.running;
  const input = document.getElementById("distance");
  input.min = range[0];
  input.max = range[1];
}

// Ten kilometres is a run; on a bike it is barely a warm-up. When nobody has
// said how far, the guess follows the sport rather than sitting at one number.
function syncDistanceToSport() {
  applyDistanceRange();
  if (state.distanceChosen) {
    setDistance(state.distanceKm);
    return;
  }
  setDistance(state.defaultDistance[state.sport] || 10);
}

// ---------------------------------------------------------------- views
function moveControls(target) {
  document.getElementById(target).appendChild(document.getElementById("controls"));
}

function showHome() {
  state.view = "home";
  document.body.dataset.view = "home";
  // A warning about the last search must not outlive it.
  notify("");
  moveControls("heroSlot");
  document.getElementById("newSearch").hidden = true;
  document.getElementById("search").textContent = t("search");
  initHeroMap();
  if (heroMap) {
    heroMap.invalidateSize({ animate: false });
    drawHeroRoute();
  }
  document.getElementById("home").scrollTop = 0;
  updateMapHint();
  startAskTypewriter();
}

function showApp() {
  state.view = "app";
  document.body.dataset.view = "app";
  moveControls("sidebarSlot");
  document.getElementById("sidebarSlot").hidden = true;
  document.getElementById("newSearch").hidden = false;
  renderSummary();
  updateMapHint();
  stopAskTypewriter();
  document.getElementById("retry").hidden = false;
}

// The map is opened deliberately rather than shown alongside: on a phone it
// would take the half of the screen the results need.
function openMap(routeId) {
  if (routeId) state.activeId = routeId;
  const view = document.getElementById("mapView");
  view.hidden = false;
  document.body.classList.add("map-open");

  const route = state.routes.find((r) => r.id === state.activeId);
  document.getElementById("mapTitle").textContent = route
    ? t("mapOf") + " " + (route.distance_m / 1000).toFixed(1) + " km · +" +
      Math.round(route.ascent_m) + " m"
    : t("mapOf");

  // Leaflet sized itself while the overlay was display:none.
  map.invalidateSize({ animate: false });
  renderResults();
  drawRoutes(true);
  renderPoiLegend();
  drawPois();
}

function closeMap() {
  document.getElementById("mapView").hidden = true;
  document.body.classList.remove("map-open");
}

function renderSummary() {
  const box = document.getElementById("summaryText");
  if (!box) return;
  const bits = [];
  if (state.start) bits.push(state.start.label);
  if (state.mode === "route" && state.end) bits.push("\u2192 " + state.end.label);
  const detail = [
    state.mode === "loop" ? t("modeLoop") : t("modeRoute"),
    t(state.sport),
    state.mode === "loop" || !state.distanceAny ? state.distanceKm + " km" : null,
    state.area === "centre" ? t("areaCentre")
      : state.area === "urban" ? t("areaUrban") : null,
    t(state.surface),
  ].filter(Boolean).join(" · ");

  box.innerHTML = "";
  const main = document.createElement("span");
  main.textContent = bits.join(" ");
  const muted = document.createElement("span");
  muted.className = "muted";
  muted.textContent = (bits.length ? "  ·  " : "") + detail;
  box.append(main, muted);
}

// ---------------------------------------------------------------- messages
function message(text, kind, focus) {
  const box = document.getElementById("messages");
  box.innerHTML = "";
  if (!text) return;
  const note = document.createElement("div");
  note.className = "notice " + (kind || "");
  note.textContent = text;
  box.appendChild(note);
  // The panel is taller than the hero, so a message can land below the fold.
  if (focus) note.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// A search notice belongs beside the results it describes. #messages lives
// inside #controls, which the results page folds into a hidden slot — so every
// warning written there was correct, rendered, and invisible: no flat route
// available, only busy roads, the longer alternative, all of it.
function notify(text) {
  const box = document.getElementById("resultNotes");
  if (!box) return;
  box.innerHTML = "";
  if (!text) return;
  const note = document.createElement("div");
  note.className = "notice warn";
  note.textContent = text;
  box.appendChild(note);
}

let lastPayload = null;

function renderContext() {
  const box = document.getElementById("context");
  if (!lastPayload) {
    box.hidden = true;
    return;
  }
  const w = lastPayload.weather || {};
  const a = lastPayload.air || {};
  const has = (v) => v !== null && v !== undefined;
  const cells = [];

  if (has(w.temperature_c)) cells.push([t("temp"), Math.round(w.temperature_c), "°C"]);
  if (has(w.wind_speed_kmh)) cells.push([t("wind"), Math.round(w.wind_speed_kmh), "km/h"]);
  if (has(a.european_aqi)) cells.push([t("aqi"), Math.round(a.european_aqi), ""]);
  if (has(a.pm2_5)) cells.push([t("pm25"), a.pm2_5.toFixed(1), "µg/m³"]);

  box.innerHTML = "";
  if (!cells.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  cells.forEach(([key, value, unit]) => {
    const cell = document.createElement("div");
    cell.className = "cell";
    const k = document.createElement("div");
    k.className = "k";
    k.textContent = key;
    const v = document.createElement("div");
    v.className = "v";
    v.textContent = value;
    if (unit) {
      const small = document.createElement("small");
      small.textContent = " " + unit;
      v.appendChild(small);
    }
    cell.append(k, v);
    box.appendChild(cell);
  });
}


// ---------------------------------------------------------------- POIs
// Glyphs, not colours: a viewer tells a fountain from a monument by its symbol
// and its tooltip, so hue never has to carry the distinction on its own.
const POI_GLYPHS = {
  water: "\uD83D\uDCA7",
  toilets: "\uD83D\uDEBB",
  green: "\uD83C\uDF33",
  viewpoint: "\uD83C\uDFDE\uFE0F",
  monument: "\uD83C\uDFDB\uFE0F",
  art: "\uD83C\uDFA8",
  bike: "\uD83D\uDD27",
};

const poiLayer = L.layerGroup().addTo(map);

const PoiLegend = L.Control.extend({
  options: { position: "bottomleft" },
  onAdd: function () {
    const box = L.DomUtil.create("div", "poi-legend");
    box.id = "poiLegend";
    L.DomEvent.disableClickPropagation(box);
    L.DomEvent.disableScrollPropagation(box);
    box.addEventListener("click", (e) => {
      const button = e.target.closest("button[data-kind]");
      if (!button) return;
      const kind = button.dataset.kind;
      state.poiOff[kind] = !state.poiOff[kind];
      renderPoiLegend();
      drawPois();
    });
    return box;
  },
});
map.addControl(new PoiLegend());

function activeCounts() {
  return (state.poiCounts && state.poiCounts[state.activeId]) || {};
}

function renderPoiLegend() {
  const box = document.getElementById("poiLegend");
  if (!box) return;
  box.innerHTML = "";

  if (state.poiLoading) {
    const note = document.createElement("span");
    note.className = "empty";
    note.textContent = t("poiLoading");
    box.appendChild(note);
    box.hidden = false;
    return;
  }

  const counts = activeCounts();
  const kinds = state.poiKinds.filter((kind) => (counts[kind] || 0) > 0);
  if (!kinds.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;

  kinds.forEach((kind) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.kind = kind;
    button.setAttribute("aria-pressed", String(!state.poiOff[kind]));
    button.title = t("poi_" + kind);
    button.innerHTML =
      '<span aria-hidden="true">' + POI_GLYPHS[kind] + "</span>" +
      '<span class="n">' + counts[kind] + "</span>";
    box.appendChild(button);
  });
}

function drawPois() {
  poiLayer.clearLayers();
  if (!state.activeId) return;

  state.pois.forEach((item) => {
    if (!item.routes.includes(state.activeId)) return;
    if (state.poiOff[item.kind]) return;

    const marker = L.marker([item.lat, item.lon], {
      icon: L.divIcon({
        className: "",
        html: '<div class="poi-dot">' + (POI_GLYPHS[item.kind] || "") + "</div>",
        iconSize: [24, 24],
        iconAnchor: [12, 12],
      }),
      zIndexOffset: 50,
    });
    marker.bindTooltip(item.name || t("poi_" + item.kind), {
      className: "poi-tip",
      direction: "top",
      offset: [0, -14],
    });
    marker.addTo(poiLayer);
  });
}

async function loadPois() {
  state.pois = [];
  state.poiCounts = {};
  state.poiScores = {};
  state.poiFailed = false;
  state.poiExpired = false;
  poiLayer.clearLayers();
  if (!state.routes.length) return;

  state.poiLoading = true;
  renderPoiLegend();
  try {
    const response = await fetch("/api/pois", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        route_ids: state.routes.map((r) => r.id),
        sights: state.sights,
      }),
    });
    if (!response.ok) {
      state.poiFailed = true;
      return;
    }
    const data = await response.json();
    state.pois = data.pois || [];
    state.poiCounts = data.counts || {};
    state.poiKinds = data.kinds || [];
    // Served rather than duplicated here, so the two definitions cannot drift.
    state.monumentKinds = data.monument_kinds || [];
    state.natureKinds = data.nature_kinds || [];
    state.poiScores = data.scores || {};
    // An outage and an empty neighbourhood look identical unless we say so.
    state.poiFailed = data.available === false;
    state.poiExpired = data.expired === true;

    // Water and scenery only arrive now, so they adjust a score that was
    // already shown. Re-order the list, but leave the selected route selected
    // rather than yanking the map somewhere else.
    state.routes.forEach((route) => {
      const extra = state.poiScores[route.id];
      if (extra) route.scores.total = extra.total;
    });
    state.routes.sort((a, b) => b.scores.total - a.scores.total);
  } catch (err) {
    // Overpass being slow or down must never break the results page.
    state.poiFailed = true;
  } finally {
    state.poiLoading = false;
    renderPoiLegend();
    drawPois();
    renderResults();
  }
}

// ---------------------------------------------------------------- results
// One line saying what this route is like, in place of two numbers that are
// already in the heading above it. Surface always, because it is the thing
// that changes most between candidates, plus at most one other clause: the
// bad news if there is any, otherwise the best thing about it. Two clauses is
// a sentence someone reads; four is a spec sheet they skip.
function describeRoute(route) {
  const paved = route.paved_share;
  let line = paved >= 0.85 ? t("sumPaved")
           : paved >= 0.6 ? t("sumMostlyPaved")
           : paved >= 0.35 ? t("sumMixed")
           : t("sumUnpaved");

  const counts = (state.poiCounts && state.poiCounts[route.id]) || {};
  const km = route.distance_m / 1000;
  const perKmWater = km > 0 ? (counts.water || 0) / km : 0;
  const sights = (state.monumentKinds || []).concat(state.natureKinds || [])
    .reduce((n, kind) => n + (counts[kind] || 0), 0);

  let extra = null;
  if ((route.big_road_share || 0) >= 0.15) extra = t("sumBusy");
  else if (state.area === "centre" && (route.centre_share || 0) >= 0.6)
    extra = t("sumCentre");
  else if ((route.bikeway_share || 0) >= 0.4) extra = t("sumBikeway");
  else if (perKmWater >= 0.5) extra = t("sumWater");
  else if (km > 0 && sights / km >= 2) extra = t("sumSights");
  else if (route.traffic_exposure <= 0.2) extra = t("sumQuiet");

  return extra ? line + ", " + extra : line;
}

function trafficLabel(exposure) {
  if (exposure < 0.25) return t("trafficLow");
  if (exposure < 0.5) return t("trafficMid");
  return t("trafficHigh");
}

const RING_R = 20;
const RING_C = 2 * Math.PI * RING_R;

function scoreRing(value) {
  const wrap = document.createElement("div");
  wrap.className = "ring";
  wrap.innerHTML =
    '<svg width="46" height="46" viewBox="0 0 46 46" aria-hidden="true">' +
    '<circle class="ring-track" cx="23" cy="23" r="' + RING_R + '" fill="none" stroke-width="4"/>' +
    '<circle class="ring-fill" cx="23" cy="23" r="' + RING_R + '" fill="none" stroke-width="4" ' +
    'stroke-dasharray="' + RING_C.toFixed(2) + '" ' +
    'stroke-dashoffset="' + (RING_C * (1 - value)).toFixed(2) + '"/>' +
    "</svg>";
  const label = document.createElement("div");
  label.className = "ring-value";
  label.textContent = Math.round(value * 100);
  wrap.appendChild(label);
  return wrap;
}

// The bar shows how this route compares; the number beside it says what was
// actually measured. A bare 0-100 reads as a percentage of something and is
// not one — "cose belle 100" only ever meant "the best of these five", and
// "acqua 100" meant "at least one tap every 3 km", which nearly everything in
// a city clears. A count, a percentage or a height is arguable; a rank is not.
function bar(label, value, fact, miss) {
  const pct = Math.round((value || 0) * 100);
  const row = document.createElement("div");
  row.className = "bar" + (miss ? " miss" : "");
  const name = document.createElement("span");
  name.textContent = label;
  const track = document.createElement("div");
  track.className = "track";
  const fill = document.createElement("div");
  fill.className = "fill";
  fill.style.width = Math.max(pct, 1.5) + "%";
  track.appendChild(fill);
  const num = document.createElement("span");
  num.className = "num";
  num.textContent = fact === undefined || fact === null ? pct : fact;
  row.append(name, track, num);
  return row;
}

function drawRoutes(fit) {
  routeLayer.clearLayers();
  if (!state.routes.length) return;

  const active = token("--accent-mark") || "#10a26a";
  const casing = token("--card") || "#ffffff";
  const onImagery = state.mapStyle === "satellite";
  const idle = onImagery ? "#ffffff" : token("--ink-3") || "#6b7772";
  let activeLine = null;

  // Inactive first, so the selected route always sits on top.
  const ordered = state.routes.slice().sort(
    (a, b) => (a.id === state.activeId ? 1 : 0) - (b.id === state.activeId ? 1 : 0)
  );

  ordered.forEach((route) => {
    const latlngs = route.coordinates.map((c) => [c[1], c[0]]);
    const isActive = route.id === state.activeId;

    if (isActive) {
      L.polyline(latlngs, { color: casing, weight: 9, opacity: 0.95 }).addTo(routeLayer);
    } else if (onImagery) {
      L.polyline(latlngs, { color: "#0d1512", weight: 6, opacity: 0.35 }).addTo(routeLayer);
    }
    const line = L.polyline(latlngs, {
      color: isActive ? active : idle,
      weight: isActive ? 5 : 3,
      opacity: isActive ? 1 : (onImagery ? 0.6 : 0.45),
      dashArray: isActive ? null : "1 7",
      lineCap: "round",
    }).addTo(routeLayer);
    line.on("click", () => selectRoute(route.id));
    if (isActive) activeLine = line;
  });

  if (fit && activeLine) map.fitBounds(activeLine.getBounds(), { padding: [40, 40] });
}

function selectRoute(id) {
  state.activeId = id;
  renderResults();
  if (!document.getElementById("mapView").hidden) {
    drawRoutes(true);
    renderPoiLegend();
    drawPois();
  }
}

function renderResults() {
  const box = document.getElementById("results");
  box.innerHTML = "";
  if (!lastPayload) return;

  if (!state.routes.length) {
    const empty = document.createElement("div");
    empty.className = "notice warn";
    empty.textContent = t("noResults");
    box.appendChild(empty);
    return;
  }

  const query = lastPayload.query || {};
  const isLoop = query.mode !== "route";

  state.routes.forEach((route, index) => {
    const card = document.createElement("div");
    card.className = "result" + (route.id === state.activeId ? " active" : "");


    const head = document.createElement("div");
    head.className = "head";

    const rank = document.createElement("div");
    rank.className = "rank";
    rank.textContent = index + 1;

    const main = document.createElement("div");
    main.className = "main";
    const title = document.createElement("div");
    title.className = "title";
    title.innerHTML =
      (route.distance_m / 1000).toFixed(1) +
      ' km<span class="sep">·</span>+' + Math.round(route.ascent_m) + " m";
    const sub = document.createElement("div");
    sub.className = "sub";
    sub.textContent = describeRoute(route);
    main.append(title, sub);

    // Where the request could not be met in full, say what this route is the
    // best answer to, so picking between compromises is the reader's call.
    (route.best_for || []).forEach((what) => {
      const tag = document.createElement("span");
      tag.className = "best-for";
      tag.textContent = what === "distance" ? t("bestForDistance") : t("bestForGain");
      sub.append(" ");
      sub.appendChild(tag);
    });

    head.append(rank, main, scoreRing(route.scores.total));

    const bars = document.createElement("div");
    bars.className = "bars";
    const query = lastPayload.query || {};

    // No distance or climb rows: the heading states both, and a bar that
    // repeats the line above it is furniture. They still carry their weight in
    // the score, and a route that misses a target you set still says so — in
    // the notice above the results, where it cannot be missed.

    // Surface: the share of the route that is actually what you asked for.
    const paved = Math.round(route.paved_share * 100);
    bars.appendChild(bar(
      t("scoreSurface"), route.scores.surface,
      query.surface === "trail" ? (100 - paved) + "% " + t("unpaved")
                                : paved + "% " + t("paved")
    ));

    // What it costs to cover. An estimate, and the bar is relative to the
    // other candidates because "how many calories is a lot" has no answer.
    if (route.calories_kcal) {
      const most = Math.max.apply(null, state.routes.map((r) => r.calories_kcal || 0));
      bars.appendChild(bar(
        t("scoreCalories"),
        most > 0 ? route.calories_kcal / most : 0,
        Math.round(route.calories_kcal) + " kcal"
      ));
    }

    // Shown for both: an Italian ciclabile is usually ciclopedonale, so it is
    // a path away from cars for whoever is on it.
    bars.appendChild(bar(
      t("scoreBikeway"), route.bikeway_share || 0,
      Math.round((route.bikeway_share || 0) * 100) + "% " + t("bikewayShort")
    ));

    // Traffic: metres beside fast roads, which is the thing you can act on.
    bars.appendChild(bar(
      t("scoreTraffic"), route.scores.traffic,
      Math.round((route.big_road_share || 0) * 100) + "% " + t("bigRoadsShort")
    ));

    // Wind is only ever a fact on a one-way route. Around a loop every metre
    // into it is repaid by a metre with it, so the number said nothing about
    // the route and the same thing about all of them.
    if (!isLoop) {
      bars.appendChild(bar(
        t("scoreWind"), route.scores.wind,
        Math.round(route.headwind_share * 100) + "% " + t("headwindShort")
      ));
    }

    if (
      route.scores.air !== null && route.scores.air !== undefined &&
      lastPayload.air.differentiates_routes
    ) {
      const aqi = route.air && route.air.european_aqi;
      bars.appendChild(bar(
        t("scoreAir"), route.scores.air,
        aqi === undefined || aqi === null ? undefined : "AQI " + Math.round(aqi)
      ));
    }

    const extra = state.poiScores[route.id];
    const counts = (state.poiCounts && state.poiCounts[route.id]) || {};
    if (extra) {
      // How far you go between taps, rather than a score that reads 100 for
      // anything with one every three kilometres.
      const taps = counts.water || 0;
      const km = route.distance_m / 1000;
      bars.appendChild(bar(
        t("scoreWater"), extra.water,
        taps ? t("everyKm").replace("{km}", (km / taps).toFixed(1)) : t("none")
      ));
      // Only the axis you asked for is scored, so only it is shown.
      if (extra.sights !== null && extra.sights !== undefined) {
        const monument = state.monumentKinds || [];
        const nature = state.natureKinds || [];
        const kinds = query.sights === "monuments" ? monument
                    : query.sights === "nature" ? nature
                    : monument.concat(nature);
        const seen = kinds.reduce((n, k) => n + (counts[k] || 0), 0);
        const label = query.sights === "monuments" ? t("scoreMonuments")
                    : query.sights === "nature" ? t("scoreNature")
                    : t("scoreSights");
        bars.appendChild(bar(label, extra.sights, String(seen)));
      }
    }

    const present = state.poiKinds.filter((kind) => (counts[kind] || 0) > 0);
    // A route on fast roads is still offered when nothing calmer exists, so it
    // has to say so on the card rather than hide inside the traffic score.
    const bigRoad = route.big_road_share || 0;
    let badgeRow = null;
    if (present.length || bigRoad >= BIG_ROAD_WARN) {
      badgeRow = document.createElement("div");
      badgeRow.className = "poi-badges";
      if (bigRoad >= BIG_ROAD_WARN) {
        const warn = document.createElement("span");
        warn.className = "badge road";
        warn.textContent = t("bigRoad").replace("{pct}", Math.round(bigRoad * 100));
        warn.title = t("bigRoadWhy");
        badgeRow.appendChild(warn);
      }
      present.forEach((kind) => {
        const badge = document.createElement("span");
        badge.className = "badge";
        badge.innerHTML =
          '<span aria-hidden="true">' + POI_GLYPHS[kind] + "</span>" +
          '<span class="n">' + counts[kind] + "</span>";
        badge.append(" " + t("poi_" + kind));
        badgeRow.appendChild(badge);
      });
    }

    const actions = document.createElement("div");
    actions.className = "actions";
    const mapButton = document.createElement("button");
    mapButton.type = "button";
    mapButton.textContent = t("showOnMap");
    mapButton.addEventListener("click", () => openMap(route.id));
    const link = document.createElement("a");
    link.href = "/api/gpx/" + route.id;
    link.textContent = t("download");
    actions.append(mapButton, link);
    // Only offered where the browser can actually hand a file to another app.
    // A button that quietly turns into a download is worse than no button.
    if (canShareFiles()) {
      const send = document.createElement("button");
      send.type = "button";
      send.className = "send";
      send.textContent = t("sendToWatch");
      send.addEventListener("click", () => shareGpx(route, send));
      actions.appendChild(send);
    }

    // Five cards of eight bars each is a wall. The headline — distance, climb,
    // surface, traffic, score — is what you choose on; the rest is what you
    // check afterwards, so it folds away. The first is open because a page
    // that shows nothing has not answered anything.
    const details = document.createElement("div");
    details.className = "details";
    details.id = "details-" + route.id;
    details.append(bars);
    if (badgeRow) details.appendChild(badgeRow);
    details.appendChild(actions);

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "details-toggle";
    const open = state.expanded[route.id] !== undefined
      ? state.expanded[route.id] : index === 0;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-controls", details.id);
    toggle.textContent = open ? t("hideDetails") : t("showDetails");
    details.hidden = !open;
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      const nowOpen = details.hidden;
      details.hidden = !nowOpen;
      state.expanded[route.id] = nowOpen;
      toggle.setAttribute("aria-expanded", String(nowOpen));
      toggle.textContent = nowOpen ? t("hideDetails") : t("showDetails");
    });

    card.append(head, toggle, details);
    box.appendChild(card);
  });

  if (state.poiFailed) {
    const failed = document.createElement("div");
    failed.className = "notice warn";
    failed.textContent = (state.poiExpired ? t("poiExpired") : t("poiFailed")) + " ";
    const retry = document.createElement("button");
    retry.className = "link-btn inline";
    retry.type = "button";
    retry.textContent = t("poiRetry");
    retry.addEventListener("click", (e) => {
      e.stopPropagation();
      loadPois();
    });
    // Retrying an expired cache cannot work; only a new search can.
    if (!state.poiExpired) failed.appendChild(retry);
    box.appendChild(failed);
  }

  // Say plainly when a layer could not separate the routes, instead of
  // showing a score bar that is the same number every time.
  if (lastPayload.air.european_aqi !== null && lastPayload.air.european_aqi !== undefined) {
    const airNote = document.createElement("div");
    airNote.className = "notice";
    airNote.textContent = lastPayload.air.differentiates_routes ? t("airVaries") : t("airFlat");
    box.appendChild(airNote);
  }
}


// ---------------------------------------------------------------- the sentence box
// A form makes you translate what you want into six controls. This lets you
// say it, then shows what it understood — a box that silently reinterprets
// you is worse than the form it replaced.

function setSegmented(id, value) {
  const box = document.getElementById(id);
  if (!box || !value) return;
  box.querySelectorAll("button").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.value === value));
  });
}

function applyIntent(data) {
  // "un giro tranquillo" says nothing about the sport, and a 10 km run is not
  // a 10 km ride. Rather than stopping to ask, carry the person's usual
  // choice and label it as a guess they can correct in one tap.
  // Once the chip has been tapped the choice is the person's, not a guess, and
  // re-reading the same silent sentence must not undo that.
  state.assumedSport = !data.sport && !state.sportChosen;
  if (data.sport) {
    state.sport = data.sport;
    state.sportChosen = false;
    remember("doiamo_sport", data.sport);
  }
  setSegmented("sport", state.sport);
  syncDistanceToSport();
  if (data.surface) { state.surface = data.surface; setSegmented("surface", data.surface); }
  if (data.sights) { state.sights = data.sights; setSegmented("sights", data.sights); }
  // Said or not said each time: "rimanere in citta" is part of this sentence,
  // not a preference that should outlive it.
  state.area = data.area || "any";

  // A calorie target is a distance we worked out, not one they gave, and the
  // body it assumed is the biggest thing that could make it wrong.
  state.calories = data.calories || null;
  state.massAssumed = !!data.mass_assumed;
  if (data.mass_kg && !data.mass_assumed) {
    state.massKg = data.mass_kg;
    remember("doiamo_mass", data.mass_kg);
  } else if (data.mass_kg) {
    state.massKg = state.massKg || data.mass_kg;
  }

  // Each sentence is a whole search, not an edit to the last one. Setting a
  // place without ever clearing one meant a sentence naming nowhere inherited
  // the previous sentence's place: after searching Bologna, "intorno a me"
  // searched Bologna. Clearing it lets the "from here" fallback do its job.
  if (data.start) {
    setPoint("start", data.start.lat, data.start.lon, data.start.label, false);
  } else {
    clearPoint("start");
    syncPlaceInput("start");
  }
  if (data.end) {
    setPoint("end", data.end.lat, data.end.lon, data.end.label, false);
  } else {
    clearPoint("end");
    syncPlaceInput("end");
  }

  // No destination named means a loop — "voglio correre 10 km" is a loop from
  // wherever you are, not half a journey to nowhere.
  state.mode = data.end ? "route" : "loop";
  document.getElementById("loopMode").checked = state.mode === "loop";
  applyMode();

  if (data.distance_km) {
    state.distanceChosen = true;
    setDistance(Math.round(data.distance_km));
    state.distanceAny = false;
    document.getElementById("distanceAny").checked = false;
    syncDistanceEnabled();
  }
  if (data.elevation_gain_m !== null && data.elevation_gain_m !== undefined) {
    state.gainM = Math.round(data.elevation_gain_m);
    const gain = document.getElementById("gain");
    gain.value = state.gainM;
    document.getElementById("gainValue").textContent = state.gainM;
    paintSlider(gain);
    state.gainAny = false;
    document.getElementById("gainAny").checked = false;
    gain.disabled = false;
  }
}

function addChip(text, missing) {
  const box = document.getElementById("askChips");
  box.hidden = false;
  const chip = document.createElement("span");
  if (missing) chip.className = "miss";
  chip.textContent = text;
  box.appendChild(chip);
}

function addGuessChip(text, onFlip, hint) {
  const box = document.getElementById("askChips");
  box.hidden = false;
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "guess";
  chip.title = t("tapToChange");
  chip.innerHTML = "";
  chip.append(text);
  const why = document.createElement("span");
  why.className = "why";
  why.textContent = hint || t("assumed");
  chip.appendChild(why);
  chip.addEventListener("click", onFlip);
  box.appendChild(chip);
}

function showChips(data) {
  const box = document.getElementById("askChips");
  box.innerHTML = "";
  const items = (data.understood || []).slice();
  (data.unresolved || []).forEach((name) => items.push({ miss: t("askUnresolved") + " " + name }));
  if (!items.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  items.forEach((item) => {
    if (typeof item === "string") addChip(item);
    else addChip(item.miss, true);
  });
}

async function askSearch() {
  const field = document.getElementById("ask");
  const sentence = field.value.trim();
  if (!sentence) {
    message(t("askEmpty"), "warn", true);
    field.focus();
    return;
  }

  const button = document.getElementById("askGo");
  button.disabled = true;
  button.textContent = t("searching");
  message("");
  try {
    const centre = (state.view === "home" && heroMap ? heroMap : map).getCenter();
    // Only what we already have: asking for a fix here would put a permission
    // prompt in front of every search, including the ones that name a city.
    const bias = state.here || { lat: centre.lat, lon: centre.lng };
    const response = await fetch("/api/interpret", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sentence: sentence,
        lang: getLang(),
        lat: bias.lat,
        lon: bias.lon,
        mass_kg: state.massKg,
      }),
    });
    if (!response.ok) {
      message(t("error"), "warn", true);
      return;
    }
    const data = await response.json();
    applyIntent(data);
    showChips(data);
    // The calorie target rests on a body mass nobody gave us. Say so, and let
    // one tap fix it, rather than printing a number that only looks precise.
    if (state.calories && state.massAssumed) {
      // The calories are what they asked for; the body is what we made up.
      addGuessChip("70 kg", askForMass, t("assumedMass"));
    }
    if (state.assumedSport) {
      addGuessChip(t(state.sport), () => {
        state.sport = state.sport === "running" ? "cycling" : "running";
        // Tapping the chip is the choice the sentence never made, so from here
        // on it is a fact and stops being drawn as a guess.
        state.assumedSport = false;
        state.sportChosen = true;
        remember("doiamo_sport", state.sport);
        setSegmented("sport", state.sport);
        syncDistanceToSport();
        askSearch();
      });
    }

    // A sentence that names no place means "from here" — so find out where
    // here is. If the browser will not say, fall back to the map centre and
    // label it as the map centre, rather than claiming it is you.
    if (!state.start) {
      const spot = await here(7000);
      if (spot && spot.accuracy <= FIX_VAGUE_M) {
        setPoint("start", spot.lat, spot.lon, t("fromHere"), false);
        addChip(t("fromHere"));
      } else if (spot) {
        // Good enough to search from, not good enough to assert. Dashed and
        // tappable, like every other thing we guessed.
        setPoint("start", spot.lat, spot.lon, t("fromHere"), false);
        addGuessChip(t("fromHere"), () => openPicker("start"), t("approxPosition"));
      } else {
        const centre = (heroMap || map).getCenter();
        setPoint("start", centre.lat, centre.lng, t("fromMapCentre"), false);
        addGuessChip(t("fromMapCentre"), () => openPicker("start"), t("tapToSetIt"));
      }
    }
    await search();
  } catch (err) {
    message(t("error"), "warn", true);
  } finally {
    button.disabled = false;
    button.textContent = t("askGo");
  }
}

function revealForm(show) {
  const slot = document.getElementById("heroSlot");
  slot.hidden = !show;
  const toggle = document.getElementById("toggleForm");
  // The label lives in a span so the chevron pseudo-element sits beside the
  // text rather than inheriting its underline.
  toggle.innerHTML = "";
  const label = document.createElement("span");
  label.textContent = show ? t("hideClassic") : t("tryClassic");
  toggle.appendChild(label);
  toggle.setAttribute("aria-expanded", show ? "true" : "false");
}

// ---------------------------------------------------------------- search
async function search() {
  if (!state.start) {
    message(t("needStart"), "warn", true);
    document.getElementById("startInput").focus();
    return;
  }
  if (state.mode === "route" && !state.end) {
    message(t("needEnd"), "warn", true);
    document.getElementById("endInput").focus();
    return;
  }

  const button = document.getElementById("search");
  button.disabled = true;
  button.textContent = t("searching");
  message("");

  const body = {
    lat: state.start.lat,
    lon: state.start.lon,
    mode: state.mode,
    sport: state.sport,
    surface: state.surface,
    sights: state.sights,
    area: state.area,
    mass_kg: state.massKg,
  };
  rememberPlace(state.start.lat, state.start.lon);
  body.elevation_gain_m = state.gainAny ? null : state.gainM;
  if (state.mode === "loop") {
    body.distance_km = state.distanceKm;
  } else {
    body.end_lat = state.end.lat;
    body.end_lon = state.end.lon;
    body.distance_km = state.distanceAny ? null : state.distanceKm;
  }

  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const detail = (await response.json().catch(() => ({}))).detail || "";
      const reason = String(detail).toLowerCase();
      if (response.status === 503) message(t("keyMissing"), "warn", true);
      else if (reason.includes("quota") || reason.includes("budget"))
        message(t("quotaSpent"), "warn", true);
      else if (reason.includes("rate limit")) message(t("rateLimited"), "warn", true);
      else message(t("error") + (detail ? ": " + detail : ""), "warn", true);
      return;
    }

    lastPayload = await response.json();
    state.routes = lastPayload.routes || [];
    state.activeId = state.routes.length ? state.routes[0].id : null;

    showApp();

    const notices = lastPayload.notices || [];
    const notes = [];
    if (notices.includes("no_exact_distance_match")) notes.push(t("noExactDistance"));
    if (notices.includes("alternatives_unavailable")) notes.push(t("altsUnavailable"));
    if (notices.includes("busy_roads_only")) notes.push(t("busyRoadsOnly"));
    if (notices.includes("centre_unknown")) notes.push(t("centreUnknown"));
    if (notices.includes("gain_target_unreachable")) notes.push(t("noFlatOption"));
    // Only one of these two: the second is the same news with a better answer
    // attached, and printing both says it twice.
    if (notices.includes("stretched_alternative")) notes.push(t("climbFurtherOut"));
    else if (notices.includes("climb_target_unreachable")) notes.push(t("noClimbOption"));
    if (notices.includes("distance_target_unreachable")) notes.push(t("noDistanceOption"));
    if (notices.includes("no_route_of_that_length")) notes.push(t("noRouteOfLength"));
    notify(notes.join(" "));

    renderContext();
    renderResults();
    renderSummary();
    drawRoutes(false);
    loadPois();
  } catch (err) {
    message(t("error"), "warn");
  } finally {
    button.disabled = false;
    button.textContent = t("search");
  }
}

// ---------------------------------------------------------------- sharing
// There is no public URL that opens a route in Garmin Connect, and the Connect
// API that could push one directly needs approval from Garmin. What does work
// today, on a phone, is handing the .gpx to the operating system: Garmin
// Connect registers itself as somewhere a GPX can go, so it shows up in the
// share sheet next to everything else that reads them.
function canShareFiles() {
  if (!navigator.canShare || !navigator.share) return false;
  try {
    // Feature-detect with a real file: some browsers expose share() but refuse
    // files, and canShare() is the only way to find out without failing loudly.
    return navigator.canShare({
      files: [new File(["<gpx/>"], "probe.gpx", { type: "application/gpx+xml" })],
    });
  } catch (err) {
    return false;
  }
}

function askForMass() {
  const current = state.massKg || 70;
  const said = window.prompt(t("askMass"), String(current));
  if (said === null) return;
  const value = Number(said.replace(",", "."));
  if (!Number.isFinite(value) || value < 30 || value > 250) {
    message(t("massRange"), "warn", true);
    return;
  }
  state.massKg = Math.round(value);
  state.massAssumed = false;
  remember("doiamo_mass", state.massKg);
  askSearch();
}

async function shareGpx(route, button) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = t("preparing");
  try {
    const response = await fetch("/api/gpx/" + route.id);
    if (!response.ok) throw new Error("gpx " + response.status);
    const blob = await response.blob();
    const name = "doiamo-" + state.sport + "-" +
      (route.distance_m / 1000).toFixed(0) + "km-" + route.id.slice(0, 6) + ".gpx";
    const file = new File([blob], name, { type: "application/gpx+xml" });
    if (!navigator.canShare({ files: [file] })) throw new Error("cannot share");
    await navigator.share({ files: [file], title: name });
  } catch (err) {
    // Dismissing the share sheet raises AbortError, which is not a failure and
    // must not be reported as one.
    if (!err || err.name !== "AbortError") message(t("shareFailed"), "warn");
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

// ------------------------------------------------------- the typing prompt
// A static example tells you the box takes a sentence. One being typed tells
// you it takes YOUR sentence — and cycling through them is the feature list,
// since every example demonstrates something different the parser reads.
const TYPE_MS = 42;      // fast enough not to test anyone's patience
const DELETE_MS = 20;    // nobody needs to watch it un-type at reading speed
const HOLD_MS = 2100;    // long enough to finish reading the finished line
const GAP_MS = 420;

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");

let askTyper = null;

// The hero map opens on the last place searched, or on a default centre for
// someone brand new. Taking that centre and calling it "da dove sei ora" was a
// claim, and a false one for anyone it did not happen to describe — so ask the
// browser where you actually are, and say plainly when it would not tell.
// A browser with no GPS and no known Wi-Fi around it falls back to locating
// you by IP address, and reports that as a position like any other — which is
// how someone in Italy is told they are in Germany. The giveaway is always
// coords.accuracy: a GPS or Wi-Fi fix lands within tens of metres, an IP fix
// is a radius of tens of kilometres. A fix that cannot tell which country you
// are in cannot start a run, so it is not a fix.
const FIX_USELESS_M = 25000;   // beyond this it is an IP guess, not a position
const FIX_VAGUE_M = 1500;      // usable, but say it is approximate

function currentPosition(timeoutMs) {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        // Firefox has been known to omit accuracy; treat unknown as usable
        // rather than throwing away a fix that may well be fine.
        const accuracy = Number.isFinite(pos.coords.accuracy)
          ? pos.coords.accuracy : 0;
        if (accuracy > FIX_USELESS_M) return resolve(null);
        resolve({
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
          accuracy: accuracy,
        });
      },
      () => resolve(null),          // denied, unavailable or timed out
      { timeout: timeoutMs, maximumAge: 5 * 60 * 1000 }
    );
  });
}

// Asked for at most once a session, and only when a search actually needs it —
// a permission prompt on page load is a toll gate on the front door.
async function here(timeoutMs) {
  if (state.here === undefined) state.here = await currentPosition(timeoutMs);
  return state.here;
}

function stopAskTypewriter() {
  if (askTyper) {
    clearTimeout(askTyper.timer);
    askTyper = null;
  }
}

function startAskTypewriter() {
  stopAskTypewriter();
  const field = document.getElementById("ask");
  if (!field) return;

  // Someone who has started writing does not need to be shown how, and text
  // moving underneath a caret is just noise.
  if (field.value) {
    field.placeholder = "";
    return;
  }
  // Off the home page, or in a background tab, there is nothing to watch — so
  // there is nothing to run. visibilitychange only fires on a change, so a tab
  // that was already hidden when the page loaded has to be caught here.
  if (state.view !== "home" || document.hidden) return;

  const examples = t("askExamples");
  if (!Array.isArray(examples) || !examples.length) {
    field.placeholder = t("askPlaceholder");
    return;
  }
  // Asked not to animate: keep the sentence that explains the box, which says
  // more than any single example.
  if (REDUCED_MOTION.matches) {
    field.placeholder = t("askPlaceholder");
    return;
  }

  // Start somewhere different each visit, so a returning user sees a new one.
  const self = {
    at: Math.floor(Math.random() * examples.length),
    shown: 0,
    phase: "type",
    timer: 0,
  };
  askTyper = self;

  function step() {
    if (askTyper !== self) return;          // superseded; let this chain die
    const line = examples[self.at % examples.length];
    let delay = TYPE_MS;

    if (self.phase === "type") {
      self.shown += 1;
      if (self.shown >= line.length) {
        self.phase = "hold";
        delay = HOLD_MS;
      }
    } else if (self.phase === "hold") {
      self.phase = "delete";
      delay = DELETE_MS;
    } else {
      self.shown -= 1;
      delay = DELETE_MS;
      if (self.shown <= 0) {
        self.phase = "type";
        self.at += 1;
        delay = GAP_MS;
      }
    }

    field.placeholder =
      line.slice(0, self.shown) + (self.phase === "hold" ? "" : "\u258c");
    self.timer = setTimeout(step, delay);
  }

  step();
}

// ---------------------------------------------------------------- i18n
function applyLang() {
  document.documentElement.lang = getLang();
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("#lang button").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.lang === getLang()));
  });
  startAskTypewriter();
  document.getElementById("askGo").textContent = t("askGo");
  revealForm(!document.getElementById("heroSlot").hidden);
  document.getElementById("startInput").placeholder = t("startPlaceholder");
  document.getElementById("endInput").placeholder = t("endPlaceholder");
  document.getElementById("locate").title = t("locate");
  document.getElementById("pickStart").title = t("pickOnMap");
  document.getElementById("pickEnd").title = t("pickOnMap");
  renderStyleSwitch();
  renderPoiLegend();
  updateMapHint();
  renderResults();
  renderContext();
}

// ---------------------------------------------------------------- boot
function wireEverything() {
  wireSegmented("surface", "surface");
  wireSegmented("sights", "sights");
  wireSegmented("sport", "sport", () => {
    remember("doiamo_sport", state.sport);
    // Choosing the sport by hand is not choosing the distance, so a default
    // still follows; a number already set stays set.
    syncDistanceToSport();
  });

  wirePlaceField("start");
  wirePlaceField("end");

  document.getElementById("distance").addEventListener("input", (e) => {
    state.distanceChosen = true;
    state.distanceKm = Number(e.target.value);
    document.getElementById("distanceValue").textContent = state.distanceKm;
    paintSlider(e.target);
  });
  document.getElementById("gain").addEventListener("input", (e) => {
    state.gainM = Number(e.target.value);
    document.getElementById("gainValue").textContent = state.gainM;
    paintSlider(e.target);
  });
  document.getElementById("gainAny").addEventListener("change", (e) => {
    state.gainAny = e.target.checked;
    document.getElementById("gain").disabled = state.gainAny;
  });
  document.getElementById("loopMode").addEventListener("change", (e) => {
    state.mode = e.target.checked ? "loop" : "route";
    applyMode();
  });
  document.getElementById("distanceAny").addEventListener("change", (e) => {
    state.distanceAny = e.target.checked;
    syncDistanceEnabled();
  });

  document.getElementById("locate").addEventListener("click", () => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => setPoint("start", pos.coords.latitude, pos.coords.longitude, null, true),
      () => message(t("error"), "warn")
    );
  });

  document.getElementById("pickStart").addEventListener("click", () => openPicker("start"));
  document.getElementById("pickEnd").addEventListener("click", () => openPicker("end"));
  document.getElementById("pickerClose").addEventListener("click", closePicker);
  document.getElementById("pickerConfirm").addEventListener("click", confirmPicker);
  document.getElementById("picker").addEventListener("click", (e) => {
    if (e.target.id === "picker") closePicker();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !document.getElementById("picker").hidden) closePicker();
  });

  document.getElementById("search").addEventListener("click", search);
  document.getElementById("askGo").addEventListener("click", askSearch);
  document.getElementById("ask").addEventListener("keydown", (e) => {
    // Enter searches; Shift+Enter is a newline.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      askSearch();
    }
  });
  // The demonstration is over the moment they start writing their own, and it
  // comes back if they clear the box and leave it alone.
  // startAskTypewriter stops whatever was running and decides from the field's
  // own state whether anything should replace it.
  document.getElementById("ask").addEventListener("input", startAskTypewriter);
  REDUCED_MOTION.addEventListener("change", startAskTypewriter);
  // A background tab throttles timers to about one a second, which turns the
  // animation into a stutter and leaves it mid-word on return. Nothing is
  // being watched anyway, so stop, and start the next line fresh when it is.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopAskTypewriter();
    else startAskTypewriter();
  });
  document.getElementById("toggleForm").addEventListener("click", () => {
    revealForm(document.getElementById("heroSlot").hidden);
  });
  document.getElementById("newSearch").addEventListener("click", showHome);
  // Straight into the precise controls, already filled in with what the
  // sentence was understood to mean — so it is a correction, not a restart.
  document.getElementById("retryAdvanced").addEventListener("click", () => {
    const slot = document.getElementById("sidebarSlot");
    slot.hidden = false;
    moveControls("sidebarSlot");
    document.getElementById("summary").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  document.getElementById("mapClose").addEventListener("click", closeMap);
  document.getElementById("editSearch").addEventListener("click", () => {
    const slot = document.getElementById("sidebarSlot");
    slot.hidden = !slot.hidden;
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !document.getElementById("mapView").hidden) closeMap();
  });
  document.getElementById("brand").addEventListener("click", showHome);

  document.getElementById("lang").addEventListener("click", (e) => {
    const button = e.target.closest("button[data-lang]");
    if (!button) return;
    setLang(button.dataset.lang);
    applyLang();
  });
}

async function boot() {
  wireEverything();
  applyMode();
  applyLang();
  document.querySelectorAll('input[type="range"]').forEach(paintSlider);
  document.getElementById("gain").disabled = state.gainAny;

  try {
    const data = await (await fetch("/api/options")).json();
    const view = data.default_view;
    if (view) map.setView(view.center, view.zoom);
    // How far is a domain figure, kept beside the calibration it came from.
    if (data.default_distance_km) state.defaultDistance = data.default_distance_km;
    if (data.distance_range_km) state.distanceRange = data.distance_range_km;
    syncDistanceToSport();
  } catch (err) {
    /* the map keeps its built-in starting view */
  }

  showHome();
}

boot();
