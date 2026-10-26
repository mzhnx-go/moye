# SketchPad 实施计划

> **2026-10-20 需求变更**：撤销保存功能（用户：「不需要保存功能，这是草稿」）。本计划中 **Task 6（storage）整体作废**，Task 11 的自动保存/启动恢复部分作废；草稿只存在内存。以设计文档 §8 为准，下文相关步骤仅作历史记录。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 做一个 Windows 桌面极简草稿板：颜色 + 4 档固定粗细 + 一键清屏（默认 `E`，可自定义、可撤销）+ 滚轮平移 + 单文件自动保存。

**Architecture:** 三层单向依赖：纯逻辑模型层（笔迹/视口/撤销栈/命令/存储/配置，全部不 import Qt）→ Qt 视图层（QGraphicsView + 每条笔迹一个 PathItem）→ `main.py` 只做接线。渲染层可整体替换，为将来加压感留位置但不留实现。

**Tech Stack:** Python 3.13、PySide6（Qt 6，视图层）、pytest 7.4.4（本机已装）。

**Spec:** `docs/superpowers/specs/2026-10-20-sketchpad-design.md`（本计划逐条实现它；数值与键位一律以设计文档为准，计划里不许改口径）

## Global Constraints

- Python 3.13（本机 `python -V` 实测）。纯逻辑模块 `app/strokes.py`、`app/camera.py`、`app/history.py`、`app/commands.py`、`app/storage.py`、`app/settings.py` **禁止** `import PySide6`/`import Qt`。
- 视图层模块：`app/canvas.py`、`app/toolbox.py`、`app/probe.py`、`main.py`。
- 第三方依赖只有 PySide6。不引入 Qt 之外的绘图/图像库。
- PySide6 装在**项目内** `.venv`（用户 2026-10-20 指定，覆盖其「第三方工具放 D:\软件」的旧习惯）：`E:\SketchPad\.venv`，不写系统 Python。凡涉及 Qt 的测试与运行一律用 `.venv/Scripts/python.exe -m pytest` / `.venv/Scripts/python.exe main.py`；纯逻辑模块（Task 1–7）用系统 `python -m pytest` 即可。
- `.gitignore` 已定：`.venv/`、`__pycache__/`、`*.pyc`、`.pytest_cache/`、`data/`（画稿属个人数据，不进版本库）。
- 环境实测（2026-10-20，安装完成后当场验的）：`.venv` 里是 **PySide6 6.12.0 / Qt 6.12.0 + pytest 9.1.1**；系统 `python` 是 3.13.0 + pytest 7.4.4。离屏冒烟已过：`QT_QPA_PLATFORM=offscreen` 下 `QGraphicsView`/`QGraphicsScene` 能建、`PySide6.QtTest.QTest` 可用、`devicePixelRatio()` 为 1.0。**从这里起所有测试一律用 `.venv/Scripts/python.exe -m pytest`**（纯逻辑与 Qt 同一把尺，避免两套 pytest 版本各说一遍好话）。
- Qt 6 的枚举不是整数：`Qt.WidgetWithChildrenShortcut` 是 `ShortcutContext` 对象，对它 `int()` 会 `TypeError`；比较时直接用枚举成员，别写数字。
- 一个项目一个 git 仓：`E:\SketchPad` 已 `git init`。每次提交用显式路径 `git add`。用户已批准本轮按任务边界提交（2026-10-20「装，提，按你建议的来」）。
- 数值取自设计文档 §2/§5/§6/§7，逐字照抄：粗细 `1.5/3/6/12`；抽稀阈值 `1.5`；单条笔迹点数上限 `4000`；缩放 `MIN_SCALE=0.08`、`MAX_SCALE=16.0`；滚轮缩放步长 `WHEEL_ZOOM_STEP=1.08`；平移比例 `WHEEL_PAN_RATIO=0.10`；自动保存延迟 `1.5` 秒；撤销深度 `100`；预设 8 色 `#1a1a1a #c0392b #d35400 #f1c40f #27ae60 #2980b9 #8e44ad #7f8c8d`。
- 变换约定：`screen = world * scale + origin`，`origin` 是世界原点 `(0,0)` 落在屏幕上的位置（设计文档 §5/§10）。
- 快捷键一律 `Qt.WidgetWithChildrenShortcut`，挂在画布视图上（设计文档 §6.1）。
- 测试一律等值断言（比较具体值/序列，不写「不为 None」「不抛异常」式空断言）；红必须自然发生：先跑测试看到 FAIL，再实现。
- 代码注释与界面文案中文，无 emoji。
- 数据文件：`data/draft.json`，备份 `data/draft.json.bak`，中间产物 `data/draft.json.tmp`。

---

### Task 1: 笔迹数据模型与抽稀（`app/strokes.py` 第一部分）

**Files:**
- Create: `app/strokes.py`
- Create: `tests/test_strokes_thin.py`
- Create: `app/__init__.py`（空文件）、`tests/__init__.py`（空文件）

**Interfaces:**
- Consumes: 无
- Produces: `Point`、`Stroke(points, color, width, tool="pen")`、`Viewport(scale=1.0, origin=(0.0,0.0))`、`Document(strokes, viewport)`、`DraftError`、`dist(a,b)`、`thin(points, min_dist=1.5)`、常量 `MIN_POINT_DIST=1.5`、`MAX_POINTS_PER_STROKE=4000`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_strokes_thin.py
from app.strokes import dist, thin

def test_dist_3_4_5():
    assert dist((0.0, 0.0), (3.0, 4.0)) == 5.0

def test_thin_drops_points_closer_than_threshold_and_keeps_first_and_last():
    # (0,0) (1.49,0) 被吞；(3.0,0) 保留；末点 (3.4,0) 距离 0.4 仍强制保留
    pts = [(0.0, 0.0), (1.49, 0.0), (3.0, 0.0), (3.4, 0.0)]
    assert thin(pts) == [(0.0, 0.0), (3.0, 0.0), (3.4, 0.0)]

def test_thin_boundary_exactly_threshold_is_kept():
    assert thin([(0.0, 0.0), (1.5, 0.0), (9.0, 0.0)]) == [(0.0, 0.0), (1.5, 0.0), (9.0, 0.0)]

def test_thin_boundary_just_under_threshold_is_dropped():
    assert thin([(0.0, 0.0), (1.499, 0.0), (9.0, 0.0)]) == [(0.0, 0.0), (9.0, 0.0)]

def test_thin_single_point_stroke_survives():
    assert thin([(2.0, 7.0)]) == [(2.0, 7.0)]

def test_thin_empty_returns_empty():
    assert thin([]) == []
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_strokes_thin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.strokes'`

- [ ] **Step 3: 写最小实现**

```python
# app/strokes.py
"""笔迹数据模型：世界坐标点列 + 颜色 + 线宽。本模块不依赖 Qt。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

MIN_POINT_DIST = 1.5
MAX_POINTS_PER_STROKE = 4000
FORMAT_VERSION = 1

Point = tuple[float, float]


class DraftError(Exception):
    """草稿读不出来：JSON 坏了、字段缺失、结构不对。"""


@dataclass
class Stroke:
    points: list[Point]
    color: str
    width: float
    tool: str = "pen"


@dataclass
class Viewport:
    scale: float = 1.0
    origin: Point = (0.0, 0.0)


@dataclass
class Document:
    strokes: list[Stroke] = field(default_factory=list)
    viewport: Viewport = field(default_factory=Viewport)


def dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def thin(points: list[Point], min_dist: float = MIN_POINT_DIST) -> list[Point]:
    """丢掉离上一个保留点过密的点；首点与末点始终保留。"""
    if not points:
        return []
    kept = [points[0]]
    for p in points[1:]:
        if dist(kept[-1], p) >= min_dist:
            kept.append(p)
    last = points[-1]
    if len(points) > 1 and kept[-1] != last:
        kept.append(last)
    return kept
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_strokes_thin.py -v`
Expected: PASS（6 个）

