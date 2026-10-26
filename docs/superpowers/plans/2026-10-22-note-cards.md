# PDF 页内手写笔记卡片实现计划

> **状态（2026-10-22）**：Task 1–6 已完成并测试（Task 5 `tests/test_note_wiring.py` 10 条；Task 6 `tests/test_export_notes.py` 10 条；应用导出已接线）。Task 7 的文档/回归已做；真实窗口验收与重新打包待用户明确要求。下列步骤复选框未逐条回填，以本行为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 PDF 任意位置放笔记图标，点图标展开带横线的手写笔记卡片；卡片自带工具栏，笔记存 sidecar，导出时作为额外页插在锚点页后。

**Architecture:** 覆盖层卡片（`NoteOverlay` + `NoteCard`）挂在 PDF 视图栈上，卡片内复用 `SketchView`/`History`/`Stroke`；PDF 页上只放 `QGraphicsItem` 笔记图标（不随缩放变大）。sidecar 增加 `notes` 字段，有笔记时版本 2；导出按原页序重建 PDF，并在锚点页后插入笔记页。

**Tech Stack:** Python 3.13、PySide6 6.12、PyMuPDF 2.0、pytest（offscreen）。

**Spec:** `docs/superpowers/specs/2026-10-22-note-cards-design.md`

## Global Constraints

- 解释器固定 `E:\SketchPad\.venv\Scripts\python.exe`；测试用 `-m pytest`。
- 测试固定 stable 渠道（`tests/conftest.py`）；不要改渠道逻辑。
- **每个任务完成先跑测试；git 提交必须等用户明确批准**（用户规则，不要自动提交）。
- 笔记 sidecar：有笔记时写 `version: 2`，无笔记保持 `version: 1`；解析器接受 1/2。
- 旧稳定版读到 v2 必须拒绝（`PdfVersionTooNew`），不能静默丢 notes。
- 卡片工具栏和主工具栏各自独立：主工具栏永远只控制 PDF；卡片工具栏只控制笔记。
- 笔记内容坐标是卡片设计坐标（560×720）；锚点是页面显示坐标。
- 不要动平滑、阅读模式、单实例、荧光笔选字的既有行为。

---

## File Structure

**Create**
- `sketchpad/core/notes.py`：`Note` 模型、默认尺寸、paper 常量、id 生成、锚点钳制。
- `sketchpad/ui/note_pin.py`：PDF 页上的笔记图标 `NotePin`（QGraphicsItem）。
- `sketchpad/ui/note_canvas.py`：`NoteScene`（横线纸背景）+ `NoteView`（复用 SketchView，适配卡片尺寸）。
- `sketchpad/ui/note_card.py`：`NoteCard`（卡片头部/工具栏/画布）+ `NoteOverlay`（遮罩、展开动画、定位）。
- `tests/test_notes_store.py`
- `tests/test_note_canvas.py`
- `tests/test_note_card.py`
- `tests/test_note_pins.py`
- `tests/test_note_wiring.py`
- `tests/test_export_notes.py`

**Modify**
- `sketchpad/core/pdfstore.py`：`Page.notes`、v2 编解码、严格校验。
- `sketchpad/pdf/document.py`：`export_with_notes`（在原页后插笔记页）。
- `sketchpad/ui/pdfview.py`：笔记图标、`note` 工具、`notePlacementRequested`/`noteRequested`、锚点定位。
- `sketchpad/ui/toolbox.py`：笔记工具按钮、`set_note_enabled`、图标。
- `sketchpad/ui/menubar.py`：工具菜单「插入笔记」、快捷键 `N`、文档模式启用。
- `sketchpad/ui/icons.py`：`note_icon`、`paper_icon`、`delete_icon`。
- `sketchpad/app.py`：创建/打开/删除笔记、卡片生命周期、焦点按键分流、导出接线。
- `sketchpad/ui/theme.py`：笔记卡片/工具栏/遮罩 QSS 常量。
- `README.md`、`PROGRESS.md`。

---

## Task 1: Note 模型与 sidecar v2

**Files**
- Create: `sketchpad/core/notes.py`
- Modify: `sketchpad/core/pdfstore.py`
- Test: `tests/test_notes_store.py`

**Interfaces**
- Produces:
  - `Note(id: str, x: float, y: float, w: float = 560.0, h: float = 720.0, paper: str = "dark", strokes: list[Stroke] = field(default_factory=list))`
  - `NOTE_W = 560.0`、`NOTE_H = 720.0`、`NOTE_PAPERS = ("dark", "light")`
  - `new_note_id(existing: set[str]) -> str`
  - `clamp_note_anchor(x, y, page_w, page_h) -> tuple[float, float]`
  - `Page.notes: list[Note]`
  - `pdfstore.FORMAT_VERSION = 2`；`from_json` 接受 v1/v2。

- [ ] **Step 1: 写失败测试**

`tests/test_notes_store.py`：

