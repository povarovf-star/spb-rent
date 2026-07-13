const state = {
  assets: null,
  mapData: null,
  mapDataPromise: null,
  map: null,
  mapMetric: "ppm2",
  scanVerdict: "overpriced",
  scanCache: {},
};

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const defaultMapView = {
  center: [30.32, 59.94],
  zoom: 9.15,
};

const verdictLabels = {
  fair: "В пределах рынка",
  overpriced: "Похоже на переплату",
  suspicious_cheap: "Подозрительно дёшево",
};

const materialLabels = {
  block: "Блочный",
  brick: "Кирпичный",
  monolith: "Монолит",
  monolithBrick: "Монолит-кирпич",
  old: "Старый фонд",
  panel: "Панельный",
  stalin: "Сталинка",
};

function $(selector) {
  return document.querySelector(selector);
}

function $all(selector) {
  return Array.from(document.querySelectorAll(selector));
}

function rub(value, suffix = " ₽") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${Math.round(Number(value)).toLocaleString("ru-RU")}${suffix}`;
}

function signedRub(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const rounded = Math.round(Number(value));
  return `${rounded > 0 ? "+" : ""}${rounded.toLocaleString("ru-RU")} ₽`;
}

function pct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`;
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
}

function option(value, label = value) {
  const node = document.createElement("option");
  node.value = value;
  node.textContent = label;
  return node;
}

function populateSelects() {
  const district = $("#district");
  const districts = state.assets.district_options.filter((name) => name !== "unknown");
  if (state.assets.district_options.includes("unknown")) {
    district.append(option("unknown", "Не знаю"));
  }
  districts.forEach((name) => district.append(option(name)));
  district.value = districts.includes("Петроградский") ? "Петроградский" : districts[0];

  const metro = $("#metro_name");
  metro.append(option("unknown", "Не знаю"));
  state.assets.metro_stations.forEach((name) => metro.append(option(name)));

  const material = $("#material_type");
  material.append(option("unknown", "Не знаю"));
  state.assets.material_types.forEach((name) => material.append(option(name, materialLabels[name] || name)));
}

function updateMetrics() {
  const model = state.assets.model || {};
  setText("metric-clean", rub(model.n_clean || 15415, ""));
  setText("metric-mdape", pct(model.mdape || 10.4));
  setText("metric-mae", rub(model.mae || 8606));
  setText("metric-coverage", pct(model.interval_coverage || 77));
  updateScanSummary(state.assets.scan || {});
}

function updateScanSummary(summary) {
  setText("scan-total", rub(summary.total, ""));
  setText("scan-fair", rub(summary.fair, ""));
  setText("scan-overpriced", rub(summary.overpriced, ""));
  setText("scan-cheap", rub(summary.suspicious_cheap, ""));
}

function prefetchMapData() {
  if (!state.mapDataPromise) {
    state.mapDataPromise = fetch("/api/map").then((r) => r.json());
  }
  return state.mapDataPromise;
}

function prefetchTab(tab) {
  // warm start on hover: by the time of the click, the map and data are on the way
  if (tab === "map") {
    loadMapLibre();
    prefetchMapData();
  }
  if (tab === "market") fetchScan(state.scanVerdict);
}

function bindTabs() {
  $all(".tab").forEach((button) => {
    button.addEventListener("pointerenter", () => prefetchTab(button.dataset.tab));
    button.addEventListener("click", () => {
      $all(".tab").forEach((item) => item.classList.remove("is-active"));
      $all(".tab-panel").forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");
      $(`#tab-${button.dataset.tab}`).classList.add("is-active");
      if (button.dataset.tab === "map") ensureMap();
      if (button.dataset.tab === "market") loadScan();
    });
  });
}

function bindSegments() {
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-segment] button");
    if (!button) return;
    const group = button.closest("[data-segment]");
    group.querySelectorAll("button").forEach((item) => item.classList.remove("is-selected"));
    button.classList.add("is-selected");

    const name = group.dataset.segment;
    if (name === "rooms") {
      $("#rooms").value = button.dataset.value;
    }
    if (name === "mapMetric") {
      state.mapMetric = button.dataset.value;
      updateMapMetric();
    }
    if (name === "scanVerdict") {
      state.scanVerdict = button.dataset.value;
      loadScan();
    }
  });
}

