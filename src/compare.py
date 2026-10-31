"""同一批问题，三条链路并列：关键词 / 向量 / 两路融合。

用法:
    .venv\\Scripts\\python.exe src\\compare.py

产出 output/compare.md。改完解析或切分后重跑这个脚本，就是一次回归测试。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hybrid import HybridRetriever  # noqa: E402
from retrieve import BM25, load_chunks  # noqa: E402
from vector_retriever import VectorRetriever  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "eval" / "questions.txt"
OUT = ROOT / "output" / "compare.md"
TOP_K = 3


def load_questions() -> list[str]:
    out = []
    for raw in QUESTIONS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.lstrip("0123456789.、) ").strip()
        if line:
            out.append(line)
    return out


def brief(doc: dict) -> str:
    page = (
        f"第{doc['page_from']}页"
        if doc["page_from"] == doc["page_to"]
        else f"第{doc['page_from']}-{doc['page_to']}页"
    )
    return f"{page} {doc['section']}"


def main() -> None:
    questions = load_questions()
    chunks = load_chunks()
    engines = [
        ("BM25", BM25(chunks)),
        ("向量", VectorRetriever()),
        ("融合", HybridRetriever()),
    ]

    lines = ["# 三条链路对比", "", f"每边取前 {TOP_K} 条", ""]

    for idx, q in enumerate(questions, start=1):
        print(f"\n{idx}. {q}")
        lines.append(f"## {idx}. {q}")
        lines.append("")

        for name, engine in engines:
            hits = engine.search(q, TOP_K)
            top = f"{hits[0][0]:.3f}  {brief(hits[0][1])}" if hits else "无命中"
            print(f"   {name:<4} {top[:76]}")

            lines.append(f"**{name}**")
            lines.append("")
            if not hits:
                lines.append("- 无命中")
            for rank, (score, doc) in enumerate(hits, start=1):
                lines.append(f"{rank}. {score:.3f} · {doc['id']} · {brief(doc)}")
            lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n对比表：{OUT}")


if __name__ == "__main__":
    main()
