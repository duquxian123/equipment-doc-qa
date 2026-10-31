"""检索：先用关键词（BM25）把链路跑通，语义检索后面再加。

设计上刻意解耦:
    Retriever.search(问题, top_k) -> [命中块]
    现在内部是 BM25；将来换成向量检索，只改这个类，外面调用方不动。

中文没有用分词词典，而是按字符二元组切（"灭火剂" -> 灭火/火剂）。
原因: 免去一个分词依赖，对短问句的召回和分词效果接近；
如果以后发现个别词被切坏，换成 jieba 只需改 tokenize 这一处。

用法:
    .venv\\Scripts\\python.exe src\\retrieve.py "这个瓶子最多能装多少药"
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHUNKS = ROOT / "output" / "chunks" / "chunks.jsonl"

K1 = 1.5  # 词频饱和系数
B = 0.75  # 长度归一化强度


def tokenize(text: str) -> list[str]:
    """英文数字按词切，中文按字符二元组切。"""
    text = text.lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


class BM25:
    """最朴素的关键词检索基线，只依赖标准库。"""

    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs
        self.tokens = [tokenize(d["text"]) for d in docs]
        self.lengths = [len(t) for t in self.tokens]
        self.avg_len = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0

        self.tfs: list[Counter[str]] = [Counter(t) for t in self.tokens]
        df: Counter[str] = Counter()
        for tf in self.tfs:
            df.update(tf.keys())
        self.df = df
        self.n = len(docs)

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log((self.n - df + 0.5) / (df + 0.5) + 1)

    def search(self, query: str, top_k: int = 5) -> list[tuple[float, dict]]:
        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        scored: list[tuple[float, int]] = []
        for i, tf in enumerate(self.tfs):
            score = 0.0
            dl = self.lengths[i]
            for term in q_tokens:
                f = tf.get(term)
                if not f:
                    continue
                score += self.idf(term) * (f * (K1 + 1)) / (
                    f + K1 * (1 - B + B * dl / self.avg_len)
                )
            if score > 0:
                scored.append((score, i))

        scored.sort(reverse=True)
        return [(s, self.docs[i]) for s, i in scored[:top_k]]


def load_chunks() -> list[dict]:
    if not CHUNKS.exists():
        raise SystemExit(f"缺少 {CHUNKS}，先跑 src/chunk.py")
    with CHUNKS.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def main() -> None:
    query = " ".join(sys.argv[1:]).strip()
    if not query:
        raise SystemExit('用法: python src/retrieve.py "你的问题"')

    retriever = BM25(load_chunks())
    hits = retriever.search(query, top_k=5)

    print(f"问题: {query}\n命中 {len(hits)} 块\n")
    for rank, (score, doc) in enumerate(hits, start=1):
        page = (
            f"第{doc['page_from']}页"
            if doc["page_from"] == doc["page_to"]
            else f"第{doc['page_from']}-{doc['page_to']}页"
        )
        print(f"[{rank}] {score:.2f}  {doc['id']}  {page}  {doc['kind']}")
        print(f"    {doc['section']}")
        print(f"    {doc['text'][:120].replace(chr(10), ' ')}")
        print()


if __name__ == "__main__":
    main()
