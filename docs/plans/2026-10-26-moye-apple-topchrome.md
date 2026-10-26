# 墨页方向 A：统一玻璃顶栏实施计划

> **给自动执行者：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务执行。每个步骤用 `- [ ]` 勾选，不要跳任务。
>
> **给人类协作者：** 先读 §0 和 §2。`app.py`、`theme.py` 是集成热点，同一时间只能有一个写者。接口以 §3 为准；要改接口，先改本文件。

**目标：** 把主窗原来三条各自铺底的横栏（标题栏 / 菜单栏 / 工具栏）收进同一块圆角玻璃面板，去掉横向分割线和工具栏竖线，状态文字改成底部浮动胶囊；功能、控件顺序、快捷键、数据与导出全部不变。

**架构：** 新增透明外壳 `TopChrome` + 内层玻璃面板 `QFrame#TopChromePanel`；标题栏、菜单栏、工具栏仍是原来的控件，只把它们挂进 TopChrome 的行布局。工具栏在右侧竖排时仍搬回 body，TopChrome 只留两行。状态栏改用 `StatusPill` 单胶囊。

**技术栈：** Python 3.13、PySide6 6.12 / Qt 6.12、QSS、QPainter 自绘阴影、pytest 离屏测试。

**Spec：** `docs/plans/2026-10-25-moye-apple-ui.md`（方向 A）

---

## 0. 协作规则

1. **不改行为。** 不改功能、控件顺序/数量/可见性规则、快捷键、sidecar v4 数据、导出、目录/缩略图/笔记/分屏逻辑。
2. **不改范围。** 本轮只动主窗外壳。草稿窗、笔记窗、设置/查找/关闭确认等弹窗留到 Stage C。
3. **不做真 Acrylic。** 玻璃是半透明 QSS + 自绘底色，不启用 DWM Acrylic，避免手写和滚动掉帧。
4. **文件归属。** 一个文件同一时间一个写者：
   - 顶栏与主题：`sketchpad/ui/top_chrome.py`、`sketchpad/ui/theme.py`
   - 主窗集成：`sketchpad/app.py`
   - 工具栏：`sketchpad/ui/toolbox.py`
   - 状态栏：`sketchpad/ui/status_pill.py`
   - 测试：同一既有测试文件一次只由一人改；新建测试文件可并行。
5. **提交。** 每个任务结束只报告“可提交”；必须经用户明确批准后才执行 `git commit`。提交信息附三样：改了什么、跑了什么、没验证什么。
6. **截图。** 只用 `WA_DontShowOnScreen` / offscreen 的程序化 `grab()`，不操控鼠标。

并行方式：测试用例（`tests/test_top_chrome.py`、`tests/test_status_pill.py`、`tests/test_toolbox_offscreen.py` 的新断言）可以由不同人先写；实现按 Task 1 → Task 5 串行，因为 `theme.py` 和 `app.py` 是共享热点。若必须并行实现，先由顶栏 owner 把 `theme.py` 的相关块一次性落完，其他人只读。

基线命令（开工前跑一次，确认起点全绿）：

```bash
python -m pytest tests -q
python -m pytest tests --collect-only -q
```

记下 `--collect-only` 的用例数。当前基线为 917 条分组用例；实施后以新计数复核，新增和删除都要在报告里写明。

---

## 1. 现状证据与目标

### 1.1 现在为什么“被线切碎”

| 现象 | 代码位置 |
|---|---|
| 标题栏自己有底 + 底边线 | `sketchpad/ui/theme.py:43` |
| 菜单栏自己有底 + 底边线 | `sketchpad/ui/menubar.py:31-37` |
| 工具栏自己是一张带边框的卡 | `sketchpad/ui/theme.py:59-63` |
| 状态栏有顶边线、贴满整宽 | `sketchpad/ui/theme.py:171-183` |
| 标题/菜单/工具栏是三条独立行 | `sketchpad/app.py:352-361` |
| 工具栏靠 6 条竖线分组 | `sketchpad/ui/toolbox.py:319-320`、`:337-359` |
| 状态文字直接排在状态栏里 | `sketchpad/app.py:405-422` |

### 1.2 目标布局

```
┌──────────────────── 窗口 ────────────────────┐
│  ┌──────────── TopChromePanel（圆角 16）────┐  │
│  │ (头像) 墨页  [笔记 1] [+]               │  │
│  │ 文件  编辑  视图  导航  工具  帮助       │  │  ← 无底线
│  │ [撤销 重做]  [工具组]  [粗细]  [颜色]   │  │  ← 无竖线
│  └─────────────────────────────────────────┘  │
│                                                │
│  内容区（PDF / 草稿 / 笔记）                    │
│                                                │
│  ┌──────────── 状态胶囊 ────────────┐           │
│  │ 提示文字            已保存  1/10 │           │
│  └──────────────────────────────────┘           │
└────────────────────────────────────────────────┘
```

### 1.3 尺寸预算

| 项 | 现状 | 目标 | 增量 |
|---|---:|---:|---:|
| 标题栏 | 36px | 36px | 0 |
| 菜单栏 | 约 25px | 约 25px | 0 |
| 工具栏展开 | 约 40px | 约 40px | 0 |
| 面板行距 | 0 | 2px × 2 | +4 |
| 面板底部内边距 | 0 | 2px | +2 |
| 外壳上/下外边距 | 0 | 4px + 6px（下边距给自绘阴影） | +10 |
| **顶栏合计** | **约 101px** | **约 117px** | **约 +16px** |
| 状态栏 | 约 22px | 约 26px | 约 +4px |

硬约束：1160×760 展开态，`win.top_chrome.height() <= 126`；792×760 时全屏批注按钮仍完整可见。

> 默认窗口宽 1160（2026-10-26 修订）：`MainWindow.resize(1160, 760)`，视口 1132px 能放下 PDF 模式的整条工具栏（1124px，余量 8px），
> 不再出现横向滚动条、设置齿轮不被裁。792 仍是窄窗/横向滚动检查；顶栏高度预算按 1100×760 的窄档量（≤126px），1160×760 是默认 PDF 适配档。

### 1.4 颜色与材质 token

| token | 值 | 用途 |
|---|---|---|
| `GLASS_TOP_ALPHA` | `0.58` | 顶栏玻璃不透明度 |
| `GLASS_TOP` | `rgba(255, 252, 253, 0.58)` | 顶栏面板底 |
| `GLASS_TOP_BORDER` | `rgba(236, 171, 195, 0.48)` | 顶栏 1px 边 |
| `GLASS_STATUS_ALPHA` | `0.52` | 状态胶囊/右侧工具栏卡 |
| `GLASS_STATUS` | `rgba(255, 255, 255, 0.52)` | 状态胶囊底 |
| `GLASS_STATUS_BORDER` | `rgba(236, 171, 195, 0.55)` | 状态胶囊边 |
| `RADIUS_CHROME` | `16` | 顶栏圆角 |
| `RADIUS_PILL` | `11` | 状态胶囊圆角 |

文字仍用 `TEXT = #4B2F38`；玻璃 alpha 不低于 0.5，保证 AA 对比度。

### 1.5 已定决策