- [ ] **Step 5: 提交（需用户批准）**

```bash
git add app/__init__.py app/strokes.py tests/__init__.py tests/test_strokes_thin.py
git commit -m "feat(strokes): 笔迹数据模型与抽稀"
```

---

### Task 2: JSON 编解码 + 版本闸门 + 命中距离（`app/strokes.py` 第二部分）

**Files:**
- Modify: `app/strokes.py`（追加 `encode`、`decode`、`stroke_distance`、`VersionTooNew`）
- Create: `tests/test_strokes_json.py`、`tests/test_strokes_hit.py`

**Interfaces:**
- Consumes: Task 1 的 `Stroke`/`Viewport`/`Document`/`DraftError`/`Point`/`dist`
- Produces: `encode(doc) -> str`、`decode(text) -> Document`、`VersionTooNew(DraftError)`、`stroke_distance(stroke, p) -> float`

- [ ] **Step 1: 写失败测试（往返 + 错误输入 + 版本）**

```python
# tests/test_strokes_json.py
import pytest
from app.strokes import Document, Stroke, VersionTooNew, Viewport, decode, encode

def _doc():
    return Document(
        [Stroke([(0.0, 0.0), (5.0, 5.0)], "#1a1a1a", 3.0),
         Stroke([(10.0, 2.5)], "#c0392b", 12.0)],
        viewport=Viewport(1.25, (40.0, -7.5)),
    )

def test_round_trip_preserves_every_field():
    d = _doc()
    assert decode(encode(d)) == d

def test_encoded_numbers_have_at_most_two_decimals_in_points():
    d = Document([Stroke([(1.0, 2.333333)], "#000000", 3.0)])
    assert '"p":[[1.0,2.33]]' in encode(d)

def test_decode_rejects_garbage():
    with pytest.raises(Exception) as e:
        decode("{not json")
    assert "invalid json" in str(e.value)

def test_decode_rejects_missing_points():
    from app.strokes import DraftError
    with pytest.raises(DraftError):
        decode('{"version":1,"strokes":[{"c":"#000000","w":3}]}')

def test_decode_refuses_newer_version_and_says_which():
    with pytest.raises(VersionTooNew) as e:
        decode('{"version":2,"strokes":[]}')
    assert "2" in str(e.value)

def test_decode_of_empty_strokes_gives_empty_document():
    assert decode('{"version":1,"strokes":[]}').strokes == []
```

```python
# tests/test_strokes_hit.py
from app.strokes import Stroke, stroke_distance

def test_distance_to_segment_not_just_vertices():
    s = Stroke([(0.0, 0.0), (10.0, 0.0)], "#000000", 3.0)
    assert stroke_distance(s, (5.0, 2.0)) == 2.0

def test_distance_to_single_point_stroke_is_radial():
    s = Stroke([(3.0, 4.0)], "#000000", 3.0)
    assert stroke_distance(s, (0.0, 0.0)) == 5.0

def test_distance_outside_segment_ends_clamps_to_endpoint():
    s = Stroke([(0.0, 0.0), (10.0, 0.0)], "#000000", 3.0)
    assert stroke_distance(s, (20.0, 0.0)) == 10.0
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_strokes_json.py tests/test_strokes_hit.py -v`
Expected: FAIL — `ImportError: cannot import name 'encode'`

- [ ] **Step 3: 实现 encode/decode/stroke_distance**

```python
# app/strokes.py 追加（放在 thin 之后）
import json


class VersionTooNew(DraftError):
    """草稿由更新的版本写入，本版不猜它的格式。"""


def encode(doc: Document) -> str:
    return json.dumps(
        {
            "version": FORMAT_VERSION,
            "viewport": {
                "scale": round(doc.viewport.scale, 4),
                "origin": [round(doc.viewport.origin[0], 2), round(doc.viewport.origin[1], 2)],
            },
            "strokes": [
                {
                    "p": [[round(x, 2), round(y, 2)] for x, y in s.points],
                    "c": s.color,
                    "w": s.width,
                    "t": s.tool,
                }
                for s in doc.strokes
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode(text: str) -> Document:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DraftError(f"invalid json: {exc}") from exc
    version = data.get("version")
    if not isinstance(version, int):
        raise DraftError(f"missing or bad version: {version!r}")
    if version > FORMAT_VERSION:
        raise VersionTooNew(f"draft written by newer version {version}")
    strokes: list[Stroke] = []
    for i, raw in enumerate(data.get("strokes", [])):
        try:
            pts = [(float(x), float(y)) for x, y in raw["p"]]
            strokes.append(Stroke(pts, str(raw["c"]), float(raw["w"]), str(raw.get("t", "pen"))))
        except (KeyError, TypeError, ValueError) as exc:
            raise DraftError(f"stroke #{i} malformed: {exc}") from exc
    vp = data.get("viewport") or {}
    origin = vp.get("origin") or [0.0, 0.0]
    return Document(strokes, Viewport(float(vp.get("scale", 1.0)), (float(origin[0]), float(origin[1]))))


def stroke_distance(stroke: Stroke, p: Point) -> float:
    """点到笔迹折线的最短距离；单点笔迹退化为点距。"""
    pts = stroke.points
    if not pts:
        return math.inf
    best = dist(pts[0], p)
    for a, b in zip(pts, pts[1:]):
        vx, vy = b[0] - a[0], b[1] - a[1]
        seg_len_sq = vx * vx + vy * vy
        if seg_len_sq == 0.0:
            continue
        t = max(0.0, min(1.0, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / seg_len_sq))
        best = min(best, dist((a[0] + t * vx, a[1] + t * vy), p))
    return best
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_strokes_json.py tests/test_strokes_hit.py tests/test_strokes_thin.py -v`
Expected: PASS（全部）

- [ ] **Step 5: 提交（需用户批准）**

```bash
git add app/strokes.py tests/test_strokes_json.py tests/test_strokes_hit.py
git commit -m "feat(strokes): JSON 往返、版本闸门与折线命中距离"
```

---

### Task 3: 视口算术（`app/camera.py`）

**Files:**
- Create: `app/camera.py`
- Create: `tests/test_camera.py`

**Interfaces:**
- Consumes: Task 1 的 `Viewport`、`Point`
- Produces: `MIN_SCALE`、`MAX_SCALE`、`WHEEL_ZOOM_STEP`、`WHEEL_PAN_RATIO`、`clamp_scale(s)`、`world_to_screen(vp, p)`、`screen_to_world(vp, p)`、`pan_by(vp, dx, dy)`、`zoom_at(vp, screen_pos, factor)`

- [ ] **Step 1: 写失败测试（锚点不变量是重点）**

```python
# tests/test_camera.py
import pytest
from app.camera import (MAX_SCALE, MIN_SCALE, Viewport, clamp_scale, pan_by,
                        screen_to_world, world_to_screen, zoom_at)

def test_transform_round_trip_is_exact_enough():
    vp = Viewport(2.5, (13.0, -4.0))
    p = (101.0, 77.0)
    assert screen_to_world(vp, world_to_screen(vp, p)) == pytest.approx(p, abs=1e-9)

def test_pan_moves_origin_and_leaves_scale():
    vp = Viewport(1.0, (0.0, 0.0))
    after = pan_by(vp, 0.0, 100.0)
    assert after.origin == (0.0, 100.0)
    assert after.scale == 1.0

def test_zoom_at_keeps_the_world_point_under_the_cursor():
    vp = Viewport(1.0, (0.0, 0.0))
    cursor = (400.0, 300.0)
    before = screen_to_world(vp, cursor)
    after = zoom_at(vp, cursor, 1.08 ** 3)
    assert screen_to_world(after, cursor) == pytest.approx(before, abs=1e-6)

def test_zoom_clamps_at_max_and_min():
    vp = Viewport(15.9, (0.0, 0.0))
    assert zoom_at(vp, (0.0, 0.0), 1000.0).scale == MAX_SCALE
    # 15.9 / 1000 = 0.0159，只能钳到 MIN_SCALE；1.0 在这里不可达
    assert zoom_at(vp, (0.0, 0.0), 1.0 / 1000.0).scale == MIN_SCALE
    assert clamp_scale(0.001) == MIN_SCALE
    assert clamp_scale(999.0) == MAX_SCALE

def test_zoom_by_factor_one_is_identity():
    vp = Viewport(1.0, (13.0, -4.0))
    assert zoom_at(vp, (123.0, 45.0), 1.0) == vp
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_camera.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.camera'`

