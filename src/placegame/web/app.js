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
  const createForm = document.querySelector("#account-create-form");
  const authModeSelect = document.querySelector("#account-auth-mode");
  const usernameField = document.querySelector("#account-username-field");
  const passwordField = document.querySelector("#account-password-field");
  const tokenField = document.querySelector("#account-token-field");
  const editDialog = document.querySelector("#edit-dialog");
  const editForm = document.querySelector("#edit-form");
  const editLabel = document.querySelector("#edit-label");
  const editUsernameField = document.querySelector("#edit-username-field");
  const editUsername = document.querySelector("#edit-username");
  const editSecret = document.querySelector("#edit-secret");
  const editSecretLabel = document.querySelector("#edit-secret-label");

  const messages = {
    unauthorized: "Session expired. Sign in again.",
    account_not_found: "Account not found.",
    game_unavailable: "Game service is temporarily unavailable.",
    game_contract_changed: "Game response changed; refresh and try again.",
    account_auth_mode_conflict: "Use the editor for the account's current authentication mode.",
    account_identity_conflict: "That game account is already managed here.",
    authentication_required: "The game credentials were rejected.",
    invalid_request: "Check the required fields and try again.",
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

  function clearSecretFields(root = document) {
    root.querySelectorAll('input[type="password"]').forEach((input) => { input.value = ""; });
  }

  function setPending(button, pending) {
    if (!button) return;
    button.disabled = pending;
    button.dataset.previousText = button.dataset.previousText || button.textContent;
    button.textContent = pending ? "Working..." : button.dataset.previousText;
  }

  function updateCreateMode() {
    const tokenMode = authModeSelect.value === "token_only";
    usernameField.classList.toggle("hidden", tokenMode);
    passwordField.classList.toggle("hidden", tokenMode);
    tokenField.classList.toggle("hidden", !tokenMode);
    document.querySelector("#account-username").required = !tokenMode;
    document.querySelector("#account-password").required = !tokenMode;
    document.querySelector("#account-token").required = tokenMode;
  }

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
      meta.textContent = `${row.auth_state} - ${row.enabled ? "enabled" : "disabled"}`;
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
      const editButton = document.createElement("button");
      editButton.type = "button";
      editButton.textContent = "Edit";
      editButton.addEventListener("click", () => editAccount(row));
      const lifecycleButton = document.createElement("button");
      lifecycleButton.type = "button";
      lifecycleButton.textContent = row.enabled ? "Disable" : "Enable";
      lifecycleButton.addEventListener("click", () => mutateAccount(
        row.account_id, row.enabled ? "disable" : "enable", {}, lifecycleButton
      ));
      const pauseButton = document.createElement("button");
      pauseButton.type = "button";
      pauseButton.textContent = row.paused_reason ? "Resume" : "Pause";
      pauseButton.addEventListener("click", () => {
        if (row.paused_reason) return mutateAccount(row.account_id, "resume", {}, pauseButton);
        const reason = window.prompt("Pause reason", "operator");
        if (reason) mutateAccount(row.account_id, "pause", { reason }, pauseButton);
      });
      const removeButton = document.createElement("button");
      removeButton.type = "button";
      removeButton.className = "danger";
      removeButton.textContent = "Remove";
      removeButton.addEventListener("click", () => {
        if (window.confirm("Remove this account? This cannot be undone.")) {
          mutateAccount(row.account_id, "remove", {}, removeButton);
        }
      });
      actions.append(statusButton, previewButton, editButton, lifecycleButton, pauseButton, removeButton);
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
      card.querySelector(".account-meta").textContent = `${result.idle.accumulatedSeconds}s idle - ${result.account.auth_state}`;
      showNotice("Status refreshed.");
    } catch (error) { showNotice(messages[error.message] || "Could not refresh status."); }
  }

  async function previewIdle(id) {
    try {
      const result = await request(`/accounts/${id}/idle-preview`);
      showNotice(result.decision === "collect" ? "Idle collection is ready." : "Idle threshold not reached.");
    } catch (error) { showNotice(messages[error.message] || "Could not preview idle state."); }
  }

  async function mutateAccount(id, action, body, button) {
    setPending(button, true);
    try {
      await request(`/accounts/${id}${action === "remove" ? "" : `/${action}`}`, {
        method: action === "remove" ? "DELETE" : "POST",
        body: JSON.stringify(body),
      });
      showNotice("Account updated.");
    } catch (error) {
      if (error.status === 401) showAuth(false);
      showNotice(messages[error.message] || "Could not update account.");
    } finally {
      setPending(button, false);
      await loadAccounts();
    }
  }

  function collectEditValues(row) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        clearSecretFields(editForm);
        editDialog.close();
        resolve(value);
      };
      editLabel.value = row.label;
      editUsername.value = "";
      editSecret.value = "";
      const credentials = row.auth_mode === "credentials";
      editUsernameField.classList.toggle("hidden", !credentials);
      editSecretLabel.textContent = credentials ? "New game password (optional)" : "New session token (optional)";
      editSecret.type = "password";
      editForm.onsubmit = (event) => {
        event.preventDefault();
        if (event.submitter && event.submitter.value === "cancel") return finish(null);
        finish({
          label: editLabel.value,
          username: editUsername.value,
          secret: editSecret.value,
        });
      };
      editDialog.oncancel = (event) => { event.preventDefault(); finish(null); };
      editDialog.showModal();
      editLabel.focus();
    });
  }

  async function editAccount(row) {
    const values = await collectEditValues(row);
    if (!values) return;
    try {
      await request(`/accounts/${row.account_id}/label`, {
        method: "PATCH", body: JSON.stringify({ label: values.label }),
      });
      if (row.auth_mode === "credentials") {
        if (values.secret) {
          await request(`/accounts/${row.account_id}/credentials`, {
            method: "PATCH", body: JSON.stringify({
              username: values.username || null,
              password: values.secret,
            }),
          });
        }
      } else {
        if (values.secret) {
          await request(`/accounts/${row.account_id}/token-only`, {
            method: "PATCH", body: JSON.stringify({ sessionToken: values.secret }),
          });
        }
      }
      showNotice("Account updated.");
    } catch (error) { showNotice(messages[error.message] || "Could not edit account."); }
    finally {
      clearSecretFields(editForm);
      editUsername.value = "";
      await loadAccounts();
    }
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
  authModeSelect.addEventListener("change", updateCreateMode);
  createForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = document.querySelector("#account-create-submit");
    const data = new FormData(createForm);
    const tokenMode = data.get("authMode") === "token_only";
    const body = tokenMode
      ? { label: data.get("label"), sessionToken: data.get("sessionToken") }
      : { label: data.get("label"), username: data.get("username"), password: data.get("password") };
    setPending(button, true);
    try {
      await request(tokenMode ? "/accounts/token-only" : "/accounts/credentials", {
        method: "POST", body: JSON.stringify(body),
      });
      createForm.reset();
      updateCreateMode();
      showNotice("Account created.");
    } catch (error) {
      showNotice(messages[error.message] || "Could not create account.");
    } finally {
      clearSecretFields(createForm);
      setPending(button, false);
      await loadAccounts();
    }
  });
  updateCreateMode();

  (async () => {
    try {
      const state = await request("/auth/status");
      if (state.authenticated) { showConsole(); await loadAccounts(); }
      else showAuth(state.setupRequired);
    } catch (_) { showNotice("Service unavailable."); showAuth(false); }
  })();
})();