| 决策 | 选择 | 理由 | 不选的原因 |
|---|---|---|---|
| 顶栏阴影 | `TopChrome` 自绘 5 层底部阴影 | 不被父控件裁切，hover 不重新模糊 | `QGraphicsDropShadowEffect` 会被外壳边界切掉 |
| 状态胶囊宽度 | 最大 680px | 不贴满整宽，长提示也放得下 | 全宽会退回“一条横带” |
| 右排工具栏 | 搬回 body，单独玻璃卡 | 竖栏塞进横条会破坏顶栏比例 | 让竖栏也进 TopChrome |
| 分隔线 | 全部删除，改横排 9px / 竖排 10px 留白 | 用户明确说“模块之间一条线划过” | 只把线调浅仍然像格子 |
| 顶栏范围 | 只包标题/菜单/工具栏 | 查找条和侧栏仍各自独立 | 把所有东西塞进一块面板 |

---

## 2. 文件结构

| 动作 | 文件 | 责任 |
|---|---|---|
| 新建 | `sketchpad/ui/top_chrome.py` | 透明外壳 + 玻璃面板 + 行容器 |
| 新建 | `sketchpad/ui/status_pill.py` | 底部单胶囊与三个标签 |
| 新建 | `tests/test_top_chrome.py` | 顶栏、行顺序、尺寸预算、主窗集成 |
| 新建 | `tests/test_status_pill.py` | 胶囊结构与状态栏无分割线 |
| 新建 | `tools/ui_snap_topchrome.py` | 可重复的离屏截图工具 |
| 修改 | `sketchpad/ui/theme.py` | 玻璃 token、顶栏/工具栏/状态栏 QSS、删除 `QFrame#Sep` |
| 修改 | `sketchpad/ui/titlebar.py` | 行内边距缩到 2px，配合面板外边距 |
| 修改 | `sketchpad/ui/menubar.py` | 菜单栏透明、无底边 |
| 修改 | `sketchpad/ui/toolbox.py` | `dock` 属性、删除分隔线、改分组留白 |
| 修改 | `sketchpad/app.py` | 装配 TopChrome、状态胶囊、工具栏搬移 |
| 修改 | `tests/test_menubar_offscreen.py` | 菜单栏透明后的 QSS 断言 |
| 修改 | `tests/test_toolbox_offscreen.py` | 分隔线测试改为分组留白测试 |
| 修改 | `tests/test_theme_offscreen.py` | 删除 Sep 断言，改测玻璃 token |
| 修改 | `tests/test_app_modes_offscreen.py` | 顶层布局顺序改为 TopChrome 内行顺序 |
| 修改 | `tests/test_window_offscreen.py` | 顶部/右侧工具栏位置断言 |
| 修改 | `tests/test_settings_dialog_offscreen.py` | 右侧工具栏断言 |
| 修改 | `docs/plans/2026-10-25-moye-apple-ui.md` | 状态行链接到本计划 |
| 修改 | `PROGRESS.md` | 完成记录与未验证项 |

目标层级：

```
QMainWindow
└ ShellBackground#Root
   ├ TopChrome#TopChrome（透明外壳，外边距 8/4/8/6，自绘底部阴影）
   │  └ QFrame#TopChromePanel（玻璃，圆角 16）
   │     └ QVBoxLayout#TopChromeRows（行距 2，内边距 6/0/6/2）
   │        ├ TitleBar#TitleBar（透明）
   │        ├ Menubar#Menubar（透明）
   │        └ QScrollArea#ToolbarScroll
   │           └ Toolbox#Toolbar[dock="top"]（透明）
   ├ FindBar#FindBar（本轮不动，仍在顶栏下方）
   └ body（侧栏 + 视图栈；右排时工具栏在 body 最右）
QMainWindow.statusBar()（透明，无边）
└ StatusPill#StatusPill（最大宽 680）
```

---

## 3. 接口契约

### 3.1 `TopChrome`

```python
SHADOW_GAP = 6
HOUSE_MARGINS = (8, 4, 8, SHADOW_GAP)   # left, top, right, bottom
PANEL_MARGINS = (6, 0, 6, 2)
ROW_SPACING = 2
PANEL_RADIUS = theme.RADIUS_CHROME

class TopChrome(QWidget):
    panel: QFrame                 # objectName "TopChromePanel"
    rows: QVBoxLayout             # 标题/菜单/工具栏按顺序挂在这里

    def add_row(self, widget: QWidget) -> None: ...
    def remove_row(self, widget: QWidget) -> None: ...
    def has_row(self, widget: QWidget) -> bool: ...
```

规则：
- `add_row` 只追加，不重排。右排切回顶排时工具栏自然回到第 3 行。
- `remove_row` 只从布局摘下，不销毁、不 reparent；由调用方的 `body.addWidget` 完成搬移。
- `TopChrome` 与 `panel` 都是 `Qt.NoFocus`，不抢画布键盘焦点。
- 阴影由 `TopChrome.paintEvent` 在面板下方自绘：5 层圆角矩形，向下偏移 1–5px，颜色 `(90, 40, 55)`，alpha 依次 18/14/10/6/3。不用 `QGraphicsDropShadowEffect`，避免被外壳裁切和 hover 时反复重模糊。
- 外壳下边距 `SHADOW_GAP = 6` 就是阴影的可见空间；面板底边到窗口内容之间始终留 6px。

### 3.2 `StatusPill`

```python
PILL_MAX_WIDTH = 680

class StatusPill(QFrame):
    message: QLabel   # 普通提示，拉伸
    save: QLabel      # objectName "SaveStatus"
    status: QLabel    # objectName "StatusValue"
```

`app.py` 必须保留 `self.message_label`、`self.save_label`、`self.status_label` 三个别名，现有业务代码和测试只认这三个名字。

### 3.3 `Toolbox`

```python
def set_dock(self, dock: str) -> None:
    """dock 只取 "top" / "right"；只改 QSS 动态属性，不碰控件顺序。"""
```

初始属性为 `"top"`。`MainWindow._apply_toolbar_position` 在切换时同时调用 `set_vertical` 与 `set_dock`。

### 3.4 QSS 选择器

```css
QFrame#TopChromePanel
QScrollArea#ToolbarScroll > QWidget > QWidget#Toolbar[dock="top"]
QScrollArea#ToolbarScroll > QWidget > QWidget#Toolbar[dock="right"]
QStatusBar
QFrame#StatusPill
QFrame#StatusPill QLabel
QFrame#StatusPill QLabel#SaveStatus
QFrame#StatusPill QLabel#StatusValue
```

---

## 4. 任务

### Task 1：玻璃顶栏外壳

**文件：**
- 新建：`sketchpad/ui/top_chrome.py`
- 修改：`sketchpad/ui/theme.py`（新增 token 与 `QFrame#TopChromePanel` 规则）
- 测试：`tests/test_top_chrome.py`

**接口：**
- 产出：`TopChrome`、`HOUSE_MARGINS`、`PANEL_MARGINS`、`ROW_SPACING`、`PANEL_RADIUS`、`SHADOW_GAP`
- 消费：`theme.RADIUS_CHROME`、`theme.GLASS_TOP`、`theme.GLASS_TOP_BORDER`

- [ ] **Step 1：写失败测试**

新建 `tests/test_top_chrome.py`：