- [ ] **Step 3: 实现**

```python
# app/camera.py
"""世界↔屏幕坐标算术：screen = world * scale + origin。本模块不依赖 Qt。"""
from __future__ import annotations

from app.strokes import Point, Viewport

MIN_SCALE = 0.08
MAX_SCALE = 16.0
WHEEL_ZOOM_STEP = 1.08
WHEEL_PAN_RATIO = 0.10


def clamp_scale(scale: float) -> float:
    return min(MAX_SCALE, max(MIN_SCALE, scale))


def world_to_screen(vp: Viewport, p: Point) -> Point:
    return (p[0] * vp.scale + vp.origin[0], p[1] * vp.scale + vp.origin[1])


def screen_to_world(vp: Viewport, p: Point) -> Point:
    return ((p[0] - vp.origin[0]) / vp.scale, (p[1] - vp.origin[1]) / vp.scale)


def pan_by(vp: Viewport, dx: float, dy: float) -> Viewport:
    return Viewport(vp.scale, (vp.origin[0] + dx, vp.origin[1] + dy))


def zoom_at(vp: Viewport, screen_pos: Point, factor: float) -> Viewport:
    """以 screen_pos 为锚缩放：该屏幕位置下的世界点变换后仍在这里。"""
    new_scale = clamp_scale(vp.scale * factor)
    anchor_world = screen_to_world(vp, screen_pos)
    return Viewport(new_scale, (screen_pos[0] - anchor_world[0] * new_scale,
                                screen_pos[1] - anchor_world[1] * new_scale))
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_camera.py -v`
Expected: PASS（13 个：计划 5 个 + 逆变换、不修改入参、factor=1 恒等、钳制边界等补充用例）

- [ ] **Step 5: 提交（需用户批准）**

```bash
git add app/camera.py tests/test_camera.py
git commit -m "feat(camera): 视口变换、平移与锚点缩放"
```

---

### Task 4: 撤销栈（`app/history.py`）

**Files:**
- Create: `app/history.py`
- Create: `tests/test_history.py`

**Interfaces:**
- Consumes: 无（`Command` 只按协议调用 `apply()`/`unapply()`）
- Produces: `History(limit=100)`，方法 `push(cmd)`、`pop_undo()`、`pop_redo()`，属性 `can_undo`、`can_redo`、`depth`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_history.py
from app.history import History

class FakeCmd:
    def __init__(self):
        self.applied = self.unapplied = 0
    def apply(self): self.applied += 1
    def unapply(self): self.unapplied += 1

def test_push_then_undo_calls_unapply_once():
    h = History(limit=100)
    c = FakeCmd(); c.apply(); h.push(c)
    got = h.pop_undo()
    assert got is c
    assert c.unapplied == 1
    assert h.can_undo is False

def test_empty_stack_pop_returns_none_without_raising():
    h = History()
    assert h.pop_undo() is None
    assert h.pop_redo() is None

def test_new_action_clears_redo_branch():
    h = History()
    a, b, c = FakeCmd(), FakeCmd(), FakeCmd()
    for x in (a, b):
        x.apply(); h.push(x)
    h.pop_undo()                 # b 进 redo
    c.apply(); h.push(c)         # 新动作，redo 分支作废
    assert h.can_redo is False

def test_limit_drops_oldest_and_depth_stays_at_limit():
    h = History(limit=3)
    cmds = [FakeCmd() for _ in range(5)]
    for c in cmds:
        c.apply(); h.push(c)
    assert h.depth == 3
    assert h.pop_undo() is cmds[4]
    assert h.pop_undo() is cmds[3]
    assert h.pop_undo() is cmds[2]   # cmds[0], cmds[1] 已被丢
    assert h.can_undo is False

def test_undo_returns_commands_in_reverse_order():
    h = History(limit=10)
    cmds = [FakeCmd() for _ in range(4)]
    for c in cmds:
        h.push(c)
    assert [h.pop_undo() for _ in range(4)][::-1] == cmds
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_history.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.history'`

- [ ] **Step 3: 实现**

```python
# app/history.py
"""撤销/重做栈。存命令对象本身（不复制笔迹），超上限丢最老的。"""
from __future__ import annotations

from typing import Protocol


class Command(Protocol):
    def apply(self) -> None: ...
    def unapply(self) -> None: ...


class History:
    def __init__(self, limit: int = 100) -> None:
        self.limit = limit
        self._undo: list[Command] = []
        self._redo: list[Command] = []

    def push(self, cmd: Command) -> None:
        self._undo.append(cmd)
        if len(self._undo) > self.limit:
            del self._undo[:-self.limit]
        self._redo.clear()

    def pop_undo(self) -> Command | None:
        if not self._undo:
            return None
        cmd = self._undo.pop()
        cmd.unapply()
        self._redo.append(cmd)
        return cmd

    def pop_redo(self) -> Command | None:
        if not self._redo:
            return None
        cmd = self._redo.pop()
        cmd.apply()
        self._undo.append(cmd)
        return cmd

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def depth(self) -> int:
        return len(self._undo)
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_history.py -v`
Expected: PASS（5 个）

- [ ] **Step 5: 提交（需用户批准）**

```bash
git add app/history.py tests/test_history.py
git commit -m "feat(history): 撤销重做栈与深度上限"
```

---

### Task 5: 三种动作（`app/commands.py`）

**Files:**
- Create: `app/commands.py`
- Create: `tests/test_commands.py`

**Interfaces:**
- Consumes: Task 1 的 `Document`、`Stroke`
- Produces: `AddStroke(doc, stroke)`、`EraseStrokes(doc, strokes)`、`ClearAll(doc)`——都有 `apply()`/`unapply()`，直接改 `doc.strokes`

- [ ] **Step 1: 写失败测试（清屏后撤销必须逐条一致）**

```python
# tests/test_commands.py
from app.commands import AddStroke, ClearAll, EraseStrokes
from app.strokes import Document, Stroke

def _doc(n):
    return Document([Stroke([(float(i), 0.0)], f"#100b0{i:02x}", 3.0) for i in range(n)])

def test_add_then_undo_removes_exactly_that_stroke():
    d = _doc(0)
    s = Stroke([(1.0, 1.0)], "#000000", 6.0)
    AddStroke(d, s).apply()
    assert d.strokes == [s]
    AddStroke(d, s).unapply()      # 新对象但同值，验证按值移除
    assert d.strokes == []

def test_clear_all_then_undo_restores_identical_list_in_order():
    d = _doc(300)
    before = list(d.strokes)
    cmd = ClearAll(d)
    cmd.apply()
    assert d.strokes == []
    cmd.unapply()
    assert d.strokes == before          # 条数、顺序、每个字段都一致

def test_clear_all_undo_after_further_edits():
    d = _doc(3)
    ClearAll(d).apply()
    extra = Stroke([(9.0, 9.0)], "#ff0000", 1.5)
    AddStroke(d, extra).apply()
    assert [s.color for s in d.strokes] == ["#ff0000"]

def test_erase_strokes_records_indices_and_restores_them():
    d = _doc(5)
    targets = [d.strokes[1], d.strokes[3]]
    cmd = EraseStrokes(d, targets)
    cmd.apply()
    assert [s.color for s in d.strokes] == ["#100b00", "#100b02", "#100b04"]
    cmd.unapply()
    assert d.strokes == list(_doc(5).strokes)

def test_erase_ignores_strokes_not_in_document():
    d = _doc(2)
    ghost = Stroke([(0.0, 0.0)], "#abcdef", 3.0)
    cmd = EraseStrokes(d, [ghost])
    cmd.apply()
    assert len(d.strokes) == 2
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_commands.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.commands'`

