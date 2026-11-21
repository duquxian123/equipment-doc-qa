"""Pydantic models used by the agent tools and graph."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchManualInput(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=5)


class OpsMetricsInput(StrictModel):
    focus: str = Field(
        default="生产、质量、成本与库存",
        min_length=1,
        max_length=200,
        description="用户关心的运营指标；查询始终返回一组安全的白名单汇总。",
    )


class CalculateMetricInput(StrictModel):
    operation: Literal["percent_change", "ratio_percent", "yield_percent"]
    left: float = Field(ge=0, le=1_000_000_000)
    right: float = Field(gt=0, le=1_000_000_000)
    label: str = Field(default="指标", min_length=1, max_length=80)


class CreateTicketDryRunInput(StrictModel):
    title: str = Field(min_length=2, max_length=80)
    description: str = Field(min_length=2, max_length=500)


class AgentState(TypedDict, total=False):
    run_id: str
    query: str
    user: dict[str, Any]
    session_id: str
    memory_context: str
    llm_messages: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    pending_tool_calls: list[dict[str, Any]]
    iterations: int
    draft_content: str
    status: str
    answer: str
    final_answer: str
    citations: list[dict[str, Any]]
    limitations: list[str]
    unsafe_reason: str