```python
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from sketchpad.ui import theme
from sketchpad.ui.titlebar import TitleBar
from sketchpad.ui.top_chrome import (
    HOUSE_MARGINS, PANEL_MARGINS, PANEL_RADIUS, ROW_SPACING, SHADOW_GAP, TopChrome,
)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _rows(chrome: TopChrome) -> list[QWidget]:
    return [chrome.rows.itemAt(i).widget() for i in range(chrome.rows.count())]


def _qss_block(qss: str, selector: str) -> str:
    return qss.split(selector, 1)[1].split("}", 1)[0]


def _rgb(name: str) -> tuple[int, int, int]:
    value = name.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        value = value / 255.0
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(item) for item in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    first, second = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (first + 0.05) / (second + 0.05)


def test_panel_geometry_and_rows(qapp):
    chrome = TopChrome()
    chrome.resize(1100, 130)
    first, second, third = QWidget(), QWidget(), QWidget()
    for widget in (first, second, third):
        widget.setFixedHeight(24)
    try:
        for widget in (first, second, third):
            chrome.add_row(widget)
        chrome.show()
        QTest.qWait(20)

        assert chrome.objectName() == "TopChrome"
        assert chrome.panel.objectName() == "TopChromePanel"
        assert _rows(chrome) == [first, second, third]
        assert chrome.rows.spacing() == ROW_SPACING

        outer = chrome.layout().contentsMargins()
        assert (outer.left(), outer.top(), outer.right(), outer.bottom()) == HOUSE_MARGINS
        inner = chrome.rows.contentsMargins()
        assert (inner.left(), inner.top(), inner.right(), inner.bottom()) == PANEL_MARGINS
        assert chrome.panel.x() == HOUSE_MARGINS[0]
        assert chrome.panel.y() == HOUSE_MARGINS[1]
        assert (chrome.panel.width()
                == chrome.width() - HOUSE_MARGINS[0] - HOUSE_MARGINS[2])
    finally:
        chrome.close()


def test_remove_row_keeps_widget_alive(qapp):
    from shiboken6 import isValid

    chrome = TopChrome()
    widget = QWidget()
    try:
        chrome.add_row(widget)
        assert chrome.has_row(widget) is True
        chrome.remove_row(widget)
        assert chrome.has_row(widget) is False
        assert isValid(widget) is True
    finally:
        widget.deleteLater()
        chrome.close()


def test_chrome_reserves_space_for_bottom_shadow(qapp):
    chrome = TopChrome()
    chrome.resize(400, 120)
    try:
        chrome.show()
        QTest.qWait(20)
        assert SHADOW_GAP == HOUSE_MARGINS[3] == 6
        assert (chrome.panel.y() + chrome.panel.height()
                == chrome.height() - SHADOW_GAP)
    finally:
        chrome.close()


def test_chrome_never_takes_keyboard_focus(qapp):
    chrome = TopChrome()
    try:
        assert chrome.focusPolicy() == Qt.NoFocus
        assert chrome.panel.focusPolicy() == Qt.NoFocus
    finally:
        chrome.close()


def test_glass_token_is_in_theme_qss():
    block = _qss_block(theme.QSS, "QFrame#TopChromePanel")
    assert theme.GLASS_TOP in block
    assert theme.GLASS_TOP_BORDER in block
    assert f"border-radius: {PANEL_RADIUS}px" in block
    assert PANEL_RADIUS == theme.RADIUS_CHROME


def test_glass_text_contrast_is_at_least_aa():
    alpha = theme.GLASS_TOP_ALPHA
    background = _rgb(theme.WINDOW_BG)
    glass = (255, 252, 253)
    mixed = tuple(round(glass[i] * alpha + background[i] * (1 - alpha))
                  for i in range(3))
    assert _contrast(_rgb(theme.TEXT), mixed) >= 4.5
```

- [ ] **Step 2：跑测试确认失败**

```bash
python -m pytest tests/test_top_chrome.py -q
```

预期：`ModuleNotFoundError: No module named 'sketchpad.ui.top_chrome'`。

- [ ] **Step 3：加主题 token 与 QSS**

在 `sketchpad/ui/theme.py` 的 `PAPER` 之后加入：

```python
GLASS_TOP_ALPHA = 0.58
GLASS_TOP = f"rgba(255, 252, 253, {GLASS_TOP_ALPHA})"
GLASS_TOP_BORDER = "rgba(236, 171, 195, 0.48)"
GLASS_STATUS_ALPHA = 0.52
GLASS_STATUS = f"rgba(255, 255, 255, {GLASS_STATUS_ALPHA})"
GLASS_STATUS_BORDER = "rgba(236, 171, 195, 0.55)"
RADIUS_CHROME = 16
RADIUS_PILL = 11
```

在 `QWidget#TitleBar` 规则之前加入：

```css
QFrame#TopChromePanel {{
    background: {GLASS_TOP};
    border: 1px solid {GLASS_TOP_BORDER};
    border-radius: {RADIUS_CHROME}px;
}}
```

- [ ] **Step 4：新建 `sketchpad/ui/top_chrome.py`**

```python
"""主窗统一玻璃顶栏：一块材质包住标题、菜单、工具栏。

只负责容器与材质；标题栏/菜单/工具栏仍由各自类处理行为与信号。
横向工具栏模式下三行都在这里；右侧竖栏模式只留标题与菜单（工具栏由
MainWindow 搬回 body）。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from sketchpad.ui import theme

SHADOW_GAP = 6
HOUSE_MARGINS = (8, 4, 8, SHADOW_GAP)   # left, top, right, bottom
PANEL_MARGINS = (6, 0, 6, 2)
ROW_SPACING = 2
PANEL_RADIUS = theme.RADIUS_CHROME
SHADOW_LAYERS = ((1, 18), (2, 14), (3, 10), (4, 6), (5, 3))
SHADOW_COLOR = QColor(90, 40, 55)


class TopChrome(QWidget):
    """透明外壳 + 一块玻璃面板；面板内的 rows 是标题/菜单/工具栏的家。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopChrome")
        self.setFocusPolicy(Qt.NoFocus)

        self.panel = QFrame(self)
        self.panel.setObjectName("TopChromePanel")
        self.panel.setAttribute(Qt.WA_StyledBackground, True)
        self.panel.setFocusPolicy(Qt.NoFocus)

        self.rows = QVBoxLayout(self.panel)
        self.rows.setContentsMargins(*PANEL_MARGINS)
        self.rows.setSpacing(ROW_SPACING)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(*HOUSE_MARGINS)
        outer.setSpacing(0)
        outer.addWidget(self.panel)

    def paintEvent(self, event) -> None:
        """在面板下方自绘 5 层低透明度圆角阴影；不裁切，也不参与 hover 重绘。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        panel = QRectF(self.panel.geometry())
        # 只画在面板下方：否则阴影会透进 0.58 透明度的玻璃里，把整块顶栏压暗
        painter.setClipRect(QRectF(panel.left(), panel.bottom(),
                                   panel.width(), SHADOW_GAP))
        for offset, alpha in SHADOW_LAYERS:
            color = QColor(SHADOW_COLOR)
            color.setAlpha(alpha)
            path = QPainterPath()
            path.addRoundedRect(panel.adjusted(0, offset, 0, offset),
                                PANEL_RADIUS, PANEL_RADIUS)
            painter.fillPath(path, color)
        painter.end()

    def add_row(self, widget: QWidget) -> None:
        self.rows.addWidget(widget)

    def remove_row(self, widget: QWidget) -> None:
        self.rows.removeWidget(widget)

    def has_row(self, widget: QWidget) -> bool:
        return self.rows.indexOf(widget) >= 0
```