- [ ] **Step 3: 实现**

```python
# app/commands.py
"""可撤销动作：一次落笔、一次橡皮、一次清屏。unapply 负责放回原位。"""
from __future__ import annotations

from app.strokes import Document, Stroke


class AddStroke:
    def __init__(self, doc: Document, stroke: Stroke) -> None:
        self._doc = doc
        self._stroke = stroke

    def apply(self) -> None:
        self._doc.strokes.append(self._stroke)

    def unapply(self) -> None:
        self._doc.strokes.remove(self._stroke)


class EraseStrokes:
    """一次划过删掉的所有条 = 一个撤销单位（设计文档 §6.1）。"""

    def __init__(self, doc: Document, strokes: list[Stroke]) -> None:
        self._doc = doc
        self._removed: list[tuple[int, Stroke]] = []
        self._targets = list(strokes)

    def apply(self) -> None:
        self._removed = []
        for s in self._targets:
            if s in self._doc.strokes:
                self._removed.append((self._doc.strokes.index(s), s))
                self._doc.strokes.remove(s)
        self._removed.sort()

    def unapply(self) -> None:
        for index, s in self._removed:
            self._doc.strokes.insert(index, s)
        self._removed = []


class ClearAll:
    def __init__(self, doc: Document) -> None:
        self._doc = doc
        self._snapshot: list[Stroke] = []

    def apply(self) -> None:
        self._snapshot = list(self._doc.strokes)
        self._doc.strokes.clear()

    def unapply(self) -> None:
        self._doc.strokes[:] = self._snapshot
        self._snapshot = []
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_commands.py -v`
Expected: PASS（6 个）

- [ ] **Step 5: 提交（需用户批准）**

```bash
git add app/commands.py tests/test_commands.py
git commit -m "feat(commands): add/erase/clear 三种可撤销动作"
```

---

### Task 6: 原子写与备份退回（`app/storage.py`）

**Files:**
- Create: `app/storage.py`
- Create: `tests/test_storage.py`

**Interfaces:**
- Consumes: Task 2 的 `DraftError`（作为 `parse` 抛错的约定类型）
- Produces: `LoadResult(text, source, recovered_from)`、`save_atomic(text, path)`、`load_with_fallback(path, parse)`、`corrupt_name(path, stamp)`

- [ ] **Step 1: 写失败测试（用 tmp_path，绝不碰真实 data/）**

```python
# tests/test_storage.py
import json
import pytest
from app.strokes import DraftError
from app.storage import corrupt_name, load_with_fallback, save_atomic

def parse_ok(text):
    return json.loads(text)          # 只测文本有效性，不测 Document 结构

def test_save_writes_file_and_no_bak_on_first_write(tmp_path):
    p = tmp_path / "draft.json"
    save_atomic('{"a":1}', p)
    assert p.read_text(encoding="utf-8") == '{"a":1}'
    assert not (tmp_path / "draft.json.bak").exists()
    assert not (tmp_path / "draft.json.tmp").exists()

def test_second_save_moves_previous_into_bak(tmp_path):
    p = tmp_path / "draft.json"
    save_atomic("v1", p)
    save_atomic("v2", p)
    assert p.read_text(encoding="utf-8") == "v2"
    assert (tmp_path / "draft.json.bak").read_text(encoding="utf-8") == "v1"

def test_fallback_uses_bak_when_main_is_garbage(tmp_path):
    p = tmp_path / "draft.json"
    save_atomic("good", p)
    save_atomic("better", p)
    p.write_text("{broken", encoding="utf-8")
    res = load_with_fallback(p, parse_ok)
    assert (res.text, res.source, res.recovered_from) == ("better", "bak", "main")

def test_both_bad_gives_none_and_renames_main_with_stamp(tmp_path):
    p = tmp_path / "draft.json"
    save_atomic("{x", p)
    res = load_with_fallback(p, parse_ok, stamp="20261020T120000")
    assert (res.text, res.source) == (None, "none")
    assert (tmp_path / "draft.json.corrupt-20261020T120000").exists()
    assert not p.exists()

def test_missing_files_give_none(tmp_path):
    res = load_with_fallback(tmp_path / "draft.json", parse_ok)
    assert (res.text, res.source) == (None, "none")

def test_parse_raising_drafterror_is_treated_as_corrupt(tmp_path):
    def parse(text):
        raise DraftError("nope")
    p = tmp_path / "draft.json"
    save_atomic("whatever", p)
    assert load_with_fallback(p, parse).source == "none"

def test_save_atomic_leaves_no_tmp_on_write_failure(tmp_path, monkeypatch):
    p = tmp_path / "draft.json"
    save_atomic("keep", p)
    monkeypatch.setattr("os.replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        save_atomic("new", p)
    assert p.read_text(encoding="utf-8") == "keep"     # 旧文件没被动过
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_storage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.storage'`

- [ ] **Step 3: 实现**

```python
# app/storage.py
"""原子落盘 + 备份退回。设计文档 §8：任何一步失败都不动旧文件。"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class LoadResult:
    text: str | None
    source: str              # "main" | "bak" | "none"
    recovered_from: str | None = None


def corrupt_name(path: Path, stamp: str) -> Path:
    return path.with_name(f"{path.name}.corrupt-{stamp}")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def save_atomic(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    if path.exists():
        os.replace(path, path.with_name(f"{path.name}.bak"))
    try:
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def load_with_fallback(path: Path, parse: Callable[[str], object],
                       stamp: str | None = None) -> LoadResult:
    main = _read(path)
    if main is not None:
        try:
            parse(main)
            return LoadResult(main, "main")
        except Exception:
            pass
    bak = path.with_name(f"{path.name}.bak")
    bak_text = _read(bak)
    if bak_text is not None:
        try:
            parse(bak_text)
            return LoadResult(bak_text, "bak", "main")
        except Exception:
            pass
    if main is not None:
        path.rename(corrupt_name(path, stamp or time.strftime("%Y%m%dT%H%M%S")))
    return LoadResult(None, "none")
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_storage.py -v`
Expected: PASS（7 个）

- [ ] **Step 5: 提交（需用户批准）**

```bash
git add app/storage.py tests/test_storage.py
git commit -m "feat(storage): 原子写、bak 退回与损坏留证"
```

---

### Task 7: 配置读写（`app/settings.py`）

**Files:**
- Create: `app/settings.py`
- Create: `tests/test_settings.py`

**Interfaces:**
- Consumes: Task 6 的 `_read` 语义（自己实现同样的容错读，不依赖 storage）
- Produces: `DEFAULT_CLEAR_KEY="E"`、`Settings(clear_key, color, width_index, autosave_delay)`、`load(path) -> Settings`、`save(path, settings) -> None`、`RESERVED_KEYS`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_settings.py
from app.settings import DEFAULT_CLEAR_KEY, RESERVED_KEYS, Settings, load, save

def test_defaults_when_file_absent(tmp_path):
    s = load(tmp_path / "settings.json")
    assert (s.clear_key, s.color, s.width_index, s.autosave_delay) == (
        "E", "#1a1a1a", 1, 1.5)

def test_round_trip(tmp_path):
    p = tmp_path / "settings.json"
    save(p, Settings(clear_key="Delete", color="#2980b9", width_index=3, autosave_delay=2.5))
    assert load(p) == Settings("Delete", "#2980b9", 3, 2.5)

def test_illegal_clear_key_falls_back_silently_but_flags(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"clear_key": "", "color": "#27ae60"}', encoding="utf-8")
    s = load(p)
    assert s.clear_key == DEFAULT_CLEAR_KEY
    assert s.color == "#27ae60"          # 合法字段照常生效，不因一处坏全丢

