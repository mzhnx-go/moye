# 方案：把全屏批注层并入 PageFlow（`E:\批注` → `sketchpad/annotate/`）

> 状态：**方案待确认，未动任何业务代码**。
> 本文是唯一交付物；分支 `annotate-embed` 上只提交这份文档，方便整体回退。
> 分支基线：`master@ab2db29`（2026-10-23 23:20 的 HEAD）。

---

## 0. 一句话

把 `E:\批注` 这个独立的全屏批注小工具，改成 PageFlow 的一个子功能：**同一个进程、同一个托盘、同一套热键**；
热键按"前台是不是本程序"决定走哪一边；顺带给批注工具栏加截图按钮（放在撤销后面）。

---

## 1. 目标拆成可验收项

| # | 需求原话 | 验收标准 |
|---|---|---|
| A | 批注功能嵌入 SketchPad，作为子功能，整合成一个软件 | 只启动一个 `PageFlow.exe`：只有 1 个托盘图标、1 个进程；批注层不再需要自己的 `run.bat` |
| B | 用 SketchPad 时优先它的快捷键，其他应用时用批注的 | 前台是 PageFlow 窗口 → 该键触发 PageFlow 的动作；前台是别的程序 → 触发全屏批注层。两者不会同时响应 |
| C | 加截图功能，按钮放在撤回键后面 | 工具栏出现截图按钮（清空 → 撤销 → **截图** → 收起）；点它框选屏幕区域 → 进剪贴板 → 提示已复制 |

---

## 2. 现状盘点

### 2.1 PageFlow（`E:\SketchPad`，分支 master）

| 关注点 | 位置 | 关键签名 / 事实 |
|---|---|---|
| 启动装配 | `sketchpad/app.py:3403 main()` | `QApplication` → `InstanceLock.acquire()` → `AppController(base/DATA_DIR)` → `MainWindow(controller)` → `SingleInstanceServer.listen()` → `show()` → `start_hotkey()` / `start_main_hotkey()` / `start_tray()` → `app.exec()` |
| 主窗口 | `sketchpad/app.py:182 class MainWindow(QMainWindow)` | 持有 `self.menubar`、`self.toolbox`、`self.hotkey`、`self.main_hotkey`、`self.tray`、`self.scratch` |
| 全局热键 | `sketchpad/services/hotkey.py` | `class GlobalHotkey(QObject)`，`register(winId, "Ctrl+Alt+S") -> bool`；内部在专属线程建 message-only 窗口发 `RegisterHotKey`，收到的键通过 Qt Signal 回主线程。**每个实例独立**，可再加第三个 |
| 已有两个热键 | `MainWindow.start_hotkey()` / `start_main_hotkey()`（app.py:3097/3101） | 草稿纸 `settings.scratch_hotkey`（默认 Alt+Q）、呼出主窗 `settings.main_hotkey`（默认 Alt+W）；失败回退 `QShortcut`（app.py:3033） |
| 托盘 | `sketchpad/ui/scratch.py:328 class TrayIcon` | 信号 `showMainRequested` / `newDraftRequested` / `quitRequested`；在 `MainWindow.start_tray()`（app.py:3105）装配 |
| 单实例 | `sketchpad/services/single_instance.py` | `InstanceLock` + `QLocalServer`；第二实例把 payload 交给 `window.handle_single_instance` |
| 设置 | `sketchpad/core/settings.py:43 @dataclass Settings` | 字段含 `scratch_hotkey` / `main_hotkey` / `close_to_tray` …；**此文件当前有未提交改动（他人正在改）** |
| 菜单栏 | `sketchpad/ui/menubar.py` | 自绘 `Menubar`。`_make_action(key=...)` 把 `triggered` 转成统一的 `actionTriggered(str)` 信号（menubar.py:66/86），MainWindow 在 `_on_menu_action` 分发 → **加菜单项成本很低** |
| 剪贴板图片 | `sketchpad/app.py:695`（Ctrl+V） | 优先取剪贴板位图 → `_insert_image_to_draft(image, "剪贴板图片")`；截图→剪贴板→粘贴进草稿的闭环已经存在，截图功能可以直接复用这条链路 |