- [ ] **Step 5：跑测试确认通过**

```bash
python -m pytest tests/test_top_chrome.py -q
```

预期：6 passed。

- [ ] **Step 6：报告可提交**

```bash
git add sketchpad/ui/top_chrome.py sketchpad/ui/theme.py tests/test_top_chrome.py
```

经用户批准后：

```bash
git commit -m "feat(ui): add unified glass top chrome"
```

---

### Task 2：去掉标题栏与菜单栏的横线

**文件：**
- 修改：`sketchpad/ui/theme.py:43`
- 修改：`sketchpad/ui/menubar.py:31-37`
- 修改：`sketchpad/ui/titlebar.py:62-64`
- 测试：`tests/test_top_chrome.py`、`tests/test_menubar_offscreen.py`

**接口：**
- 消费：Task 1 的 `TopChrome`
- 产出：标题栏/菜单栏都变成透明、无边框、无底边

- [ ] **Step 1：写失败测试**

在 `tests/test_top_chrome.py` 追加：

```python
from sketchpad.ui.menubar import MENUBAR_QSS


def test_titlebar_has_no_divider_and_no_fill():
    block = _qss_block(theme.QSS, "QWidget#TitleBar")
    assert "border-bottom" not in block
    assert "transparent" in block


def test_menubar_has_no_divider_and_no_fill():
    block = _qss_block(MENUBAR_QSS, "QMenuBar#Menubar")
    assert "border-bottom" not in block
    assert "transparent" in block


def test_titlebar_row_margins_are_compact(qapp):
    bar = TitleBar("墨页")
    try:
        margins = bar.layout().contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (2, 0, 2, 0)
    finally:
        bar.close()
```

- [ ] **Step 2：跑测试确认失败**

```bash
python -m pytest tests/test_top_chrome.py -q
```

预期：三条新用例失败（还能看到 `border-bottom` / `rgba`）。

- [ ] **Step 3：改标题栏 QSS**

`sketchpad/ui/theme.py`：

```css
QWidget#TitleBar {{ background: transparent; border: none; }}
```

- [ ] **Step 4：改菜单栏 QSS**

`sketchpad/ui/menubar.py` 的 `MENUBAR_QSS` 首段替换为：

```python
QMenuBar#Menubar {{
    background: transparent;
    color: {theme.TEXT};
    border: none;
    padding: 0 4px;
}}
```

`QMenuBar#Menubar QMenu` 及其后的弹出菜单规则不变。

- [ ] **Step 5：缩标题栏行内边距**

`sketchpad/ui/titlebar.py` 的 `row.setContentsMargins(10, 0, 4, 0)` 改为：

```python
row.setContentsMargins(2, 0, 2, 0)     # 左右留白交给 TopChromePanel（6px + 外壳 8px）
```

- [ ] **Step 6：更新旧测试**

`tests/test_menubar_offscreen.py` 中：

```python
def test_scoped_qss_styles_the_bar_and_its_menus_for_dark_theme(bar):
    qss = bar.styleSheet()
    assert bar.objectName() == "Menubar"
    assert "QMenuBar#Menubar" in qss
    assert "QMenuBar#Menubar QMenu" in qss
    assert theme.TEXT in qss
    assert "background: transparent" in qss
    assert "border-bottom" not in qss.split("QMenuBar#Menubar {", 1)[1].split("}", 1)[0]
    assert theme.ACCENT_SOFT in qss
```

删除 `test_rendered_bar_paints_the_dark_shell_background`。它测的是旧设计“菜单栏自己铺一层不透明底”，透明后不再成立；新的验收由上面的 QSS 断言和 Task 6 的整窗截图承担。

- [ ] **Step 7：跑测试**

```bash
python -m pytest tests/test_top_chrome.py tests/test_menubar_offscreen.py -q
```

预期：全部通过。

- [ ] **Step 8：报告可提交**

```bash
git add sketchpad/ui/theme.py sketchpad/ui/menubar.py sketchpad/ui/titlebar.py tests/test_top_chrome.py tests/test_menubar_offscreen.py
```

经用户批准后提交。

---

### Task 3：把三行装进顶栏，并保持左右两种工具栏位置

**文件：**
- 修改：`sketchpad/app.py`
- 修改：`sketchpad/ui/toolbox.py`
- 修改：`sketchpad/ui/theme.py`
- 测试：`tests/test_top_chrome.py`、`tests/test_app_modes_offscreen.py`、`tests/test_window_offscreen.py`、`tests/test_settings_dialog_offscreen.py`

**接口：**
- 消费：`TopChrome.add_row/remove_row/has_row`、`Toolbox.set_dock`
- 产出：`MainWindow.top_chrome`、`Toolbox` 的 `dock` 动态属性

- [ ] **Step 1：写失败测试**

在 `tests/test_top_chrome.py` 追加：

```python
import main


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


def test_main_window_puts_three_rows_in_one_panel(win):
    assert win.top_chrome.rows.indexOf(win.titlebar) == 0
    assert win.top_chrome.rows.indexOf(win.menubar) == 1
    assert win.top_chrome.rows.indexOf(win.toolbar_scroll) == 2
    assert win.titlebar.parent() is win.top_chrome.panel
    assert win.menubar.parent() is win.top_chrome.panel
    assert win.toolbar_scroll.parent() is win.top_chrome.panel
    assert win.centralWidget().layout().indexOf(win.top_chrome) == 0


def test_chrome_height_stays_in_budget(win):
    QTest.qWait(40)
    assert win.top_chrome.height() <= 126


def test_right_toolbar_leaves_chrome_with_two_rows(win):
    win._apply_toolbar_position("right")
    QTest.qWait(40)
    assert win.top_chrome.rows.count() == 2
    assert win.top_chrome.has_row(win.toolbar_scroll) is False
    assert win.body.indexOf(win.toolbar_scroll) == win.body.count() - 1

    win._apply_toolbar_position("top")
    QTest.qWait(40)
    assert win.top_chrome.rows.indexOf(win.toolbar_scroll) == 2


def test_annotate_button_fits_at_792(win):
    win.resize(792, 760)
    QTest.qWait(60)
    button = win.toolbox.annotate_button
    assert button.isVisible() is True
    corner = button.mapTo(win.toolbar_scroll.viewport(), QPoint(0, 0))
    assert corner.x() >= 0
    assert corner.x() + button.width() <= win.toolbar_scroll.viewport().width()
```

- [ ] **Step 2：跑测试确认失败**

```bash
python -m pytest tests/test_top_chrome.py -q
```

预期：`AttributeError: 'MainWindow' object has no attribute 'top_chrome'`。

- [ ] **Step 3：加 `Toolbox.set_dock`**

`sketchpad/ui/toolbox.py` 的 `__init__` 里，`self.setObjectName("Toolbar")` 之后加：

```python
self.setProperty("dock", "top")        # QSS：顶排透明，右排独立玻璃卡
```

在 `set_vertical` 之前加入：