def test_illegal_width_index_clamped(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"width_index": 99}', encoding="utf-8")
    assert load(p).width_index == 3       # 4 档：0..3

def test_reserved_keys_are_the_rejected_bindings():
    assert set(RESERVED_KEYS) == {"Ctrl+C", "Ctrl+V", "Ctrl+Z", "Ctrl+Y", "Ctrl+A"}
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.settings'`

- [ ] **Step 3: 实现**

```python
# app/settings.py
"""settings.json：坏字段各自回退默认，不整份丢弃。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CLEAR_KEY = "E"
DEFAULT_COLOR = "#1a1a1a"
WIDTHS = (1.5, 3.0, 6.0, 12.0)
RESERVED_KEYS = ("Ctrl+C", "Ctrl+V", "Ctrl+Z", "Ctrl+Y", "Ctrl+A")   # 设计文档 §6.3


@dataclass
class Settings:
    clear_key: str = DEFAULT_CLEAR_KEY
    color: str = DEFAULT_COLOR
    width_index: int = 1
    autosave_delay: float = 1.5


def _raw(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(path: Path) -> Settings:
    data = _raw(path)
    clear_key = str(data.get("clear_key", "")).strip()
    color = str(data.get("color", DEFAULT_COLOR))
    if not color.startswith("#") or len(color) != 7:
        color = DEFAULT_COLOR
    try:
        width_index = int(data.get("width_index", 1))
    except (TypeError, ValueError):
        width_index = 1
    width_index = max(0, min(len(WIDTHS) - 1, width_index))
    try:
        delay = float(data.get("autosave_delay", 1.5))
    except (TypeError, ValueError):
        delay = 1.5
    return Settings(clear_key or DEFAULT_CLEAR_KEY, color, width_index,
                    delay if 0.2 <= delay <= 10.0 else 1.5)


def save(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.__dict__, ensure_ascii=False, indent=2),
                    encoding="utf-8")
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_settings.py -v`
Expected: PASS（5 个）

- [ ] **Step 5: 提交（需用户批准）**

```bash
git add app/settings.py tests/test_settings.py
git commit -m "feat(settings): 配置读写与坏字段回退"
```

---

### Task 8: 便携环境装 PySide6 + 事件探针（阻塞项，需用户批准）

**Files:**
- Create: `app/probe.py`
- Create: `run-probe.bat`

**Interfaces:**
- Consumes: 无
- Produces: 一份真实设备的事件类型记录（打印到控制台），决定 Task 9 的落笔分支

- [x] **Step 1: 向用户确认安装** — 已批准（2026-10-20「装，提，按你建议的来，pyside6放在项目文件夹下」）。

- [x] **Step 2: 建 venv 并装包** — 已完成，位置改为项目内 `.venv`：

```bash
cd /e/SketchPad && python -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install PySide6 pytest
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -c "import PySide6;from PySide6 import QtCore;print(PySide6.__version__, QtCore.__version__)"
```
实测结果：`6.12.0 6.12.0`。清华源 403 会让 pip 报 `from versions: none`，那不等于「没有 wheel」，换 `-i https://pypi.org/simple` 再核（本次没遇到）。

- [ ] **Step 3: 写探针**

```python
# app/probe.py
"""一次性事件探针：用笔/鼠标在这个窗口里画几下，控制台会打出每类事件。
不参与主程序。目的：确认落笔走 QMouseEvent 还是 QTabletEvent，以及是否重复送达。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QMouseEvent, QTabletEvent
from PySide6.QtWidgets import QApplication, QWidget


class Probe(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SketchPad 事件探针（画完按 Esc 退出）")
        self.resize(800, 500)
        self.setMouseTracking(True)
        self.counts: dict[str, int] = {}

    def _log(self, tag: str, ev) -> None:
        extra = ""
        if isinstance(ev, QTabletEvent):
            extra = (f" device={ev.deviceType()} pressure={ev.pressure():.3f}"
                     f" uniqueId={ev.uniqueId()}")
        pos: QPoint = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
        self.counts[tag] = self.counts.get(tag, 0) + 1
        print(f"{tag:22s} at=({pos.x()},{pos.y()})"
              f" buttons={int(ev.buttons())}{extra}")

    def mousePressEvent(self, ev): self._log("mousePress", ev)
    def mouseMoveEvent(self, ev): self._log("mouseMove", ev)
    def mouseReleaseEvent(self, ev): self._log("mouseRelease", ev)
    def tabletEvent(self, ev):
        kind = {Qt.TabletPress: "tabletPress", Qt.TabletMove: "tabletMove",
                Qt.TabletRelease: "tabletRelease", Qt.TabletProximity: "tabletProximity"
                }.get(ev.type(), "tabletOther")
        self._log(kind, ev)
        ev.ignore()      # 交回 Qt 生成对应的 mouse 事件，观察是否重复

    def wheelEvent(self, ev):
        self._log("wheel", ev)
        print(f"{'':22s} angleDelta={ev.angleDelta().y()} pixelDelta={ev.pixelDelta()}")

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            print("---- 计数汇总 ----")
            for k, v in sorted(self.counts.items()):
                print(f"{k:22s} {v}")
            QApplication.quit()


if __name__ == "__main__":
    app = QApplication([])
    # 必须留住引用：直接 Probe().show() 会让 Python 包装对象被回收，
    # 进程活着但窗口消失（实测 hwnd=0，看起来像点了没反应）
    probe = Probe()
    probe.show()
    app.exec()
```

- [ ] **Step 4: 写 run-probe.bat**

```bat
@echo off
"%~dp0.venv\Scripts\python.exe" "%~dp0app\probe.py"
pause
```

- [x] **Step 5: 让用户跑一次并把控制台原始输出贴回来** — 已完成（2026-10-20 实测，日志 165 KB / 3493 条事件）：

判据结果：
1. 落笔只有 `mousePress/mouseMove/mouseRelease`，`QTabletEvent` 计数为 0 → 走鼠标分支。
2. 一次拖动只送达一遍：15 次按下对 15 次抬起，无重复、无粘连。
3. 悬空不接触时出现大量**无按键的 `mouseMove`**（3493 条里绝大多数）→ 起笔必须判 `buttons() == LeftButton`。
   另测：滚轮 `angleDelta = ±120`/格、`pixelDelta = 0`；用户复测 9 笔 9 对按下/抬起，笔迹跟随与连续性经用户确认"没问题"。
   探针两个坑（已在代码与本节修正）：窗口对象必须留引用（否则 GC 后进程活着但窗口消失）；Qt6 枚举不可 `int()`。

判据（三条，能答是/否/数）：
1. 用笔接触板面拖动时，出现的是 `mousePress+mouseMove+mouseRelease`，还是 `tabletPress+tabletMove+tabletRelease`？
2. 同一次拖动里 `tabletMove` 与 `mouseMove` 的**数量比**是否为 1:1（1:1 以外都要在 Task 9 里显式去重）。
3. 笔悬空不接触时是否出现 `tabletProximity`/`tabletMove`（出现即证明「不能用 proximity 起笔」这条约束必要）。

- [ ] **Step 6: 提交（需用户批准）**

```bash
git add app/probe.py run-probe.bat
git commit -m "chore(probe): 数位板事件探针"
```

---

### Task 9: 画布视图（`app/canvas.py`）

**Files:**
- Create: `app/canvas.py`
- Create: `tests/test_canvas_offscreen.py`

**Interfaces:**
- Consumes: Task 1–7 全部；Task 8 的事件分支结论
- Produces: `SketchScene(QGraphicsScene)`（`item_for(stroke)`/`add_stroke(stroke)`/`remove_stroke(stroke)`）、`SketchView(QGraphicsView)`：`set_tool(str)`、`set_color(str)`、`set_width(float)`、`set_clear_key(str)`、`document`、`history`、信号 `changed`、`autosaveRequested`

- [ ] **Step 1: 写失败测试（离屏 Qt，不依赖真机）**