### 2.2 批注工具（`E:\批注`，**不是 git 仓库**）

| 关注点 | 位置 | 事实 |
|---|---|---|
| 装配 | `annotation/app.py:238 main()` / `class AnnotationApp` | 自己的 `InstanceLock`、托盘、两个 `GlobalHotkey`（`Ctrl+Alt+D` / `Ctrl+Alt+H`）、`SingleInstanceServer` —— 与 PageFlow 是同一套东西的两份实现 |
| 覆盖层 | `annotation/overlay.py:OverlayWindow` | 全屏 `TOPMOST` + `WA_TranslucentBackground`；`mode` ∈ {hidden, draw, passthrough}；工具栏是独立顶层窗口 `Toolbar`（穿透层吃不到它） |
| 工具栏 | `annotation/toolbar.py:Toolbar` | 顺序：`grip / 穿透 钢笔 橡皮(三选一) / 粗细×4 / 色块×8 / 清空 / 撤销 / 收起 / swap`；尺寸 613×40 |
| 画布 | `annotation/canvas.py:Canvas` | 钢笔/橡皮、`pen_width`、撤销、自愈巡检（`abort_gesture`） |
| 配置 | `annotation/config.py` → `data/settings.json` | 6 个键：两个热键 + color + pen_width + toolbar_orientation + toolbar_pos |
| 依赖 | —— | 只依赖 PySide6，**不依赖 pymupdf**；包内自成一体（`annotation/*` 直接 import 自己） |

**结论**：两边是"同一个模式的两份实现"（单实例 + 托盘 + 全局热键 + 全屏层）。合并时删掉批注那份、复用 PageFlow 的，是改动最小、最不容易出双托盘/双热键竞争的路线。

---

## 3. 可行性判定

### 3.1 嵌入：可行，风险集中在"两套基础设施去重"

- 批注包自包含，搬目录即可，不存在循环依赖。
- 必须处理的重叠：单实例、托盘、全局热键、配置目录、退出流程。**不处理就会两个托盘图标、两组热键抢同一个键。**
- 唯一硬约束：批注层是**全屏 TOPMOST 窗口**，PageFlow 主窗是普通窗口。两者共存时 z 序要明确——批注层置顶，PageFlow 主窗在其下；用户点批注工具栏即可退出到"穿透"状态。

### 3.2 快捷键按前台路由：可行，但要先明确"哪些键算冲突"

先把两类键分清，这是整个需求里最容易做错的地方：

| 类型 | 例子 | 是否真的冲突 |
|---|---|---|
| **应用内快捷键**（窗口级） | `B` 画笔、`Q` 橡皮、`1`–`4` 粗细、`Ctrl+Z`、`E` 清屏 | **不冲突**。键盘事件只发给焦点窗口，谁有焦点谁响应，天然隔离 |
| **全局热键**（系统级 RegisterHotKey） | 草稿纸 `Alt+Q`、主窗 `Alt+W`、批注 `Ctrl+Alt+D` / `Ctrl+Alt+H` | 两组键名不同，**也不冲突**；只有"想让同一个组合键在两处做不同事"时才需要路由 |

所以"优先用 SketchPad 的快捷键"有两条实现路线：

- **路线 1（保守，零风险）**：两组全局热键保持独立，谁的键就干谁的事。应用内快捷键靠焦点天然隔离，不做任何改动。
- **路线 2（推荐，符合原话）**：只给 `Ctrl+Alt+D` 一个键做**前台路由**——
  ```
  按 Ctrl+Alt+D
    ├─ 前台窗口属于本进程 → PageFlow 内部进入批注模式（当前文档/草稿）
    └─ 前台是别的程序     → 呼出全屏批注层
  ```
  判定用一个纯函数（`sketchpad/services/hotkey.py` 已有 ctypes 基础设施）：
  ```python
  def foreground_belongs_to_us() -> bool:
      hwnd = user32.GetForegroundWindow()
      _, pid = user32.GetWindowThreadProcessId(hwnd, byref(DWORD()))
      return pid.value == os.getpid()
  ```
  注意：`GetForegroundWindow` 必须是**当下**取值，不能在热键回调里缓存；批注层自己在前台时要排除（否则会自己路由给自己）。

