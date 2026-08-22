let csrfToken = "";

async function authState() {
  const response = await fetch("/api/auth/me", { cache: "no-store" });
  const data = await response.json();
  if (!data.authenticated) {
    location.replace("/login");
    return null;
  }
  csrfToken = data.csrf_token;
  if (data.approved) {
    location.replace(data.user.role === "admin" ? "/admin" : "/");
    return null;
  }
  document.querySelector("#pending-name").textContent = data.user.display_name;
  return data;
}

document.querySelector("#refresh-status").addEventListener("click", async (event) => {
  event.currentTarget.disabled = true;
  document.querySelector("#pending-message").textContent = "در حال بررسی…";
  const state = await authState();
  if (state) document.querySelector("#pending-message").textContent = "هنوز در انتظار تأیید مدیر است.";
  event.currentTarget.disabled = false;
});

document.querySelector("#logout-button").addEventListener("click", async () => {
  await fetch("/api/auth/logout", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    body: "{}",
  });
  location.replace("/login");
});

authState();
