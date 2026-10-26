"""笔记富内容的附件存储：与 PDF 同目录的 `<pdf 名>.sketch-assets/` 目录。

设计约束（与 sidecar 配套）：
- sidecar 里只记**纯文件名**，路径一律由 PDF 路径推导，PDF 连同资源目录
  一起搬到别处仍然可用；
- 同名冲突由 uuid 命名避免，用户原始文件名只存进 `NoteAttachment.name`；
- 本模块纯标准库，不依赖 Qt / PyMuPDF。
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

ASSET_DIR_SUFFIX = ".sketch-assets"


def assets_dir(pdf_path: Path) -> Path:
    """`a.pdf` → 同目录的 `a.pdf.sketch-assets/`（保留 .pdf 全名，避免撞名）。"""
    path = Path(pdf_path)
    return path.with_name(path.name + ASSET_DIR_SUFFIX)


def is_plain_name(name: object) -> bool:
    """是否是可安全拼进 assets 目录的纯文件名。

    拒绝的东西：非字符串/空串、`.` `..`、带 `/` `\\` `:`（Windows 盘符与
    NTFS 数据流）、含 `..`、控制字符（NUL 等），以及 `Path(name).name` 与
    原名不同的任何形式（`a/../b`、`a/` 之类）。
    """
    if not isinstance(name, str) or not name:
        return False
    if name in (".", ".."):
        return False
    if any(ch in name for ch in "/\\:"):
        return False
    if ".." in name:
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):   # 控制字符（含 NUL）
        return False
    try:
        if Path(name).name != name or Path(name).is_absolute():
            return False
    except (OSError, ValueError):        # 非法字符（NUL 等）
        return False
    return True


def _require_plain_name(name: object) -> str:
    if not is_plain_name(name):
        raise ValueError(f"附件名必须是纯文件名：{name!r}")
    return str(name)


def store_asset(pdf_path: Path, source: Path) -> str:
    """把 `source` 复制进 assets 目录，返回新文件名（纯文件名）。

    新名字是 `uuid4().hex[:12] + source.suffix.lower()`，不保留原名是为了
    多个笔记引用同名文件时不互相覆盖。源不存在或读不了抛 `OSError`——
    调用方要能据此提示用户，而不是得到一个空附件。
    """
    src = Path(source)
    suffix = src.suffix.lower()
    target_dir = assets_dir(pdf_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex[:12]}{suffix}"
    try:
        shutil.copyfile(src, target_dir / name)
    except OSError:
        # 复制失败不留半截文件
        try:
            (target_dir / name).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return name


def store_png(pdf_path: Path, data: bytes) -> str:
    """把内存里的 PNG 字节写进 assets 目录，返回新文件名（剪贴板图片用）。"""
    target_dir = assets_dir(pdf_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex[:12]}.png"
    try:
        (target_dir / name).write_bytes(data)
    except OSError:
        try:
            (target_dir / name).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return name


def asset_path(pdf_path: Path, name: str) -> Path:
    """assets 目录内的资源路径；只接受纯文件名，绝不跟着路径穿越走。

    不检查文件是否存在：调用方要区分"名字非法"（ValueError）和"文件丢了"
    （渲染时画占位框），两者是不同的事。
    """
    return assets_dir(pdf_path) / _require_plain_name(name)


def remove_assets(pdf_path: Path, names) -> None:
    """尽力删除几个资源文件：缺失、非法名、权限不足都静默跳过。

    删附件是"最好做到"的操作，任何单个失败都不该打断调用方的删除流程。
    """
    directory = assets_dir(pdf_path)
    if isinstance(names, str):      # 单个文件名按一项处理，别逐字符遍历
        names = [names]
    for name in names or ():
        if not is_plain_name(name):
            continue
        try:
            (directory / str(name)).unlink(missing_ok=True)
        except OSError:
            pass