### 3.3 截图：可行，且与现有能力天然衔接

- 抓屏：`QScreen.grabWindow(0)`（PageFlow 已在多处用 `grabWindow` 思路，PySide6 自带，无新依赖）。
- 框选交互：新建一个全屏 `CaptureOverlay`（`Qt.Tool | FramelessWindowHint | StaysOnTop`），背景贴抓好的整屏位图并压暗，鼠标拖出选框、实时显示尺寸、`Esc` 取消、松开即裁剪。
- 产物：**默认复制到剪贴板**（`QGuiApplication.clipboard().setImage(...)`）。这一步就能闭环：截完直接 `Ctrl+V` 粘进 PageFlow 草稿（`app.py:695` 已支持）。
- 可选加强（不在本期）：把截图作为底图贴进批注画布，用于"截屏后直接在图上标注"。

---

## 4. 方案对比

| | A. 同进程内嵌（**推荐**） | B. 子进程 + IPC | C. 只复用窗口类 |
|---|---|---|---|
| 做法 | `annotation/` 搬成 `sketchpad/annotate/`，由 MainWindow 持有 | 保留独立 exe，PageFlow 用 QProcess 起停 | 只把 OverlayWindow/Toolbar 搬进来，重写装配 |
| 托盘 | 1 个 | 2 个（要合并托盘，很别扭） | 1 个 |
| 热键 | 统一在 MainWindow 注册，天然可路由 | 两进程各注册，**同键必抢**，只能靠路由规避 | 统一 |
| 配置 | 合并到一处（可独立文件） | 两套 | 合并 |
| 打包 | 一个包体 | 两个 exe | 一个包体 |
| 风险 | 需要重构批注的 app.py 装配（约 260 行） | 进程生命周期、崩溃互不影响但状态不同步 | 改动面最大（等于重写） |
| 结论 | ✅ 选它 | ❌ 与"整合成一个软件"直接矛盾 | ❌ 工作量最大、收益最低 |

---

## 5. 推荐方案（A）详设

### 5.1 目录

```
sketchpad/
  annotate/                  ← 新包，从 E:\批注\annotation\ 搬来
    __init__.py              ← 只导出 AnnotateController
    controller.py            ← 新写：替代原 annotation/app.py 的装配部分
    overlay.py               ← 原样搬（OverlayWindow）
    toolbar.py               ← 原样搬（+ 截图按钮）
    canvas.py                ← 原样搬
    icons.py / theme.py / constants.py / config.py
    capture.py               ← 新写：截图框选窗口 CaptureOverlay
    services/
      selfheal.py            ← 原 overlay 里的巡检拆出来（可选）
tests/
  annotate/                  ← 从 E:\批注\tests\ 搬来（换成 sketchpad.annotate.*）
docs/plans/2026-10-23-annotate-embed.md   ← 本文
```

**保留 `E:\批注` 不动**，作为对照实现；合并稳定后再决定是否归档。

### 5.2 装配改动（"两行挂载"原则）

协作者正在实时改 `app.py` / `scratch.py` / `toolbox.py` / `tabs.py`（见 §6），所以挂载点要薄到极致：
**除了 `app.py` 里加 2 行，其余全部在新包内部完成**，不改 `scratch.py`（托盘）、不改 `menubar.py`。

`main()`（app.py:3403）里，在 `window.start_tray()` 之后插两行：

```python
from sketchpad.annotate import install          # 顶部 import 区
...
    window.start_tray()
    window._annotate = install(window)          # ← 新增：一行挂载
```

`sketchpad/annotate/install.py`：