function payloadFromForm() {
  const actualRaw = $("#actual_price").value;
  const buildYearRaw = $("#build_year").value;
  return {
    district: $("#district").value,
    rooms: Number($("#rooms").value),
    total_area: Number($("#total_area").value),
    floor: Number($("#floor").value),
    floors_total: Number($("#floors_total").value),
    metro_walk_min: Number($("#metro_walk_min").value),
    metro_name: $("#metro_name").value,
    build_year: buildYearRaw ? Number(buildYearRaw) : null,
    material_type: $("#material_type").value,
    is_apartments: $("#is_apartments").checked,
    is_by_homeowner: $("#is_by_homeowner").checked,
    dishwasher: $("#dishwasher").checked,
    furnished: $("#furnished").checked,
    renov_euro: $("#renov_euro").checked,
    balcony: $("#balcony").checked,
    actual_price: actualRaw ? Number(actualRaw) : null,
  };
}

function validatePayload(payload) {
  if (payload.floor > payload.floors_total) return "Этаж не может быть больше этажности дома.";
  const range = state.assets.form_ranges?.total_area;
  if (range && (payload.total_area < range[0] || payload.total_area > range[1])) {
    return `Площадь вне обучающего диапазона: ${range[0].toFixed(0)}-${range[1].toFixed(0)} м².`;
  }
  return "";
}

function bindForm() {
  $("#estimate-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = payloadFromForm();
    const error = validatePayload(payload);
    $("#form-error").textContent = error;
    if (error) return;

    const button = $(".primary-action");
    button.classList.add("is-loading");
    button.textContent = "Сверяю с рынком…";
    try {
      const response = await fetch("/api/predict", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "API вернул ошибку");
      }
      renderResult(data);
    } catch (err) {
      $("#form-error").textContent = `Не удалось получить оценку: ${err.message}`;
    } finally {
      button.classList.remove("is-loading");
      button.textContent = "Проверить цену";
    }
  });
}

// calm price counting: first write the final value synchronously (always works),
// then a short animation as progressive enhancement on top
function animateRub(id, target) {
  const node = document.getElementById(id);
  if (!node) return;
  node.textContent = rub(target);
  const value = Number(target);
  if (!Number.isFinite(value) || reducedMotion.matches) return;
  const from = value * 0.9;
  const duration = 380;
  let start = null;
  const tick = (now) => {
    if (start === null) start = now;
    const t = Math.min((now - start) / duration, 1);
    const eased = 1 - (1 - t) * (1 - t) * (1 - t);
    const current = Math.round((from + (value - from) * eased) / 100) * 100;
    node.textContent = rub(t < 1 ? current : value);
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function renderResult(result) {
  const firstRender = !$("#empty-result").hidden;
  $("#empty-result").hidden = true;
  const content = $("#result-content");
  content.hidden = false;
  if (firstRender && !reducedMotion.matches) {
    content.classList.remove("is-entering");
    void content.offsetWidth; // restart the CSS reveal animation
    content.classList.add("is-entering");
  }

  animateRub("fair-price", result.fair_price);
  setText("price-low", rub(result.price_low));
  setText("price-high", rub(result.price_high));
  setText("actual-price", result.actual_price ? rub(result.actual_price) : "Не указана");

  const delta = document.getElementById("delta-price");
  if (delta) {
    const deltaValue = result.delta_vs_fair;
    delta.textContent = deltaValue !== undefined && deltaValue !== null ? signedRub(deltaValue) : "—";
    // highlight the delta only when the verdict is actually alarming
    delta.className = "";
    if (result.verdict === "overpriced") delta.classList.add("is-over");
    if (result.verdict === "suspicious_cheap") delta.classList.add("is-under");
  }

  const chip = $("#verdict-chip");
  chip.className = `verdict-chip ${result.verdict || "neutral"}`;
  chip.textContent = result.verdict ? verdictLabels[result.verdict] : "Цена не указана";

  const spread = Math.max(Number(result.price_high) - Number(result.price_low), 1);
  const toTrack = (value) =>
    `${Math.min(Math.max((Number(value) - Number(result.price_low)) / spread, 0), 1) * 100}%`;

  const fairMarker = $("#fair-marker");
  if (fairMarker) fairMarker.style.left = toTrack(result.fair_price);

  const marker = $("#actual-marker");
  if (result.actual_price && result.price_low && result.price_high) {
    marker.style.left = toTrack(result.actual_price);
    marker.hidden = false;
  } else {
    marker.hidden = true;
  }

  if (result.warning) {
    $("#result-note").textContent = result.warning;
  } else if (result.verdict_text) {
    $("#result-note").textContent = result.verdict_text.charAt(0).toUpperCase()
      + result.verdict_text.slice(1) + ".";
  } else {
    $("#result-note").textContent = "Коридор показывает разброс цен похожих квартир, а не гарантию.";
  }

  renderFactors(result.factors || {});
}

function renderFactors(factors) {
  const list = $("#factor-list");
  list.replaceChildren();
  const entries = Object.entries(factors);
  const maxAbs = Math.max(...entries.map(([, value]) => Math.abs(Number(value))), 1);
  entries.forEach(([name, value]) => {
    const amount = Number(value);
    const row = document.createElement("div");
    row.className = "factor-row";
    const direction = amount >= 0 ? "positive" : "negative";
    const width = Math.max(Math.abs(amount) / maxAbs * 48, 2);
    row.innerHTML = `
      <div class="factor-name">${name}</div>
      <div class="factor-track">
        <div class="factor-fill ${direction}" style="width:0%"></div>
      </div>
      <div class="factor-value ${direction}">${signedRub(amount)}</div>
    `;
    list.append(row);
    const fill = row.querySelector(".factor-fill");
    // a synchronous reflow locks in width:0, then the transition draws the bar to target;
    // requestAnimationFrame is avoided because it does not tick in background tabs
    void fill.offsetWidth;
    fill.style.width = `${width}%`;
  });
}

// MapLibre (~250 KB) is loaded only when the map is first opened,
// so it does not slow down the main price-check screen
function loadMapLibre() {
  if (window.maplibregl) return Promise.resolve();
  if (state.maplibrePromise) return state.maplibrePromise;
  state.maplibrePromise = new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css";
    document.head.append(css);
    const script = document.createElement("script");
    script.src = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js";
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("MapLibre не загрузился"));
    document.head.append(script);
  });
  return state.maplibrePromise;
}

