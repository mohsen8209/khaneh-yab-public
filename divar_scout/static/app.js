const state = {
  status: "",
  query: "",
  district: "",
  sort: localStorage.getItem("khane-yab-sort") || "newest",
  depositRate: 0.03,
  listings: [],
  learning: null,
  feedbackItem: null,
  feedbackChoice: null,
  comparison: null,
  skippedPairs: new Set(),
  auth: null,
};

const fa = new Intl.NumberFormat("fa-IR", { maximumFractionDigits: 1 });
const relativeTime = new Intl.RelativeTimeFormat("fa-IR", { numeric: "auto" });
const money = (value) => {
  if (value === null || value === undefined) return "نامشخص";
  if (value >= 1_000_000_000) return `${fa.format(value / 1_000_000_000)} میلیارد`;
  return `${fa.format(value / 1_000_000)} میلیون`;
};

const statusLabels = {
  alert: "کیس برتر",
  review: "نیازمند بررسی",
  candidate: "نامزد",
  rejected: "رد شده",
  new: "در انتظار جزئیات",
};

const choiceLabels = {
  favorite: "پسندیدم",
  visit: "برای بازدید مناسبه",
  rejected: "ردش می‌کنم",
};

async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (method !== "GET" && state.auth?.csrf_token) {
    headers["X-CSRF-Token"] = state.auth.csrf_token;
  }
  const response = await fetch(path, {
    ...options,
    method,
    headers,
    cache: "no-store",
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error("پاسخ سرور کامل نبود؛ چند ثانیه دیگر دوباره تلاش کن");
  }
  if (response.status === 401) {
    location.replace("/login");
    throw new Error("نشست ورود پایان یافته است");
  }
  if (response.status === 403 && ["pending", "rejected", "suspended"].includes(data.code)) {
    location.replace("/pending");
    throw new Error(data.error);
  }
  if (response.status === 409 && ["profile_required", "calibration_required"].includes(data.code)) {
    location.replace(data.redirect || (data.code === "calibration_required" ? "/calibration" : "/setup"));
    throw new Error(data.error);
  }
  if (!response.ok) throw new Error(data.error || "خطای نامشخص");
  return data;
}

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function load() {
  const auth = await api("/api/auth/me");
  if (!auth.authenticated) {
    location.replace("/login");
    return;
  }
  if (!auth.approved) {
    location.replace("/pending");
    return;
  }
  state.auth = auth;
  renderAccount(auth.user);
  const [stats, config, listings, learning] = await Promise.all([
    api("/api/stats"),
    api("/api/config"),
    api("/api/listings"),
    api("/api/learning"),
  ]);
  state.listings = listings;
  state.learning = learning;
  renderStats(stats);
  renderConfig(config);
  renderLearning(learning);
  populateDistricts(listings);
  renderListings();
}

function renderAccount(user) {
  document.querySelector("#current-user").textContent = user.display_name;
  document.querySelector("#admin-link").hidden = user.role !== "admin";
  document.querySelector("#profile-link").hidden = user.role === "admin";
  document.querySelector("#scan-button").hidden = false;
}

function renderStats(stats) {
  document.querySelector("#stat-total").textContent = fa.format(stats.total || 0);
  document.querySelector("#stat-alerts").textContent = fa.format(stats.alerts || 0);
  document.querySelector("#stat-reviews").textContent = fa.format(stats.reviews || 0);
  document.querySelector("#stat-best").textContent = stats.best_score ? fa.format(stats.best_score) : "۰";
  if (stats.last_scan?.finished_at) {
    const date = new Date(stats.last_scan.finished_at);
    document.querySelector("#last-scan").textContent = `آخرین اسکن ${date.toLocaleString("fa-IR")}`;
  }
}

function renderConfig(config) {
  state.depositRate = Number(config.profile?.deposit_to_monthly_rate) || 0.03;
  const notify = config.notifications;
  document.querySelector("#notification-bar").hidden = !notify.subscription_url;
  document.querySelector("#topic-url").value = notify.subscription_url;
  const status = document.querySelector("#notify-state");
  status.textContent = notify.enabled ? "اعلان گوشی: روشن" : "اعلان گوشی: خاموش";
  status.style.color = notify.enabled ? "var(--green)" : "var(--muted)";
}

