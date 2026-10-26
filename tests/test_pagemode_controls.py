"""阅读模式（正常/夜间/护眼）控制与持久化离屏测试。

覆盖四层：settings 读写回退、菜单子项与互斥、设置对话框下拉、主窗接线与落盘。
像素级渲染归另一份测试，这里只验证状态与信号。
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

import main
from sketchpad.core import settings as settings_store
from sketchpad.core.settings import PAGE_MODES, Settings, load, save
from sketchpad.ui.menubar import Menubar
from sketchpad.ui.settings_dialog import SettingsDialog

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


def _close_window(win) -> None:
    win.scratch.hide()
    if win.settings_dialog is not None:
        win.settings_dialog.close()
    win.pdf_view.close_document()
    win._quitting = True
    win.close()


@pytest.fixture
def win(qapp, tmp_path):
    ctrl = main.AppController(tmp_path / "cfg")
    w = main.MainWindow(ctrl)
    w._confirm_unsaved = lambda action: QMessageBox.Discard   # 离屏不弹模态框
    w.show()
    _ALIVE.append(w)
    yield w
    _close_window(w)


@pytest.fixture
def bar(qapp):
    widget = Menubar()
    yield widget
    widget.close()
    qapp.processEvents()


# ---------- settings：默认值 / 往返 / 坏值回退 ----------

def test_page_mode_defaults_to_normal(tmp_path):
    assert Settings().page_mode == "normal"
    assert load(tmp_path / "settings.json").page_mode == "normal"


def test_page_mode_round_trips(tmp_path):
    p = tmp_path / "settings.json"
    for mode in PAGE_MODES:
        save(p, Settings(page_mode=mode))
        assert load(p).page_mode == mode
    assert json.loads(p.read_text(encoding="utf-8"))["page_mode"] == "eye"


@pytest.mark.parametrize("raw", ['"blue"', "3", "null", "true", "[]", '""', '"NIGHT"'])
def test_bad_page_mode_falls_back_to_normal(tmp_path, raw):
    p = tmp_path / "settings.json"
    p.write_text('{"page_mode": ' + raw + "}", encoding="utf-8")
    assert load(p).page_mode == "normal"      # 坏值只影响自己，绝不抛


def test_bad_page_mode_does_not_discard_other_fields(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"page_mode": "blue", "color": "#2980b9"}', encoding="utf-8")
    loaded = load(p)
    assert loaded.page_mode == "normal"
    assert loaded.color == "#2980b9"


# ---------- 菜单：三项互斥 + 启停 ----------

def test_page_mode_submenu_has_three_exclusive_actions(bar):
    menu = bar.page_mode_menu
    assert menu.title() == "阅读模式"
    actions = menu.actions()
    assert len(actions) == 3
    assert [action.text() for action in actions] == ["正常", "夜间（反相）", "护眼（羊皮纸）"]
    assert all(action.isCheckable() for action in actions)
    assert set(bar.page_mode_actions) == set(PAGE_MODES)
    # 位于缩放四项之后、全屏之前
    view_actions = bar.view_menu.actions()
    assert (view_actions.index(bar.fit_width_action)
            < view_actions.index(menu.menuAction())
            < view_actions.index(bar.fullscreen_action))
    # 默认勾选正常；触发护眼后互斥取消正常
    assert bar.page_mode_actions["normal"].isChecked() is True
    bar.page_mode_actions["eye"].trigger()
    assert [bar.page_mode_actions[mode].isChecked() for mode in PAGE_MODES] == [
        False, False, True]


def test_triggering_page_action_emits_stable_id(bar):
    got: list[str] = []
    bar.actionTriggered.connect(got.append)
    bar.page_mode_actions["night"].trigger()
    assert got == ["page_night"]
    assert bar.page_mode_actions["night"].isChecked() is True


def test_set_page_mode_checks_only_and_stays_silent(bar):
    got: list[str] = []
    bar.actionTriggered.connect(got.append)
    bar.set_page_mode("eye")
    assert [bar.page_mode_actions[mode].isChecked() for mode in PAGE_MODES] == [
        False, False, True]
    bar.set_page_mode("night")
    assert [bar.page_mode_actions[mode].isChecked() for mode in PAGE_MODES] == [
        False, True, False]
    assert got == []
    with pytest.raises(ValueError):
        bar.set_page_mode("blue")


def test_reading_mode_submenu_enabled_only_in_document_mode(bar):
    assert bar.mode == "draft"
    assert bar.page_mode_menu.isEnabled() is False
    bar.set_mode("document")
    assert bar.page_mode_menu.isEnabled() is True
    bar.set_mode("draft")
    assert bar.page_mode_menu.isEnabled() is False


# ---------- 设置对话框：下拉种子与取值 ----------

def test_dialog_combo_seeds_from_settings_and_returns_choice(qapp):
    dlg = SettingsDialog(Settings(page_mode="night"))
    try:
        combo = dlg.page_mode_combo
        assert [combo.itemData(i) for i in range(combo.count())] == list(PAGE_MODES)
        assert [combo.itemText(i) for i in range(combo.count())] == ["正常", "夜间", "护眼"]
        assert combo.currentData() == "night"
        assert dlg.values().page_mode == "night"

        combo.setCurrentIndex(combo.findData("eye"))
        assert dlg.values().page_mode == "eye"
    finally:
        dlg.close()


def test_dialog_combo_defaults_to_normal_with_plain_thumbnail_tooltip(qapp):
    dlg = SettingsDialog(Settings())
    try:
        assert dlg.page_mode_combo.currentData() == "normal"
        assert dlg.values().page_mode == "normal"
        tip = dlg.page_mode_combo.toolTip()
        assert "反相" in tip and "笔迹颜色不变" in tip
    finally:
        dlg.close()


def test_dialog_reset_restores_normal_page_mode(qapp):
    dlg = SettingsDialog(Settings(page_mode="eye"))
    try:
        dlg.reset_button.click()
        assert dlg.page_mode_combo.currentData() == "normal"
        assert dlg.values().page_mode == "normal"
    finally:
        dlg.close()


# ---------- 主窗接线 ----------

def test_night_action_reaches_both_views_settings_and_disk(win, monkeypatch):
    calls = {"pdf": [], "thumb": []}
    monkeypatch.setattr(win.pdf_view, "set_page_mode",
                        lambda mode: calls["pdf"].append(mode))
    monkeypatch.setattr(win.thumb_panel, "set_page_mode",
                        lambda mode: calls["thumb"].append(mode))

    win.menubar.page_mode_actions["night"].trigger()

    assert calls == {"pdf": ["night"], "thumb": ["night"]}
    assert win.controller.settings.page_mode == "night"
    assert win.menubar.page_mode_actions["night"].isChecked() is True
    assert win.menubar.page_mode_actions["normal"].isChecked() is False
    saved = json.loads(win.controller.settings_path.read_text(encoding="utf-8"))
    assert saved["page_mode"] == "night"
    assert "阅读模式" in win.message_label.text()


def test_set_page_mode_rejects_unknown_without_mutating(win):
    with pytest.raises(ValueError):
        win._set_page_mode("blue")
    assert win.controller.settings.page_mode == "normal"


def test_settings_dialog_accept_applies_and_persists_page_mode(win, monkeypatch):
    prepared = Settings(page_mode="eye")
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(SettingsDialog, "values", lambda self: prepared)

    win._open_settings()

    assert win.controller.settings.page_mode == "eye"
    assert win.pdf_view.page_mode == "eye"
    saved = json.loads(win.controller.settings_path.read_text(encoding="utf-8"))
    assert saved["page_mode"] == "eye"


def test_enter_document_applies_mode_mutated_outside_setter(win, pdf):
    """配置被直接改过（测试/外部）时，打开文档前两个视图要对齐。"""
    win.controller.settings.page_mode = "night"
    assert win._enter_document(pdf) is True
    assert win.pdf_view.page_mode == "night"
    assert win.thumb_panel._page_mode == "night"


def test_startup_applies_saved_page_mode(qapp, tmp_path):
    cfg = tmp_path / "cfg"
    settings_store.save(cfg / "settings.json", Settings(page_mode="eye"))
    ctrl = main.AppController(cfg)
    assert ctrl.settings.page_mode == "eye"

    w = main.MainWindow(ctrl)
    _ALIVE.append(w)
    try:
        assert w.pdf_view.page_mode == "eye"
        assert w.thumb_panel._page_mode == "eye"
        assert w.menubar.page_mode_actions["eye"].isChecked() is True
        assert w.menubar.page_mode_actions["normal"].isChecked() is False
    finally:
        _close_window(w)
