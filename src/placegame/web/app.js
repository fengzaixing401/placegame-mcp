(() => {
  const api = "/api/admin/v1";
  const authPanel = document.querySelector("#auth-panel");
  const authForm = document.querySelector("#auth-form");
  const authMode = document.querySelector("#auth-mode");
  const authTitle = document.querySelector("#auth-title");
  const authCopy = document.querySelector("#auth-copy");
  const authSubmit = document.querySelector("#auth-submit");
  const consolePanel = document.querySelector("#console");
  const accounts = document.querySelector("#accounts");
  const notice = document.querySelector("#notice");
  const logout = document.querySelector("#logout");
  const refreshAll = document.querySelector("#refresh-all");

  const messages = {
    unauthorized: "Session expired. Sign in again.",
    account_not_found: "Account not found.",
    game_unavailable: "Game service is temporarily unavailable.",
    game_contract_changed: "Game response changed; refresh and try again.",
  };

  async function request(path, options = {}) {
    const response = await fetch(`${api}${path}`, {
      credentials: "same-origin",
      headers: options.body ? { "Content-Type": "application/json" } : undefined,
      ...options,
    });
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) {
      const error = new Error(data.error || "internal_error");
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function showNotice(message = "") { notice.textContent = message; }

  function setAuthMode(setup) {
    authPanel.classList.remove("hidden");
    authMode.textContent = setup ? "FIRST RUN" : "ACCESS";
    authTitle.textContent = setup ? "Set administrator password" : "Sign in";
    authCopy.textContent = setup ? "Create the password used to access this server." : "Use the administrator password for this server.";
    authSubmit.textContent = setup ? "Create password" : "Sign in";
    authForm.dataset.mode = setup ? "setup" : "login";
    document.querySelector("#password").autocomplete = setup ? "new-password" : "current-password";
  }

  function showConsole() {
    authPanel.classList.add("hidden");
    consolePanel.classList.remove("hidden");
    logout.classList.remove("hidden");
  }

  function showAuth(setup) {
    consolePanel.classList.add("hidden");
    logout.classList.add("hidden");
    setAuthMode(setup);
  }

  function renderAccounts(rows) {
    accounts.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "No game accounts configured.";
      accounts.append(empty);
      return;
    }
    rows.forEach((row) => {
      const card = document.createElement("article");
      card.className = "account";
      const detail = document.createElement("div");
      const title = document.createElement("h3");
      title.textContent = row.label;
      const meta = document.createElement("div");
      meta.className = "account-meta";
      meta.textContent = `${row.auth_state} · ${row.enabled ? "enabled" : "disabled"}`;
      detail.append(title, meta);
      const actions = document.createElement("div");
      actions.className = "account-actions";
      const statusButton = document.createElement("button");
      statusButton.type = "button";
      statusButton.textContent = "Status";
      statusButton.addEventListener("click", () => refreshStatus(row.account_id, card));
      const previewButton = document.createElement("button");
      previewButton.type = "button";
      previewButton.textContent = "Idle preview";
      previewButton.addEventListener("click", () => previewIdle(row.account_id, card));
      actions.append(statusButton, previewButton);
      card.append(detail, actions);
      accounts.append(card);
    });
  }

  async function loadAccounts() {
    accounts.textContent = "Loading...";
    try { renderAccounts(await request("/accounts")); }
    catch (error) { if (error.status === 401) showAuth(false); showNotice(messages[error.message] || "Could not load accounts."); }
  }

  async function refreshStatus(id, card) {
    try {
      const result = await request(`/accounts/${id}/status`);
      card.querySelector(".account-meta").textContent = `${result.idle.accumulated_seconds}s idle · ${result.account.auth_state}`;
      showNotice("Status refreshed.");
    } catch (error) { showNotice(messages[error.message] || "Could not refresh status."); }
  }

  async function previewIdle(id) {
    try {
      const result = await request(`/accounts/${id}/idle-preview`);
      showNotice(result.decision === "collect" ? "Idle collection is ready." : "Idle threshold not reached.");
    } catch (error) { showNotice(messages[error.message] || "Could not preview idle state."); }
  }

  authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showNotice("");
    const password = document.querySelector("#password").value;
    const mode = authForm.dataset.mode;
    try {
      await request(`/auth/${mode}`, { method: "POST", body: JSON.stringify({ password }) });
      document.querySelector("#password").value = "";
      if (mode === "setup") { showAuth(false); showNotice("Password created. Sign in to continue."); }
      else { showConsole(); await loadAccounts(); }
    } catch (error) { showNotice(messages[error.message] || "Authentication failed."); }
  });

  logout.addEventListener("click", async () => {
    try { await request("/auth/logout", { method: "POST", body: "{}" }); } finally { showAuth(false); }
  });
  refreshAll.addEventListener("click", loadAccounts);

  (async () => {
    try {
      const state = await request("/auth/status");
      if (state.authenticated) { showConsole(); await loadAccounts(); }
      else showAuth(state.setupRequired);
    } catch (_) { showNotice("Service unavailable."); showAuth(false); }
  })();
})();