function renderLearning(learning) {
  const title = document.querySelector("#learning-title");
  const detail = document.querySelector("#learning-detail");
  const dot = document.querySelector("#learning-dot");
  const progress = document.querySelector("#learning-progress");
  const preferences = document.querySelector("#preference-list");
  const signals = learning?.signals || 0;
  const activation = learning?.activation_signals || 12;

  dot.classList.toggle("active", learning?.stage === "active");
  const readiness = Math.round(learning?.progress || 0);
  progress.style.width = `${readiness}%`;
  if (learning?.stage === "active") {
    title.textContent = "مدل شخصی فعال و در حال یادگیری";
    detail.textContent = `${fa.format(signals)} انتخاب ثبت شده · یادگیری با انتخاب‌های تازه ادامه دارد`;
  } else if (learning?.stage === "calibrating") {
    title.textContent = "مدل شخصی در حال دقیق‌شدن";
    detail.textContent = `${fa.format(signals)} انتخاب ثبت شده · یادگیری ادامه دارد`;
  } else {
    title.textContent = "در حال شناخت سلیقه تو";
    detail.textContent = `${fa.format(signals)} از ${fa.format(activation)} انتخاب لازم برای شروع اثرگذاری`;
  }

  preferences.replaceChildren();
  (learning?.top_preferences || []).slice(0, 3).forEach((preference) => {
    const item = document.createElement("span");
    item.className = "preference-item";
    item.textContent = preference.label;
    preferences.append(item);
  });
  if (!preferences.children.length) {
    const item = document.createElement("span");
    item.className = "preference-item";
    item.textContent = "هنوز ترجیح پایداری ثبت نشده";
    preferences.append(item);
  }
}

function populateDistricts(listings) {
  const select = document.querySelector("#district-filter");
  const selected = select.value;
  const districts = [...new Set(listings.map((item) => item.district).filter(Boolean))].sort();
  select.replaceChildren(new Option("همه محله‌ها", ""));
  districts.forEach((district) => select.add(new Option(district, district)));
  select.value = selected;
}

function renderListings() {
  const grid = document.querySelector("#listing-grid");
  const template = document.querySelector("#listing-template");
  const visible = state.listings.filter((item) => {
    const score = item.score || { status: "new", total: 0 };
    const matchesStatus = !state.status || score.status === state.status;
    const matchesDistrict = !state.district || item.district === state.district;
    const haystack = `${item.title || ""} ${item.district || ""}`.toLowerCase();
    return matchesStatus && matchesDistrict && haystack.includes(state.query.toLowerCase());
  });
  sortListings(visible);
  grid.replaceChildren();
  visible.forEach((item) => grid.append(renderCard(template, item)));
  document.querySelector("#result-count").textContent = `${fa.format(visible.length)} نتیجه`;
  document.querySelector("#empty-state").hidden = visible.length !== 0;
}

const sortModes = {
  newest: [(item) => publishedTimestamp(item), -1],
  oldest: [(item) => publishedTimestamp(item), 1],
  score_desc: [(item) => item.score?.total, -1],
  score_asc: [(item) => item.score?.total, 1],
  location_desc: [(item) => item.score?.location, -1],
  location_asc: [(item) => item.score?.location, 1],
  trust_desc: [(item) => item.score?.trust, -1],
  rent_asc: [(item) => item.rent, 1],
  rent_desc: [(item) => item.rent, -1],
  deposit_asc: [(item) => item.deposit, 1],
  deposit_desc: [(item) => item.deposit, -1],
  effective_asc: [(item) => effectiveMonthlyCost(item), 1],
  effective_desc: [(item) => effectiveMonthlyCost(item), -1],
  age_asc: [(item) => item.age, 1],
  age_desc: [(item) => item.age, -1],
  size_desc: [(item) => item.size, -1],
  size_asc: [(item) => item.size, 1],
};

