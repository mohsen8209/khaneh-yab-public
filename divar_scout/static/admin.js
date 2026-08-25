const state = { auth: null, users: [], status: "pending", query: "" };
const fa = new Intl.NumberFormat("fa-IR");
const statusLabels = {
  pending: "در انتظار",
  approved: "تأییدشده",
  rejected: "ردشده",
  suspended: "تعلیق‌شده",
};

async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (method !== "GET" && state.auth?.csrf_token) headers["X-CSRF-Token"] = state.auth.csrf_token;
  const response = await fetch(path, { ...options, method, headers, cache: "no-store" });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error("پاسخ سرور کامل نبود؛ چند ثانیه دیگر دوباره تلاش کن");
  }
  if (response.status === 401) location.replace("/login");
  if (!response.ok) throw new Error(data.error || "درخواست انجام نشد");
  return data;
}

function formatDate(value) {
  if (!value) return "ثبت نشده";
  return new Date(value).toLocaleString("fa-IR", { dateStyle: "medium", timeStyle: "short" });
}

function showMessage(text, error = false) {
  const message = document.querySelector("#admin-message");
  message.textContent = text;
  message.style.color = error ? "var(--accent-dark)" : "var(--green)";
  message.style.background = error ? "var(--danger-bg)" : "var(--green-bg)";
  message.style.borderColor = error ? "#e3b6b3" : "#a7c8b4";
  clearTimeout(showMessage.timer);
  showMessage.timer = setTimeout(() => { message.textContent = ""; }, 3500);
}

async function load() {
  state.auth = await api("/api/auth/me");
  if (!state.auth.authenticated) return;
  if (state.auth.user.role !== "admin" || !state.auth.approved) {
    location.replace("/");
    return;
  }
  document.querySelector("#admin-identity").textContent = `${state.auth.user.display_name} · @${state.auth.user.username}`;
  document.querySelector("#password-username").value = state.auth.user.username;
  const data = await api("/api/admin/users");
  state.users = data.users;
  renderStats(data.stats);
  renderUsers();
}

function renderStats(stats) {
  ["pending", "approved", "suspended", "rejected"].forEach((status) => {
    document.querySelector(`#stat-${status}`).textContent = fa.format(stats[status] || 0);
  });
  document.querySelector("#stat-total").textContent = fa.format(
    Object.values(stats).reduce((sum, value) => sum + Number(value || 0), 0),
  );
}

function userActions(user) {
  const actions = document.createElement("div");
  actions.className = "user-actions";
  if (user.role === "admin") return actions;
  const choices = [];
  if (user.status !== "approved") choices.push(["approved", "تأیید", "approve"]);
  if (user.status === "pending") choices.push(["rejected", "رد", "reject"]);
  if (user.status === "approved") choices.push(["suspended", "تعلیق", "suspend"]);
  choices.forEach(([status, label, className]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `user-command ${className}`;
    button.textContent = label;
    button.addEventListener("click", () => updateStatus(user, status, button));
    actions.append(button);
  });
  return actions;
}

function renderUsers() {
  const query = state.query.toLocaleLowerCase("fa");
  const visible = state.users.filter((user) => {
    const statusMatch = !state.status || user.status === state.status;
    const queryMatch = `${user.display_name} ${user.username}`.toLocaleLowerCase("fa").includes(query);
    return statusMatch && queryMatch;
  });
  const body = document.querySelector("#user-rows");
  body.replaceChildren();
  visible.forEach((user) => {
    const row = document.createElement("tr");
    const identity = document.createElement("td");
    identity.className = "user-cell";
    const name = document.createElement("strong");
    name.textContent = user.display_name;
    const username = document.createElement("span");
    username.textContent = `@${user.username}`;
    identity.append(name, username);

    const statusCell = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `status-badge ${user.role === "admin" ? "status-admin" : `status-${user.status}`}`;
    badge.textContent = user.role === "admin" ? "روت‌ادمین" : statusLabels[user.status];
    statusCell.append(badge);

    [
      identity,
      statusCell,
      textCell(formatDate(user.created_at)),
      textCell(formatDate(user.last_login_at)),
      textCell(fa.format(user.active_sessions || 0)),
    ].forEach((cell) => row.append(cell));
    const actionCell = document.createElement("td");
    actionCell.append(userActions(user));
    row.append(actionCell);
    body.append(row);
  });
  document.querySelector("#directory-empty").hidden = visible.length > 0;
  document.querySelector("#directory-count").textContent = `${fa.format(visible.length)} حساب`;
  const title = state.status ? statusLabels[state.status] : "همه حساب‌ها";
  document.querySelector("#directory-title").textContent = title;
  const tableWrap = document.querySelector(".table-wrap");
  requestAnimationFrame(() => { tableWrap.scrollLeft = tableWrap.scrollWidth; });
}

function textCell(text) {
  const cell = document.createElement("td");
  cell.textContent = text;
  return cell;
}

async function updateStatus(user, status, button) {
  const verb = statusLabels[status];
  if (!window.confirm(`وضعیت حساب ${user.display_name} به «${verb}» تغییر کند؟`)) return;
  button.disabled = true;
  try {
    await api("/api/admin/users/status", {
      method: "POST",
      body: JSON.stringify({ user_id: user.id, status }),
    });
    showMessage(`حساب ${user.display_name} ${verb} شد.`);
    await load();
  } catch (error) {
    showMessage(error.message, true);
    button.disabled = false;
  }
}

document.querySelectorAll(".stat-filter").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".stat-filter").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    state.status = button.dataset.status;
    renderUsers();
  });
});

document.querySelector("#user-search").addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  renderUsers();
});
document.querySelector("#refresh-users").addEventListener("click", load);
document.querySelector("#logout-button").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST", body: "{}" });
  location.replace("/login");
});

const passwordDialog = document.querySelector("#password-dialog");
document.querySelector("#password-button").addEventListener("click", () => passwordDialog.showModal());
document.querySelector("#close-password").addEventListener("click", () => passwordDialog.close());
document.querySelector("#password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.querySelector("#password-message");
  message.hidden = true;
  try {
    await api("/api/auth/password", {
      method: "POST",
      body: JSON.stringify({
        current_password: document.querySelector("#current-password").value,
        new_password: document.querySelector("#new-password").value,
      }),
    });
    location.replace("/login");
  } catch (error) {
    message.textContent = error.message;
    message.hidden = false;
  }
});

load().catch((error) => showMessage(error.message, true));
setInterval(() => load().catch(() => {}), 20000);