```python
import json
import pytest
from sketchpad.core import pdfstore
from sketchpad.core.notes import NOTE_H, NOTE_W, Note, clamp_note_anchor, new_note_id
from sketchpad.core.pdfstore import Page, PdfDocument, PdfFormatError, PdfVersionTooNew
from sketchpad.core.strokes import Stroke

def _note():
    return Note("n-1", 30.0, 40.0, strokes=[Stroke([(5.0, 6.0), (7.0, 8.0)], "#e8eaed", 2.0)])

def test_v1_sidecar_has_no_notes():
    raw = json.dumps({"version": 1, "pages": [{"w": 100, "h": 200, "r": 0,
                                               "strokes": []}]})
    doc = pdfstore.from_json(raw, 1)
    assert doc.pages[0].notes == []

def test_v2_round_trip_note():
    doc = PdfDocument([Page(100.0, 200.0, 0, [], [_note()])])
    raw = pdfstore.to_json(doc)
    assert json.loads(raw)["version"] == 2
    back = pdfstore.from_json(raw, 1)
    note = back.pages[0].notes[0]
    assert (note.id, note.x, note.y, note.w, note.h, note.paper) == (
        "n-1", 30.0, 40.0, NOTE_W, NOTE_H, "dark")
    assert note.strokes[0].points == [(5.0, 6.0), (7.0, 8.0)]

def test_no_notes_still_writes_v1():
    doc = PdfDocument([Page(100.0, 200.0)])
    assert json.loads(pdfstore.to_json(doc))["version"] == 1

def test_v3_and_bad_note_fields_are_rejected():
    with pytest.raises(PdfVersionTooNew):
        pdfstore.from_json(json.dumps({"version": 3, "pages": []}))
    bad = {"version": 2, "pages": [{"w": 100, "h": 200, "r": 0, "strokes": [],
            "notes": [{"id": "", "x": 1, "y": 2, "w": 560, "h": 720,
                       "paper": "dark", "strokes": []}]}]}
    with pytest.raises(PdfFormatError):
        pdfstore.from_json(json.dumps(bad))

def test_new_id_and_anchor_clamp():
    assert new_note_id({"n-1"}) != "n-1"
    assert clamp_note_anchor(-5.0, 500.0, 100.0, 200.0) == (0.0, 200.0)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_notes_store.py -q`
Expected: FAIL（`sketchpad.core.notes` 不存在 / `Page` 没有 notes）

- [ ] **Step 3: 实现 `notes.py`**

```python
"""笔记卡片模型：卡片设计坐标 + 页面锚点。本模块不依赖 Qt。"""
from __future__ import annotations

from dataclasses import dataclass, field
from sketchpad.core.strokes import Stroke

NOTE_W, NOTE_H = 560.0, 720.0
NOTE_PAPERS = ("dark", "light")

@dataclass
class Note:
    id: str
    x: float
    y: float
    w: float = NOTE_W
    h: float = NOTE_H
    paper: str = "dark"
    strokes: list[Stroke] = field(default_factory=list)

def new_note_id(existing: set[str]) -> str:
    index = 1
    while f"n-{index}" in existing:
        index += 1
    return f"n-{index}"

def clamp_note_anchor(x: float, y: float, page_w: float, page_h: float):
    return (min(max(float(x), 0.0), float(page_w)),
            min(max(float(y), 0.0), float(page_h)))
```

- [ ] **Step 4: 改 `pdfstore.py`**

- `FORMAT_VERSION = 2`；保留 `LEGACY_VERSION = 1`。
- `Page` 增加 `notes: list[Note] = field(default_factory=list)`（放在 strokes 后，保持旧位置参数兼容）。
- 抽出 `_parse_stroke(raw, where)`；`_stroke` 只负责拼 where。
- 新增 `_note(page_index, note_index, raw)`：

```python
from sketchpad.core.notes import NOTE_PAPERS, Note

def _note(page_index: int, note_index: int, raw: object) -> Note:
    where = f"page #{page_index} note #{note_index}"
    if not isinstance(raw, dict):
        raise PdfFormatError(f"{where} must be an object, got {type(raw).__name__}")
    try:
        note_id = raw["id"]
        x = _number(raw["x"], f"{where} x")
        y = _number(raw["y"], f"{where} y")
        w = _number(raw["w"], f"{where} w")
        h = _number(raw["h"], f"{where} h")
        paper = raw.get("paper", "dark")
        raw_strokes = raw.get("strokes", [])
    except KeyError as exc:
        raise PdfFormatError(f"{where} missing field {exc}") from exc
    if not isinstance(note_id, str) or not note_id.strip():
        raise PdfFormatError(f"{where} id must be a non-empty string")
    if paper not in NOTE_PAPERS:
        raise PdfFormatError(f"{where} paper must be one of {NOTE_PAPERS}")
    if w <= 0 or h <= 0:
        raise PdfFormatError(f"{where} size must be positive")
    if not isinstance(raw_strokes, list):
        raise PdfFormatError(f"{where} strokes must be a list")
    strokes = [_parse_stroke(s, f"{where} stroke #{j}") for j, s in enumerate(raw_strokes)]
    return Note(note_id, x, y, w, h, paper, strokes)
```

- `_page` 解析 `notes = raw.get("notes", [])`，类型检查后 `[_note(index, j, n) ...]`，传给 `Page(..., strokes, notes)`。
- `to_json`：
  - `has_notes = any(page.notes for page in doc.pages)`；`version = 2 if has_notes else 1`。
  - 每页 JSON 增加（仅当有 notes）`"notes": [{"id": n.id, "x": n.x, "y": n.y, "w": n.w, "h": n.h, "paper": n.paper, "strokes": [stroke_json(s) ...]}]`。
  - 抽出 `_stroke_json(s)` 复用。
- `from_json` 的 `version > FORMAT_VERSION` 保留；v1/v2 都可读。
- 旧 `test_pdfstore.py` 只读无笔记文件，保持通过。