function finiteNumber(value) {
  const number = Number(value);
  return value === null || value === undefined || !Number.isFinite(number) ? null : number;
}

function publishedTimestamp(item) {
  const timestamp = Date.parse(item.published_at || item.first_seen_at || "");
  return Number.isFinite(timestamp) ? timestamp : null;
}

function effectiveMonthlyCost(item) {
  const rent = finiteNumber(item.rent);
  const deposit = finiteNumber(item.deposit);
  return rent === null || deposit === null ? null : rent + deposit * state.depositRate;
}

function compareNullable(left, right, direction) {
  const leftValue = finiteNumber(left);
  const rightValue = finiteNumber(right);
  if (leftValue === null && rightValue === null) return 0;
  if (leftValue === null) return 1;
  if (rightValue === null) return -1;
  return (leftValue - rightValue) * direction;
}

function sortListings(items) {
  const [valueOf, direction] = sortModes[state.sort] || sortModes.newest;
  items.sort((left, right) => {
    const selected = compareNullable(valueOf(left), valueOf(right), direction);
    if (selected) return selected;
    const score = compareNullable(left.score?.total, right.score?.total, -1);
    if (score) return score;
    const published = compareNullable(publishedTimestamp(left), publishedTimestamp(right), -1);
    if (published) return published;
    return String(left.token || "").localeCompare(String(right.token || ""));
  });
}

function publishedAge(item) {
  const timestamp = publishedTimestamp(item);
  if (timestamp === null) return "زمان انتشار نامشخص";
  const elapsed = Math.max(0, Date.now() - timestamp);
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes < 1) return "همین حالا";
  if (minutes < 60) return relativeTime.format(-minutes, "minute");
  const hours = Math.floor(elapsed / 3_600_000);
  if (hours < 24) return relativeTime.format(-hours, "hour");
  const days = Math.floor(elapsed / 86_400_000);
  if (days < 7) return relativeTime.format(-days, "day");
  if (days < 30) return relativeTime.format(-Math.floor(days / 7), "week");
  if (days < 365) return relativeTime.format(-Math.floor(days / 30), "month");
  return relativeTime.format(-Math.floor(days / 365), "year");
}

function renderCard(template, item) {
  const node = template.content.firstElementChild.cloneNode(true);
  const score = item.score || {
    total: 0,
    status: "new",
    trust: 0,
    location_confidence: 0,
    location: 0,
    value: 0,
    property: 0,
    trust_points: 0,
    freshness: 0,
    reasons: [],
    risks: [],
    personal_reasons: [],
  };
  node.dataset.token = item.token;
  node.dataset.status = score.status;
  const image = node.querySelector(".listing-image");
  if (item.images?.[0]) {
    image.src = item.images[0];
    image.alt = item.title || "تصویر ملک";
    image.addEventListener("error", () => image.remove());
  } else {
    image.remove();
  }
  node.querySelector(".photo-count").textContent = `${fa.format(item.image_count || 0)} عکس`;
  node.querySelector(".status-label").textContent = score.user_rejected
    ? "ردشده توسط تو"
    : statusLabels[score.status] || score.status;
  node.querySelector(".district").textContent = item.district || "محله نامشخص";
  const publication = node.querySelector(".published-age");
  publication.textContent = publishedAge(item);
  const publicationTimestamp = publishedTimestamp(item);
  if (publicationTimestamp !== null) {
    publication.dateTime = new Date(publicationTimestamp).toISOString();
    publication.title = `انتشار: ${new Date(publicationTimestamp).toLocaleString("fa-IR")}`;
  }
  node.querySelector(".title").textContent = item.title;
  node.querySelector(".score-disc b").textContent = fa.format(score.total || 0);
  node.querySelector(".deposit").textContent = money(item.deposit);
  node.querySelector(".rent").textContent = money(item.rent);
  node.querySelector(".size").textContent = item.size ? `${fa.format(item.size)} متر` : "نامشخص";
  node.querySelector(".age").textContent = item.age === 0 ? "نوساز" : item.age ? `${fa.format(item.age)} سال` : "نامشخص";
  node.querySelector(".trust").textContent = `اعتبار آگهی ${fa.format(score.trust || 0)}٪`;
  node.querySelector(".location-confidence").textContent = `اطمینان مکانی ${fa.format(score.location_confidence || 0)}٪`;
  renderPersonalScore(node, score);
  renderBreakdown(node.querySelector(".breakdown"), score);
  appendSignals(node.querySelector(".reasons"), score.reasons || []);
  appendSignals(node.querySelector(".risks"), score.risks || []);
  appendSignals(node.querySelector(".personal-reasons"), score.personal_reasons || []);

  const safeUrl = item.url?.startsWith("https://divar.ir/") ? item.url : "https://divar.ir";
  node.querySelector(".open-link").href = safeUrl;
  const mapLink = node.querySelector(".map-link");
  if (Number.isFinite(item.latitude) && Number.isFinite(item.longitude)) {
    mapLink.href = `https://www.openstreetmap.org/?mlat=${item.latitude}&mlon=${item.longitude}#map=16/${item.latitude}/${item.longitude}`;
  } else {
    mapLink.hidden = true;
  }

  const buttons = {
    favorite: node.querySelector(".favorite"),
    visit: node.querySelector(".visit"),
    rejected: node.querySelector(".reject"),
  };
  Object.entries(buttons).forEach(([choice, button]) => {
    button.classList.toggle("active", item.feedback === choice);
    button.addEventListener("click", () => openFeedback(item, choice));
  });
  buttons.favorite.textContent = item.feedback === "favorite" ? "★" : "☆";
  return node;
}

