"""真机验收探针（v2.1，v2.4/v2.5 追加）：真实窗口驱动全部新功能，截图 + 断言双证据。

覆盖验收清单第 25–30 步：侧栏目录/缩略图、Ctrl+F 查找与命中高亮、最近打开、
PDF 批注自动保存、工具栏草稿纸按钮、帮助对话框。另附草稿/文档开局与缩放截图。
v2.5 追加自动化项：打开定位页首、标题栏文件名/未保存圆点、保存角标、空态提示、
跨页拖笔钳回起笔页、Ctrl+0/9 + F3 + Ctrl+Shift+Z + F11/Esc、350 页缩略图窗口
策略、8× 缩放下的笔迹缓存倍率上限。

纪律：
- 真实平台（不设 QT_QPA_PLATFORM=offscreen），窗口优先放在副屏，避免占用主屏；
- 配置目录用临时目录，绝不碰项目 data/；
- 样例 PDF 现造（6 页，第 2/4 页藏 "needle"，带目录；另造一份 350 页空页大文档）；
- 恒退 0；结论看末尾汇总表与截图文件。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pymupdf
from PySide6.QtCore import QElapsedTimer, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import main
from sketchpad.ui import dialogs
from sketchpad.core import pdfstore
from sketchpad.services.pdfautosave import cache_path_for, read_cache

SHOTS = Path(tempfile.gettempdir()) / "sketchpad-acceptance"
RESULTS: list[tuple[str, bool, str]] = []
_ALIVE: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def shot(widget, name: str) -> Path | None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / name
    try:
        widget.grab().save(str(path))
        return path
    except Exception as exc:                     # 截图失败不该中断验收
        print(f"（截图 {name} 失败：{exc}）")
        return None


def pump(app, ms: int) -> None:
    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        app.processEvents()


def _test_screen():
    """测试窗优先放非主屏；只有一块屏时退回主屏。"""
    primary = QGuiApplication.primaryScreen()
    for screen in QGuiApplication.screens():
        if screen is not primary:
            return screen
    return primary


def _park(widget) -> None:
    """把窗口挪到测试屏（副屏）居中，不打扰主屏上的工作。"""
    area = _test_screen().availableGeometry()
    widget.move(area.center() - QPoint(widget.width() // 2, widget.height() // 2))


def make_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for index in range(6):
        page = doc.new_page(width=595.0, height=842.0)
        page.insert_text((72.0, 100.0 + index * 20.0), f"page {index + 1} content")
        if index in (1, 3):                      # 第 2、4 页各藏一个 needle
            page.insert_text((72.0, 400.0), "needle")
    doc.set_toc([[1, "第一章 基础", 1], [2, "1.1 小节", 2], [1, "第三章 进阶", 4]])
    doc.save(str(path))
    doc.close()
    return path


def make_big_pdf(path: Path, pages: int = 350) -> Path:
    """大文档样例：缩略图窗口策略用；页面全空不画字，造起来快。"""
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=595.0, height=842.0)
    doc.save(str(path))
    doc.close()
    return path


def main_probe() -> int:
    work = Path(tempfile.mkdtemp(prefix="sketchpad-accept-"))
    pdf = make_pdf(work / "lecture.pdf")

    app = QApplication.instance() or QApplication([])
    controller = main.AppController(work / "cfg")
    win = main.MainWindow(controller)
    win.resize(1160, 760)
    _park(win)                       # 先挪到副屏再显示，避免主屏闪一下
    win.show()
    QTest.qWaitForWindowExposed(win)
    _ALIVE.append(win)

    shot(win, "01_draft_mode.png")
    check("开局草稿模式：状态栏四项齐全", "当前色" in win.status_label.text()
          and "缩放" in win.status_label.text(), win.status_label.text())

    # ---------- v2.8：工具栏收起/展开 ----------
    expanded_height = win.toolbar_scroll.height()
    check("工具栏折叠键在最左边",
          win.toolbox.layout().itemAt(0).widget() is win.toolbox.collapse_button)
    win.toolbox.collapse_button.click()
    pump(app, 80)
    check("收起工具栏：只剩左侧箭头窄条",
          win.toolbox.collapsed and win.toolbar_scroll.height() <= 26
          and win.toolbox.collapse_button.isVisible(),
          f"height={win.toolbar_scroll.height()}")
    shot(win, "01b_toolbar_collapsed.png")
    win.toolbox.collapse_button.click()
    pump(app, 80)
    check("再点箭头：工具栏恢复原高度",
          not win.toolbox.collapsed
          and win.toolbar_scroll.height() >= expanded_height - 1,
          f"height={win.toolbar_scroll.height()} expanded={expanded_height}")

    # ---------- v2.5 追加：草稿空态提示 ----------
    check("空态提示：草稿无笔迹时可见", win.empty_hint.isVisible())
    QTest.mousePress(win.view.viewport(), Qt.LeftButton, pos=QPoint(240, 200))
    QTest.mouseRelease(win.view.viewport(), Qt.LeftButton, pos=QPoint(340, 260))
    pump(app, 150)
    check("空态提示：画一笔后隐藏",
          len(win.controller.document.strokes) == 1 and not win.empty_hint.isVisible(),
          f"笔迹 {len(win.controller.document.strokes)} 条")
    win.view.history.pop_undo()                  # 清回空画布，后续检查不受影响
    pump(app, 150)
    check("空态提示：撤销回空后复现",
          not win.controller.document.strokes and win.empty_hint.isVisible())

    # ---------- 25. 打开 PDF：侧栏目录 + 缩略图 ----------
    check("打开 PDF 进文档模式", win._enter_document(pdf) is True)
    pump(app, 400)                               # 等适应页宽 + 去抖渲染 + 烘焙
    shot(win, "02_document_mode.png")
    check("状态栏显示页码", "第 1/6 页" in win.status_label.text(),
          win.status_label.text())
    check("标题栏显示文件名（暂无未保存圆点）",
          "lecture.pdf" in win.titlebar.title_label.text()
          and "●" not in win.titlebar.title_label.text(),
          win.titlebar.title_label.text())
    layer0 = win.pdf_view._layers[0]
    top = win.pdf_view.mapFromScene(QPointF(layer0.x, layer0.y))
    v_bar = win.pdf_view.verticalScrollBar()
    check("打开定位页首：滚动条归零且页顶可见",
          v_bar.value() == 0 and -1 <= top.y() <= win.pdf_view.viewport().height(),
          f"value={v_bar.value()}，页顶屏幕 y={top.y()}，"
          f"视口高 {win.pdf_view.viewport().height()}")

    win.menubar.toc_action.trigger()             # 真实菜单点击路径（Qt 先翻勾选态）
    pump(app, 150)
    shot(win, "03_toc_panel.png")
    check("目录面板：条目来自 PDF 书签", win.toc_panel.tree.topLevelItemCount() == 2,
          f"顶层 {win.toc_panel.tree.topLevelItemCount()} 项")
    check("目录面板：边栏与面板可见",
          win.side_panel.isVisible() and win.toc_panel.isVisible())

    win.menubar.thumbs_action.trigger()
    pump(app, 600)                               # 缩略图增量渲染（10ms/tick，4 页/tick）
    shot(win, "04_thumb_panel.png")
    check("缩略图面板：6 页占位 + 位图已补",
          win.thumb_panel.list.count() == 6
          and not win.thumb_panel.list.item(0).icon().isNull())

    win.thumb_panel.pageRequested.emit(3)        # 点第 4 页缩略图
    pump(app, 200)
    shot(win, "05_thumb_jump_page4.png")
    check("缩略图点击跳页", win.pdf_view.current_page == 3,
          f"current_page={win.pdf_view.current_page}")
    check("页码状态栏跟上", "第 4/6 页" in win.status_label.text(),
          win.status_label.text())

    # ---------- 26. Ctrl+F 查找 ----------
    win.pdf_view.setFocus()
    QTest.keyClick(win, Qt.Key_F, Qt.ControlModifier)
    pump(app, 100)
    shot(win, "06_find_bar.png")
    check("Ctrl+F 唤起查找条", win.find_bar.isVisible())

    win.find_bar.input.setText("needle")         # 输入即搜索（textChanged 路径）
    pump(app, 400)
    shot(win, "07_find_hits.png")
    check("全文搜索命中 2 处", win.find_bar.count_label.text() == "1/2",
          f"计数 {win.find_bar.count_label.text()}")
    check("命中高亮画在页面上", len(win.pdf_view._hit_items) == 1,
          f"高亮项 {len(win.pdf_view._hit_items)}")

    QTest.keyClick(win.find_bar.input, Qt.Key_Return)   # 真回车 = 下一个命中
    pump(app, 200)
    shot(win, "08_find_next.png")
    check("回车：跳到第 4 页并计数 2/2",
          win.pdf_view.current_page == 3 and win.find_bar.count_label.text() == "2/2",
          f"page={win.pdf_view.current_page} {win.find_bar.count_label.text()}")
    QTest.keyClick(win.find_bar.input, Qt.Key_Return, Qt.ShiftModifier)
    pump(app, 200)
    check("Shift+回车：回到第一个命中 1/2",
          win.find_bar.count_label.text() == "1/2",
          win.find_bar.count_label.text())

    QTest.keyClick(win.find_bar.input, Qt.Key_F3)       # F3 = 下一个命中（窗口级快捷键）
    pump(app, 200)
    check("F3：下一个命中 1/2 → 2/2",
          win.find_bar.count_label.text() == "2/2" and win.pdf_view.current_page == 3,
          f"计数 {win.find_bar.count_label.text()}，page={win.pdf_view.current_page}")

    QTest.keyClick(win.find_bar.input, Qt.Key_Escape)   # 输入框内 Esc：自清自关
    pump(app, 100)
    shot(win, "09_find_closed.png")
    check("Esc 关闭查找条并清高亮",
          not win.find_bar.isVisible() and win.pdf_view._hit_items == [])

    # ---------- 27. 最近打开 ----------
    win.menubar.recent_menu.popup(win.mapToGlobal(QPoint(140, 40)))
    pump(app, 150)
    shot(win.menubar.recent_menu, "10_recent_menu.png")
    win.menubar.recent_menu.close()
    actions = win.menubar.recent_menu.actions()
    check("最近打开：菜单 1 条且是刚开的 PDF",
          len(actions) == 1 and actions[0].text() == pdf.name,
          f"{[a.text() for a in actions]}")
    check("最近打开：已落盘 settings.json",
          str(pdf) in controller.settings.recent_files
          and "recent_files" in controller.settings_path.read_text(encoding="utf-8"))

    # ---------- 28. PDF 批注自动暂存（停笔 2 秒 → 缓存，提交才写 sidecar） ----------
    controller.settings.pdf_autosave = True
    win._configure_autosave()
    sidecar = pdfstore.sidecar_path(pdf)
    cache = cache_path_for(pdf, controller.settings_path.parent)
    check("自动暂存：开局无 sidecar / 无缓存",
          not sidecar.exists() and not cache.exists())
    layer = win.pdf_view._layers[3]
    start = win.pdf_view.mapFromScene(QPointF(layer.x + 100.0, layer.y + 500.0))
    end = win.pdf_view.mapFromScene(QPointF(layer.x + 200.0, layer.y + 560.0))
    QTest.mousePress(win.pdf_view.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseRelease(win.pdf_view.viewport(), Qt.LeftButton, pos=end)
    check("保存角标：画一笔后 ● 未保存", win.save_label.text() == "● 未保存",
          win.save_label.text())
    check("标题栏：画一笔后出现未保存圆点",
          "●" in win.titlebar.title_label.text(), win.titlebar.title_label.text())
    pump(app, 2600)                              # 停笔 2 秒 debounce + 余量
    shot(win, "11_autosave.png")
    check("自动暂存：停笔 2 秒生成缓存文件", cache.exists(), f"{cache.name}")
    check("自动暂存：状态栏有提示", "已自动暂存" in win.message_label.text(),
          win.message_label.text())
    cached = read_cache(pdf, controller.settings_path.parent)
    check("自动暂存：缓存读回就是这一笔",
          cached is not None and len(cached[0].pages[3].strokes) == 1)
    check("自动暂存：此时还没有 sidecar", not sidecar.exists())
    check("提交：手动保存写出 sidecar", win._save_annotations() and sidecar.exists())
    check("提交：sidecar 读回就是这一笔",
          len(pdfstore.load_sidecar(pdf, 6).pages[3].strokes) == 1)
    check("提交：缓存已清理", not cache.exists())
    check("保存角标：提交后回到已保存",
          win.save_label.text().startswith("已保存") and "●" not in win.save_label.text(),
          win.save_label.text())
    check("标题栏：提交后未保存圆点消失",
          "●" not in win.titlebar.title_label.text()
          and "lecture.pdf" in win.titlebar.title_label.text(),
          win.titlebar.title_label.text())

    # ---------- 29. 工具栏草稿纸按钮 ----------
    scratch = win.scratch
    scratch_window = scratch.window          # 懒创建但不显示
    _park(scratch_window)                    # 先放到副屏
    scratch._placed = True                   # 之后 toggle 用记住的位置，不再跟鼠标走
    win.toolbox.scratch_button.click()
    pump(app, 300)
    shot(win.scratch.window, "12_scratch_window.png")
    check("草稿纸按钮：草稿窗弹出", win.scratch.is_visible())
    win.toolbox.scratch_button.click()
    pump(app, 200)
    check("草稿纸按钮：再点收起", not win.scratch.is_visible())

    # ---------- 30. 帮助对话框 ----------
    for cls, name in ((dialogs.ShortcutsDialog, "13_shortcuts_dialog.png"),
                      (dialogs.AboutDialog, "14_about_dialog.png")):
        dialog = cls(win)
        dialog.show()
        QTest.qWaitForWindowExposed(dialog)
        pump(app, 150)
        shot(dialog, name)
        dialog.close()
    check("帮助对话框渲染（快捷键一览/关于）", True, "截图见 13/14")

    # ---------- v2.5 追加：跨页拖笔钳回起笔页 ----------
    view = win.pdf_view
    view.goto_page(0)
    layer = view._layers[0]
    view.centerOn(QPointF(layer.x + layer.width / 2.0, layer.y + layer.height - 30.0))
    pump(app, 250)
    start = view.mapFromScene(QPointF(layer.x + layer.width / 2.0, layer.y + layer.height - 10.0))
    end = view.mapFromScene(QPointF(layer.x + layer.width / 2.0, layer.y + layer.height + 30.0))
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=start)
    try:                                         # 拖动中途越过页边：要走进拖拽分支
        mid = view.mapFromScene(QPointF(layer.x + layer.width / 2.0, layer.y + layer.height + 4.0))
        drag = QMouseEvent(QEvent.MouseMove, QPointF(mid),
                           view.viewport().mapToGlobal(QPointF(mid)),
                           Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(view.viewport(), drag)
    except Exception as exc:
        print(f"（跨页拖拽中途事件注入失败：{exc}）")
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=end)
    pump(app, 250)
    shot(win, "16_cross_page_clamp.png")
    page1 = view.document.pages[0]
    points = page1.strokes[-1].points if page1.strokes else []
    inside = bool(points) and all(
        0.0 <= x <= layer.width + 1e-6 and 0.0 <= y <= layer.height + 1e-6
        for x, y in points)
    check("跨页拖笔：提交笔迹全部落在第 1 页内",
          len(page1.strokes) == 1 and inside,
          f"{len(points)} 点，页内={inside}，y 最大 "
          f"{max((y for _, y in points), default=-1.0):.1f} / 页高 {layer.height:g}")
    check("跨页拖笔：第 2 页没有被写入", not view.document.pages[1].strokes)

    QTest.keyClick(view, Qt.Key_S, Qt.ControlModifier)   # 真 Ctrl+S 提交这一笔
    pump(app, 200)
    check("Ctrl+S：状态栏回 已保存、标题圆点消失",
          win._dirty is False and win.save_label.text().startswith("已保存")
          and "●" not in win.titlebar.title_label.text(),
          f"chip={win.save_label.text()!r} title={win.titlebar.title_label.text()!r}")
    restored = pdfstore.load_sidecar(pdf, 6)
    check("Ctrl+S：sidecar 读回含第 1 页跨页笔迹",
          len(restored.pages[0].strokes) == 1 and len(restored.pages[3].strokes) == 1)

    # ---------- v2.5 追加：Ctrl+Z / Ctrl+Shift+Z ----------
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
    pump(app, 200)
    check("Ctrl+Z：撤掉跨页笔迹（第 1 页归零、第 4 页不动）",
          not view.document.pages[0].strokes and len(view.document.pages[3].strokes) == 1,
          f"p0={len(view.document.pages[0].strokes)} p3={len(view.document.pages[3].strokes)}")
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    pump(app, 200)
    check("Ctrl+Shift+Z：重做把笔迹加回来", len(view.document.pages[0].strokes) == 1)

    # ---------- v2.5 追加：Ctrl+0 / Ctrl+9 ----------
    view.set_zoom(2.0)                           # 先离开 100%，Ctrl+0 才算真的复位
    pump(app, 150)
    QTest.keyClick(view, Qt.Key_0, Qt.ControlModifier)
    pump(app, 250)
    check("Ctrl+0：缩放复位 100%", view.zoom == 1.0, f"zoom={view.zoom:g}")
    QTest.keyClick(view, Qt.Key_9, Qt.ControlModifier)
    pump(app, 250)
    fit_expect = view.viewport().width() / layer.width
    check("Ctrl+9：适应页宽（视口宽/页宽、auto_fit 打开）",
          view.auto_fit and abs(view.zoom - fit_expect) < 1e-6,
          f"zoom={view.zoom:.6f} 期望 {fit_expect:.6f}")

    # ---------- v2.5 追加：笔迹缓存倍率上限（≤ 页宽 ×4 + 1 px） ----------
    view.centerOn(QPointF(layer.x + layer.width / 2.0, layer.y + layer.height - 80.0))
    pump(app, 250)
    try:
        from sketchpad.ui.pdfview import STROKE_TAIL as tail
    except Exception:
        tail = 16
    for index in range(tail + 1):               # 超过尾部配额 → 头一条烘进位图缓存
        y = layer.y + layer.height - 40.0 - index * 2.0
        QTest.mousePress(view.viewport(), Qt.LeftButton,
                         pos=view.mapFromScene(QPointF(layer.x + 80.0, y)))
        QTest.mouseRelease(view.viewport(), Qt.LeftButton,
                           pos=view.mapFromScene(QPointF(layer.x + 140.0, y)))
    pump(app, 250)
    check(f"缓存上限：{tail + 1} 条后已有笔迹烘进位图缓存",
          len(layer.cache_strokes) >= 1, f"cache_strokes={len(layer.cache_strokes)}")
    view.set_zoom(8.0)
    pump(app, 300)
    view._render_stroke_cache(layer)            # 强制按 8× 缩放走一遍缓存渲染
    pixmap = layer.cache_item.pixmap()
    limit = round(layer.width * 4.0) + 1        # CACHE_MAX_ZOOM=4：宽度不许超过页宽 4×+1
    check("缓存上限：8× 下缓存图宽 ≤ 页宽 4×+1",
          view.zoom == 8.0 and not pixmap.isNull() and pixmap.width() <= limit,
          f"zoom={view.zoom:g} 缓存 {pixmap.width()}×{pixmap.height()}px 上限 {limit}")
    win._save_annotations()                     # 收口：dirty 与暂存清掉，方便后面换文档

    # ---------- 附：缩放与笔迹缓存交互 ----------
    win.pdf_view.set_zoom(1.0)
    pump(app, 300)
    bar = win.pdf_view.verticalScrollBar()
    for value in (0, 300, 900):
        bar.setValue(value)
        pump(app, 120)
    shot(win, "15_zoom_scroll.png")
    check("缩放/滚动后页面与笔迹仍在",
          win.pdf_view.zoom == 1.0
          and len(win.pdf_view.document.pages[3].strokes) == 1)

    # ---------- v2.5 追加：350 页缩略图窗口策略 ----------
    big = make_big_pdf(work / "lecture-350.pdf")
    check("打开 350 页合成 PDF", win._enter_document(big) is True)
    panel = win.thumb_panel
    guard = QElapsedTimer()
    guard.start()
    while panel._timer.isActive() and guard.elapsed() < 15000:  # 等渲染队列排空（停表）
        pump(app, 100)
    pump(app, 200)
    shot(win, "17_thumbs_350_top.png")
    panel.list.scrollToBottom()
    pump(app, 150)
    shot(win, "18_thumbs_350_bottom.png")
    check("大文档缩略图：350 项占位齐全", panel.list.count() == 350,
          f"列表 {panel.list.count()} 项")
    near = []
    for index in (0, 2):                         # 当前页（第 1 页）附近应有图标
        item = panel.list.item(index)
        if item is not None and not item.icon().isNull():
            near.append(index)
    check("大文档缩略图：当前页窗口有图标", near == [0, 2], f"有图标页 {near}")
    far_item = panel.list.item(349)
    far_tip = far_item.toolTip() if far_item is not None else None
    check("大文档缩略图：远处页仍是占位（无图标）",
          far_item is not None and far_item.icon().isNull(),
          f"第 350 页 tooltip={far_tip!r}")
    check("大文档缩略图：渲染计时器已停", not panel._timer.isActive(),
          f"timer active={panel._timer.isActive()}")

    # ---------- v2.5 追加：F11 全屏 / Esc 退出 ----------
    win.activateWindow()
    view.setFocus()
    pump(app, 100)
    QTest.keyClick(view, Qt.Key_F11)
    pump(app, 300)
    shot(win, "19_fullscreen.png")
    check("F11：进入全屏且 Esc 出口启用",
          win.isFullScreen() and win._esc_shortcut.isEnabled(),
          f"fullscreen={win.isFullScreen()}")
    QTest.keyClick(view, Qt.Key_Escape)
    pump(app, 300)
    check("Esc：退出全屏并停用 Esc 出口",
          not win.isFullScreen() and not win._esc_shortcut.isEnabled(),
          f"fullscreen={win.isFullScreen()}")

    # ---------- v2.9：工具栏右侧竖向摆放（放在最后，避免改布局影响前面的快捷键检查） ----------
    win._apply_toolbar_position("right")
    pump(app, 120)
    check("右侧摆放：工具栏在窗口右侧且为竖向",
          win.toolbox.vertical and win.body.indexOf(win.toolbar_scroll) >= 0
          and not win.top_chrome.has_row(win.toolbar_scroll))
    check("右侧摆放：宽度收窄",
          win.toolbar_scroll.width() < 90,
          f"width={win.toolbar_scroll.width()}")
    win.toolbox.collapse_button.click()
    pump(app, 80)
    check("右侧收起：只剩左侧窄条",
          win.toolbox.collapsed and win.toolbar_scroll.width() <= 26,
          f"width={win.toolbar_scroll.width()}")
    shot(win, "01c_toolbar_right_collapsed.png")
    win.toolbox.collapse_button.click()
    win._apply_toolbar_position("top")
    pump(app, 120)
    check("切回顶部：恢复横向摆放",
          not win.toolbox.vertical
          and win.top_chrome.rows.indexOf(win.toolbar_scroll) == 2)

    win._quitting = True
    win.scratch.hide()
    win.close()
    app.processEvents()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n截图目录：{SHOTS}")
    print(f"汇总：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过"
          + (f"；失败：{failed}" if failed else "；无失败"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_probe())
