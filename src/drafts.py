"""待发布文档的暂存、审核和发布。

上传文档先进入 output/staging/<job_id>，自动完成解析、清洗和切片。
审核期间不修改 corpus、正式知识块或正式向量索引，只有 publish_draft
才会把通过审核的结果合并进当前问答索引。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import build_vector_index
import chunk
import parse_pdf
import review_clean

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "corpus"
STAGING_ROOT = ROOT / "output" / "staging"
PUBLISH_BACKUP_ROOT = ROOT / "output" / "publish_backups"
CHUNKS_FILE = ROOT / "output" / "chunks" / "chunks.jsonl"
INDEX_DIR = ROOT / "output" / "index"

PROCESS_LOCK = threading.Lock()
PUBLISH_LOCK = threading.Lock()
JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")

Progress = Callable[[str, str, int], None]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_job_id(job_id: str) -> None:
    if not JOB_ID_RE.fullmatch(job_id):
        raise ValueError("任务编号无效")


def job_dir(job_id: str) -> Path:
    _check_job_id(job_id)
    return STAGING_ROOT / job_id


def state_file(job_id: str) -> Path:
    return job_dir(job_id) / "state.json"


def load_state(job_id: str) -> dict:
    path = state_file(job_id)
    if not path.exists():
        raise FileNotFoundError("入库任务不存在")
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(state: dict) -> dict:
    path = state_file(state["job_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return state


def safe_document_name(name: str, digest: str) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(name).stem).strip(" .")
    stem = stem or "document"
    target = CORPUS_DIR / f"{stem}.pdf"
    if target.exists():
        stem = f"{stem}-{digest[:8]}"
    return stem


def create_draft(
    job_id: str,
    *,
    source_path: Path,
    original_name: str,
    content_hash: str,
    document_name: str,
) -> dict:
    directory = job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=False)
    source_target = directory / "source.pdf"
    shutil.copy2(source_path, source_target)
    source_path.unlink(missing_ok=True)
    now = utc_now()
    state = {
        "job_id": job_id,
        "filename": original_name,
        "document_name": document_name,
        "source_hash": content_hash,
        "status": "queued",
        "stage": "queued",
        "progress": 2,
        "message": "文件已上传，等待处理",
        "error": None,
        "duplicate": False,
        "published": False,
        "created_at": now,
        "updated_at": now,
        "clean_settings": {
            "preset": "standard",
            "restore_keys": [],
            "remove_keys": [],
            "excluded_pages": [],
        },
        "chunk_settings": {
            "max_chars": 400,
            "min_chars": 40,
            "table_max_chars": 800,
            "split_by_page": False,
        },
        "section_overrides": {},
        "parse": None,
        "clean": None,
        "chunk_count": 0,
        "active_chunk_count": 0,
        "publish": None,
    }
    return save_state(state)


def _progress(state: dict, stage: str, message: str, percent: int) -> None:
    state.update(
        {
            "status": "running",
            "stage": stage,
            "message": message,
            "progress": percent,
        }
    )
    save_state(state)


def _parse_source(state: dict) -> dict:
    directory = job_dir(state["job_id"])
    source = directory / "source.pdf"
    parsed = {**parse_pdf.parse_txt(source), **parse_pdf.parse_md(source)}
    txt = parsed.pop("txt")
    md = parsed.pop("md")
    (directory / "parsed.txt").write_text(txt, encoding="utf-8")
    (directory / "parsed.md").write_text(md, encoding="utf-8")
    state["parse"] = parsed
    save_state(state)
    return parsed


def _clean_and_chunk(state: dict) -> None:
    directory = job_dir(state["job_id"])
    parsed_markdown = (directory / "parsed.md").read_text(encoding="utf-8")
    settings = state["clean_settings"]
    cleaned = review_clean.clean_document(
        parsed_markdown,
        preset=settings["preset"],
        restore_keys=set(settings["restore_keys"]),
        remove_keys=set(settings["remove_keys"]),
        excluded_pages={int(page) for page in settings["excluded_pages"]},
    )
    (directory / "cleaned.md").write_text(cleaned["markdown"], encoding="utf-8")
    state["clean"] = {
        "preset": cleaned["preset"],
        "options": cleaned["options"],
        "headers": cleaned["headers"],
        "toc_pages": cleaned["toc_pages"],
        "excluded_pages": cleaned["excluded_pages"],
        "removed_counts": cleaned["removed_counts"],
    }

    settings = state["chunk_settings"]
    options = chunk.ChunkOptions(
        max_chars=int(settings["max_chars"]),
        min_chars=int(settings["min_chars"]),
        table_max_chars=int(settings["table_max_chars"]),
        split_by_page=bool(settings["split_by_page"]),
    )
    work_path = directory / f"{state['document_name']}.md"
    shutil.copy2(directory / "cleaned.md", work_path)
    raw_chunks = chunk.chunk_doc(work_path, options)
    chunks = [
        item
        for item in raw_chunks
        if item["kind"] == "table" or len(item["body"]) >= options.min_chars
    ]

    overrides = state.get("section_overrides", {})
    for index, item in enumerate(chunks, start=1):
        original_section = item["section"]
        section = overrides.get(original_section, original_section)
        page = (
            f"第{item['page_from']}页"
            if item["page_from"] == item["page_to"]
            else f"第{item['page_from']}-{item['page_to']}页"
        )
        item.update(
            {
                "id": f"{state['document_name']}#{index:04d}",
                "doc": state["document_name"],
                "section_original": original_section,
                "section": section,
                "state": "active",
                "exclude_reason": None,
                "text": f"《{state['document_name']}》 {section or '正文'} {page}\n{item['body']}",
            }
        )
        item["chars"] = len(item["text"])

    write_chunks(state["job_id"], chunks)
    state["chunk_count"] = len(chunks)
    state["active_chunk_count"] = len(chunks)
    save_state(state)


def process_job(job_id: str, progress: Progress | None = None) -> dict:
    with PROCESS_LOCK:
        state = load_state(job_id)
        try:
            _progress(state, "parsing", "正在解析 PDF 页面与表格", 12)
            if progress:
                progress("parsing", "正在解析 PDF 页面与表格", 12)
            _parse_source(state)

            _progress(state, "cleaning", "正在生成可审核的清洗结果", 34)
            if progress:
                progress("cleaning", "正在生成可审核的清洗结果", 34)
            _clean_and_chunk(state)

            _progress(state, "review", "自动处理完成，等待人工审核", 100)
            state["status"] = "review"
            if progress:
                progress("review", "自动处理完成，等待人工审核", 100)
            save_state(state)
            return state
        except Exception as exc:
            state.update(
                {
                    "status": "failed",
                    "stage": "failed",
                    "progress": 100,
                    "message": str(exc) if isinstance(exc, ValueError) else "文档处理失败",
                    "error": str(exc) if isinstance(exc, ValueError) else "文档处理失败，请查看服务日志",
                }
            )
            save_state(state)
            raise


def _chunks_path(job_id: str) -> Path:
    return job_dir(job_id) / "chunks.jsonl"


def read_chunks(job_id: str) -> list[dict]:
    path = _chunks_path(job_id)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_chunks(job_id: str, chunks: list[dict]) -> None:
    path = _chunks_path(job_id)
    temp = path.with_suffix(".jsonl.tmp")
    with temp.open("w", encoding="utf-8") as fh:
        for item in chunks:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    temp.replace(path)


def review_payload(job_id: str) -> dict:
    state = load_state(job_id)
    directory = job_dir(job_id)
    parsed_path = directory / "parsed.md"
    cleaning = None
    if parsed_path.exists():
        settings = state["clean_settings"]
        cleaned = review_clean.clean_document(
            parsed_path.read_text(encoding="utf-8"),
            preset=settings["preset"],
            restore_keys=set(settings["restore_keys"]),
            remove_keys=set(settings["remove_keys"]),
            excluded_pages={int(page) for page in settings["excluded_pages"]},
        )
        cleaning = {
            "preset": cleaned["preset"],
            "options": cleaned["options"],
            "pages": cleaned["pages"],
            "removed_items": cleaned["removed_items"],
            "removed_counts": cleaned["removed_counts"],
        }
    return {
        "job": state,
        "cleaning": cleaning,
        "chunks": read_chunks(job_id),
        "pdf_url": f"/jobs/{job_id}/source",
    }


def list_jobs(limit: int = 20) -> list[dict]:
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    jobs: list[dict] = []
    for state_path in STAGING_ROOT.glob("*/state.json"):
        try:
            jobs.append(load_state(state_path.parent.name))
        except (OSError, ValueError, FileNotFoundError):
            continue
    jobs.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return jobs[:limit]


def apply_cleaning(job_id: str, payload: dict) -> dict:
    state = load_state(job_id)
    if state["status"] not in {"review", "failed"}:
        raise ValueError("当前任务不在可审核状态")
    state["clean_settings"] = {
        "preset": payload.get("preset", "standard"),
        "restore_keys": [str(key) for key in payload.get("restore_keys", [])],
        "remove_keys": [str(key) for key in payload.get("remove_keys", [])],
        "excluded_pages": sorted({int(page) for page in payload.get("excluded_pages", [])}),
    }
    save_state(state)
    _progress(state, "cleaning", "正在应用清洗审核结果", 45)
    _clean_and_chunk(state)
    state.update(
        {
            "status": "review",
            "stage": "review",
            "progress": 100,
            "message": "清洗结果已更新，请复核切片",
            "error": None,
        }
    )
    return save_state(state)


def reparse_draft(job_id: str) -> dict:
    state = load_state(job_id)
    if state["status"] not in {"review", "failed"}:
        raise ValueError("当前任务不在可重新解析状态")
    with PROCESS_LOCK:
        _progress(state, "parsing", "正在重新解析 PDF", 20)
        _parse_source(state)
        _progress(state, "cleaning", "正在重新清洗与切片", 55)
        _clean_and_chunk(state)
        state.update(
            {
                "status": "review",
                "stage": "review",
                "progress": 100,
                "message": "已重新解析并生成审核草稿",
                "error": None,
            }
        )
        return save_state(state)


def apply_chunk_settings(job_id: str, payload: dict) -> dict:
    state = load_state(job_id)
    if state["status"] not in {"review", "failed"}:
        raise ValueError("当前任务不在可审核状态")
    settings = state["chunk_settings"]
    for key in ("max_chars", "min_chars", "table_max_chars"):
        if key in payload:
            settings[key] = max(20, min(2000, int(payload[key])))
    if "split_by_page" in payload:
        settings["split_by_page"] = bool(payload["split_by_page"])
    save_state(state)
    _progress(state, "chunking", "正在按审核参数重新切分", 65)
    _clean_and_chunk(state)
    state.update(
        {
            "status": "review",
            "stage": "review",
            "progress": 100,
            "message": "切片已重新生成，请复核结果",
            "error": None,
        }
    )
    return save_state(state)


def update_chunk(job_id: str, chunk_id: str, payload: dict) -> dict:
    state = load_state(job_id)
    if state["status"] != "review":
        raise ValueError("当前任务不在审核状态")
    chunks = read_chunks(job_id)
    target = next((item for item in chunks if item["id"] == chunk_id), None)
    if not target:
        raise FileNotFoundError("知识块不存在")

    if "excluded" in payload:
        target["state"] = "excluded" if payload["excluded"] else "active"
        target["exclude_reason"] = payload.get("exclude_reason") if target["state"] == "excluded" else None

    if "section" in payload:
        new_section = str(payload["section"]).strip()
        if not new_section:
            raise ValueError("章节名称不能为空")
        original = target.get("section_original", target["section"])
        state.setdefault("section_overrides", {})[original] = new_section
        for item in chunks:
            if item.get("section_original") == original:
                item["section"] = new_section
                page = (
                    f"第{item['page_from']}页"
                    if item["page_from"] == item["page_to"]
                    else f"第{item['page_from']}-{item['page_to']}页"
                )
                item["text"] = f"《{state['document_name']}》 {new_section} {page}\n{item['body']}"
                item["chars"] = len(item["text"])

    write_chunks(job_id, chunks)
    state["active_chunk_count"] = sum(1 for item in chunks if item.get("state") == "active")
    return save_state(state)


def discard_draft(job_id: str) -> dict:
    state = load_state(job_id)
    if state["status"] == "published":
        raise ValueError("已发布文档不能在此步骤放弃")
    state.update(
        {
            "status": "discarded",
            "stage": "discarded",
            "progress": 100,
            "message": "本次入库已放弃，正式索引未改变",
        }
    )
    return save_state(state)


def _backup_active(job_id: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = PUBLISH_BACKUP_ROOT / f"{stamp}-{job_id[:8]}"
    backup.mkdir(parents=True, exist_ok=False)
    if CHUNKS_FILE.exists():
        shutil.copy2(CHUNKS_FILE, backup / "chunks.jsonl")
    for name in ("vectors.npy", "index_meta.json"):
        source = INDEX_DIR / name
        if source.exists():
            shutil.copy2(source, backup / name)
    return backup


def publish_draft(job_id: str, progress: Progress | None = None) -> dict:
    with PUBLISH_LOCK:
        state = load_state(job_id)
        if state["status"] != "review":
            raise ValueError("只有审核中的文档可以发布")
        directory = job_dir(job_id)
        draft_chunks = [
            item for item in read_chunks(job_id) if item.get("state") == "active"
        ]
        if not draft_chunks:
            raise ValueError("没有可发布的知识块")

        _progress(state, "publishing", "正在备份当前索引", 15)
        backup = _backup_active(job_id)
        active_chunks = []
        if CHUNKS_FILE.exists():
            with CHUNKS_FILE.open(encoding="utf-8") as fh:
                active_chunks = [
                    json.loads(line)
                    for line in fh
                    if line.strip()
                    and json.loads(line).get("doc") != state["document_name"]
                ]
        combined = active_chunks + draft_chunks

        CHUNKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        chunks_temp = CHUNKS_FILE.with_suffix(".jsonl.tmp")
        with chunks_temp.open("w", encoding="utf-8") as fh:
            for item in combined:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        chunks_temp.replace(CHUNKS_FILE)

        target_pdf = CORPUS_DIR / f"{state['document_name']}.pdf"
        publish_outputs: list[Path] = []
        try:
            if target_pdf.exists():
                raise ValueError("正式文档库中存在同名文档，请放弃后重新上传")
            CORPUS_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(directory / "source.pdf", target_pdf)
            publish_outputs.append(target_pdf)

            parsed_txt = ROOT / "output" / "parsed" / f"{state['document_name']}.txt"
            parsed_md = ROOT / "output" / "parsed-md" / f"{state['document_name']}.md"
            cleaned_md = ROOT / "output" / "parsed-clean" / f"{state['document_name']}.md"
            for destination in (parsed_txt, parsed_md, cleaned_md):
                destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(directory / "parsed.txt", parsed_txt)
            shutil.copy2(directory / "parsed.md", parsed_md)
            shutil.copy2(directory / "cleaned.md", cleaned_md)
            publish_outputs.extend([parsed_txt, parsed_md, cleaned_md])

            _progress(state, "publishing", "正在生成正式向量索引", 55)
            index_result = build_vector_index.build_index()
            state.update(
                {
                    "status": "complete",
                    "stage": "published",
                    "progress": 100,
                    "message": "审核通过，文档已发布到问答索引",
                    "published": True,
                    "error": None,
                    "publish": {
                        "published_at": utc_now(),
                        "chunk_count": len(draft_chunks),
                        "index_count": index_result["count"],
                        "backup": str(backup.relative_to(ROOT)),
                    },
                }
            )
            if progress:
                progress("published", "审核通过，文档已发布到问答索引", 100)
            return save_state(state)
        except Exception:
            for path in publish_outputs:
                path.unlink(missing_ok=True)
            if (backup / "chunks.jsonl").exists():
                shutil.copy2(backup / "chunks.jsonl", CHUNKS_FILE)
            for name in ("vectors.npy", "index_meta.json"):
                source = backup / name
                if source.exists():
                    shutil.copy2(source, INDEX_DIR / name)
            state.update(
                {
                    "status": "review",
                    "stage": "review",
                    "progress": 100,
                    "message": "发布失败，正式索引已恢复",
                }
            )
            save_state(state)
            raise
