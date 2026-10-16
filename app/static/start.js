// Invitation verification page.
// The invitation token arrives in the URL fragment (#token=...), which browsers never send
// to the server. We move it to sessionStorage and remove it from the address bar.
(function () {
  "use strict";

  const TOKEN_KEY = "assessflow_invitation_token";
  const form = document.getElementById("verify-form");
  const noToken = document.getElementById("no-token");
  const message = document.getElementById("message");
  const passcodeInput = document.getElementById("passcode");
  const verifyBtn = document.getElementById("verify-btn");
  const resendBtn = document.getElementById("resend-btn");

  function readToken() {
    const params = new URLSearchParams(window.location.hash.slice(1));
    const fromHash = params.get("token");
    if (fromHash) {
      sessionStorage.setItem(TOKEN_KEY, fromHash);
      history.replaceState(null, "", window.location.pathname);
      return fromHash;
    }
    return sessionStorage.getItem(TOKEN_KEY);
  }

  function show(text, kind) {
    message.textContent = text;
    message.className = "alert " + kind;
  }

  async function post(url, body) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let data = {};
    try { data = await response.json(); } catch (e) { /* empty body */ }
    return { ok: response.ok, status: response.status, data: data };
  }

  const token = readToken();
  if (!token) {
    noToken.classList.remove("hidden");
    return;
  }
  form.classList.remove("hidden");

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const code = passcodeInput.value.trim();
    if (!/^\d{4,10}$/.test(code)) {
      show("Enter the numeric code from your email.", "error");
      return;
    }
    verifyBtn.disabled = true;
    try {
      const res = await post("/api/invitations/verify", { token: token, passcode: code });
      if (res.ok) {
        show("Verified. Opening your assessment...", "ok");
        window.location.assign("/assessment");
        return;
      }
      show(res.data.detail || "Verification failed. Please try again.", "error");
    } catch (e) {
      show("Network error. Check your connection and try again.", "error");
    } finally {
      verifyBtn.disabled = false;
      passcodeInput.value = "";
    }
  });

  resendBtn.addEventListener("click", async function () {
    resendBtn.disabled = true;
    try {
      const res = await post("/api/invitations/resend-code", { token: token });
      show(res.data.detail || res.data.message || "Request sent.", res.ok ? "info" : "error");
    } catch (e) {
      show("Network error. Please try again.", "error");
    }
    // Prevent rapid repeated requests from the page (the server also enforces a cooldown).
    setTimeout(function () { resendBtn.disabled = false; }, 30000);
  });
})();
