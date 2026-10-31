"""把评估清单里的问题全部跑一遍生成，结果写成人工核对表。

    .venv\\Scripts\\python.exe src\\answer_all.py
    → output/answers.md
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from answer import answer, load_env  # noqa: E402
from candidates import CandidateMerger  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "eval" / "questions.txt"
OUT = ROOT / "output" / "answers.md"


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


def main() -> None:
    env = load_env()
    merger = CandidateMerger()
    questions = load_questions()

    lines = [
        "# 生成结果核对表",
        "",
        f"- 模型：{env.get('DEEPSEEK_MODEL', 'deepseek-chat')}，温度 0",
        f"- 候选：原句与改写句各检索 BM25/向量前三，去重后交给模型",
        f"- 问题数：{len(questions)}",
        "- 用法：在每条「判定」后填 对 / 错，错的要注明是检索没给到，还是模型没挑对",
        "",
    ]

    for idx, q in enumerate(questions, start=1):
        started = time.time()
        text, cands = answer(q, merger, env)
        cost = time.time() - started

        print(f"[{idx}] {q}  ({cost:.1f}s)")
        print(f"     {text[:150].replace(chr(10), ' ')}")

        lines.append(f"## {idx}. {q}")
        lines.append("")
        lines.append(f"判定：____　（{cost:.1f}s）")
        lines.append("")
        lines.append(text)
        lines.append("")
        lines.append("<details><summary>候选块</summary>")
        lines.append("")
        for c in cands:
            lines.append(f"- `{c['source']}` 第{c['page_from']}页 · {c['section']}")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n核对表：{OUT}")


if __name__ == "__main__":
    main()
