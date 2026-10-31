"""命令行问答入口。

    .venv\\Scripts\\python.exe src\\cli.py                 交互模式，一行一个问题
    .venv\\Scripts\\python.exe src\\cli.py "设备多久保养一次"   单次提问
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from answer import answer  # noqa: E402


def show(query: str) -> None:
    text, cands = answer(query)
    print(f"\n{text}\n")

    # 只有答案里真的引用了资料才列出出处，避免寒暄或拒答时挂一堆无意义的来源
    if not cands or "［" not in text:
        return

    print("出处：")
    for c in cands:
        page = f"第{c['page_from']}页" if c["page_from"] == c["page_to"] else f"第{c['page_from']}-{c['page_to']}页"
        print(f"  · {c['doc']} {page}（{c['source']}）")


def main() -> None:
    query = " ".join(sys.argv[1:]).strip()
    if query:
        show(query)
        return

    print("输入问题，回车提问，输入 q 退出。\n")
    while True:
        try:
            query = input("问> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if query.lower() in {"q", "quit", "exit"}:
            break
        if query:
            show(query)


if __name__ == "__main__":
    main()
