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
  const changePassword = document.querySelector("#change-password");
  const passwordDialog = document.querySelector("#password-dialog");
  const passwordForm = document.querySelector("#password-form");
  const currentPassword = document.querySelector("#current-password");
  const newPassword = document.querySelector("#new-password");

  const messages = {
    unauthorized: "会话已过期，请重新登录。",
    account_not_found: "账号不存在。",
    game_unavailable: "游戏服务暂时不可用。",
    game_contract_changed: "游戏接口返回的结构与本服务的预期不符，重试无效，需要先更新接口契约。",
    game_client_version_rejected: "游戏要求更新客户端版本。本服务已记录游戏指定的新版本，请重试一次；若仍失败请升级本服务。",
    account_auth_mode_conflict: "请使用与该账号当前认证方式匹配的编辑入口。",
    account_identity_conflict: "该游戏账号已在此处管理。",
    authentication_required: "游戏凭据被拒绝。",
    invalid_request: "请检查必填项后重试。",
    password_too_short: "密码不能为空。",
    setup_already_complete: "管理员密码已设置过，请直接登录。",
  };

  const authStates = {
    authenticated: "已认证",
    required: "待认证",
    unknown: "状态未知",
  };

  function authStateLabel(state) {
    return authStates[state] || state;
  }

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
    button.textContent = pending ? "处理中…" : button.dataset.previousText;
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
    authMode.textContent = setup ? "首次配置" : "访问";
    authTitle.textContent = setup ? "设置管理员密码" : "登录";
    authCopy.textContent = setup ? "创建用于访问本服务器的密码。" : "请输入本服务器的管理员密码。";
    authSubmit.textContent = setup ? "创建密码" : "登录";
    authForm.dataset.mode = setup ? "setup" : "login";
    document.querySelector("#password").autocomplete = setup ? "new-password" : "current-password";
  }

  function showConsole() {
    authPanel.classList.add("hidden");
    consolePanel.classList.remove("hidden");
    logout.classList.remove("hidden");
    changePassword.classList.remove("hidden");
  }

  function showAuth(setup) {
    consolePanel.classList.add("hidden");
    logout.classList.add("hidden");
    changePassword.classList.add("hidden");
    setAuthMode(setup);
  }

  function renderAccounts(rows) {
    accounts.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "尚未配置任何游戏账号。";
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
      meta.textContent = `${authStateLabel(row.auth_state)} · ${row.enabled ? "已启用" : "已停用"}`;
      detail.append(title, meta);
      const actions = document.createElement("div");
      actions.className = "account-actions";
      const statusButton = document.createElement("button");
      statusButton.type = "button";
      statusButton.textContent = "状态";
      statusButton.addEventListener("click", () => refreshStatus(row.account_id, card));
      const previewButton = document.createElement("button");
      previewButton.type = "button";
      previewButton.textContent = "挂机预览";
      previewButton.addEventListener("click", () => previewIdle(row.account_id, card));
      const editButton = document.createElement("button");
      editButton.type = "button";
      editButton.textContent = "编辑";
      editButton.addEventListener("click", () => editAccount(row));
      const lifecycleButton = document.createElement("button");
      lifecycleButton.type = "button";
      lifecycleButton.textContent = row.enabled ? "停用" : "启用";
      lifecycleButton.addEventListener("click", () => mutateAccount(
        row.account_id, row.enabled ? "disable" : "enable", {}, lifecycleButton
      ));
      const pauseButton = document.createElement("button");
      pauseButton.type = "button";
      pauseButton.textContent = row.paused_reason ? "恢复" : "暂停";
      pauseButton.addEventListener("click", () => {
        if (row.paused_reason) return mutateAccount(row.account_id, "resume", {}, pauseButton);
        const reason = window.prompt("暂停原因", "operator");
        if (reason) mutateAccount(row.account_id, "pause", { reason }, pauseButton);
      });
      const removeButton = document.createElement("button");
      removeButton.type = "button";
      removeButton.className = "danger";
      removeButton.textContent = "移除";
      removeButton.addEventListener("click", () => {
        if (window.confirm("确定移除该账号？此操作不可撤销。")) {
          mutateAccount(row.account_id, "remove", {}, removeButton);
        }
      });
      actions.append(statusButton, previewButton, editButton, lifecycleButton, pauseButton, removeButton);
      card.append(detail, actions);
      accounts.append(card);
    });
  }

  async function loadAccounts() {
    accounts.textContent = "加载中…";
    try { renderAccounts(await request("/accounts")); }
    catch (error) { if (error.status === 401) showAuth(false); showNotice(messages[error.message] || "无法加载账号列表。"); }
  }

  async function refreshStatus(id, card) {
    try {
      const result = await request(`/accounts/${id}/status`);
      card.querySelector(".account-meta").textContent = `已挂机 ${result.idle.accumulatedSeconds} 秒 · ${authStateLabel(result.account.auth_state)}`;
      showNotice("状态已刷新。");
    } catch (error) { showNotice(messages[error.message] || "无法刷新状态。"); }
  }

  async function previewIdle(id) {
    try {
      const result = await request(`/accounts/${id}/idle-preview`);
      showNotice(result.decision === "collect" ? "挂机收益已可领取。" : "尚未达到挂机领取阈值。");
    } catch (error) { showNotice(messages[error.message] || "无法预览挂机状态。"); }
  }

  async function mutateAccount(id, action, body, button) {
    setPending(button, true);
    try {
      await request(`/accounts/${id}${action === "remove" ? "" : `/${action}`}`, {
        method: action === "remove" ? "DELETE" : "POST",
        body: JSON.stringify(body),
      });
      showNotice("账号已更新。");
    } catch (error) {
      if (error.status === 401) showAuth(false);
      showNotice(messages[error.message] || "无法更新账号。");
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
      editSecretLabel.textContent = credentials ? "新游戏密码（可选）" : "新会话令牌（可选）";
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
      showNotice("账号已更新。");
    } catch (error) { showNotice(messages[error.message] || "无法编辑账号。"); }
    finally {
      clearSecretFields(editForm);
      editUsername.value = "";
      await loadAccounts();
    }
  }

  function collectPasswordChange() {
    return new Promise((resolve) => {
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        clearSecretFields(passwordForm);
        passwordDialog.close();
        resolve(value);
      };
      currentPassword.value = "";
      newPassword.value = "";
      passwordForm.onsubmit = (event) => {
        event.preventDefault();
        if (event.submitter && event.submitter.value === "cancel") return finish(null);
        finish({ current: currentPassword.value, next: newPassword.value });
      };
      passwordDialog.oncancel = (event) => { event.preventDefault(); finish(null); };
      passwordDialog.showModal();
      currentPassword.focus();
    });
  }

  async function editPassword() {
    const values = await collectPasswordChange();
    if (!values) return;
    try {
      await request("/auth/password", {
        method: "PATCH",
        body: JSON.stringify({
          currentPassword: values.current,
          newPassword: values.next,
        }),
      });
      showAuth(false);
      showNotice("密码已修改，所有会话已失效，请用新密码重新登录。");
    } catch (error) {
      showNotice(messages[error.message] || "无法修改密码。");
    } finally {
      clearSecretFields(passwordForm);
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
      if (mode === "setup") { showAuth(false); showNotice("密码已创建，请登录后继续。"); }
      else { showConsole(); await loadAccounts(); }
    } catch (error) { showNotice(messages[error.message] || "认证失败。"); }
  });

  logout.addEventListener("click", async () => {
    try { await request("/auth/logout", { method: "POST", body: "{}" }); } finally { showAuth(false); }
  });
  refreshAll.addEventListener("click", loadAccounts);
  changePassword.addEventListener("click", editPassword);
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
      showNotice("账号已添加。");
    } catch (error) {
      showNotice(messages[error.message] || "无法添加账号。");
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
    } catch (_) { showNotice("服务不可用。"); showAuth(false); }
  })();
})();
