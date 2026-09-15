const state = {
  auth: null,
  pair: null,
  scanAttempted: false,
  submitting: false,
  pollingScan: false,
};

const fa = new Intl.NumberFormat("fa-IR", { maximumFractionDigits: 1 });

async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (method !== "GET" && state.auth?.csrf_token) {
    headers["X-CSRF-Token"] = state.auth.csrf_token;
  }
  const response = await fetch(path, { ...options, method, headers, cache: "no-store" });
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
  if (response.status === 409 && data.redirect) {
    location.replace(data.redirect);
    throw new Error(data.error);
  }
  if (!response.ok) throw new Error(data.error || "درخواست انجام نشد");
  return data;
}

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function money(value) {
  if (value === null || value === undefined) return "نامشخص";
  if (value >= 1_000_000_000) return `${fa.format(value / 1_000_000_000)} میلیارد`;
  return `${fa.format(value / 1_000_000)} میلیون`;
}

function safeDivarUrl(value) {
  return typeof value === "string" && value.startsWith("https://divar.ir/")
    ? value
    : "https://divar.ir";
}

function safeImageUrl(value) {
  if (typeof value !== "string") return "";
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.href : "";
  } catch {
    return "";
  }
}

function setVisible(id, visible) {
  document.querySelector(id).hidden = !visible;
}

function showError(message = "") {
  const error = document.querySelector("#calibration-error");
  error.textContent = message;
  error.hidden = !message;
}

function renderProgress(calibration) {
  const target = Number(calibration.target || 12);
  const completed = Math.min(target, Number(calibration.completed || 0));
  const next = Math.min(target, completed + 1);
  document.querySelector("#progress-label").textContent = calibration.complete
    ? "۱۲ انتخاب کامل شد"
    : `انتخاب ${fa.format(next)} از ${fa.format(target)}`;
  document.querySelector("#progress-detail").textContent = calibration.complete
    ? "مدل اولیه سلیقه فعال است"
    : `${fa.format(calibration.remaining || target)} انتخاب تا آماده‌شدن نتیجه اولیه`;
  document.querySelector("#progress-count").textContent = `${fa.format(completed)} از ${fa.format(target)}`;
  const track = document.querySelector(".progress-track");
  track.setAttribute("aria-valuemax", String(target));
  track.setAttribute("aria-valuenow", String(completed));
  document.querySelector("#progress-fill").style.width = `${target ? completed / target * 100 : 100}%`;
  document.querySelector("#comparison-number").textContent = `مقایسه ${fa.format(next)}`;
}

function addPhotos(container, item) {
  const urls = (item.images || []).map(safeImageUrl).filter(Boolean).slice(0, 3);
  if (!urls.length) {
    const placeholder = document.createElement("span");
    placeholder.className = "photo-placeholder";
    placeholder.textContent = "تصویر معتبر ندارد";
    container.append(placeholder);
    return;
  }
  if (urls.length === 1) container.classList.add("single");
  if (urls.length === 2) container.classList.add("two");
  urls.forEach((url, index) => {
    const image = document.createElement("img");
    image.src = url;
    image.alt = index === 0 ? `تصویر آگهی ${item.title || "خانه"}` : "";
    image.loading = index === 0 ? "eager" : "lazy";
    image.referrerPolicy = "no-referrer";
    container.append(image);
  });
}

function amenity(label, value) {
  const chip = document.createElement("span");
  chip.className = `amenity ${value === true ? "yes" : value === false ? "no" : ""}`;
  const mark = value === true ? "✓" : value === false ? "×" : "•";
  chip.textContent = `${mark} ${label}`;
  return chip;
}

function renderOption(item, name) {
  const template = document.querySelector("#home-option-template");
  const node = template.content.firstElementChild.cloneNode(true);
  addPhotos(node.querySelector(".home-photos"), item);
  node.querySelector(".option-name").textContent = name;
  node.querySelector(".home-district").textContent = item.district || "محله نامشخص";
  node.querySelector(".home-title").textContent = item.title || "آگهی بدون عنوان";
  node.querySelector('[data-fact="deposit"]').textContent = money(item.deposit);
  node.querySelector('[data-fact="rent"]').textContent = money(item.rent);
  node.querySelector('[data-fact="size"]').textContent = item.size ? `${fa.format(item.size)} متر` : "نامشخص";
  node.querySelector('[data-fact="rooms"]').textContent = item.rooms === 0 ? "بدون خواب" : item.rooms ? fa.format(item.rooms) : "نامشخص";
  node.querySelector('[data-fact="age"]').textContent = item.age === 0 ? "نوساز" : item.age ? `${fa.format(item.age)} سال` : "نامشخص";
  node.querySelector('[data-fact="floor"]').textContent = item.floor || "نامشخص";
  const amenities = node.querySelector(".home-amenities");
  amenities.append(
    amenity("پارکینگ", item.has_parking),
    amenity("آسانسور", item.has_elevator),
    amenity("انباری", item.has_storage),
    amenity("بالکن", item.has_balcony),
  );
  node.querySelector(".open-divar").href = safeDivarUrl(item.url);
  node.querySelector(".choose-home").addEventListener("click", () => choose(item.token));
  return node;
}

