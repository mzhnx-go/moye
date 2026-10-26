import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSizePolicy

import main
from sketchpad.ui import theme


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    controller = main.AppController(tmp_path / "cfg")
    window = main.MainWindow(controller)
    window.resize(1100, 760)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    window._quitting = True
    window.close()


def test_status_row_is_flush_plain(win):
    QTest.qWait(40)
    bar = win.statusBar()
    assert win.status_pill.objectName() == "StatusPill"
    assert win.status_pill.parent() is bar
    assert bar.isSizeGripEnabled() is False
    assert win.status_pill.width() >= win.width() - 40
    for label in (win.message_label, win.save_label, win.status_label):
        assert win.status_pill.isAncestorOf(label)


def test_status_bar_has_no_top_divider():
    block = theme.QSS.split("QStatusBar {", 1)[1].split("}", 1)[0]
    assert theme.WINDOW_BG in block
    assert "border-top" not in block


def test_status_row_qss_is_plain(win):
    block = theme.QSS.split("QFrame#StatusPill", 1)[1].split("}", 1)[0]
    assert "transparent" in block
    assert "border: none" in block
    assert "border-radius" not in block


def test_status_message_still_reaches_pill(win):
    win._note("测试提示")
    assert win.message_label.text() == "测试提示"
    assert win.status_pill.isAncestorOf(win.message_label)


def test_durable_status_keeps_full_width_with_long_note(win):
    """长提示只能挤自己，页面/缩放/笔迹这类持久文字必须保持完整宽度。"""
    QTest.qWait(40)
    assert win.status_pill.width() > 0
    assert win.save_label.isHidden() is True

    status_text = "第 12/128 页 · 缩放 125% · 笔迹 1024"
    win.status_label.setText(status_text)
    win._note("发现 3 份未恢复的批注暂存；打开对应 PDF 时会提示恢复，"
              "这条提示故意写得很长，用来把临时消息那一列撑到极限")
    QTest.qWait(60)

    assert win.status_label.text() == status_text
    assert (win.status_label.sizePolicy().horizontalPolicy()
            == QSizePolicy.Fixed)
    assert win.status_label.width() >= win.status_label.sizeHint().width()
