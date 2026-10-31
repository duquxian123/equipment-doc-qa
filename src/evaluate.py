"""跑评估：把 eval/questions.txt 里的问题逐条检索，生成人工核对表。

用法:
    .venv\\Scripts\\python.exe src\\evaluate.py

产出 output/eval_report.md，每条问题下列出前 5 个命中块，
人工在后面标 对 / 错。标错的那些，就是下一步要调的地方。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retrieve import BM25, load_chunks  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "eval" / "questions.txt"
REPORT = ROOT / "output" / "eval_report.md"
TOP_K = 5


def load_questions() -> list[str]:
    if not QUESTIONS.exists():
        raise SystemExit(f"缺少问题清单：{QUESTIONS}")
    lines = []
    for raw in QUESTIONS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.lstrip("0123456789.、) ").strip()
        if line:
            lines.append(line)
    return lines


def main() -> None:
    questions = load_questions()
    if not questions:
        raise SystemExit("问题清单是空的")

    retriever = BM25(load_chunks())

    out: list[str] = [
        "# 检索效果核对表",
        "",
        f"- 检索方式：BM25（关键词基线），每问取前 {TOP_K} 块",
        f"- 问题数：{len(questions)}",
        "- 用法：在每条问题的「判定」后填 对 / 错；填错的那几条就是要调的",
        "",
    ]

    miss = 0
    for idx, q in enumerate(questions, start=1):
        hits = retriever.search(q, top_k=TOP_K)
        if not hits:
            miss += 1

        out.append(f"## {idx}. {q}")
        out.append("")
        out.append("判定：____")
        out.append("")
        if not hits:
            out.append("> 没有命中任何块")
            out.append("")
            continue

        for rank, (score, doc) in enumerate(hits, start=1):
            page = (
                f"第{doc['page_from']}页"
                if doc["page_from"] == doc["page_to"]
                else f"第{doc['page_from']}-{doc['page_to']}页"
            )
            preview = doc["text"][:100].replace("\n", " ")
            out.append(f"{rank}. **{score:.2f}** · {doc['id']} · {page} · {doc['kind']}")
            out.append(f"   - {doc['section']}")
            out.append(f"   - {preview}")
        out.append("")

        print(f"[{idx}] {q}  命中 {len(hits)}，首条 {hits[0][1]['id'] if hits else '无'}")

    REPORT.write_text("\n".join(out), encoding="utf-8")
    print(f"\n共 {len(questions)} 题，{miss} 题完全没命中")
    print(f"核对表：{REPORT}")


if __name__ == "__main__":
    main()