- [ ] **Step 5: 跑测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_notes_store.py tests/test_pdfstore.py -q`
Expected: PASS

- [ ] **Step 6: （用户批准后）提交**

```bash
git add sketchpad/core/notes.py sketchpad/core/pdfstore.py tests/test_notes_store.py
git commit -m "feat(notes): add note model and sidecar v2"
```

---

## Task 2: 笔记纸面与画布

**Files**
- Create: `sketchpad/ui/note_canvas.py`
- Test: `tests/test_note_canvas.py`

**Interfaces**
- Produces:
  - `PAPER_BG = {"dark": "#2b2c2f", "light": "#f6f1e6"}`
  - `PAPER_LINE = {"dark": "#4a4b50", "light": "#d8cdb8"}`
  - `LINE_GAP = 28.0`、`MARGIN_X = 42.0`
  - `NoteScene(document: Document, paper: str = "dark")`；`set_paper(paper)`。
  - `NoteView(note: Note, settings: Settings, parent=None)`；`fit_page()`；`set_paper(paper)`；`view` 的 `document` 与 `note.strokes` 共享同一 list。

- [ ] **Step 1: 写失败测试**

`tests/test_note_canvas.py`：

```python
import os; os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from sketchpad.core.commands import AddStroke
from sketchpad.core.notes import NOTE_H, NOTE_W, Note
from sketchpad.core.settings import Settings
from sketchpad.core.strokes import Document, Stroke
from sketchpad.ui.note_canvas import LINE_GAP, PAPER_BG, PAPER_LINE, NoteScene, NoteView

@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])

def test_scene_paints_paper_and_ruled_lines(qapp, tmp_path):
    scene = NoteScene(Document(), "light")
    scene.setSceneRect(0, 0, NOTE_W, NOTE_H)
    from PySide6.QtGui import QImage, QPainter
    img = QImage(200, 200, QImage.Format_RGB32); img.fill(0)
    p = QPainter(img); scene.render(p, img.rect()); p.end()
    assert img.pixelColor(2, 2).name() == PAPER_BG["light"]
    # 找到一条横线：第 2 条线附近颜色应是线色
    y = int(LINE_GAP * 2)
    assert img.pixelColor(100, y).name() == PAPER_LINE["light"]

def test_note_view_shares_note_strokes_and_undo(qapp):
    note = Note("n-1", 10, 20)
    view = NoteView(note, Settings())
    view.resize(560, 720); view.show(); QTest.qWait(30)
    stroke = Stroke([(10.0, 10.0), (30.0, 30.0)], "#e8eaed", 2.0)
    cmd = AddStroke(view.document, stroke)
    cmd.apply(); view.history.push(cmd); view.scene.rebind()
    assert view.document.strokes is note.strokes
    assert note.strokes == [stroke]
    view.history.pop_undo()
    assert note.strokes == []
    assert view.document.viewport.scale > 0
```

（测试里的追加笔迹改用真实 `AddStroke` 命令，见 Step 3；不要用 `history.push` 占位。）

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_note_canvas.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `note_canvas.py`**

```python
"""笔记卡片画布：横线纸背景 + 复用 SketchView 的落笔/橡皮/撤销。"""
from __future__ import annotations
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLineF, QPainter, QPen
from sketchpad.core.notes import Note
from sketchpad.core.settings import Settings
from sketchpad.core.strokes import Document, Stroke
from sketchpad.ui.canvas import SketchScene, SketchView

PAPER_BG = {"dark": "#2b2c2f", "light": "#f6f1e6"}
PAPER_LINE = {"dark": "#4a4b50", "light": "#d8cdb8"}
LINE_GAP = 28.0
MARGIN_X = 42.0

