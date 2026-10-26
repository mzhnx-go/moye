# PageFlow 标签栏 / 笔记分屏 重构方案书

> 日期：2026-10-23
> 范围：多标签栏、草稿关闭、笔记分屏、独立笔记窗口
> 状态：P0/P1 已落地（2026-10-23），全量测试通过；未打包

## 1. 结论（推荐方案）

把现在分散在三条横栏里的东西收成一套：

1. **标签栏并入标题栏**：标题栏从 28px 长到 36px，标签、`+`、分屏按钮都放进去；删掉现在单独的一行标签栏。净省约 20px 高度。
2. **草稿标签也有 ×**：关闭前确认（有内容才问）；关掉后可以“新建草稿”，最后一个标签关掉显示空白工作区，而不是强行留一个草稿页。
3. **笔记分屏改成真正的 QSplitter**：PDF 左、笔记右，中间有 6px 可拖动分隔条；比例可拖、可记住、双击恢复 60/40；PDF 不再被笔记盖住。
4. **分屏时两边各有一个标题框**：左边 PDF 文件名 + “适应分屏宽度”，右边笔记名 + 纸张/窗口/删除/关闭。不再用“弹一个系统边框小窗”作为默认分屏形态。
5. **独立笔记窗口保留，但换成应用内同款标题栏**：深色自绘标题栏、拖动/双击最大化/边缘缩放，不再出现突兀的 Windows 原生白条。

## 2. 现状与根因

### 2.1 标签栏占掉一整行

`sketchpad/app.py:293-302` 把标题栏、菜单、工具栏、标签页容器依次竖排；`sketchpad/workspace/tabs.py:159-169` 里标签栏自己还有一行 `header`。所以窗口顶部实际是：

```
标题栏 28px
菜单栏
工具栏
标签栏 ~29px   ← 用户说的“再加一栏太浪费”
内容
```

### 2.2 草稿没有关闭键

- `tabs.py:199` `add_draft()` 明确“没有关闭按钮”；
- `tabs.py:335-336` 对 `entry.draft` 直接把右侧关闭按钮设为 `None`；
- `app.py:1558` `_close_entry()` 对草稿直接 `return`。

这三处共同导致草稿只能一直占着第一个标签。

### 2.3 笔记分屏不是分屏，是往布局里插一块固定宽度的面板

`app.py:747-798` 的当前做法：

- `card.set_resize_enabled(False)`（`app.py:767`）；
- 新建 `pane`，`pane.setMinimumWidth(360)`（`app.py:789`）；
- 直接 `host.layout().insertWidget(position + 1, pane, 0)`（`app.py:798`）。

没有 `QSplitter`，所以：

- 用户不能拖分隔条；
- 笔记面板按卡片 `sizeHint`（设计尺寸 560×720）占宽，PDF 被挤到左边；
- 窗口窄时 PDF 右侧内容被笔记挡住/裁掉，看起来像“挡住原文”。

### 2.4 独立笔记窗口是系统原生边框

`note_card.py:949` 用 `super().__init__(parent, Qt.Window)`，标题栏由 Windows 画成白/灰原生条；主窗是深色无边框，所以弹出时非常突兀。用户说的“两个标题框”也可以在这里落地：主窗分屏里给 PDF 和笔记各一个统一标题框，独立窗口也用同一套自绘标题栏。

## 3. 交互方案

### 3.1 顶部布局（推荐稿）

```
┌ PageFlow  [草稿 ×] [讲义.pdf ×] [笔记.md ×]  +  分屏   ⚙  📌  ─  □  ✕ ┐ 36px
├ 文件  编辑  视图  导航  工具  帮助        [工具栏按钮……]                 ┤
├────────────────────────────────────────────────────────────────────────┤
│ 内容区                                                                  │
└────────────────────────────────────────────────────────────────────────┘
```

- 标题栏左侧保留 `PageFlow` 小字；有标签时标签从它右边开始，没有标签时显示“打开文件 / 新建草稿”的提示。
- 标签右侧的 `+` = 打开文件；再右边一个“纸”图标 = 新建草稿；分屏按钮只对普通文档可用。
- 空白处双击仍是最大化/还原；在标签上拖动只移动标签，不拖窗口。
- 标签超出宽度时横向滚动，不压缩标签文字。

