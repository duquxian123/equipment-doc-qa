(() => {
  "use strict";

  const state = {
    dashboard: null,
    orders: [],
    selectedId: null,
    canManage: false,
    loading: false,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const kpis = $("#ops-kpis");
  const orderList = $("#ops-order-list");
  const orderSummary = $("#ops-order-summary");
  const refreshTime = $("#ops-refresh-time");
  const detailEmpty = $("#ops-detail-empty");
  const detailContent = $("#ops-detail-content");
  const createForm = $("#ops-create-form");
  const permissionNote = $("#ops-create-permission-note");

  const statusLabels = {
    planned: "待下发",
    dispatched: "生产中",
    completed: "已完成",
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatMoney(value) {
    return new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency: "CNY",
      maximumFractionDigits: 0,
    }).format(Number(value || 0));
  }

  function formatNumber(value, digits = 1) {
    return new Intl.NumberFormat("zh-CN", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(Number(value || 0));
  }

  function formatTime(value) {
    if (!value) return "-";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "-";
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function showToast(message, type = "success") {
    const toast = $("#toast");
    if (!toast) return;
    window.clearTimeout(showToast.timer);
    toast.textContent = message;
    toast.classList.toggle("is-error", type === "error");
    toast.hidden = false;
    showToast.timer = window.setTimeout(() => {
      toast.hidden = true;
    }, 4200);
  }

  async function errorText(response) {
    try {
      const payload = await response.json();
      return typeof payload.detail === "string"
        ? payload.detail
        : payload.detail?.message || `请求失败（${response.status}）`;
    } catch {
      return `请求失败（${response.status}）`;
    }
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error(await errorText(response));
    return response.json();
  }

  function statusBadge(value) {
    const label = statusLabels[value] || value;
    return `<span class="ops-status is-${escapeHtml(value)}">${escapeHtml(label)}</span>`;
  }

  function renderKpis(data) {
    const production = data.production;
    const inventory = data.inventory;
    const counts = data.order_counts;
    kpis.innerHTML = [
      ["模拟订单", `${counts.total} 笔`, `${counts.completed} 笔已完成`],
      ["合格产出", `${formatNumber(production.qualified_tons, 2)} 吨`, `投入 ${formatNumber(production.input_tons, 2)} 吨`],
      ["综合良率", `${formatNumber(production.yield_percent, 2)}%`, `废品 ${formatNumber(production.scrap_tons, 2)} 吨`],
      ["平均单位成本", formatMoney(production.avg_unit_cost), "按合格吨折算"],
      ["成品库存", `${formatNumber(inventory.finished_goods_tons, 2)} 吨`, `待处理废品 ${formatNumber(inventory.scrap_tons, 2)} 吨`],
      ["成本偏差", formatMoney(production.cost_variance), "实际成本 - 计划成本"],
    ].map(([label, value, detail]) => `
      <article class="ops-kpi">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
        <small>${escapeHtml(detail)}</small>
      </article>
    `).join("");
  }

  function renderOrders() {
    orderList.replaceChildren();
    if (!state.orders.length) {
      orderList.innerHTML = '<tr><td class="ops-empty" colspan="7">暂无订单</td></tr>';
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const order of state.orders) {
      const row = document.createElement("tr");
      row.classList.toggle("is-selected", order.id === state.selectedId);

      const orderCell = document.createElement("td");
      const orderMain = document.createElement("div");
      orderMain.className = "ops-order-main";
      const orderId = document.createElement("strong");
      orderId.textContent = order.id;
      const orderDate = document.createElement("small");
      orderDate.textContent = formatTime(order.created_at);
      orderMain.append(orderId, orderDate);
      orderCell.append(orderMain);

      const customerCell = document.createElement("td");
      const customerMain = document.createElement("div");
      customerMain.className = "ops-order-main";
      const customer = document.createElement("strong");
      customer.textContent = order.customer;
      const spec = document.createElement("small");
      spec.textContent = `${order.alloy} · ${formatNumber(order.thickness_mm, 2)} mm`;
      customerMain.append(customer, spec);
      customerCell.append(customerMain);

      const quantity = document.createElement("td");
      quantity.textContent = `${formatNumber(order.quantity_tons, 1)} 吨`;
      const status = document.createElement("td");
      status.innerHTML = statusBadge(order.status);
      const line = document.createElement("td");
      line.textContent = order.line || "待分配";
      const quality = document.createElement("td");
      quality.innerHTML = order.quality_result === "failed"
        ? `<span class="ops-status is-failed">${escapeHtml(order.defect_type || "不合格")}</span>`
        : order.quality_result === "passed"
          ? '<span class="ops-status is-completed">合格</span>'
          : "-";
      const action = document.createElement("td");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ops-link-button";
      button.textContent = order.id === state.selectedId ? "已选择" : "查看";
      button.addEventListener("click", () => selectOrder(order.id));
      action.append(button);
      row.append(orderCell, customerCell, quantity, status, line, quality, action);
      fragment.append(row);
    }
    orderList.append(fragment);
  }

  function metric(label, value) {
    return `<div class="ops-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
  }

  function renderDetail(order) {
    detailEmpty.hidden = true;
    detailContent.hidden = false;
    const isPlanned = order.status === "planned";
    const isDispatched = order.status === "dispatched";
    const isCompleted = order.status === "completed";
    const qualityLabel = order.quality_result === "passed"
      ? "合格"
      : order.quality_result === "failed"
        ? `不合格 · ${order.defect_type || "未分类"}`
        : "待质检";

    const dispatchPanel = isPlanned && state.canManage
      ? `
        <form class="ops-action-bar" id="ops-dispatch-form">
          <label class="ops-field">
            <span>分配到 MES 产线</span>
            <select id="ops-dispatch-line">
              <option value="1#冷轧线">1#冷轧线</option>
              <option value="2#冷轧线">2#冷轧线</option>
              <option value="精整线">精整线</option>
            </select>
          </label>
          <button class="primary-button" type="submit">下发生产工单</button>
        </form>
      `
      : isPlanned
        ? '<p class="ops-permission-note">当前角色只能查看，不能下发工单。</p>'
        : "";

    const qualityOptions = [
      '<option value="passed">合格</option>',
      '<option value="failed">不合格</option>',
    ].join("");
    const defectOptions = [
      ["厚度超差", "厚度超差"],
      ["表面划伤", "表面划伤"],
      ["板形不良", "板形不良"],
      ["孔洞", "孔洞"],
    ].map(([value, label]) => `<option value="${value}">${label}</option>`).join("");

    const completePanel = isDispatched && state.canManage
      ? `
        <form class="ops-complete-form" id="ops-complete-form">
          <h3>MES 生产与质检回报</h3>
          <div class="ops-form-grid">
            <label class="ops-field">
              <span>投入重量 kg</span>
              <input id="ops-input-weight" type="number" min="1" step="0.1" value="${(Number(order.quantity_tons) * 1050).toFixed(1)}" required>
            </label>
            <label class="ops-field">
              <span>合格产出 kg</span>
              <input id="ops-qualified-weight" type="number" min="0" step="0.1" value="${(Number(order.quantity_tons) * 1000).toFixed(1)}" required>
            </label>
            <label class="ops-field">
              <span>废品重量 kg</span>
              <input id="ops-scrap-weight" type="number" min="0" step="0.1" value="${(Number(order.quantity_tons) * 20).toFixed(1)}" required>
            </label>
            <label class="ops-field">
              <span>停机分钟</span>
              <input id="ops-downtime" type="number" min="0" step="1" value="30" required>
            </label>
            <label class="ops-field">
              <span>能耗 kWh</span>
              <input id="ops-energy" type="number" min="0" step="0.1" value="${(Number(order.quantity_tons) * 430).toFixed(1)}" required>
            </label>
            <label class="ops-field">
              <span>班次</span>
              <select id="ops-shift">
                <option value="早班">早班</option>
                <option value="中班">中班</option>
                <option value="夜班">夜班</option>
              </select>
            </label>
            <label class="ops-field">
              <span>质检结论</span>
              <select id="ops-quality-result">${qualityOptions}</select>
            </label>
            <label class="ops-field" id="ops-defect-field" hidden>
              <span>缺陷类型</span>
              <select id="ops-defect-type">${defectOptions}</select>
            </label>
            <label class="ops-field">
              <span>操作员</span>
              <input id="ops-operator" value="模拟操作员" maxlength="40" required>
            </label>
            <label class="ops-field">
              <span>质检员</span>
              <input id="ops-inspector" value="模拟质检员" maxlength="40" required>
            </label>
            <label class="ops-field">
              <span>备注</span>
              <input id="ops-report-notes" maxlength="500" placeholder="可选">
            </label>
          </div>
          <div class="ops-form-actions">
            <p class="ops-form-note">提交后自动计算良率、成本并更新成品与废品库存。</p>
            <button class="primary-button" type="submit">提交生产与质检结果</button>
          </div>
        </form>
      `
      : isDispatched
        ? '<p class="ops-permission-note">当前角色只能查看，不能提交报工。</p>'
        : "";

    const completedMetrics = isCompleted
      ? `
        <div class="ops-detail-grid">
          ${metric("投入重量", `${formatNumber(order.input_weight_kg, 1)} kg`)}
          ${metric("合格产出", `${formatNumber(order.qualified_weight_kg, 1)} kg`)}
          ${metric("良率", `${formatNumber(order.yield_percent, 2)}%`)}
          ${metric("质检结论", qualityLabel)}
          ${metric("材料成本", formatMoney(order.material_cost))}
          ${metric("能耗成本", formatMoney(order.energy_cost))}
          ${metric("停机损失", formatMoney(order.downtime_cost))}
          ${metric("总成本", formatMoney(order.total_cost))}
          ${metric("吨成本", formatMoney(order.unit_cost_per_ton))}
          ${metric("计划成本", formatMoney(order.planned_cost))}
          ${metric("成本偏差", formatMoney(order.cost_variance))}
          ${metric("库存动作", `${formatNumber(Number(order.qualified_weight_kg) / 1000, 2)} 吨入库`)}
        </div>
      `
      : "";

    const sopButton = isCompleted && order.defect_type
      ? `<button class="text-button ops-sop-button" type="button" data-sop-defect="${escapeHtml(order.defect_type)}">查询“${escapeHtml(order.defect_type)}”处理规范</button>`
      : "";

    detailContent.innerHTML = `
      <div class="ops-detail-head">
        <div>
          <span class="section-kicker">订单 ${escapeHtml(order.id)}</span>
          <h2>${escapeHtml(order.customer)}</h2>
          <p>${escapeHtml(order.alloy)} · ${formatNumber(order.thickness_mm, 2)} mm · ${formatNumber(order.quantity_tons, 1)} 吨 · 交期 ${escapeHtml(order.due_date)}</p>
        </div>
        ${statusBadge(order.status)}
      </div>
      <div class="ops-flow">
        <div class="ops-flow-node is-complete">
          <strong>1. ERP 建单</strong>
          <span>计划成本 ${formatMoney(order.planned_cost)}</span>
        </div>
        <span class="ops-flow-arrow" aria-hidden="true">→</span>
        <div class="ops-flow-node ${order.work_order_id ? "is-complete" : ""}">
          <strong>2. MES 生产</strong>
          <span>${order.work_order_id ? `${escapeHtml(order.work_order_id)} · ${escapeHtml(order.line || "待产线")}` : "等待下发"}</span>
        </div>
        <span class="ops-flow-arrow" aria-hidden="true">→</span>
        <div class="ops-flow-node ${isCompleted ? "is-complete" : ""}">
          <strong>3. ERP 结算</strong>
          <span>${isCompleted ? "生产、质量、成本已回传" : "等待生产回报"}</span>
        </div>
      </div>
      ${dispatchPanel}
      ${completePanel}
      ${completedMetrics}
      ${sopButton}
    `;

    $("#ops-dispatch-form")?.addEventListener("submit", dispatchSelectedOrder);
    const completeForm = $("#ops-complete-form");
    completeForm?.addEventListener("submit", completeSelectedWorkOrder);
    const qualitySelect = $("#ops-quality-result");
    qualitySelect?.addEventListener("change", () => {
      $("#ops-defect-field").hidden = qualitySelect.value !== "failed";
    });
    $$("[data-sop-defect]", detailContent).forEach((button) => {
      button.addEventListener("click", () => openSopQuestion(button.dataset.sopDefect));
    });
  }

  async function selectOrder(orderId) {
    try {
      state.selectedId = orderId;
      renderOrders();
      const order = await requestJson(`/ops/orders/${encodeURIComponent(orderId)}`, {
        cache: "no-store",
      });
      renderDetail(order);
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function dispatchSelectedOrder(event) {
    event.preventDefault();
    if (!state.selectedId) return;
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const order = await requestJson(
        `/ops/orders/${encodeURIComponent(state.selectedId)}/dispatch`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ line: $("#ops-dispatch-line").value }),
        },
      );
      showToast("MES 生产工单已生成");
      await load();
      renderDetail(order);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function completeSelectedWorkOrder(event) {
    event.preventDefault();
    const order = state.selectedOrder;
    if (!order?.work_order_id) return;
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    const qualityResult = $("#ops-quality-result").value;
    try {
      const completed = await requestJson(
        `/ops/work-orders/${encodeURIComponent(order.work_order_id)}/complete`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            input_weight_kg: Number($("#ops-input-weight").value),
            qualified_weight_kg: Number($("#ops-qualified-weight").value),
            scrap_weight_kg: Number($("#ops-scrap-weight").value),
            downtime_minutes: Number($("#ops-downtime").value),
            energy_kwh: Number($("#ops-energy").value),
            shift: $("#ops-shift").value,
            quality_result: qualityResult,
            defect_type: qualityResult === "failed" ? $("#ops-defect-type").value : null,
            inspector: $("#ops-inspector").value.trim(),
            operator: $("#ops-operator").value.trim(),
            notes: $("#ops-report-notes").value.trim(),
          }),
        },
      );
      showToast("生产、质检、成本和库存已同步");
      await load();
      renderDetail(completed);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function createOrder(event) {
    event.preventDefault();
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const order = await requestJson("/ops/orders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          customer: $("#ops-customer").value.trim(),
          alloy: $("#ops-alloy").value,
          thickness_mm: Number($("#ops-thickness").value),
          quantity_tons: Number($("#ops-quantity").value),
          due_date: $("#ops-due-date").value,
          priority: $("#ops-priority").value,
        }),
      });
      state.selectedId = order.id;
      showToast("模拟销售订单已创建");
      await load();
      await selectOrder(order.id);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  function renderAnalysis(data) {
    const lineList = $("#ops-line-list");
    lineList.innerHTML = data.line_performance.map((line) => `
      <article class="ops-line-card">
        <strong>${escapeHtml(line.line)}</strong>
        <span>${formatNumber(line.output_tons, 2)} 吨 · ${line.order_count} 单</span>
        <b>${formatNumber(line.yield_percent, 2)}%</b>
        <span>平均停机 ${formatNumber(line.avg_downtime_minutes, 1)} 分钟</span>
      </article>
    `).join("");

    const maxOutput = Math.max(...data.production_trend.map((item) => item.output_tons), 1);
    $("#ops-trend").innerHTML = data.production_trend.map((item) => `
      <div class="ops-trend-row">
        <span>${escapeHtml(item.production_date.slice(5))}</span>
        <div class="ops-trend-track">
          <div class="ops-trend-fill" style="width:${Math.max(3, item.output_tons / maxOutput * 100)}%"></div>
        </div>
        <strong>${formatNumber(item.output_tons, 1)}t</strong>
      </div>
    `).join("");

    const maxDefects = Math.max(...data.defect_distribution.map((item) => item.order_count), 1);
    $("#ops-defect-list").innerHTML = data.defect_distribution.length
      ? data.defect_distribution.map((item) => `
        <div class="ops-defect-row">
          <strong>${escapeHtml(item.defect_type)}</strong>
          <div class="ops-defect-track">
            <div class="ops-defect-fill" style="width:${Math.max(6, item.order_count / maxDefects * 100)}%"></div>
          </div>
          <span>${item.order_count} 单</span>
        </div>
      `).join("")
      : '<p class="ops-empty">暂无不合格质检记录</p>';
  }

  async function load() {
    if (state.loading) return;
    state.loading = true;
    try {
      const [dashboard, ordersPayload] = await Promise.all([
        requestJson("/ops/dashboard", { cache: "no-store" }),
        requestJson("/ops/orders?limit=100", { cache: "no-store" }),
      ]);
      state.dashboard = dashboard;
      state.orders = ordersPayload.orders;
      if (!state.selectedId && state.orders.length) state.selectedId = state.orders[0].id;
      state.selectedOrder = state.orders.find((item) => item.id === state.selectedId) || null;

      renderKpis(dashboard);
      orderSummary.textContent = `${dashboard.order_counts.total} 笔订单 · ${dashboard.order_counts.planned} 笔待下发 · ${dashboard.order_counts.dispatched} 笔生产中`;
      refreshTime.textContent = `更新于 ${formatTime(dashboard.generated_at)}`;
      renderOrders();
      renderAnalysis(dashboard);

      if (state.selectedId) {
        const order = await requestJson(`/ops/orders/${encodeURIComponent(state.selectedId)}`, {
          cache: "no-store",
        });
        state.selectedOrder = order;
        renderDetail(order);
      }
    } catch (error) {
      kpis.innerHTML = `<div class="ops-kpi is-loading">无法读取生产运营数据：${escapeHtml(error.message)}</div>`;
      showToast(error.message, "error");
    } finally {
      state.loading = false;
    }
  }

  function openSopQuestion(defectType = "厚度超差") {
    window.location.hash = "#/chat";
    window.setTimeout(() => {
      const input = $("#question-input");
      if (!input) return;
      input.value = `出现${defectType}时，应检查哪些设备、工艺参数和维护记录？`;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    }, 30);
  }

  function applyPermissions(canManage) {
    state.canManage = canManage;
    createForm.hidden = !canManage;
    permissionNote.hidden = canManage;
  }

  function setDefaultDueDate() {
    const due = new Date();
    due.setDate(due.getDate() + 7);
    const year = due.getFullYear();
    const month = String(due.getMonth() + 1).padStart(2, "0");
    const day = String(due.getDate()).padStart(2, "0");
    $("#ops-due-date").value = `${year}-${month}-${day}`;
  }

  createForm.addEventListener("submit", createOrder);
  $("#ops-reload-button").addEventListener("click", load);
  $("#ops-open-sop").addEventListener("click", () => openSopQuestion("厚度超差"));
  document.addEventListener("workspace:user", (event) => {
    const permissions = event.detail?.user?.permissions || [];
    applyPermissions(permissions.includes("ops.manage"));
  });

  setDefaultDueDate();
  applyPermissions(Boolean(window.currentWorkspaceUser?.permissions?.includes("ops.manage")));
  window.ManufacturingOps = { load };
})();
