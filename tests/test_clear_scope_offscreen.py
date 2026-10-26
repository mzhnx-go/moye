"""清屏作用域离屏测试：文档模式只清当前页且一步可撤销，三条入口必须一致。

三条入口在 app.py 里都汇到 `_clear_active`：
  * 工具栏垃圾桶 → Toolbox.clearRequested → app._clear_active
  * 菜单「清空」→ Menubar.actionTriggered("clear_canvas") → app._on_menu_action
  * 配置的清屏键 → QApplication 事件过滤器 → app.eventFilter → app._clear_active

本文件只做验证，不改生产代码。样例 PDF 在 tmp_path 现造；对话框一律不弹。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import main
from sketchpad.core.strokes import Stroke

# 三页竖版：第一页 842 pt 高，保证开局视口中心落在第 1 页
SIZES = ((595.0, 842.0), (500.0, 700.0), (420.0, 900.0))

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


@pytest.fixture
def pdf(tmp_path):
    return _make_pdf(tmp_path / "sample.pdf")


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    ctrl = main.AppController(tmp_path / "cfg")
    w = main.MainWindow(ctrl)
    mono = monkeypatch.setattr      # 离屏不弹模态框：有未保存批注一律「放弃」
    mono(w, "_confirm_unsaved", lambda action: QMessageBox.Discard)
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


def _draw_draft_stroke(win, start=(100, 100), end=(200, 200)):
    """草稿画布：屏幕坐标直接落笔，得一条两点笔迹。"""
    QTest.mousePress(win.view.viewport(), Qt.LeftButton, pos=QPoint(*start))
    QTest.mouseRelease(win.view.viewport(), Qt.LeftButton, pos=QPoint(*end))


def _open_with_strokes(win, pdf):
    """进文档模式，给三页各放一条可辨认的笔迹；返回文档与三条笔迹的引用。"""
    assert win.mode == "draft"
    assert win._enter_document(pdf) is True
    doc = win.pdf_view.document
    assert doc is not None and len(doc.pages) == 3
    strokes = []
    for index, page in enumerate(doc.pages):
        stroke = Stroke([(10.0 + index, 20.0), (30.0 + index, 40.0)],
                        "#c0392b", 3.0)
        page.strokes.append(stroke)
        strokes.append(stroke)
    return doc, strokes


def _focus_pdf_view(win):
    """离屏下焦点要显式给视图：应用级过滤器只看「焦点在本窗口」。"""
    win.activateWindow()
    win.pdf_view.setFocus()
    QTest.qWait(20)
    assert QApplication.focusWidget() is win.pdf_view


def _assert_only_page_cleared(doc, strokes, index):
    """目标页清空，其余页原样（数量与对象身份都没变）。"""
    assert doc.pages[index].strokes == []
    for other, stroke in enumerate(strokes):
        if other == index:
            continue
        assert doc.pages[other].strokes == [stroke]
        assert doc.pages[other].strokes[0] is stroke


def _assert_all_pages_restored(doc, strokes):
    for index, stroke in enumerate(strokes):
        assert doc.pages[index].strokes == [stroke]
        assert doc.pages[index].strokes[0] is stroke


# ---------- 三条入口：文档模式只清当前页，一步撤销 ----------

def test_toolbar_trash_clears_only_current_page_and_undoes_in_one_step(win, pdf):
    doc, strokes = _open_with_strokes(win, pdf)
    win.pdf_view.goto_page(1)
    assert win.pdf_view.current_page == 1
    depth = win.pdf_view.history.depth

    win.toolbox.clear_button.click()
    _assert_only_page_cleared(doc, strokes, 1)
    assert win.pdf_view.history.depth == depth + 1      # 只压了一条 ClearPage

    win.pdf_view.history.pop_undo()
    _assert_all_pages_restored(doc, strokes)
    assert win.pdf_view.history.depth == depth


def test_menu_clear_action_clears_only_current_page_and_undoes_in_one_step(win, pdf):
    doc, strokes = _open_with_strokes(win, pdf)
    win.pdf_view.goto_page(1)
    assert win.pdf_view.current_page == 1
    depth = win.pdf_view.history.depth

    win.menubar.clear_canvas_action.trigger()
    _assert_only_page_cleared(doc, strokes, 1)
    assert win.pdf_view.history.depth == depth + 1

    win.pdf_view.history.pop_undo()
    _assert_all_pages_restored(doc, strokes)
    assert win.pdf_view.history.depth == depth


def test_configured_clear_key_clears_only_current_page_and_undoes_in_one_step(win, pdf):
    doc, strokes = _open_with_strokes(win, pdf)
    assert win.view.clear_key == "E"                    # 配置默认清屏键
    # 清屏键只有一条路径：草稿画布内建的 QShortcut 必须停用（app.py 进文档前关掉），
    # 键盘 E 只能由应用级 eventFilter 转发，pdf_view 自身完全不处理 E
    assert win.view._clear_shortcut is not None
    assert win.view._clear_shortcut.isEnabled() is False
    win.pdf_view.goto_page(1)
    assert win.pdf_view.current_page == 1
    depth = win.pdf_view.history.depth

    # 真实按键路径：QTest 把 KeyPress 投给视图 → 应用级 eventFilter 接管
    _focus_pdf_view(win)
    QTest.keyClick(win.pdf_view, Qt.Key_E)

    _assert_only_page_cleared(doc, strokes, 1)
    assert win.pdf_view.history.depth == depth + 1

    win.pdf_view.history.pop_undo()
    _assert_all_pages_restored(doc, strokes)
    assert win.pdf_view.history.depth == depth


def test_clear_key_reaches_view_even_when_focus_is_not_on_the_view(win, pdf):
    """焦点被工具栏滚动条抢走时，清屏键仍要落到当前文档视图（防回归）。"""
    doc, strokes = _open_with_strokes(win, pdf)
    win.pdf_view.goto_page(1)
    assert win.pdf_view.current_page == 1
    win.activateWindow()
    win.resize(main.MIN_W, 520)                         # 窄窗：工具栏出现横向滚动条
    QTest.qWait(50)
    bar = win.toolbar_scroll.horizontalScrollBar()
    assert bar.isVisible() is True
    bar.setFocus()
    QTest.qWait(20)
    assert QApplication.focusWidget() is bar

    QTest.keyClick(bar, Qt.Key_E)
    _assert_only_page_cleared(doc, strokes, 1)

    win.pdf_view.history.pop_undo()
    _assert_all_pages_restored(doc, strokes)


# ---------- 草稿模式：同一条入口清整块画布（既有行为不回归） ----------

def test_draft_clear_still_clears_whole_canvas_and_undoes_in_one_step(win):
    _draw_draft_stroke(win, (100, 100), (160, 160))
    _draw_draft_stroke(win, (220, 220), (280, 280))
    assert len(win.controller.document.strokes) == 2
    depth = win.view.history.depth

    win.toolbox.clear_button.click()
    assert win.controller.document.strokes == []
    assert win.view.history.depth == depth + 1

    win.view.history.pop_undo()
    assert len(win.controller.document.strokes) == 2
    assert win.view.history.depth == depth


def test_draft_clear_key_still_clears_whole_canvas(win):
    _draw_draft_stroke(win, (100, 100), (160, 160))
    _draw_draft_stroke(win, (220, 220), (280, 280))
    assert len(win.controller.document.strokes) == 2

    win.activateWindow()
    win.view.setFocus()
    QTest.qWait(20)
    assert QApplication.focusWidget() is win.view
    QTest.keyClick(win.view, Qt.Key_E)

    assert win.controller.document.strokes == []
    win.view.history.pop_undo()
    assert len(win.controller.document.strokes) == 2