```python
def install(window) -> AnnotateController:
    """把全屏批注层挂到已建好的 MainWindow 上（不动 PageFlow 的任何模块）。

    选在 start_tray() 之后调用，是为了托盘已经存在——这样托盘菜单项可以在这里
    动态加，不必去改 sketchpad/ui/scratch.py（该文件正被他人修改）。
    """
    controller = AnnotateController(window)
    if window.tray is not None:
        menu = window.tray.contextMenu()
        action = menu.addAction("全屏批注")
        action.triggered.connect(controller.enter_draw)
    return controller
```

`AnnotateController` 的职责（替代原 `annotation/app.py`）：

```python
class AnnotateController(QObject):
    """把全屏批注层挂到 PageFlow 上：不建托盘、不建单实例、不自己收键。"""
    def __init__(self, main_window):
        self.window = main_window
        self.config = AnnotateConfig.load(DATA_DIR / "annotate.json")   # 独立配置
        self.overlay = OverlayWindow(self.config)                       # 原 OverlayWindow
        self.hotkey = GlobalHotkey(self)                                # 第三个实例
        self.hotkey.activated.connect(self.on_hotkey)

    def on_hotkey(self):        # 前台路由
        if foreground_belongs_to_us():
            self.window.enter_internal_annotate()      # 走 PageFlow 自己的批注
        else:
            self.overlay.enter_draw_mode()             # 走全屏层

    def enter_draw(self) / enter_passthrough(self) / hide(self)
    def shutdown(self):         # 只注销自己的热键、藏掉覆盖层
```

**要删掉的批注基础设施**（搬过来时直接不搬）：
- `annotation/services/single_instance.py`（PageFlow 已有）
- `annotation/app.py` 的 `AnnotationApp`、`_create_tray`、`_register_hotkeys`、`_hand_off_to_running`
- 批注自己的 `data/settings.json`（改成 `data/annotate.json`，见 5.3）

退出接线：`AnnotateController.shutdown()` 由 `install()` 返回对象持有；`MainWindow.quit_app` 里若不方便改，可让 controller 自己监听 `QApplication.aboutToQuit`——**同样不碰 app.py**。

### 5.3 配置隔离（关键，为了不与他人冲突）

`sketchpad/core/settings.py` **当前正被别人修改**。为了避免改动同一文件，批注配置**独立成 `data/annotate.json`**：

```json
{
  "toggle_hotkey": "Ctrl+Alt+D",
  "hide_hotkey": "Ctrl+Alt+H",
  "color": "#e8eaed",
  "pen_width": 4.0,
  "toolbar_orientation": "horizontal",
  "toolbar_pos": null
}
```

读写继续用 `annotation/config.py` 的逻辑（`Config.load/save`），只把路径换成 `DATA_DIR / "annotate.json"`。
**不动 `Settings` dataclass、不动 `settings_dialog.py`**，等到协作者的改动落地、合并稳定后，再考虑把这两个热键搬进设置对话框（那属于 P4 的收尾）。

### 5.4 热键路由

| 键 | 归属 | 前台是本程序 | 前台是其他程序 |
|---|---|---|---|
| `Ctrl+Alt+D` | 批注切换 | `window.enter_internal_annotate()` | `overlay.enter_draw_mode()` |
| `Ctrl+Alt+H` | 批注隐藏/显示 | 隐藏批注层 | 显示批注层 |
| `Alt+Q` | PageFlow 草稿纸 | 原行为（不改） | 原行为（不改） |
| `Alt+W` | 呼出主窗 | 原行为（不改） | 原行为（不改） |

实现要点：
- 三个 `GlobalHotkey` 实例共存没问题（各自的 message-only 窗口 + 独立 ID），但要确认 `HOTKEY_ID = 0xB0B1` 是**每个实例的消息窗口内唯一**即可——查 `hotkey.py` 的注册函数是否把 `HOTKEY_ID` 写死为常量：写死也没关系，因为它们在不同的消息窗口里。**（待确认，见 §9-1）**
- 路由函数只在批注热键里用；`Alt+Q/Alt+W` 完全不动，避免影响协作者的功能。
- 若 `Ctrl+Alt+D` 注册失败（被占用），回退用程序内 `QShortcut`，并在状态栏提示（沿用 `_register_scratch_hotkey` 的写法）。