function renderPersonalScore(node, score) {
  const element = node.querySelector(".personal-score");
  if (!score.personalized) return;
  const delta = Number(score.personal_adjustment || 0);
  const signed = (value) => `${value > 0 ? "+" : ""}${fa.format(value)}`;
  const parts = [`پایه ${fa.format(score.base_total)}`];
  if (Math.abs(delta) >= 0.05) parts.push(`سلیقه تو ${signed(delta)}`);
  parts.push(`اطمینان ${fa.format(score.personal_confidence || 0)}٪`);
  element.textContent = parts.join(" · ");
  element.hidden = false;
}

function renderBreakdown(container, score) {
  const metrics = [
    ["محله", score.location, 8],
    ["ارزش", score.value, 5],
    ["ملک", score.property, 3],
    ["اعتبار", score.trust_points, 3],
    ["تازگی", score.freshness, 1],
  ];
  metrics.forEach(([label, value, max]) => {
    const metric = document.createElement("div");
    metric.className = "metric";
    const caption = document.createElement("span");
    caption.append(document.createTextNode(label));
    const number = document.createElement("b");
    number.textContent = fa.format(value || 0);
    caption.append(number);
    const bar = document.createElement("i");
    bar.style.setProperty("--width", `${Math.min(100, ((value || 0) / max) * 100)}%`);
    metric.append(caption, bar);
    container.append(metric);
  });
}

function appendSignals(list, items) {
  items.slice(0, 4).forEach((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    list.append(item);
  });
}

function openFeedback(item, choice) {
  state.feedbackItem = item;
  state.feedbackChoice = choice;
  const dialog = document.querySelector("#feedback-dialog");
  document.querySelector("#feedback-choice-label").textContent = choiceLabels[choice];
  document.querySelector("#feedback-listing-title").textContent = item.title;
  const options = document.querySelector("#feedback-reasons");
  const group = choice === "rejected" ? "negative" : "positive";
  const selected = item.feedback === choice ? new Set(item.feedback_reasons || []) : new Set();
  options.replaceChildren();
  (state.learning?.reason_options?.[group] || []).forEach((reason) => {
    const label = document.createElement("label");
    label.className = "reason-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = reason.id;
    input.checked = selected.has(reason.id);
    const text = document.createElement("span");
    text.textContent = reason.label;
    label.append(input, text);
    options.append(label);
  });
  document.querySelector("#remove-feedback").hidden = !item.feedback;
  if (!dialog.open) dialog.showModal();
}

