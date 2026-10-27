# tests/test_settings_dialog_offscreen.py
"""设置对话框离屏测试：改值不污染原配置、改绑捕获的拒收/收下、Esc 不关窗、齿轮与主窗接线。"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

import main
from sketchpad.ui import theme
from sketchpad.services.hotkey import GlobalHotkey
from sketchpad.core.settings import Settings
from sketchpad.ui.settings_dialog import (FIXED_BINDINGS, HINT_DUPLICATE_MAIN,
                                 HINT_DUPLICATE_SCRATCH, HINT_INVALID, HINT_RESERVED,
                                 SettingsDialog)
from sketchpad.ui.toolbox import PALETTE
from sketchpad.ui.titlebar import TitleBar

_ALIVE: list = []          # 窗口/对话框必须留引用，否则被 Python 回收（PROGRESS §5）


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(qapp):
    dlg = SettingsDialog(Settings())
    dlg.show()
    QTest.qWaitForWindowExposed(dlg)
    yield dlg
    dlg.releaseKeyboard()
    dlg.close()


@pytest.fixture
def win(qapp, tmp_path):
    ctrl = main.AppController(tmp_path)
    w = main.MainWindow(ctrl)
    w.show()
    QTest.qWaitForWindowExposed(w)
    _ALIVE.append(w)
    yield w
    w.scratch.hide()
    if w.settings_dialog is not None:
        w.settings_dialog.close()
    w._quitting = True
    w.close()


# ---------- 对话框本身 ----------

def test_dialog_is_styled_with_scoped_id(dialog):
    assert dialog.objectName() == "SettingsDialog"
    assert dialog.testAttribute(Qt.WA_StyledBackground) is True


def test_theme_scopes_the_dialog_rule_and_keeps_native_dialogs_dark_free(qapp):
    """全局 QDialog 规则会把原生取色器刷成白底（PROGRESS §5）。"""
    assert "QDialog#SettingsDialog" in theme.QSS
    assert "QDialog {" not in theme.QSS


def test_settings_dialog_renders_light_peach_surface(qapp):
    """设置面板不能再出现旧深色主题（用户 2026-10-27 反馈一片黑）。"""
    old = qapp.styleSheet()
    qapp.setStyleSheet(theme.QSS)
    dlg = SettingsDialog(Settings())
    try:
        dlg.show()
        QTest.qWaitForWindowExposed(dlg)
        image = dlg.grab().toImage()
        for x, y in ((5, 5), (dlg.width() // 2, dlg.height() - 5)):
            color = image.pixelColor(x, y)
            assert min(color.red(), color.green(), color.blue()) > 200
    finally:
        dlg.close()
        qapp.setStyleSheet(old)


def test_fixed_bindings_are_plain_labels(qapp):
    dlg = SettingsDialog(Settings())
    try:
        assert [label.text() for label in dlg.fixed_labels] == list(FIXED_BINDINGS)
    finally:
        dlg.close()


def test_fixed_bindings_mention_the_new_v25_shortcuts():
    """固定键位表要跟上新增组合键，不能停留在 v2.4 之前。"""
    text = " ".join(FIXED_BINDINGS)
    for token in ("H 荧光笔", "Ctrl+Shift+Z", "Ctrl+=", "Ctrl+-", "Ctrl+9",
                  "Home/End", "F3/Shift+F3", "F11"):
        assert token in text, token


def test_values_reflects_edits_and_leaves_input_untouched(qapp):
    original = Settings()
    dlg = SettingsDialog(original)
    dlg.show()
    QTest.qWaitForWindowExposed(dlg)
    try:
        dlg.color_buttons[1].click()                    # #2980b9
        dlg.size_slider.setValue(95)                     # 9.5
        assert dlg.size_label.text() == "9.5"
        dlg.clear_key_button.click()
        QTest.keyClick(dlg, Qt.Key_F9)
        dlg.hotkey_button.click()
        QTest.keyClick(dlg, Qt.Key_D, Qt.ControlModifier | Qt.AltModifier)

        values = dlg.values()
        assert values == Settings(clear_key="F9", color="#2980b9", width_index=1,
                                  scratch_hotkey="Ctrl+Alt+D", width=9.5)
        assert values is not original
        assert original == Settings()                    # 输入对象一字未改
    finally:
        dlg.releaseKeyboard()
        dlg.close()


def test_reset_restores_defaults_without_applying(dialog):
    dialog.color_buttons[1].click()
    dialog.size_slider.setValue(90)                      # 9.0
    dialog.clear_key_button.click()
    QTest.keyClick(dialog, Qt.Key_F9)
    dialog.hotkey_button.click()
    QTest.keyClick(dialog, Qt.Key_D, Qt.ControlModifier | Qt.AltModifier)

    dialog.reset_button.click()
    assert dialog.values() == Settings()
    assert dialog.size_slider.value() == 20
    assert dialog.size_label.text() == "2"
    assert dialog.clear_key_edit.text() == "E"
    assert dialog.hotkey_edit.text() == "Alt+Q"
    assert dialog.main_hotkey_edit.text() == "Alt+W"
    assert [b.isChecked() for b in dialog.color_buttons] == [False] * 3 + [True] + [False] * 5


def test_pdf_autosave_checkbox_seeds_from_settings(dialog):
    assert dialog.pdf_autosave_check.text() == "PDF 批注自动保存（崩溃恢复）"
    assert dialog.pdf_autosave_check.isChecked() is False
    assert dialog.values().pdf_autosave is False
    assert dialog.values().pdf_autosave_interval_min == 5


def test_pdf_autosave_interval_presets_are_chinese(dialog):
    labels = [dialog.autosave_interval_combo.itemText(i)
              for i in range(dialog.autosave_interval_combo.count())]
    assert labels == ["1 分钟", "5 分钟", "10 分钟", "20 分钟", "1 小时", "自定义"]


def test_pdf_autosave_checkbox_seeds_checked_value(qapp):
    dlg = SettingsDialog(Settings(pdf_autosave=True))
    try:
        assert dlg.pdf_autosave_check.isChecked() is True
        assert dlg.values().pdf_autosave is True
    finally:
        dlg.close()


def test_pdf_autosave_checkbox_flows_into_values(dialog):
    dialog.pdf_autosave_check.click()
    assert dialog.values().pdf_autosave is True
    dialog.pdf_autosave_check.click()
    assert dialog.values().pdf_autosave is False


def test_autosave_custom_interval_uses_chinese_unit_controls(dialog):
    dialog.pdf_autosave_check.setChecked(True)
    dialog.autosave_interval_combo.setCurrentIndex(
        dialog.autosave_interval_combo.findData(-1))
    dialog.autosave_custom_unit.setCurrentIndex(
        dialog.autosave_custom_unit.findData(60))
    dialog.autosave_custom_value.setValue(2)
    assert dialog.values().pdf_autosave_interval_min == 120
    assert [dialog.autosave_custom_unit.itemText(i)
            for i in range(dialog.autosave_custom_unit.count())] == ["分钟", "小时"]


def test_reset_restores_pdf_autosave_default(dialog):
    dialog.pdf_autosave_check.setChecked(True)
    dialog.autosave_interval_combo.setCurrentIndex(
        dialog.autosave_interval_combo.findData(20))
    assert dialog.values().pdf_autosave is True
    dialog.reset_button.click()
    assert dialog.pdf_autosave_check.isChecked() is False
    assert dialog.values().pdf_autosave is False
    assert dialog.values().pdf_autosave_interval_min == 5


# ---------- v2.9：工具栏位置 ----------

def test_toolbar_position_combo_seeds_and_returns_choice(qapp):
    dlg = SettingsDialog(Settings(toolbar_position="right"))
    try:
        combo = dlg.toolbar_position_combo
        assert [combo.itemText(i) for i in range(combo.count())] == ["顶部", "右侧"]
        assert combo.currentData() == "right"          # 从配置恢复选中
        assert dlg.values().toolbar_position == "right"

        combo.setCurrentIndex(combo.findData("top"))
        assert dlg.values().toolbar_position == "top"
        combo.setCurrentIndex(combo.findData("right"))
        assert dlg.values().toolbar_position == "right"
    finally:
        dlg.close()


def test_toolbar_position_combo_defaults_to_top(dialog):
    assert dialog.toolbar_position_combo.currentData() == "top"
    assert dialog.values().toolbar_position == "top"


def test_reset_sets_toolbar_position_top_and_expanded(qapp):
    dlg = SettingsDialog(Settings(toolbar_position="right", toolbar_collapsed=True))
    dlg.show()
    QTest.qWaitForWindowExposed(dlg)
    try:
        dlg.reset_button.click()
        assert dlg.toolbar_position_combo.currentData() == "top"
        assert dlg.values().toolbar_position == "top"
        assert dlg.values().toolbar_collapsed is False
    finally:
        dlg.close()


def test_values_keep_incoming_toolbar_collapsed(qapp):
    """收起状态在对话框里不改，但要原样带回（主窗以工具栏真实状态为准）。"""
    dlg = SettingsDialog(Settings(toolbar_collapsed=True))
    try:
        assert dlg.values().toolbar_collapsed is True
    finally:
        dlg.close()


def test_save_accepts_and_cancel_rejects(qapp):
    save = SettingsDialog(Settings())
    save.show()
    save.save_button.click()
    assert save.result() == QDialog.Accepted
    save.close()

    cancel = SettingsDialog(Settings())
    cancel.show()
    cancel.cancel_button.click()
    assert cancel.result() == QDialog.Rejected
    cancel.close()


# ---------- 清屏键捕获 ----------

def test_clear_key_capture_rejects_ctrl_z_and_accepts_f9(dialog):
    assert dialog.clear_key_edit.text() == "E"
    dialog.clear_key_button.click()
    assert dialog.is_capturing() is True
    assert dialog.clear_key_button.text() == "请按键…"

    QTest.keyClick(dialog, Qt.Key_Z, Qt.ControlModifier)
    assert dialog.is_capturing() is True                  # 留在捕获态
    assert dialog.hint_label.text() == HINT_RESERVED
    assert dialog.clear_key_edit.text() == "E"
    assert dialog.clear_key_button.text() == "请按键…"

    dialog._handle_capture_key(" ctrl ", "z")             # 大小写/空格无关
    assert dialog.is_capturing() is True
    assert dialog.hint_label.text() == HINT_RESERVED

    QTest.keyClick(dialog, Qt.Key_F9)
    assert dialog.is_capturing() is False
    assert dialog.clear_key_edit.text() == "F9"
    assert dialog.hint_label.text() == ""


def test_clear_key_capture_rejects_v24_page_keys(dialog):
    # v2 的 PgUp/PgDown 是翻页快捷键，清屏键不能再占
    dialog.clear_key_button.click()
    QTest.keyClick(dialog, Qt.Key_PageDown)
    assert dialog.is_capturing() is True
    assert dialog.hint_label.text() == HINT_RESERVED
    assert dialog.clear_key_edit.text() == "E"


# ---------- 全局热键捕获 ----------

def test_hotkey_capture_rejects_invalid_and_accepts_ctrl_alt_d(dialog):
    dialog.hotkey_button.click()
    assert dialog.is_capturing() is True
    assert dialog.hotkey_button.text() == "请按键…"

    dialog._handle_capture_key("", "Mouse")
    assert dialog.is_capturing() is True
    assert dialog.hint_label.text() == HINT_INVALID

    dialog._handle_capture_key("Ctrl", "")                # "Ctrl+"：不完整组合
    assert dialog.is_capturing() is True
    assert dialog.hint_label.text() == HINT_INVALID
    assert dialog.hotkey_edit.text() == "Alt+Q"           # 默认未变

    QTest.keyClick(dialog, Qt.Key_D, Qt.ControlModifier | Qt.AltModifier)
    assert dialog.is_capturing() is False
    assert dialog.hotkey_edit.text() == "Ctrl+Alt+D"


def test_hotkey_capture_rejects_bare_key_and_accepts_f9(dialog):
    assert dialog.hotkey_edit.text() == "Alt+Q"
    dialog.hotkey_button.click()
    assert dialog.is_capturing() is True

    QTest.keyClick(dialog, Qt.Key_A)                      # 裸键：全局热键必须有修饰键
    assert dialog.is_capturing() is True
    assert dialog.hint_label.text() == HINT_INVALID
    assert dialog.hotkey_edit.text() == "Alt+Q"
    assert dialog.hotkey_button.text() == "请按键…"

    dialog._handle_capture_key("", "Space")               # 裸 Space 同样不收
    assert dialog.is_capturing() is True
    assert dialog.hint_label.text() == HINT_INVALID
    assert dialog.hotkey_edit.text() == "Alt+Q"

    QTest.keyClick(dialog, Qt.Key_F9)                     # F1–F24 可以单独用
    assert dialog.is_capturing() is False
    assert dialog.hotkey_edit.text() == "F9"
    assert dialog.hint_label.text() == ""


def test_main_hotkey_capture_and_duplicate_rejection(dialog):
    """主窗热键可改；两条全局热键不能选同一个组合。"""
    assert dialog.main_hotkey_edit.text() == "Alt+W"
    dialog.main_hotkey_button.click()
    dialog._handle_capture_key("Alt", "Q")                # 与草稿纸 Alt+Q 相同
    assert dialog.is_capturing() is True
    assert dialog.hint_label.text() == HINT_DUPLICATE_SCRATCH
    assert dialog.main_hotkey_edit.text() == "Alt+W"

    QTest.keyClick(dialog, Qt.Key_M, Qt.ControlModifier | Qt.ShiftModifier)
    assert dialog.is_capturing() is False
    assert dialog.main_hotkey_edit.text() == "Ctrl+Shift+M"
    assert dialog.values().main_hotkey == "Ctrl+Shift+M"

    dialog.hotkey_button.click()
    dialog._handle_capture_key("Ctrl+Shift", "M")         # 草稿纸不能撞主窗
    assert dialog.is_capturing() is True
    assert dialog.hint_label.text() == HINT_DUPLICATE_MAIN
    QTest.keyClick(dialog, Qt.Key_Escape)
    assert dialog.is_capturing() is False


# ---------- Esc / Enter 不能收窗 ----------

def test_escape_cancels_capture_but_keeps_dialog_open(dialog):
    dialog.clear_key_button.click()
    QTest.keyClick(dialog, Qt.Key_Escape)
    assert dialog.is_capturing() is False
    assert dialog.clear_key_edit.text() == "E"
    assert dialog.isVisible() is True
    assert dialog.result() == 0


def test_return_does_not_click_default_button_while_capturing(dialog):
    dialog.hotkey_button.click()
    QTest.keyClick(dialog, Qt.Key_Return)                 # Return 不是合法热键
    assert dialog.is_capturing() is True
    assert dialog.isVisible() is True
    assert dialog.result() == 0
    QTest.keyClick(dialog, Qt.Key_Escape)
    assert dialog.is_capturing() is False
    assert dialog.isVisible() is True


# ---------- 标题栏齿轮 ----------

def test_titlebar_gear_is_left_of_pin_and_emits(qapp):
    bar = TitleBar("PageFlow")
    got = []
    bar.settingsRequested.connect(lambda: got.append(1))
    assert bar.settings_button.objectName() == "WinButton"
    assert (bar.settings_button.width(), bar.settings_button.height()) == (32, 24)
    assert bar.settings_button.toolTip() == "设置 (Ctrl+,)"
    layout = bar.layout()
    assert layout.indexOf(bar.settings_button) < layout.indexOf(bar.pin_button)
    bar.settings_button.click()
    assert got == [1]
    bar.close()


# ---------- 主窗接线 ----------

def _stub_dialog(monkeypatch, values, code=QDialog.Accepted):
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: code)
    monkeypatch.setattr(SettingsDialog, "values", lambda self: values)


def test_gear_click_applies_values_and_writes_settings(win, monkeypatch):
    prepared = Settings(clear_key="F9", color="#2980b9", width_index=2,
                        scratch_hotkey="Ctrl+Alt+D", width=9.5)
    monkeypatch.setattr(GlobalHotkey, "unregister", lambda self: None)
    monkeypatch.setattr(GlobalHotkey, "register", lambda self, hwnd, sequence: True)
    _stub_dialog(monkeypatch, prepared)

    # 设置入口已从工具栏移到菜单栏
    assert win.toolbox.settings_button is None
    assert win.menubar.settings_action.text() == "设置…"
    win.menubar.settings_action.trigger()

    assert win.view.color == "#2980b9"
    assert win.view.width == 9.5
    assert win.view.clear_key == "F9"
    assert win.controller.settings == prepared
    assert win.settings_dialog is not None                # 引用留住
    saved = json.loads(win.controller.settings_path.read_text(encoding="utf-8"))
    assert saved == {"clear_key": "F9", "color": "#2980b9", "width_index": 2,
                     "scratch_hotkey": "Ctrl+Alt+D", "width": 9.5,
                     "pdf_autosave": False, "pdf_autosave_interval_min": 5,
                     "recent_files": [],
                     "toolbar_position": "top", "toolbar_collapsed": False,
                     "main_hotkey": "Alt+W", "page_mode": "normal", "ink_smoothing": "low",
                     "highlighter_color": "#d4a017",
                     "pressure_enabled": True, "note_paper": "dark",
                     "close_to_tray": True, "close_prompt": True,
                     "open_export_folder": False}


def test_settings_switch_to_right_moves_toolbar_and_persists(win, monkeypatch):
    _stub_dialog(monkeypatch, Settings(toolbar_position="right"))

    win._open_settings()
    QTest.qWait(40)

    assert win.toolbox.vertical is True
    assert win.column.indexOf(win.toolbar_scroll) == -1
    assert win.top_chrome.has_row(win.toolbar_scroll) is False
    assert win.body.indexOf(win.toolbar_scroll) >= 0
    assert win.controller.settings.toolbar_position == "right"
    saved = json.loads(win.controller.settings_path.read_text(encoding="utf-8"))
    assert saved["toolbar_position"] == "right"
    assert saved["toolbar_collapsed"] is False


def test_settings_keep_live_collapsed_state_when_saved(win, monkeypatch):
    """对话框不碰收起状态：保存后以工具栏真实状态为准（v2.8 折叠不丢）。"""
    win.toolbox.set_collapsed(True)
    QTest.qWait(20)
    _stub_dialog(monkeypatch, Settings(toolbar_position="right", toolbar_collapsed=False))

    win._open_settings()
    QTest.qWait(40)

    assert win.toolbox.collapsed is True
    assert win.controller.settings.toolbar_collapsed is True
    saved = json.loads(win.controller.settings_path.read_text(encoding="utf-8"))
    assert saved["toolbar_collapsed"] is True
    assert saved["toolbar_position"] == "right"


def test_hotkey_change_unregisters_then_registers_new_sequence(win, monkeypatch):
    calls = []
    monkeypatch.setattr(GlobalHotkey, "unregister", lambda self: calls.append("unregister"))

    def fake_register(self, hwnd, sequence):
        calls.append(("register", sequence))
        self._registered = True
        return True

    monkeypatch.setattr(GlobalHotkey, "register", fake_register)
    _stub_dialog(monkeypatch, Settings(scratch_hotkey="Ctrl+Alt+F"))

    win._open_settings()

    assert calls == ["unregister", ("register", "Ctrl+Alt+F")]
    assert win.controller.settings.scratch_hotkey == "Ctrl+Alt+F"
    assert win.fallback_shortcut is None


def test_hotkey_failure_enables_fallback_with_new_sequence(win, monkeypatch):
    monkeypatch.setattr(GlobalHotkey, "unregister", lambda self: None)
    monkeypatch.setattr(GlobalHotkey, "register", lambda self, hwnd, sequence: False)
    win.hotkey.error_message = "注册热键失败（错误码 1409）"
    _stub_dialog(monkeypatch, Settings(scratch_hotkey="Ctrl+Alt+F"))

    win._open_settings()

    assert win.fallback_shortcut is not None
    assert win.fallback_shortcut.key().toString() == "Ctrl+Alt+F"
    assert win.fallback_shortcut.isEnabled() is True
    assert "Ctrl+Alt+F" in win.message_label.text()
    assert "不可用" in win.message_label.text()


def test_main_hotkey_change_rebinds(win, monkeypatch):
    calls = []
    monkeypatch.setattr(GlobalHotkey, "unregister", lambda self: calls.append("unregister"))

    def fake_register(self, hwnd, sequence):
        calls.append(("register", sequence))
        self._registered = True
        return True

    monkeypatch.setattr(GlobalHotkey, "register", fake_register)
    _stub_dialog(monkeypatch, Settings(main_hotkey="Ctrl+Shift+M"))

    win._open_settings()

    assert calls == ["unregister", ("register", "Ctrl+Shift+M")]
    assert win.controller.settings.main_hotkey == "Ctrl+Shift+M"


def test_swapping_both_hotkeys_unregisters_both_first(win, monkeypatch):
    """两条热键互换：必须先注销旧的，再按新值注册，否则第二条会 1409。"""
    calls = []
    monkeypatch.setattr(
        GlobalHotkey, "unregister",
        lambda self: calls.append(("unregister", self is win.hotkey)))

    def fake_register(self, hwnd, sequence):
        calls.append(("register", sequence))
        self._registered = True
        return True

    monkeypatch.setattr(GlobalHotkey, "register", fake_register)
    _stub_dialog(monkeypatch, Settings(scratch_hotkey="Alt+W", main_hotkey="Alt+Q"))

    win._open_settings()

    assert calls == [("unregister", True), ("unregister", False),
                     ("register", "Alt+W"), ("register", "Alt+Q")]
    assert win.controller.settings.scratch_hotkey == "Alt+W"
    assert win.controller.settings.main_hotkey == "Alt+Q"


def test_ctrl_comma_shortcut_opens_settings(win, monkeypatch):
    _stub_dialog(monkeypatch, Settings(), code=QDialog.Rejected)
    win.activateWindow()
    win.view.setFocus()
    QTest.qWait(20)

    QTest.keyClick(win.view, Qt.Key_Comma, Qt.ControlModifier)

    assert isinstance(win.settings_dialog, SettingsDialog)


def test_open_settings_while_scratch_window_exists(win, monkeypatch):
    win.scratch.new_draft()                               # 草稿窗真的建出来
    assert win.scratch.is_visible() is True
    _stub_dialog(monkeypatch, Settings(clear_key="F9"))

    win._open_settings()

    assert win.controller.settings.clear_key == "F9"
    assert win.scratch.settings is win.controller.settings   # 草稿窗拿的是同一个配置对象
    assert win.controller.settings.scratch_hotkey == Settings().scratch_hotkey


def test_palette_swatches_use_toolbox_colors(qapp):
    dlg = SettingsDialog(Settings())
    try:
        assert [b.property("color") for b in dlg.color_buttons] == PALETTE
    finally:
        dlg.close()


def test_new_settings_controls_and_theme_contrast(dialog):
    from sketchpad.ui import theme
    assert "QDialog#SettingsDialog QCheckBox" in theme.QSS
    assert dialog.note_paper_combo.currentData() == "dark"
    assert dialog.close_behavior_combo.currentData() == "ask"
    assert dialog.open_export_folder_check.isChecked() is False
    assert dialog.highlighter_color_button.text().startswith("#")

    dialog.note_paper_combo.setCurrentIndex(1)
    dialog.close_behavior_combo.setCurrentIndex(
        dialog.close_behavior_combo.findData("quit"))
    dialog.open_export_folder_check.setChecked(True)
    values = dialog.values()
    assert (values.note_paper, values.close_to_tray, values.close_prompt,
            values.open_export_folder) == ("light", False, False, True)

    dialog.close_behavior_combo.setCurrentIndex(
        dialog.close_behavior_combo.findData("tray"))
    values = dialog.values()
    assert (values.close_to_tray, values.close_prompt) == (True, False)

    dialog.reset_button.click()
    assert dialog.note_paper_combo.currentData() == "dark"
    assert dialog.close_behavior_combo.currentData() == "ask"
    assert dialog.open_export_folder_check.isChecked() is False


def test_settings_are_split_into_tabbed_modules(qapp):
    dialog = SettingsDialog(Settings())
    try:
        titles = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
        assert titles == ["绘制", "界面", "快捷键", "文件", "高级"]
        assert dialog.tabs.tabToolTip(0)
        assert dialog.minimumWidth() >= 520
        page = dialog.tabs.widget(0)
        assert page.objectName() == "SettingsPage"
        assert page.palette().color(page.backgroundRole()).name().lower() == theme.SURFACE.lower()
    finally:
        dialog.close()
