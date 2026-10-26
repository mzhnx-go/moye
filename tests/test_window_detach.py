"""标签拖出成独立窗口（WPS 式）：内容跟走、关闭互不影响。

回归背景（2026-10-24 用户报告）：拖出后新窗口内容为空、关闭新窗把原窗一起关。
两处都在真实窗口下出现，offscreen 下先把数据链路钉住：会话/视图不重建、
registry 不误删主窗；重绘保险（_repaint_view）只做冒烟。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sketchpad.app import AppController, MainWindow
from sketchpad.core.strokes import Stroke
from sketchpad.ui.pdfview import PdfView

pytestmark = pytest.mark.offscreen

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def workspace(tmp_path):
    controller = AppController(tmp_path)
    win = MainWindow(controller)
    win.show()
    QTest.qWaitForWindowExposed(win)
    _ALIVE.append(win)
    yield win
    win._quitting = True
    win.close()
    win.window_registry.unregister(win) if win.window_registry else None


@pytest.fixture
def sample_pdf(tmp_path):
    path = tmp_path / "detach.pdf"
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc[0].insert_text((72.0, 72.0), "detach me")
    doc.save(str(path))
    doc.close()
    return path


def test_detach_moves_content_and_keeps_primary_alive(qapp, workspace, sample_pdf):
    win = workspace
    assert win._enter_document(sample_pdf) is True
    QTest.qWait(120)
    view = win.pdf_view
    stroke = Stroke([(10.0, 10.0), (80.0, 60.0)], "#ff453a", 2.0)
    view.document.pages[0].strokes.append(stroke)
    entry = win.tabs.current_entry
    assert entry is not None and not entry.draft

    target = win.window_registry.detach(
        win, entry, QPoint(win.x() + 80, win.y() + 60))
    assert target is not None
    _ALIVE.append(target)
    try:
        assert len(target.tabs.entries) == 1
        adopted = target.tabs.current_entry
        assert adopted is not None
        # 同一个 view / pane 实例：不重读 PDF、状态跟走
        assert adopted.sessions[0].view is view
        assert view.document.pages[0].strokes == [stroke]
        assert target.tabs.current_entry.page is entry.page
        # 原窗还活着、registry 里还是主窗
        assert win.isVisible() is True
        assert win.window_registry.find(win.window_id) is win

        # 关掉新窗：主窗必须毫发无损（用户报告的"连关"回归）
        target.close()
        QTest.qWait(120)
        assert win.isVisible() is True
        assert win.window_registry.find(win.window_id) is win
        assert win.window_registry.find(target.window_id) is None
    finally:
        pass