async function saveFeedback(choice) {
  const item = state.feedbackItem;
  if (!item) return;
  const reasons = choice ? [...document.querySelectorAll("#feedback-reasons input:checked")].map((input) => input.value) : [];
  const save = document.querySelector("#save-feedback");
  save.disabled = true;
  try {
    await api("/api/feedback", {
      method: "POST",
      body: JSON.stringify({ token: item.token, choice, reasons }),
    });
    document.querySelector("#feedback-dialog").close();
    document.querySelector("#scan-state").textContent = choice ? "بازخورد ثبت شد" : "بازخورد حذف شد";
    await load();
  } catch (error) {
    document.querySelector("#scan-state").textContent = error.message;
  } finally {
    save.disabled = false;
  }
}

async function openComparison() {
  const dialog = document.querySelector("#comparison-dialog");
  if (!dialog.open) dialog.showModal();
  await loadComparison();
}

async function loadComparison() {
  const grid = document.querySelector("#comparison-grid");
  grid.replaceChildren();
  const loading = document.createElement("span");
  loading.textContent = "در حال انتخاب مقایسه مناسب...";
  grid.append(loading);
  try {
    const exclude = [...state.skippedPairs].join("|");
    const result = await api(`/api/comparison${exclude ? `?exclude=${encodeURIComponent(exclude)}` : ""}`);
    state.comparison = result;
    grid.replaceChildren();
    if (!result.left || !result.right) {
      const empty = document.createElement("span");
      empty.textContent = "مقایسه تازه‌ای باقی نمانده است";
      grid.append(empty);
      return;
    }
    document.querySelector("#comparison-kind").textContent = "مقایسه سلیقه";
    document.querySelector("#comparison-question").textContent = "کدام را برای بازدید انتخاب می‌کنی؟";
    const template = document.querySelector("#comparison-template");
    grid.append(
      renderComparisonOption(template, result.left),
      renderComparisonOption(template, result.right),
    );
  } catch (error) {
    grid.replaceChildren(document.createTextNode(error.message));
  }
}

function renderComparisonOption(template, item) {
  const node = template.content.firstElementChild.cloneNode(true);
  const image = node.querySelector(".comparison-image");
  if (item.images?.[0]) {
    image.src = item.images[0];
    image.alt = item.title;
  } else {
    image.remove();
  }
  node.querySelector(".comparison-district").textContent = item.district || "محله نامشخص";
  node.querySelector(".comparison-title").textContent = item.title;
  const age = item.age === 0 ? "نوساز" : item.age ? `${fa.format(item.age)} ساله` : "سن نامشخص";
  node.querySelector(".comparison-facts").textContent = `${fa.format(item.size || 0)} متر · ${fa.format(item.rooms || 0)} خواب · ${age} · ${money(item.deposit)} ودیعه · ${money(item.rent)} اجاره`;
  node.querySelector(".comparison-score").textContent = `نمره فعلی ${fa.format(item.score?.total || 0)} از ۲۰`;
  const safeUrl = item.url?.startsWith("https://divar.ir/") ? item.url : "https://divar.ir";
  node.querySelector(".comparison-open").href = safeUrl;
  node.querySelector(".comparison-choose").addEventListener("click", () => chooseComparison(item.token));
  return node;
}

async function chooseComparison(winnerToken) {
  const { left, right } = state.comparison || {};
  if (!left || !right) return;
  document.querySelectorAll(".comparison-choose").forEach((button) => { button.disabled = true; });
  try {
    const result = await api("/api/comparison", {
      method: "POST",
      body: JSON.stringify({
        left_token: left.token,
        right_token: right.token,
        winner_token: winnerToken,
        kind: "general",
      }),
    });
    state.learning = result.learning;
    renderLearning(result.learning);
    document.querySelector("#scan-state").textContent = "انتخاب ثبت شد و مدل دوباره آموزش دید";
    await load();
    await loadComparison();
  } catch (error) {
    document.querySelector("#scan-state").textContent = error.message;
    document.querySelectorAll(".comparison-choose").forEach((button) => { button.disabled = false; });
  }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    state.status = tab.dataset.status;
    renderListings();
  });
});

