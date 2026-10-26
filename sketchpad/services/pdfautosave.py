"""PDF 批注自动保存：停笔 debounce 调度器 + 崩溃可恢复的缓存落地。

前半部分（PdfAutoSaver）负责调度：用户在草稿板/PDF 批注上连续作画时，笔迹
每变一次就 notify_changed 一次；本类把这些连续通知合并成一次保存——停笔满
interval_ms 毫秒（默认 2 秒）才真正落地写盘，避免每一笔落点都触发一次磁盘
写入。保存动作是一道闭包（"当前打开的是哪个文档就存哪个"），由接线层在
configure 时注入。

后半部分（cache_dir / cache_path_for / write_cache / read_cache / clear_cache）
是纯函数式的缓存存储：每份 PDF 在 `<base_dir>/autosave/<sha1>.json` 里留一份
批注快照，进程被强杀后可从这份快照恢复。缓存按 PDF 绝对路径的规范化字符串
做键，内容包一层信封（pdf / saved_at / document），写入走同目录 tmp + fsync +
os.replace，绝不留下半截文件。这些函数不创建 QApplication，可在无界面环境调用。
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from sketchpad.core.pdfstore import PdfDocument, PdfFormatError, from_json, to_json

DEBOUNCE_MS = 2000
AUTOSAVE_DIRNAME = "autosave"


class PdfAutoSaver(QObject):
    """停笔 debounce 调度器：连续 notify_changed 合并成一次保存。"""

    saved = Signal()              # 每次保存成功发一次
    failed = Signal(str)          # 保存动作抛异常时发，参数是错误文本

    def __init__(self, parent: QObject | None = None, interval_ms: int = DEBOUNCE_MS) -> None:
        super().__init__(parent)
        self._enabled = False
        self._save: Callable[[], None] | None = None
        self._interval = interval_ms
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)        # 单次触发：连续 restart 只落一次
        self._timer.timeout.connect(self._on_timeout)

    # ---------- 配置 ----------
    def configure(self, enabled: bool, save: Callable[[], None] | None) -> None:
        """设置开关与保存动作；关闭或无保存动作时立即取消待定保存（不等定时器）。"""
        self._enabled = enabled
        self._save = save
        if not (self._enabled and self._save is not None):
            self.cancel()

    # ---------- 笔迹变化 ----------
    def notify_changed(self) -> None:
        """"笔迹变了"的通知；未启用或无保存动作则完全 no-op，否则重启单次定时器。

        连续多次通知只让定时器反复重启——所以只会在最后那次之后满间隔触发一次。
        """
        if not (self._enabled and self._save is not None):
            return
        self._timer.start(self._interval)

    # ---------- 立即保存 ----------
    def flush(self) -> None:
        """有待定保存就立刻执行一次（停表→调用 save→发 saved/failed）；没有待定则 no-op。"""
        if not self.pending:
            return
        self._timer.stop()
        self._do_save()

    # ---------- 取消 ----------
    def cancel(self) -> None:
        """停表、清待定标志，不执行保存。"""
        self._timer.stop()

    # ---------- 间隔 ----------
    def set_interval(self, ms: int) -> None:
        """改 debounce 间隔；后续 notify 立即生效。

        已在跑的这一次按旧定时器继续，再发一次 notify 即按新间隔重新计时。
        """
        self._interval = ms

    # ---------- 只读状态 ----------
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def pending(self) -> bool:
        """是否有一段保存待触发：等价于单次定时器还在跑。"""
        return self._timer.isActive()

    # ---------- 内部 ----------
    def _on_timeout(self) -> None:
        # 单次定时器到点：触发前 isActive 已转 False，故 pending 在保存执行期间即为 False
        self._do_save()

    def _do_save(self) -> None:
        """真正调用保存动作；任何异常都接住转成 failed，绝不逃逸到 Qt 事件循环。"""
        save = self._save
        if save is None:
            return
        try:
            save()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.saved.emit()


# ---------------- 崩溃可恢复缓存（纯存储，不创建 QApplication） ----------------

def _resolved(pdf_path: Path) -> str:
    """PDF 路径的唯一键：先 resolve 成绝对路径，再按平台规范大小写与分隔符。

    normcase 在 Windows 上折小写并把 `/` 换成反斜杠，同一个文件的不同写法
    （相对/绝对、大小写、斜杠方向）拿到同一个键、也就落在同一个缓存文件上。
    """
    return os.path.normcase(os.path.normpath(str(Path(pdf_path).resolve())))


def cache_dir(base_dir: Path) -> Path:
    """缓存目录：`<base_dir>/autosave`（不保证已存在）。"""
    return Path(base_dir) / AUTOSAVE_DIRNAME


def cache_path_for(pdf_path: Path, base_dir: Path) -> Path:
    """该 PDF 的缓存文件：sha1(绝对路径键) + `.json`，同一路径恒定同名。"""
    digest = hashlib.sha1(_resolved(pdf_path).encode("utf-8")).hexdigest()
    return cache_dir(base_dir) / f"{digest}.json"


def write_cache(pdf_path: Path, document: PdfDocument, base_dir: Path) -> Path:
    """把文档写进缓存，返回缓存文件路径。

    信封里的 `document` 是 pdfstore.to_json 的产物再解析回来的嵌套对象（不是
    转义后的字符串），方便人眼/脚本直接看。写入走同目录 `.tmp` + flush + fsync
    + os.replace：进程中途被杀也只会看到旧版或新版，不会看到半截 JSON。
    """
    path = cache_path_for(pdf_path, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pdf": str(Path(pdf_path).resolve()),
        "saved_at": datetime.now().isoformat(),
        # to_json 返回字符串；嵌套存成对象，读回时再 dumps 给 from_json
        "document": json.loads(to_json(document)),
    }
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        # 半截临时文件不留在目录里；旧缓存未被 replace 动过
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


def read_cache(pdf_path: Path, base_dir: Path) -> tuple[PdfDocument, datetime] | None:
    """读缓存；缺失、读不出、JSON 坏了、信封不认、pdf 对不上，一律返回 None。

    返回 (文档, saved_at)。这里对损坏一律静默 None——缓存是"有就用、没有就当
    没存过"的退路，不该让一份坏缓存把打开 PDF 的主流程拦下来。
    """
    path = cache_path_for(pdf_path, base_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    recorded = payload.get("pdf")
    if not isinstance(recorded, str) or _resolved(recorded) != _resolved(pdf_path):
        return None
    saved_at_raw = payload.get("saved_at")
    if not isinstance(saved_at_raw, str):
        return None
    try:
        saved_at = datetime.fromisoformat(saved_at_raw)
    except ValueError:
        return None
    try:
        document = from_json(json.dumps(payload["document"], ensure_ascii=False),
                             expected_page_count=None)
    except (KeyError, TypeError, ValueError, PdfFormatError):
        return None
    return document, saved_at


def clear_cache(pdf_path: Path, base_dir: Path) -> None:
    """删掉该 PDF 的缓存文件（本来就没有也算成功），目录空了顺手收走。"""
    path = cache_path_for(pdf_path, base_dir)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return                      # 删不掉（被占用/无权限）也不把异常抛给调用方
    try:
        cache_dir(base_dir).rmdir()  # 只在目录为空时成功；还有别的缓存就留着
    except OSError:
        pass


def list_caches(base_dir: Path) -> list[tuple[Path, datetime]]:
    """列出所有可读的自动暂存：[(pdf_path, saved_at)]，坏文件静默跳过。

    启动时只用来提示"有残留"，不解析完整文档；真正恢复仍走 read_cache。
    """
    folder = cache_dir(base_dir)
    try:
        files = list(folder.glob("*.json"))
    except OSError:
        return []
    found: list[tuple[Path, datetime]] = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            pdf = payload["pdf"]
            saved_at = datetime.fromisoformat(payload["saved_at"])
        except (OSError, UnicodeDecodeError, json.JSONDecodeError,
                KeyError, TypeError, ValueError):
            continue
        if isinstance(pdf, str) and pdf.strip():
            found.append((Path(pdf), saved_at))
    return found
