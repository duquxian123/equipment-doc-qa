"""索引版本快照、列表与回滚。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import auth

ROOT = Path(__file__).resolve().parents[1]
VERSION_ROOT = ROOT / "output" / "versions"
CHUNKS_FILE = ROOT / "output" / "chunks" / "chunks.jsonl"
INDEX_DIR = ROOT / "output" / "index"
CURRENT_FILE = VERSION_ROOT / "current.json"

_lock = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    if path.exists():
        with path.open("rb") as fh:
            while block := fh.read(1024 * 1024):
                digest.update(block)
    return digest.hexdigest()


def _read_current_id() -> str | None:
    if not CURRENT_FILE.exists():
        return None
    try:
        return json.loads(CURRENT_FILE.read_text(encoding="utf-8")).get("version_id")
    except (OSError, ValueError, TypeError):
        return None


def _write_current(version_id: str) -> None:
    VERSION_ROOT.mkdir(parents=True, exist_ok=True)
    temp = CURRENT_FILE.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps({"version_id": version_id, "updated_at": utc_now()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(CURRENT_FILE)


def _manifests() -> list[dict]:
    VERSION_ROOT.mkdir(parents=True, exist_ok=True)
    result: list[dict] = []
    for manifest_path in VERSION_ROOT.glob("*/manifest.json"):
        try:
            result.append(json.loads(manifest_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            continue
    result.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return result


def _document_names_from_chunks() -> list[str]:
    names: set[str] = set()
    if CHUNKS_FILE.exists():
        with CHUNKS_FILE.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    names.add(str(json.loads(line)["doc"]))
    return sorted(names)


def _index_meta() -> dict:
    path = INDEX_DIR / "index_meta.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def create_snapshot(
    *,
    actor: dict | None,
    label: str,
    note: str = "",
    make_current: bool = True,
) -> dict:
    with _lock:
        if not CHUNKS_FILE.exists() or not (INDEX_DIR / "vectors.npy").exists():
            raise ValueError("当前知识块或索引不完整，无法创建版本")
        VERSION_ROOT.mkdir(parents=True, exist_ok=True)
        version_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        destination = VERSION_ROOT / version_id
        destination.mkdir(parents=True, exist_ok=False)
        shutil.copy2(CHUNKS_FILE, destination / "chunks.jsonl")
        for name in ("vectors.npy", "index_meta.json"):
            shutil.copy2(INDEX_DIR / name, destination / name)

        meta = _index_meta()
        manifest = {
            "version_id": version_id,
            "label": label,
            "note": note,
            "created_at": utc_now(),
            "created_by": actor["username"] if actor else "system",
            "chunk_count": int(meta.get("count", 0)),
            "document_names": _document_names_from_chunks(),
            "chunks_sha256": _file_hash(destination / "chunks.jsonl"),
            "vectors_sha256": _file_hash(destination / "vectors.npy"),
        }
        (destination / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if make_current:
            _write_current(version_id)
        return manifest


def ensure_initial_version() -> dict | None:
    if _manifests():
        return None
    if not CHUNKS_FILE.exists() or not (INDEX_DIR / "vectors.npy").exists():
        return None
    return create_snapshot(
        actor=None,
        label="初始索引",
        note="系统首次启用版本管理时自动创建",
        make_current=True,
    )


def list_versions() -> dict:
    ensure_initial_version()
    current_id = _read_current_id()
    items = []
    for manifest in _manifests():
        items.append({**manifest, "is_current": manifest["version_id"] == current_id})
    return {"current_version_id": current_id, "versions": items}


def rollback_version(version_id: str, actor: dict) -> dict:
    with _lock:
        source = VERSION_ROOT / version_id
        manifest_path = source / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError("版本不存在")
        for name in ("chunks.jsonl", "vectors.npy", "index_meta.json"):
            if not (source / name).exists():
                raise ValueError(f"版本文件不完整：{name}")

        safety = create_snapshot(
            actor=actor,
            label="回滚前自动备份",
            note=f"回滚到 {version_id} 前自动保存",
            make_current=False,
        )
        temp_paths: list[tuple[Path, Path]] = []
        try:
            CHUNKS_FILE.parent.mkdir(parents=True, exist_ok=True)
            INDEX_DIR.mkdir(parents=True, exist_ok=True)
            for name, destination in (
                ("chunks.jsonl", CHUNKS_FILE),
                ("vectors.npy", INDEX_DIR / "vectors.npy"),
                ("index_meta.json", INDEX_DIR / "index_meta.json"),
            ):
                temp = destination.with_name(destination.name + ".rollback.tmp")
                shutil.copy2(source / name, temp)
                temp_paths.append((temp, destination))
            for temp, destination in temp_paths:
                os.replace(temp, destination)
            _write_current(version_id)
            auth.audit(
                actor,
                "version.rollback",
                target=f"version:{version_id}",
                details={"safety_version": safety["version_id"]},
            )
            return {**json.loads(manifest_path.read_text(encoding="utf-8")), "is_current": True}
        except Exception:
            for temp, _ in temp_paths:
                temp.unlink(missing_ok=True)
            raise
