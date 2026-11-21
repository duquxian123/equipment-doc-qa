const { createApp } = Vue;

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (response.status === 401) {
    window.location.href = "/";
    throw new Error("请先登录");
  }
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : "服务请求失败";
    throw new Error(detail);
  }
  return payload;
}

createApp({
  data() {
    return {
      query: "对比当前运营合格产量和废品量，并结合设备手册分析主要原因。",
      mode: "auto",
      running: false,
      result: null,
      errorMessage: "",
      sessionId: localStorage.getItem("j05_agent_session") || null,
      meta: {
        max_steps: 4,
        tools: [],
        memory: {},
        live_model_configured: false,
      },
      history: { turns: [] },
      examples: [
        "对比当前运营合格产量和废品量，并结合设备手册分析主要原因。",
        "查询设备维护要求和故障处理规范。",
        "帮我创建一个维修工单，先展示待确认内容。",
        "公司2027年内部预算和员工薪酬明细是多少？",
      ],
      modeOptions: [
        { value: "auto", label: "自动" },
        { value: "live", label: "真实模型" },
        { value: "demo", label: "演示模式" },
      ],
    };
  },
  computed: {
    serviceStatus() {
      if (this.running) return "running";
      if (this.result?.status === "failed") return "failed";
      return "ready";
    },
    serviceStatusText() {
      if (this.running) return "执行中";
      if (this.result?.status === "failed") return "需要检查";
      return this.meta.live_model_configured ? "模型已配置" : "演示可用";
    },
    actualModeLabel() {
      return this.modeLabel(this.result?.mode || (this.meta.live_model_configured ? "live" : "demo"));
    },
    modeDescription() {
      return {
        auto: this.meta.live_model_configured
          ? "优先调用 DeepSeek；接口失败时自动降级，并明确提示。"
          : "当前未检测到可用模型配置，自动模式会使用演示规划器。",
        live: "使用 DeepSeek 原生 Function Calling，需联网和有效密钥。",
        demo: "使用固定规则选择工具，便于离线演示和自动测试。",
      }[this.mode];
    },
    availableToolCount() {
      return (this.meta.tools || []).filter((item) => item.available).length;
    },
    successStepCount() {
      return (this.result?.steps || []).filter((item) => item.status !== "failed" && item.status !== "refused").length;
    },
    resultTitle() {
      if (!this.result) return "";
      if (this.result.status === "refused") return "安全边界或资料范围触发了拒绝";
      if (this.result.status === "failed") return "本轮没有形成可靠结论";
      return "任务已完成，过程和依据均可核对";
    },
  },
  async mounted() {
    try {
      this.meta = await requestJson("/agent/meta");
      await this.loadHistory();
    } catch (error) {
      this.errorMessage = error.message;
    }
  },
  methods: {
    async runExample(example) {
      this.query = example;
      await this.runAgent();
    },
    async runAgent() {
      if (!this.query.trim() || this.running) return;
      this.running = true;
      this.errorMessage = "";
      try {
        const payload = await requestJson("/agent/run", {
          method: "POST",
          body: JSON.stringify({
            query: this.query.trim(),
            session_id: this.sessionId,
            mode: this.mode,
          }),
        });
        this.result = payload;
        this.sessionId = payload.session_id;
        localStorage.setItem("j05_agent_session", payload.session_id);
        await this.loadHistory();
      } catch (error) {
        this.errorMessage = error.message;
      } finally {
        this.running = false;
      }
    },
    async loadHistory() {
      if (!this.sessionId) {
        this.history = { turns: [] };
        return;
      }
      try {
        this.history = await requestJson(`/agent/sessions/${encodeURIComponent(this.sessionId)}`);
      } catch (error) {
        if (error.message.includes("不存在") || error.message.includes("不属于")) {
          localStorage.removeItem("j05_agent_session");
          this.sessionId = null;
          this.history = { turns: [] };
          return;
        }
        throw error;
      }
    },
    async clearSession() {
      if (!this.sessionId) return;
      try {
        await requestJson(`/agent/sessions/${encodeURIComponent(this.sessionId)}`, {
          method: "DELETE",
        });
      } finally {
        localStorage.removeItem("j05_agent_session");
        this.sessionId = null;
        this.history = { turns: [] };
        this.result = null;
      }
    },
    statusLabel(status) {
      return { completed: "已完成", refused: "已拒绝", failed: "失败" }[status] || status;
    },
    modeLabel(mode) {
      return { live: "真实模型", demo: "演示模式", auto: "自动" }[mode] || mode;
    },
    toolLabel(name) {
      return {
        search_manual: "文档检索",
        query_ops_metrics: "运营指标",
        calculate_metric: "指标计算",
        create_ticket_dry_run: "工单预览",
      }[name] || name;
    },
    shortSession(value) {
      return String(value || "").slice(0, 8);
    },
  },
}).mount("#app");
