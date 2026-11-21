"""LangGraph orchestration and public agent service."""

from __future__ import annotations

import time
import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph

from . import memory
from .models import AgentState
from .policy import detect_injection, safe_preview
from .providers import DeepSeekError, DeepSeekProvider, DemoProvider
from .tools import ToolPermissionError, ToolRegistry


REFUSAL_TEXT = "该请求包含绕过安全规则、读取敏感信息或执行未授权操作的意图，已被拦截。"
MAX_STEPS = 4


def _step(
    state: dict,
    kind: str,
    title: str,
    status: str,
    input_summary: str = "",
    output_summary: str = "",
    name: str | None = None,
    duration_ms: int = 0,
    error: str | None = None,
) -> list[dict]:
    steps = list(state.get("steps", []))
    steps.append(
        {
            "index": len(steps) + 1,
            "kind": kind,
            "name": name,
            "title": title,
            "status": status,
            "input_summary": input_summary,
            "output_summary": output_summary,
            "duration_ms": duration_ms,
            "error": error,
        }
    )
    return steps


class GraphRunner:
    def __init__(self, provider, registry: ToolRegistry) -> None:
        self.provider = provider
        self.registry = registry
        self.graph = self._build()

    def _guard(self, state: dict) -> dict:
        unsafe = detect_injection(state["query"])
        if unsafe:
            return {
                "status": "refused",
                "unsafe_reason": unsafe,
                "final_answer": REFUSAL_TEXT,
                "steps": _step(
                    state,
                    "guard",
                    "输入安全检查",
                    "refused",
                    input_summary=safe_preview(state["query"]),
                    output_summary=unsafe,
                ),
            }
        return {
            "status": "running",
            "llm_messages": self.provider.initial_messages(
                state["query"],
                state["memory_context"],
            ),
            "steps": _step(
                state,
                "guard",
                "输入与权限检查",
                "completed",
                input_summary=safe_preview(state["query"]),
                output_summary="输入通过安全检查，允许使用白名单工具。",
            ),
        }

    def _plan(self, state: dict) -> dict:
        started = time.perf_counter()
        turn = self.provider.plan(state)
        duration = int((time.perf_counter() - started) * 1000)
        return {
            "llm_messages": turn.messages,
            "draft_content": turn.assistant_content,
            "pending_tool_calls": turn.tool_calls,
            "iterations": int(state.get("iterations", 0)),
            "steps": _step(
                state,
                "plan",
                "模型规划",
                "completed",
                input_summary=f"工具结果 {len(state.get('tool_results', []))} 条",
                output_summary=turn.detail,
                duration_ms=duration,
            ),
        }

    def _execute(self, state: dict) -> dict:
        tool_results = list(state.get("tool_results", []))
        steps = list(state.get("steps", []))
        messages = list(state["llm_messages"])
        for call in state.get("pending_tool_calls", []):
            started = time.perf_counter()
            result: dict[str, Any]
            error: str | None = None
            ok = True
            try:
                result = self.registry.execute_with_retry(
                    call["name"],
                    call.get("arguments") or {},
                    state["user"],
                )
            except ToolPermissionError as exc:
                ok = False
                error = str(exc)
                result = {"summary": error, "data": {}, "citations": []}
            except Exception as exc:
                ok = False
                error = str(exc)
                result = {
                    "summary": "工具执行失败，已保留失败轨迹并允许后续降级。",
                    "data": {},
                    "citations": [],
                }
            duration = int((time.perf_counter() - started) * 1000)
            tool_results.append(
                {
                    "name": call["name"],
                    "ok": ok,
                    "result": result,
                    "error": error,
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": safe_preview(result, 5000),
                }
            )
            steps.append(
                {
                    "index": len(steps) + 1,
                    "kind": "tool",
                    "name": call["name"],
                    "title": (
                        "执行工具"
                        if ok
                        else "工具调用失败"
                    ),
                    "status": "completed" if ok else "failed",
                    "input_summary": safe_preview(call.get("arguments") or {}),
                    "output_summary": result.get("summary", ""),
                    "duration_ms": duration,
                    "error": error,
                }
            )
        return {
            "tool_results": tool_results,
            "llm_messages": messages,
            "pending_tool_calls": [],
            "iterations": int(state.get("iterations", 0)) + 1,
            "steps": steps,
        }

    def _finalize(self, state: dict) -> dict:
        if state.get("final_answer"):
            answer = state["final_answer"]
        elif state.get("pending_tool_calls") and not state.get("draft_content"):
            executed = [
                item["name"] for item in state.get("tool_results", []) if item.get("ok")
            ]
            answer = (
                "已达到 4 步工具循环上限，停止继续调用工具。"
                f"已成功执行：{'、'.join(executed) if executed else '无'}。"
                "请根据当前轨迹核对结果，或拆成更小的问题后重试。"
            )
        else:
            answer = self.provider.finish(state)
        citations: list[dict] = []
        seen: set[tuple] = set()
        for item in state.get("tool_results", []):
            for citation in item.get("result", {}).get("citations", []):
                key = (
                    citation.get("document"),
                    citation.get("page_start"),
                    citation.get("page_end"),
                    citation.get("section"),
                )
                if key not in seen:
                    seen.add(key)
                    citations.append(citation)
        tool_results = state.get("tool_results", [])
        has_tool_failure = any(not item.get("ok") for item in tool_results)
        has_tool_success = any(item.get("ok") for item in tool_results)
        permission_only_failure = has_tool_failure and not has_tool_success and any(
            "权限" in str(item.get("error") or "") for item in tool_results
        )
        refused = (
            state.get("status") == "refused"
            or permission_only_failure
            or answer.lstrip().startswith("资料中没有相关内容")
        )
        failed = has_tool_failure and not has_tool_success and not refused
        limitations = []
        if any(item["name"] == "query_ops_metrics" for item in tool_results):
            limitations.append("运营数据来自程序生成的模拟沙盘，不是真实企业数据。")
        if has_tool_failure:
            limitations.append("本轮至少有一个工具失败或权限不足，最终结论只能使用成功返回的数据。")
        limitations.append("当前为本地演示版本，不是生产级 Agent 平台。")
        return {
            "answer": answer,
            "status": "refused" if refused else ("failed" if failed else "completed"),
            "citations": citations,
            "limitations": limitations,
            "steps": _step(
                state,
                "answer",
                "生成最终答复",
                "refused" if refused else ("failed" if failed else "completed"),
                input_summary=f"工具结果 {len(state.get('tool_results', []))} 条",
                output_summary=safe_preview(answer, 260),
            ),
        }

    def _after_guard(self, state: dict) -> str:
        return "finalize" if state.get("status") == "refused" else "plan"

    def _after_plan(self, state: dict) -> str:
        if state.get("pending_tool_calls") and int(state.get("iterations", 0)) < MAX_STEPS:
            return "execute"
        return "finalize"

    def _build(self):
        builder = StateGraph(AgentState)
        builder.add_node("guard", self._guard)
        builder.add_node("plan", self._plan)
        builder.add_node("execute", self._execute)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "guard")
        builder.add_conditional_edges(
            "guard",
            self._after_guard,
            {"plan": "plan", "finalize": "finalize"},
        )
        builder.add_conditional_edges(
            "plan",
            self._after_plan,
            {"execute": "execute", "finalize": "finalize"},
        )
        builder.add_edge("execute", "plan")
        builder.add_edge("finalize", END)
        return builder.compile()

    def invoke(self, state: dict) -> dict:
        return self.graph.invoke(state, {"recursion_limit": 16})


