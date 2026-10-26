"""笔记富内容 UI 离屏测试：文字/图片/附件图元、鼠标与键盘路由、分屏/独立窗口按钮。

样例 PDF 与 PNG/附件全部在 tmp_path 现造，不碰 data/；对话框与系统打开一律
monkeypatch，不弹真窗、不真起外部程序。

统一约定：所有富内容坐标都是**设计坐标**（560x720），鼠标事件送给视口时用
`view.mapFromScene` 换算成屏幕坐标；全局坐标必须给真实值（Qt 在缺省 (0,0)
时不会把按下事件当成一次真正的点击，选中态就不会发生）。
"""
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QAbstractButton, QApplication, QFileDialog, QGraphicsItem,
                               QMessageBox, QWidget)

from sketchpad.core import noteassets
from sketchpad.core.notes import Note
from sketchpad.core.settings import Settings
from sketchpad.ui import note_canvas
from sketchpad.ui.note_canvas import (ATTACH_H, ATTACH_W, AttachmentItem, NoteImageItem,
                                      NoteView, TextBlockItem)
from sketchpad.ui.note_card import NoteCard, NoteWindow

# 显示出来的控件必须留 Python 引用，否则包装对象被回收、窗口"活着但没了"
_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


# --- 夹具：PDF / 图片 / 附件 ---


def _make_pdf(path, width=300.0, height=400.0):
    doc = pymupdf.open()
    doc.new_page(width=width, height=height)
    doc.save(str(path))
    doc.close()
    return path


def _make_png(path, width=80, height=60, fill=(1.0, 0.0, 0.0)):
    """用 PyMuPDF 造一张纯色 PNG（不依赖 Qt/PIL）。"""
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.draw_rect(page.rect, color=None, fill=fill, width=0)
    page.get_pixmap(alpha=False).save(str(path))
    doc.close()
    return path


@pytest.fixture
def pdf(tmp_path):
    return _make_pdf(tmp_path / "sample.pdf")


def _show_card(note: Note, pdf_path: Path | None = None,
               settings: Settings | None = None) -> NoteCard:
    card = NoteCard(note, settings or Settings(), pdf_path=pdf_path)
    card.resize(560, 720)
    card.show()
    QTest.qWait(30)
    _ALIVE.append(card)
    return card


def _close(widget) -> None:
    if widget in _ALIVE:
        _ALIVE.remove(widget)
    widget.close()


# --- 鼠标工具 ---


def _scene_pt(view: NoteView, x: float, y: float) -> tuple[float, float]:
    """设计坐标 → 视口坐标（浮点）。"""
    point = view.mapFromScene(QPointF(float(x), float(y)))
    return float(point.x()), float(point.y())


def _send_mouse(view: NoteView, kind, x: float, y: float, button, buttons) -> None:
    """直接给视口发鼠标事件（QTest.mouseMove 带按键拖动在 offscreen 上不稳定）。"""
    local = QPointF(float(x), float(y))
    global_pos = QPointF(view.viewport().mapToGlobal(QPoint(int(x), int(y))))
    QApplication.sendEvent(view.viewport(),
                           QMouseEvent(kind, local, global_pos, button, buttons,
                                       Qt.NoModifier))


def _click_scene(view: NoteView, x: float, y: float) -> None:
    px, py = _scene_pt(view, x, y)
    _send_mouse(view, QEvent.MouseButtonPress, px, py, Qt.LeftButton, Qt.LeftButton)
    _send_mouse(view, QEvent.MouseButtonRelease, px, py, Qt.LeftButton, Qt.NoButton)


def _double_click_scene(view: NoteView, x: float, y: float) -> None:
    px, py = _scene_pt(view, x, y)
    _send_mouse(view, QEvent.MouseButtonPress, px, py, Qt.LeftButton, Qt.LeftButton)
    _send_mouse(view, QEvent.MouseButtonDblClick, px, py, Qt.LeftButton, Qt.LeftButton)
    _send_mouse(view, QEvent.MouseButtonRelease, px, py, Qt.LeftButton, Qt.NoButton)