async function ensureMap() {
  if (state.map) {
    resizeMapSoon();
    return;
  }
  try {
    const [mapData] = await Promise.all([prefetchMapData(), loadMapLibre()]);
    state.mapData = mapData;
    setText("map-count", rub(state.mapData.meta.count, ""));
    if (!window.maplibregl) throw new Error("MapLibre не загружен");
    state.map = new maplibregl.Map({
      // Voyager: OSM data with streets and district labels reads better as a
      // "map of the area" than the pale Positron, while staying light under the hexagons
      container: "price-map",
      style: "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
      center: defaultMapView.center,
      zoom: defaultMapView.zoom,
      attributionControl: true,
    });
    resizeMapSoon(true);
    state.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    state.map.on("load", () => {
      state.map.addSource("hexes", { type: "geojson", data: state.mapData, generateId: true });
      state.map.addLayer({
        id: "hex-fill",
        type: "fill",
        source: "hexes",
        paint: {
          "fill-color": mapColorExpression("ppm2"),
          "fill-color-transition": { duration: 250 },
          // dense fill so the whole city reads as a heat map;
          // brighter on hover, but the Voyager streets still show through
          "fill-opacity": [
            "case", ["boolean", ["feature-state", "hover"], false], 0.9, 0.72,
          ],
          "fill-opacity-transition": { duration: 150 },
        },
      });
      state.map.addLayer({
        id: "hex-line",
        type: "line",
        source: "hexes",
        paint: {
          "line-color": "rgba(255,255,255,0.82)",
          "line-width": 0.7,
        },
      });
      state.map.on("click", "hex-fill", mapPopup);
      bindHexHover();
      updateMapMetric();
      resizeMapSoon(true);
    });
  } catch (err) {
    $("#map-fallback").hidden = false;
    $("#map-fallback").textContent = `Карта не загрузилась: ${err.message}`;
  }
}

function resizeMapSoon(resetView = false) {
  if (!state.map) return;
  const apply = () => {
    if (!state.map) return;
    state.map.resize();
    if (resetView) state.map.jumpTo(defaultMapView);
  };
  requestAnimationFrame(apply);
  setTimeout(apply, 120);
  setTimeout(apply, 600);
  setTimeout(apply, 1200);
}

function mapColorExpression(metric) {
  const meta = state.mapData?.meta?.[metric] || { low: 0, high: 1 };
  // warm scale in the product palette: light ochre -> terracotta -> burnt clay
  return [
    "interpolate",
    ["linear"],
    ["to-number", ["get", metric]],
    meta.low,
    "#ecdfc3",
    (meta.low + meta.high) / 2,
    "#d97757",
    meta.high,
    "#8c3b1b",
  ];
}

function updateMapMetric() {
  const metric = state.mapMetric;
  setText("map-layer-label", metric === "ppm2" ? "₽/м²" : "₽/мес");
  const meta = state.mapData?.meta?.[metric];
  if (meta) {
    setText("legend-low", rub(meta.low, metric === "ppm2" ? "" : " ₽"));
    setText("legend-high", rub(meta.high, metric === "ppm2" ? "" : " ₽"));
  }
  if (state.map?.getLayer("hex-fill")) {
    state.map.setPaintProperty("hex-fill", "fill-color", mapColorExpression(metric));
  }
}