```python
    def set_dock(self, dock: str) -> None:
        """QSS 用：顶部并进顶栏（透明），右侧独立成玻璃卡。"""
        if dock not in ("top", "right"):
            raise ValueError(f"未知工具栏位置：{dock!r}（只支持 top / right）")
        if self.property("dock") == dock:
            return
        self.setProperty("dock", dock)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()
```

- [ ] **Step 4：改工具栏 QSS**

`sketchpad/ui/theme.py` 原来的：

```css
QScrollArea#ToolbarScroll > QWidget > QWidget#Toolbar {{
    background: rgba(255, 255, 255, 0.32);
    border: 1px solid rgba(236, 171, 195, 0.62);
    border-radius: 12px;
}}
```

替换为：

```css
QScrollArea#ToolbarScroll > QWidget > QWidget#Toolbar[dock="top"] {{
    background: transparent;
    border: none;
}}
QScrollArea#ToolbarScroll > QWidget > QWidget#Toolbar[dock="right"] {{
    background: {GLASS_STATUS};
    border: 1px solid {GLASS_STATUS_BORDER};
    border-radius: 12px;
}}
```

- [ ] **Step 5：在 `MainWindow` 里装配 TopChrome**

`sketchpad/app.py` 导入区加入：

```python
from sketchpad.ui.top_chrome import TopChrome
```

把 `central = ShellBackground(self)` 到 `column.addWidget(self.find_bar)` 一段替换为：

```python
        central = ShellBackground(self)
        central.setObjectName("Root")
        central.setAttribute(Qt.WA_StyledBackground, True)   # 同上：不吃 QSS 背景
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.top_chrome = TopChrome(central)
        self.top_chrome.add_row(self.titlebar)
        self.top_chrome.add_row(self.menubar)
        self.top_chrome.add_row(self.toolbar_scroll)
        column.addWidget(self.top_chrome)
        column.addWidget(self.find_bar)
```

`self.column = column`、`body`、`setCentralWidget` 等后续行保持原样。

- [ ] **Step 6：改工具栏搬移逻辑**

`MainWindow._apply_toolbar_position` 替换为：

```python
    def _apply_toolbar_position(self, position: str) -> None:
        """把工具栏搬到顶部顶栏或右侧竖排。

        顶排：工具栏在 TopChrome 的第 3 行；右排：塞进 body 行最右（视图栈之后）。
        两套布局共用同一个 QScrollArea，所以换位置 = 从一套布局摘下来、挂到另一套。
        """
        vertical = position == "right"
        self.toolbox.set_vertical(vertical)
        self.toolbox.set_dock("right" if vertical else "top")
        self.toolbar_scroll.setMinimumWidth(0)
        self.toolbar_scroll.setMaximumWidth(MAX_WIDGET_SIZE)
        self.toolbar_scroll.setMinimumHeight(0)
        self.toolbar_scroll.setMaximumHeight(MAX_WIDGET_SIZE)
        if vertical:
            if self.top_chrome.has_row(self.toolbar_scroll):
                self.top_chrome.remove_row(self.toolbar_scroll)
            self.body.addWidget(self.toolbar_scroll)      # 加在视图栈之后 = 右边缘
            self.toolbar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.toolbar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        else:
            self.body.removeWidget(self.toolbar_scroll)
            if not self.top_chrome.has_row(self.toolbar_scroll):
                self.top_chrome.add_row(self.toolbar_scroll)
            self.toolbar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.toolbar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.toolbar_scroll.updateGeometry()
        self._schedule_toolbar_sync()
```

- [ ] **Step 7：更新既有布局断言**

`tests/test_app_modes_offscreen.py` 的 `test_layout_order_titlebar_menubar_toolbar_find_body` 改为：

```python
def test_layout_order_chrome_find_body(win):
    column = win.centralWidget().layout()
    assert column.indexOf(win.top_chrome) == 0
    assert win.top_chrome.rows.indexOf(win.titlebar) == 0
    assert win.top_chrome.rows.indexOf(win.menubar) == 1
    assert win.top_chrome.rows.indexOf(win.toolbar_scroll) == 2

    body = None
    body_index = -1
    for i in range(column.count()):
        item = column.itemAt(i)
        if item.layout() is not None:
            body = item.layout()
            body_index = i
            break
    assert body is not None
    assert column.indexOf(win.find_bar) < body_index
    assert body.indexOf(win.side_panel) >= 0
    assert body.indexOf(win.tabs) >= 0
```

`test_layout_with_right_toolbar_keeps_body_and_document_mode` 增加：

```python
    assert win.top_chrome.has_row(win.toolbar_scroll) is False
```

`tests/test_window_offscreen.py`：

```python
# test_toolbar_position_right_moves_scroll_to_body_right_edge
    assert win.top_chrome.has_row(win.toolbar_scroll) is False
    assert win.column.indexOf(win.toolbar_scroll) == -1
```

```python
# test_switch_back_to_top_restores_column_placement
    assert win.top_chrome.rows.indexOf(win.toolbar_scroll) == 2
    assert win.body.indexOf(win.toolbar_scroll) == -1
```

`tests/test_settings_dialog_offscreen.py` 的 `test_settings_switch_to_right_moves_toolbar_and_persists` 增加：

```python
    assert win.top_chrome.has_row(win.toolbar_scroll) is False
```

- [ ] **Step 8：跑布局相关测试**

```bash
python -m pytest tests/test_top_chrome.py tests/test_app_modes_offscreen.py tests/test_window_offscreen.py tests/test_settings_dialog_offscreen.py -q
```

预期：全部通过。

- [ ] **Step 9：报告可提交**

```bash
git add sketchpad/app.py sketchpad/ui/toolbox.py sketchpad/ui/theme.py tests/test_top_chrome.py tests/test_app_modes_offscreen.py tests/test_window_offscreen.py tests/test_settings_dialog_offscreen.py
```

经用户批准后提交。

---

### Task 4：底部状态胶囊

**文件：**
- 新建：`sketchpad/ui/status_pill.py`
- 修改：`sketchpad/ui/theme.py`
- 修改：`sketchpad/app.py`
- 测试：`tests/test_status_pill.py`

**接口：**
- 产出：`StatusPill.message`、`.save`、`.status`、`PILL_MAX_WIDTH`
- 消费：`app.MainWindow.message_label`、`save_label`、`status_label` 三个别名保持不变

- [ ] **Step 1：写失败测试**

新建 `tests/test_status_pill.py`：

```python
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import main
from sketchpad.ui import theme
from sketchpad.ui.status_pill import PILL_MAX_WIDTH


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


def test_status_bar_is_one_floating_pill(win):
    QTest.qWait(40)
    bar = win.statusBar()
    assert win.status_pill.objectName() == "StatusPill"
    assert win.status_pill.parent() is bar
    assert bar.isSizeGripEnabled() is False
    assert win.status_pill.width() <= PILL_MAX_WIDTH
    assert win.status_pill.width() < win.width() - 80
    for label in (win.message_label, win.save_label, win.status_label):
        assert win.status_pill.isAncestorOf(label)


def test_status_bar_has_no_top_divider():
    block = theme.QSS.split("QStatusBar {", 1)[1].split("}", 1)[0]
    assert "transparent" in block
    assert "border-top" not in block


def test_status_pill_qss_uses_glass_tokens():
    block = theme.QSS.split("QFrame#StatusPill", 1)[1].split("}", 1)[0]
    assert theme.GLASS_STATUS in block
    assert theme.GLASS_STATUS_BORDER in block
    assert f"border-radius: {theme.RADIUS_PILL}px" in block


def test_status_message_still_reaches_pill(win):
    win._note("测试提示")
    assert win.message_label.text() == "测试提示"
    assert win.status_pill.isAncestorOf(win.message_label)
```