def _drag_scene(view: NoteView, x: float, y: float, dx: float, dy: float,
                steps: int = 3) -> None:
    px, py = _scene_pt(view, x, y)
    sx = px + dx
    sy = py + dy
    _send_mouse(view, QEvent.MouseButtonPress, px, py, Qt.LeftButton, Qt.LeftButton)
    for step in range(1, steps + 1):
        ratio = step / steps
        _send_mouse(view, QEvent.MouseMove, px + (sx - px) * ratio,
                    py + (sy - py) * ratio, Qt.NoButton, Qt.LeftButton)
    _send_mouse(view, QEvent.MouseButtonRelease, sx, sy, Qt.LeftButton, Qt.NoButton)


# ---------- 1. 工具行 / 卡头按钮 ----------


def test_card_rich_tools_and_split_window_buttons(qapp, pdf):
    card = _show_card(Note("n-1", 0, 0), pdf)
    try:
        assert card.text_button.toolTip() == "文字 (T)"
        assert "图片" in card.image_button.toolTip()
        assert "附件" in card.attach_button.toolTip()
        for button in (card.text_button, card.image_button, card.attach_button):
            assert button.isVisible()
            assert button.focusPolicy() == Qt.NoFocus
        # 放在橡皮后面：同一行、顺序固定
        layout = card.tools_layout
        order = [layout.itemAt(i).widget() for i in range(layout.count())
                 if layout.itemAt(i).widget() is not None]
        assert order.index(card.text_button) == order.index(card.eraser_button) + 1
        assert order.index(card.image_button) == order.index(card.text_button) + 1
        assert order.index(card.attach_button) == order.index(card.image_button) + 1

        assert card.split_button.toolTip() == "分屏阅读"
        assert card.window_button.toolTip() == "在独立窗口打开"
        # 两个新按钮都要有真图标（不是空 QIcon）
        assert card.split_button.icon().isNull() is False
        assert card.window_button.icon().isNull() is False

        hits: list[str] = []
        card.splitRequested.connect(lambda: hits.append("split"))
        card.windowRequested.connect(lambda: hits.append("window"))
        card.split_button.click()
        card.window_button.click()
        assert hits == ["split", "window"]        # 每次点击正好一次意图

        # 不能笔记套笔记：没有任何「插笔记」按钮/信号
        assert not hasattr(card, "note_button")
        tooltips = [button.toolTip() for button in card.findChildren(QAbstractButton)]
        assert not any("笔记" in tip and "插入" in tip for tip in tooltips)
        requested = [name for name in dir(type(card)) if name.endswith("Requested")]
        assert not any("note" in name.lower() for name in requested)
    finally:
        _close(card)


def test_note_window_hides_window_button_and_forwards(qapp, pdf):
    window = NoteWindow(Note("n-win", 0, 0), Settings(), pdf_path=pdf)
    _ALIVE.append(window)
    try:
        assert window.card.window_button.isHidden()    # 已经在窗口里，不再开窗口
        assert window.card.view.pdf_path == pdf
        hits: list[str] = []
        window.splitRequested.connect(lambda: hits.append("split"))
        window.card.split_button.click()
        assert hits == ["split"]
    finally:
        _close(window)


def test_card_passes_pdf_path_and_forwards_new_signals(qapp, pdf):
    """卡片自带 pdf_path：图片/附件按钮靠它定位素材目录（浮层已取消，直接建卡片）。"""
    card = NoteCard(Note("n-1", 0, 0), Settings(), pdf_path=pdf)
    _ALIVE.append(card)
    try:
        hits: list[str] = []
        card.splitRequested.connect(lambda: hits.append("split"))
        card.windowRequested.connect(lambda: hits.append("window"))
        card.show()
        QTest.qWait(30)
        assert card.view.pdf_path == pdf
        card.split_button.click()
        card.window_button.click()
        assert hits == ["split", "window"]
    finally:
        _close(card)


