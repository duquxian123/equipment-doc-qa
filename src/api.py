"""设备文档问答 Web 应用与本地 FastAPI 服务。

启动：
    .\\run_api.ps1

页面：
    http://127.0.0.1:8000/

接口文档：
    http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from answer import answer  # noqa: E402
from agent import AgentService  # noqa: E402
from agent.memory import SessionAccessError  # noqa: E402
from candidates import CandidateMerger  # noqa: E402
import drafts  # noqa: E402
import auth  # noqa: E402
import ops as ops_service  # noqa: E402
import versions  # noqa: E402

ENV_FILE = ROOT / ".env"
CORPUS_DIR = ROOT / "corpus"
CHUNKS_FILE = ROOT / "output" / "chunks" / "chunks.jsonl"
INDEX_DIR = ROOT / "output" / "index"
WEB_DIR = ROOT / "web"
UPLOAD_TMP_DIR = ROOT / "output" / "uploads"

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
READ_BYTES = 1024 * 1024

app = FastAPI(
    title="Equipment Document QA",
    description="设备文档 RAG 问答、制造业生产运营沙盘与可观察智能体工作台",
    version="0.5.0",
)

auth.initialize()
ops_service.initialize()
versions.ensure_initial_version()
app.include_router(ops_service.router)
agent_service = AgentService()

_engine: CandidateMerger | None = None
_engine_lock = threading.Lock()


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, examples=["二级保养多久做一次"])


class Source(BaseModel):
    document: str
    document_id: str | None = None
    chunk_id: str | None = None
    page_start: int
    page_end: int
    section: str
    channel: str
    rank: int
    score: float
    quote: str | None = None
    cited: bool = False
    pdf_url: str | None = None


class AskResponse(BaseModel):
    request_id: str
    answer: str
    refused: bool
    sources: list[Source]
    citations: list[Source]


class DocumentInfo(BaseModel):
    id: str
    name: str
    pages: int | None
    chunks: int
    size_bytes: int
    uploaded_at: str
    status: str
    pdf_url: str


class DocumentsResponse(BaseModel):
    documents: list[DocumentInfo]
    totals: dict[str, int]


class JobResponse(BaseModel):
    job_id: str
    filename: str
    document_name: str | None = None
    stage: str
    progress: int
    message: str
    status: str
    created_at: str
    updated_at: str
    document_id: str | None = None
    duplicate: bool = False
    published: bool = False
    error: str | None = None
    result: dict | None = None
    publish: dict | None = None


class CleanReviewRequest(BaseModel):
    preset: str = "standard"
    restore_keys: list[str] = Field(default_factory=list)
    remove_keys: list[str] = Field(default_factory=list)
    excluded_pages: list[int] = Field(default_factory=list)


class ChunkSettingsRequest(BaseModel):
    max_chars: int = Field(default=400, ge=20, le=2000)
    min_chars: int = Field(default=40, ge=0, le=500)
    table_max_chars: int = Field(default=800, ge=100, le=4000)
    split_by_page: bool = False


class ChunkUpdateRequest(BaseModel):
    section: str | None = None
    excluded: bool | None = None
    exclude_reason: str | None = None


class AuthCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)


class UserCreateRequest(AuthCredentials):
    role: str = "user"


class UserUpdateRequest(BaseModel):
    role: str | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class AgentRunRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, min_length=8, max_length=64)
    mode: str = Field(default="auto", pattern="^(auto|live|demo)$")


PUBLIC_PATHS = {
    "/",
    "/agent",
    "/api",
    "/health",
    "/auth/status",
    "/auth/login",
    "/auth/setup",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
}


@app.middleware("http")
async def session_permission_middleware(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)

    token = request.cookies.get(auth.SESSION_COOKIE)
    user = auth.current_user(token)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"detail": "请先登录", "code": "authentication_required"},
        )
    request.state.user = user
    permission = auth.permission_required(path, request.method)
    if permission and permission not in user["permissions"]:
        return JSONResponse(
            status_code=403,
            content={"detail": "当前角色没有执行此操作的权限", "code": "permission_denied"},
        )
    return await call_next(request)


def has_api_key() -> bool:
    """只检查密钥是否存在，不读取或返回密钥内容。"""
    injected = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if injected and injected != "replace-me":
        return True
    if not ENV_FILE.exists():
        return False
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("DEEPSEEK_API_KEY="):
            value = line.split("=", 1)[1].strip()
            return bool(value and value != "replace-me")
    return False


def deployment_checks() -> dict[str, bool]:
    return {
        "env_file": ENV_FILE.exists(),
        "deepseek_api_key": has_api_key(),
        "chunks": CHUNKS_FILE.exists(),
        "vector_index": (INDEX_DIR / "vectors.npy").exists()
        and (INDEX_DIR / "index_meta.json").exists(),
    }


def get_engine() -> CandidateMerger:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = CandidateMerger()
    return _engine


def reload_engine() -> None:
    global _engine
    with _engine_lock:
        _engine = None


def document_id(path: Path) -> str:
    return hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:12]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(READ_BYTES):
            digest.update(block)
    return digest.hexdigest()


def chunk_groups() -> tuple[dict[str, list[dict]], set[str]]:
    grouped: dict[str, list[dict]] = {}
    indexed_ids: set[str] = set()
    meta_file = INDEX_DIR / "index_meta.json"
    if meta_file.exists():
        try:
            indexed_ids = set(json.loads(meta_file.read_text(encoding="utf-8")).get("ids", []))
        except (OSError, ValueError, TypeError):
            indexed_ids = set()

    if CHUNKS_FILE.exists():
        with CHUNKS_FILE.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                item = json.loads(line)
                grouped.setdefault(str(item["doc"]), []).append(item)
    return grouped, indexed_ids


def pdf_page_count(path: Path) -> int | None:
    try:
        with pymupdf.open(path) as document:
            return document.page_count
    except Exception:
        return None


def list_documents() -> tuple[list[DocumentInfo], dict[str, int]]:
    grouped, indexed_ids = chunk_groups()
    documents: list[DocumentInfo] = []
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    for path in sorted(CORPUS_DIR.glob("*.pdf"), key=lambda item: item.stat().st_mtime, reverse=True):
        items = grouped.get(path.stem, [])
        item_ids = {str(item["id"]) for item in items}
        ready = bool(item_ids) and item_ids.issubset(indexed_ids)
        stat = path.stat()
        identifier = document_id(path)
        documents.append(
            DocumentInfo(
                id=identifier,
                name=path.stem,
                pages=pdf_page_count(path),
                chunks=len(items),
                size_bytes=stat.st_size,
                uploaded_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                status="ready" if ready else ("pending" if items else "unindexed"),
                pdf_url=f"/documents/{identifier}/file",
            )
        )

    return documents, {
        "documents": len(documents),
        "pages": sum(item.pages or 0 for item in documents),
        "chunks": sum(item.chunks for item in documents),
    }


def run_draft_job(job_id: str) -> None:
    try:
        drafts.process_job(job_id)
    except Exception:
        logging.exception("Draft processing failed for job %s", job_id)


def run_publish_job(job_id: str, actor: dict) -> None:
    try:
        state = drafts.publish_draft(job_id)
        versions.create_snapshot(
            actor=actor,
            label=f"发布：{state.get('document_name', job_id)}",
            note=f"由入库任务 {job_id} 发布",
            make_current=True,
        )
        auth.audit(
            actor,
            "document.publish",
            target=f"job:{job_id}",
            details={
                "document": state.get("document_name"),
                "chunk_count": state.get("active_chunk_count"),
            },
        )
        reload_engine()
    except Exception:
        logging.exception("Draft publish failed for job %s", job_id)
        reload_engine()


def citation_page(label: str) -> tuple[int, int] | None:
    match = re.search(r"第\s*(\d+)(?:\s*[-–—]\s*(\d+))?\s*页", label)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or match.group(1))


def candidate_aliases(document: str) -> set[str]:
    aliases = {document}
    if "_" in document:
        aliases.add(document.rsplit("_", 1)[-1])
    return aliases


def locate_citations(text: str, candidates: list[dict]) -> list[dict]:
    """把模型输出中的［文档 第X页］映射回实际候选知识块。"""
    if text.lstrip().startswith("资料中没有相关内容"):
        return []

    labels = re.findall(r"［([^］]+)］", text)
    located: list[dict] = []
    seen: set[str] = set()
    for label in labels:
        page_range = citation_page(label)
        if not page_range:
            continue
        page_from, page_to = page_range
        for item in candidates:
            aliases = candidate_aliases(str(item["doc"]))
            if not any(alias and alias in label for alias in aliases):
                continue
            if int(item["page_from"]) > page_to or int(item["page_to"]) < page_from:
                continue
            identifier = str(item["id"])
            if identifier not in seen:
                seen.add(identifier)
                located.append(item)
                break
    return located


@app.get("/api")
def api_info() -> dict[str, str]:
    return {
        "service": "equipment-doc-qa",
        "docs": "/docs",
        "health": "/health",
        "web": "/",
    }


@app.get("/agent/meta")
def agent_meta(request: Request) -> dict:
    return agent_service.meta(request.state.user)


@app.post("/agent/run")
def run_agent(payload: AgentRunRequest, request: Request) -> dict:
    try:
        result = agent_service.run(
            payload.query.strip(),
            request.state.user,
            session_id=payload.session_id,
            mode=payload.mode,
        )
    except (SessionAccessError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    auth.audit(
        request.state.user,
        "agent.run",
        target=f"session:{result['session_id']}",
        details={
            "run_id": result["run_id"],
            "mode": result["mode"],
            "status": result["status"],
            "tools": [
                step["name"]
                for step in result["steps"]
                if step.get("kind") == "tool" and step.get("name")
            ],
        },
        ip_address=client_ip(request),
    )
    return result


@app.get("/agent/sessions/{session_id}")
def agent_session_history(session_id: str, request: Request) -> dict:
    try:
        return agent_service.history(session_id, request.state.user)
    except (SessionAccessError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/agent/sessions/{session_id}", status_code=204)
def clear_agent_session(session_id: str, request: Request) -> Response:
    try:
        agent_service.clear(session_id, request.state.user)
    except (SessionAccessError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


@app.get("/health")
def health() -> dict:
    checks = deployment_checks()
    missing = [name for name, passed in checks.items() if not passed]
    return {
        "status": "ok" if not missing else "degraded",
        "ready_for_questions": not missing,
        "checks": checks,
        "missing": missing,
    }


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def set_session_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        key=auth.SESSION_COOKIE,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        path="/",
    )


@app.get("/auth/status")
def auth_status() -> dict:
    return {"initialized": auth.initialized()}


@app.post("/auth/setup")
def setup_admin(payload: AuthCredentials, request: Request, response: Response) -> dict:
    try:
        user = auth.create_initial_admin(
            payload.username,
            payload.password,
            ip_address=client_ip(request),
        )
    except ValueError as exc:
        status = 409 if "已经初始化" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    token, max_age = auth.create_session(user["id"])
    set_session_cookie(response, token, max_age)
    return {"user": user}


@app.post("/auth/login")
def login(payload: AuthCredentials, request: Request, response: Response) -> dict:
    user = auth.authenticate(
        payload.username,
        payload.password,
        ip_address=client_ip(request),
    )
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token, max_age = auth.create_session(user["id"])
    set_session_cookie(response, token, max_age)
    return {"user": user}


@app.get("/auth/me")
def me(request: Request) -> dict:
    return {"user": request.state.user}


@app.post("/auth/logout")
def logout(request: Request, response: Response) -> dict:
    user = request.state.user
    auth.destroy_session(request.cookies.get(auth.SESSION_COOKIE))
    auth.audit(user, "auth.logout", target=f"user:{user['id']}", ip_address=client_ip(request))
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, request: Request) -> AskResponse:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")

    checks = deployment_checks()
    missing = [name for name, passed in checks.items() if not passed]
    if missing:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "服务尚未完成数据或模型配置",
                "missing": missing,
            },
        )

    try:
        text, candidates = answer(question, merger=get_engine())
    except Exception as exc:
        logging.exception("Question answering failed")
        raise HTTPException(
            status_code=500,
            detail=f"问答执行失败：{type(exc).__name__}",
        ) from exc

    documents, _ = list_documents()
    ids_by_name = {item.name: item.id for item in documents}
    urls_by_name = {item.name: item.pdf_url for item in documents}
    located = locate_citations(text, candidates)
    cited_ids = {str(item["id"]) for item in located}
    refused = text.lstrip().startswith("资料中没有相关内容")

    def build_source(item: dict) -> Source:
        doc = str(item["doc"])
        chunk_id = str(item["id"])
        cited = chunk_id in cited_ids and not refused
        return Source(
            document=doc,
            document_id=ids_by_name.get(doc),
            chunk_id=chunk_id if cited else None,
            page_start=int(item["page_from"]),
            page_end=int(item["page_to"]),
            section=str(item["section"]),
            channel=str(item["source"]),
            rank=int(item["rank"]),
            score=float(item["score"]),
            quote=str(item["text"]) if cited else None,
            cited=cited,
            pdf_url=urls_by_name.get(doc),
        )

    sources = [build_source(item) for item in candidates]
    citations = [build_source(item) for item in located] if not refused else []
    auth.audit(
        request.state.user,
        "qa.ask",
        target=None,
        details={
            "question": question[:300],
            "citations": len(citations),
            "refused": refused,
        },
        ip_address=client_ip(request),
    )
    return AskResponse(
        request_id=uuid.uuid4().hex,
        answer=text,
        refused=refused,
        sources=sources,
        citations=citations,
    )


@app.get("/documents", response_model=DocumentsResponse)
def documents() -> DocumentsResponse:
    items, totals = list_documents()
    return DocumentsResponse(documents=items, totals=totals)


@app.get("/documents/{doc_id}/file")
def document_file(doc_id: str) -> FileResponse:
    for path in CORPUS_DIR.glob("*.pdf"):
        if document_id(path) == doc_id:
            return FileResponse(
                path,
                media_type="application/pdf",
                filename=path.name,
                content_disposition_type="inline",
            )
    raise HTTPException(status_code=404, detail="文档不存在")


@app.post("/documents/upload", response_model=JobResponse, status_code=202)
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> JobResponse:
    original_name = Path(file.filename or "").name
    if Path(original_name).suffix.lower() != ".pdf":
        raise HTTPException(status_code=422, detail="只支持 PDF 文件")

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    temp_path = UPLOAD_TMP_DIR / f"{job_id}.pdf.part"
    digest = hashlib.sha256()
    size = 0
    head = b""

    try:
        with temp_path.open("wb") as fh:
            while block := await file.read(READ_BYTES):
                if not head:
                    head = block[:8]
                size += len(block)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="PDF 不能超过 50 MB")
                digest.update(block)
                fh.write(block)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    if size == 0:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="上传文件为空")
    if not head.startswith(b"%PDF-"):
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="文件内容不是有效的 PDF")

    incoming_hash = digest.hexdigest()
    for existing in CORPUS_DIR.glob("*.pdf"):
        if file_sha256(existing) == incoming_hash:
            temp_path.unlink(missing_ok=True)
            now = datetime.now(timezone.utc).isoformat()
            job = JobResponse(
                job_id=job_id,
                filename=existing.stem,
                document_name=existing.stem,
                stage="complete",
                progress=100,
                message="该文档已存在，无需重复入库",
                status="complete",
                created_at=now,
                updated_at=now,
                document_id=document_id(existing),
                duplicate=True,
                published=True,
            )
            auth.audit(
                request.state.user,
                "document.upload_duplicate",
                target=f"document:{existing.stem}",
                ip_address=client_ip(request),
            )
            return job

    for state_path in drafts.STAGING_ROOT.glob("*/state.json"):
        try:
            staged = drafts.load_state(state_path.parent.name)
        except (OSError, ValueError, FileNotFoundError):
            continue
        if staged.get("source_hash") == incoming_hash and staged.get("status") != "discarded":
            temp_path.unlink(missing_ok=True)
            auth.audit(
                request.state.user,
                "document.upload_duplicate_draft",
                target=f"job:{staged['job_id']}",
                ip_address=client_ip(request),
            )
            return JobResponse(**staged)

    document_name = drafts.safe_document_name(original_name, incoming_hash)
    state = drafts.create_draft(
        job_id,
        source_path=temp_path,
        original_name=original_name,
        content_hash=incoming_hash,
        document_name=document_name,
    )
    auth.audit(
        request.state.user,
        "document.upload",
        target=f"job:{job_id}",
        details={"filename": original_name},
        ip_address=client_ip(request),
    )
    background_tasks.add_task(run_draft_job, job_id)
    return JobResponse(**state)


@app.get("/jobs", response_model=list[JobResponse])
def list_jobs() -> list[JobResponse]:
    return [JobResponse(**state) for state in drafts.list_jobs()]


@app.get("/jobs/{job_id}", response_model=JobResponse)
def job_status(job_id: str) -> JobResponse:
    try:
        state = drafts.load_state(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="处理任务不存在")
    return JobResponse(**state)


@app.get("/jobs/{job_id}/source")
def draft_source(job_id: str) -> FileResponse:
    try:
        source = drafts.job_dir(job_id) / "source.pdf"
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not source.exists():
        raise HTTPException(status_code=404, detail="待审核 PDF 不存在")
    return FileResponse(
        source,
        media_type="application/pdf",
        filename=f"{job_id}.pdf",
        content_disposition_type="inline",
    )


@app.get("/jobs/{job_id}/review")
def draft_review(job_id: str) -> dict:
    try:
        return drafts.review_payload(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="审核任务不存在")


@app.post("/jobs/{job_id}/clean", response_model=JobResponse)
def apply_draft_cleaning(job_id: str, payload: CleanReviewRequest, request: Request) -> JobResponse:
    try:
        state = drafts.apply_cleaning(job_id, payload.model_dump())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    auth.audit(
        request.state.user,
        "document.clean",
        target=f"job:{job_id}",
        details=payload.model_dump(),
        ip_address=client_ip(request),
    )
    return JobResponse(**state)


@app.post("/jobs/{job_id}/reparse", response_model=JobResponse)
def reparse_draft(job_id: str, request: Request) -> JobResponse:
    try:
        state = drafts.reparse_draft(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    auth.audit(
        request.state.user,
        "document.reparse",
        target=f"job:{job_id}",
        ip_address=client_ip(request),
    )
    return JobResponse(**state)


@app.post("/jobs/{job_id}/rechunk", response_model=JobResponse)
def apply_draft_chunking(job_id: str, payload: ChunkSettingsRequest, request: Request) -> JobResponse:
    try:
        state = drafts.apply_chunk_settings(job_id, payload.model_dump())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    auth.audit(
        request.state.user,
        "document.rechunk",
        target=f"job:{job_id}",
        details=payload.model_dump(),
        ip_address=client_ip(request),
    )
    return JobResponse(**state)


@app.patch("/jobs/{job_id}/chunks/{chunk_id}", response_model=JobResponse)
def update_draft_chunk(
    job_id: str,
    chunk_id: str,
    payload: ChunkUpdateRequest,
    request: Request,
) -> JobResponse:
    try:
        state = drafts.update_chunk(job_id, chunk_id, payload.model_dump(exclude_none=True))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    auth.audit(
        request.state.user,
        "document.chunk_update",
        target=f"job:{job_id}/chunk:{chunk_id}",
        details=payload.model_dump(exclude_none=True),
        ip_address=client_ip(request),
    )
    return JobResponse(**state)


@app.post("/jobs/{job_id}/publish", response_model=JobResponse, status_code=202)
def publish_draft(
    job_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> JobResponse:
    try:
        state = drafts.load_state(job_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="审核任务不存在")
    if state.get("status") != "review":
        raise HTTPException(status_code=409, detail="只有审核中的文档可以发布")
    background_tasks.add_task(run_publish_job, job_id, request.state.user)
    return JobResponse(**state)


@app.post("/jobs/{job_id}/discard", response_model=JobResponse)
def discard_draft(job_id: str, request: Request) -> JobResponse:
    try:
        state = drafts.discard_draft(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    auth.audit(
        request.state.user,
        "document.discard",
        target=f"job:{job_id}",
        ip_address=client_ip(request),
    )
    return JobResponse(**state)


@app.get("/versions")
def version_list() -> dict:
    return versions.list_versions()


@app.post("/versions/{version_id}/rollback")
def rollback_version(version_id: str, request: Request) -> dict:
    try:
        version = versions.rollback_version(version_id, request.state.user)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    reload_engine()
    return {"version": version}


@app.get("/admin/users")
def admin_users() -> dict:
    return {"users": auth.list_users(), "roles": auth.roles_payload()["roles"]}


@app.post("/admin/users", status_code=201)
def admin_create_user(payload: UserCreateRequest, request: Request) -> dict:
    try:
        user = auth.create_user(
            payload.username,
            payload.password,
            payload.role,
            actor=request.state.user,
            ip_address=client_ip(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"user": user}


@app.patch("/admin/users/{user_id}")
def admin_update_user(user_id: int, payload: UserUpdateRequest, request: Request) -> dict:
    try:
        user = auth.update_user(
            user_id,
            payload.model_dump(exclude_none=True),
            actor=request.state.user,
            ip_address=client_ip(request),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"user": user}


@app.get("/admin/permissions")
def admin_permissions() -> dict:
    return auth.roles_payload()


@app.get("/audit")
def audit_logs(limit: int = 100) -> dict:
    return {"logs": auth.list_audit(limit)}


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/agent", include_in_schema=False)
def agent_web_app() -> FileResponse:
    return FileResponse(WEB_DIR / "agent.html")


@app.get("/", include_in_schema=False)
def web_app() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
