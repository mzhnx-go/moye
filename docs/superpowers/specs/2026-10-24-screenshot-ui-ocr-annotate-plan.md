# 截图工具栏/蓝边/OCR 异步 + 批注光标/全屏穿透 方案

> 日期：2026-10-24
> 状态：**已实施**（白色图标工具栏 + 蓝色选区边距 + OCR 后台线程 + 批注光标/穿透提顶）
> 范围：①选区蓝色边距；②截图工具栏改成白底黑图标 + 悬浮提示；③OCR 移出 UI 线程；④全屏批注光标统一放大、其他软件全屏时穿透模式字迹可见性。

## 0. 实施结果

- 新增 `sketchpad/screenshot/icons.py`、`ocr_worker.py`；重写 `toolbar.py`；`overlay.py` 补蓝边与异步 OCR；`annotate/canvas.py` 统一光标；`annotate/overlay.py` 补提顶巡检与独占全屏提示。
- 测试：全量 **1374 passed / 6 skipped**（1380 collected；skip 为既有 offscreen 关闭确认项）。
- 真机：副屏 150% 预览白色图标工具栏（848×36 逻辑像素）与蓝色选区边距；真实 RapidOCR 调用 `run_ocr()` **1ms 返回**、约 **1.98s** 后台完成，遮罩未冻结。
- 独占全屏（D3D/演示模式）普通 TOPMOST 无法叠加，程序会提示一次；窗口化/无边框全屏靠每 250ms 重新提顶保持字迹可见。

## 1. 选区蓝色边距

- 选区仍保留白色内框；新增 **#0a84ff 蓝色外框**：距选区 2 逻辑像素，线宽 2 逻辑像素。
- 8 个手柄改为白底 + 蓝色描边，保证在浅色/深色截图上都可见。
- 尺寸标签改为白底 + 蓝边 + 黑字，和工具栏同一视觉语言。
- 实现位置：`sketchpad/screenshot/overlay.py` 的 `_draw_border` / `_draw_handles` / `_draw_size_label`。
- 压暗层仍在最底层，蓝框/手柄/标签在 foreground，不参与导出。

## 2. 截图工具栏：白底黑图标 + 悬浮提示

### 2.1 视觉

- 白色圆角条（`#ffffff`，1px `#c7c9cf` 边框），黑色图标（`#1c1c1e`），选中工具用浅蓝底 `#dbeafe` + 蓝色下边/描边，不再用整块深蓝。
- 悬停浅灰 `#f2f3f5`；禁用 35% 透明；所有按钮 `NoFocus`，不抢画布键盘。
- 高度约 40 逻辑像素（比现在两行文字条更紧凑），宽度按内容自适应，仍跟随选区下方/上方。
- 尺寸标签放在工具栏右侧，黑字；OCR 忙时在同一区域显示“识别中…”。

### 2.2 图标清单（全部 QPainter 现画，无外部资源）

| 工具 | 图标 | 提示（悬浮） |
|---|---|---|
| 指针 | 箭头 | 指针 (V)：选择/移动标注；拖选区空白移动选区 |
| 矩形 | 方框 | 矩形 (R)：拖拽画矩形 |
| 椭圆 | 圆 | 椭圆 (O) |
| 箭头 | 斜箭头 | 箭头 (A) |
| 画笔 | 笔身 | 画笔 (P) |
| 马克笔 | 粗笔/半透明 | 马克笔 (M)：半透明 |
| 文字 | T | 文字 (T)：点击输入；Ctrl+Enter 结束 |
| 序号 | ① | 序号 (N)：点击生成 1、2、3… |
| 马赛克 | 棋盘格 | 马赛克 (G)：框选打码 |
| 模糊 | 柔化圆 | 模糊 (B)：框选模糊 |
| 裁剪 | 裁切角 | 裁剪 (C)：把当前选区变成新底图（可撤销） |
| 颜色 | 6 个圆点 | 红/黄/绿/蓝/黑/白 + HEX |
| 粗细 | 三个大小不同的实心点 | 细 3px / 中 6px / 粗 10px |
| 撤销/重做 | 弯箭头 | Ctrl+Z / Ctrl+Y |
| 复制 | 两张叠纸 | 复制（Enter / Ctrl+C） |
| 保存 | 软盘 | 保存（Ctrl+S） |
| 贴图 | 图钉 | 钉在屏幕上 |
| OCR | 扫描框+字 | 识别图中文字（仅标准版） |
| 关闭 | × | 关闭（Esc，不复制） |

- 实现：新增 `sketchpad/screenshot/icons.py`（`@lru_cache`，24×24 逻辑坐标、2 倍绘制）；重写 `toolbar.py` 为单行/必要时两行自适应布局，保留现有信号名和 `_tool_buttons` / `_width_buttons` / `set_undo_enabled` 等测试接口。
- 悬浮提示沿用应用级 1 秒 tooltip 控制器；工具栏自带 QToolTip 深色样式改为白底黑字，避免在白色条上突兀。
- 颜色按钮：圆形色块图标 + 选中蓝圈；白色色块加灰边。
- 宽度按钮：实心圆点图标（直径 6/10/14），选中蓝圈。