def test_insert_buttons_warn_without_pdf(qapp, monkeypatch):
    card = _show_card(Note("n-1", 0, 0))          # 没有 pdf_path：无处存放素材
    try:
        warned: list[tuple] = []
        opened: list[tuple] = []
        monkeypatch.setattr(QMessageBox, "warning",
                            staticmethod(lambda *a, **k: warned.append(a)))
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: opened.append(a) or ("", "")))
        card.image_button.click()
        card.attach_button.click()
        assert len(warned) == 2
        assert all("先打开 PDF 再插文件" in args[2] for args in warned)
        assert opened == []                        # 连文件对话框都不该弹
        assert card.note.texts == [] and card.note.images == []
    finally:
        _close(card)


# ---------- 2. 文字块：模型/图元/编辑/拖动/空块清理 ----------


def test_text_block_model_item_edit_move_and_empty_removal(qapp, pdf):
    note = Note("n-1", 0, 0)
    card = _show_card(note, pdf)
    view = card.view
    try:
        changes: list[int] = []
        card.contentChanged.connect(lambda: changes.append(1))
        block = view.add_text_block(40.0, 60.0, "hello")
        assert note.texts == [block]
        item = view._text_items[block.id]
        assert isinstance(item, TextBlockItem)
        assert item.toPlainText() == "hello"
        assert item.pos() == QPointF(40.0, 60.0)
        assert item.textWidth() == pytest.approx(block.w)
        assert item.font().pointSizeF() == pytest.approx(block.size)
        assert item.defaultTextColor().name() == block.color.lower()
        assert item.zValue() < 0.0                 # 压在笔迹下面
        assert item.flags() & QGraphicsItem.ItemIsSelectable
        assert item.editing is True                 # add_text_block 立即进入编辑
        assert changes

        # 双击进入编辑，打字写回模型
        item.end_edit()
        assert item.flags() & QGraphicsItem.ItemIsMovable    # 编辑结束才允许拖动
        _double_click_scene(view, 55.0, 70.0)
        assert item.editing is True
        assert item.textInteractionFlags() == Qt.TextEditorInteraction
        view.setFocus()
        QTest.keyClicks(view.viewport(), " world")
        assert block.text == "hello world"
        assert item.toPlainText() == "hello world"

        # 点空白 → 结束编辑、清掉选中、落笔；文字块留在模型里
        item.end_edit()
        assert item.editing is False
        _click_scene(view, 460.0, 620.0)
        assert item.isSelected() is False
        QTest.keyClick(view, Qt.Key_Backspace)     # 没选中：Backspace 不该删块
        assert block in note.texts

        # 拖动：松手后模型 x/y 与图元一致
        start = item.pos()
        _drag_scene(view, 70.0, 75.0, 45.0, 30.0)
        assert item.pos() != start
        assert (block.x, block.y) == pytest.approx((item.pos().x(), item.pos().y()))

        # 空文字块失焦 → 从模型与图元里一起消失
        empty = view.add_text_block(40.0, 400.0)
        assert empty in note.texts
        empty_item = view._text_items[empty.id]
        assert empty_item.editing is True
        other = view.add_text_block(40.0, 460.0, "another")   # 抢走焦点
        assert empty not in note.texts
        assert empty.id not in view._text_items
        assert empty_item.scene() is None
        assert other in note.texts
        view._text_items[other.id].end_edit()
    finally:
        _close(card)


# ---------- 3. 图片 ----------


def test_add_image_copies_asset_and_tracks_model(qapp, pdf, tmp_path):
    png = _make_png(tmp_path / "pic.png")
    note = Note("n-1", 0, 0)
    card = _show_card(note, pdf)
    view = card.view
    try:
        image = view.add_image_file(png, center=QPointF(200.0, 150.0))
        assert image is not None
        stored = noteassets.assets_dir(pdf) / image.asset
        assert stored.exists()
        assert stored != png                       # 复制成 uuid 命名，不覆盖源文件
        assert stored.read_bytes() == png.read_bytes()
        assert note.images == [image]

        item = view._image_items[image.id]
        assert isinstance(item, NoteImageItem)
        assert item.pixmap().isNull() is False
        rect = item.sceneBoundingRect()
        assert rect.width() == pytest.approx(image.w, abs=1.0)
        assert rect.height() == pytest.approx(image.h, abs=1.0)
        # 居中插入：中心就是给定的设计坐标
        assert image.x + image.w / 2.0 == pytest.approx(200.0, abs=1.0)
        assert image.y + image.h / 2.0 == pytest.approx(150.0, abs=1.0)

        item.setPos(120.0, 220.0)                  # 移动写回模型
        assert (image.x, image.y) == pytest.approx((120.0, 220.0))

        # 缺资源重建：图元跳过，模型数据保留
        stored.unlink()
        reopened = _show_card(note, pdf)
        try:
            assert reopened.view._image_items == {}
            assert note.images == [image]
        finally:
            _close(reopened)
    finally:
        _close(card)


