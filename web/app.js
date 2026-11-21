(() => {
  "use strict";

  const state = {
    documents: [],
    route: "chat",
    asking: false,
    uploading: false,
    activeJob: null,
    review: null,
    cleanDraft: null,
    cleanPage: null,
    reviewTab: "cleaning",
    user: null,
    rolesPayload: null,
    versions: [],
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const chatThread = $("#chat-thread");
  const questionForm = $("#question-form");
  const questionInput = $("#question-input");
  const sendButton = $("#send-button");
  const documentList = $("#document-list");
  const documentTotals = $("#document-totals");
  const documentReadyBadge = $("#document-ready-badge");
  const serviceState = $("#service-state");
  const serviceStateText = $("#service-state-text");
  const fileInput = $("#file-input");
  const uploadDropzone = $("#upload-dropzone");
  const jobPanel = $("#job-panel");
  const jobTitle = $("#job-title");
  const jobMessage = $("#job-message");
  const jobPercent = $("#job-percent");
  const jobProgress = $("#job-progress");
  const progressTrack = $(".progress-track");
  const pipelineItems = $$("#pipeline-steps li");
  const indexSummary = $("#index-summary");
  const toast = $("#toast");
  const authGate = $("#auth-gate");
  const appShell = $("#app-shell");
  const authForm = $("#auth-form");
  const authError = $("#auth-error");

  const reviewCard = $("#review-card");
  const reviewDocumentName = $("#review-document-name");
  const reviewStatusPill = $("#review-status-pill");
  const cleanCountBadge = $("#clean-count-badge");
  const chunkCountBadge = $("#chunk-count-badge");
  const cleanPreset = $("#clean-preset");
  const cleanPageSelect = $("#clean-page-select");
  const excludePageCheckbox = $("#exclude-page-checkbox");
  const cleanLines = $("#clean-lines");
  const cleanSummary = $("#clean-summary");
  const chunkPreset = $("#chunk-preset");
  const splitByPageCheckbox = $("#split-by-page-checkbox");
  const chunkSummary = $("#chunk-summary");
  const chunkList = $("#chunk-list");

  const stageOrder = ["queued", "parsing", "cleaning", "chunking", "review", "publishing", "published"];
  const cleanReasonLabels = {
    header: "重复页眉",
    page_number: "页码",
    toc_page: "目录页",
    excluded_page: "整页排除",
    manual: "人工排除",
  };
  const chunkPresets = {
    short: { max_chars: 300, min_chars: 20, table_max_chars: 600 },
    standard: { max_chars: 400, min_chars: 40, table_max_chars: 800 },
    long: { max_chars: 600, min_chars: 20, table_max_chars: 1200 },
  };
  const routeLabels = {
    chat: "问答工作台",
    ops: "生产运营沙盘",
    documents: "文档入库",
    versions: "版本管理",
    users: "用户与角色",
    permissions: "权限配置",
    audit: "操作日志",
  };
  const routePermissions = {
    chat: "chat.use",
    ops: "ops.view",
    documents: "documents.manage",
    versions: "versions.manage",
    users: "users.manage",
    permissions: "users.manage",
    audit: "audit.view",
  };

  function makeElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function formatDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "时间未知";
    return new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function pageLabel(source) {
    if (source.page_start === source.page_end) return `第 ${source.page_start} 页`;
    return `第 ${source.page_start}–${source.page_end} 页`;
  }

  function displayDocumentName(name) {
    return name.includes("_") ? name.split("_").pop() : name;
  }

  function showToast(message, type = "success") {
    window.clearTimeout(showToast.timer);
    toast.textContent = message;
    toast.classList.toggle("is-error", type === "error");
    toast.hidden = false;
    showToast.timer = window.setTimeout(() => {
      toast.hidden = true;
    }, 4200);
  }

  async function errorText(response) {
    let payload;
    try {
      payload = await response.json();
    } catch {
      return `请求失败（${response.status}）`;
    }
    if (typeof payload.detail === "string") return payload.detail;
    if (payload.detail?.message) {
      const missing = payload.detail.missing?.length
        ? `：${payload.detail.missing.join("、")}`
        : "";
      return `${payload.detail.message}${missing}`;
    }
    return `请求失败（${response.status}）`;
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) {
      const message = await errorText(response);
      if (response.status === 401 && !url.startsWith("/auth/")) {
        showAuth("login");
      }
      throw new Error(message);
    }
    return response.json();
  }

  function hasPermission(permission) {
    return Boolean(state.user?.permissions?.includes(permission));
  }

  function showAuth(mode = "login") {
    state.user = null;
    appShell.hidden = true;
    authGate.hidden = false;
    const setup = mode === "setup";
    $("#auth-kicker").textContent = setup ? "首次启动" : "本地安全入口";
    $("#auth-title").textContent = setup ? "创建系统管理员" : "登录工作台";
    $("#auth-description").textContent = setup
      ? "当前没有本地账号。请创建第一个系统管理员，完成后即可进入工作台。"
      : "使用本地账号进入设备文档问答与知识库管理。";
    $("#auth-confirm-field").hidden = !setup;
    $("#auth-confirm-password").required = setup;
    $("#auth-submit").textContent = setup ? "创建管理员并进入" : "登录";
    $("#auth-note").textContent = setup
      ? "账号保存在项目内的 output/app.db，不会上传到外部服务。"
      : "首次启动时会引导创建第一个系统管理员。";
    authError.hidden = true;
    authForm.dataset.mode = mode;
    $("#auth-username").focus();
  }

  async function bootstrapAuth() {
    try {
      const status = await requestJson("/auth/status", { cache: "no-store" });
      if (!status.initialized) {
        showAuth("setup");
        return;
      }
      const response = await fetch("/auth/me", { cache: "no-store" });
      if (!response.ok) {
        showAuth("login");
        return;
      }
      const payload = await response.json();
      enterApp(payload.user);
    } catch {
      showAuth("login");
    }
  }

  function enterApp(user) {
    state.user = user;
    window.currentWorkspaceUser = user;
    document.dispatchEvent(new CustomEvent("workspace:user", { detail: { user } }));
    authGate.hidden = true;
    appShell.hidden = false;
    const initial = user.username.slice(0, 1).toUpperCase();
    for (const element of [
      $("#sidebar-user-avatar"),
      $("#topbar-user-avatar"),
    ]) {
      element.textContent = initial;
    }
    $("#sidebar-user-name").textContent = user.username;
    $("#sidebar-user-role").textContent = user.role_label;

    $$("[data-permission]").forEach((element) => {
      element.hidden = !hasPermission(element.dataset.permission);
    });
    $$(".nav-item.is-disabled").forEach((element) => {
      element.hidden = true;
    });
    $$(".nav-group").forEach((group) => {
      group.hidden = !$$(".nav-item:not([hidden])", group).length;
    });

    let route = currentRouteFromHash();
    if (!hasPermission(routePermissions[route] || "chat.use")) route = "chat";
    setRoute(route);
    loadHealth();
    loadDocuments({ silent: true });
    if (hasPermission("documents.manage")) resumeLatestReview();
    questionInput.focus();
  }

  function delay(milliseconds) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  }

  function setRoute(route, { updateHash = true } = {}) {
    const requested = route in routeLabels ? route : "chat";
    const nextRoute = hasPermission(routePermissions[requested] || "chat.use")
      ? requested
      : "chat";
    state.route = nextRoute;

    $$(".page").forEach((page) => {
      const active = page.id === `page-${nextRoute}`;
      page.classList.toggle("is-active", active);
      page.hidden = !active;
    });

    $$("[data-route]").forEach((button) => {
      const active = button.dataset.route === nextRoute;
      button.classList.toggle("is-active", active);
      if (active) {
        button.setAttribute("aria-current", "page");
      } else {
        button.removeAttribute("aria-current");
      }
    });

    const label = routeLabels[nextRoute];
    $("#current-page-label").textContent = label;
    document.title = `${label} · 设备文档助手`;
    if (updateHash && window.location.hash !== `#/${nextRoute}`) {
      window.location.hash = `#/${nextRoute}`;
    }
    if (nextRoute === "documents") {
      loadDocuments({ silent: true });
      resumeLatestReview();
    } else if (nextRoute === "ops") {
      window.ManufacturingOps?.load();
    } else if (nextRoute === "versions") {
      loadVersions();
    } else if (nextRoute === "users") {
      loadUsers();
    } else if (nextRoute === "permissions") {
      loadPermissions();
    } else if (nextRoute === "audit") {
      loadAudit();
    }
  }

  function currentRouteFromHash() {
    const route = window.location.hash.replace(/^#\//, "");
    return route in routeLabels ? route : "chat";
  }

  async function loadHealth() {
    try {
      const payload = await requestJson("/health", { cache: "no-store" });
      serviceState.classList.remove("is-loading");
      serviceState.classList.toggle("is-error", !payload.ready_for_questions);
      serviceStateText.textContent = payload.ready_for_questions ? "服务正常" : "配置待完善";
      serviceState.title = payload.ready_for_questions
        ? "数据、索引和模型配置均已就绪"
        : `缺少：${payload.missing.join("、")}`;
    } catch {
      serviceState.classList.remove("is-loading");
      serviceState.classList.add("is-error");
      serviceStateText.textContent = "服务不可用";
      serviceState.title = "无法连接本地 API";
    }
  }

  function renderDocuments() {
    documentList.replaceChildren();

    if (!state.documents.length) {
      const row = makeElement("tr", "empty-row");
      const cell = makeElement("td", "", "还没有文档，请先上传一份 PDF。");
      cell.colSpan = 5;
      row.append(cell);
      documentList.append(row);
      return;
    }

    for (const doc of state.documents) {
      const row = document.createElement("tr");
      const nameCell = makeElement("td", "document-name-cell");
      const name = makeElement("span", "document-name", doc.name);
      name.title = doc.name;
      const subtitle = makeElement(
        "span",
        "document-subtitle",
        `${formatDate(doc.uploaded_at)} · ${formatBytes(doc.size_bytes)}`,
      );
      nameCell.append(name, subtitle);
      const pagesCell = makeElement("td", "", doc.pages == null ? "未知" : String(doc.pages));
      const chunksCell = makeElement("td", "", String(doc.chunks));
      const statusCell = document.createElement("td");
      const status = makeElement(
        "span",
        `status-pill${doc.status === "ready" ? "" : " is-pending"}`,
        doc.status === "ready" ? "已完成" : doc.status === "pending" ? "待处理" : "未索引",
      );
      statusCell.append(status);
      const actionCell = document.createElement("td");
      if (doc.pdf_url) {
        const link = makeElement("a", "document-open", "↗");
        link.href = doc.pdf_url;
        link.target = "_blank";
        link.rel = "noopener";
        link.setAttribute("aria-label", `打开 ${doc.name}`);
        actionCell.append(link);
      }
      row.append(nameCell, pagesCell, chunksCell, statusCell, actionCell);
      documentList.append(row);
    }
  }

  async function loadDocuments({ silent = false } = {}) {
    if (!silent) documentTotals.textContent = "正在读取文档目录";
    try {
      const payload = await requestJson("/documents", { cache: "no-store" });
      state.documents = payload.documents;
      renderDocuments();
      const totals = payload.totals;
      documentTotals.textContent = `${totals.documents} 份 · ${totals.pages} 页 · ${totals.chunks} 个知识块`;
      indexSummary.textContent = `当前正式索引包含 ${totals.chunks} 个知识块`;
      const readyCount = state.documents.filter((doc) => doc.status === "ready").length;
      documentReadyBadge.lastElementChild.textContent = `${readyCount} 份文档已就绪`;
    } catch (error) {
      documentTotals.textContent = "文档目录读取失败";
      documentList.replaceChildren();
      const row = makeElement("tr", "empty-row");
      const cell = makeElement("td", "", error.message);
      cell.colSpan = 5;
      row.append(cell);
      documentList.append(row);
      if (!silent) showToast(error.message, "error");
    }
  }

  async function loadVersions() {
    const container = $("#version-list");
    container.replaceChildren(makeElement("p", "admin-empty", "正在读取版本列表…"));
    try {
      const payload = await requestJson("/versions", { cache: "no-store" });
      state.versions = payload.versions;
      $("#versions-summary").textContent = `共 ${payload.versions.length} 个索引版本，当前版本会显示标记`;
      container.replaceChildren();
      if (!payload.versions.length) {
        container.append(makeElement("p", "admin-empty", "还没有可用的索引版本"));
        return;
      }
      for (const version of payload.versions) {
        const row = makeElement("div", `version-row${version.is_current ? " is-current" : ""}`);
        const main = makeElement("div", "version-main");
        main.append(
          makeElement("strong", "", version.label),
          makeElement(
            "small",
            "",
            `${formatDate(version.created_at)} · ${version.created_by}${version.note ? ` · ${version.note}` : ""}`,
          ),
        );
        const stats = makeElement(
          "span",
          "version-stats",
          `${version.chunk_count} 个知识块 · ${version.document_names.length} 份文档`,
        );
        if (version.is_current) {
          row.append(main, stats, makeElement("span", "current-version-tag", "当前版本"));
        } else {
          const button = makeElement("button", "mini-button", "回滚到此版本");
          button.type = "button";
          button.addEventListener("click", () => rollbackVersion(version));
          row.append(main, stats, button);
        }
        container.append(row);
      }
    } catch (error) {
      container.replaceChildren(makeElement("p", "admin-empty", error.message));
    }
  }

  async function rollbackVersion(version) {
    if (!window.confirm(`确定回滚到“${version.label}”吗？系统会先自动备份当前版本。`)) return;
    try {
      await requestJson(`/versions/${encodeURIComponent(version.version_id)}/rollback`, {
        method: "POST",
      });
      showToast("索引已回滚，问答将使用所选版本");
      await Promise.all([loadVersions(), loadDocuments({ silent: true })]);
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function loadUsers() {
    const body = $("#user-list");
    body.replaceChildren();
    const loading = makeElement("tr", "empty-row");
    const loadingCell = makeElement("td", "", "正在读取用户…");
    loadingCell.colSpan = 5;
    loading.append(loadingCell);
    body.append(loading);
    try {
      const payload = await requestJson("/admin/users", { cache: "no-store" });
      state.rolesPayload = { roles: payload.roles };
      $("#users-summary").textContent = `共 ${payload.users.length} 个账号`;
      body.replaceChildren();
      for (const user of payload.users) {
        const row = document.createElement("tr");
        const userCell = makeElement("td", "user-main");
        userCell.append(
          makeElement("strong", "", user.username),
          makeElement("small", "", user.id === state.user.id ? "当前登录账号" : `ID ${user.id}`),
        );

        const roleCell = document.createElement("td");
        const roleSelect = document.createElement("select");
        roleSelect.className = "chunk-section-input";
        roleSelect.style.minWidth = "130px";
        for (const role of payload.roles) {
          const option = document.createElement("option");
          option.value = role.id;
          option.textContent = role.label;
          option.selected = role.id === user.role;
          roleSelect.append(option);
        }
        roleSelect.addEventListener("change", () => updateManagedUser(user.id, { role: roleSelect.value }));
        roleCell.append(roleSelect);

        const statusCell = makeElement(
          "td",
          "",
          user.active ? "已启用" : "已停用",
        );
        const loginCell = makeElement(
          "td",
          "",
          user.last_login_at ? formatDate(user.last_login_at) : "从未登录",
        );
        const actionCell = makeElement("td", "user-row-actions");
        const toggle = makeElement("button", "mini-button", user.active ? "停用" : "启用");
        toggle.type = "button";
        toggle.addEventListener("click", () => updateManagedUser(user.id, { active: !user.active }));
        const reset = makeElement("button", "mini-button", "重置密码");
        reset.type = "button";
        reset.addEventListener("click", () => {
          const password = window.prompt(`为 ${user.username} 设置新密码（至少 8 位）`);
          if (password) updateManagedUser(user.id, { password });
        });
        actionCell.append(toggle, reset);
        row.append(userCell, roleCell, statusCell, loginCell, actionCell);
        body.append(row);
      }
    } catch (error) {
      body.replaceChildren();
      const row = makeElement("tr", "empty-row");
      const cell = makeElement("td", "", error.message);
      cell.colSpan = 5;
      row.append(cell);
      body.append(row);
    }
  }

  async function updateManagedUser(userId, payload) {
    try {
      await requestJson(`/admin/users/${userId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      showToast("用户信息已更新");
      if (userId === state.user.id) {
        const meResponse = await fetch("/auth/me", { cache: "no-store" });
        if (meResponse.ok) {
          const current = await meResponse.json();
          enterApp(current.user);
        } else {
          showAuth("login");
        }
      } else {
        await loadUsers();
      }
    } catch (error) {
      showToast(error.message, "error");
      await loadUsers();
    }
  }

  async function loadPermissions() {
    const matrix = $("#permission-matrix");
    matrix.replaceChildren(makeElement("p", "admin-empty", "正在读取权限矩阵…"));
    try {
      const payload = await requestJson("/admin/permissions", { cache: "no-store" });
      state.rolesPayload = payload;
      const table = makeElement("table", "permission-table");
      const head = document.createElement("thead");
      const headRow = document.createElement("tr");
      headRow.append(makeElement("th", "", "权限"));
      for (const role of payload.roles) {
        headRow.append(makeElement("th", "", role.label));
      }
      head.append(headRow);
      const body = document.createElement("tbody");
      for (const [permission, label] of Object.entries(payload.permissions)) {
        const row = document.createElement("tr");
        row.append(makeElement("td", "", label));
        for (const role of payload.roles) {
          const allowed = role.permissions.includes(permission);
          row.append(
            makeElement(
              "td",
              allowed ? "permission-yes" : "permission-no",
              allowed ? "允许" : "—",
            ),
          );
        }
        body.append(row);
      }
      table.append(head, body);
      matrix.replaceChildren(table);
    } catch (error) {
      matrix.replaceChildren(makeElement("p", "admin-empty", error.message));
    }
  }

  const auditActionLabels = {
    "auth.setup": "初始化系统",
    "auth.login": "登录",
    "auth.logout": "退出登录",
    "qa.ask": "文档问答",
    "document.upload": "上传文档",
    "document.upload_duplicate": "重复上传",
    "document.upload_duplicate_draft": "重复草稿",
    "document.clean": "更新清洗结果",
    "document.reparse": "重新解析",
    "document.rechunk": "重新切分",
    "document.chunk_update": "修改知识块",
    "document.publish": "发布文档",
    "document.discard": "放弃入库",
    "version.rollback": "回滚版本",
    "user.create": "创建用户",
    "user.update": "更新用户",
  };

  async function loadAudit() {
    const list = $("#audit-list");
    list.replaceChildren(makeElement("p", "admin-empty", "正在读取操作日志…"));
    try {
      const payload = await requestJson("/audit?limit=200", { cache: "no-store" });
      $("#audit-summary").textContent = `最近 ${payload.logs.length} 条操作`;
      list.replaceChildren();
      if (!payload.logs.length) {
        list.append(makeElement("p", "admin-empty", "还没有操作日志"));
        return;
      }
      for (const item of payload.logs) {
        const row = makeElement("div", "audit-row");
        row.append(
          makeElement("span", "audit-time", formatDate(item.created_at)),
          makeElement("span", "audit-action", auditActionLabels[item.action] || item.action),
          makeElement(
            "span",
            "audit-detail",
            `${item.username} · ${item.role}${item.target ? ` · ${item.target}` : ""}`,
          ),
          makeElement("span", "audit-ip", item.ip_address || "本地"),
        );
        list.append(row);
      }
    } catch (error) {
      list.replaceChildren(makeElement("p", "admin-empty", error.message));
    }
  }

  function scrollChatToBottom() {
    window.requestAnimationFrame(() => {
      chatThread.scrollTop = chatThread.scrollHeight;
    });
  }

  function createMessageShell(role) {
    const article = makeElement("article", `message ${role}-message`);
    if (role === "user") {
      const body = makeElement("div", "message-body");
      body.append(
        makeElement("div", "message-author", "你"),
        makeElement("div", "message-bubble"),
      );
      article.append(body);
      return article;
    }
    const avatar = makeElement("div", "message-avatar");
    avatar.setAttribute("aria-hidden", "true");
    avatar.append(makeElement("span", "mini-doc"));
    const body = makeElement("div", "message-body");
    body.append(
      makeElement("div", "message-author", "设备文档助手"),
      makeElement("div", "message-bubble"),
    );
    article.append(avatar, body);
    return article;
  }

  function appendUserMessage(text) {
    const article = createMessageShell("user");
    $(".message-bubble", article).append(makeElement("p", "", text));
    chatThread.append(article);
    scrollChatToBottom();
  }

  function appendLoadingMessage() {
    const article = createMessageShell("assistant");
    const dots = makeElement("div", "loading-dots");
    dots.setAttribute("aria-label", "正在检索并生成答案");
    dots.append(document.createElement("span"), document.createElement("span"), document.createElement("span"));
    $(".message-bubble", article).append(dots);
    chatThread.append(article);
    scrollChatToBottom();
    return article;
  }

  function addEvidenceCard(container, source, requestId, index) {
    const details = makeElement("details", "evidence-card");
    details.id = `evidence-${requestId}-${index}`;
    const summary = document.createElement("summary");
    summary.append(
      makeElement("span", "", `原文依据 ${index + 1} · ${displayDocumentName(source.document)}`),
      makeElement("span", "", pageLabel(source)),
    );
    const body = makeElement("div", "evidence-body");
    const location = makeElement("div", "evidence-location");
    location.append(
      makeElement("span", "", source.section || "正文"),
      makeElement("span", "", source.chunk_id || ""),
    );
    body.append(location, makeElement("blockquote", "evidence-quote", source.quote || ""));
    if (source.pdf_url) {
      const link = makeElement("a", "pdf-link", `打开 PDF ${pageLabel(source)} ↗`);
      link.href = `${source.pdf_url}#page=${source.page_start}&zoom=page-width`;
      link.target = "_blank";
      link.rel = "noopener";
      body.append(link);
    }
    details.append(summary, body);
    container.append(details);
  }

  function renderAnswer(article, result, elapsedSeconds) {
    const bubble = $(".message-bubble", article);
    const body = $(".message-body", article);
    bubble.replaceChildren();
    const paragraphs = result.answer
      .split(/\n{2,}/)
      .map((paragraph) => paragraph.trim())
      .filter(Boolean);
    for (const paragraph of paragraphs) {
      bubble.append(makeElement("p", "", paragraph));
    }

    const meta = makeElement("div", "message-meta");
    let verificationText;
    let verificationClass = "verification-pill";
    if (result.refused) {
      verificationText = "资料不足 · 已明确拒答";
      verificationClass += " is-refused";
    } else if (result.citations.length) {
      verificationText = `引用已定位 ${result.citations.length} 条`;
    } else {
      verificationText = "未定位到可核验引用";
      verificationClass += " is-warning";
    }
    meta.append(
      makeElement("span", verificationClass, verificationText),
      makeElement("span", "", `${elapsedSeconds.toFixed(1)} 秒`),
    );
    body.append(meta);

    if (result.citations.length) {
      const citationList = makeElement("div", "citation-list");
      result.citations.forEach((source, index) => {
        const button = makeElement(
          "button",
          "citation-chip",
          `${displayDocumentName(source.document)} · ${pageLabel(source)}`,
        );
        button.type = "button";
        button.addEventListener("click", () => {
          const target = $(`#evidence-${result.request_id}-${index}`);
          if (!target) return;
          target.open = true;
          target.scrollIntoView({ block: "nearest", behavior: "smooth" });
        });
        citationList.append(button);
      });
      body.append(citationList);
      const evidenceList = makeElement("div", "evidence-list");
      result.citations.forEach((source, index) => {
        addEvidenceCard(evidenceList, source, result.request_id, index);
      });
      body.append(evidenceList);
    }
    window.requestAnimationFrame(() => {
      article.scrollIntoView({ block: "start", behavior: "smooth" });
    });
  }

  function renderAnswerError(article, message) {
    const bubble = $(".message-bubble", article);
    bubble.replaceChildren(makeElement("p", "", message));
    bubble.classList.add("error-bubble");
    scrollChatToBottom();
  }

  async function submitQuestion(question) {
    const text = question.trim();
    if (!text || state.asking) return;
    state.asking = true;
    sendButton.disabled = true;
    questionInput.disabled = true;
    questionInput.value = "";
    resizeComposer();
    appendUserMessage(text);
    const answerMessage = appendLoadingMessage();
    const started = performance.now();
    try {
      const result = await requestJson("/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text }),
      });
      renderAnswer(answerMessage, result, (performance.now() - started) / 1000);
    } catch (error) {
      renderAnswerError(answerMessage, error.message || "问答请求失败，请稍后重试。");
      showToast(error.message || "问答请求失败", "error");
    } finally {
      state.asking = false;
      sendButton.disabled = false;
      questionInput.disabled = false;
      questionInput.focus();
    }
  }

  function resizeComposer() {
    questionInput.style.height = "auto";
    questionInput.style.height = `${Math.min(questionInput.scrollHeight, 140)}px`;
  }

  function setPipeline(stage) {
    const processingStages = ["parsing", "cleaning", "chunking", "review", "publishing"];
    const currentIndex = reviewStageIndex(stage);
    for (const item of pipelineItems) {
      const itemIndex = processingStages.indexOf(item.dataset.stage);
      item.classList.toggle("is-current", itemIndex === currentIndex);
      item.classList.toggle(
        "is-complete",
        currentIndex > itemIndex || ["published", "discarded"].includes(stage),
      );
    }
  }

  function reviewStageIndex(stage) {
    if (stage === "published" || stage === "complete") return 5;
    return ["parsing", "cleaning", "chunking", "review", "publishing"].indexOf(stage);
  }

  function updateJobUI(job, uploadPercent = null) {
    jobPanel.hidden = false;
    const percent = uploadPercent == null ? Number(job.progress || 0) : uploadPercent;
    let title = `正在处理：${job.document_name || job.filename || "文档"}`;
    if (job.status === "review") title = "草稿已生成，等待人工审核";
    if (job.status === "failed") title = "文档处理失败";
    if (job.status === "complete" && job.published) title = "文档发布完成";
    if (job.status === "complete" && job.duplicate) title = "文档已存在";
    if (job.status === "discarded") title = "本次入库已放弃";
    jobTitle.textContent = title;
    jobMessage.textContent = job.message || "处理中";
    jobPercent.textContent = `${Math.round(percent)}%`;
    jobProgress.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    progressTrack.setAttribute("aria-valuenow", String(Math.round(percent)));
    setPipeline(job.stage || "queued");
  }

  function updateReviewStatus(status) {
    reviewStatusPill.className = `status-pill${status === "review" ? " is-pending" : ""}`;
    reviewStatusPill.textContent = status === "review"
      ? "待审核"
      : status === "publishing"
        ? "发布中"
        : status === "complete"
          ? "已发布"
          : status === "discarded"
            ? "已放弃"
            : "处理中";
  }

  async function loadReview(jobId) {
    const payload = await requestJson(`/jobs/${encodeURIComponent(jobId)}/review`, {
      cache: "no-store",
    });
    state.review = payload;
    state.activeJob = payload.job;
    const settings = payload.job.clean_settings;
    state.cleanDraft = {
      preset: settings.preset || "standard",
      restoreKeys: new Set(settings.restore_keys || []),
      removeKeys: new Set(settings.remove_keys || []),
      excludedPages: new Set(settings.excluded_pages || []),
    };
    if (!payload.cleaning?.pages?.some((page) => page.page === state.cleanPage)) {
      state.cleanPage = payload.cleaning?.pages?.[0]?.page ?? null;
    }
    reviewCard.hidden = false;
    reviewDocumentName.textContent = payload.job.document_name || payload.job.filename;
    updateReviewStatus(payload.job.status);
    renderReview();
  }

  async function resumeLatestReview() {
    if (state.activeJob && state.review) return;
    try {
      const jobs = await requestJson("/jobs", { cache: "no-store" });
      const latest = jobs.find((job) => job.status === "review");
      if (!latest) return;
      state.activeJob = latest;
      updateJobUI(latest);
      await loadReview(latest.job_id);
    } catch {
      // 没有历史草稿时保持文档列表即可。
    }
  }

  function renderReview() {
    if (!state.review || !state.cleanDraft) return;
    const { cleaning, chunks } = state.review;
    cleanCountBadge.textContent = String(cleaning?.removed_items?.length || 0);
    chunkCountBadge.textContent = String(chunks.filter((item) => item.state === "active").length);
    cleanPreset.value = state.cleanDraft.preset;

    cleanPageSelect.replaceChildren();
    for (const page of cleaning?.pages || []) {
      const option = document.createElement("option");
      option.value = String(page.page);
      option.textContent = `第 ${page.page} 页 · 保留 ${page.kept_lines} / 移除 ${page.removed_lines}`;
      option.selected = page.page === state.cleanPage;
      cleanPageSelect.append(option);
    }
    renderCleanSummary();
    renderCleanPage();
    renderChunkSettings();
    renderChunks();
  }

  function renderCleanSummary() {
    const counts = state.review?.cleaning?.removed_counts || {};
    const labels = Object.entries(counts)
      .map(([reason, count]) => `${cleanReasonLabels[reason] || reason} ${count}`)
      .join(" · ");
    cleanSummary.textContent = labels
      ? `自动清洗删除：${labels}。可在右侧恢复误删行或标记新的噪声行。`
      : "自动清洗未删除内容；可在右侧人工标记噪声行或排除整页。";
  }

  function renderCleanPage() {
    cleanLines.replaceChildren();
    const page = state.review?.cleaning?.pages?.find((item) => item.page === state.cleanPage);
    if (!page) {
      cleanLines.append(makeElement("div", "clean-empty", "没有可审核的页面"));
      excludePageCheckbox.checked = false;
      excludePageCheckbox.disabled = true;
      return;
    }
    const pageExcluded = state.cleanDraft.excludedPages.has(page.page);
    excludePageCheckbox.disabled = false;
    excludePageCheckbox.checked = pageExcluded;

    for (const line of page.lines) {
      const restored = state.cleanDraft.restoreKeys.has(line.key);
      const isRemoved = pageExcluded
        || (!restored && (line.state === "removed" || state.cleanDraft.removeKeys.has(line.key)));
      const row = makeElement(
        "div",
        `clean-line${isRemoved ? " is-removed" : ""}${state.cleanDraft.removeKeys.has(line.key) ? " is-manual-remove" : ""}`,
      );
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = !isRemoved;
      checkbox.disabled = pageExcluded;
      checkbox.setAttribute("aria-label", `保留第 ${page.page} 页这一行`);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          state.cleanDraft.restoreKeys.add(line.key);
          state.cleanDraft.removeKeys.delete(line.key);
        } else {
          state.cleanDraft.removeKeys.add(line.key);
          state.cleanDraft.restoreKeys.delete(line.key);
        }
        renderCleanPage();
      });
      const text = makeElement("span", "", line.text);
      const reasonText = pageExcluded
        ? "整页排除"
        : state.cleanDraft.removeKeys.has(line.key)
          ? "人工排除"
          : line.reason
            ? cleanReasonLabels[line.reason] || line.reason
            : "保留";
      const reason = makeElement("span", "line-reason", reasonText);
      row.append(checkbox, text, reason);
      cleanLines.append(row);
    }
  }

  function renderChunkSettings() {
    const settings = state.activeJob?.chunk_settings || {};
    const preset = Object.entries(chunkPresets).find(([, value]) => (
      value.max_chars === settings.max_chars
      && value.min_chars === settings.min_chars
      && value.table_max_chars === settings.table_max_chars
    ));
    chunkPreset.value = preset?.[0] || "standard";
    splitByPageCheckbox.checked = Boolean(settings.split_by_page);
  }

  function renderChunks() {
    chunkList.replaceChildren();
    const chunks = state.review?.chunks || [];
    const activeCount = chunks.filter((item) => item.state === "active").length;
    chunkSummary.textContent = `共 ${chunks.length} 个知识块，当前 ${activeCount} 个会进入问答索引。修改章节名称后需点击空白处或按 Enter 保存。`;

    for (const item of chunks) {
      const card = makeElement("article", `chunk-item${item.state === "excluded" ? " is-excluded" : ""}`);
      const head = makeElement("div", "chunk-head");
      const sectionInput = makeElement("input", "chunk-section-input");
      sectionInput.value = item.section || "正文";
      sectionInput.setAttribute("aria-label", "修改章节名称");
      const saveSection = async () => {
        if (sectionInput.value.trim() === item.section) return;
        try {
          const job = await requestJson(
            `/jobs/${encodeURIComponent(state.activeJob.job_id)}/chunks/${encodeURIComponent(item.id)}`,
            {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ section: sectionInput.value }),
            },
          );
          state.activeJob = job;
          await loadReview(job.job_id);
          showToast("章节名称已更新");
        } catch (error) {
          sectionInput.value = item.section;
          showToast(error.message, "error");
        }
      };
      sectionInput.addEventListener("blur", saveSection);
      sectionInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          sectionInput.blur();
        }
      });

      const meta = makeElement(
        "span",
        "chunk-meta",
        `${item.page_from === item.page_to ? `第 ${item.page_from} 页` : `第 ${item.page_from}–${item.page_to} 页`} · ${item.kind === "table" ? "表格" : "正文"} · ${item.body?.length || 0} 字`,
      );
      const includeLabel = makeElement("label", "chunk-include");
      const include = document.createElement("input");
      include.type = "checkbox";
      include.checked = item.state === "active";
      includeLabel.append(include, document.createTextNode("参与问答"));
      include.addEventListener("change", async () => {
        try {
          const job = await requestJson(
            `/jobs/${encodeURIComponent(state.activeJob.job_id)}/chunks/${encodeURIComponent(item.id)}`,
            {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                excluded: !include.checked,
                exclude_reason: include.checked ? null : "人工排除",
              }),
            },
          );
          state.activeJob = job;
          await loadReview(job.job_id);
          showToast(include.checked ? "知识块已恢复" : "知识块已排除");
        } catch (error) {
          include.checked = !include.checked;
          showToast(error.message, "error");
        }
      });
      head.append(sectionInput, meta, includeLabel);

      const body = makeElement("div", "chunk-body clamped", item.body || item.text);
      const footer = makeElement("div", "chunk-footer");
      const toggle = makeElement("button", "", "展开全文");
      toggle.type = "button";
      toggle.addEventListener("click", () => {
        const clamped = body.classList.toggle("clamped");
        toggle.textContent = clamped ? "展开全文" : "收起正文";
      });
      const link = makeElement(
        "a",
        "",
        `查看 PDF 第 ${item.page_from} 页 ↗`,
      );
      link.href = `${state.review.pdf_url}#page=${item.page_from}&zoom=page-width`;
      link.target = "_blank";
      link.rel = "noopener";
      footer.append(toggle, link);
      card.append(head, body, footer);
      chunkList.append(card);
    }
  }

  function setReviewTab(tab) {
    state.reviewTab = tab;
    $$("[data-review-tab]").forEach((button) => {
      const active = button.dataset.reviewTab === tab;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
    });
    $("#review-cleaning").hidden = tab !== "cleaning";
    $("#review-chunks").hidden = tab !== "chunks";
  }

  async function pollDraftJob(jobId) {
    for (let attempt = 0; attempt < 180; attempt += 1) {
      await delay(900);
      const job = await requestJson(`/jobs/${encodeURIComponent(jobId)}`, { cache: "no-store" });
      state.activeJob = job;
      updateJobUI(job);
      if (job.status === "review") {
        state.uploading = false;
        uploadDropzone.disabled = false;
        await loadReview(job.job_id);
        setReviewTab("cleaning");
        showToast("自动处理完成，请审核清洗和切片结果");
        return job;
      }
      if (job.status === "failed") {
        state.uploading = false;
        uploadDropzone.disabled = false;
        throw new Error(job.message || "文档处理失败");
      }
    }
    throw new Error("文档处理超时，请稍后刷新页面");
  }

  async function pollPublishJob(jobId) {
    for (let attempt = 0; attempt < 180; attempt += 1) {
      await delay(900);
      const job = await requestJson(`/jobs/${encodeURIComponent(jobId)}`, { cache: "no-store" });
      state.activeJob = job;
      updateJobUI(job);
      updateReviewStatus(job.status);
      if (job.status === "complete" && job.published) {
        state.uploading = false;
        uploadDropzone.disabled = false;
        showToast("审核通过，文档已发布到问答索引");
        await loadDocuments({ silent: true });
        return job;
      }
      if (job.status === "review") {
        state.uploading = false;
        uploadDropzone.disabled = false;
        throw new Error("发布失败，已恢复正式索引");
      }
    }
    throw new Error("发布超时，请刷新页面确认状态");
  }

  function uploadWithProgress(file) {
    return new Promise((resolve, reject) => {
      const formData = new FormData();
      formData.append("file", file);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/documents/upload");
      xhr.responseType = "json";
      xhr.upload.addEventListener("progress", (event) => {
        if (!event.lengthComputable) return;
        const uploadPercent = Math.round((event.loaded / event.total) * 5);
        updateJobUI(
          {
            filename: file.name,
            document_name: file.name,
            stage: "queued",
            progress: uploadPercent,
            message: "正在上传 PDF 文件",
            status: "queued",
          },
          uploadPercent,
        );
      });
      xhr.addEventListener("load", () => {
        const payload = xhr.response;
        if (xhr.status >= 200 && xhr.status < 300 && payload) {
          resolve(payload);
          return;
        }
        const detail = payload?.detail;
        const message = typeof detail === "string"
          ? detail
          : detail?.message || `上传失败（${xhr.status}）`;
        reject(new Error(message));
      });
      xhr.addEventListener("error", () => reject(new Error("网络错误，PDF 上传失败")));
      xhr.send(formData);
    });
  }

  async function handleFile(file) {
    if (!file || state.uploading) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      showToast("只支持 PDF 文件", "error");
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      showToast("PDF 不能超过 50 MB", "error");
      return;
    }
    state.uploading = true;
    uploadDropzone.disabled = true;
    reviewCard.hidden = true;
    jobPanel.hidden = false;
    updateJobUI({
      filename: file.name,
      document_name: file.name,
      stage: "queued",
      progress: 0,
      message: "正在准备上传",
      status: "queued",
    });
    try {
      const job = await uploadWithProgress(file);
      state.activeJob = job;
      updateJobUI(job);
      if (job.duplicate) {
        state.uploading = false;
        uploadDropzone.disabled = false;
        showToast("该 PDF 已在文档库中");
        await loadDocuments({ silent: true });
      } else if (job.status === "review") {
        state.uploading = false;
        uploadDropzone.disabled = false;
        await loadReview(job.job_id);
      } else {
        await pollDraftJob(job.job_id);
      }
    } catch (error) {
      state.uploading = false;
      uploadDropzone.disabled = false;
      jobTitle.textContent = "文档处理失败";
      jobMessage.textContent = error.message || "请稍后重试";
      showToast(error.message || "文档处理失败", "error");
    } finally {
      fileInput.value = "";
    }
  }

  async function applyCleaning() {
    if (!state.activeJob) return;
    if (
      state.review?.chunks?.some((item) => item.state === "excluded")
      && !window.confirm("应用清洗会重新切分知识块，并重置当前已经排除的知识块。是否继续？")
    ) {
      return;
    }
    const button = $("#apply-clean-button");
    button.disabled = true;
    try {
      const job = await requestJson(`/jobs/${encodeURIComponent(state.activeJob.job_id)}/clean`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preset: state.cleanDraft.preset,
          restore_keys: [...state.cleanDraft.restoreKeys],
          remove_keys: [...state.cleanDraft.removeKeys],
          excluded_pages: [...state.cleanDraft.excludedPages],
        }),
      });
      state.activeJob = job;
      updateJobUI(job);
      await loadReview(job.job_id);
      showToast("清洗结果已更新，切片已重新生成");
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function reparseDraft() {
    if (!state.activeJob) return;
    if (!window.confirm("重新解析会覆盖当前清洗和切片结果，是否继续？")) return;
    const button = $("#reparse-button");
    button.disabled = true;
    try {
      const job = await requestJson(`/jobs/${encodeURIComponent(state.activeJob.job_id)}/reparse`, {
        method: "POST",
      });
      state.activeJob = job;
      await loadReview(job.job_id);
      showToast("PDF 已重新解析并生成新的审核草稿");
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function rechunkDraft() {
    if (!state.activeJob) return;
    if (
      state.review?.chunks?.some((item) => item.state === "excluded")
      && !window.confirm("重新切分后，当前知识块的排除状态会被重置。是否继续？")
    ) {
      return;
    }
    const button = $("#rechunk-button");
    button.disabled = true;
    const preset = chunkPresets[chunkPreset.value] || chunkPresets.standard;
    try {
      const job = await requestJson(`/jobs/${encodeURIComponent(state.activeJob.job_id)}/rechunk`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...preset,
          split_by_page: splitByPageCheckbox.checked,
        }),
      });
      state.activeJob = job;
      await loadReview(job.job_id);
      setReviewTab("chunks");
      showToast("切片已按新参数重新生成");
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function publishDraft() {
    if (!state.activeJob) return;
    if (!window.confirm("发布后该文档会进入问答正式索引，是否继续？")) return;
    state.uploading = true;
    $("#publish-draft-button").disabled = true;
    try {
      const job = await requestJson(`/jobs/${encodeURIComponent(state.activeJob.job_id)}/publish`, {
        method: "POST",
      });
      state.activeJob = job;
      updateJobUI({
        ...job,
        status: "running",
        stage: "publishing",
        progress: 8,
        message: "正在构建正式索引",
      });
      updateReviewStatus("publishing");
      await pollPublishJob(job.job_id);
    } catch (error) {
      state.uploading = false;
      showToast(error.message, "error");
    } finally {
      $("#publish-draft-button").disabled = false;
    }
  }

  async function discardDraft() {
    if (!state.activeJob) return;
    if (!window.confirm("放弃后审核草稿不会进入正式问答索引，是否继续？")) return;
    try {
      const job = await requestJson(`/jobs/${encodeURIComponent(state.activeJob.job_id)}/discard`, {
        method: "POST",
      });
      state.activeJob = job;
      reviewCard.hidden = true;
      updateJobUI(job);
      showToast("本次入库已放弃，正式索引未改变");
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  questionForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitQuestion(questionInput.value);
  });
  questionInput.addEventListener("input", resizeComposer);
  questionInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      questionForm.requestSubmit();
    }
  });
  $$("[data-question]").forEach((button) => {
    button.addEventListener("click", () => {
      questionInput.value = button.dataset.question;
      resizeComposer();
      submitQuestion(questionInput.value);
    });
  });
  $$("[data-route]").forEach((button) => {
    button.addEventListener("click", () => setRoute(button.dataset.route));
  });
  $$("[data-review-tab]").forEach((button) => {
    button.addEventListener("click", () => setReviewTab(button.dataset.reviewTab));
  });

  cleanPreset.addEventListener("change", () => {
    state.cleanDraft.preset = cleanPreset.value;
  });
  cleanPageSelect.addEventListener("change", () => {
    state.cleanPage = Number(cleanPageSelect.value);
    renderCleanPage();
  });
  excludePageCheckbox.addEventListener("change", () => {
    if (state.cleanPage == null) return;
    if (excludePageCheckbox.checked) {
      state.cleanDraft.excludedPages.add(state.cleanPage);
    } else {
      state.cleanDraft.excludedPages.delete(state.cleanPage);
    }
    renderCleanPage();
  });
  $("#apply-clean-button").addEventListener("click", applyCleaning);
  $("#reparse-button").addEventListener("click", reparseDraft);
  $("#rechunk-button").addEventListener("click", rechunkDraft);
  $("#publish-draft-button").addEventListener("click", publishDraft);
  $("#discard-draft-button").addEventListener("click", discardDraft);

  uploadDropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => handleFile(fileInput.files[0]));
  for (const eventName of ["dragenter", "dragover"]) {
    uploadDropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      uploadDropzone.classList.add("is-dragging");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    uploadDropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      uploadDropzone.classList.remove("is-dragging");
    });
  }
  uploadDropzone.addEventListener("drop", (event) => handleFile(event.dataTransfer.files[0]));
  $("#reload-documents").addEventListener("click", () => loadDocuments());
  $("#refresh-button").addEventListener("click", () => {
    if (state.route === "documents") {
      loadDocuments();
      loadHealth();
      return;
    }
    if (state.route === "versions") {
      loadVersions();
      return;
    }
    if (state.route === "ops") {
      window.ManufacturingOps?.load();
      return;
    }
    if (state.route === "users") {
      loadUsers();
      return;
    }
    if (state.route === "permissions") {
      loadPermissions();
      return;
    }
    if (state.route === "audit") {
      loadAudit();
      return;
    }
    loadHealth();
    loadDocuments({ silent: true });
    questionInput.focus();
  });
  $("#reload-versions").addEventListener("click", loadVersions);
  $("#reload-users").addEventListener("click", loadUsers);
  $("#reload-audit").addEventListener("click", loadAudit);
  $("#logout-button").addEventListener("click", async () => {
    try {
      await requestJson("/auth/logout", { method: "POST" });
    } catch {
      // 会话可能已经过期，仍然回到登录页。
    }
    state.user = null;
    state.review = null;
    state.activeJob = null;
    window.location.hash = "#/chat";
    showAuth("login");
  });

  authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const mode = authForm.dataset.mode || "login";
    const username = $("#auth-username").value.trim();
    const password = $("#auth-password").value;
    if (mode === "setup" && password !== $("#auth-confirm-password").value) {
      authError.textContent = "两次输入的密码不一致";
      authError.hidden = false;
      return;
    }
    const submit = $("#auth-submit");
    submit.disabled = true;
    authError.hidden = true;
    try {
      const response = await fetch(mode === "setup" ? "/auth/setup" : "/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!response.ok) throw new Error(await errorText(response));
      const payload = await response.json();
      authForm.reset();
      enterApp(payload.user);
    } catch (error) {
      authError.textContent = error.message;
      authError.hidden = false;
    } finally {
      submit.disabled = false;
    }
  });

  $("#create-user-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await requestJson("/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: $("#new-user-name").value.trim(),
          password: $("#new-user-password").value,
          role: $("#new-user-role").value,
        }),
      });
      event.target.reset();
      showToast("用户已创建");
      await loadUsers();
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  window.addEventListener("hashchange", () => setRoute(currentRouteFromHash(), { updateHash: false }));

  resizeComposer();
  bootstrapAuth();
})();