- [ ] **Step 2：跑测试确认失败**

```bash
python -m pytest tests/test_status_pill.py -q
```

预期：`ModuleNotFoundError: No module named 'sketchpad.ui.status_pill'`。

- [ ] **Step 3：新建 `sketchpad/ui/status_pill.py`**

```python
"""主窗底部浮动状态胶囊：消息、保存角标、页面/缩放/笔迹。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy

PILL_MAX_WIDTH = 680


class StatusPill(QFrame):
    """状态栏里唯一的胶囊；标签名字保持 app.py 现有业务代码不变。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusPill")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMaximumWidth(PILL_MAX_WIDTH)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 2, 8, 2)
        row.setSpacing(6)

        self.message = QLabel("", self)
        self.save = QLabel("", self)
        self.save.setObjectName("SaveStatus")
        self.status = QLabel(self)
        self.status.setObjectName("StatusValue")
        self.save.hide()

        row.addWidget(self.message, 1)
        row.addWidget(self.save, 0)
        row.addWidget(self.status, 0)
```

- [ ] **Step 4：改状态栏 QSS**

`sketchpad/ui/theme.py` 中从 `QStatusBar {{` 到 `QStatusBar QLabel#StatusValue` 规则末尾整段替换为：

```css
QStatusBar {{ background: transparent; border: none; }}
QStatusBar::item {{ border: none; }}
QFrame#StatusPill {{
    background: {GLASS_STATUS};
    border: 1px solid {GLASS_STATUS_BORDER};
    border-radius: {RADIUS_PILL}px;
}}
QFrame#StatusPill QLabel {{
    color: {TEXT_DIM};
    padding: 0 4px;
}}
QFrame#StatusPill QLabel#SaveStatus {{
    color: {TEXT_DIM};
    font-size: 11px;
    background: transparent;
    border: none;
    padding: 0 4px;
}}
QFrame#StatusPill QLabel#StatusValue {{
    color: {TEXT_DIM};
    font-size: 11px;
    background: transparent;
    border: none;
    padding: 0 4px;
}}
```

- [ ] **Step 5：接进 `MainWindow`**

`sketchpad/app.py` 导入区加入：

```python
from sketchpad.ui.status_pill import StatusPill
```

把 `self.message_label = QLabel("", self)` 替换为：

```python
        self.status_pill = StatusPill(self)
        self.message_label = self.status_pill.message
        self.save_label = self.status_pill.save
        self.status_label = self.status_pill.status
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        bar.setContentsMargins(8, 0, 8, 4)
        bar.addWidget(self.status_pill, 1)
```

删除稍后这 6 行：

```python
        self.save_label = QLabel("", self)
        self.save_label.setObjectName("SaveStatus")
        self.status_label = QLabel(self)
        self.status_label.setObjectName("StatusValue")
        self.statusBar().addWidget(self.message_label, 1)
        self.statusBar().addPermanentWidget(self.save_label)
        self.statusBar().addPermanentWidget(self.status_label)
```

`_note`、`_refresh_save_chip`、`status_text` 等业务代码不动。

- [ ] **Step 6：跑测试**

```bash
python -m pytest tests/test_status_pill.py tests/test_app_modes_offscreen.py tests/test_window_offscreen.py -q
```

预期：全部通过。

- [ ] **Step 7：报告可提交**

```bash
git add sketchpad/ui/status_pill.py sketchpad/ui/theme.py sketchpad/app.py tests/test_status_pill.py
```

经用户批准后提交。

---

### Task 5：去掉工具栏竖线，改用分组留白

**文件：**
- 修改：`sketchpad/ui/toolbox.py`
- 修改：`sketchpad/ui/theme.py`
- 测试：`tests/test_toolbox_offscreen.py`、`tests/test_theme_offscreen.py`

**接口：**
- 产出：`GROUP_GAP = 7`、`COLUMN_GROUP_GAP = 6`（QBoxLayout 的 spacing 只在 spacer 一侧加一次，有效横排 9px / 竖排 10px）、`Toolbox._group_gaps`
- 消费：无；本任务不改变任何信号或控件顺序

- [ ] **Step 1：写失败测试**

`tests/test_toolbox_offscreen.py` 的导入改为：

```python
from sketchpad.ui.toolbox import (COLLAPSED_BAND, COLUMN_GROUP_GAP, COLUMN_SPACING,
                                  CURRENT_COLOR_SIZE, GROUP_GAP, HIT_TARGET, ICON_SIZE,
                                  PALETTE, ROW_SPACING, SLIDER_LENGTH,
                                  VERTICAL_SLIDER_WIDTH, Toolbox)
```

把 `test_row_spacing_and_separator_geometry` 替换为：

```python
def test_row_spacing_and_group_gaps(qapp):
    """行距 2 + 7px spacer = 有效 9px 组间留白（布局 spacing 只加一次）；主工具栏不再有 Sep 竖线。"""
    tb = Toolbox()
    try:
        assert ROW_SPACING == 2
        assert GROUP_GAP == 7
        assert ROW_SPACING + GROUP_GAP == 9
        assert tb.layout().spacing() == ROW_SPACING
        assert [widget for widget in tb.findChildren(QFrame)
                if widget.objectName() == "Sep"] == []
        assert len(tb._group_gaps) == 6
        assert all(gap.sizeHint().width() == GROUP_GAP for gap in tb._group_gaps)
        assert "QFrame#Sep" not in theme.QSS
    finally:
        tb.close()
```

把 `test_set_vertical_rebuilds_layout_as_one_narrow_column` 中处理 `_separators` 的两段改为：

```python
        for gap in tb._group_gaps:
            assert gap.sizeHint().height() == COLUMN_GROUP_GAP
            assert gap.sizeHint().width() == 1
            assert gap.sizePolicy().verticalPolicy() == QSizePolicy.Fixed
        assert COLUMN_SPACING + COLUMN_GROUP_GAP == 10       # 列距只加一次，有效 10px
```

```python
        for gap in tb._group_gaps:
            assert gap.sizeHint().width() == GROUP_GAP
```

`tests/test_theme_offscreen.py` 的 `test_border_token_is_lightened` 最后一行：

```python
    assert "QFrame#TopChromePanel" in theme.QSS
    assert theme.GLASS_TOP_BORDER in theme.QSS
```

替换原来的 `QFrame#Sep` 断言。

- [ ] **Step 2：跑测试确认失败**

```bash
python -m pytest tests/test_toolbox_offscreen.py tests/test_theme_offscreen.py -q
```

预期：`ImportError: cannot import name 'GROUP_GAP'`。

- [ ] **Step 3：改常量与布局**

`sketchpad/ui/toolbox.py`：

```python
ROW_SPACING = 2                           # 横向行内控件间距
COLUMN_SPACING = 4                        # 纵向右侧栏的列间距
GROUP_GAP = 7                             # 横排组间留白（替代竖线）：QBoxLayout 只加一次行距 2，有效 9px
COLUMN_GROUP_GAP = 6                      # 竖排组间留白：QBoxLayout 只加一次列距 4，有效 10px
```