class NoteScene(SketchScene):
    def __init__(self, document: Document, paper: str = "dark") -> None:
        super().__init__(document)
        self.paper = paper
        self.set_scene_size = None
        self.setBackgroundBrush(QColor(PAPER_BG[paper]))

    def set_paper(self, paper: str) -> None:
        self.paper = paper
        self.setBackgroundBrush(QColor(PAPER_BG[paper]))
        self.update()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor(PAPER_BG[self.paper]))
        pen = QPen(QColor(PAPER_LINE[self.paper]))
        pen.setCosmetic(True); pen.setWidthF(1.0)
        painter.setPen(pen)
        start = int(rect.top() // LINE_GAP) - 1
        end = int(rect.bottom() // LINE_GAP) + 2
        for i in range(start, end):
            y = i * LINE_GAP
            painter.drawLine(QLineF(rect.left(), y, rect.right(), y))
        painter.drawLine(MARGIN_X, rect.top(), MARGIN_X, rect.bottom())

class NoteView(SketchView):
    def __init__(self, note: Note, settings: Settings, parent=None) -> None:
        self.note = note
        self._design_w, self._design_h = float(note.w), float(note.h)
        scene = NoteScene(Document(strokes=note.strokes), note.paper)
        super().__init__(scene, settings)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.StrongFocus)
        self.set_tool("pen")

    def _sync_scene_rect(self) -> None:
        self.setSceneRect(QRectF(0.0, 0.0, self._design_w, self._design_h))

    def fit_page(self) -> None:
        vw, vh = self.viewport().width(), self.viewport().height()
        if vw <= 0 or vh <= 0:
            return
        scale = min(vw / self._design_w, vh / self._design_h)
        self.document.viewport.scale = scale
        self.document.viewport.origin = (
            (vw - self._design_w * scale) / 2.0,
            (vh - self._design_h * scale) / 2.0,
        )
        self._apply_view()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self.fit_page()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self.fit_page()

    def set_paper(self, paper: str) -> None:
        self.note.paper = paper
        self.scene.set_paper(paper)

    def wheelEvent(self, ev) -> None:
        ev.accept()                 # 笔记页不缩放；缩放由卡片的放大/还原负责

    def keyPressEvent(self, ev) -> None:
        key, mods = ev.key(), ev.modifiers()
        if mods & Qt.ControlModifier and key in (Qt.Key_Z, Qt.Key_Y):
            (self.history.pop_undo if key == Qt.Key_Z else self.history.pop_redo)()
            ev.accept(); return
        if key == Qt.Key_B:
            self.set_tool("pen"); ev.accept(); return
        if key == Qt.Key_Q:
            self.set_tool("eraser"); ev.accept(); return
        if key in (Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4):
            from sketchpad.core.settings import WIDTHS
            self.set_width(WIDTHS[(key - Qt.Key_1)]); ev.accept(); return
        ev.ignore()
```

- [ ] **Step 4: 跑测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_note_canvas.py -q`
Expected: PASS

- [ ] **Step 5: （用户批准后）提交**

```bash
git add sketchpad/ui/note_canvas.py tests/test_note_canvas.py
git commit -m "feat(notes): add ruled note canvas"
```

---

## Task 3: 笔记卡片与内置工具栏

**Files**
- Create: `sketchpad/ui/note_card.py`
- Modify: `sketchpad/ui/icons.py`
- Modify: `sketchpad/ui/theme.py`
- Test: `tests/test_note_card.py`

**Interfaces**
- Produces:
  - `NoteCard(note: Note, settings: Settings, parent=None)`
  - Signals：`contentChanged`、`toggleExpandRequested`、`deleteRequested`、`closeRequested`
  - 内部 `self.view: NoteView`；`set_paper(paper)`。
  - `NoteOverlay(stack: QWidget, settings: Settings, parent=None)`：`open_note(note, anchor_view_pos)`、`close_note()`、`is_open`、`card`；信号 `closed`、`deleteRequested`、`contentChanged`。
  - 新增图标：`icons.note_icon()`、`icons.paper_icon(dark: bool)`、`icons.delete_icon()`。

- [ ] **Step 1: 写失败测试**

`tests/test_note_card.py`：

```python
import os; os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from sketchpad.core.notes import Note
from sketchpad.core.settings import Settings
from sketchpad.core.strokes import Stroke
from sketchpad.ui.note_card import NoteCard, NoteOverlay

@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])

def test_card_toolbar_controls_note_only(qapp):
    note = Note("n-1", 10, 20)
    card = NoteCard(note, Settings())
    card.resize(560, 720); card.show(); QTest.qWait(30)
    card.eraser_button.click()
    assert card.view.tool == "eraser"
    card.pen_button.click()
    assert card.view.tool == "pen"
    card.clear_button.click()
    assert note.strokes == []

def test_paper_toggle_switches_colors(qapp):
    note = Note("n-1", 10, 20)
    card = NoteCard(note, Settings())
    card.paper_button.click()
    assert note.paper == "light"
    assert card.view.scene.paper == "light"
    assert card.view.color == "#2c2c2e"       # 浅纸自动换深色笔

def test_overlay_open_close_and_signals(qapp):
    host = QWidget(); host.resize(1000, 800); host.show()
    overlay = NoteOverlay(host, Settings())
    got = []
    overlay.closed.connect(lambda: got.append("closed"))
    overlay.open_note(Note("n-1", 10, 20), QPoint(100, 100))
    assert overlay.is_open
    QTest.qWait(260)
    overlay.close_note()
    QTest.qWait(260)
    assert not overlay.is_open
    assert got == ["closed"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_note_card.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `NoteCard`**

要点（完整实现按此结构）：

```python
class NoteCard(QWidget):
    contentChanged = Signal()
    toggleExpandRequested = Signal()
    deleteRequested = Signal()
    closeRequested = Signal()

    def __init__(self, note, settings, parent=None):
        super().__init__(parent)
        self.note = note
        self.setObjectName("NoteCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.view = NoteView(note, settings, self)
        self.view.changed.connect(self.contentChanged)
        # header: 标题 + paper_button + expand_button + delete_button + close_button
        # tools: pen_button, eraser_button, color swatches(PALETTE), width dots(WIDTHS),
        #        undo_button, redo_button, clear_button
        # 布局：header / tools / view（view 拉伸）
        # 所有按钮 NoFocus；按钮 ctrl 直接调用 self.view.set_tool/set_color/set_width/
        # history.pop_undo/pop_redo/clear_all
        # paper_button 调 self.set_paper("light" if note.paper=="dark" else "dark")
        # delete_button 弹 QMessageBox.question，确认后 deleteRequested.emit()
        # close_button 调 closeRequested.emit()
        # expand_button 调 toggleExpandRequested.emit()
```

`set_paper`：

```python
def set_paper(self, paper: str) -> None:
    self.note.paper = paper
    self.view.set_paper(paper)
    color = QColor(self.view.color)
    too_light = color.lightness() > 190
    too_dark = color.lightness() < 60
    if paper == "light" and too_light:
        self.view.set_color("#2c2c2e")
    elif paper == "dark" and too_dark:
        self.view.set_color("#e8eaed")
    self._reflect_tools()
```

图标：
- `note_icon()`：圆角便签 + 折角；
- `paper_icon(dark)`：半明半暗的圆；
- `delete_icon()`：垃圾桶 + 减号（与 clear_icon 区分）。

主题 QSS：
```css
QWidget#NoteCard { background:#2f3033; border:1px solid #6e6e73; border-radius:12px; }
QWidget#NoteHeader { background:#35363a; border-top-left-radius:12px; border-top-right-radius:12px; }
QWidget#NoteTools { background:#303135; }
```
颜色加到 `theme.py` 常量（`NOTE_CARD_BG` 等）。

- [ ] **Step 4: 实现 `NoteOverlay`**

```python
class NoteOverlay(QWidget):
    closed = Signal()
    deleteRequested = Signal()
    contentChanged = Signal()

    def __init__(self, stack, settings, parent=None):
        super().__init__(parent or stack)
        self.stack = stack
        self.settings = settings
        self.card = None
        self._expanded = False
        self.is_open = False
        self.setObjectName("NoteOverlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setGeometry(stack.rect())
        self._anim: QPropertyAnimation | None = None

    def paintEvent(self, ev):
        p = QPainter(self); p.fillRect(self.rect(), QColor(0, 0, 0, 90)); p.end()

    def mousePressEvent(self, ev):
        if self.card is not None and not self.card.geometry().contains(ev.position().toPoint()):
            self.close_note()
        ev.accept()

    def open_note(self, note, anchor_view_pos):
        self.is_open = True
        self.card = NoteCard(note, self.settings, self)
        self.card.closed...  # 实际信号名 closeRequested
        self.card.closeRequested.connect(self.close_note)
        self.card.deleteRequested.connect(self.deleteRequested)
        self.card.contentChanged.connect(self.contentChanged)
        self.card.toggleExpandRequested.connect(self.toggle_expand)
        self.setGeometry(self.stack.rect()); self.raise_(); self.show()
        start = QRect(anchor_view_pos.x()-12, anchor_view_pos.y()-12, 24, 24)
        end = self._base_rect()
        self.card.setGeometry(start); self.card.show()
        self._animate(start, end, 200)
        self.card.view.setFocus()

    def close_note(self):
        if not self.is_open: return
        self.is_open = False
        if self.card is None:
            self.hide(); self.closed.emit(); return
        end = self.card.geometry()
        self._animate(end, QRect(end.center().x()-12, end.center().y()-12, 24, 24), 160,
                      on_done=self._finish_close)

    def _finish_close(self):
        c, self.card = self.card, None
        if c is not None: c.deleteLater()
        self.hide(); self.closed.emit()

    def toggle_expand(self):
        if self.card is None: return
        target = self._large_rect() if not self._expanded else self._base_rect()
        self._expanded = not self._expanded
        self._animate(self.card.geometry(), target, 220)

    def _base_rect(self):
        # 560x720 比例，按 stack 可用区域封顶（最大 90%），居中偏上
    def _large_rect(self):
        # 视口宽高 80%，保持 560:720 比例，居中
    def _animate(self, start, end, ms, on_done=None):
        anim = QPropertyAnimation(self.card, b"geometry", self)
        anim.setDuration(ms); anim.setStartValue(start); anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        if on_done: anim.finished.connect(on_done)
        anim.start(QAbstractAnimation.DeleteWhenStopped)
        self._anim = anim

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if (self.is_open and self.card is not None
                and (self._anim is None or self._anim.state() != QAbstractAnimation.Running)):
            self.card.setGeometry(self._large_rect() if self._expanded else self._base_rect())
```

注意：
- `NoteCard` 的子控件会接收鼠标事件；遮罩只在卡片外关闭。
- 卡片打开时 `self.raise_()`；`stack.installEventFilter` 或在 `MainWindow.resizeEvent` 调 `overlay.setGeometry(stack.rect())`；覆盖层自己 `resizeEvent` 只调整卡片。
- `QPropertyAnimation` 对象留引用。

- [ ] **Step 5: 跑测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_note_card.py -q`
Expected: PASS

- [ ] **Step 6: （用户批准后）提交**

```bash
git add sketchpad/ui/note_card.py sketchpad/ui/icons.py sketchpad/ui/theme.py tests/test_note_card.py
git commit -m "feat(notes): add note card and overlay"
```

---

## Task 4: PDF 图标、放置工具与开卡接线

**Files**
- Create: `sketchpad/ui/note_pin.py`
- Modify: `sketchpad/ui/pdfview.py`
- Test: `tests/test_note_pins.py`

**Interfaces**
- Produces:
  - `NotePin(note_id: str, number: int)`（QGraphicsItem，`ItemIgnoresTransformations`，24×24，z=4）
  - `PdfView.notePlacementRequested = Signal(int, float, float)`
  - `PdfView.noteRequested = Signal(int, str)`
  - `PdfView.add_note(page_index: int, note: Note) -> None`
  - `PdfView.remove_note(page_index, note_id) -> None`
  - `PdfView.rebuild_note_pins(page_index) -> None`
  - `PdfView.note_anchor_view_pos(page_index, note_id) -> QPoint | None`
  - `PdfView.set_tool("note")` 支持。

- [ ] **Step 1: 写失败测试**

`tests/test_note_pins.py`：

```python
import os; os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pymupdf, pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from sketchpad.core.notes import Note
from sketchpad.core.settings import Settings
from sketchpad.ui.pdfview import PdfView

@pytest.fixture(scope="module")
def qapp(): yield QApplication.instance() or QApplication([])

@pytest.fixture
def pdf(tmp_path):
    p = tmp_path/"n.pdf"; d = pymupdf.open(); d.new_page(width=300, height=400)
    d.save(str(p)); d.close(); return p

@pytest.fixture
def view(qapp):
    v = PdfView(Settings()); v.resize(400, 500); v.show(); QTest.qWaitForWindowExposed(v)
    yield v; v.close_document(); v.close()

def test_note_tool_emits_placement(view, pdf):
    view.open(pdf); view.set_tool("note")
    got = []
    view.notePlacementRequested.connect(lambda *a: got.append(a))
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=QPoint(100, 100))
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=QPoint(100, 100))
    assert got and got[0][0] == 0
    assert 0 <= got[0][1] <= 300 and 0 <= got[0][2] <= 400

def test_add_pin_and_click_emits_note_request(view, pdf):
    view.open(pdf)
    note = Note("n-1", 50.0, 60.0)
    view.document.pages[0].notes.append(note)
    view.rebuild_note_pins(0)
    pos = view.note_anchor_view_pos(0, "n-1")
    assert pos is not None
    got = []
    view.noteRequested.connect(lambda p, i: got.append((p, i)))
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=pos)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=pos)
    assert got == [(0, "n-1")]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_note_pins.py -q`
Expected: FAIL

- [ ] **Step 3: 实现 `NotePin`**

- `boundingRect` 约 `QRectF(-13,-13,26,26)`；`paint` 画便签形状（圆角矩形 + 右上折角）+ 序号（白色 9pt），深色底/浅色边；`shape` 用圆角矩形；`setFlag(ItemIgnoresTransformations, True)`；`setZValue(4)`；`setAcceptHoverEvents(True)`；hover 画亮边。
- `data(0)` 存 `("note", note_id)` 或单独属性；PdfView 用 `isinstance(item, NotePin)` 判定。
- 注意 `ItemIgnoresTransformations` 下 `setPos` 仍是场景坐标；图标以锚点为中心。

- [ ] **Step 4: 改 `PdfView`**

- 顶部 import `NotePin`, `Note`.
- 新信号。
- `__init__`: `self._note_pins: dict[str, NotePin] = {}`。
- `set_tool` 允许 `"note"`；`_update_cursor` 对 note 用 `Qt.CrossCursor`。
- `mousePressEvent`：
  - 先检查 pin：
    ```python
    if ev.button() == Qt.LeftButton:
        for item in self.items(ev.position()):
            if isinstance(item, NotePin):
                self.noteRequested.emit(item.page_index, item.note_id)
                ev.accept(); return
    ```
  - 若 `self.tool == "note"` 且 page index 有效：
    ```python
    layer = self._layers[index]
    local = (scene_point.x()-layer.x, scene_point.y()-layer.y)
    self.notePlacementRequested.emit(index, local[0], local[1])
    ev.accept(); return
    ```
- `add_note(page_index, note)`：append 到 `document.pages[page_index].notes`，`rebuild_note_pins(page_index)`。
- `remove_note`：过滤 notes，rebuild。
- `rebuild_note_pins(page_index)`：
  - 删除该页旧 pins（`self._note_pins.pop` + `scene.removeItem`）。
  - `for number, note in enumerate(page.notes, 1)`: pin = NotePin(note.id, number); pin.setPos(layer.x+note.x, layer.y+note.y); scene.addItem; dict[id]=pin。
  - 不随缩放重建（ItemIgnoresTransformations）。
- `_build_layers` 末尾 `rebuild_note_pins(index)`。
- `close_document` 清空 `_note_pins`（scene.clear 已删）。
- `note_anchor_view_pos`：取 pin；`self.mapFromScene(pin.scenePos())`，返回 QPoint；`mapFromScene` in PdfView returns QPoint (or `_ScreenPoint`?). PdfView's mapFromScene is not overridden? It might return QPoint; use `self.viewport().mapFrom(self, ...)`? Simpler:
  ```python
  scene_pos = self.mapToScene(...) ...
  screen = self.mapFromScene(pin.scenePos())
  return QPoint(int(screen.x()), int(screen.y()))
  ```
- Pin click detection with `self.items(ev.position())` includes items under; `ItemIgnoresTransformations` shape; okay.
- Pin tool cursor: when tool == "note", crosshair; hovering a pin could show pointing hand in `mouseMoveEvent` (optional).

- [ ] **Step 5: 跑测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_note_pins.py tests/test_pdfview_offscreen.py -q`
Expected: PASS

- [ ] **Step 6: （用户批准后）提交**

```bash
git add sketchpad/ui/note_pin.py sketchpad/ui/pdfview.py tests/test_note_pins.py
git commit -m "feat(notes): add note pins and placement tool"
```

---

## Task 5: 主窗接线：创建/展开/保存/删除/焦点

**Files**
- Modify: `sketchpad/ui/toolbox.py`
- Modify: `sketchpad/ui/menubar.py`
- Modify: `sketchpad/app.py`
- Test: `tests/test_note_wiring.py`

**Interfaces**
- Produces:
  - Toolbox：`note_button`、`set_note_enabled(bool)`。
  - Menubar：`note_action`（工具菜单，快捷键 N，文档模式）。
  - `MainWindow._create_note(page, x, y)`、`_open_note(page, note_id)`、`_delete_note(page, note_id)`、`_close_note_card()`。
  - `_apply_tool("note")` 仅在文档模式生效；草稿模式忽略。
  - `self._note_card: NoteOverlay | None`。

- [ ] **Step 1: 写失败测试**

`tests/test_note_wiring.py`（offscreen，复制 `test_app_modes_offscreen.py` 的 fixture）：

```python
def test_note_tool_creates_pin_and_card_roundtrip(win, pdf):
    assert win._enter_document(pdf)
    win._apply_tool("note")
    got = []
    win.pdf_view.notePlacementRequested.connect(lambda *a: got.append(a))
    # 直接调 _create_note，避免坐标换算；再验证 pin 与卡片
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]
    assert note.x == 40.0 and note.y == 50.0
    win._open_note(0, note.id)
    assert win._note_card is not None and win._note_card.is_open
    win._note_card.card.view.document.strokes.append(
        Stroke([(10.0, 10.0), (30.0, 30.0)], "#e8eaed", 2.0))
    win._note_card.card.view.scene.rebind()
    win._note_card.close_note()
    qwait(300)
    assert win._note_card is None
    assert note.strokes and win._dirty is True

def test_note_tool_only_in_document_mode(win, pdf):
    assert win._enter_document(pdf)
    win._apply_tool("note")
    assert win.pdf_view.tool == "note"
    win._enter_draft()
    assert not win.toolbox.note_button.isVisible()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_note_wiring.py -q`
Expected: FAIL

- [ ] **Step 3: Toolbox/Menubar**

- `icons.note_icon()`；Toolbox `note_button` 加入 tool group，tooltip `"插入笔记 (N)"`，`set_note_enabled` 控制 `setVisible`（文档模式才显示，类似 select）。
- Menubar `note_action`：工具菜单 `插入笔记`，`checkable=True`，`setShortcut(QKeySequence("N"))`；加入 `set_mode` 的文档模式列表；`set_tool` 映射。
- `Toolbox.reflect_tool` 加 `"note"`。
- 主工具栏的 `toolChosen` 发 `"note"`；app `_apply_tool` 调 `pdf_view.set_tool("note")`；草稿视图不设 note。

- [ ] **Step 4: App 接线**

```python
self._note_card: NoteOverlay | None = None
pdf_view.notePlacementRequested.connect(self._create_note)
pdf_view.noteRequested.connect(self._open_note)

def _create_note(self, page: int, x: float, y: float) -> None:
    document = self.pdf_view.document
    if document is None or not 0 <= page < len(document.pages):
        return
    page_obj = document.pages[page]
    note = Note(new_note_id({n.id for n in page_obj.notes}),
                *clamp_note_anchor(x, y, page_obj.width, page_obj.height))
    self.pdf_view.add_note(page, note)
    self._on_notes_changed()
    self._note(f"已插入笔记 {len(page_obj.notes)}，点图标展开")

def _open_note(self, page: int, note_id: str) -> None:
    if self._note_card is not None:
        self._close_note_card()
    document = self.pdf_view.document
    if document is None or not 0 <= page < len(document.pages):
        return
    note = next((n for n in document.pages[page].notes if n.id == note_id), None)
    anchor = self.pdf_view.note_anchor_view_pos(page, note_id)
    if note is None or anchor is None:
        return
    overlay = NoteOverlay(self.stack, self.controller.settings)
    overlay.contentChanged.connect(self._on_notes_changed)
    overlay.deleteRequested.connect(lambda p=page, i=note_id: self._delete_note(p, i))
    overlay.closed.connect(self._on_note_closed)
    self._note_card = overlay
    overlay.open_note(note, anchor)

def _on_notes_changed(self) -> None:
    self._on_strokes_changed()          # 复用 dirty + 自动暂存

def _on_note_closed(self) -> None:
    self._note_card = None

def _close_note_card(self) -> None:
    if self._note_card is not None:
        self._note_card.close_note()

def _delete_note(self, page: int, note_id: str) -> None:
    # 确认框在 NoteCard 里已弹；这里只改模型
    self._close_note_card()
    self.pdf_view.remove_note(page, note_id)
    self._on_notes_changed()
```

- `_enter_draft` / `_enter_document` 开头：`self._close_note_card()`，再 `_flush_autosave()`（避免切文档时卡片还开着）。
- `quit_app` / `closeEvent`：先 `_close_note_card()`，再 flush。
- 焦点按键分流：在 `eventFilter` 的工具键分支前：
```python
focus = QApplication.focusWidget()
if self._note_card is not None and focus is not None and self._note_card.isAncestorOf(focus):
    return False        # 让 NoteView 自己处理 B/Q/1-4/Ctrl+Z/E
```
  - NoteView 的 `keyPressEvent` 已处理 B/Q/1-4/Ctrl+Z/Y；清屏键由 NoteView 的 QShortcut 处理（`SketchView.set_clear_key`）。
  - 主工具栏的 undo/clear 仍走 `_active_view()` → PDF。

- [ ] **Step 5: 跑测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_note_wiring.py tests/test_app_modes_offscreen.py tests/test_toolbox_offscreen.py tests/test_menubar_offscreen.py -q`
Expected: PASS

- [ ] **Step 6: （用户批准后）提交**

```bash
git add sketchpad/ui/toolbox.py sketchpad/ui/menubar.py sketchpad/app.py tests/test_note_wiring.py
git commit -m "feat(notes): wire note tool, card lifecycle and focus routing"
```

---

## Task 6: 导出笔记页

**Files**
- Modify: `sketchpad/pdf/document.py`
- Modify: `sketchpad/app.py`
- Test: `tests/test_export_notes.py`

**Interfaces**
- Produces:
  - `export_with_notes(src, pages_strokes, pages_notes, out_path) -> Path`
  - `pages_notes: list[list[Note]]`，长度必须等于页数。
  - 原 `export_with_strokes` 不变（无笔记路径）。
- 导出页序：原页 0 → 页 0 的笔记页 → 原页 1 → 页 1 的笔记页 → …
- 笔记页：宽度 = 锚点页宽度；高度 = 宽度 * note.h/note.w；绘制纸面/横线/页边线/笔记笔迹；锚点页上画带序号的小标记。

- [ ] **Step 1: 写失败测试**

`tests/test_export_notes.py`：

```python
import pymupdf
import pytest
from pathlib import Path
from sketchpad.core.notes import Note
from sketchpad.core.strokes import Stroke
from sketchpad.pdf.document import PdfSource, export_with_notes

def test_note_pages_follow_anchor_pages(tmp_path):
    src = tmp_path/"in.pdf"; d = pymupdf.open()
    d.new_page(width=300, height=400); d.new_page(width=300, height=400)
    d.save(str(src)); d.close()
    note = Note("n-1", 40, 50, 560, 720, "light",
                [Stroke([(10.0, 10.0), (100.0, 100.0)], "#2c2c2e", 2.0)])
    out = tmp_path/"out.pdf"
    export_with_notes(PdfSource(src), [[], []], [[], [note]], out)
    d = pymupdf.open(str(out))
    try:
        assert d.page_count == 3
        assert d[2].rect.width == pytest.approx(300.0)
        assert d[2].rect.height == pytest.approx(300.0 * 720 / 560)
        assert d[2].get_pixmap().samples  # 页面可渲染
    finally:
        d.close()

def test_export_does_not_overwrite_source(tmp_path): ...
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_export_notes.py -q`
Expected: FAIL

- [ ] **Step 3: 实现导出**

- `export_with_notes`：
  - 校验不覆盖原文件、页数、notes 长度。
  - 打开源；新建 `out_doc = pymupdf.open()`。
  - 对每页：
    ```python
    out_doc.insert_pdf(src_doc, from_page=i, to_page=i)
    page = out_doc[-1]
    for stroke in _highlights_first(pages_strokes[i]): _draw_stroke(page, i, stroke)
    for number, note in enumerate(pages_notes[i], 1):
        _draw_note_marker(page, note, number)   # 小圆角标记
        _append_note_page(out_doc, note)
    ```
  - `_append_note_page`：
    ```python
    page_w = note_page_width  # 传入锚点页宽
    page_h = page_w * note.h / note.w
    p = out_doc.new_page(width=page_w, height=page_h)
    scale = page_w / note.w
    bg = PAPER_BG[note.paper]; line = PAPER_LINE[note.paper]
    p.draw_rect(p.rect, color=None, fill=_rgb(bg), width=0)
    pen_color = _rgb(line)
    for y in range(28, int(note.h), 28):
        yy = y * scale
        p.draw_line((0, yy), (page_w, yy), color=pen_color, width=0.5)
    p.draw_line((42*scale, 0), (42*scale, page_h), color=pen_color, width=0.5)
    for stroke in _highlights_first(note.strokes):
        scaled = Stroke([(x*scale, y*scale) for x, y in stroke.points],
                        stroke.color, stroke.width*scale, stroke.tool)
        _draw_stroke(p, 0, scaled)
    ```
  - 标记：在锚点页画一个 14×14 的圆角/圆 + 序号；坐标用 `note.x/y`，乘 `page.derotation_matrix`。
  - 保存。
- App `_export_pdf`：
  ```python
  notes = [page.notes for page in view.document.pages]
  if any(notes):
      export_with_notes(view.source, [p.strokes ...], notes, out)
  else:
      export_with_strokes(...)
  ```
- 测试也验证原 PDF 哈希不变。

- [ ] **Step 4: 跑测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_export_notes.py tests/test_pdfdoc.py -q`
Expected: PASS

- [ ] **Step 5: （用户批准后）提交**

```bash
git add sketchpad/pdf/document.py sketchpad/app.py tests/test_export_notes.py
git commit -m "feat(notes): export note pages after anchor pages"
```

---

## Task 7: 文档、全量回归与打包

**Files**
- Modify: `README.md`、`PROGRESS.md`
- 无新代码

- [ ] **Step 1: 更新文档**
  - README：新增「笔记卡片」一节（N 工具、卡片工具栏、深浅纸、导出页序、sidecar v2/旧版拒绝）。
  - PROGRESS：功能表新增一行（需求、代码、测试、验证方式）与变更记录。
- [ ] **Step 2: 全量回归**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: 全部通过；记录准确数字。

- [ ] **Step 3: 真实窗口验收**
  - 打开真实讲义；N 放图标；展开、写/擦/撤销、深浅纸、放大/还原；关闭重开内容还在；
  - 焦点在卡片时 B/Q/1-4/Ctrl+Z 只影响笔记；主工具栏垃圾桶只清 PDF；
  - 导出，检查锚点页后的笔记页；原 PDF 不变；
  - 用旧稳定版打开 v2 sidecar，确认拒绝而非丢数据。

- [ ] **Step 4: 重建稳定包**
  - 先保存/关闭正在运行的稳定版（笔记 v2 不能被旧包保存）；
  - `.venv\Scripts\python.exe tools\build_exe.py`；
  - 产物按现有约定整理到 `dist/SketchPad-v2.13/`（版本号按需升）；
  - 实机启动、打开讲义、放置/展开/导出各验一次。

---

## Self-Review

- **Spec coverage**：数据模型/版本（Task 1）、纸面/画布（Task 2）、卡片/工具栏/深浅纸/放大（Task 3）、图标/放置（Task 4）、生命周期/焦点/自动暂存（Task 5）、导出（Task 6）、文档/验收/打包（Task 7）。
- **类型一致性**：`Note` 在 Task 1 定义，Task 2/3/4/5/6 都使用 `Note(id,x,y,w,h,paper,strokes)`；`NoteOverlay.open_note(note, anchor)` 在 Task 3 定义、Task 5 使用；`PdfView.add_note(page_index, note)` 在 Task 4 定义、Task 5 使用；`export_with_notes(src, pages_strokes, pages_notes, out)` 在 Task 6 定义/使用。
- **无占位**：所有任务给出实际测试/实现片段；UI 细节按结构列出，执行时以现有控件风格为准。
- **风险**：卡片覆盖层与主窗焦点、sidecar v2 与旧稳定版、导出页序是三处高风险点，已分别有任务与验收。
