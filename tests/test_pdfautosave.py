"""PdfAutoSaver 离屏测试 + 自动保存缓存（autosave 目录）的往返/损坏/清理测试。

调度器部分用短间隔（interval_ms=50）+ QTest.qWait(150) 顶替真等 2 秒；保存动作
一律用计数闭包，不碰真实磁盘。缓存部分只在 tmp_path 操作，不需要 QApplication。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sketchpad.core.pdfstore import Page, PdfDocument
from sketchpad.core.strokes import Stroke
from sketchpad.services.pdfautosave import (
    PdfAutoSaver,
    cache_dir,
    cache_path_for,
    clear_cache,
    list_caches,
    read_cache,
    write_cache,
)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_repeated_notify_fires_once(qapp):
    """连续多次 notify_changed 只触发一次保存（debounce 合并）。"""
    calls: list[int] = []
    saver = PdfAutoSaver(interval_ms=50)
    saver.configure(True, lambda: calls.append(1))
    for _ in range(5):
        saver.notify_changed()
    QTest.qWait(150)
    assert len(calls) == 1
    assert saver.pending is False


def test_notify_when_disabled_is_noop(qapp):
    """未启用时 notify 完全不保存。"""
    calls: list[int] = []
    saver = PdfAutoSaver(interval_ms=50)
    saver.configure(False, lambda: calls.append(1))
    saver.notify_changed()
    QTest.qWait(150)
    assert calls == []
    assert saver.enabled is False


def test_configure_false_cancels_pending(qapp):
    """configure(False) 立即取消已安排的待定保存，定时器不再触发。"""
    calls: list[int] = []
    saver = PdfAutoSaver(interval_ms=5000)   # 间隔够大，正常不会在等待内触发
    saver.configure(True, lambda: calls.append(1))
    saver.notify_changed()
    assert saver.pending is True
    saver.configure(False, None)
    assert saver.pending is False
    QTest.qWait(200)
    assert calls == []


def test_flush_saves_immediately_once(qapp):
    """flush() 立即保存一次，且后续定时器不应重复触发。"""
    calls: list[int] = []
    saver = PdfAutoSaver(interval_ms=5000)
    saver.configure(True, lambda: calls.append(1))
    saver.notify_changed()
    assert saver.pending is True
    saver.flush()
    assert calls == [1]
    assert saver.pending is False
    QTest.qWait(200)          # 定时器本要 5 秒后才到，flush 后不得重复
    assert calls == [1]


def test_cancel_does_not_save(qapp):
    """cancel() 后不保存，但恢复启用后仍能正常保存。"""
    calls: list[int] = []
    saver = PdfAutoSaver(interval_ms=5000)
    saver.configure(True, lambda: calls.append(1))
    saver.notify_changed()
    assert saver.pending is True
    saver.cancel()
    assert saver.pending is False
    QTest.qWait(200)
    assert calls == []
    saver.set_interval(50)    # 把间隔调回测试用的短值
    saver.notify_changed()    # 重新启用后还能用
    QTest.qWait(150)
    assert len(calls) == 1


def test_save_exception_emits_failed_and_recovers(qapp):
    """save 抛异常：failed 带文本、进程不崩、后续 notify 仍能正常工作。"""
    messages: list[str] = []
    saver = PdfAutoSaver(interval_ms=50)
    saver.failed.connect(lambda m: messages.append(m))

    def boom() -> None:
        raise RuntimeError("磁盘写满")

    saver.configure(True, boom)
    saver.notify_changed()
    QTest.qWait(150)
    assert messages == ["磁盘写满"]

    calls: list[int] = []
    saver.configure(True, lambda: calls.append(1))
    saver.notify_changed()
    QTest.qWait(150)
    assert len(calls) == 1


def test_state_transitions(qapp):
    """enabled / pending 状态转换正确。"""
    saver = PdfAutoSaver(interval_ms=5000)
    assert saver.enabled is False
    assert saver.pending is False

    saver.configure(True, lambda: None)
    assert saver.enabled is True
    assert saver.pending is False

    saver.notify_changed()
    assert saver.pending is True
    saver.cancel()
    assert saver.pending is False
    assert saver.enabled is True


def test_set_interval_takes_effect(qapp):
    """set_interval 改变后续 notify 的触发节奏。"""
    calls: list[int] = []
    saver = PdfAutoSaver(interval_ms=50)
    saver.configure(True, lambda: calls.append(1))
    saver.set_interval(5000)
    saver.notify_changed()
    QTest.qWait(150)
    assert calls == []            # 已改成 5 秒，150ms 内不触发
    assert saver.pending is True

    saver.set_interval(50)
    saver.notify_changed()        # 按新间隔重新计时
    QTest.qWait(150)
    assert len(calls) == 1
    assert saver.pending is False


# ---------------- 自动保存缓存（storage 半） ----------------

def _doc() -> PdfDocument:
    """2 页共 3 笔；坐标只留 2 位小数（to_json 只保留 2 位，往返才严格相等）。"""
    return PdfDocument(
        [
            Page(
                595.28,
                841.89,
                0,
                [
                    Stroke([(10.0, 20.5), (30.25, 40.0)], "#1a1a1a", 3.0),
                    Stroke([(0.0, 0.0), (595.28, 841.89)], "#c0392b", 12.0),
                ],
            ),
            Page(
                841.89,
                595.28,
                90,
                [Stroke([(5.5, 6.25), (7.0, 8.5)], "#2e86de", 1.0, "eraser")],
            ),
        ]
    )


def test_cache_path_is_stable_for_same_pdf(tmp_path):
    """相对/绝对、`..` 绕路都指向同一份缓存文件；不同 PDF 落到不同文件。"""
    pdf = tmp_path / "讲义.pdf"
    detour = tmp_path / "sub" / ".." / "讲义.pdf"
    assert cache_path_for(pdf, tmp_path) == cache_path_for(detour, tmp_path)
    assert cache_path_for(pdf, tmp_path) != cache_path_for(tmp_path / "another.pdf", tmp_path)
    assert cache_path_for(pdf, tmp_path).parent == tmp_path / "autosave"
    assert cache_path_for(pdf, tmp_path).suffix == ".json"
    assert cache_dir(tmp_path) == tmp_path / "autosave"


def test_write_then_read_roundtrip(tmp_path):
    """写→读还原页数与笔迹，saved_at 解析成 datetime。"""
    pdf = tmp_path / "讲义.pdf"
    path = write_cache(pdf, _doc(), tmp_path)
    assert path.exists()
    assert path.parent == tmp_path / "autosave"

    got = read_cache(pdf, tmp_path)
    assert got is not None
    document, saved_at = got
    assert isinstance(saved_at, datetime)
    assert abs((datetime.now() - saved_at).total_seconds()) < 60

    original = _doc()
    assert len(document.pages) == len(original.pages) == 2
    for got_page, want_page in zip(document.pages, original.pages):
        assert (got_page.width, got_page.height, got_page.rotation) == (
            want_page.width, want_page.height, want_page.rotation)
        assert len(got_page.strokes) == len(want_page.strokes)
        for got_s, want_s in zip(got_page.strokes, want_page.strokes):
            assert got_s.points == want_s.points
            assert (got_s.color, got_s.width, got_s.tool) == (
                want_s.color, want_s.width, want_s.tool)


def test_read_cache_for_other_pdf_returns_none(tmp_path):
    """信封里记录的 pdf 与查询路径不一致 → None，绝不把 A 的批注当成 B 的。"""
    write_cache(tmp_path / "a.pdf", _doc(), tmp_path)
    assert read_cache(tmp_path / "b.pdf", tmp_path) is None


def test_read_cache_missing_returns_none(tmp_path):
    """从没写过缓存 → None（缓存只是退路，缺失不是错误）。"""
    assert read_cache(tmp_path / "nope.pdf", tmp_path) is None


def test_read_cache_corrupt_json_returns_none(tmp_path):
    """坏 JSON / 非对象根 / 缺字段 → None，不抛异常。"""
    pdf = tmp_path / "bad.pdf"
    path = cache_path_for(pdf, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for text in ('{"pdf": ', "[]", '{"pdf":"x"}', "not json at all"):
        path.write_text(text, encoding="utf-8")
        assert read_cache(pdf, tmp_path) is None


def test_clear_cache_removes_file_and_empty_dir(tmp_path):
    """删缓存：文件消失、空 autosave 目录一并收走；对不存在的缓存再删也不报错。"""
    pdf = tmp_path / "讲义.pdf"
    path = write_cache(pdf, _doc(), tmp_path)
    assert path.exists()

    clear_cache(pdf, tmp_path)
    assert not path.exists()
    assert not cache_dir(tmp_path).exists()

    clear_cache(pdf, tmp_path)    # 幂等：本来就没有也算成功
    assert not path.exists()


def test_clear_cache_keeps_dir_with_other_caches(tmp_path):
    """同目录还有别人的缓存时，只删自己那份，目录留着。"""
    pdf_a, pdf_b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    path_a = write_cache(pdf_a, _doc(), tmp_path)
    path_b = write_cache(pdf_b, _doc(), tmp_path)

    clear_cache(pdf_a, tmp_path)
    assert not path_a.exists()
    assert path_b.exists()
    assert cache_dir(tmp_path).is_dir()


def test_list_caches_returns_valid_entries_and_skips_corrupt(tmp_path):
    """启动提示只列可读信封；坏 JSON 静默跳过。"""
    pdf_a, pdf_b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    write_cache(pdf_a, _doc(), tmp_path)
    write_cache(pdf_b, _doc(), tmp_path)
    (cache_dir(tmp_path) / "broken.json").write_text("{not json", encoding="utf-8")

    found = list_caches(tmp_path)
    assert {p.resolve() for p, _ in found} == {pdf_a.resolve(), pdf_b.resolve()}
    assert all(isinstance(saved_at, datetime) for _, saved_at in found)
    assert list_caches(tmp_path / "missing") == []


def test_write_leaves_no_tmp_file(tmp_path):
    """成功写入后目录里只有 .json，不留 `.tmp` 半成品。"""
    write_cache(tmp_path / "讲义.pdf", _doc(), tmp_path)
    names = sorted(p.name for p in cache_dir(tmp_path).iterdir())
    assert names and all(not name.endswith(".tmp") for name in names)
