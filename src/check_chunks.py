"""生成知识块后的自检：把可以自动判定的问题一次性列出来。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rows = [json.loads(line) for line in (ROOT / "output" / "chunks" / "chunks.jsonl").open(encoding="utf-8")]

print("总块数", len(rows))
print("含页标记残留:", sum("=== page" in r["text"] for r in rows))
print("含 HTML 标签:", sum("<" in r["text"] for r in rows))
print("空 section   :", sum(not r["section"] for r in rows))
print("页码缺失     :", sum(r["page_from"] is None for r in rows))

bins = Counter((r["chars"] // 100) * 100 for r in rows)
print("块大小分布 :", dict(sorted(bins.items())))

print("\n--- 各文档块数 ---")
for doc, n in Counter(r["doc"] for r in rows).items():
    print(f"  {n:>4}  {doc}")

print("\n--- 抽查：汽车零部件 首块 ---")
for r in rows:
    if r["doc"].startswith("T-CMEEEA_汽车"):
        print(r["text"][:260])
        break

print("\n--- 抽查：切割设备 术语块 ---")
for r in rows:
    if r["doc"].startswith("T-GXDSL") and "术语" in r["section"]:
        print("section:", r["section"])
        print(r["text"][:160])
        break

print("\n--- 抽查：一个表格块 ---")
for r in rows:
    if r["kind"] == "table" and "技术参数" in r["text"]:
        print("section:", r["section"], "| 页", r["page_from"], "-", r["page_to"])
        print(r["text"][:280])
        break