```python
# tests/test_canvas_offscreen.py
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from app.canvas import SketchScene, SketchView
from app.commands import ClearAll
from app.strokes import Document, Stroke

@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app

def _view(qapp):
    scene = SketchScene(Document())
    view = SketchView(scene)
    view.resize(400, 300)
    view.show()
    QTest.qWaitForWindowExposed(view)
    return view

def test_wheel_pan_keeps_scale(qapp):
    v = _view(qapp)
    scale_before = v.viewport_scale()
    y_before = v.viewport_origin()[1]
    v.pan_screen(0.0, 30.0)
    assert v.viewport_scale() == scale_before
    assert v.viewport_origin()[1] == pytest.approx(y_before + 30.0)

def test_ctrl_wheel_zooms_around_cursor(qapp):
    v = _view(qapp)
    world_before = v.screen_to_world(QPointF(200.0, 150.0))
    v.zoom_at_screen(200.0, 150.0, 1.08 ** 3)
    assert v.screen_to_world(QPointF(200.0, 150.0)) == pytest.approx(world_before, abs=1e-4)

def test_clear_key_binding_wipes_document_and_undo_restores(qapp):
    v = _view(qapp)
    v.scene.document.strokes = [Stroke([(1.0, 1.0)], "#000000", 3.0)] * 7
    v.clear_all()
    assert v.scene.document.strokes == []
    v.history.pop_undo()
    assert len(v.scene.document.strokes) == 7
    assert len([i for i in v.scene.items() if i.data(0) is not None]) == 7   # 视图与模型同步

def test_pen_drag_creates_one_stroke(qapp):
    v = _view(qapp)
    n_before = len(v.scene.document.strokes)
    QTest.mousePress(v.viewport(), Qt.LeftButton, pos=QPoint(50, 50))
    for x in (60, 80, 120, 170):
        QTest.mouseMove(v.viewport(), pos=QPoint(x, 60))
    QTest.mouseRelease(v.viewport(), Qt.LeftButton, pos=QPoint(170, 60))
    assert len(v.scene.document.strokes) == n_before + 1
    assert len(v.scene.document.strokes[-1].points) >= 2

def test_zero_size_viewport_changes_are_ignored(qapp):
    v = _view(qapp)
    scale_before, origin_before = v.viewport_scale(), v.viewport_origin()
    v.resize(0, 0)                       # 设计文档 §9：最小化时尺寸可为 0
    v.pan_screen(0.0, 40.0)
    v.zoom_at_screen(10.0, 10.0, 1.08)
    assert (v.viewport_scale(), v.viewport_origin()) == (scale_before, origin_before)

def test_max_points_per_stroke_is_enforced(qapp):
    v = _view(qapp)
    n_before = len(v.scene.document.strokes)
    QTest.mousePress(v.viewport(), Qt.LeftButton, pos=QPoint(10, 10))
    for i in range(6000):                # 全部远超抽稀阈值的点
        QTest.mouseMove(v.viewport(), pos=QPoint(10 + (i % 400) + 1, 10 + (i % 400) + 1))
    QTest.mouseRelease(v.viewport(), Qt.LeftButton, pos=QPoint(20, 20))
    assert len(v.scene.document.strokes[-1].points) <= 4000   # MAX_POINTS_PER_STROKE
    assert len(v.scene.document.strokes) == n_before + 1

def test_cursor_is_hidden_and_dot_follows_width(qapp):
    v = _view(qapp)
    assert v.viewport().cursor().shape() == Qt.BlankCursor      # R8：画布内无系统箭头
    v.set_tool("pen"); v.set_width(6.0)
    assert v.dot_radius_px() == pytest.approx(3.0)             # 直径 6×scale，scale=1
    v.set_width(1.5)
    assert v.dot_radius_px() == pytest.approx(2.0)             # 下限 4px 直径
    v.set_tool("eraser")
    assert v.dot_radius_px() == pytest.approx(12.0)            # 空心圆环固定 24px 直径
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_canvas_offscreen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.canvas'`

- [ ] **Step 3: 实现（要点，非全文）**

```python
# app/canvas.py 关键骨架
class SketchScene(QGraphicsScene):
    """模型 + item 索引。item.data(0) 存对应的 Stroke，删除时能找回。"""
    def __init__(self, document: Document) -> None: ...
    def add_stroke(self, stroke) -> QGraphicsPathItem: ...
    def remove_stroke(self, stroke) -> None: ...
    def rebind(self) -> None:      # ClearAll.unapply 之后重建全部 item


class SketchView(QGraphicsView):
    def __init__(self, scene, settings) -> None:
        self.history = History(100)
        ...
        self.setRenderHints(QPainter.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    # --- 输入（设计文档 §6.1）---
    def wheelEvent(self, ev):
        steps = ev.angleDelta().y() / 120.0
        if ev.modifiers() & Qt.ControlModifier:
            factor = WHEEL_ZOOM_STEP ** steps
            self.zoom_at_screen(ev.position().x(), ev.position().y(), factor)
        elif ev.modifiers() & Qt.ShiftModifier:
            self.pan_screen(-steps * self.width() * WHEEL_PAN_RATIO, 0.0)
        else:
            self.pan_screen(0.0, -steps * self.height() * WHEEL_PAN_RATIO)

    # --- 落笔：按 Task 8 实测的事件分支 ---
    def mousePressEvent(self, ev):   # 起笔条件：LeftButton；禁止用 proximity 起笔
    def mouseMoveEvent(self, ev):    # 世界坐标追加 → 超 4000 点停止并状态栏提示
    def mouseReleaseEvent(self, ev): # thin() 后成 Stroke → AddStroke(doc, s).apply() → history.push → autosaveRequested
    def drawForeground(self, painter, rect):  # 当前未固化笔迹用中点+quadTo 平滑绘制

    # --- 橡皮 ---
    def _erase_at(self, screen_pos):  # 命中 tol = max(width,6)/2 世界像素；一次按下划过的所有条 → 一个 EraseStrokes

    # --- 清屏（R3/R4）---
    def set_clear_key(self, key_sequence):   # 重建 QShortcut(WidgetWithChildrenShortcut, self)
    def clear_all(self):
        cmd = ClearAll(self.scene.document); cmd.apply(); self.history.push(cmd); self.scene.clear_view_items(); self.autosaveRequested.emit()
```

实现顺序内部约束：`viewport_scale/viewport_origin/screen_to_world/pan_screen/zoom_at_screen` 全部走 `app/camera.py` 的纯函数 + `self.setTransform(QTransform.fromScale(s,1,1).translate(...))`，视图里**不自己写缩放算术**（那样 §12 第 6 步的锚点测试就没有牙）。

对应上面两个边界测试，还须实现：① `self.viewport().width() <= 0 or height() <= 0` 时 `pan_screen/zoom_at_screen` 立刻 return，不改 `Viewport`（设计文档 §9 的「窗口尺寸 0」行）；② 单条笔迹累积点数达到 `MAX_POINTS_PER_STROKE = 4000` 后停止追加并保持当前笔可用，抬笔正常成条（设计文档 §5）。

