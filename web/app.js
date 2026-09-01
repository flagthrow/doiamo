const state = {
  view: "home",
  mode: "route",   // both endpoints by default; the checkbox makes it a loop
  sport: "running",
  surface: "asphalt",
  sights: "both",
  distanceKm: 10,
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

function remember(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (err) {
    /* private browsing */
  }
}

state.mapStyle = stored("doiamo_map_style", "clean", STYLE_ORDER);

const map = L.map("map", { zoomControl: true, attributionControl: true })
  .setView([45.4642, 9.19], 13);

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
  }).setView([45.4705, 9.1830], 13);

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
  state.distanceKm = km;
  const input = document.getElementById("distance");
  input.value = km;
  document.getElementById("distanceValue").textContent = km;
  paintSlider(input);
}

// ---------------------------------------------------------------- views
function moveControls(target) {
  document.getElementById(target).appendChild(document.getElementById("controls"));
}

function showHome() {
  state.view = "home";
  document.body.dataset.view = "home";
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
}

function showApp() {
  state.view = "app";
  document.body.dataset.view = "app";
  moveControls("sidebarSlot");
  document.getElementById("sidebarSlot").hidden = true;
  document.getElementById("newSearch").hidden = false;
  renderSummary();
  updateMapHint();
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

function bar(label, value) {
  const pct = Math.round((value || 0) * 100);
  const row = document.createElement("div");
  row.className = "bar";
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
  num.textContent = pct;
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
  const hasDistanceTarget = query.distance_km !== null && query.distance_km !== undefined;
  const hasGainTarget = query.elevation_gain_m !== null && query.elevation_gain_m !== undefined;
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
    sub.textContent =
      Math.round(route.paved_share * 100) + "% " + t("paved") + " · " +
      trafficLabel(route.traffic_exposure);
    main.append(title, sub);

    head.append(rank, main, scoreRing(route.scores.total));

    const bars = document.createElement("div");
    bars.className = "bars";
    // The same number means different things depending on what was asked for:
    // a match against your target, or a comparison against the siblings.
    bars.appendChild(bar(
      hasDistanceTarget ? t("scoreDistance") : t("scoreDirectness"),
      route.scores.distance
    ));
    if (hasGainTarget || !isLoop) {
      bars.appendChild(bar(
        hasGainTarget ? t("scoreGain") : t("scoreGainFlat"),
        route.scores.gain
      ));
    }
    bars.appendChild(bar(t("scoreSurface"), route.scores.surface));
    bars.appendChild(bar(t("scoreTraffic"), route.scores.traffic));
    bars.appendChild(bar(t("scoreWind"), route.scores.wind));
    if (
      route.scores.air !== null && route.scores.air !== undefined &&
      lastPayload.air.differentiates_routes
    ) {
      bars.appendChild(bar(t("scoreAir"), route.scores.air));
    }
    const extra = state.poiScores[route.id];
    if (extra) {
      bars.appendChild(bar(t("scoreWater"), extra.water));
      // Only the axis the viewer asked for is scored, so only it is shown —
      // a bar for something that did not count would be a lie.
      const wanted = (lastPayload.query || {}).sights;
      if (extra.sights !== null && extra.sights !== undefined) {
        const label = wanted === "monuments" ? t("scoreMonuments")
                    : wanted === "nature" ? t("scoreNature")
                    : t("scoreSights");
        bars.appendChild(bar(label, extra.sights));
      }
    }

    const counts = (state.poiCounts && state.poiCounts[route.id]) || {};
    const present = state.poiKinds.filter((kind) => (counts[kind] || 0) > 0);
    let badgeRow = null;
    if (present.length) {
      badgeRow = document.createElement("div");
      badgeRow.className = "poi-badges";
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

    card.append(head, bars);
    if (badgeRow) card.appendChild(badgeRow);
    card.appendChild(actions);
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
  };
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
      else if (reason.includes("quota")) message(t("quotaSpent"), "warn", true);
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
    message(notes.join(" "), notes.length ? "warn" : "");

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

// ---------------------------------------------------------------- i18n
function applyLang() {
  document.documentElement.lang = getLang();
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("#lang button").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.lang === getLang()));
  });
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
    // A cyclist asking for 10 km is not asking for the same ride a runner is.
    const distance = document.getElementById("distance");
    distance.max = state.sport === "cycling" ? 120 : 60;
    if (state.sport === "cycling" && state.distanceKm < 15) setDistance(30);
    else if (state.sport === "running" && state.distanceKm > 60) setDistance(10);
    else paintSlider(distance);
  });

  wirePlaceField("start");
  wirePlaceField("end");

  document.getElementById("distance").addEventListener("input", (e) => {
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
  document.getElementById("newSearch").addEventListener("click", showHome);
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
  } catch (err) {
    /* the map keeps its built-in starting view */
  }

  showHome();
}

boot();
