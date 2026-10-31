"""按问题清单打印合并后的候选块，人工判断正确答案有没有被框进来。

    .venv\\Scripts\\python.exe src\\check_candidates.py

这里看的不是「排第几」，而是「在不在这堆候选里」。
因为后面的链路是模型从候选里挑，挑得出来就行，排第一不重要。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from candidates import CandidateMerger  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "eval" / "questions.txt"


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
    merger = CandidateMerger()
    for idx, q in enumerate(load_questions(), start=1):
        cands = merger.candidates(q)
        print(f"\n{idx}. {q}   候选 {len(cands)} 块")
        for c in cands:
            page = f"p{c['page_from']}" if c["page_from"] == c["page_to"] else f"p{c['page_from']}-{c['page_to']}"
            print(f"   {c['source']:<6} {page:<8} {c['section'][:64]}")


if __name__ == "__main__":
    main()
