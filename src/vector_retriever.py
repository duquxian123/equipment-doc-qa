"""向量检索：接口和 BM25 完全一致，外面调用方不用改。

    Retriever.search(问题, top_k) -> [(分数, 块), ...]

向量是预先算好的（见 build_vector_index.py），这里只把问题转成向量再比对。
239 个块的全量比对就是一次矩阵乘法，不需要向量数据库。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CHUNKS = ROOT / "output" / "chunks" / "chunks.jsonl"
INDEX = ROOT / "output" / "index"
CACHE = ROOT / ".cache" / "fastembed"


class VectorRetriever:
    def __init__(self) -> None:
        meta = json.loads((INDEX / "index_meta.json").read_text(encoding="utf-8"))
        self.model_name = meta["model"]
        self.vectors = np.load(INDEX / "vectors.npy")
        self.docs = [json.loads(line) for line in CHUNKS.open(encoding="utf-8")]

        ids = [d["id"] for d in self.docs]
        if ids != meta["ids"]:
            raise SystemExit("索引和当前的块对不上，先重跑 src/build_vector_index.py")

        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name=self.model_name, cache_dir=str(CACHE))

    def search(self, query: str, top_k: int = 5) -> list[tuple[float, dict]]:
        q = np.array(list(self.model.query_embed([query]))[0], dtype=np.float32)
        q /= np.linalg.norm(q)
        scores = self.vectors @ q
        order = np.argsort(-scores)[:top_k]
        return [(float(scores[i]), self.docs[i]) for i in order]
