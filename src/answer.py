"""生成答案：把合并后的候选块交给模型，要求只依据资料回答并标出处。

用法:
    .venv\\Scripts\\python.exe src\\answer.py "这个瓶子最多能装多少药"

设计要点:
    1. 资料里没有答案时必须说「资料中没有相关内容」，不许猜 —— 拒答靠提示词，
       不靠检索分数（实测分数区分不出来）。
    2. 每条结论后面带 ［文档名 第X页］，出处是这段话的唯一凭据。
    3. 温度设 0，同一问题每次答案稳定，便于对比不同版本的效果。
    4. 打招呼这类不需要查文档的话直接回固定话术，不检索、不调接口 ——
       否则会把几块无关内容塞给模型，答完再挂几行莫名其妙的出处。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from candidates import CandidateMerger  # noqa: E402
from llm import chat, load_env  # noqa: E402
from rewrite import rewrite_query  # noqa: E402

# 寒暄词表。必须「问题很短」且「命中词表」两条同时成立才走寒暄分支，
# 这样「你好，二级保养多久做一次」不会被误判。
SMALLTALK_WORDS = (
    "你好", "您好", "在吗", "谢谢", "多谢", "再见", "拜拜",
    "你是谁", "你能做什么", "你能干什么", "hello", "hi", "thanks",
)
PUNCT_RE = re.compile(r"[\s，。！？、,.!?~～…：:；;]+")
SMALLTALK_MAX_LEN = 6

SMALLTALK_REPLY = (
    "你好，我是设备与工艺文档的问答助手。可以问设备参数、维护保养、故障处理、"
    "档案管理这类问题，答案会标注出自哪份文档第几页；资料里没有的我会直说。"
)


def is_smalltalk(query: str) -> bool:
    q = PUNCT_RE.sub("", query).lower()
    return len(q) <= SMALLTALK_MAX_LEN and any(w in q for w in SMALLTALK_WORDS)

SYSTEM_PROMPT = """你是设备与工艺文档的问答助手。严格按下面五条回答：

1. 只能依据给定的【资料】回答，不得使用资料以外的任何知识。
2. 资料里有能回答问题的内容时，即使措辞和问题不完全一致，也要给出明确结论。不要因为用词不同就拒答。
3. 只有资料里确实没有相关信息时，才回答「资料中没有相关内容」，不要推测、不要补充常识。
4. 每条结论后面用［文档名 第X页］标注出处；资料里若标了条款号（如 8.2.4.3），一并写进出处。
5. 可以用资料里的数据做必要的计算或换算，但必须先列出原始数据，再把结果单独标成「（推算：由上面的数据计算得出，非原文）」。资料里没有给出的前提一律不得自行补充。"""


def build_context(cands: list[dict]) -> str:
    """把候选块拼成带编号的资料，编号便于模型引用。"""
    blocks = []
    for i, c in enumerate(cands, start=1):
        page = f"第{c['page_from']}页" if c["page_from"] == c["page_to"] else f"第{c['page_from']}-{c['page_to']}页"
        blocks.append(f"【资料{i}】{c['doc']} {page}\n{c['text']}")
    return "\n\n".join(blocks)


def ask(query: str, cands: list[dict], env: dict[str, str]) -> str:
    return chat(SYSTEM_PROMPT, f"{build_context(cands)}\n\n【问题】{query}", env)


def answer(query: str, merger: CandidateMerger | None = None, env: dict[str, str] | None = None):
    if is_smalltalk(query):
        return SMALLTALK_REPLY, []

    env = env or load_env()
    merger = merger or CandidateMerger()
    alt = rewrite_query(query, env)
    cands = merger.candidates(query, alt_query=alt)
    return ask(query, cands, env), cands


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]).strip()
    if not q:
        raise SystemExit('用法: python src/answer.py "你的问题"')

    text, cands = answer(q)
    print(f"问题：{q}\n")
    print(text)
    print("\n候选来源：")
    for c in cands:
        print(f"  {c['source']:<6} 第{c['page_from']}页  {c['section'][:60]}")
