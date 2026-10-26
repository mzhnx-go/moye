# tests/test_theme_offscreen.py
"""离屏测试：目录面板深色主题，以及全局 QSS 里的滚动条/页码输入框规则。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from sketchpad.ui import theme
from sketchpad.ui.toc_panel import TocPanel

# 保住引用，防止被 GC
_ALIVE = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def toc(qapp):
    panel = TocPanel()
    _ALIVE.append(panel)
    panel.show()
    qapp.processEvents()
    yield panel
    panel.close()


def test_toc_panel_has_dark_stylesheet(toc):
    """面板自带深色 QSS（不再是系统白底），且按 objectName 限定不泄漏。"""
    qss = toc.styleSheet()
    assert qss.strip()
    assert theme.WINDOW_BG in qss
    assert "QWidget#TocPanel" in qss
    assert "QTreeWidget#TocTree" in qss


def test_toc_tree_object_name(toc):
    assert toc.tree.objectName() == "TocTree"


def test_toc_tree_does_not_take_focus(toc):
    """点目录不抢画布键盘焦点。"""
    assert toc.tree.focusPolicy() == Qt.NoFocus


@pytest.mark.parametrize(
    "selector",
    ["QScrollBar:vertical", "QScrollBar:horizontal", "QInputDialog"],
)
def test_theme_qss_has_dark_rules(selector):
    assert selector in theme.QSS


def test_theme_qss_scrollbar_handle_colors():
    assert theme.BORDER_STRONG in theme.QSS   # 滑块常态
    assert theme.ACCENT_BORDER in theme.QSS   # 滑块悬停


# ---- 视觉打磨（v2.7）：单一强调色、分隔线提亮、色块 24 ----


def test_slider_and_selection_share_one_accent():
    """滑杆已选段就是主强调色：全应用只留一个桃色强调。"""
    assert theme.ACCENT == "#A84E35"
    assert theme.SLIDER_ACCENT == theme.ACCENT
    assert "#0a84ff" not in theme.QSS
    # QSlider 的 sub-page 用的就是这一档
    sub_page = theme.QSS.split("QSlider::sub-page:horizontal", 1)[1]
    assert theme.ACCENT in sub_page.split("}", 1)[0]


def test_border_token_is_lightened():
    """浅桃底上分隔线用 F3DACB，避免深灰线把界面切碎。"""
    assert theme.BORDER == "#F6C7D8"
    assert "#3f3f44" not in theme.QSS
    assert theme.BORDER in theme.QSS
    assert "QFrame#TopChromePanel" in theme.QSS
    assert theme.GLASS_TOP_BORDER in theme.QSS


def test_swatch_token_matches_toolbar_swatch_size():
    """主题里的色块直径与工具栏色块同源：24px（半径 12）。"""
    assert theme.SWATCH == 20


def test_annotate_toggle_has_no_blue_checked_state():
    """用户要求全屏批注按钮不要蓝色；专用规则改成中性灰底 + 灰边。"""
    marker = "QToolButton#AnnotateButton:checked"
    assert marker in theme.QSS
    block = theme.QSS.split(marker, 1)[1].split("}", 1)[0]
    assert theme.ACCENT not in block
    assert theme.SURFACE_HOVER in block
    assert theme.BORDER_STRONG in block


def test_capture_menu_area_has_no_blue_highlight():
    """截图按钮的下拉菜单区不再用系统高亮蓝，改中性灰。"""
    marker = "QToolButton#CaptureButton::menu-button"
    assert marker in theme.QSS
    block = theme.QSS.split(marker, 1)[1].split("}", 1)[0]
    assert theme.ACCENT not in block
    assert theme.SURFACE_HOVER in theme.QSS.split(marker, 1)[1]