class AgentService:
    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or ToolRegistry()

    def meta(self, user: dict | None = None) -> dict:
        permissions = set(user.get("permissions", [])) if user else None
        configured = True
        try:
            DeepSeekProvider(self.registry)
        except DeepSeekError:
            configured = False
        return {
            "default_mode": "auto",
            "live_model_configured": configured,
            "max_steps": MAX_STEPS,
            "tools": self.registry.specs(permissions),
            "memory": {
                "persistent": True,
                "recent_turns": 6,
                "scope": "当前用户、当前会话",
            },
        }

    def _provider(self, mode: str):
        if mode == "demo":
            return DemoProvider(self.registry)
        return DeepSeekProvider(self.registry)

    def _execute_graph(
        self,
        *,
        query: str,
        user: dict,
        session_id: str,
        memory_context: str,
        mode: str,
        run_id: str,
    ) -> dict:
        provider = self._provider(mode)
        runner = GraphRunner(provider, self.registry)
        state = {
            "run_id": run_id,
            "query": query,
            "user": user,
            "session_id": session_id,
            "memory_context": memory_context,
            "llm_messages": [],
            "step_count": 0,
            "steps": [],
            "tool_results": [],
            "pending_tool_calls": [],
            "iterations": 0,
            "draft_content": "",
            "status": "running",
            "answer": "",
            "citations": [],
            "limitations": [],
        }
        return runner.invoke(state)

    def run(
        self,
        query: str,
        user: dict,
        session_id: str | None = None,
        mode: str = "auto",
    ) -> dict:
        if mode not in {"auto", "live", "demo"}:
            raise ValueError("mode 必须是 auto、live 或 demo")
        started = time.perf_counter()
        session = memory.ensure_session(session_id, int(user["id"]))
        memory_context = memory.recent_context(session, int(user["id"]))
        run_id = uuid.uuid4().hex
        fallback_used = False
        fallback_reason: str | None = None
        actual_mode = "demo" if mode == "demo" else "live"

        try:
            state = self._execute_graph(
                query=query,
                user=user,
                session_id=session,
                memory_context=memory_context,
                mode=actual_mode,
                run_id=run_id,
            )
        except Exception as exc:
            state = {
                "status": "failed",
                "answer": "智能体执行失败，未生成结论。",
                "steps": [
                    {
                        "index": 1,
                        "kind": "runtime",
                        "name": None,
                        "title": "运行失败",
                        "status": "failed",
                        "input_summary": "",
                        "output_summary": "",
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                ],
                "citations": [],
                "limitations": ["运行失败，未形成可验证结论。"],
            }
            fallback_reason = f"{type(exc).__name__}: {exc}"

        if mode == "auto" and state.get("status") == "failed":
            fallback_used = True
            actual_mode = "demo"
            try:
                state = self._execute_graph(
                    query=query,
                    user=user,
                    session_id=session,
                    memory_context=memory_context,
                    mode="demo",
                    run_id=run_id,
                )
            except Exception as exc:
                fallback_reason = f"演示模式也失败：{type(exc).__name__}: {exc}"

        status = str(state.get("status") or "failed")
        answer = str(state.get("answer") or "智能体没有生成答案。")
        steps = list(state.get("steps", []))
        tools_used = [
            step["name"] for step in steps if step.get("kind") == "tool" and step.get("name")
        ]
        if status in {"completed", "refused"}:
            memory.add_turn(
                session,
                int(user["id"]),
                query,
                answer,
                actual_mode,
                status,
                tools_used,
            )
        return {
            "run_id": run_id,
            "session_id": session,
            "mode": actual_mode,
            "status": status,
            "answer": answer,
            "steps": steps,
            "citations": list(state.get("citations", [])),
            "limitations": list(state.get("limitations", [])),
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    def history(self, session_id: str, user: dict) -> dict:
        return memory.get_history(session_id, int(user["id"]))

    def clear(self, session_id: str, user: dict) -> None:
        memory.clear_session(session_id, int(user["id"]))