### 5.5 截图功能（`sketchpad/annotate/capture.py`）

```python
class CaptureOverlay(QWidget):
    """全屏截图：先抓整屏做背景，再拖框选区；松开即复制到剪贴板。"""
    captured = Signal(QPixmap)      # 裁剪好的图

    def start(self):
        screen = QGuiApplication.primaryScreen()
        self._shot = screen.grabWindow(0)            # 抓的是"按下按钮之前"的屏幕
        self.setGeometry(screen.geometry())
        self.show(); self.raise_(); self.activateWindow()

    def paintEvent(self, ev):        # 画整屏位图 + 压暗 + 选区镂空 + 尺寸标注
    def mousePressEvent / mouseMoveEvent / mouseReleaseEvent
    def keyPressEvent(self, ev):     # Esc 取消
```

行为细节：
- **抓屏时机**：点按钮的**前一瞬**抓（`Toolbar` 发 `captureRequested` → `controller` 先 `overlay.hide()` → `QTimer.singleShot(80, capture.start)`），否则会把自己的工具栏也截进去。
- 选区太小（< 4×4）视为取消。
- 松开后：`captured` → `QGuiApplication.clipboard().setImage(pixmap.toImage())` → 状态提示「已复制到剪贴板」，并顺带把 `overlay` 恢复成原来的模式（穿透/批注）。
- 依赖：仅 `QScreen` / `QClipboard`，不加新包。

**工具栏顺序**（`toolbar.py::_build`）：
```
grip / 穿透 钢笔 橡皮 / 粗细×4 / 色块×8 / 清空 / 撤销 / 【截图】 / 收起 / swap
```
截图按钮图标：画一个"取景框 + 四角标记"（`icons.capture_icon()`），与现有纯 QPainter 图标同风格。

### 5.6 与 PageFlow 的交互细节

- 批注层进入 `draw` 模式才会 `activateWindow()` 抢焦点；`passthrough` 绝不抢（否则会打断 PageFlow 的 `B/Q/1-4`）。
- PageFlow 主窗被最小化/隐藏时，批注层仍可用（独立顶层窗口）。
- 退出：`MainWindow.quit_app` 里调用 `annotate.shutdown()`，避免热键残留。

---

## 6. 并行开发冲突面（重点）

`master` 当前有 **26 项未提交改动**，属于协作者正在做的 tabs / note-split / titlebar 功能。
**这个数字还在变**：写本文的 10 分钟内从 22 项涨到 26 项，`sketchpad/app.py`、`ui/scratch.py`、
`ui/toolbox.py`、`workspace/tabs.py` 都是期间新进入修改态的。

当前清单（2026-10-23 23:22 快照）：

```
 M docs/superpowers/specs/2026-10-23-tabs-note-split-redesign.md
 M sketchpad/app.py                    ← 挂载点所在的文件，已被他人改
 M sketchpad/core/history.py           M sketchpad/core/settings.py
 M sketchpad/ui/scratch.py             ← 托盘所在文件，已被他人改
 M sketchpad/ui/settings_dialog.py     M sketchpad/ui/titlebar.py
 M sketchpad/ui/toolbox.py             M sketchpad/workspace/session.py
 M sketchpad/workspace/tabs.py
 M tests/test_dialogs_offscreen.py     等 6 个测试
?? sketchpad/ui/empty_workspace.py     ?? sketchpad/workspace/{note_split,tabdrag,windows}.py
?? tests/test_{close_confirm_app,note_split_ui,tab_drag,titlebar_tabs,workspace_windows}.py
?? vibe_images/
```

**隔离策略（已执行）**：用 `git worktree` 建了独立工作区，基线是 `master@ab2db29` 的干净快照，
**完全不含那 26 项**：

```
E:\SketchPad            master              （协作者继续用，我不碰）
E:\SketchPad-annotate   annotate-embed      （我在这里开发）
```

**冲突面（按"两行挂载"原则收敛后）**：