function bindHexHover() {
  // подсветка гексагона + лёгкая подсказка, следующая за курсором
  let hoveredId = null;
  const tip = new maplibregl.Popup({
    closeButton: false, closeOnClick: false, className: "hex-tip", offset: 8,
  });
  state.map.on("mousemove", "hex-fill", (event) => {
    state.map.getCanvas().style.cursor = "pointer";
    const f = event.features[0];
    if (hoveredId !== null) {
      state.map.setFeatureState({ source: "hexes", id: hoveredId }, { hover: false });
    }
    hoveredId = f.id;
    state.map.setFeatureState({ source: "hexes", id: hoveredId }, { hover: true });
    const p = f.properties;
    const value = state.mapMetric === "ppm2" ? rub(p.ppm2, " ₽/м²") : rub(p.price_median);
    tip.setLngLat(event.lngLat)
      .setHTML(`<strong>${value}</strong> · ${rub(p.n, "")} об.`)
      .addTo(state.map);
  });
  state.map.on("mouseleave", "hex-fill", () => {
    state.map.getCanvas().style.cursor = "";
    if (hoveredId !== null) {
      state.map.setFeatureState({ source: "hexes", id: hoveredId }, { hover: false });
    }
    hoveredId = null;
    tip.remove();
  });
}

function mapPopup(event) {
  const p = event.features[0].properties;
  const metricLine = state.mapMetric === "ppm2"
    ? `${rub(p.ppm2, " ₽/м²")}`
    : `${rub(p.price_median)}`;
  new maplibregl.Popup()
    .setLngLat(event.lngLat)
    .setHTML(`
      <strong>${metricLine}</strong><br />
      Медианная цена: ${rub(p.price_median)}<br />
      Объявлений: ${rub(p.n, "")}
    `)
    .addTo(state.map);
}

function fetchScan(verdict) {
  if (!state.scanCache[verdict]) {
    state.scanCache[verdict] = fetch(`/api/scan?verdict=${verdict}&limit=60`)
      .then((r) => r.json())
      .catch((err) => {
        delete state.scanCache[verdict];
        throw err;
      });
  }
  return state.scanCache[verdict];
}

async function loadScan() {
  const table = $("#scan-table");
  const verdict = state.scanVerdict;
  if (!state.scanCache[verdict]) {
    table.innerHTML = `<tr><td colspan="6">Загружаю объявления…</td></tr>`;
  }
  try {
    const data = await fetchScan(verdict);
    if (state.scanVerdict !== verdict) return; // пользователь уже переключился
    updateScanSummary(data.summary || {});
    renderScanRows(data.items || []);
  } catch (err) {
    table.innerHTML = `<tr><td colspan="6">Не удалось загрузить скан рынка: ${err.message}</td></tr>`;
  }
}

function renderScanRows(items) {
  const table = $("#scan-table");
  if (!items.length) {
    table.innerHTML = `<tr><td colspan="6">В этом сегменте нет объявлений.</td></tr>`;
    return;
  }
  table.replaceChildren();
  items.forEach((item) => {
    const tr = document.createElement("tr");
    const rooms = Number(item.rooms_n) === 0 ? "Студия" : `${Number(item.rooms_n).toFixed(0)}-комн.`;
    const area = Number(item.total_area).toLocaleString("ru-RU", { maximumFractionDigits: 1 });
    const deltaClass = Number(item.delta) >= 0 ? "delta-positive" : "delta-negative";
    const source = item.url
      ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">объявление</a>`
      : (item.offer_id || "");
    tr.innerHTML = `
      <td>${item.district && item.district !== "unknown" ? item.district : "—"}<span class="meta">${source}</span></td>
      <td>${item.metro_name && item.metro_name !== "unknown" ? item.metro_name : "—"}</td>
      <td>${rooms}<span class="meta">${area} м²</span></td>
      <td class="num">${rub(item.price)}</td>
      <td class="num">${rub(item.fair_price)}<span class="meta">${rub(item.price_low, "")}–${rub(item.price_high)}</span></td>
      <td class="num ${deltaClass}">${signedRub(item.delta)}<span class="meta">${Number(item.delta_pct) > 0 ? "+" : ""}${pct(item.delta_pct)}</span></td>
    `;
    table.append(tr);
  });
}

async function init() {
  bindTabs();
  bindSegments();
  bindForm();
  const response = await fetch("/api/assets");
  state.assets = await response.json();
  populateSelects();
  updateMetrics();
}

init().catch((err) => {
  document.body.innerHTML = `<main class="app-shell"><section class="panel empty-result"><h1>SPb Rent</h1><p>Не удалось загрузить приложение: ${err.message}</p></section></main>`;
});
