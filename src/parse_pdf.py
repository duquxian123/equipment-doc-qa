"""把 corpus/ 下的 PDF 解析成文本，输出两路结果以便对比。

用法:
    .venv\\Scripts\\python.exe src\\parse_pdf.py

两路输出:
    TXT   -> output/parsed/<文件名>.txt     按坐标顺序抄文字，结构丢失
    MD    -> output/parsed-md/<文件名>.md   经版面分析，标题与表格用 Markdown 表达

外加 output/parse_report.json，记录每份文档两种输出的规模与表格情况。
注意: 两路只用于对比，最终进索引的只能是其中一路。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf
import pymupdf4llm

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
OUT_TXT = ROOT / "output" / "parsed"
OUT_MD = ROOT / "output" / "parsed-md"


def parse_txt(path: Path) -> dict:
    """按坐标顺序抽取文字，保留分页标记。"""
    doc = pymupdf.open(path)
    pages: list[str] = []
    per_page: list[dict] = []
    empty_pages: list[int] = []
    table_blocks = 0

    for page_no, page in enumerate(doc, start=1):
        text = (page.get_text("text") or "").strip()
        pages.append(f"=== page {page_no} ===\n{text}")

        try:
            n_tables = len(page.find_tables().tables)
        except Exception:  # 个别页面结构异常时不中断整体解析
            n_tables = -1
        table_blocks += max(n_tables, 0)

        per_page.append({"page": page_no, "chars": len(text), "tables": n_tables})
        if not text:
            empty_pages.append(page_no)

    body = re.sub(r"\n{3,}", "\n\n", "\n\n".join(pages))

    return {
        "txt_chars": len(body),
        "txt_pages": doc.page_count,
        "txt_empty_pages": empty_pages,
        "txt_table_blocks": table_blocks,
        "txt_per_page": per_page,
        "txt": body,
    }


def parse_md(path: Path) -> dict:
    """经版面分析转 Markdown，标题与表格带上结构标记。

    按页输出并保留 === page N === 标记：一是后面统计页眉需要页边界，
    二是切分后标注「出处第几页」要靠它。
    """
    chunks = pymupdf4llm.to_markdown(str(path), page_chunks=True, show_progress=False)

    pages = []
    for chunk in chunks:
        page_no = chunk["metadata"]["page_number"]
        body = (chunk["text"] or "").strip()
        pages.append(f"=== page {page_no} ===\n{body}")
    md = "\n\n".join(pages)

    # Markdown 表格的判定：连续以 | 开头结尾的行，含一行分隔线
    lines = md.splitlines()
    table_blocks = 0
    in_table = False
    for line in lines:
        is_row = line.strip().startswith("|") and line.strip().endswith("|")
        if is_row and not in_table:
            in_table = True
            table_blocks += 1
        elif not is_row:
            in_table = False

    headings = sum(1 for line in lines if re.match(r"^#{1,6}\s", line))

    return {"md_chars": len(md), "md_table_blocks": table_blocks, "md_headings": headings, "md": md}


def main() -> None:
    OUT_TXT.mkdir(parents=True, exist_ok=True)
    OUT_MD.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(CORPUS.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"corpus 里没有 PDF：{CORPUS}")

    report: list[dict] = []
    for pdf in pdfs:
        result = {**parse_txt(pdf), **parse_md(pdf)}

        (OUT_TXT / f"{pdf.stem}.txt").write_text(result.pop("txt"), encoding="utf-8")
        (OUT_MD / f"{pdf.stem}.md").write_text(result.pop("md"), encoding="utf-8")

        result = {"file": pdf.name, **result}
        report.append(result)

        print(
            f"{pdf.stem}\n"
            f"    TXT  页={result['txt_pages']:>3}  字符={result['txt_chars']:>6}  "
            f"表格块={result['txt_table_blocks']:>3}  空页={result['txt_empty_pages'] or '无'}\n"
            f"    MD   字符={result['md_chars']:>6}  表格块={result['md_table_blocks']:>3}  "
            f"标题={result['md_headings']:>3}"
        )

    (ROOT / "output" / "parse_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"\n合计 {len(report)} 份 / {sum(r['txt_pages'] for r in report)} 页 | "
        f"TXT {sum(r['txt_chars'] for r in report)} 字符 / 表 {sum(r['txt_table_blocks'] for r in report)} | "
        f"MD {sum(r['md_chars'] for r in report)} 字符 / 表 {sum(r['md_table_blocks'] for r in report)}"
    )


if __name__ == "__main__":
    main()
