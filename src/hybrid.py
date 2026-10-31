"""混合检索：把关键词和语义两路的结果按排名融合。

为什么按排名融合而不是分数相加:
    BM25 的分数是 0~48 的开方量级，向量是 0.47~0.77 的余弦值，量纲完全不同，
    直接相加等于让 BM25 单方面说了算。RRF 只看排名，两边贡献对等。

    RRF 分 = Σ 1 / (60 + 该路排名)，60 是经验常数，作用是压平头几名的差距。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retrieve import BM25, load_chunks  # noqa: E402
from vector_retriever import VectorRetriever  # noqa: E402

RRF_K = 60
CANDIDATES = 20  # 每一路先多取一些：某路排第 12 的块，可能是另一路的第一名


class HybridRetriever:
    def __init__(self) -> None:
        self.chunks = load_chunks()
        self.bm25 = BM25(self.chunks)
        self.vector = VectorRetriever()

    def search(self, query: str, top_k: int = 5) -> list[tuple[float, dict]]:
        by_id = {d["id"]: d for d in self.chunks}
        fused: dict[str, float] = {}

        for retriever in (self.bm25, self.vector):
            for rank, (_, doc) in enumerate(retriever.search(query, CANDIDATES), start=1):
                fused[doc["id"]] = fused.get(doc["id"], 0.0) + 1.0 / (RRF_K + rank)

        ranked = sorted(fused.items(), key=lambda kv: -kv[1])[:top_k]
        return [(score, by_id[cid]) for cid, score in ranked]


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]).strip()
    if not query:
        raise SystemExit('用法: python src/hybrid.py "你的问题"')
    for rank, (score, doc) in enumerate(HybridRetriever().search(query, 5), start=1):
        print(f"[{rank}] {score:.4f}  {doc['id']}  第{doc['page_from']}页")
        print(f"    {doc['section']}")
