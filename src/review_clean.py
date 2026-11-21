"""带审核信息的知识清洗。

与命令行使用的 clean_md.py 不同，这里会保留每一行原始文本、稳定行号和
删除原因，供 Web 审核页恢复误删内容或手动排除噪声行。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass

import clean_md


@dataclass(frozen=True)
class CleanOptions:
    header_ratio: float = 0.5
    header_min_len: int = 6
    header_max_len: int = 40
    toc_min_items: int = 4
    edge_lines: int = 3


CLEAN_PRESETS: dict[str, CleanOptions] = {
    "conservative": CleanOptions(
        header_ratio=0.8,
        header_min_len=8,
        header_max_len=50,
        toc_min_items=6,
        edge_lines=2,
    ),
    "standard": CleanOptions(),
    "aggressive": CleanOptions(
        header_ratio=0.35,
        header_min_len=6,
        header_max_len=60,
        toc_min_items=3,
        edge_lines=4,
    ),
}


def preset_options(preset: str) -> tuple[str, CleanOptions]:
    name = preset if preset in CLEAN_PRESETS else "standard"
    return name, CLEAN_PRESETS[name]


def _find_headers(
    pages: list[tuple[int, list[str]]],
    options: CleanOptions,
) -> set[str]:
    counter: Counter[str] = Counter()
    for _, lines in pages:
        seen: set[str] = set()
        for line in lines:
            norm = re.sub(r"\s+", " ", line.strip())
            if not norm or norm.startswith(("|", "#", "-", "*")) or clean_md.TOC_RE.search(norm):
                continue
            if options.header_min_len <= len(norm) <= options.header_max_len:
                seen.add(norm)
        counter.update(seen)

    threshold = max(2, math.ceil(len(pages) * options.header_ratio))
    return {line for line, count in counter.items() if count >= threshold}


def _is_toc_page(lines: list[str], options: CleanOptions) -> bool:
    hits = 0
    for line in lines:
        norm = line.strip()
        if clean_md.TOC_RE.search(norm) and re.search(r"[\dIVX]+\s*\|?\s*$", norm):
            hits += 1
    return hits >= options.toc_min_items


def clean_document(
    markdown: str,
    *,
    preset: str = "standard",
    restore_keys: set[str] | None = None,
    remove_keys: set[str] | None = None,
    excluded_pages: set[int] | None = None,
) -> dict:
    preset, options = preset_options(preset)
    restore_keys = restore_keys or set()
    remove_keys = remove_keys or set()
    excluded_pages = excluded_pages or set()

    pages = clean_md.split_pages(markdown)
    headers = _find_headers(pages, options)
    toc_pages: list[int] = []
    page_reviews: list[dict] = []
    cleaned_pages: list[str] = []
    removed_items: list[dict] = []
    removed_counts = Counter()

    for page_no, raw_lines in pages:
        toc_page = _is_toc_page(raw_lines, options)
        if toc_page:
            toc_pages.append(page_no)
        page_excluded = page_no in excluded_pages
        non_empty = [index for index, line in enumerate(raw_lines) if line.strip()]
        edge = set(non_empty[: options.edge_lines]) | set(non_empty[-options.edge_lines :])
        kept_lines: list[str] = []
        line_reviews: list[dict] = []

        for line_index, raw in enumerate(raw_lines):
            text = raw.rstrip()
            norm = re.sub(r"\s+", " ", text.strip())
            if not norm:
                continue

            key = f"{page_no}:{line_index}"
            reason: str | None = None
            if page_excluded:
                reason = "excluded_page"
            elif toc_page:
                reason = "toc_page"
            elif key in restore_keys:
                reason = None
            elif key in remove_keys:
                reason = "manual"
            elif norm in headers:
                reason = "header"
            elif line_index in edge and clean_md.PAGE_NO_RE.match(norm):
                reason = "page_number"

            state = "removed" if reason else "kept"
            line_reviews.append(
                {
                    "key": key,
                    "text": text,
                    "state": state,
                    "reason": reason,
                }
            )
            if reason:
                removed_counts[reason] += 1
                removed_items.append(
                    {
                        "key": key,
                        "page": page_no,
                        "text": text,
                        "reason": reason,
                    }
                )
            else:
                kept_lines.append(text)

        kept_lines = clean_md.structural_blanks(kept_lines)
        while kept_lines and kept_lines[0] == "":
            kept_lines.pop(0)
        while kept_lines and kept_lines[-1] == "":
            kept_lines.pop()
        if kept_lines:
            cleaned_pages.append(f"=== page {page_no} ===\n" + "\n".join(kept_lines))

        page_reviews.append(
            {
                "page": page_no,
                "excluded": page_excluded,
                "toc_page": toc_page,
                "lines": line_reviews,
                "kept_lines": sum(1 for line in line_reviews if line["state"] == "kept"),
                "removed_lines": sum(1 for line in line_reviews if line["state"] == "removed"),
            }
        )

    return {
        "markdown": "\n\n".join(cleaned_pages),
        "preset": preset,
        "options": asdict(options),
        "headers": sorted(headers),
        "toc_pages": toc_pages,
        "excluded_pages": sorted(excluded_pages),
        "restore_keys": sorted(restore_keys),
        "remove_keys": sorted(remove_keys),
        "pages": page_reviews,
        "removed_items": removed_items,
        "removed_counts": dict(removed_counts),
    }
