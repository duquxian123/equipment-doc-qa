"""查询改写：把用户的口语问题换成文档里常见的术语，再拿去检索。

为什么需要:
    问「机器坏了怎么修」，文档写的是「设备」「故障」「诊断」「维修」，
    BM25 一个词都对不上，向量模型也没能把「坏了」映射到「故障」，
    结果四条候选全是保养章节，正确答案（第 7 章故障诊断与维修）根本没进候选。

代价:
    每句话多一次模型调用。改写失败时退回原句，不影响主流程。
"""

from __future__ import annotations

from llm import chat

REWRITE_SYSTEM = """你是检索查询改写器。把用户的口语问题改写成适合在企业设备、工艺、维修类文档中检索的短句。

要求：
1. 保留原意。不要回答问题，不要解释，不要补充资料。
2. 换成文档里常见的书面术语（例如把“机器坏了”写成“设备故障”）。
3. 只输出一行改写结果，不要引号、不要序号、不要任何说明。"""


def rewrite_query(query: str, env: dict[str, str]) -> str | None:
    """返回改写后的检索式；失败或没改出东西时返回 None。"""
    try:
        text = chat(REWRITE_SYSTEM, query, env, max_tokens=60)
    except Exception:  # 改写是增强项，出错就退回原句检索
        return None

    line = text.splitlines()[0].strip().strip("\"“”'")
    if not line or line == query.strip():
        return None
    return line
