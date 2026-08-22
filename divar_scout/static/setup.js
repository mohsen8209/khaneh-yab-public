const state = {
  auth: null,
  profile: null,
  districts: [],
  districtById: new Map(),
  selectedDistricts: new Set(),
  districtView: "list",
  districtMap: null,
  districtMarkers: new Map(),
  districtLabelLayer: null,
  districtBounds: null,
  mapSearchTimer: null,
  step: 1,
};

const fa = new Intl.NumberFormat("fa-IR", { maximumFractionDigits: 1 });
const tomanScale = 1_000_000;
const requirementLabels = {
  required: "الزامی",
  preferred: "مزیت است",
  irrelevant: "مهم نیست",
};

async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (method !== "GET" && state.auth?.csrf_token) {
    headers["X-CSRF-Token"] = state.auth.csrf_token;
  }
  const response = await fetch(path, { ...options, method, headers, cache: "no-store" });
  const data = await response.json();
  if (response.status === 401) {
    location.replace("/login");
    throw new Error("نشست ورود پایان یافته است");
  }
  if (response.status === 403 && ["pending", "rejected", "suspended"].includes(data.code)) {
    location.replace("/pending");
    throw new Error(data.error);
  }
  if (!response.ok) throw new Error(data.error || "درخواست انجام نشد");
  return data;
}