删除 `SEPARATOR_HEIGHT`、`SEPARATOR_THICKNESS`。删除 `self._separators: list[QFrame] = []` 与 `self._separators = [...]`。

`_rebuild_layout` 中：

```python
        blocks = (
            (self.collapse_button,),
            (self.undo_button, self.redo_button),
            (self.pen_button, self.highlighter_button,
             self.select_button, self.note_button, self.text_button,
             self.eraser_button, self.annotate_button),
            tuple(self.width_buttons),
            (self.size_slider, self.size_label),
            (*self.color_buttons, self.custom_color_button, self.current_color_swatch),
            (self.word_button, self.image_button, self.new_note_button,
             self.capture_button, self.find_button, self.clear_button,
             self.scratch_button, self.split_button, self.split_doc_button,
             self.settings_button),
        )
        self._group_gaps: list[QSpacerItem] = []
        for index, block in enumerate(blocks):
            if index:
                if self._vertical:
                    gap = QSpacerItem(1, COLUMN_GROUP_GAP,
                                      QSizePolicy.Minimum, QSizePolicy.Fixed)
                else:
                    gap = QSpacerItem(GROUP_GAP, 1,
                                      QSizePolicy.Fixed, QSizePolicy.Minimum)
                box.addItem(gap)
                self._group_gaps.append(gap)
            for widget in block:
                if self._vertical:
                    box.addWidget(widget, 0, Qt.AlignHCenter)
                else:
                    box.addWidget(widget)
        box.addStretch(1)

        self._shape_slider()
```

删除 `_shape_separators()`、`_new_separator()` 和 `_shape_separators` 整个方法。导入区加 `QSpacerItem`。

- [ ] **Step 4：隐藏组时把留白归零**

`_apply_visibility` 开头加入：

```python
        gaps_visible = self._annotations_enabled and not self._collapsed
        for gap in self._group_gaps:
            if self._vertical:
                gap.changeSize(1, COLUMN_GROUP_GAP if gaps_visible else 0,
                               QSizePolicy.Minimum, QSizePolicy.Fixed)
            else:
                gap.changeSize(GROUP_GAP if gaps_visible else 0, 1,
                               QSizePolicy.Fixed, QSizePolicy.Minimum)
        self.layout().invalidate()
```

删除原来 `for separator in self._separators: separator.setVisible(visible)` 三行。

- [ ] **Step 5：删掉主主题里的 Sep 规则**

`sketchpad/ui/theme.py` 删除：

```css
QFrame#Sep {{ background: {BORDER}; border: none; max-width: 1px; }}
```

`sketchpad/annotate/theme.py` 里同名的 Sep 规则属于全屏批注工具条，不要动。

- [ ] **Step 6：跑测试**

```bash
python -m pytest tests/test_toolbox_offscreen.py tests/test_theme_offscreen.py tests/test_window_offscreen.py -q
```

预期：全部通过。重点确认：
- 横排 6 条组间留白，没有 `Sep` 子控件；
- 竖排留白变成 6px 高（加 4px 列距后有效 10px）；横排 7px（加 2px 行距后有效 9px，落在 8–10px 目标内）；
- 收起后工具栏高度仍 ≤ 26px；
- `settings_button` 仍是最后一个控件。

- [ ] **Step 7：报告可提交**

```bash
git add sketchpad/ui/toolbox.py sketchpad/ui/theme.py tests/test_toolbox_offscreen.py tests/test_theme_offscreen.py
```

经用户批准后提交。

---

### Task 6：截图工具与验收图

**文件：**
- 新建：`tools/ui_snap_topchrome.py`
- 修改：`docs/plans/2026-10-25-moye-apple-ui.md`（状态行）
- 产出：`docs/plans/assets/2026-10-26-topchrome/*.png`

- [ ] **Step 1：新建截图工具**

```python
"""离屏截取方向 A 的顶栏验收图；不显示到桌面，不操控鼠标。"""
from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import main
from sketchpad.ui import theme


def _make_pdf(path: Path) -> None:
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()


def _grab(window, out: Path, name: str) -> None:
    QTest.qWait(250)
    target = out / name
    window.grab().save(str(target))
    print(target)


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/plans/assets/2026-10-26-topchrome")
    parser.add_argument("--scale", default="100")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    app = QApplication([])
    app.setStyleSheet(theme.QSS)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        controller = main.AppController(root / "cfg")
        window = main.MainWindow(controller)
        window.resize(1160, 760)
        window.show()
        QTest.qWaitForWindowExposed(window)
        _grab(window, out, f"draft_1160_{args.scale}.png")

        window.resize(792, 760)
        _grab(window, out, f"draft_792_{args.scale}.png")

        pdf = root / "sample.pdf"
        _make_pdf(pdf)
        window.resize(1160, 760)
        window._enter_document(str(pdf))
        _grab(window, out, f"pdf_1160_{args.scale}.png")

        window._quitting = True
        window.close()

        empty = main.MainWindow(controller, primary=False)
        empty.resize(1160, 760)
        empty.show()
        QTest.qWaitForWindowExposed(empty)
        _grab(empty, out, f"empty_1160_{args.scale}.png")
        empty._quitting = True
        empty.close()


if __name__ == "__main__":
    run()
```

- [ ] **Step 2：跑 100% 与 150% 截图**

```bash
QT_SCALE_FACTOR=1 python tools/ui_snap_topchrome.py --scale 100
QT_SCALE_FACTOR=1.5 python tools/ui_snap_topchrome.py --scale 150
```

预期生成 8 张 PNG：
`draft_1160_100.png`、`draft_792_100.png`、`pdf_1160_100.png`、`empty_1160_100.png`，以及对应的 `_150` 文件。

- [ ] **Step 3：逐张检查**

| 图 | 检查 |
|---|---|
| `draft_1160_100` | 顶栏是一整块圆角玻璃；标题/菜单/工具栏之间没有横线；工具栏没有竖线；状态胶囊不贴满宽 |
| `draft_792_100` | 全屏批注按钮在可视区；工具栏可横向滚动；圆角不裁切 |
| `pdf_1160_100` | PDF fit-width，左右无灰边；无横向工具栏滚动条，设置齿轮完整可见；顶栏玻璃不压住内容 |
| `empty_1160_100` | 空态卡仍在玻璃底上，标题/菜单/工具栏同一块材质 |
| `*_150` | 圆角、1px 边、阴影不糊；文字不虚；无裁切 |

- [ ] **Step 4：更新旧方案状态行**

`docs/plans/2026-10-25-moye-apple-ui.md` 顶部：

```markdown
状态：方向 A 已展开为实施计划：`docs/plans/2026-10-26-moye-apple-topchrome.md`（2026-10-26）
```

- [ ] **Step 5：报告可提交**

```bash
git add tools/ui_snap_topchrome.py docs/plans/2026-10-25-moye-apple-ui.md docs/plans/assets/2026-10-26-topchrome
```

经用户批准后提交。

---

### Task 7：全量回归与交接记录

**文件：**
- 修改：`PROGRESS.md`

- [ ] **Step 1：跑相关分组**

