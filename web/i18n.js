// Italian is the default: the first users are in Rome and Milan Facebook
// groups, where an English-only tool reads as foreign.
const STRINGS = {
  it: {
    headline: "Dove vuoi andare oggi?",
    tagline: "Trova il percorso giusto. Distanza, dislivello, fondo — e l'aria che respiri.",
    heroNote: "Ovunque ci sia una strada · corsa e bici · GPX da scaricare · nessun account",

    pitchTitle: "Non è un pianificatore di percorsi. È un modo di sceglierli.",
    pitch1Title: "Cinque percorsi, in classifica",
    pitch1Body:
      "Non uno solo. Ne generiamo una dozzina, teniamo i migliori cinque e ti mostriamo il punteggio voce per voce — così puoi anche non essere d'accordo.",
    pitch2Title: "Traffico e aria, non solo chilometri",
    pitch2Body:
      "Quanto resti lontano dalle auto, che aria respiri, quanto vento prendi in faccia. Più le fontanelle lungo la strada.",
    pitch3Title: "Conta anche quando esci",
    pitch3Body:
      "Le stesse strade non sono uguali alle 8 del mattino e alle 18. Il punteggio parte dalle condizioni di adesso, non da una media.",
    pitchFoot: "Gratis, senza account. I dati sono di OpenStreetMap.",

    loopToggle: "Voglio partire e tornare nello stesso punto",
    pickOnMap: "Scegli sulla mappa",
    pickStartTitle: "Scegli la partenza",
    pickEndTitle: "Scegli l'arrivo",
    pickConfirm: "Usa questo punto",

    sport: "Sport",
    running: "Corsa",
    cycling: "Bici",

    start: "Partenza",
    end: "Destinazione",
    startPlaceholder: "Indirizzo, parco, piazza…",
    endPlaceholder: "Dove vuoi arrivare",
    startHint: "Tocca la mappa per spostare la partenza",
    mapHintStart: "Tocca la mappa per scegliere la partenza",
    mapHintEnd: "Tocca la mappa per scegliere l'arrivo",
    locate: "Usa la mia posizione",
    searching_place: "Cerco…",
    noPlaces: "Nessun risultato",
    needStart: "Scegli un punto di partenza.",
    needEnd: "Scegli una destinazione.",

    distance: "Distanza",
    gain: "Dislivello positivo",
    gainAny: "Non mi interessa",
    directAsPossible: "Il più diretto possibile",
    surface: "Fondo",
    asphalt: "Asfalto",
    mixed: "Misto",
    trail: "Sterrato",

    search: "Cerca il percorso",
    searchAgain: "Nuova ricerca",
    searching: "Sto cercando…",
    results: "Risultati",
    noResults: "Nessun percorso trovato. Prova a cambiare distanza o punto di partenza.",
    download: "Scarica GPX",

    scoreDistance: "Distanza",
    scoreDirectness: "Diretto",
    scoreGain: "Dislivello",
    scoreGainFlat: "Poco dislivello",
    scoreSurface: "Fondo",
    scoreTraffic: "Lontano dal traffico",
    scoreWind: "Vento a favore",
    scoreAir: "Aria",
    scoreWater: "Acqua lungo il percorso",
    scoreScenery: "Cose belle da vedere",
    paved: "asfaltato",
    trafficLow: "poco traffico",
    trafficMid: "traffico medio",
    trafficHigh: "molto traffico",

    temp: "Temperatura",
    wind: "Vento",
    aqi: "Indice aria (EAQI)",
    pm25: "PM2.5",
    airFlat:
      "L'aria è uguale su tutti questi percorsi: la griglia dei dati è più larga del tuo giro, quindi non entra nel punteggio. Quello che cambia davvero da strada a strada è la vicinanza al traffico.",
    airVaries: "L'aria cambia tra questi percorsi, quindi pesa nel punteggio.",
    noExactDistance:
      "Nessun giro esattamente della distanza chiesta: qui sotto i più vicini.",
    altsUnavailable: "Un solo percorso possibile tra questi due punti.",
    error: "Qualcosa è andato storto",
    keyMissing:
      "Manca la chiave OpenRouteService. Imposta ORS_API_KEY e riavvia il server.",
    rateLimited: "Troppe richieste verso il servizio di routing. Riprova fra un minuto.",
    quotaSpent: "Quota giornaliera di OpenRouteService esaurita. Si azzera domani. Un giro ad anello costa 12 chiamate, un percorso A→B una sola.",

    poiTitle: "Punti utili",
    poiLoading: "Cerco i punti utili…",
    poiNone: "Nessun punto utile trovato lungo questo percorso.",
    poiFailed: "Non sono riuscito a caricare i punti utili (il servizio OpenStreetMap era occupato).",
    poiRetry: "Riprova",
    poiExpired: "I risultati sono scaduti. Rifai la ricerca per vedere i punti utili.",
    poi_water: "Acqua",
    poi_toilets: "Bagni",
    poi_viewpoint: "Panorami",
    poi_monument: "Monumenti",
    poi_green: "Verde",
    poi_art: "Arte",
    poi_bike: "Ciclofficine",

    map_clean: "Pulita",
    map_satellite: "Satellite",
    disclaimer: "Percorsi generati automaticamente, non verificati da nessuno: guardali prima di seguirli. Le fontanelle segnate potrebbero non esserci o non essere potabili — porta acqua.",
    footer: "Dati: OpenStreetMap, OpenRouteService, Open-Meteo.",
  },

  en: {
    headline: "Where do you want to go today?",
    tagline: "Find the right route. Distance, climb, surface — and the air you breathe.",
    heroNote: "Anywhere there is a road · running and cycling · GPX to download · no account",

    pitchTitle: "Not a route planner. A way of choosing between routes.",
    pitch1Title: "Five routes, ranked",
    pitch1Body:
      "Not one. We generate a dozen, keep the best five, and show you the score line by line — so you can disagree with it.",
    pitch2Title: "Traffic and air, not just kilometres",
    pitch2Body:
      "How far you stay from cars, what you breathe, how much wind you take in the face. Plus the drinking fountains along the way.",
    pitch3Title: "When you go out matters",
    pitch3Body:
      "The same streets are not the same at 8am and at 6pm. The score starts from conditions right now, not from an average.",
    pitchFoot: "Free, no account. Data from OpenStreetMap.",

    loopToggle: "I want to start and finish in the same place",
    pickOnMap: "Pick on the map",
    pickStartTitle: "Pick the start",
    pickEndTitle: "Pick the destination",
    pickConfirm: "Use this point",

    sport: "Sport",
    running: "Running",
    cycling: "Cycling",

    start: "Start",
    end: "Destination",
    startPlaceholder: "Address, park, square…",
    endPlaceholder: "Where you want to end up",
    startHint: "Tap the map to move the start",
    mapHintStart: "Tap the map to set the start",
    mapHintEnd: "Tap the map to set the finish",
    locate: "Use my location",
    searching_place: "Searching…",
    noPlaces: "No matches",
    needStart: "Pick a start point.",
    needEnd: "Pick a destination.",

    distance: "Distance",
    gain: "Elevation gain",
    gainAny: "Don't care",
    directAsPossible: "As direct as possible",
    surface: "Surface",
    asphalt: "Asphalt",
    mixed: "Mixed",
    trail: "Trail",

    search: "Find routes",
    searchAgain: "New search",
    searching: "Searching…",
    results: "Results",
    noResults: "No routes found. Try a different distance or start point.",
    download: "Download GPX",

    scoreDistance: "Distance",
    scoreDirectness: "Directness",
    scoreGain: "Climb",
    scoreGainFlat: "Less climb",
    scoreSurface: "Surface",
    scoreTraffic: "Away from traffic",
    scoreWind: "Wind",
    scoreAir: "Air",
    scoreWater: "Water on the way",
    scoreScenery: "Worth looking at",
    paved: "paved",
    trafficLow: "low traffic",
    trafficMid: "medium traffic",
    trafficHigh: "heavy traffic",

    temp: "Temperature",
    wind: "Wind",
    aqi: "Air index (EAQI)",
    pm25: "PM2.5",
    airFlat:
      "Air quality is identical across these routes — the data grid is wider than your route, so it stays out of the score. What actually varies street by street is traffic proximity.",
    airVaries: "Air quality differs across these routes, so it counts in the score.",
    noExactDistance: "No loop matched the exact distance — closest ones below.",
    altsUnavailable: "Only one route is possible between these two points.",
    error: "Something went wrong",
    keyMissing: "OpenRouteService key missing. Set ORS_API_KEY and restart the server.",
    rateLimited: "Too many requests to the routing service. Try again in a minute.",
    quotaSpent: "OpenRouteService daily quota is used up. It resets tomorrow. A loop costs 12 calls; an A-to-B route costs one.",

    poiTitle: "Useful points",
    poiLoading: "Looking for useful points…",
    poiNone: "No useful points found along this route.",
    poiFailed: "Couldn't load the useful points — the OpenStreetMap service was busy.",
    poiRetry: "Try again",
    poiExpired: "These results have expired. Search again to see the useful points.",
    poi_water: "Water",
    poi_toilets: "Toilets",
    poi_viewpoint: "Viewpoints",
    poi_monument: "Monuments",
    poi_green: "Green",
    poi_art: "Art",
    poi_bike: "Bike repair",

    map_clean: "Clean",
    map_satellite: "Satellite",
    disclaimer: "Routes are generated automatically and checked by nobody: look at one before you follow it. Fountains shown here may not exist or may not be drinkable — carry water.",
    footer: "Data: OpenStreetMap, OpenRouteService, Open-Meteo.",
  },
};

let currentLang = localStorage.getItem("doiamo_lang") || "it";

function t(key) {
  return (STRINGS[currentLang] && STRINGS[currentLang][key]) || STRINGS.en[key] || key;
}

function setLang(lang) {
  currentLang = STRINGS[lang] ? lang : "it";
  try {
    localStorage.setItem("doiamo_lang", currentLang);
  } catch (err) {
    /* private browsing */
  }
}

function getLang() {
  return currentLang;
}
