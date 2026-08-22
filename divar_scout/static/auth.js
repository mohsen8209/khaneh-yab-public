const registerMode = location.pathname === "/register";
const form = document.querySelector("#auth-form");
const message = document.querySelector("#form-message");
const submit = document.querySelector("#submit-button");

function configureMode() {
  document.title = registerMode ? "ساخت حساب | خانه‌یاب عمومی" : "ورود | خانه‌یاب عمومی";
  document.querySelector("#auth-eyebrow").textContent = registerMode ? "عضویت کنترل‌شده" : "ورود امن";
  document.querySelector("#auth-title").textContent = registerMode ? "ساخت حساب شخصی" : "ورود به حساب";
  document.querySelector("#auth-subtitle").textContent = registerMode
    ? "بعد از ثبت‌نام، مدیر حساب را بررسی و دسترسی را فعال می‌کند."
    : "نتایج و یادگیری سلیقه فقط در حساب خودت نگه‌داری می‌شود.";
  document.querySelectorAll(".register-only").forEach((element) => { element.hidden = !registerMode; });
  document.querySelector("#display-name").required = registerMode;
  document.querySelector("#password-confirmation").required = registerMode;
  document.querySelector("#password").autocomplete = registerMode ? "new-password" : "current-password";
  submit.textContent = registerMode ? "ثبت درخواست عضویت" : "ورود";
  document.querySelector("#switch-copy").textContent = registerMode ? "قبلاً حساب ساخته‌ای؟" : "حساب نداری؟";
  const link = document.querySelector("#switch-link");
  link.href = registerMode ? "/login" : "/register";
  link.textContent = registerMode ? "ورود" : "ساخت حساب";
}

function showError(text) {
  message.textContent = text;
  message.hidden = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.hidden = true;
  if (!form.reportValidity()) return;
  const password = document.querySelector("#password").value;
  if (registerMode && password !== document.querySelector("#password-confirmation").value) {
    showError("تکرار رمز عبور با رمز اصلی یکسان نیست.");
    return;
  }
  submit.disabled = true;
  submit.textContent = registerMode ? "در حال ثبت درخواست…" : "در حال ورود…";
  try {
    const payload = {
      username: document.querySelector("#username").value.trim(),
      password,
    };
    if (registerMode) payload.display_name = document.querySelector("#display-name").value.trim();
    const response = await fetch(registerMode ? "/api/auth/register" : "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "ورود انجام نشد");
    location.replace(data.redirect || "/");
  } catch (error) {
    showError(error.message);
    submit.disabled = false;
    submit.textContent = registerMode ? "ثبت درخواست عضویت" : "ورود";
  }
});

configureMode();
