"""Download the public sample PDFs and build the local demo index."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLES_DIR = ROOT / "corpus"


@dataclass(frozen=True)
class Sample:
    filename: str
    url: str
    sha256: str


SAMPLES = (
    Sample(
        "T-ACCEM_CNC售后维修服务技术规程.pdf",
        "https://www.ttbz.org.cn/upload/file/20241012/6386434189092523249849402.pdf",
        "5D2777602EA5C36E0D2E6740BC194C6E53E94B70130BE80E7B6D3C034F21E4BC",
    ),
    Sample(
        "T-CMEEEA_企业机电制造设备管理服务要求.pdf",
        "https://www.ttbz.org.cn/upload/file/20250401/6387910422312619315429443.pdf",
        "2B0763F2E34C7B802A960F6B2D4D13435A893BEE3E7600C94620076531160574",
    ),
    Sample(
        "T-CMEEEA_汽车零部件制造通用技术规范.pdf",
        "https://www.ttbz.org.cn/upload/file/20260306/6390840910509235657307827.pdf",
        "C0DFDE696E473374A1B3C87B4D0EFB0F031B87681ABBE4B80955469B622254D1",
    ),
    Sample(
        "T-GXDSL-249-2025_切割设备维护保养规范.pdf",
        "https://www.ttbz.org.cn/upload/file/20251212/6390115620704125083126838.pdf",
        "65028E2E7542E0300345191A5CD2403BEE2F3CA3BF7E20E96F78DA60F99FC7D9",
    ),
    Sample(
        "威特龙_七氟丙烷灭火设备使用说明书.pdf",
        "http://www.vitalong.cn/upfile/att/2017030119355321b1c.pdf",
        "D41685018C47468B4E9787E1CA7D609DFE92B3B96EB6A60B94F432A70A5B3CB7",
    ),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def ensure_sample(sample: Sample, directory: Path) -> str:
    target = directory / sample.filename
    if target.exists():
        actual = sha256(target)
        if actual.upper() == sample.sha256.upper():
            return f"skip {sample.filename}"
        raise RuntimeError(
            f"{target} 已存在但 SHA-256 不一致。为避免覆盖本地文件，请手动检查或移除后重试。"
        )

    directory.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        sample.url,
        headers={"User-Agent": "EquipmentDoc-QA/1.0"},
    )
    temporary_path: Path | None = None
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{sample.filename}.",
                suffix=".part",
                dir=directory,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                while block := response.read(1024 * 1024):
                    temporary.write(block)

        actual = sha256(temporary_path)
        if actual.upper() != sample.sha256.upper():
            raise RuntimeError(
                f"{sample.filename} 下载文件校验失败：期望 {sample.sha256}，实际 {actual}"
            )
        os.replace(temporary_path, target)
        temporary_path = None
        return f"download {sample.filename}"
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def run_build_step(script: str) -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "src" / script)],
        cwd=ROOT,
        check=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="下载公开示例 PDF，校验哈希，并构建本地问答索引。"
    )
    parser.add_argument(
        "--samples-only",
        action="store_true",
        help="只下载和校验样例，不运行解析与索引。",
    )
    parser.add_argument(
        "--samples-dir",
        type=Path,
        default=DEFAULT_SAMPLES_DIR,
        help="样例 PDF 保存目录，默认使用项目的 corpus/。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    samples_dir = args.samples_dir.expanduser().resolve()
    if not args.samples_only and samples_dir != DEFAULT_SAMPLES_DIR.resolve():
        raise SystemExit("自定义 --samples-dir 仅可与 --samples-only 一起使用。")

    try:
        for sample in SAMPLES:
            print(ensure_sample(sample, samples_dir), flush=True)
    except Exception as exc:
        print(f"样例准备失败：{exc}", file=sys.stderr)
        return 1

    if args.samples_only:
        print("样例准备完成。")
        return 0

    for script in (
        "parse_pdf.py",
        "clean_md.py",
        "chunk.py",
        "build_vector_index.py",
    ):
        print(f"运行 {script}", flush=True)
        try:
            run_build_step(script)
        except subprocess.CalledProcessError as exc:
            print(
                f"构建步骤 {script} 失败（退出码 {exc.returncode}）。"
                "请检查网络、依赖和 .cache/fastembed；已有知识块不会自动删除。",
                file=sys.stderr,
            )
            return 1

    print("演示数据与索引准备完成，可运行 .\\run_api.ps1。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
