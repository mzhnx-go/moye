"""截图历史：落盘、倒序、保留策略、索引损坏兜底。"""
from datetime import datetime, timedelta

from PySide6.QtGui import QColor, QImage

from sketchpad.screenshot.history import ScreenshotHistory


def make_image(width: int = 40, height: int = 30, color: str = "#3366cc") -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def test_添加后落盘且可读回(tmp_path):
    history = ScreenshotHistory(tmp_path / "shots")
    entry = history.add(make_image(), "region")
    assert entry is not None
    path = entry.path(history.root)
    assert path.exists() and path.suffix == ".png"
    assert (entry.width, entry.height, entry.source) == (40, 30, "region")
    loaded = history.load(entry)
    assert loaded is not None and loaded.size() == make_image().size()


def test_新条目排在最前(tmp_path):
    history = ScreenshotHistory(tmp_path / "shots")
    first = history.add(make_image(), "region")
    second = history.add(make_image(), "window")
    entries = history.entries()
    assert [item.id for item in entries] == [second.id, first.id]


def test_超过数量上限删除最旧文件(tmp_path):
    history = ScreenshotHistory(tmp_path / "shots", limit=2)
    first = history.add(make_image(), "region")
    history.add(make_image(), "region")
    history.add(make_image(), "region")
    assert len(history.entries()) == 2
    assert not first.path(history.root).exists()


def test_超过天数删除过期条目(tmp_path):
    day = datetime(2026, 10, 1, 12, 0, 0)
    times = iter([day, day + timedelta(days=40)])
    history = ScreenshotHistory(tmp_path / "shots", limit=100, days=30,
                                clock=lambda: next(times))
    old = history.add(make_image(), "region")
    history.add(make_image(), "window")
    assert len(history.entries()) == 1
    assert not old.path(history.root).exists()


def test_索引损坏当空历史且还能继续写入(tmp_path):
    root = tmp_path / "shots"
    root.mkdir()
    (root / "history.json").write_text("{ not json", encoding="utf-8")
    history = ScreenshotHistory(root)
    assert history.entries() == []
    entry = history.add(make_image(), "region")
    assert entry is not None
    assert len(ScreenshotHistory(root).entries()) == 1


def test_删除和清空(tmp_path):
    history = ScreenshotHistory(tmp_path / "shots")
    first = history.add(make_image(), "region")
    second = history.add(make_image(), "region")
    history.delete(first)
    assert [item.id for item in history.entries()] == [second.id]
    assert not first.path(history.root).exists()
    assert history.clear() == 1
    assert history.entries() == []
    assert not second.path(history.root).exists()


def test_空图不写入(tmp_path):
    history = ScreenshotHistory(tmp_path / "shots")
    assert history.add(QImage(), "region") is None
    assert history.entries() == []
