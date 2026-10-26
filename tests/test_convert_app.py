"""转换中心接线：Standard 菜单/快捷键、对话框选项、后台合并任务。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import main
from sketchpad.convert import install as install_convert
from sketchpad.convert.dialog import ConvertCenterDialog
from sketchpad.reader import install as install_readers
from sketchpad.word import install as install_word
from sketchpad.workspace.viewers import clear_viewers, has_converter

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def standard_tools():
    install_readers()
    install_word()
    install_convert()
    yield
    clear_viewers()


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "doc.pdf"
    document = pymupdf.open()
    document.new_page().insert_text((40, 60), "hello")
    document.save(str(path))
    document.close()
    return path


def _make_pdf(path, text):
    document = pymupdf.open()
    document.new_page().insert_text((40, 60), text)
    document.save(str(path))
    document.close()
    return path


def test_standard_menu_exposes_conversion_center(qapp, tmp_path):
    assert has_converter() is True
    window = main.MainWindow(main.AppController(tmp_path / "cfg"))
    _ALIVE.append(window)
    assert window.menubar.convert_action.isVisible() is True
    assert window.menubar.convert_action.isEnabled() is True
    assert window._open_convert_center() is True
    assert isinstance(window.convert_dialog, ConvertCenterDialog)
    window.convert_dialog.close()
    window._quitting = True
    window.close()


def test_dialog_options_follow_operation(qapp):
    dialog = ConvertCenterDialog()
    assert dialog.operation_combo.count() == 16
    dialog.operation_combo.setCurrentIndex(2)      # 旋转
    assert dialog.angle_spin.isVisible() is False  # 未 show 的窗口只能看 hidden 状态
    assert dialog.angle_spin.isHidden() is False
    dialog.operation_combo.setCurrentIndex(0)      # 合并
    assert dialog.options_group.isHidden() is True
    dialog.close()


def test_dialog_runs_merge_in_background(qapp, tmp_path):
    first = _make_pdf(tmp_path / "a.pdf", "A")
    second = _make_pdf(tmp_path / "b.pdf", "B")
    out = tmp_path / "out"
    dialog = ConvertCenterDialog()
    dialog.files.addItem(str(first))
    dialog.files.addItem(str(second))
    dialog.output_edit.setText(str(out))
    dialog._start()
    for _ in range(200):
        if dialog.worker is None:
            break
        QTest.qWait(25)
    assert dialog.worker is None
    assert "完成" in dialog.result_label.text()
    merged = out / "a-合并.pdf"
    assert merged.is_file()
    with pymupdf.open(merged) as document:
        assert document.page_count == 2
    dialog.close()
