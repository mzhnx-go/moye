"""PDF 阅读位置记忆离屏测试：settings 清洗、pdfview 取/恢复、app 关闭/切换接线。

样例 PDF 全在 tmp_path 现造，绝不碰 data/ 或真实用户文件；对话框一律 monkeypatch。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import time
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import main
from sketchpad.core import settings as settings_store
from sketchpad.core.settings import (POSITION_LIMIT, POSITION_TTL_DAYS, Settings,
                                     load, pdf_position, prune_pdf_positions,
                                     remember_pdf_position, save)
from sketchpad.ui.pdfview import PdfView

SIZES = ((595.0, 842.0), (595.0, 842.0), (595.0, 842.0))
TTL_SECONDS = POSITION_TTL_DAYS * 86400

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_pdf(path, sizes=SIZES):
    doc = pymupdf.open()
    for width, height in sizes:
        doc.new_page(width=width, height=height)
    doc.save(str(path))
    doc.close()
    return path


def _key(path) -> str:
    """与 settings 内部一致的位置键，供测试比对。"""
    return os.path.normcase(os.path.normpath(str(Path(path).resolve())))


@pytest.fixture
def pdf(tmp_path):
    return _make_pdf(tmp_path / "sample.pdf")


# ---------- settings：解析 / 清洗 / 上限 ----------

def test_position_round_trip(tmp_path, pdf):
    s = Settings()
    remember_pdf_position(s, pdf, page=2, offset=0.25, zoom=1.5, auto_fit=False)
    assert len(s.pdf_positions) == 1
    assert next(iter(s.pdf_positions)) == _key(pdf)

    path = tmp_path / "settings.json"
    save(path, s)
    loaded = load(path)
    assert loaded == s
    entry = pdf_position(loaded, pdf)
    assert entry is not None
    assert (entry["page"], entry["offset"], entry["zoom"], entry["auto_fit"]) == (
        2, 0.25, 1.5, False)
    assert entry["saved_at"] > 0


def test_missing_files_dropped_on_load_and_remember(tmp_path, pdf):
    s = Settings()
    remember_pdf_position(s, pdf, page=0, offset=0.5, zoom=1.0, auto_fit=True)
    assert pdf_position(s, pdf) is not None

    pdf.unlink()
    assert pdf_position(s, pdf) is None          # 读取时文件已不在
    prune_pdf_positions(s)
    assert s.pdf_positions == {}

    remember_pdf_position(s, pdf, page=0, offset=0.5, zoom=1.0, auto_fit=True)
    assert s.pdf_positions == {}                 # 不存在的文件不记

    s.pdf_positions = {_key(pdf): {"page": 0, "offset": 0.5, "zoom": 1.0,
                                   "auto_fit": True, "saved_at": time.time()}}
    path = tmp_path / "settings.json"
    save(path, s)
    assert load(path).pdf_positions == {}        # 载入时丢掉死条目


def test_expired_positions_dropped(tmp_path, pdf):
    s = Settings()
    old = time.time() - TTL_SECONDS - 60
    remember_pdf_position(s, pdf, page=1, offset=0.5, zoom=1.5,
                          auto_fit=False, now=old)
    assert pdf_position(s, pdf) is None          # 超过 TTL 不返回
    prune_pdf_positions(s)
    assert s.pdf_positions == {}

    remember_pdf_position(s, pdf, page=1, offset=0.5, zoom=1.5,
                          auto_fit=False, now=old)
    path = tmp_path / "settings.json"
    save(path, s)
    assert load(path).pdf_positions == {}        # 落盘再读也不回来


def test_malformed_entries_dropped_without_raising(tmp_path):
    good = _make_pdf(tmp_path / "good.pdf")
    clamp = _make_pdf(tmp_path / "clamp.pdf")
    now = time.time()
    valid = {"page": 0, "offset": 0.5, "zoom": 1.0,
             "auto_fit": True, "saved_at": now}
    positions = {
        str(good): dict(valid, page=1, zoom=1.5, auto_fit=False),
        # 越界数值要钳住而不是丢弃
        str(clamp): dict(valid, offset=7.5, zoom=99.0),
        "relative.pdf": dict(valid),
        str(tmp_path / "missing.pdf"): dict(valid),
    }
    bads = [
        [], "not a dict", 7, None,
        dict(valid, page=True),                    # bool 冒充 int
        dict(valid, page=-1),
        dict(valid, page="1"),
        dict(valid, offset="0.5"),
        dict(valid, zoom=None),
        dict(valid, auto_fit=1),                   # 只认真布尔
        dict(valid, offset=float("nan")),
        dict(valid, zoom=float("inf")),
        dict(valid, saved_at=float("inf")),
        dict(valid, saved_at=now - TTL_SECONDS - 60),
    ]
    for index, bad in enumerate(bads):
        path = tmp_path / f"bad{index}.pdf"
        path.write_bytes(b"x")
        positions[str(path)] = bad
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"pdf_positions": positions}), encoding="utf-8")

    loaded = load(path)
    assert set(loaded.pdf_positions) == {_key(good), _key(clamp)}
    assert loaded.pdf_positions[_key(good)]["page"] == 1
    clamped = loaded.pdf_positions[_key(clamp)]
    assert clamped["offset"] == 1.0
    assert clamped["zoom"] == settings_store.POSITION_MAX_ZOOM


@pytest.mark.parametrize("raw", ['"nope"', "[]", "7", "null"])
def test_bad_positions_container_gives_empty_dict(tmp_path, raw):
    path = tmp_path / "settings.json"
    path.write_text('{"pdf_positions": ' + raw + "}", encoding="utf-8")
    assert load(path).pdf_positions == {}


def test_positions_capped_to_limit(tmp_path):
    now = time.time()
    s = Settings()
    for index in range(POSITION_LIMIT + 5):
        path = tmp_path / f"p{index:02d}.pdf"
        path.write_bytes(b"x")
        remember_pdf_position(s, path, page=index, offset=0.0, zoom=1.0,
                              auto_fit=True, now=now + index)
    assert len(s.pdf_positions) == POSITION_LIMIT
    assert _key(tmp_path / "p00.pdf") not in s.pdf_positions
    assert _key(tmp_path / f"p{POSITION_LIMIT + 4:02d}.pdf") in s.pdf_positions

    path = tmp_path / "settings.json"
    save(path, s)
    assert len(load(path).pdf_positions) == POSITION_LIMIT


# ---------- pdfview：取位置 / 恢复 ----------

@pytest.fixture
def view(qapp):
    v = PdfView(Settings())
    v.resize(600, 800)
    v.show()
    QTest.qWaitForWindowExposed(v)
    _ALIVE.append(v)
    yield v
    v.close_document()
    v.close()


def test_current_position_plausible_and_restore_matches(view, qapp, pdf):
    view.open(pdf)
    view.goto_page(1)
    view.set_zoom(1.5)
    pos = view.current_position()
    assert pos is not None
    assert pos["page"] == 1
    assert pos["zoom"] == pytest.approx(1.5)
    assert pos["auto_fit"] is False
    assert 0.0 <= pos["offset"] <= 1.0

    other = PdfView(Settings())
    other.resize(600, 800)
    other.show()
    QTest.qWaitForWindowExposed(other)
    _ALIVE.append(other)
    other.open(pdf)
    other.restore_position(pos["page"], pos["offset"], pos["zoom"], pos["auto_fit"])
    QTest.qWait(50)

    restored = other.current_position()
    assert restored["page"] == pos["page"]
    assert restored["zoom"] == pytest.approx(pos["zoom"])
    assert restored["auto_fit"] is False
    assert restored["offset"] == pytest.approx(pos["offset"], abs=0.05)


def test_restore_clamps_out_of_range_page(view, pdf):
    view.open(pdf)
    view.restore_position(99, 0.5, 1.0, False)
    assert view.current_page == 2
    assert view.zoom == pytest.approx(1.0)
    view.restore_position(-7, 0.5, 1.0, True)
    assert view.current_page == 0
    assert view.auto_fit is True


def test_position_none_without_document_and_restore_is_noop(view):
    assert view.current_position() is None
    view.restore_position(0, 0.5, 1.0, False)      # 不应抛
    assert view.current_position() is None


# ---------- app：关闭 / 切换 / 退出接线 ----------

@pytest.fixture
def win(qapp, tmp_path):
    ctrl = main.AppController(tmp_path / "cfg")
    w = main.MainWindow(ctrl)
    w._confirm_unsaved = lambda action: QMessageBox.Discard   # 离屏不弹模态框
    w.show()
    QTest.qWaitForWindowExposed(w)
    _ALIVE.append(w)
    yield w
    w.scratch.hide()
    if w.settings_dialog is not None:
        w.settings_dialog.close()
    w.pdf_view.close_document()
    w._quitting = True
    w.close()


def test_enter_draft_records_even_when_discarded_and_reopen_restores(win, pdf):
    assert win._enter_document(pdf) is True
    win.pdf_view.goto_page(1)
    win.pdf_view.set_zoom(1.5)
    recorded = win.pdf_view.current_position()

    win._dirty = True                  # 模拟未保存批注：flush 会问 Save/Discard
    win._enter_draft()
    assert win.mode == "draft"
    assert pdf.exists()

    entry = pdf_position(win.controller.settings, pdf)
    assert entry is not None
    assert entry["page"] == recorded["page"] == 1
    assert entry["zoom"] == pytest.approx(1.5)
    assert entry["auto_fit"] is False
    # 配置确实落盘（设置文件里也能读到）
    assert pdf_position(load(win.controller.settings_path), pdf) is not None

    assert win._enter_document(pdf) is True
    QTest.qWait(60)
    assert win.pdf_view.current_page == 1
    assert win.pdf_view.zoom == pytest.approx(1.5)
    assert win.pdf_view.auto_fit is False


def test_switching_pdfs_records_old_and_keeps_both_positions(win, tmp_path):
    a = _make_pdf(tmp_path / "a.pdf")
    b = _make_pdf(tmp_path / "b.pdf", ((400.0, 600.0),) * 3)
    assert win._enter_document(a) is True
    win.pdf_view.set_zoom(2.0)
    win.pdf_view.goto_page(2)

    assert win._enter_document(b) is True
    entry_a = pdf_position(win.controller.settings, a)
    assert entry_a is not None
    assert entry_a["page"] == 2
    assert entry_a["zoom"] == pytest.approx(2.0)
    assert pdf_position(win.controller.settings, b) is None   # B 还没关
    assert pdf_position(load(win.controller.settings_path), a) is not None

    win.pdf_view.set_zoom(1.25)
    win.pdf_view.goto_page(1)
    win._enter_draft()
    entry_b = pdf_position(win.controller.settings, b)
    assert entry_b is not None
    assert entry_b["page"] == 1
    assert entry_b["zoom"] == pytest.approx(1.25)
    # 两份 PDF 各记各的（A 的记录没被 B 覆盖）
    assert pdf_position(win.controller.settings, a)["page"] == 2


def test_quit_records_position_even_when_discarded(win, pdf):
    assert win._enter_document(pdf) is True
    win.pdf_view.goto_page(1)
    win.pdf_view.set_zoom(1.5)
    win._dirty = True                  # 退出时选「放弃」也必须留位置

    win.quit_app()

    entry = pdf_position(win.controller.settings, pdf)
    assert entry is not None
    assert entry["page"] == 1
    assert pdf_position(load(win.controller.settings_path), pdf) is not None


def test_page_turn_writes_position_without_closing_document(win, pdf):
    """翻页立即落盘：不依赖关闭文档，强杀进程后也能从这一页继续。"""
    assert win._enter_document(pdf) is True
    win.pdf_view.goto_page(1)
    QTest.qWait(30)
    entry = pdf_position(load(win.controller.settings_path), pdf)
    assert entry is not None
    assert entry["page"] == 1


def test_scroll_debounce_writes_position(win, pdf):
    """页内滚动只改 offset：停 600ms 后也要落盘。"""
    assert win._enter_document(pdf) is True
    bar = win.pdf_view.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum() // 4)
    QTest.qWait(800)
    entry = pdf_position(load(win.controller.settings_path), pdf)
    assert entry is not None
    assert entry["offset"] > 0.01


def test_hide_flushes_position_immediately(win, pdf):
    """收进托盘/隐藏窗口时立即写盘，不等 600ms 去抖。"""
    assert win._enter_document(pdf) is True
    bar = win.pdf_view.verticalScrollBar()
    bar.setValue(bar.maximum() // 3)
    win.hide()                              # 立即 hide，不给去抖计时器机会
    entry = pdf_position(load(win.controller.settings_path), pdf)
    assert entry is not None
    assert entry["offset"] > 0.01
