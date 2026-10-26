"""截图历史：PNG 落盘 + 一份 JSON 索引。

只存本机用户数据目录（`data/screenshots`），不写文件所在目录旁边，也不联网。
默认保留最近 100 条 / 30 天；索引损坏时按“空历史”处理并重建，不阻止截图。
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtGui import QImage

from sketchpad.screenshot.constants import HISTORY_DAYS, HISTORY_LIMIT

INDEX_NAME = "history.json"
VERSION = 1


@dataclass(frozen=True)
class HistoryEntry:
    id: str
    file: str                 # 相对 root 的路径，读的时候再拼绝对路径
    created: str              # ISO 本地时间，秒级
    width: int
    height: int
    source: str = "region"

    def path(self, root: Path) -> Path:
        return Path(root) / self.file


class ScreenshotHistory:
    def __init__(self, root: str | Path, limit: int = HISTORY_LIMIT,
                 days: int = HISTORY_DAYS, clock=None) -> None:
        self.root = Path(root)
        self.limit = max(1, int(limit))
        self.days = max(1, int(days))
        self._clock = clock or datetime.now
        self.index_path = self.root / INDEX_NAME
        self._entries: list[HistoryEntry] | None = None

    # ---------- 读写 ----------

    def entries(self) -> list[HistoryEntry]:
        if self._entries is None:
            self._entries = self._load()
        return list(self._entries)

    def add(self, image: QImage, source: str = "region") -> HistoryEntry | None:
        if image is None or image.isNull():
            return None
        now = self._clock()
        folder = self.root / now.strftime("%Y%m%d")
        folder.mkdir(parents=True, exist_ok=True)
        identifier = uuid.uuid4().hex
        name = f"{now.strftime('%H%M%S')}-{identifier[:8]}.png"
        path = folder / name
        if not image.save(str(path), "PNG"):
            return None
        entry = HistoryEntry(
            id=identifier,
            file=path.relative_to(self.root).as_posix(),
            created=now.isoformat(timespec="seconds"),
            width=image.width(),
            height=image.height(),
            source=source,
        )
        entries = [entry] + self.entries()
        self._entries = entries
        self._trim(now)
        self._save()
        return entry

    def load(self, entry: HistoryEntry) -> QImage | None:
        image = QImage(str(entry.path(self.root)))
        return None if image.isNull() else image

    def delete(self, entry: HistoryEntry) -> bool:
        try:
            entry.path(self.root).unlink(missing_ok=True)
        except OSError:
            pass
        self._entries = [item for item in self.entries() if item.id != entry.id]
        self._save()
        return True

    def clear(self) -> int:
        count = 0
        for entry in self.entries():
            try:
                entry.path(self.root).unlink(missing_ok=True)
                count += 1
            except OSError:
                pass
        self._entries = []
        self._save()
        return count

    # ---------- 内部 ----------

    def _load(self) -> list[HistoryEntry]:
        if not self.index_path.exists():
            return []
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            raw = payload.get("entries", []) if isinstance(payload, dict) else []
        except (OSError, ValueError):
            return []
        entries: list[HistoryEntry] = []
        for item in raw:
            try:
                entry = HistoryEntry(**item)
            except TypeError:
                continue
            if entry.path(self.root).exists():
                entries.append(entry)
        entries.sort(key=lambda item: item.created, reverse=True)
        return entries

    def _trim(self, now: datetime) -> None:
        cutoff = now - timedelta(days=self.days)
        kept: list[HistoryEntry] = []
        for index, entry in enumerate(self.entries()):
            try:
                created = datetime.fromisoformat(entry.created)
            except ValueError:
                created = now
            expired = created < cutoff
            if index >= self.limit or expired:
                try:
                    entry.path(self.root).unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            kept.append(entry)
        self._entries = kept

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": VERSION,
            "entries": [asdict(entry) for entry in self.entries()],
        }
        temp = self.index_path.with_suffix(".json.tmp")
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            os.replace(temp, self.index_path)
        except OSError:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