function renderPair(result) {
  state.pair = result;
  renderProgress(result.calibration);
  const grid = document.querySelector("#calibration-grid");
  grid.replaceChildren(
    renderOption(result.left, "خانه الف"),
    renderOption(result.right, "خانه ب"),
  );
  setVisible("#loading-state", false);
  setVisible("#waiting-state", false);
  setVisible("#complete-state", false);
  setVisible("#comparison-stage", true);
  showError();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function choose(winnerToken) {
  if (state.submitting || !state.pair?.left || !state.pair?.right) return;
  state.submitting = true;
  document.querySelectorAll(".home-option").forEach((option) => option.classList.add("busy"));
  showError();
  try {
    const result = await api("/api/calibration", {
      method: "POST",
      body: JSON.stringify({
        left_token: state.pair.left.token,
        right_token: state.pair.right.token,
        winner_token: winnerToken,
      }),
    });
    renderProgress(result.calibration);
    if (result.calibration.complete) {
      state.pair = null;
      setVisible("#comparison-stage", false);
      setVisible("#complete-state", true);
      setTimeout(() => location.replace(result.redirect || "/"), 850);
      return;
    }
    await loadComparison();
  } catch (error) {
    if (["فعال نیست", "قبلاً مقایسه", "کامل شده"].some((part) => error.message.includes(part))) {
      await loadComparison();
      return;
    }
    showError(error.message);
    document.querySelectorAll(".home-option").forEach((option) => option.classList.remove("busy"));
  } finally {
    state.submitting = false;
  }
}

async function prepareInventory(result) {
  renderProgress(result.calibration);
  setVisible("#loading-state", false);
  setVisible("#comparison-stage", false);
  setVisible("#waiting-state", true);
  const detail = document.querySelector("#waiting-detail");
  const retry = document.querySelector("#retry-button");
  retry.hidden = true;
  if (state.scanAttempted) {
    detail.textContent = "هنوز آگهی کامل کافی برای ۱۲ مقایسه پیدا نشده است. کمی بعد دوباره امتحان کن.";
    retry.hidden = false;
    return;
  }
  state.scanAttempted = true;
  detail.textContent = "در حال اسکن آگهی‌های تهران و آماده‌کردن گزینه‌های منطبق با معیارهای تو هستیم...";
  try {
    const scan = await api("/api/scan", { method: "POST", body: "{}" });
    const completed = await waitForScan(scan);
    if (!completed?.inventoryReady) await loadComparison();
  } catch (error) {
    detail.textContent = error.message;
    retry.hidden = false;
  }
}

async function waitForScan(scan) {
  if (!scan?.running && !scan?.accepted) return scan;
  if (state.pollingScan) return null;
  state.pollingScan = true;
  const detail = document.querySelector("#waiting-detail");
  try {
    for (let attempt = 0; attempt < 90; attempt += 1) {
      const delay = Math.max(3, Number(scan?.poll_after_seconds || 6));
      await wait(delay * 1000);
      const status = await api("/api/scan/status");
      if (status.last_error) throw new Error(status.last_error);
      if (!status.running) return status.last_result || status;
      const calibration = await api("/api/calibration");
      if (calibration.calibration?.complete) {
        location.replace(calibration.redirect || "/");
        return { inventoryReady: true };
      }
      if (calibration.ready && calibration.left && calibration.right) {
        renderPair(calibration);
        return { inventoryReady: true };
      }
      const summaries = Number(status.last_scan?.summary_count || 0).toLocaleString("fa-IR");
      const details = Number(status.last_scan?.detail_count || 0).toLocaleString("fa-IR");
      detail.textContent = `در حال بررسی آگهی‌های منطبق با انتخاب تو هستیم؛ تا این لحظه ${summaries} آگهی دیده و ${details} آگهی کامل بررسی شده است.`;
    }
    throw new Error("اسکن طولانی شد؛ کمی بعد دوباره تلاش کن");
  } finally {
    state.pollingScan = false;
  }
}

async function loadComparison() {
  showError();
  try {
    const result = await api("/api/calibration");
    if (result.calibration?.complete) {
      location.replace(result.redirect || "/");
      return;
    }
    if (!result.ready || !result.left || !result.right) {
      await prepareInventory(result);
      return;
    }
    renderPair(result);
  } catch (error) {
    setVisible("#loading-state", false);
    showError(error.message);
  }
}

async function logout() {
  const impersonating = Boolean(state.auth?.impersonation?.active);
  const result = await api(
    impersonating ? "/api/auth/impersonation/stop" : "/api/auth/logout",
    { method: "POST", body: "{}" },
  );
  location.replace(result.redirect || "/login");
}

async function start() {
  try {
    state.auth = await api("/api/auth/me");
    if (!state.auth.authenticated) {
      location.replace("/login");
      return;
    }
    if (!state.auth.approved) {
      location.replace("/pending");
      return;
    }
    if (!state.auth.profile_completed) {
      location.replace("/setup");
      return;
    }
    if (!state.auth.calibration_required) {
      location.replace("/");
      return;
    }
    document.querySelector("#calibration-user").textContent = `${state.auth.user.display_name} · @${state.auth.user.username}`;
    if (state.auth.impersonation?.active) {
      document.querySelector("#calibration-user").textContent += " · مشاهده توسط مدیر";
      document.querySelector("#logout-button").textContent = "بازگشت به پنل مدیر";
    }
    await loadComparison();
  } catch (error) {
    setVisible("#loading-state", false);
    showError(error.message);
  }
}

document.querySelector("#logout-button").addEventListener("click", logout);
document.querySelector("#retry-button").addEventListener("click", () => {
  state.scanAttempted = false;
  loadComparison();
});

start();