| 文件 | 现状 | 我要做的 | 冲突风险 |
|---|---|---|---|
| `sketchpad/app.py` | 他人正在改 | 只加 1 个 import + 1 行 `install(window)` | **低**（单行、位置固定） |
| `sketchpad/ui/scratch.py` | 他人正在改 | **不改**（托盘项在 install() 里动态加） | 无 |
| `sketchpad/ui/menubar.py` | 干净 | **不改**（本期走托盘 + 热键） | 无 |
| `sketchpad/core/settings.py` | 他人正在改 | **不改**（配置独立 `data/annotate.json`） | 无 |
| `sketchpad/ui/settings_dialog.py` | 他人正在改 | **不改**（P4 再议） | 无 |
| `sketchpad/annotate/**` | 新增 | 整个批注包 | 无 |
| `tests/annotate/**` | 新增 | 批注测试 | 无 |

结论：**只需要在 `app.py` 里加 2 行**，其余全部落在新包里。合并时最多一处单行冲突。

**基线漂移的对策**（每个阶段开工前做一次）：
```bash
cd E:/SketchPad-annotate
git fetch . master:master        # 同仓，直接同步
git log --oneline master -3       # 看协作者有没有新提交
# 若 app.py 有新提交：rebuild 前先 rebase，冲突只在那一行
git rebase master
```
注意：协作者的改动**当前还没提交**（都在工作区里），所以 `annotate-embed` 分支上看到的 `app.py` 是
`ab2db29` 的版本；等他们提交后，我的两行插入点需要按新版本重新定位（`start_tray()` 调用处）。

---

## 7. 分阶段计划

每阶段结束都提交一次，任何一步出问题都能单独回退。

| 阶段 | 内容 | 验收 | 提交 |
|---|---|---|---|
| **P0（本次）** | 方案文档 + 隔离分支 | 本文入库；`git worktree list` 有两个工作区 | `docs(plan): 批注层并入 PageFlow 方案` |
| P1 | 搬包：`annotation/*` → `sketchpad/annotate/*`；删掉单实例与 `AnnotationApp`；写 `AnnotateController` + `install()`；`app.py` 里加 2 行 | PageFlow 启动后：托盘多一项「全屏批注」，点了能出批注层、能画、能穿透、能收起；**只有一个托盘图标、一个进程** | `feat(annotate): 批注层并入 PageFlow（骨架）` |
| P2 | 热键路由：第三个 `GlobalHotkey` + `foreground_belongs_to_us()` | 站在 PageFlow 窗口按 `Ctrl+Alt+D` → 走内部批注；切到记事本按同键 → 出全屏批注层；`Alt+Q/Alt+W` 行为不变 | `feat(annotate): 热键按前台窗口路由` |
| P3 | 截图：`capture.py` + 工具栏按钮（清空→撤销→截图→收起） | 点截图 → 框选 → 提示已复制 → `Ctrl+V` 能粘进 PageFlow 草稿；`Esc` 可取消 | `feat(annotate): 截图按钮与框选复制` |
| P4 | 收尾：`shutdown` 接线、设置对话框里加两个热键、打包配置、回归 | `pytest tests/` 全绿 + 真机 `probe_acceptance` 不退化；`dist/` 能打出单 exe | `chore(annotate): 收尾与打包` |

---

## 8. 回退方案

| 粒度 | 操作 |
|---|---|
| 退回本次（P0） | 什么都不用做：只在 `annotate-embed` 分支上多了一个 md 文件，`master` 未受影响 |
| 回退某一阶段 | 在 `annotate-embed` 上 `git reset --hard <上一阶段 commit>` |
| 整体放弃 | `git switch master`（回原工作区）；`git worktree remove E:\SketchPad-annotate`；`git branch -D annotate-embed` → 三个工作区干净如初 |
| 合并到 master 后想撤 | `git revert <merge commit>`；批注功能全部在新包 + 3 处钩子里，revert 一次即可 |

---

## 9. 待确认项（我会在动手前逐个核实）