光标（R8）实现：视图初始化 `self.viewport().setCursor(Qt.BlankCursor)`；`mouseMoveEvent` 里（不论按没按键）记录 `self._cursor_pos`；`drawForeground` 里按当前工具画——画笔：实心圆，半径 `dot_radius_px()`，颜色为当前色、80% 不透明度；橡皮：空心圆环，半径固定 12 像素（直径 24）。`dot_radius_px()` 返回屏幕像素半径：`max(2.0, self.width_px * self.viewport_scale() / 2)`，橡皮时恒为 12.0。`leaveEvent` 恢复 `Qt.ArrowCursor`，`enterEvent` 再设回 `Qt.BlankCursor`。

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_canvas_offscreen.py -v`
Expected: PASS（7 个）

- [ ] **Step 5: 提交（需用户批准）**

```bash
git add app/canvas.py tests/test_canvas_offscreen.py
git commit -m "feat(canvas): 画布视图、滚轮平移/缩放、落笔与清屏"
```

---

### Task 10: 工具栏（`app/toolbox.py`）

**Files:**
- Create: `app/toolbox.py`
- Create: `tests/test_toolbox_offscreen.py`

**Interfaces:**
- Consumes: Task 7 的 `Settings`、`WIDTHS`、`RESERVED_KEYS`
- Produces: `Toolbox(QWidget)`，信号 `colorPicked(str)`、`widthChosen(float)`、`toolChosen(str)`、`clearKeyCaptured(str)`、`savedRequested`；方法 `set_clear_key_label(str)`、`is_capturing() -> bool`

- [ ] **Step 1: 写失败测试（含 8 色与捕获态）**

```python
# tests/test_toolbox_offscreen.py
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.settings import WIDTHS
from app.toolbox import PALETTE, Toolbox

@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])

def test_palette_has_eight_colors_from_spec(qapp):
    assert PALETTE == ["#1a1a1a", "#c0392b", "#d35400", "#f1c40f",
                       "#27ae60", "#2980b9", "#8e44ad", "#7f8c8d"]

def test_width_buttons_emit_the_four_spec_widths(qapp):
    tb = Toolbox()
    got = []
    tb.widthChosen.connect(got.append)
    for b in tb.width_buttons:
        b.click()
    assert got == list(WIDTHS)          # 1.5, 3.0, 6.0, 12.0

def test_clear_key_button_enters_capture_and_ignores_reserved(qapp):
    tb = Toolbox()
    tb.clear_key_button.click()
    assert tb.is_capturing() is True
    tb._handle_capture_key("Ctrl", "Z")           # 保留键拒收
    assert tb.is_capturing() is True
    tb._handle_capture_key("", "Delete")
    assert tb.is_capturing() is False
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_toolbox_offscreen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.toolbox'`

- [ ] **Step 3: 实现要点**

- `PALETTE` 常量逐字用 Global Constraints 的 8 色。
- 布局一行：`[8 色块][取色器][细/中/粗/特粗][画笔][橡皮][清屏键：E][保存][缩放%][笔迹数]`。
- 捕获态：点击「清屏键」后 `grabKeyboard()` + `QTimer.singleShot(3000, cancel)`；`keyPressEvent` 里把 `(modifiers, key)` 转成 `QKeySequence` 文本；命中 `RESERVED_KEYS` → 状态提示「这个键已被占用」并留在捕获态；`Esc` → 取消并 `releaseKeyboard()`。
- **捕获结束必须 `releaseKeyboard()`**，否则键盘焦点被工具栏吃掉、画布再也收不到按键（这条进 §12 第 8 步）。

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/test_toolbox_offscreen.py -v`
Expected: PASS（3 个）

- [ ] **Step 5: 提交（需用户批准）**

```bash
git add app/toolbox.py tests/test_toolbox_offscreen.py
git commit -m "feat(toolbox): 颜色/粗细/工具/清屏键捕获"
```

---

### Task 11: 接线与自动保存（`main.py`）

**Files:**
- Create: `main.py`
- Create: `run.bat`, `requirements.txt`
- Modify: 无

**Interfaces:**
- Consumes: Task 1–10 全部
- Produces: 可运行的应用；`AppController`（持有 Document/History/Settings/定时器，供测试直接构造）

- [ ] **Step 1: 写失败测试（自动保存的防抖与落点）**

```python
# tests/test_autosave.py
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.settings import Settings
from app.strokes import Document, Stroke
from main import AppController

def make(tmp_path: Path) -> AppController:
    app = QApplication.instance() or QApplication([])
    return AppController(document=Document(), settings=Settings(autosave_delay=0.1),
                         data_dir=tmp_path, app=app)

def test_request_then_wait_writes_file(tmp_path):
    c = make(tmp_path)
    c.document.strokes.append(Stroke([(0.0, 0.0), (9.0, 0.0)], "#1a1a1a", 3.0))
    c.request_autosave()
    QTest.qWait(400)
    assert (tmp_path / "draft.json").exists()

def test_rapid_requests_coalesce_into_one_write(tmp_path, monkeypatch):
    c = make(tmp_path)
    calls = []
    monkeypatch.setattr("main.encode", lambda doc: calls.append(1) or "{}")
    for _ in range(20):
        c.request_autosave()
    QTest.qWait(400)
    assert len(calls) == 1

def test_startup_restores_previous_draft(tmp_path):
    c = make(tmp_path)
    c.document.strokes.append(Stroke([(1.0, 1.0)], "#2980b9", 6.0))
    c.flush_autosave()
    c2 = make(tmp_path)
    assert c2.document.strokes == c.document.strokes

def test_unwritable_data_dir_falls_back_to_appdata(tmp_path, monkeypatch):
    blocked = tmp_path / "ro"
    blocked.mkdir()
    monkeypatch.setattr("os.access", lambda *a, **k: False)
    c = make(blocked)
    assert str(c.draft_path) != str(blocked / "draft.json")   # 换到别处去了
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest tests/test_autosave.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'main'`（或 `ImportError: cannot import name 'AppController'`）

- [ ] **Step 3: 实现要点**

```python
# main.py
- try: import PySide6  except ImportError: print("pip install PySide6"); raise SystemExit(1)
- AppController(document, settings, data_dir, app)
    · draft_path = data_dir/"draft.json"；不可写时 → %APPDATA%/SketchPad/draft.json（设计文档 §8）
    · request_autosave(): QTimer.singleShot(int(settings.autosave_delay*1000), self._write)，
      用一个 pending 标志去抖（20 次请求只写 1 次）
    · flush_autosave(): 立即写（Ctrl+S、关窗前）
    · 启动：load_with_fallback(draft_path, strokes.decode) → 三个分支各给状态栏一句话
    · 快捷键：QShortcut(QKeySequence(settings.clear_key), view,
                context=Qt.WidgetWithChildrenShortcut)  → view.clear_all()
              Ctrl+Z/Ctrl+Y 同理，绑定后调用 view.after_history_change() 做 scene.rebind()
    · 关窗：flush_autosave() + settings 落盘
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest tests/ -v`
Expected: PASS（全部）

- [ ] **Step 5: 提交（需用户批准）**

```bash
git add main.py run.bat requirements.txt tests/test_autosave.py
git commit -m "feat(app): 接线、自动保存与启动恢复"
```

---

### Task 12: 真机验收（设计文档 §12 十二步）

**Files:**
- 无新增代码；产出验收记录 `docs/verification/2026-10-20-acceptance.md`

**Interfaces:**
- Consumes: Task 1–11 全部

- [ ] **Step 1: 逐条跑 §12 的 12 步，只收原始现象**

每条记录三列：`步骤 | 我给的动作 | 屏幕/文件的原始现象`。不写「正常」「符合预期」这种判定词。第 9 步（取色对话框里按 `e`）与第 12 步（悬笔不画线）必须截图或贴状态栏文字。

- [ ] **Step 1b: 量一次设计文档 §7 的性能线**

用一次性脚本往 `Document` 灌 1000 条 × 200 点（`python -c` 在 PowerShell 里会吞引号，写成 `tools/perf_fill.py` 再跑），保存后打开，连续滚轮平移 20 次、`Ctrl+滚轮` 缩放 20 次，记录状态栏 FPS 原始数值。
Expected（设计文档 §7 原文）：不出现持续 > 16ms 的单帧。达不到就按设计文档 §11 的两条降级手段做，**不许把 1000 条阈值改低**。`tools/perf_fill.py` 属于一次性工具，跑完保留（下次回归还要用），在验收记录里写明它的用法。

- [ ] **Step 2: 任一失败就停下报缺口，不进下一步**

已知的三个高危点：① 事件分支（Task 8 判据 2 若 1:1 之外）；② `ClearAll.unapply` 后 item 与模型不同步；③ 高 DPI 下笔与鼠标坐标错位（`run.bat` 里设 `QT_ENABLE_HIGHDPI_SCALING=1` 与 `QT_SCALE_FACTOR=1` 做对照）。

- [ ] **Step 3: 提交验收记录（需用户批准）**

```bash
git add docs/verification/2026-10-20-acceptance.md
git commit -m "docs: 真机验收记录"
```

---

### Task 13: 全局热键（`app/hotkey.py`）

**Files:**
- Create: `app/hotkey.py`
- Modify: `app/settings.py`（加 `scratch_hotkey`，默认 `"Ctrl+Alt+S"`）、`tests/test_settings.py`
- Create: `tests/test_hotkey.py`

**Interfaces:**
- Consumes: `Settings`
- Produces: `parse_hotkey(text) -> tuple[int, int]`（返回 Win32 `mods`, `vk`）、`GlobalHotkey(QObject)`：`register(seq) -> bool`、`unregister()`、信号 `activated`

- [ ] **Step 1: 写失败测试（纯函数 + 设置项）**

```python
# tests/test_hotkey.py
import pytest
from app.hotkey import MOD_ALT, MOD_CONTROL, MOD_SHIFT, parse_hotkey