## 3. OCR 移出 UI 线程

- 新增 `sketchpad/screenshot/ocr_worker.py`：`OcrWorker(QThread)`，信号 `done(str)` / `failed(str)`；`run()` 里调用注入的 `ocr_func(image)`。
- `ScreenshotOverlay.run_ocr()`：
  1. 在 UI 线程只做 `render_region`（快），把图 `.copy()` 后交给 worker；
  2. 立即返回，工具栏 OCR 按钮禁用并显示“识别中…”；
  3. worker 完成后回 UI 线程弹 `OCRResultDialog`；失败弹警告；恢复按钮；
  4. 同一时间只允许一个 OCR；遮罩关闭后结果不再弹窗（bound signal 自动断开）。
- 线程生命周期：worker 无 parent，保存在模块级 `_LIVE_WORKERS` 集合直到 `finished`，避免“QThread destroyed while running”；OCR 引擎不放到 UI 线程初始化。
- `recognize()` 保持可用；OCR 按钮仍只在 Standard 显示。
- 验收：点 OCR 后遮罩仍可移动/画图/取消；识别完成才弹结果；大图不冻 UI。

## 4. 全屏批注：光标与穿透可见性

### 4.1 光标统一放大

- `sketchpad/annotate/canvas.py`：新增 `CURSOR_RADIUS = 10.0`（直径 20 逻辑像素，原为线宽/2，4px 笔只有 2px 半径）。
- `_cursor_radius()` 对钢笔/任何粗细统一返回 `CURSOR_RADIUS`；橡皮环从 12 调到 14。
- 只改光标指示圆点，不改实际笔迹线宽（线宽仍按用户档位）。
- 验收：1px 与 10px 线宽的笔尖圆点一样大且明显；橡皮环更大。

### 4.2 其他软件全屏时穿透模式显示字迹

- 现状：覆盖层是 TOPMOST，但另一个刚被激活的全屏/置顶窗口可能压到它上面；`_self_heal` 只重新提顶工具栏，没有重新提顶覆盖层。
- 改动 `sketchpad/annotate/overlay.py`：
  1. 在 `_self_heal` 的可见巡检里调用 `_reassert_overlay_topmost()`：`SetWindowPos(HWND_TOPMOST, SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE|SWP_NOOWNERZORDER)`；每 250ms 一次，成本低且不抢焦点。
  2. 检测 **独占全屏**：`shell32.SHQueryUserNotificationState` 返回 `QUNS_RUNNING_D3D_FULL_SCREEN` 时，普通置顶窗口无法叠加；通过 `trayMessage` 只提示一次“当前程序为独占全屏，批注层无法覆盖；请切换为无边框/窗口全屏”。退出独占状态后重置提示。
  3. 不改变现有 `WS_EX_TRANSPARENT` 穿透样式、`NOACTIVATE` 和前台恢复逻辑；只补“重新提顶”和“诚实告警”。
- 验收：
  - 窗口化/无边框全屏视频或浏览器全屏：穿透模式下笔迹仍显示；
  - 独占全屏（典型 3D 游戏）：给出明确提示，不假装能覆盖；
  - 不抢前台焦点，不影响 `Alt+1..4` 和画布输入。

## 5. 实施顺序与验收

1. **方案/图标基础**：`icons.py` + 工具栏重排 → 离屏截图预览检查。
2. **蓝边/手柄/尺寸标签**：overlay 前景绘制；复制导出确认不含前景。
3. **OCR worker**：注入假 OCR 的异步测试（调用立即返回、后台完成后弹结果）。
4. **批注光标**：`CURSOR_RADIUS=10`；单测 1px/10px 半径一致。
5. **穿透提顶/独占全屏提示**：Windows 真机验证；无 Win32 时安全 no-op。
6. **回归**：截图/批注分组测试 + 全量（排除既有 6 项关闭确认）。
7. **真机**：副屏 150% 截图工具栏图标/蓝边；全屏视频/浏览器验证穿透字迹。

## 6. 风险

- 白色工具栏在纯白截图上边界依赖 1px 灰边和轻微阴影；加 `QGraphicsDropShadowEffect`（小半径）保证可见，但不要在透明层上滥用。
- 每 250ms SetWindowPos 可能与某些全屏程序互相抢 z 序；用 `SWP_NOACTIVATE`，只在可见时执行；独占全屏直接提示不硬顶。
- OCR worker 退出/遮罩关闭时线程仍在跑：只丢结果，不等待，避免关窗卡住。
- 图标为 24×24 线性图；高 DPI 用 2 倍绘制，避免发虚。
