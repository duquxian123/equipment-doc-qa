"""Allow-listed tools used by the agent."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import ValidationError

import ops as ops_service
from candidates import CandidateMerger

from .models import (
    CalculateMetricInput,
    CreateTicketDryRunInput,
    OpsMetricsInput,
    SearchManualInput,
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    permission: str
    input_model: type
    handler: Callable[[Any], dict]
    writes: bool = False


class ToolError(RuntimeError):
    pass


class ToolPermissionError(RuntimeError):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._engine: CandidateMerger | None = None
        self._engine_lock = threading.Lock()
        self._tools = {
            spec.name: spec
            for spec in (
                ToolSpec(
                    name="search_manual",
                    description="检索设备手册、维护规范和故障处理资料，返回文档、页码与原文片段。",
                    permission="chat.use",
                    input_model=SearchManualInput,
                    handler=self._search_manual,
                ),
                ToolSpec(
                    name="query_ops_metrics",
                    description="查询模拟生产运营汇总，包括产量、良率、废品、成本、产线和缺陷分布。",
                    permission="ops.view",
                    input_model=OpsMetricsInput,
                    handler=self._query_ops_metrics,
                ),
                ToolSpec(
                    name="calculate_metric",
                    description="按白名单公式计算百分比变化、比例或良率；不会执行任意表达式。",
                    permission="chat.use",
                    input_model=CalculateMetricInput,
                    handler=self._calculate_metric,
                ),
                ToolSpec(
                    name="create_ticket_dry_run",
                    description="演示维修工单创建前的权限和确认流程；只返回待确认提示，不写入任何数据。",
                    permission="ops.manage",
                    input_model=CreateTicketDryRunInput,
                    handler=self._create_ticket_dry_run,
                ),
            )
        }

    def _get_engine(self) -> CandidateMerger:
        if self._engine is None:
            with self._engine_lock:
                if self._engine is None:
                    self._engine = CandidateMerger()
        return self._engine

    def _search_manual(self, payload: SearchManualInput) -> dict:
        candidates = self._get_engine().candidates(payload.query, per_side=3)
        def pdf_url(document: str) -> str:
            identifier = hashlib.sha256(f"{document}.pdf".encode("utf-8")).hexdigest()[:12]
            return f"/documents/{identifier}/file"

        matches = [
            {
                "document": str(item["doc"]),
                "section": str(item["section"]),
                "page_start": int(item["page_from"]),
                "page_end": int(item["page_to"]),
                "channel": str(item["source"]),
                "score": round(float(item["score"]), 4),
                "quote": str(item["text"])[:500],
                "pdf_url": pdf_url(str(item["doc"])),
            }
            for item in candidates[: payload.top_k]
        ]
        citations = [
            {
                "document": item["document"],
                "page_start": item["page_start"],
                "page_end": item["page_end"],
                "section": item["section"],
                "quote": item["quote"][:260],
                "pdf_url": item["pdf_url"],
            }
            for item in matches
        ]
        return {
            "summary": f"检索到 {len(matches)} 个文档候选。",
            "data": {"matches": matches},
            "citations": citations,
        }

    def _query_ops_metrics(self, payload: OpsMetricsInput) -> dict:
        source = ops_service.dashboard()
        data = {
            "focus": payload.focus,
            "order_counts": source["order_counts"],
            "production": source["production"],
            "inventory": source["inventory"],
            "defect_distribution": source["defect_distribution"],
            "line_performance": source["line_performance"],
            "disclaimer": source["disclaimer"],
        }
        return {
            "summary": (
                f"读取模拟运营数据：综合良率 {data['production']['yield_percent']}%，"
                f"合格产量 {data['production']['qualified_tons']} 吨。"
            ),
            "data": data,
            "citations": [],
        }

    def _calculate_metric(self, payload: CalculateMetricInput) -> dict:
        if payload.operation == "percent_change":
            value = (payload.left - payload.right) / payload.right * 100
            formula = "(left - right) / right × 100"
        elif payload.operation == "ratio_percent":
            value = payload.left / payload.right * 100
            formula = "left / right × 100"
        else:
            value = payload.left / payload.right * 100
            formula = "qualified / input × 100"
        result = round(value, 4)
        return {
            "summary": f"{payload.label}计算结果为 {result}%。",
            "data": {
                "label": payload.label,
                "operation": payload.operation,
                "left": payload.left,
                "right": payload.right,
                "formula": formula,
                "result_percent": result,
            },
            "citations": [],
        }

    def _create_ticket_dry_run(self, payload: CreateTicketDryRunInput) -> dict:
        return {
            "summary": "已生成维修工单预览，等待人工确认；没有写入任何业务数据。",
            "data": {
                "requires_confirmation": True,
                "would_create": {
                    "title": payload.title,
                    "description": payload.description,
                },
                "written": False,
            },
            "citations": [],
        }

    def specs(self, user_permissions: set[str] | None = None) -> list[dict]:
        output = []
        for spec in self._tools.values():
            output.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "permission": spec.permission,
                    "writes": False,
                    "available": user_permissions is None or spec.permission in user_permissions,
                }
            )
        return output

    def schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.input_model.model_json_schema(),
                },
            }
            for spec in self._tools.values()
        ]

    def execute(self, name: str, arguments: dict[str, Any], user: dict) -> dict:
        spec = self._tools.get(name)
        if not spec:
            raise ToolError(f"工具 {name} 不在白名单中")
        permissions = set(user.get("permissions", []))
        if spec.permission not in permissions:
            raise ToolPermissionError(f"当前角色缺少 {spec.permission} 权限")
        try:
            payload = spec.input_model.model_validate(arguments)
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            raise ToolError(f"工具参数不合法：{first.get('msg', 'validation error')}") from exc
        try:
            return spec.handler(payload)
        except (ToolError, ToolPermissionError):
            raise
        except Exception as exc:
            raise ToolError(f"{type(exc).__name__}: {exc}") from exc

    def execute_with_retry(
        self,
        name: str,
        arguments: dict[str, Any],
        user: dict,
        retries: int = 1,
    ) -> dict:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return self.execute(name, arguments, user)
            except ToolPermissionError:
                raise
            except ToolError as exc:
                last_error = exc
                if attempt >= retries:
                    raise
        raise last_error or ToolError("工具执行失败")