def test_ctrl_alt_s():
    assert parse_hotkey("Ctrl+Alt+S") == (MOD_CONTROL | MOD_ALT, 0x53)

def test_shift_and_letter_lowercase():
    assert parse_hotkey("shift+x") == (MOD_SHIFT, 0x58)

def test_special_keys_map_to_vk():
    assert parse_hotkey("Delete") == (0, 0x2E)
    assert parse_hotkey("F1") == (0, 0x70)
    assert parse_hotkey("E") == (0, 0x45)

def test_bad_input_raises_valueerror():
    for bad in ("", "Ctrl+", "Ctrl+Alt+NoSuchKey", "Ctrl+Ctrl+S"):
        with pytest.raises(ValueError):
            parse_hotkey(bad)
```

```python
# tests/test_settings.py 追加
def test_scratch_hotkey_default_and_round_trip(tmp_path):
    assert load(tmp_path / "s.json").scratch_hotkey == "Ctrl+Alt+S"
    p = tmp_path / "s.json"
    p.write_text('{"scratch_hotkey": "Ctrl+Alt+D"}', encoding="utf-8")
    assert load(p).scratch_hotkey == "Ctrl+Alt+D"
```

- [ ] **Step 2: 跑测试确认失败** — `... -m pytest tests/test_hotkey.py -v` → FAIL（无模块/无字段）
- [ ] **Step 3: 实现**

```python
# app/hotkey.py
"""Win32 全局热键。parse_hotkey 是纯函数；RegisterHotKey 部分只能在真 Windows 上验。"""
from __future__ import annotations

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000

_VK = {"DELETE": 0x2E, "ESC": 0x1B, "TAB": 0x09, "SPACE": 0x20,
       **{f"F{i}": 0x6F + i for i in range(1, 13)}}


def parse_hotkey(text: str) -> tuple[int, int]:
    parts = [p.strip().upper() for p in text.split("+")]
    if not parts or any(not p for p in parts):
        raise ValueError(f"bad hotkey: {text!r}")
    *mods_part, key = parts
    mods = 0
    seen = set()
    for m in mods_part:
        if m in seen:
            raise ValueError(f"duplicate modifier: {m}")
        seen.add(m)
        mods |= {"CTRL": MOD_CONTROL, "CONTROL": MOD_CONTROL, "ALT": MOD_ALT,
                 "SHIFT": MOD_SHIFT, "WIN": MOD_WIN}.get(m) or _raise(f"bad modifier: {m}")
    if key in _VK:
        vk = _VK[key]
    elif len(key) == 1 and (key.isascii() and key.isalnum()):
        vk = ord(key)
    else:
        raise ValueError(f"bad key: {key}")
    return mods, vk


def _raise(msg: str):
    raise ValueError(msg)


class GlobalHotkey(QObject):
    activated = Signal()
    # register()/unregister() 用 ctypes.windll.user32.RegisterHotKey(hwnd, id, mods|MOD_NOREPEAT, vk)
    # hwnd 取主窗 winId()；WM_HOTKEY 由 QAbstractNativeEventFilter 捕获后 emit activated
```

- [ ] **Step 4: 跑测试确认通过** — `... pytest tests/test_hotkey.py tests/test_settings.py -v`
- [ ] **Step 5: 真机验一次注册**（人工）：注册成功返回 True；用别的软件占住 `Ctrl+Alt+S` 再注册 → 返回 False 且状态栏提示
- [ ] **Step 6: 提交（需用户批准）**

---

### Task 14: 草稿纸弹窗与托盘（`app/scratch.py`）

**Files:**
- Create: `app/scratch.py`、`tests/test_scratch_offscreen.py`
- Modify: `main.py`（接线：热键 → 切换草稿窗；`closeEvent` 改隐藏；托盘菜单）

**Interfaces:**
- Consumes: `SketchView`、`SketchScene`、`Toolbox`、`Document`、`History`、`Settings`、`GlobalHotkey`
- Produces: `ScratchWindow(QWidget)`（属性 `view`）、`ScratchController(QObject)`：`toggle()`、`is_visible()`、`show_near_cursor()`；`TrayIcon(QSystemTrayIcon)` 菜单三项

- [ ] **Step 1: 写失败测试（离屏）**

```python
def test_toggle_shows_then_hides_and_keeps_content(qapp):
    c = ScratchController(settings=Settings())
    c.toggle()
    assert c.is_visible() is True
    c.view.scene.document.strokes.append(Stroke([(1.0, 1.0)], "#1a1a1a", 3.0))
    c.toggle()
    assert c.is_visible() is False
    c.toggle()
    assert len(c.view.scene.document.strokes) == 1        # 收起不清空

def test_scratch_has_own_document_and_never_saves(qapp, tmp_path):
    c = ScratchController(settings=Settings())
    assert c.view.document is not c.main_document
    c.request_save()                                       # 草稿窗没有落盘路径
    assert list(tmp_path.glob("*")) == []

def test_esc_hides(qapp):
    c = ScratchController(settings=Settings()); c.toggle()
    QTest.keyClick(c.view, Qt.Key_Escape)
    assert c.is_visible() is False
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**：`ScratchWindow` 用 `QVBoxLayout(Toolbox(compact=True), SketchView)`，置顶；`ScratchController` 懒创建、位置钳在 `screen.availableGeometry()` 内；托盘用 `QSystemTrayIcon`（菜单：显示主窗 / 新建草稿 / 退出），`QApplication.setQuitOnLastWindowClosed(False)`。
- [ ] **Step 4: 跑测试确认通过** + 全量
- [ ] **Step 5: 提交（需用户批准）**

---

### Task 15: 草稿纸真机验收

- [ ] 把设计文档 §15 的三条验收写进 `docs/verification/2026-10-20-acceptance.md`，逐条记原始现象：
  1. 在 Xournal++ 里按 `Ctrl+Alt+S` → 草稿窗置顶出现；再按隐藏；再按内容还在。
  2. 草稿窗画一笔 → 主草稿 `draft.json` 的修改时间不变。
  3. 关主窗 → 托盘还在、热键可用；托盘「退出」→ 进程结束、热键失效。

---

## 并行分派建议（给编排用）

互不依赖、可并行：**Task 1 → 之后 Task 2/3/6/7 可并行**（都只依赖 Task 1 的类型；Task 6 需要 Task 2 的 `DraftError` 名字，先占位实现即可）、**Task 4/5 可并行**（Task 5 依赖 Task 1）。
必须串行：Task 8（装包，需批准）→ Task 9 → Task 10 → Task 11 → Task 12；之后 R9 草稿纸：Task 13（热键）→ Task 14（弹窗+托盘）→ Task 15（真机验收）。
文件冲突：Task 1 与 Task 2 改同一个 `app/strokes.py`，**不许并行**。