# ---------- 4. 附件 ----------


def test_add_attachment_copies_and_double_click_opens(qapp, pdf, tmp_path, monkeypatch):
    source = tmp_path / "report.txt"
    source.write_text("hello attachment", encoding="utf-8")
    note = Note("n-1", 0, 0)
    card = _show_card(note, pdf)
    view = card.view
    try:
        opened: list[str] = []

        class _FakeDesktop:
            @staticmethod
            def openUrl(url):
                opened.append(url.toLocalFile())
                return True

        monkeypatch.setattr(note_canvas, "QDesktopServices", _FakeDesktop)

        attachment = view.add_attachment_file(source, center=QPointF(280.0, 360.0))
        assert attachment is not None
        assert attachment.name == "report.txt"
        assert attachment.size == source.stat().st_size
        stored = noteassets.assets_dir(pdf) / attachment.asset
        assert stored.exists()
        assert stored != source
        assert note.attachments == [attachment]

        item = view._attachment_items[attachment.id]
        assert isinstance(item, AttachmentItem)
        assert item.missing is False
        assert item.boundingRect().width() == pytest.approx(ATTACH_W)
        assert item.boundingRect().height() == pytest.approx(ATTACH_H)
        assert item.zValue() < 0.0

        center = item.sceneBoundingRect().center()
        _double_click_scene(view, center.x(), center.y())
        assert opened and Path(opened[0]) == stored
        assert note.strokes == []                  # 双击不会落笔

        # 附件本体丢了：卡片仍在（灰字「附件缺失」），并能从模型里删掉
        stored.unlink()
        reopened = _show_card(note, pdf)
        try:
            missing = reopened.view._attachment_items[attachment.id]
            assert missing.missing is True
            assert missing.scene() is not None
            assert reopened.view.remove_item(attachment.id) is True
            assert note.attachments == []
            assert attachment.id not in reopened.view._attachment_items
        finally:
            _close(reopened)
    finally:
        _close(card)


# ---------- 5. 鼠标路由：点富内容不落笔，点空白照常 ----------


def test_clicks_on_rich_items_do_not_draw(qapp, pdf, tmp_path):
    note = Note("n-1", 0, 0)
    card = _show_card(note, pdf)
    view = card.view
    try:
        source = tmp_path / "a.txt"
        source.write_text("x", encoding="utf-8")
        block = view.add_text_block(40.0, 60.0, "text")
        view._text_items[block.id].end_edit()
        image = view.add_image_file(_make_png(tmp_path / "pic.png"),
                                   center=QPointF(200.0, 200.0))
        attachment = view.add_attachment_file(source, center=QPointF(300.0, 320.0))
        assert image is not None and attachment is not None

        _click_scene(view, 55.0, 70.0)             # 文字
        _click_scene(view, 200.0, 200.0)           # 图片
        _click_scene(view, 300.0, 320.0)           # 附件
        assert note.strokes == []

        _click_scene(view, 460.0, 620.0)           # 空白：仍然落笔
        assert len(note.strokes) == 1
    finally:
        _close(card)


# ---------- 6. Delete 键 ----------