### 3.2 草稿关闭

- 所有标签（包括草稿）右侧都有 ×。
- 草稿有笔迹时点 ×：提示“草稿不会保存，关闭后内容会清空”，按钮为“关闭 / 取消”。
- 草稿为空时直接关闭。
- 关闭后：
  - 还有别的标签：切到最近使用的文档标签；
  - 没有标签：内容区显示空白页，两个按钮“新建草稿”“打开文件”，标题栏只剩 `+` 和“新建草稿”。
- 新建草稿入口：标题栏“纸”按钮、菜单 文件 → 新建草稿、快捷键 `Ctrl+N`。
- `Alt+Q` 仍然呼出独立的草稿纸浮窗，和主窗草稿标签不是同一个东西（保持现状）。

### 3.3 笔记分屏（主窗内）

```
┌ 讲义.pdf                          适应分屏  ║ 笔记 1                     …  × ┐
│                                   6px 可拖 ║ 工具行：画笔/文字/图片/颜色/粗细        │
│  PDF 页面（保持进入分屏时的缩放）           ║ 笔记画布（可手写/打字/插图片/附件）      │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

- 进入分屏时仍然**冻结 PDF 当前缩放**（不自动改变原文缩放）；但如果 PDF 太宽，“适应分屏”按钮可一键按新的左栏宽度重排（等价 `Ctrl+9`）。
- 中间分隔条：宽 6px，悬停高亮，鼠标变成左右箭头；拖动改变 PDF/笔记宽度比例。
- 比例范围：PDF ≥ 360px、笔记 ≥ 340px；窗口太窄时保持最小宽度并出现横向滚动，而不是互相覆盖。
- 双击分隔条恢复默认 60/40（或上次保存的比例）。
- 每份文档自己记住分屏比例；切换标签再回来比例不变。
- 关闭分屏后：按原有逻辑恢复进入前的“适应页宽”状态，PDF 滚动位置不跳。
- 分屏中“独立窗口”按钮仍保留：把这张笔记放到第二块屏幕；但窗口改成应用内同款标题栏。

### 3.4 独立笔记窗口

- 无边框 + 自绘标题栏（28px）：左边笔记名，右边最小化/最大化/关闭；深色与应用一致，圆角/1px 外框与主窗一致。
- 拖动标题栏移动；双击标题栏最大化；四边/四角可缩放（复用 `EdgeResizeFilter` / `install_window_border`）。
- 卡片自己的标题行保留“纸张/分屏/删除”等笔记动作，但隐藏重复的关闭按钮；窗口关闭由标题栏负责。
- 打开位置：主窗右侧偏移 24px；首次尺寸 640×820，之后记住这张笔记的窗口尺寸/位置（只放会话内，不写 sidecar）。

## 4. 技术方案

### 4.1 标签栏并入标题栏

- `sketchpad/ui/titlebar.py`
  - `TITLE_BAR_HEIGHT: 28 → 36`；新增 `set_center_widget(widget)`，把标签控件插到标题文字和窗口按钮之间。
  - 空白区域继续用 `startSystemMove` / 双击最大化；标签区域的事件不被标题栏吞掉。
- `sketchpad/workspace/tabs.py`
  - 拆出 `TabStrip(QWidget)`：包含 `tabbar`、`new_button`、`draft_button`、`split_button`；
  - `WorkspaceTabs` 保留 `stack`、`entries`、`add/remove/update_title` 等接口，`self.strip` 交给主窗挂到标题栏；
  - `_sync_decorations()` 对草稿也创建关闭按钮；
  - 新增 `newDraftRequested` 信号与 `Ctrl+N`；`closeRequested` 继续复用，由主窗判断草稿。
- `sketchpad/app.py`
  - 构造顺序改成先 `WorkspaceTabs`，再 `titlebar.set_center_widget(self.tabs.strip)`；
  - 菜单加“新建草稿”（`Ctrl+N`）、“关闭当前标签”（`Ctrl+W`）；
  - `_close_entry()` 增加草稿分支：确认 → 清空草稿历史/笔迹 → `remove_entry` → 切到其它标签或空态；
  - 新增 `EmptyWorkspace` 占位页；`_activate_draft()` 不再假设 `_draft_entry` 永远存在。

### 4.2 笔记分屏

- 新类 `sketchpad/ui/note_split.py`（或放进 `workspace/session.py`）：
  - `SessionPane(QWidget)`：内部 `QSplitter(Qt.Horizontal)`；
  - 左：`PaneFrame`（PDF 视图 + 26px 标题行，标题行只在分屏时出现）；
  - 右：`NotePane`（直接承载 `NoteCard`，卡片 header 就是右侧标题框）；
  - `show_note(card, ratio)` / `hide_note()` / `set_ratio()`；
  - `splitter.setChildrenCollapsible(False)`、`handleWidth(6)`、`setOpaqueResize(True)`；
  - `splitterMoved` 发出比例变化，主窗写回 `DocumentSession.note_split_ratio`。
- `DocumentSession` 增加：
  - `note_split_ratio: float = 0.62`
  - 现有 `note_split_card/pane/key` 保留，用于会话切换恢复。
- `app.py`
  - `_open_note_split()` 不再往 `host.layout()` 插 widget；改为 `session.pane.show_note(card, ratio)`；
  - 进入分屏：先 `_capture` 当前 PDF 的 `auto_fit`，再冻结缩放（保持现有语义）；
  - 关闭分屏：`session.pane.hide_note()`，按 `split_prev_auto_fit` 恢复；
  - 切换标签时 `_use_session_globals` 把比例/卡片状态带回。
- `NoteCard`
  - 分屏模式下允许任意宽高（去掉 560×720 的 `sizeHint` 约束），工具行放进横向滚动容器；最小宽度 340；
  - 只读/独立窗口模式保持现状。

### 4.3 独立笔记窗口

- `NoteWindow` 改为 `Qt.FramelessWindowHint`，使用新的 `NoteTitleBar`（可复用 `TitleBar` 的按钮绘制）；
- 复用 `install_window_border`、`EdgeResizeFilter`、`apply_rounded_corners`；
- 关闭按钮走现有 `close()`；`Esc` 仍不关窗；
- 窗口几何按笔记 id 记在 `DocumentSession.note_window_geometry`，重开还原。

## 5. 分阶段实施

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0-1 | 标签栏并入标题栏；草稿 ×；新建草稿/空态 | 顶部只剩一行标题栏；关草稿有确认；关最后一个标签不崩 |
| P0-2 | QSplitter 分屏、可拖比例、最小宽度、比例记忆 | 拖分隔条 PDF/笔记宽度变化；PDF 不再被覆盖；切标签比例不丢 |
| P1-1 | 两侧标题框、适应分屏按钮 | 左 PDF 标题 + 右笔记标题；`Ctrl+9` 只改 PDF 缩放 |
| P1-2 | 独立笔记窗口换自绘标题栏 | 无原生白条；可拖、可缩放、双击最大化 |
| P2 | 动画/细节（分隔条悬停、空态过渡、窗口位置记忆） | 不影响功能测试 |

每阶段单独提交；P0-1、P0-2 完成并跑完回归后再动 P1。

## 6. 测试与验收

新增离屏测试：

- `tests/test_titlebar_tabs.py`：标签在标题栏内、草稿关闭、空态、`Ctrl+N`、标签拖动不触发窗口拖动。
- `tests/test_note_split_ui.py`：QSplitter 存在、handle 拖动改变宽度、最小宽度、双击复位、比例随会话保存、PDF `zoom` 进入分屏前后不变、`fit_width` 显式改变缩放、关闭后恢复 `auto_fit`。
- `tests/test_note_window_chrome.py`：无原生边框标志、自绘标题栏、最小化/最大化/关闭、边缘缩放过滤器安装。

回归：原有 1042 项全部重跑；重点检查 `test_app_modes_offscreen`、`test_window_offscreen`、`test_note_wiring`、`test_note_card`、`test_workspace_*`。

人工验收（需要你亲自看）：

1. 窄窗口下拖分隔条，PDF 是否始终能横向滚动到右侧内容；
2. 150% 副屏上标题栏/标签高度是否拥挤；
3. 独立笔记窗口拖到副屏、双击最大化、四边缩放是否顺手；
4. 关草稿、撤销、再新建草稿是否符合预期。

## 7. 风险与回退

- **标签移进标题栏**是窗口拖动逻辑的改动：标签点击/拖动/滚轮/关闭按钮都要与 `startSystemMove` 隔离；风险最高，所以单独 P0-1 提交，可单独回退。
- **草稿可关闭**会让 `_draft_entry` 可能为 `None`，所有“回草稿”路径都要改；用空态页兜底。
- **分屏比例**不写 sidecar，只存会话/配置；不影响批注文件格式。
- **PDF 不自动缩放**的既有约束继续保留；只提供显式“适应分屏”，避免又出现“缩放原文”的抱怨。

## 8. 需要你点头的默认值

1. 分屏默认比例 **60/40**（PDF/笔记），可拖、双击复位——是否合适？
2. 草稿关闭时**清空内容**并移除标签（入口 `Ctrl+N` / 标题栏“新建草稿”）——还是希望关闭后仍保留草稿内容、只是隐藏？
3. 独立笔记窗口保留，但换成应用同款深色标题栏——还是干脆取消独立窗口、只保留主窗分屏？

按推荐方案执行时，这三项默认就是：60/40、关掉即清空、保留独立窗口但换皮。

## 9. 追加需求（2026-10-23，已随本轮落地）

用户补充的三条不属于上面的标签/分屏重构，单独先做，避免等重构：

### 9.1 每个功能控件悬浮 1 秒都有提示

- 新增 `sketchpad/ui/tooltips.py`：应用级事件过滤器记录悬停控件，单次 `QTimer` 满 1000ms 才 `QToolTip.showText`；同时压掉 Qt 原生提示，避免默认 0.7s 先弹。标签页 tab 提示也走同一控制器。
- 主程序启动时 `tooltips.install(app)`；菜单项自动用“文字（快捷键）”生成 tooltip/statusTip。
- 补齐了查找条“上一个/下一个/输入框”、工具栏图片按钮等缺失提示。
- 测试：`tests/test_tooltips.py` 覆盖工具栏、标题栏、查找条、笔记卡、菜单和主窗所有用户按钮。

### 9.2 Standard 必须包含 Lite 的全部功能

- 鼠标工具（`V`，平移/选字/拖图钉/×删除）在 PDF、Word 缓存 PDF 中保留原行为。
- 纯文本/Markdown/思维导图阅读器现在也显示「鼠标」按钮：文本直接拖选（原生 QTextEdit 选择），导图拖动平移；不再因为 `kind="text"` 把按钮整组藏掉。
- 菜单“鼠标（平移/选字）”在文本/导图模式也可用。
- 测试：文本阅读器、思维导图的主窗测试断言 `select_button` 可见、菜单项可用；PDF `V` 选字测试保持。

### 9.3 草稿可插入截图/图片并直接批注

- `SketchScene` 增加独立于笔迹模型的背景图列表；图片固定在笔迹下方（`z=-100`，不吃鼠标事件）。
- 插入方式：工具栏「插入截图/图片」按钮、`Ctrl+V` 粘贴剪贴板截图、把图片文件拖进草稿窗口。
- 图片只缩小不放大，居中放进当前视口；**清屏只清笔迹，保留背景图**，方便擦掉重画。
- 草稿本来不落盘，图片也只存在内存；关闭草稿/新建草稿时清空。
- 测试：`tests/test_draft_images.py` 覆盖插入、在图片上落笔、清屏保留图片、剪贴板粘贴、文档模式不接图片。

**注**：9.3 的“关闭草稿清空图片”依赖 9.4（草稿可关闭）落地；在那之前图片会随草稿标签一直保留到程序退出。

## 10. 分屏空白：已定位并修复（2026-10-23）

用户截图里标签标题已经是 `A | B`、分隔线和“取消分屏”都在，但两栏全空。

根因：`SplitWorkspace.detach_sessions()` 会把两侧 `session.pane` 先 `hide()` 再 `setParent(None)`；
再次装配时 `_make_pane()` 只 `layout.addWidget(session.pane)`，没有 `show()`。Qt 不会把显式隐藏的子控件因为换父而自动显示，所以两栏一直是隐藏状态。

修复：装配时 `session.pane.show()` + `frame.show()`；`tests/test_workspace_tabs_app.py` 增加“两栏 pane 真的 visible”的断言。提交 `eb689a7`。

## 11. 标签拖拽交互（新增需求）

### 11.1 三种拖拽结果

| 拖动落点 | 结果 |
|---|---|
| 同一标签栏内、落在两个标签之间 | 改变顺序（插到光标所在的前/后） |
| 悬停在**另一个文档标签**上满 **3 秒**，且该目标不是分屏、源也不是分屏 | 预览合并；**松开才真正合并** |
| 拖出所有窗口范围后松开 | 该标签变成一个新窗口 |

约束：

- 草稿标签固定第一个，不参与合并/拖出；文档标签可重排。
- 分屏标签（`A | B`）只能整体重排/拖出，不能再与第三个文档合并（最多 2 栏）。
- 拖到目标标签上不足 3 秒就松开：按“插入到该标签前/后”处理，不合并。
- 悬停 3 秒期间目标标签标题临时显示 `源文件 | 目标文件`（例如 `讲义.pdf | 卷子.pdf`）；移开或按 Esc 立即恢复原文字，不落任何状态。
- 合并预览只改标签文字和一条高亮边框，不移动 session；松开才执行 `_merge_*`。
- 拖出时鼠标附近浮一个“松开 → 新建窗口”的提示；拖回窗口内自动消失。

### 11.2 拖拽状态机

```
Idle
 └─ 左键按在文档标签，移动 > 6px
      ├─ 仍在 tab 栏水平范围 → Reorder（显示插入位置）
      ├─ 光标进入另一窗口 tab 栏 → CrossWindow
      │    ├─ 目标 tab 上停留 <3s → 松开=插入到该 tab 前/后
      │    ├─ 目标 tab 上停留 ≥3s → MergePreview
      │    │     ├─ 松开 → merge
      │    │     └─ 移开/Esc → 取消预览，继续拖
      │    └─ 松开且不在 tab 上 → 不操作
      └─ 光标离开所有窗口 > 24px → DetachPreview
           ├─ 松开 → 新建窗口并移入该标签
           └─ 拖回 → 取消预览
