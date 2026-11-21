"""Input and tool safety checks for the local agent."""

from __future__ import annotations

import json
import re
from typing import Any


INJECTION_PATTERNS = (
    re.compile(r"忽略.{0,16}(之前|以上|系统).{0,16}(指令|要求|规则)"),
    re.compile(r"(输出|泄露|显示|告诉我).{0,20}(系统提示|系统指令|api[\s_-]*key|密钥|密码)"),
    re.compile(r"(删除|清空).{0,16}(全部|所有).{0,16}(数据|会话|日志)"),
    re.compile(r"执行.{0,16}(任意|系统).{0,16}(命令|代码)"),
    re.compile(r"ignore.{0,24}(previous|system).{0,24}(instruction|prompt)"),
    re.compile(r"(reveal|print|show).{0,24}(system prompt|api key|secret)"),
)

UNSUPPORTED_TERMS = (
    "公司2027年内部预算",
    "员工薪酬明细",
    "未公开的并购计划",
)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def detect_injection(query: str) -> str | None:
    text = normalize_text(query)
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return "请求试图绕过系统规则、读取密钥或执行未授权操作。"
    return None


def has_known_no_answer(query: str) -> bool:
    text = normalize_text(query)
    return any(term in text for term in UNSUPPORTED_TERMS)


def safe_preview(value: Any, limit: int = 220) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