document.querySelector("#search-input").addEventListener("input", (event) => {
  state.query = event.target.value;
  renderListings();
});

document.querySelector("#district-filter").addEventListener("change", (event) => {
  state.district = event.target.value;
  renderListings();
});

const sortSelect = document.querySelector("#sort-select");
if (![...sortSelect.options].some((option) => option.value === state.sort)) {
  state.sort = "newest";
}
sortSelect.value = state.sort;
sortSelect.addEventListener("change", (event) => {
  state.sort = event.target.value;
  localStorage.setItem("khane-yab-sort", state.sort);
  renderListings();
});

document.querySelector("#copy-topic").addEventListener("click", async () => {
  await navigator.clipboard.writeText(document.querySelector("#topic-url").value);
  document.querySelector("#scan-state").textContent = "آدرس کانال کپی شد";
});

document.querySelector("#test-notification").addEventListener("click", async (event) => {
  event.target.disabled = true;
  try {
    await api("/api/notify-test", { method: "POST", body: "{}" });
    document.querySelector("#scan-state").textContent = "اعلان آزمایشی ارسال شد";
  } catch (error) {
    document.querySelector("#scan-state").textContent = error.message;
  } finally {
    event.target.disabled = false;
  }
});

document.querySelector("#scan-button").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  document.querySelector("#scan-state").textContent = "اسکن در حال اجراست";
  try {
    const scan = await api("/api/scan", { method: "POST", body: JSON.stringify({ detail_limit: 20 }) });
    const result = await waitForScan(scan);
    showScanResult(result);
    await load();
  } catch (error) {
    document.querySelector("#scan-state").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

async function waitForScan(scan) {
  if (!scan?.running && !scan?.accepted) return scan;
  for (let attempt = 0; attempt < 90; attempt += 1) {
    const delay = Math.max(3, Number(scan?.poll_after_seconds || 6));
    await wait(delay * 1000);
    const status = await api("/api/scan/status");
    if (status.last_error) throw new Error(status.last_error);
    if (!status.running) return status.last_result || status;
    document.querySelector("#scan-state").textContent = "اسکن هنوز در حال آماده‌سازی نتایج است";
  }
  throw new Error("اسکن طولانی شد؛ کمی بعد دوباره تلاش کن");
}

function showScanResult(result) {
  const detailCount = Number(result?.detail_count || 0);
  const expired = result?.expired_count
    ? ` · ${fa.format(result.expired_count)} آگهی منقضی حذف شد`
    : "";
  const filtered = result?.filtered_count
    ? ` · ${fa.format(result.filtered_count)} اسپم الک شد`
    : "";
  document.querySelector("#scan-state").textContent = `${fa.format(detailCount)} آگهی تازه بررسی شد${filtered}${expired}`;
}

document.querySelector("#save-feedback").addEventListener("click", () => saveFeedback(state.feedbackChoice));
document.querySelector("#remove-feedback").addEventListener("click", () => saveFeedback(null));
document.querySelector("#compare-button").addEventListener("click", openComparison);
document.querySelector("#close-comparison").addEventListener("click", () => document.querySelector("#comparison-dialog").close());
document.querySelector("#skip-comparison").addEventListener("click", async () => {
  const { left, right } = state.comparison || {};
  if (!left || !right) return;
  state.skippedPairs.add([left.token, right.token].sort().join(","));
  await loadComparison();
});
document.querySelector("#reset-learning").addEventListener("click", async () => {
  if (!window.confirm("همه پسندها، ردها و مقایسه‌های شخصی پاک شوند؟")) return;
  await api("/api/personalization/reset", { method: "POST", body: "{}" });
  state.skippedPairs.clear();
  document.querySelector("#scan-state").textContent = "یادگیری شخصی بازنشانی شد";
  await load();
});

document.querySelector("#logout-button").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST", body: "{}" });
  location.replace("/login");
});

load().catch((error) => {
  document.querySelector("#scan-state").textContent = error.message;
});