function showMessage(target, message) {
  const element = document.querySelector(target);
  element.textContent = message;
  element.hidden = !message;
  if (message) element.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function normalizeText(value) {
  return String(value || "")
    .replaceAll("ي", "ی")
    .replaceAll("ك", "ک")
    .replace(/[\u200c\s-]+/g, " ")
    .trim()
    .toLocaleLowerCase("fa");
}

function selectedValue(name) {
  return document.querySelector(`input[name="${name}"]:checked`)?.value || "";
}

function setRadio(name, value) {
  const input = document.querySelector(`input[name="${name}"][value="${value}"]`);
  if (input) input.checked = true;
}

function setStep(step) {
  state.step = step;
  document.querySelectorAll(".setup-step").forEach((section) => {
    section.hidden = Number(section.dataset.step) !== step;
  });
  document.querySelectorAll("[data-progress]").forEach((item) => {
    const itemStep = Number(item.dataset.progress);
    item.classList.toggle("active", itemStep === step);
    item.classList.toggle("done", itemStep < step);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function setField(id, value) {
  if (value === null || value === undefined || Number.isNaN(value)) return;
  const field = document.querySelector(`#${id}`);
  if (field) field.value = String(value);
}

function renderDistrictScope() {
  const picker = document.querySelector("#district-picker");
  picker.hidden = selectedValue("district-scope") !== "selected";
  updateDistrictCount();
  if (!picker.hidden && state.districtView === "map") {
    requestAnimationFrame(() => setDistrictView("map"));
  }
}

function renderDistricts() {
  const query = normalizeText(document.querySelector("#district-search").value);
  const visible = state.districts.filter((district) => normalizeText(district.name).includes(query));
  const list = document.querySelector("#district-list");
  list.replaceChildren();
  visible.forEach((district) => {
    const label = document.createElement("label");
    label.className = "district-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.districtId = String(district.id);
    input.checked = state.selectedDistricts.has(Number(district.id));
    input.addEventListener("change", () => {
      setDistrictSelected(Number(district.id), input.checked);
    });
    const content = document.createElement("span");
    const name = document.createElement("b");
    name.textContent = district.name;
    const rating = document.createElement("small");
    rating.textContent = `${district.quality} · نمره ${fa.format(district.score)} از ۸`;
    content.append(name, rating);
    label.append(input, content);
    list.append(label);
  });
  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "district-empty";
    empty.textContent = "محله‌ای با این نام پیدا نشد";
    list.append(empty);
  }
}

function updateDistrictCount() {
  document.querySelector("#district-count").textContent = `${fa.format(state.selectedDistricts.size)} محله انتخاب شده`;
  document.querySelector("#district-clear").hidden = state.selectedDistricts.size === 0;
}

function setDistrictSelected(districtId, selected) {
  const id = Number(districtId);
  if (!state.districtById.has(id)) return;
  if (selected) state.selectedDistricts.add(id);
  else state.selectedDistricts.delete(id);
  document.querySelectorAll(`input[data-district-id="${id}"]`).forEach((input) => {
    input.checked = selected;
  });
  updateDistrictCount();
  syncDistrictMapMarker(id);
  renderDistrictMapLabels();
}

function clearDistrictSelection() {
  const changed = [...state.selectedDistricts];
  state.selectedDistricts.clear();
  document.querySelectorAll("input[data-district-id]").forEach((input) => {
    input.checked = false;
  });
  changed.forEach(syncDistrictMapMarker);
  updateDistrictCount();
  renderDistrictMapLabels();
}

function districtColor(score) {
  if (Number(score) >= 6.8) return "#187659";
  if (Number(score) >= 5.2) return "#245a91";
  if (Number(score) >= 3.7) return "#b46b12";
  return "#777f84";
}

function districtMatchesQuery(district) {
  const query = normalizeText(document.querySelector("#district-search").value);
  return !query || normalizeText(district.name).includes(query);
}

function districtMarkerStyle(district) {
  const selected = state.selectedDistricts.has(Number(district.id));
  const matches = districtMatchesQuery(district);
  const zoom = state.districtMap?.getZoom() || 10;
  return {
    radius: selected ? Math.max(7, Math.min(9, zoom - 4)) : Math.max(3.5, Math.min(6, zoom - 7)),
    color: selected ? "#ffffff" : districtColor(district.score),
    weight: selected ? 2.5 : 1,
    opacity: matches || selected ? 1 : 0.24,
    fillColor: selected ? "#b3262d" : districtColor(district.score),
    fillOpacity: matches || selected ? 0.9 : 0.16,
  };
}

function syncDistrictMapMarker(districtId) {
  const district = state.districtById.get(Number(districtId));
  const marker = state.districtMarkers.get(Number(districtId));
  if (district && marker) {
    marker.setStyle(districtMarkerStyle(district));
    if (state.selectedDistricts.has(Number(districtId))) marker.bringToFront();
  }
}

function syncDistrictMapMarkers() {
  state.districts.forEach((district) => syncDistrictMapMarker(district.id));
}

function tooltipContent(district) {
  const wrapper = document.createElement("span");
  wrapper.className = "district-map-tooltip-content";
  const name = document.createElement("b");
  name.textContent = district.name;
  const rating = document.createElement("small");
  rating.textContent = `${district.quality} · نمره ${fa.format(district.score)} از ۸`;
  wrapper.append(name, rating);
  return wrapper;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function rectanglesOverlap(first, second, gap = 4) {
  return !(
    first.right + gap <= second.left ||
    first.left >= second.right + gap ||
    first.bottom + gap <= second.top ||
    first.top >= second.bottom + gap
  );
}

function labelPlacement(point, width, height, placed, mapSize) {
  const options = [
    { left: point.x - width / 2, top: point.y - height - 10, anchor: [width / 2, height + 10] },
    { left: point.x - width / 2, top: point.y + 10, anchor: [width / 2, -10] },
    { left: point.x + 10, top: point.y - height / 2, anchor: [-10, height / 2] },
    { left: point.x - width - 10, top: point.y - height / 2, anchor: [width + 10, height / 2] },
  ];
  return options.find((option) => {
    const rectangle = {
      left: option.left,
      top: option.top,
      right: option.left + width,
      bottom: option.top + height,
    };
    if (rectangle.left < 4 || rectangle.top < 4 || rectangle.right > mapSize.x - 4 || rectangle.bottom > mapSize.y - 4) {
      return false;
    }
    if (placed.some((current) => rectanglesOverlap(rectangle, current))) return false;
    option.rectangle = rectangle;
    return true;
  });
}

function renderDistrictMapLabels() {
  if (!state.districtMap || !state.districtLabelLayer) return;
  state.districtLabelLayer.clearLayers();
  const map = state.districtMap;
  const zoom = map.getZoom();
  const query = normalizeText(document.querySelector("#district-search").value);
  const bounds = map.getBounds().pad(0.04);
  const limit = zoom <= 10 ? 16 : zoom === 11 ? 30 : zoom === 12 ? 58 : zoom === 13 ? 100 : zoom === 14 ? 180 : 453;
  const candidates = state.districts
    .filter((district) => bounds.contains([district.latitude, district.longitude]))
    .map((district) => ({
      district,
      selected: state.selectedDistricts.has(Number(district.id)),
      matched: Boolean(query) && normalizeText(district.name).includes(query),
      exact: Boolean(query) && normalizeText(district.name) === query,
    }))
    .sort((first, second) =>
      Number(second.selected) - Number(first.selected) ||
      Number(second.exact) - Number(first.exact) ||
      Number(second.matched) - Number(first.matched) ||
      Number(second.district.score) - Number(first.district.score) ||
      first.district.name.localeCompare(second.district.name, "fa")
    );
  const placed = [];
  const mapSize = map.getSize();
  let rendered = 0;
  for (const candidate of candidates) {
    if (rendered >= limit && !candidate.selected && !candidate.matched) continue;
    const district = candidate.district;
    const width = Math.min(146, Math.max(58, 24 + [...district.name].length * 6));
    const height = 22;
    const point = map.latLngToContainerPoint([district.latitude, district.longitude]);
    const placement = labelPlacement(point, width, height, placed, mapSize);
    if (!placement) continue;
    placed.push(placement.rectangle);
    const classes = ["district-map-label"];
    if (candidate.selected) classes.push("selected");
    if (candidate.matched) classes.push("matched");
    const icon = L.divIcon({
      className: classes.join(" "),
      html: `<span>${escapeHtml(district.name)}</span>`,
      iconSize: [width, height],
      iconAnchor: placement.anchor,
    });
    const labelMarker = L.marker([district.latitude, district.longitude], {
      icon,
      interactive: true,
      keyboard: true,
      title: `انتخاب ${district.name}`,
      pane: "districtLabels",
    }).addTo(state.districtLabelLayer);
    labelMarker.on("click", () => {
      const id = Number(district.id);
      setDistrictSelected(id, !state.selectedDistricts.has(id));
    });
    const labelElement = labelMarker.getElement();
    if (labelElement) {
      labelElement.setAttribute("role", "button");
      labelElement.setAttribute("aria-label", `انتخاب محله ${district.name}`);
    }
    rendered += 1;
  }
}

function initializeDistrictMap() {
  if (state.districtMap || !state.districts.length) return;
  const status = document.querySelector("#district-map-status");
  if (!window.L) {
    status.textContent = "نقشه بارگذاری نشد؛ انتخاب محله از فهرست همچنان در دسترس است.";
    status.hidden = false;
    return;
  }
  const map = L.map("district-map", {
    minZoom: 9,
    maxZoom: 18,
    zoomControl: true,
    attributionControl: true,
    maxBounds: [[35.25, 50.95], [36.0, 52.0]],
    maxBoundsViscosity: 0.75,
  });
  map.createPane("districtLabels");
  map.getPane("districtLabels").style.zIndex = "460";
  const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    minZoom: 9,
    maxZoom: 18,
    detectRetina: true,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
  }).addTo(map);
  let tileErrors = 0;
  tiles.on("tileerror", () => {
    tileErrors += 1;
    if (tileErrors >= 3) {
      status.textContent = "نقشه پایه در دسترس نیست؛ نقاط محله‌ها همچنان قابل انتخاب‌اند.";
      status.hidden = false;
    }
  });
  tiles.on("load", () => {
    tileErrors = 0;
    status.hidden = true;
  });
  state.districtMap = map;
  state.districtLabelLayer = L.layerGroup().addTo(map);
  state.districtBounds = L.latLngBounds();
  state.districts.forEach((district) => {
    const location = [Number(district.latitude), Number(district.longitude)];
    state.districtBounds.extend(location);
    const marker = L.circleMarker(location, districtMarkerStyle(district)).addTo(map);
    marker.bindTooltip(tooltipContent(district), {
      direction: "top",
      offset: [0, -7],
      opacity: 1,
      className: "district-map-tooltip",
    });
    marker.on("click", () => {
      const id = Number(district.id);
      setDistrictSelected(id, !state.selectedDistricts.has(id));
    });
    state.districtMarkers.set(Number(district.id), marker);
  });
  map.fitBounds(state.districtBounds, { padding: [24, 24], maxZoom: 11 });
  map.on("zoomend", () => {
    syncDistrictMapMarkers();
    renderDistrictMapLabels();
  });
  map.on("moveend", renderDistrictMapLabels);
  map.whenReady(renderDistrictMapLabels);
}

function resetDistrictMap() {
  if (!state.districtMap || !state.districtBounds) return;
  document.querySelector("#district-search").value = "";
  renderDistricts();
  syncDistrictMapMarkers();
  state.districtMap.fitBounds(state.districtBounds, { padding: [24, 24], maxZoom: 11 });
}

function focusDistrictSearch() {
  if (!state.districtMap) return;
  const query = normalizeText(document.querySelector("#district-search").value);
  syncDistrictMapMarkers();
  renderDistrictMapLabels();
  if (query.length < 2) return;
  const matches = state.districts.filter((district) => normalizeText(district.name).includes(query));
  if (matches.length === 1) {
    state.districtMap.flyTo([matches[0].latitude, matches[0].longitude], 15, { duration: 0.45 });
  } else if (matches.length > 1 && matches.length <= 25) {
    const bounds = L.latLngBounds(matches.map((district) => [district.latitude, district.longitude]));
    state.districtMap.fitBounds(bounds, { padding: [45, 45], maxZoom: 14 });
  }
}

function scheduleDistrictMapSearch() {
  window.clearTimeout(state.mapSearchTimer);
  state.mapSearchTimer = window.setTimeout(focusDistrictSearch, 220);
}

function setDistrictView(view) {
  state.districtView = view === "map" ? "map" : "list";
  document.querySelectorAll("[data-district-view]").forEach((button) => {
    const active = button.dataset.districtView === state.districtView;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  document.querySelector("#district-list-panel").hidden = state.districtView !== "list";
  document.querySelector("#district-map-panel").hidden = state.districtView !== "map";
  if (state.districtView === "map") {
    initializeDistrictMap();
    requestAnimationFrame(() => {
      state.districtMap?.invalidateSize();
      focusDistrictSearch();
    });
  }
}

function toggleExceptional() {
  const enabled = document.querySelector("#exceptional-budget").checked;
  const fields = document.querySelector("#stretch-fields");
  fields.hidden = !enabled;
  fields.querySelectorAll("input").forEach((input) => { input.required = enabled; });
  if (enabled) {
    if (!document.querySelector("#stretch-deposit").value) {
      setField("stretch-deposit", document.querySelector("#hard-deposit").value);
    }
    if (!document.querySelector("#stretch-rent").value) {
      setField("stretch-rent", document.querySelector("#hard-rent").value);
    }
  }
}

function numberValue(id, optional = false) {
  const raw = document.querySelector(`#${id}`).value;
  if (raw === "" && optional) return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function criteriaError() {
  const invalid = [...document.querySelectorAll('[data-step="1"] input, [data-step="1"] select')]
    .find((field) => !field.checkValidity());
  if (invalid) {
    invalid.focus();
    return "همه موارد ستاره‌دار را کامل کن";
  }
  const idealDeposit = numberValue("ideal-deposit");
  const hardDeposit = numberValue("hard-deposit");
  const idealRent = numberValue("ideal-rent");
  const hardRent = numberValue("hard-rent");
  const minSize = numberValue("min-size");
  const idealSize = numberValue("ideal-size");
  const maxSize = numberValue("max-size", true);
  const minRooms = numberValue("min-rooms");
  const maxRooms = numberValue("max-rooms", true);
  const idealAge = numberValue("ideal-age", true);
  const hardAge = numberValue("hard-age", true);
  const minFloor = numberValue("min-floor", true);
  const maxFloor = numberValue("max-floor", true);
  if (idealDeposit > hardDeposit) return "ودیعه ایده‌آل نباید از سقف قطعی بیشتر باشد";
  if (idealRent > hardRent) return "اجاره ایده‌آل نباید از سقف قطعی بیشتر باشد";
  if (idealSize < minSize) return "متراژ ایده‌آل باید حداقل برابر حداقل متراژ باشد";
  if (maxSize !== null && maxSize < idealSize) return "حداکثر متراژ نباید از متراژ ایده‌آل کمتر باشد";
  if (maxRooms !== null && maxRooms < minRooms) return "حداکثر خواب نباید از حداقل خواب کمتر باشد";
  if (idealAge !== null && hardAge !== null && idealAge > hardAge) return "سن بنای ایده‌آل نباید از حداکثر قابل قبول بیشتر باشد";
  if (minFloor !== null && maxFloor !== null && minFloor > maxFloor) return "حداقل طبقه نباید از حداکثر طبقه بیشتر باشد";
  if (document.querySelector("#exceptional-budget").checked) {
    const stretchDeposit = numberValue("stretch-deposit");
    const stretchRent = numberValue("stretch-rent");
    if (stretchDeposit === null || stretchRent === null) return "هر دو سقف بودجه استثنایی را وارد کن";
    if (stretchDeposit < hardDeposit || stretchRent < hardRent) return "سقف استثنایی نباید از سقف قطعی کمتر باشد";
  }
  if (selectedValue("district-scope") === "selected" && !state.selectedDistricts.size) {
    return "حداقل یک محله را از فهرست انتخاب کن";
  }
  return "";
}

function buildCriteria() {
  const exceptional = document.querySelector("#exceptional-budget").checked;
  const hardDeposit = numberValue("hard-deposit") * tomanScale;
  const hardRent = numberValue("hard-rent") * tomanScale;
  return {
    ideal_deposit: numberValue("ideal-deposit") * tomanScale,
    hard_max_deposit: hardDeposit,
    stretch_max_deposit: exceptional ? numberValue("stretch-deposit") * tomanScale : hardDeposit,
    ideal_rent: numberValue("ideal-rent") * tomanScale,
    hard_max_rent: hardRent,
    stretch_max_rent: exceptional ? numberValue("stretch-rent") * tomanScale : hardRent,
    min_size: numberValue("min-size"),
    ideal_size: numberValue("ideal-size"),
    max_size: numberValue("max-size", true),
    min_rooms: numberValue("min-rooms"),
    max_rooms: numberValue("max-rooms", true),
    ideal_max_age: numberValue("ideal-age", true),
    hard_max_age: numberValue("hard-age", true),
    min_floor: numberValue("min-floor", true),
    max_floor: numberValue("max-floor", true),
    max_building_floors: numberValue("max-building-floors", true),
    max_units_per_floor: numberValue("max-units-per-floor", true),
    parking: document.querySelector("#parking").value,
    elevator: document.querySelector("#elevator").value,
    storage: document.querySelector("#storage").value,
    balcony: document.querySelector("#balcony").value,
    district_scope: selectedValue("district-scope"),
    district_ids: [...state.selectedDistricts],
    exceptional_budget_allowed: exceptional,
  };
}

function rangeText(minimum, maximum, unit) {
  if (minimum !== null && maximum !== null) {
    if (minimum === maximum) return `${fa.format(minimum)}${unit ? ` ${unit}` : ""}`;
    return `${fa.format(minimum)} تا ${fa.format(maximum)}${unit ? ` ${unit}` : ""}`;
  }
  if (minimum !== null) return `از ${fa.format(minimum)}${unit ? ` ${unit}` : ""}`;
  if (maximum !== null) return `تا ${fa.format(maximum)}${unit ? ` ${unit}` : ""}`;
  return "مهم نیست";
}

function addReview(label, value) {
  const row = document.createElement("div");
  row.className = "review-row";
  const term = document.createElement("dt");
  term.textContent = label;
  const detail = document.createElement("dd");
  detail.textContent = value;
  row.append(term, detail);
  document.querySelector("#review-list").append(row);
}

function renderReview() {
  const criteria = buildCriteria();
  document.querySelector("#review-list").replaceChildren();
  addReview("دامنه جست‌وجو", "همه اجاره‌آپارتمان‌های تهران");
  addReview("بودجه ایده‌آل", `${fa.format(criteria.ideal_deposit / tomanScale)} میلیون ودیعه · ${fa.format(criteria.ideal_rent / tomanScale)} میلیون اجاره`);
  addReview("سقف قطعی", `${fa.format(criteria.hard_max_deposit / tomanScale)} میلیون ودیعه · ${fa.format(criteria.hard_max_rent / tomanScale)} میلیون اجاره`);
  if (criteria.exceptional_budget_allowed) {
    addReview("سقف استثنایی", `${fa.format(criteria.stretch_max_deposit / tomanScale)} میلیون ودیعه · ${fa.format(criteria.stretch_max_rent / tomanScale)} میلیون اجاره`);
  }
  addReview("متراژ", `حداقل ${fa.format(criteria.min_size)} · ایده‌آل ${fa.format(criteria.ideal_size)}${criteria.max_size === null ? "" : ` · حداکثر ${fa.format(criteria.max_size)}`} متر`);
  addReview("تعداد خواب", rangeText(criteria.min_rooms, criteria.max_rooms, "خواب"));
  addReview("سن بنا", criteria.hard_max_age === null ? "مهم نیست" : `ایده‌آل تا ${fa.format(criteria.ideal_max_age ?? criteria.hard_max_age)} سال · حداکثر ${fa.format(criteria.hard_max_age)} سال`);
  addReview("طبقه واحد", rangeText(criteria.min_floor, criteria.max_floor, ""));
  if (criteria.max_building_floors !== null || criteria.max_units_per_floor !== null) {
    addReview("تراکم ساختمان", `${criteria.max_building_floors === null ? "تعداد طبقات مهم نیست" : `حداکثر ${fa.format(criteria.max_building_floors)} طبقه`} · ${criteria.max_units_per_floor === null ? "تعداد واحد مهم نیست" : `حداکثر ${fa.format(criteria.max_units_per_floor)} واحد در طبقه`}`);
  }
  addReview("امکانات", `پارکینگ: ${requirementLabels[criteria.parking]} · آسانسور: ${requirementLabels[criteria.elevator]} · انباری: ${requirementLabels[criteria.storage]} · بالکن: ${requirementLabels[criteria.balcony]}`);
  addReview("محله‌ها", criteria.district_scope === "all" ? "همه تهران؛ مرتب‌شده با نمره محله" : `${fa.format(criteria.district_ids.length)} محله انتخابی`);
}

function populateProfile(profile) {
  if (!profile?.completed) return;
  const criteria = profile.criteria || {};
  const values = {
    "ideal-deposit": criteria.ideal_deposit / tomanScale,
    "hard-deposit": criteria.hard_max_deposit / tomanScale,
    "stretch-deposit": criteria.stretch_max_deposit / tomanScale,
    "ideal-rent": criteria.ideal_rent / tomanScale,
    "hard-rent": criteria.hard_max_rent / tomanScale,
    "stretch-rent": criteria.stretch_max_rent / tomanScale,
    "min-size": criteria.min_size,
    "ideal-size": criteria.ideal_size,
    "max-size": criteria.max_size,
    "min-rooms": criteria.min_rooms,
    "max-rooms": criteria.max_rooms,
    "ideal-age": criteria.ideal_max_age,
    "hard-age": criteria.hard_max_age,
    "min-floor": criteria.min_floor,
    "max-floor": criteria.max_floor,
    "max-building-floors": criteria.max_building_floors,
    "max-units-per-floor": criteria.max_units_per_floor,
    parking: criteria.parking,
    elevator: criteria.elevator,
    storage: criteria.storage,
    balcony: criteria.balcony,
  };
  Object.entries(values).forEach(([id, value]) => setField(id, value));
  document.querySelector("#exceptional-budget").checked = Boolean(criteria.exceptional_budget_allowed);
  setRadio("district-scope", criteria.district_scope || "all");
  state.selectedDistricts = new Set((criteria.district_ids || []).map(Number));
  toggleExceptional();
  renderDistrictScope();
  renderDistricts();
}

async function saveProfile(event) {
  event.preventDefault();
  const error = criteriaError();
  if (error) {
    setStep(1);
    showMessage("#criteria-message", error);
    return;
  }
  if (state.step !== 2) {
    renderReview();
    setStep(2);
    return;
  }
  const button = document.querySelector("#save-profile");
  button.disabled = true;
  button.textContent = "در حال ثبت...";
  showMessage("#save-message", "");
  try {
    const result = await api("/api/profile", {
      method: "POST",
      body: JSON.stringify({ search_mode: "all_tehran", criteria: buildCriteria() }),
    });
    location.replace(result.redirect || "/");
  } catch (saveError) {
    showMessage("#save-message", saveError.message);
  } finally {
    button.disabled = false;
    button.textContent = "ثبت و دیدن خانه‌ها";
  }
}

async function logout() {
  await api("/api/auth/logout", { method: "POST", body: "{}" });
  location.replace("/login");
}

async function load() {
  state.auth = await api("/api/auth/me");
  if (!state.auth.authenticated) return;
  if (!state.auth.approved) {
    location.replace("/pending");
    return;
  }
  if (state.auth.user.role === "admin") {
    location.replace("/admin");
    return;
  }
  document.querySelector("#setup-user").textContent = `${state.auth.user.display_name} · @${state.auth.user.username}`;
  const payload = await api("/api/profile");
  state.profile = payload.profile;
  state.districts = payload.districts || [];
  state.districtById = new Map(state.districts.map((district) => [Number(district.id), district]));
  renderDistricts();
  populateProfile(state.profile);
  if (state.districtView === "map") setDistrictView("map");
}

document.querySelectorAll('input[name="district-scope"]').forEach((input) => {
  input.addEventListener("change", renderDistrictScope);
});
document.querySelector("#district-search").addEventListener("input", () => {
  renderDistricts();
  scheduleDistrictMapSearch();
});
document.querySelectorAll("[data-district-view]").forEach((button) => {
  button.addEventListener("click", () => setDistrictView(button.dataset.districtView));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const nextView = state.districtView === "list" ? "map" : "list";
    setDistrictView(nextView);
    document.querySelector(`[data-district-view="${nextView}"]`).focus();
  });
});
document.querySelector("#district-clear").addEventListener("click", clearDistrictSelection);
document.querySelector("#district-map-reset").addEventListener("click", resetDistrictMap);
document.querySelector("#exceptional-budget").addEventListener("change", toggleExceptional);
document.querySelector("#criteria-next").addEventListener("click", () => {
  const error = criteriaError();
  showMessage("#criteria-message", error);
  if (error) return;
  renderReview();
  setStep(2);
});
document.querySelectorAll("[data-back]").forEach((button) => {
  button.addEventListener("click", () => setStep(Number(button.dataset.back)));
});
document.querySelector("#setup-form").addEventListener("submit", saveProfile);
document.querySelector("#logout-button").addEventListener("click", logout);

load().catch((error) => showMessage("#criteria-message", error.message));
