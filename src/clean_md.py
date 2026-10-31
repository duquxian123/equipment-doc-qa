"""清洗 Markdown 正文：去掉页眉、页码、目录页。

输入: output/parsed-md/*.md     （由 parse_pdf.py 生成，含 === page N === 标记）
输出: output/parsed-clean/*.md  （保留页标记，出处页码靠它）
      output/clean_report.json  （记录了删了什么，便于人工核对）

两条规则都是确定性的，不涉及模型:
    1. 某个短行在超过一半的页面里重复出现 -> 判为页眉
    2. 一页里出现 4 行以上「标题 + 点线 + 页码」-> 判为目录页，整页丢弃
    3. 页面首尾三行内的纯数字/罗马数字 -> 判为页码
    4. 空行压缩成一个，并在标题与表格前后补上 Markdown 结构必需的空行
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN_DIR = ROOT / "output" / "parsed-md"
OUT_DIR = ROOT / "output" / "parsed-clean"

PAGE_RE = re.compile(r"^=== page (\d+) ===$")
PAGE_NO_RE = re.compile(r"^[\dIVXivx]{1,4}$")
TOC_RE = re.compile(r"(\.{3,}|…{2,})")
TOC_AREA = 3  # 页面首尾各保留多少行不清洗


def split_pages(md: str) -> list[tuple[int, list[str]]]:
    """把带页标记的 md 拆成 [(页码, 该页的所有行), ...]。"""
    pages: list[tuple[int, list[str]]] = []
    current_no: int | None = None
    current: list[str] = []

    for line in md.splitlines():
        m = PAGE_RE.match(line.strip())
        if m:
            if current_no is not None:
                pages.append((current_no, current))
            current_no = int(m.group(1))
            current = []
        elif current_no is not None:
            current.append(line)

    if current_no is not None:
        pages.append((current_no, current))
    return pages


def find_headers(pages: list[tuple[int, list[str]]]) -> set[str]:
    """在多页重复出现的短行判定为页眉。"""
    counter: Counter[str] = Counter()
    for _, lines in pages:
        seen = set()
        for line in lines:
            norm = re.sub(r"\s+", " ", line.strip())
            # 表格行、标题行、目录行不参与页眉判定
            if not norm or norm.startswith(("|", "#", "-", "*")) or TOC_RE.search(norm):
                continue
            if 6 <= len(norm) <= 40:
                seen.add(norm)
        counter.update(seen)

    threshold = math.ceil(len(pages) / 2)
    return {line for line, n in counter.items() if n >= threshold}


def is_toc_page(lines: list[str]) -> bool:
    """有 4 行以上「标题 + 点线 + 页码」的页面判定为目录页。"""
    hits = 0
    for line in lines:
        norm = line.strip()
        if TOC_RE.search(norm) and re.search(r"[\dIVX]+\s*\|?\s*$", norm):
            hits += 1
    return hits >= 4


def structural_blanks(lines: list[str]) -> list[str]:
    """补齐 Markdown 结构所需的空行：标题前、表格前后各留一个空行。

    只补结构需要的，不还原原来的排版空行；目的有二:
        一是让人直接打开 md 也能看懂；
        二是避免将来渲染或按空行分块时，标题和表格被并进上一段。
    """
    out: list[str] = []
    for line in lines:
        if not out:
            out.append(line)
            continue
        prev = out[-1]
        need_blank = prev != "" and (
            re.match(r"^#{1,6}\s", line) is not None  # 标题前
            or (line.startswith("|") and not prev.startswith("|"))  # 表格开始
        )
        if need_blank:
            out.append("")
        out.append(line)

    result: list[str] = []
    for i, line in enumerate(out):
        result.append(line)
        nxt = out[i + 1] if i + 1 < len(out) else None
        if line.startswith("|") and nxt is not None and nxt and not nxt.startswith("|"):
            result.append("")  # 表格结束后补空行
    return result


def clean_page(lines: list[str], headers: set[str]) -> tuple[list[str], dict]:
    """清洗单页，返回清洗后的行与统计。空行压缩成一个，不整体删除。"""
    stats = {"header": 0, "page_no": 0, "blank": 0}
    kept: list[str] = []

    non_empty_idx = [i for i, line in enumerate(lines) if line.strip()]
    edge = set(non_empty_idx[:TOC_AREA]) | set(non_empty_idx[-TOC_AREA:])

    for i, line in enumerate(lines):
        norm = re.sub(r"\s+", " ", line.strip())

        if not norm:
            stats["blank"] += 1
            if kept and kept[-1] != "":
                kept.append("")  # 连续空行只保留一个
            continue
        if norm in headers:
            stats["header"] += 1
            continue
        if i in edge and PAGE_NO_RE.match(norm):
            stats["page_no"] += 1
            continue

        kept.append(line.rstrip())

    kept = structural_blanks(kept)
    while kept and kept[0] == "":
        kept.pop(0)
    while kept and kept[-1] == "":
        kept.pop()

    return kept, stats


def process(path: Path) -> dict:
    md = path.read_text(encoding="utf-8")
    pages = split_pages(md)
    headers = find_headers(pages)

    out_pages: list[str] = []
    dropped_toc: list[int] = []
    removed = Counter()

    for page_no, lines in pages:
        if is_toc_page(lines):
            dropped_toc.append(page_no)
            continue
        kept, stats = clean_page(lines, headers)
        removed.update(stats)
        if kept:
            out_pages.append(f"=== page {page_no} ===\n" + "\n".join(kept))

    cleaned = "\n\n".join(out_pages)
    (OUT_DIR / f"{path.stem}.md").write_text(cleaned, encoding="utf-8")

    return {
        "file": path.stem,
        "pages_total": len(pages),
        "pages_kept": len(out_pages),
        "toc_pages_dropped": dropped_toc,
        "headers_removed": sorted(headers),
        "lines_removed_header": removed["header"],
        "lines_removed_page_no": removed["page_no"],
        "chars_before": len(md),
        "chars_after": len(cleaned),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(IN_DIR.glob("*.md"))
    if not files:
        raise SystemExit(f"没有输入文件：{IN_DIR}（先跑 parse_pdf.py）")

    report = []
    for path in files:
        r = process(path)
        report.append(r)
        print(
            f"{r['file']}\n"
            f"    页 {r['pages_total']} -> 保留 {r['pages_kept']}，"
            f"剔除目录页 {r['toc_pages_dropped'] or '无'}\n"
            f"    删除页眉 {r['lines_removed_header']} 行 / 页码 {r['lines_removed_page_no']} 行，"
            f"字符 {r['chars_before']} -> {r['chars_after']}\n"
            f"    判定的页眉: {r['headers_removed']}"
        )

    (ROOT / "output" / "clean_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n合计 {len(report)} 份，清洗后 {sum(r['chars_after'] for r in report)} 字符")


if __name__ == "__main__":
    main()
