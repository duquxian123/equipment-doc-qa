"""把清洗后的 Markdown 切成知识块。

输入: output/parsed-clean/*.md （含 === page N === 页标记）
输出: output/chunks/chunks.jsonl 每行一个知识块，供后续向量化
      output/chunks/<文档名>.md  便于人工翻看每块是怎么切的

切分策略（结构优先 + 长度兜底）:
    1. 遇到标题就开新块，块里保留自己的标题
    2. 普通正文累积到 400 字上限，超了就在行边界断开
    3. 表格单独成块，不参与切分；超长表格按行切并重复表头
    4. 每块开头标注「文档名 / 章节路径 / 起止页」，这是出处的来源
    5. 不做重叠 —— 结构边界本身就是语义边界
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN_DIR = ROOT / "output" / "parsed-clean"
OUT_DIR = ROOT / "output" / "chunks"

MAX_CHARS = 400       # 正文块上限
TABLE_MAX_CHARS = 800  # 表格超过这个长度就按行拆开
MIN_CHARS = 40        # 正文块低于这个字数视为封面/页脚残渣，丢弃

PAGE_RE = re.compile(r"^=== page (\d+) ===$")
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# 标准文档里有一半章节标题被渲染成列表项（"- 4 通用要求"），不是 "#" 标题。
# 判定条件: 以编号开头 + 短 + 不像句子，满足才当标题，避免把条款正文误判。
LIST_HEAD_RE = re.compile(r"^[-*]\s*(\d+(?:\.\d+)*)\s+(\S.*)$")
STD_NO_RE = re.compile(r"^[A-Z]{1,6}(/[A-Z]+)?\s*\S*\d")
MAX_TITLE = 20


def plain(text: str) -> str:
    """去掉 Markdown 与 HTML 装饰，只留可读文字。"""
    text = re.sub(r"<[^>]+>", "", text)      # <u> <mark> <sup> 这类标签
    text = text.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def is_std_number(text: str) -> bool:
    """标准号（T/CMEEEA XXXX-2026、GB 50263）不是标题，是没清干净的页眉。"""
    return bool(STD_NO_RE.match(text))


def title_of(text: str) -> tuple[int, str] | None:
    """判断一行是不是标题，返回 (层级, 标题文字)；不是标题返回 None。

    两种来源:
        "# 8 标识、可追溯性" -> 层级由编号点分段数决定，保证和列表项版本一致
        "- 8.1 产品标识"     -> 同上，但要求短且不像句子
    """
    m = HEAD_RE.match(text)
    if m:
        title = plain(m.group(2))
        if not title or is_std_number(title):
            return None
        num = re.match(r"^(\d+(?:\.\d+)*)\s", title)
        if num:
            return 2 + num.group(1).count(".") + 1, title
        return len(m.group(1)), title

    m = LIST_HEAD_RE.match(text)
    if m:
        num, rest = m.group(1), plain(m.group(2))
        title = f"{num} {rest}"
        if (
            len(rest) <= MAX_TITLE
            and not rest.endswith(("。", "；", "：", "，", "、"))
            and not is_std_number(title)
        ):
            return 2 + num.count(".") + 1, title
    return None


def clean_tags(text: str) -> str:
    """表格里的 HTML 标签去掉，只留文字（<sup>3</sup> -> 3）。"""
    text = re.sub(r"</?(sup|u|mark|span|div|p|em|strong)>", "", text)
    return text.replace("<br>", " ").replace("<br/>", " ")


def split_pages(md: str) -> list[tuple[int, list[str]]]:
    pages: list[tuple[int, list[str]]] = []
    no: int | None = None
    body: list[str] = []
    for line in md.splitlines():
        m = PAGE_RE.match(line.strip())
        if m:
            if no is not None:
                pages.append((no, body))
            no, body = int(m.group(1)), []
        elif no is not None:
            body.append(line)
    if no is not None:
        pages.append((no, body))
    return pages


def split_table(rows: list[str], caption: str, section: str, page_from: int, page_to: int) -> list[str]:
    """超长表格按行拆开，每段都重复表头和表名。"""
    text = "\n".join(rows)
    if len(text) <= TABLE_MAX_CHARS or len(rows) <= 2:
        return [text]

    head = rows[:2]  # 表头 + 分隔线
    body = rows[2:]
    parts: list[str] = []
    buf: list[str] = []
    for row in body:
        buf.append(row)
        if len("\n".join(head + buf)) >= MAX_CHARS:
            parts.append("\n".join(head + buf))
            buf = []
    if buf:
        parts.append("\n".join(head + buf))

    return parts


def chunk_doc(path: Path) -> list[dict]:
    doc = path.stem
    pages = split_pages(path.read_text(encoding="utf-8"))

    chunks: list[dict] = []
    stack: list[tuple[int, str]] = []  # (标题层级, 标题文字)

    buf: list[str] = []
    buf_len = 0
    page_from: int | None = None
    page_to: int | None = None

    def section() -> str:
        return " / ".join(t for _, t in stack)

    def flush(kind: str = "text", body: str | None = None) -> None:
        nonlocal buf, buf_len, page_from, page_to
        text = body if body is not None else "\n".join(buf).strip()
        if not text:
            buf, buf_len, page_from, page_to = [], 0, None, None
            return
        chunks.append(
            {
                "doc": doc,
                "section": section(),
                "page_from": page_from,
                "page_to": page_to if page_to is not None else page_from,
                "kind": kind,
                "text": text,
            }
        )
        buf, buf_len, page_from, page_to = [], 0, None, None

    for page_no, lines in pages:
        i = 0
        while i < len(lines):
            raw = lines[i]
            line = raw.rstrip()
            norm = plain(line)

            if not norm:
                i += 1
                continue

            # 1. 标题：开新块，并把标题压进标题栈
            heading = title_of(line.strip())
            if heading:
                level, title = heading
                flush()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                # 同一个标题在相邻层级重复出现时不叠加，只把层级改过来
                if stack and stack[-1][1] == title:
                    stack[-1] = (level, title)
                else:
                    stack.append((level, title))
                buf = [title]
                buf_len = len(title)
                page_from = page_to = page_no
                i += 1
                continue

            # 2. 表格：整块单独成块
            if line.strip().startswith("|"):
                # 表名通常紧贴在表格前一行，把它从正文块里摘出来，跟着表格走
                caption = ""
                if buf and "表" in buf[-1] and len(buf[-1]) <= 50:
                    caption = buf.pop().strip()
                    buf_len -= len(caption)

                flush()

                rows: list[str] = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    rows.append(lines[i].rstrip())
                    i += 1

                if not caption:
                    for back in range(i - len(rows) - 1, -1, -1):
                        prev = plain(lines[back])
                        if prev:
                            if "表" in prev and len(prev) <= 50:
                                caption = prev
                            break

                parts = split_table(rows, caption, section(), page_no, page_no)
                for part in parts:
                    page_from = page_to = page_no
                    body = f"{caption}\n{part}".strip() if caption else part
                    flush("table", clean_tags(body))
                continue

            # 3. 普通正文：够长了就先断开
            if buf_len and buf_len + len(norm) > MAX_CHARS:
                flush()
            if page_from is None:
                page_from = page_no
            page_to = page_no
            buf.append(norm)
            buf_len += len(norm)
            i += 1

    flush()
    return chunks


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(IN_DIR.glob("*.md"))
    if not files:
        raise SystemExit(f"没有输入：{IN_DIR}（先跑 clean_md.py）")

    all_chunks: list[dict] = []
    dropped_all: list[dict] = []
    for path in files:
        raw_chunks = chunk_doc(path)
        chunks = [c for c in raw_chunks if c["kind"] == "table" or len(c["text"]) >= MIN_CHARS]
        dropped = [c for c in raw_chunks if c not in chunks]
        dropped_all.extend(dropped)
        for idx, c in enumerate(chunks, start=1):
            c["id"] = f"{c['doc']}#{idx:04d}"
            c["chars"] = len(c["text"])

        # 每块开头补上「文档 / 章节 / 起止页」，这是出处的来源
        for c in chunks:
            page = (
                f"第{c['page_from']}页"
                if c["page_from"] == c["page_to"]
                else f"第{c['page_from']}-{c['page_to']}页"
            )
            c["text"] = f"《{c['doc']}》 {c['section'] or '正文'} {page}\n{c['text']}"
            c["chars"] = len(c["text"])

        all_chunks.extend(chunks)

        show = OUT_DIR / f"{path.stem}.md"
        show.write_text(
            "\n\n".join(f"<!-- chunk {c['id']} · {c['kind']} · {c['chars']}字 -->\n{c['text']}" for c in chunks),
            encoding="utf-8",
        )

        sizes = [c["chars"] for c in chunks]
        print(
            f"{path.stem}\n"
            f"    块数 {len(chunks):>3}  弃 {len(dropped):>2}  平均 {sum(sizes) // len(sizes):>3} 字  "
            f"最大 {max(sizes):>3} 字  表格块 {sum(1 for c in chunks if c['kind'] == 'table'):>2}"
        )

    with (OUT_DIR / "chunks.jsonl").open("w", encoding="utf-8") as fh:
        for c in all_chunks:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    # 被丢弃的短块单独留档，便于人工确认没有丢正文
    (OUT_DIR / "dropped.md").write_text(
        "\n\n".join(
            f"<!-- {c['doc']} · {c['section'] or '正文'} · 第{c['page_from']}页 · {len(c['text'])}字 -->\n{c['text']}"
            for c in dropped_all
        ),
        encoding="utf-8",
    )

    sizes = [c["chars"] for c in all_chunks]
    print(
        f"\n合计 {len(all_chunks)} 块 | 平均 {sum(sizes) // len(sizes)} 字 | "
        f"最大 {max(sizes)} 字 | 表格块 {sum(1 for c in all_chunks if c['kind'] == 'table')}"
    )


if __name__ == "__main__":
    main()