```bash
python -m pytest tests/test_top_chrome.py tests/test_status_pill.py \
  tests/test_titlebar_tabs.py tests/test_menubar_offscreen.py \
  tests/test_toolbox_offscreen.py tests/test_theme_offscreen.py \
  tests/test_app_modes_offscreen.py tests/test_window_offscreen.py \
  tests/test_settings_dialog_offscreen.py -q
```

- [ ] **Step 2：跑全量**

```bash
python -m pytest tests -q
python -m pytest tests --collect-only -q
```

- [ ] **Step 3：确认行为未变**

逐项手测/离屏确认：
- 打开 PDF、翻页、缩放、fit-width；
- 钢笔/荧光笔/橡皮/笔记/文字/全屏批注；
- 撤销/重做、清空、查找、设置；
- 右排工具栏切换后 B/Q/1-4 仍落到画布；
- 草稿窗、笔记窗、设置/查找弹窗未被 TopChrome 影响。

- [ ] **Step 4：写 `PROGRESS.md`**

追加一节，字段必须齐全：

```markdown
## 2026-10-26 方向 A Stage A：统一玻璃顶栏

| 需求 | 代码 | 测试 | 验证 |
|---|---|---|---|
| 一块玻璃顶栏包住标题/菜单/工具栏 | `sketchpad/ui/top_chrome.py`、`sketchpad/app.py` | `tests/test_top_chrome.py` | 离屏截图 + 行顺序断言 |
| 去掉标题/菜单/状态栏横线 | `sketchpad/ui/theme.py`、`sketchpad/ui/menubar.py` | 同左 | QSS 断言 + 截图 |
| 去掉工具栏竖线 | `sketchpad/ui/toolbox.py` | `tests/test_toolbox_offscreen.py` | 组间留白断言 |
| 状态浮动胶囊 | `sketchpad/ui/status_pill.py` | `tests/test_status_pill.py` | 结构断言 + 截图 |

### 未验证
（只写真实没做的事，例如：未在 125%/200% 缩放下逐像素检查；未做真机触控板拖拽）
```

- [ ] **Step 5：最终报告**

报告必须包含：
- 改动文件清单；
- 相关测试与全量测试的原始通过行；
- 8 张截图路径；
- 未验证项；
- 是否还有未提交改动。

---

## 5. 验收矩阵

| 验收项 | 判定方式 | 通过标准 |
|---|---|---|
| 只有一块顶栏材质 | `test_main_window_puts_three_rows_in_one_panel` | 三行都在 `top_chrome.panel`，顶层只有 `top_chrome` |
| 没有横向分割线 | QSS 断言 + 截图 | TitleBar/Menubar/StatusBar 均无 `border-bottom`/`border-top` |
| 工具栏没有竖线 | `test_row_spacing_and_group_gaps` | `Sep` 子控件数为 0，6 条组间留白 |
| 顶栏高度受控 | `test_chrome_height_stays_in_budget` | ≤126px 门禁在 1100×760（更窄的严格档）跑；1160×760 是默认 PDF 适配档，同一预算 |
| 792 宽可用 | `test_annotate_button_fits_at_792` + 截图 | 全屏批注按钮完整在视口内；工具栏可横向滚动 |
| 状态栏浮动 | `test_status_bar_is_one_floating_pill` | 单胶囊，宽 ≤ 680，不贴满整宽 |
| 文字可读 | `test_glass_text_contrast_is_at_least_aa` | 对比度 ≥ 4.5 |
| 右侧工具栏不破 | 现有右排测试 | 工具栏仍在 body 最右，TopChrome 只两行 |
| 功能不变 | 全量测试 + 主路径手测 | 打开/书写/高亮/橡皮/笔记/查找/设置/导出一致 |
| 150% 清晰 | 8 张截图 | 圆角、1px 边、阴影、文字均不糊不裁 |

---

## 6. 风险与回退

| 风险 | 表现 | 对策 |
|---|---|---|
| 阴影被裁切 | 面板边缘阴影缺一块 | TopChrome 外壳外边距就是给阴影留的 8/4/8/6；仍裁切就把外边距加到 12，不退回横线 |
| 自绘阴影太重或颜色发脏 | 顶栏下沿像一条灰带 | 只保留 1px 边和 6px 留白，把 `SHADOW_LAYERS` 清空即可；不退回横线 |
| 窄窗横向滚动条压到圆角 | 792 宽时滚动条两端露出圆角外 | 截图检查；必要时把 `PANEL_MARGINS` 底边从 2 改 4（总高 +2） |
| 透明后文字发灰 | 玻璃下的桃色透太多 | alpha 不低于 0.5；`GLASS_TOP_ALPHA` 只用 0.58，不往下调 |
| 顶栏变高压内容 | 760 高窗口内容变少 | 行距固定 2、面板上下内边距 0/2、外壳 4/2；超 126px 先砍外边距 |
| 顶部 4px 空白不能拖窗 | 极小概率点到外壳边距 | 标题栏本身仍是拖动热区；不为此加额外事件过滤器 |
| 工具栏右侧模式无底 | 竖栏按钮悬在桃底上 | `Toolbox[dock="right"]` 保留独立玻璃卡；`set_dock` 里 unpolish/polish |
| 工具栏隐藏组后留白残留 | TXT 阅读器只剩动作组，前面一片空 | `_apply_visibility` 把 `_group_gaps` 尺寸归零 |
| 全局 QSS 误伤弹窗 | 设置/取色器被刷底 | 继续只用 objectName 选择器；不写全局 `QDialog` |

回退顺序：
1. 只撤本任务的提交，不动数据；
2. 阴影问题只删 `setGraphicsEffect` 三行；
3. 布局问题只把 `TopChrome` 换回三条直接 `addWidget`，保留 Task 2 的透明 QSS 也不会坏功能；
4. 不通过“删测试”来让验收变绿。

---

## 7. 后续阶段（本轮不做）

### Stage B：工具栏分组胶囊

- 在 `Toolbox` 的 7 个 block 外包 4–5 个透明 `QFrame#ToolGroup`，用 3px 内边距和浅白底分组。
- 组间仍用 8–10px 留白，不恢复竖线。
- 验收：40px 高度不变；792 宽全屏批注仍可见；组顺序与按钮顺序完全一致。
- 新增测试：组数量、每组首尾按钮、选中态、hover 只亮组不亮整条。

### Stage C：菜单、标签、弹层

- 设置、查找、最近打开、关闭确认统一玻璃卡；菜单项 hover 圆角；标签书签化。
- 草稿窗与笔记窗只保留一层边框。
- 验收：所有弹层同一材质；取色器等原生对话框不被罩；现有弹窗测试全绿。

### Stage D：可选浅色草稿画布

- 涉及“浅色底上的旧墨迹自动转深色”，属于落笔颜色逻辑，不是纯样式。
- 单独写方案，不并入本计划。

---

## 8. 交接

实施完成后，接手的人应能只读三个地方恢复上下文：

1. 本文件的 §2 层级图和 §3 接口；
2. `PROGRESS.md` 的 2026-10-26 记录；
3. `docs/plans/assets/2026-10-26-topchrome/` 的 8 张截图。

如果实现与本文冲突，以“功能不变、控件顺序不变、顶栏一块材质”三条为准，并先更新本文再改代码。