1. **`GlobalHotkey` 多实例**：`HOTKEY_ID = 0xB0B1` 是硬编码常量，注册在**每个实例自己的 message-only 窗口**里；需确认两个额外实例同时注册不同按键不会互相覆盖（预期不会，但要跑一遍真机验证）。
2. **`hotkey.register(winId, seq)` 的 hwnd 参数**：批注侧一直传 `0`（自己建消息窗口），PageFlow 传 `self.winId()`。合并时统一传 0 还是传主窗 hwnd，需要按 `hotkey.py` 的注册实现确认。
3. **打包 spec**：`dist/` 被 `.gitignore` 忽略，构建脚本/spec 未在仓库根看到；`products/` 下有 4 个被跟踪文件，需要确认新包是否要写进 `hiddenimports`。
4. **截图是否要"贴进批注画布"**：本期只做剪贴板；如果要贴回画布，画布需要加图片层，工作量翻倍——需要确认。
5. **`Ctrl+Alt+D` 在 PageFlow 内部该做什么**：是「切到 PDF/草稿的批注工具」，还是「呼出全屏批注层」？本文按前者设计，需要确认。
6. **是否要把 `E:\批注` 归档**：合并稳定后是删掉、还是保留为独立工具？

---

## 10. 本文档的边界（已被后续实施改写）

- 没有修改任何业务代码；`E:\SketchPad` 工作区（含协作者的 22 项改动）未被触碰。
- `E:\批注` 未被修改（它不在 git 里，无法用版本管理回退，所以本期不动它）。
- 唯一新增：本文件 + `annotate-embed` 分支 + `E:\SketchPad-annotate` 工作区。

---

## 11. 实施记录（滚动更新）

### P0 方案定稿 —— `d647ccd` / `f2a21bf`（tag `annotate-p0`）
只有本文档。用 `git worktree add E:/SketchPad-annotate -b annotate-embed master`
隔离出干净基线，**不碰 master 上协作者的未提交改动**。

### P1 骨架 —— `8d85a85`
- `annotation/*` → `sketchpad/annotate/*`（core/ + canvas/config/constants/icons/overlay/theme/toolbar）
- **删掉**：`annotation/app.py`（AnnotationApp 装配）、`annotation/services/single_instance.py`
- **新增**：`controller.py`（`AnnotateController` + `foreground_belongs_to_us()`）、
  `__init__.py` 的 `install(window)`
- `config.py`：改成 `data/annotate.json`，路径由调用方给（取
  `window.controller.settings_path` 的父目录，源码/打包/Lite 三种布局都对）；
  `storage_dir` 运行时推导、不落盘
- `app.py`：**只加 2 行**（1 个 import + 1 行 `install(window)`，位于 `start_tray()` 之后）
- `tests/annotate/`：135 条
- 热键路由在 P1 一并落地（原计划的 P2 并入 P1）

### P3 截图 —— `b2bab77`
- 新增 `capture.py`：`CaptureOverlay` 全屏框选，Esc / 小选区取消，结果只进剪贴板
- 工具栏顺序：`grip / 穿透 钢笔 橡皮 / 粗细 / 色盘 / 清空 / 撤销 / **截图** / 收起 / swap`
- `controller.start_capture()`：先收掉批注层 → 80ms 后抓鼠标所在屏 → 框选 → 按原模式恢复
- `tests/annotate/`：**143 passed**

### 阻塞项（需要协作者配合）
`master@ab2db29` 本身跑不起来：它 import 的 `sketchpad/ui/empty_workspace.py` 是**未提交的新文件**，
不在该 commit 里。因此本分支的 PageFlow 侧测试（`tests/test_app_modes_offscreen.py` 等）全部
ImportError。**批注侧测试不依赖 app.py，143 passed 不受影响。**
需要协作者把 `empty_workspace.py` 等新文件提交上去，我再 rebase 一次就能跑全量回归。

### 未做（P4）
- 设置对话框里加两个批注热键（要等 `settings_dialog.py` / `core/settings.py` 的改动落地）
- PyInstaller 打包配置核对（`hiddenimports` / `datas`）
- PageFlow 全量回归（依赖上面的阻塞项解除）
- 合并回 master
