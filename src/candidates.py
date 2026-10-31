"""候选合并：不走排名融合，两路各取前几条，去重后一起交给模型判断。

为什么不融合排名:
    实测等权 RRF 把「只有向量找得到」的题目反而排到了后面（5/8，比单路都差）。
    RRF 奖励「两路都提到」，而这类语料里正确答案常常只有一路找得到。
    与其硬算一个排名，不如把两路的候选都给模型，让它自己挑。

    代价是模型要多读几块，成本略高；换来的是不丢候选。

改写语句的候选也一并合并：
    口语提问常常一个词都对不上文档（「机器坏了」vs「设备故障」），
    所以原句和改写句各检索一遍，取并集，宁可多给几块。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retrieve import BM25, load_chunks  # noqa: E402
from vector_retriever import VectorRetriever  # noqa: E402

PER_SIDE = 3  # 每一路取几条
MAX_CANDIDATES = 8  # 交给模型的候选上限，两句检索乘起来不能无限多


class CandidateMerger:
    def __init__(self) -> None:
        self.chunks = load_chunks()
        self.bm25 = BM25(self.chunks)
        self.vector = VectorRetriever()

    def candidates(self, query: str, per_side: int = PER_SIDE, alt_query: str | None = None) -> list[dict]:
        """原句与改写句各检索一遍，交替取块、去重，并标注来源与排名。"""
        sides: list[tuple[str, list]] = []
        for label, q in (("", query), ("改写", alt_query)):
            if q and q.strip():
                sides.append((f"bm25{label}", self.bm25.search(q, per_side)))
                sides.append((f"向量{label}", self.vector.search(q, per_side)))

        out: list[dict] = []
        seen: set[str] = set()
        for rank in range(per_side):
            for name, hits in sides:
                if rank >= len(hits):
                    continue
                score, doc = hits[rank]
                if doc["id"] in seen:
                    continue
                seen.add(doc["id"])
                out.append({**doc, "source": name, "rank": rank + 1, "score": float(score)})
                if len(out) >= MAX_CANDIDATES:
                    return out
        return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--rewrite"]
    query = " ".join(args).strip()
    if not query:
        raise SystemExit('用法: python src/candidates.py [--rewrite] "你的问题"')

    alt = None
    if "--rewrite" in sys.argv[1:]:
        from llm import load_env
        from rewrite import rewrite_query

        alt = rewrite_query(query, load_env())
        print(f"原问题：{query}")
        print(f"改写后：{alt or '（没有改写，退回原句）'}\n")

    for i, c in enumerate(CandidateMerger().candidates(query, alt_query=alt), start=1):
        print(f"[{i}] {c['source']} #{c['rank']}  {c['id']}  第{c['page_from']}页")
        print(f"    {c['section']}")
