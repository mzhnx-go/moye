# 全屏批注：去色/ Esc / 色盘 / 数字键 / 笔尖大小 方案

> 日期：2026-10-24
> 状态：**已实施**（主工具栏去蓝、Esc 不收起、9 色盘、1–6 换色、笔尖小/大切换）
> 涉及：主工具栏「全屏批注」按钮、`sketchpad/annotate` 的 canvas / toolbar / overlay / theme。

## 0. 实施结果

- `AnnotateButton` checked 改中性灰底+灰边；截图 `CaptureButton::menu-button` 下拉区不再刷系统高亮蓝；Esc 只收手势不隐藏；色盘 9 色与主软件同序（红蓝在前）；画布 1–6 换前六色；笔尖移动/书写 8px、停 **2.5s** 回到 20px，橡皮环 28px 不变。
- 测试：全量 **1386 passed / 6 skipped**（1392 collected）。
- 新增测试：色盘顺序/数量、数字键换色、光标小/大与书写中保持小、Esc 不隐藏、批注按钮 checked 不含 ACCENT。

## 1. 去掉主工具栏按钮的蓝色选中态

- 现象①：主工具栏「全屏批注」按钮开启时整块变蓝（`QToolButton:checked → ACCENT`）。
- 现象②：主工具栏「截图」按钮右侧下拉菜单区会按系统高亮色刷蓝。
- 改动：
  - `sketchpad/ui/toolbox.py` 给批注按钮设 `objectName="AnnotateButton"`、截图按钮设 `objectName="CaptureButton"`；
  - `sketchpad/ui/theme.py` 增加专属规则：批注 checked 用中性灰底 + 灰边；截图 `::menu-button` 常态透明、hover/pressed 中性灰，不再用蓝色；
  - 不改其它按钮的蓝色选中态（截图工具条仍保留浅蓝选中）。
- 验收：开/关批注、打开截图下拉菜单时都不出现蓝色。

## 2. Esc 不再退出批注模式

- 现象：`Canvas.keyPressEvent` 发 `escapePressed` → `OverlayWindow.hide_overlay()`，误按就整层收起。
- 改动：
  - `overlay._wire`：`escapePressed` 改接 `_on_escape`；
  - `_on_escape` 只在正在画/擦时收掉当前手势（`canvas.abort_gesture()`），**绝不 hide**；
  - 收起入口保留：工具栏「收起」按钮、`Ctrl+Alt+H`、`Ctrl+Alt+D`；
  - 工具栏「收起」tooltip 从“收起 (Esc)”改为“收起（Ctrl+Alt+H）”。
- 验收：批注/穿透模式下按 Esc，模式不变、笔迹不丢；收起按钮和热键仍可隐藏。

## 3. 色盘顺序与主软件一致，红蓝在前

- 主软件色盘（`sketchpad/ui/toolbox.PALETTE`）：
  `红 #ff453a → 蓝 #2980b9 → 黄 #f1c40f → 白 #e8eaed → 橙 #d35400 → 深黄 #d4a017 → 绿 #27ae60 → 紫 #8e44ad → 灰 #7f8c8d`
- 改动：
  - `sketchpad/annotate/constants.py` 的 `PALETTE` 改为同一顺序（9 色），并加一份名称表；
  - 批注工具栏按此顺序生成 9 个色块（原来 8 个、白色在第一位）；
  - 色块 tooltip 带上序号和名称（如 `1 · 红 #ff453a`）；
  - 增加测试比对两处色盘顺序完全一致，防止以后漂移。
- 验收：批注工具栏前两个色块是红、蓝；9 个颜色与主软件逐一相同。

## 4. 画笔下 1–6 对应颜色

- `Canvas.keyPressEvent` 增加 `1..6`：
  - 1 红、2 蓝、3 黄、4 白、5 橙、6 深黄；
  - 调 `canvas.set_color(PALETTE[n-1])`，`changed` 触发 overlay 倒映色块与当前色圆点；
  - 不要求按 B 才能用：橡皮状态下按数字只换颜色，切回画笔即生效；
  - 只在画布有焦点时有效（穿透模式按数字仍归底层软件，保持穿透语义）。
- 验收：画布焦点下按 1–6，当前色与工具栏勾选同步；B/Q/E、Ctrl+Z 不受影响。

## 5. 笔尖圆点：写的时候小，空闲后变大

- 现状：统一大圆点（半径 10）一直显示，写字时盖住落笔。
- 目标：
  - **移动/落笔/书写期间**：小圆点（半径 **4**，直径 8）；
  - **停止操作 2500ms 后**：恢复大圆点（半径 **10**，直径 20）；
  - 橡皮环保持 14（不参与此规则）；
  - 正在画 (`_drawing`) 时即使计时器到点也保持小圆点。
- 实现（`sketchpad/annotate/canvas.py`）：
  - 常量 `CURSOR_SMALL_RADIUS=4.0`、`CURSOR_LARGE_RADIUS=10.0`、`CURSOR_IDLE_MS=2500`；
  - `_cursor_large` 状态 + 单次 `QTimer`；
  - 鼠标 **press/release** 调 `_mark_cursor_active()`：置小、重启计时；
  - **悬停 move 不重置计时**（数位板悬停会连续发 move，否则大点永远不出现）；只更新位置/重绘；
  - 计时到点 `_set_cursor_large(True)`，按大半径重绘光标脏矩形；
  - `_cursor_radius()`：橡皮→14；正在画→4；空闲→10；其它→4；
  - 大小切换时用大半径的并集刷一次，避免旧圆点残留。
- **停止操作 2.5s 后**恢复大点；正在画保持小点。
- 验收：写字时圆点明显变小、不挡笔迹；停笔 **2.5 秒**后变回大圆点；1px 与 10px 线宽的指示点行为一致。

## 6. 测试

- `tests/annotate/test_canvas.py`：
  - 1–6 选色（色值与 PALETTE 前 6 项一致）；
  - 光标：初始大、move 后小、qWait 后大、drawing 中保持小；
  - Esc 信号仍发（兼容），但 overlay 不再因此隐藏。
- `tests/annotate/test_overlay.py`：
  - `canvas.escapePressed` 后 `mode` 不变；
  - 收起按钮仍能 hide。
- `tests/annotate/test_toolbar.py`：
  - 色块数量 9、顺序与主软件一致、每个色块发对应颜色、显示序号 tooltip。
- `tests/test_toolbox_offscreen.py` / 主题测试：
  - 批注按钮 checked 样式不含 ACCENT 蓝。
- 真机：主工具栏开批注无蓝色；Esc 不收起；1–6 换色；停笔 1.2s 圆点变大。

## 7. 风险

- Esc 被彻底改成不退出后，用户必须知道收起入口：tooltip 与快捷键列表要同步更新。
- 色盘从 8 变 9 会让批注工具栏变宽约 20px；横排仍应能放进 1920/1366 屏，竖排高度增加一档。
- 光标计时器跟随 Canvas 生命周期；窗口隐藏时停止计时（`_cursor=None` 时 no-op），不产生后台重绘。
- 主工具栏专属 QSS 不能影响其它 checkable 按钮。