def test_delete_key_removes_selected_rich_but_only_chars_while_editing(qapp, pdf):
    note = Note("n-1", 0, 0)
    card = _show_card(note, pdf)
    view = card.view
    try:
        block = view.add_text_block(40.0, 60.0, "abc")
        item = view._text_items[block.id]
        item.end_edit()
        _click_scene(view, 55.0, 70.0)             # 选中（不进入编辑）
        assert item.isSelected()
        assert item.editing is False

        QTest.keyClick(view, Qt.Key_Delete)
        assert note.texts == []
        assert view._text_items == {}
        assert item.scene() is None

        # 编辑中的 Delete/Backspace 只删字符，绝不删块
        block = view.add_text_block(40.0, 60.0, "abc")
        item = view._text_items[block.id]
        assert item.editing is True
        view.setFocus()
        QTest.keyClick(view.viewport(), Qt.Key_Backspace)
        assert block.text == "ab"
        assert block in note.texts
        QTest.keyClick(view.viewport(), Qt.Key_Delete)
        assert block in note.texts
        item.end_edit()
    finally:
        _close(card)


# ---------- 7. 文字模式 ----------


def test_text_mode_click_creates_block_and_exits(qapp, pdf):
    note = Note("n-1", 0, 0)
    card = _show_card(note, pdf)
    view = card.view
    try:
        card.text_button.click()
        assert view.text_mode is True
        assert card.text_button.isChecked()
        assert not card.pen_button.isChecked()

        _click_scene(view, 120.0, 300.0)           # 文字模式：单击放一块字，不落笔
        assert view.text_mode is False
        assert card.text_button.isChecked() is False
        assert len(note.texts) == 1
        assert note.strokes == []
        block = note.texts[0]
        assert (block.x, block.y) == pytest.approx((120.0, 300.0), abs=1.5)
        assert view._text_items[block.id].editing is True

        # 第二次进文字模式后再落笔：自动退出文字模式
        card.text_button.click()
        assert view.text_mode is True
        card.pen_button.click()
        assert view.text_mode is False
        assert card.pen_button.isChecked() is True
        assert card.text_button.isChecked() is False
    finally:
        _close(card)


# ---------- 8. 关闭/重开：模型数据驱动重建 ----------


def test_reopen_card_rebuilds_text_image_attachment(qapp, pdf, tmp_path):
    png = _make_png(tmp_path / "pic.png")
    source = tmp_path / "report.txt"
    source.write_text("payload", encoding="utf-8")
    note = Note("n-1", 0, 0)

    first = _show_card(note, pdf)
    try:
        view = first.view
        block = view.add_text_block(40.0, 60.0, "重建文本")
        view._text_items[block.id].end_edit()
        image = view.add_image_file(png, center=QPointF(200.0, 200.0))
        attachment = view.add_attachment_file(source, center=QPointF(300.0, 320.0))
        assert image is not None and attachment is not None
    finally:
        _close(first)

    reopened = _show_card(note, pdf)
    try:
        view = reopened.view
        assert set(view._text_items) == {block.id}
        assert view._text_items[block.id].toPlainText() == "重建文本"
        assert (view._text_items[block.id].block.x,
                view._text_items[block.id].block.y) == pytest.approx((40.0, 60.0))
        assert set(view._image_items) == {image.id}
        assert view._image_items[image.id].pixmap().isNull() is False
        assert set(view._attachment_items) == {attachment.id}
        assert view._attachment_items[attachment.id].missing is False

        # 撤销/清屏会触发 scene.rebind()（整体 clear）：富内容必须按模型补回来
        view.scene.rebind()
        assert set(view._text_items) == {block.id}
        assert set(view._image_items) == {image.id}
        assert set(view._attachment_items) == {attachment.id}
        assert view._text_items[block.id].scene() is not None
    finally:
        _close(reopened)


# ---------- 9. z 层序：富内容压在笔迹下面 ----------


def test_rich_items_below_ink_z_order(qapp, pdf, tmp_path):
    note = Note("n-1", 0, 0)
    card = _show_card(note, pdf)
    view = card.view
    try:
        block = view.add_text_block(40.0, 60.0, "t")
        view._text_items[block.id].end_edit()
        image = view.add_image_file(_make_png(tmp_path / "pic.png"))
        assert image is not None
        text_z = view._text_items[block.id].zValue()
        image_z = view._image_items[image.id].zValue()
        assert image_z < text_z < 0.0             # 笔迹图元默认 z=0，都在它下面
    finally:
        _close(card)