```

计时从“悬停到同一个目标标签”开始；换目标重新计时。预览阶段用目标标签的临时文本 + 2px 强调色上边框表示，不弹窗。

### 11.3 新窗口（标签拖出）

推荐**同进程二级工作区窗口**，不是再起一个进程：

- 主窗（primary）继续独占：托盘、全局热键、草稿纸浮窗、单实例 IPC、退出流程。
- 拖出的窗口是 `WorkspaceWindow`：同款标题栏/菜单/工具栏/标签栏，只装文档标签，**不带草稿标签**；共享同一个 `AppController`（settings、最近文件）。
- 移动标签时只 reparent `TabEntry` 的 page/session，不销毁、不重读 PDF；撤销栈、滚动、笔记、标签状态都保留。
- 主窗关闭到托盘时，二级窗口保留；托盘“退出”会逐个窗口走保存确认。
- 二级窗口不做单实例/热键注册；在二级窗口里改设置只写共享 `settings.json`，本轮不做其他窗口的实时热更（下次打开生效），避免多窗口信号风暴。
- 分屏标签拖出时，两个 session 一起进新窗口，仍保持 `A | B` 分屏。

## 12. 技术拆分

### 12.1 拖拽引擎

新增 `sketchpad/workspace/tabdrag.py`：

- `TabDragController(QObject)` 管理状态机、6px 阈值、3s 计时器、Esc 取消；
- QDrag MIME：`application/x-pageflow-tab`，负载 `{window_id, entry_id}`；同进程用 `WindowRegistry` 解析；
- 信号：
  - `reorderRequested(entry, target_index)`
  - `mergePreviewChanged(source, target, preview_text | None)`
  - `mergeRequested(source_window, source_entry, target_window, target_entry)`
  - `detachRequested(source_window, source_entry, global_pos)`
- `WorkspaceTabs` 负责渲染插入位置/预览文字；实际合并/移窗仍由 `WindowRegistry` 调用现有 `_merge_document_entries` 逻辑。
- 3 秒预览用单次 `QTimer`；每次 `dragMoveEvent` 检查目标 tab 是否变化，变则重新计时。

### 12.2 二级窗口

新增 `sketchpad/workspace/windows.py`：

- `WindowRegistry`：保存 primary + 所有 `WorkspaceWindow`，按 window_id 查窗口；
- `WorkspaceWindow(MainWindow)` 或组合一个轻量窗口：复用现有 tabs/session 代码，构造时 `primary=False` 关闭 tray/hotkey/scratch/instance server；
- `detach_entry(source_window, entry, global_pos)`：从源 tabs 移除（不关闭 session）→ 新窗口 `add_entry` → 摆到光标附近；
- `close_all_windows()` 供托盘退出使用。

### 12.3 标题栏与草稿

按第 3/4 节：标签栏搬进 36px 标题栏；草稿 × + 确认 + 空态；`Ctrl+N`/`Ctrl+W`。这部分与拖拽共享 `TabStrip`，由主接线统一改。

## 13. 子 agent 分工

用户要求并行开发。按“文件不相交”拆成三个阶段，主 agent 负责 app.py 接线和合并：

**Phase 1（并行）**

| Agent | 文件 | 交付 |
|---|---|---|
| A：拖拽引擎 | `workspace/tabdrag.py`、`workspace/tabs.py`、`tests/test_tab_drag.py` | 状态机、重排、3s 预览、跨窗口 MIME、信号；不碰 app.py |
| B：分屏模块 | `workspace/note_split.py`、`workspace/session.py`、`tests/test_note_split_ui.py` | QSplitter、两侧标题框、比例保存、最小宽度；不碰 app.py |
| C：标题栏 | `ui/titlebar.py`、`tests/test_titlebar_tabs.py` | 36px 标题栏容纳 `TabStrip`、拖拽空白区、窗口按钮布局；不碰 app.py |
| 主 agent | `app.py` | 接线 A/B/C、草稿关闭/空态、`Ctrl+N/W` |
| 校验 agent | 只读 | 审 diff、跑测试、找回归；不改代码 |

**Phase 2（依赖 Phase 1 的 tabs API）**

| Agent | 文件 | 交付 |
|---|---|---|
| A：跨窗口拖拽 | `workspace/tabdrag.py` + `tabs.py` | 接 `WindowRegistry` 的 detach/merge 落点 |
| B：二级窗口 | `workspace/windows.py`、`app.py`（与主 agent 约定接口后再改） | `WorkspaceWindow`、`WindowRegistry`、退出收口 |
| 主 agent | `app.py` | 最终接线、全量回归 |

**Phase 3**：主 agent 统一跑 1052+ 测试、人工验收；每个 Phase 单独提交，失败可回退。

## 14. 新增验收

- 拖一个标签到两个标签之间 → 顺序改变，不打开新窗；
- 拖到另一标签上 2 秒松开 → 只重排/不合并；
- 悬停满 3 秒 → 目标标签显示 `A | B` 预览；松开后成为真正分屏；Esc/移开恢复；
- 拖出窗口松开 → 新窗口出现且文档状态（撤销/滚动/笔记）保留；原窗口少一个标签；
- 分屏标签拖出 → 新窗口仍是 `A | B`；
- 分屏现在真的有内容（`pane.isVisible()`），不是两条空栏；
- 草稿拖拽被禁止且始终第一位；草稿 ×/空态行为按第 3.2 节。

## 15. 风险

- QTabBar 自绘拖拽会与“点击切换”“拖动重排”“双击标题栏最大化”抢事件；阈值和状态机必须集中在一个 controller，TabBar 不再单独处理拖动。
- 二级窗口共享 `AppController`，但 `MainWindow` 里很多 `self.controller.document`（草稿）的假设只对 primary 成立；`primary=False` 时必须禁用草稿相关路径。
- 跨窗口 reparent QGraphicsView 会触发 resize/fit_width；移动 session 前先保存缩放/滚动，移动后恢复，避免“拖一次窗口缩放变了”。
- 3 秒预览计时器要在 dragLeave/目标变化/Esc/窗口关闭时全部取消，防止幽灵标签文字。

**仍待确认**：第 8 节的 60/40、关草稿即清空、独立笔记窗口换皮三项默认值；本轮先按推荐值实现。
