"""把每个知识块转成向量并落盘。

产出:
    output/index/vectors.npy     归一化后的向量矩阵，一行一个块
    output/index/index_meta.json 用的什么模型、多少块、每块的 id

模型缓存在项目内 .cache/fastembed，不写用户目录。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

ROOT = Path(__file__).resolve().parents[1]
CHUNKS = ROOT / "output" / "chunks" / "chunks.jsonl"
OUT = ROOT / "output" / "index"
CACHE = ROOT / ".cache" / "fastembed"
MODEL = "BAAI/bge-small-zh-v1.5"


def main() -> None:
    docs = [json.loads(line) for line in CHUNKS.open(encoding="utf-8")]
    print(f"载入 {len(docs)} 个块，模型 {MODEL}")

    model = TextEmbedding(model_name=MODEL, cache_dir=str(CACHE))
    started = time.time()
    vectors = np.array(list(model.embed([d["text"] for d in docs], batch_size=32)), dtype=np.float32)

    # 归一化后，点积就等于余弦相似度，检索时省一次除法
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    OUT.mkdir(parents=True, exist_ok=True)
    np.save(OUT / "vectors.npy", vectors)
    (OUT / "index_meta.json").write_text(
        json.dumps(
            {
                "model": MODEL,
                "count": len(docs),
                "dim": int(vectors.shape[1]),
                "ids": [d["id"] for d in docs],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"完成：{vectors.shape[0]} 块 × {vectors.shape[1]} 维，耗时 {time.time() - started:.1f}s")
    print(f"落盘：{OUT / 'vectors.npy'}")


if __name__ == "__main__":
    main()
