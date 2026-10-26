# SketchPad 设计文档

> **2026-10-20 需求变更（以本节与 §8 为准）**：撤销「保存」功能——草稿只存在内存，关程序即丢（用户原话：「不需要保存功能，这是草稿」）。全文凡提到 `draft.json`、`.bak`、自动保存、启动恢复、`app/storage.py` 的段落一律作废，按 §8 执行；`settings.json`（配置）仍然保留。

日期：2026-10-20
状态：待用户复核（本文只定设计与接口，不含实现代码）
目标平台：Windows 10/11 x64，Python 3.13 + PySide6（Qt 6）

## 1. 一句话定位

打开就能涂的桌面草稿板：颜色、粗细档位、一键清屏、滚轮平移。不是画师软件，不是笔记软件。

## 2. 需求（用户点名要的）

| 编号 | 需求 | 定死的口径 |
|---|---|---|
| R1 | 画笔颜色 | 画布底色为深灰 `#202124`（参考 Lorien），故工具栏 8 个预设色以浅色打头：`#e8eaed` `#c0392b` `#d35400` `#f1c40f` `#27ae60` `#2980b9` `#8e44ad` `#7f8c8d`，另加 1 个自定义取色器；默认笔色 `#e8eaed`；当前色实时生效 |
| R2 | 笔粗细 | 工具栏 4 档预设（1.5 / 3 / 6 / 12 世界像素）+ **自定义滑杆**：1–48、0.5 一档，右侧显示数值；点预设同步滑杆，滑杆值恰为某档时该档高亮，否则四档都不亮；自定义值写进 `settings.json` 的 `width` |
| R3 | 一键清屏 | 默认单键 `E`，不弹确认框；工具栏垃圾桶图标同样一键清屏（草稿清整块画布、文档清当前页）；清空是**一个可撤销动作** |
| R4 | 清屏键可自定义 | 默认键可改，**只在设置面板**里改（工具栏图标直接清屏，不进捕获），改法见 §6.3；改完写进配置文件 |
| R5 | 滚轮下滑 | 无修饰滚轮 = 垂直平移（一格 = 视口高度 10%）；`Ctrl+滚轮` = 缩放 |
| R6 | 保存 | **不做保存**（用户 2026-10-20：「不需要保存功能，这是草稿」）。草稿只存在内存里，关掉程序即丢；不写磁盘、没有保存按钮、没有启动恢复。配置文件（`settings.json`：清屏键/颜色/粗细/热键）仍保留——那是设置，不是草稿。 |
| R7 | 撤销/重做 | `Ctrl+Z` / `Ctrl+Y`，深度上限 100 步 |
| R8 | 画笔光标 | 画布内**隐藏系统箭头**，在笔尖当前位置画一个**实心小圆点**；直径 = `当前线宽 × 缩放` 的屏幕像素，下限 4 像素；橡皮时为**空心圆环**（固定 24 像素直径，作为"非画笔"的视觉区分）；光标移出画布恢复系统箭头 |
| R9 | 草稿纸弹窗 | **全局热键**（默认 `Alt+Q`，配置项 `scratch_hotkey`）任意程序里按下即可弹出一块独立置顶草稿窗；再按一次隐藏（内容保留在内存里，进程退出即丢，**不写进任何文件**）；草稿窗内 `E` 清屏、`Esc` 隐藏；主窗关闭时进程驻留托盘，否则全局热键会随进程消失 |
| R10 | 窗口置顶 | 标题栏「置顶」图钉可勾选（在最小化 `−` 左边）：勾上后主窗 `WindowStaysOnTopHint`，可压在 Xournal++/浏览器之上；取消即恢复；不记忆（每次启动默认不置顶） |
| R11 | 可缩到很小 | 最小尺寸 **340×240**；工具栏放进横向滚动区，窗口很窄时按钮仍可横向滚动到达 |
| R12 | 设置面板 | 标题栏齿轮按钮（`Ctrl+,`）打开：**绘制**（8 色 + 取色器、1–48 粗细滑杆）、**快捷键**（清屏键、草稿纸热键，点「修改」后按键捕获，非法/保留键拒收）、**固定键位**只读列表；底部「还原默认 / 取消 / 保存」。保存后立即生效并落盘；草稿纸热键变更会重新注册，失败时状态栏提示并回退程序内快捷键 |

不做（明确排除，出现即算范围外）：压感、图层、多页/翻页、PDF 导入与标注、手写识别、笔迹局部擦除（只删整条）、图片导入、云同步、协作、手写转文字、橡皮灰度、多草稿标签、**草稿存盘/自动保存/保存按钮/启动恢复**（2026-10-20 撤销 R6）。

## 2.1 外观（2026-10-20 起）

- 工具栏参考 Lorien：**纯图标**（撤销 / 重做 / 画笔 / 橡皮 / 4 档粗细圆点 / 8 个圆形色块 / 调色盘 / 清屏），底色 `#333336`（比外壳浅一档的灰），分组之间细分隔线。
- **无边框窗口 + 自绘标题栏**（34px，用户 2026-10-20：置顶要放到 `−` 旁边，Windows 原生标题栏做不到）：左标题，右依次为 **置顶 / 最小化 / 最大化(还原) / 关闭**。拖动交给 `QWindow.startSystemMove()`，双击标题栏最大化/还原，窗口边缘 6px 的缩放由 `WM_NCHITTEST` 返回 `HTLEFT` 等；Win11 用 DWM 属性 `DWMWA_WINDOW_CORNER_PREFERENCE=ROUND` 加圆角。代价：失去原生贴边分屏提示与系统阴影。
- 图标由 `app/icons.py` 用 QPainter 现画（2 倍 DPR，任意缩放不发虚），不引入图片资源；文字只出现在工具提示里（如「画笔 (B)」「清屏 / 清空本页 (E)」）。工具栏按钮只触发动作：清屏键的改绑只在设置面板里（§6.3）。
- 色块为圆形（22px），选中加白色描边；取色器图标是四色圆盘。
- 画布仍为 `#202124`（比外壳深一档，形成层次）。全部颜色在 `app/theme.py` 一个文件里。
- Qt 坑：`QWidget` 子类必须 `setAttribute(Qt.WA_StyledBackground, True)` 才吃 QSS 背景；给视口 `setStyleSheet` 会级联到子控件（曾把按钮背景刷没）；给 QPixmap 设了 `devicePixelRatio` 后 QPainter 会自动放大，别再手动 `scale`（否则图标被裁成四分之一）。

压感为何在本文里仍保留一条数据字段位置：见 §5，代价为零，且这是唯一一处为将来留的口子。

## 3. 参考来源（区分已核实与未核实）

已核实（今天从用户本机文件直接读出，不是网上文档）：

- `E:\Lorien`（mbrlabs/Lorien v0.6.0，Godot 3.5.3 打包）。从 `res://project.binary` 读出全部 26 个注册输入动作，其中删除类只有 `delete_selected_strokes`，**没有** clear-canvas 动作；`res://UI/Toolbar.tscn` 里有 `ClearCanvasButton`（tooltip "Clear Canvas"，连到 `Main._on_clear_canvas`），但节点写着 `visible = false`，界面摸不到。它自己的键位：`B` 画笔、`E` 橡皮、`L` 直线、`R` 矩形、`C` 圆、`S` 选择、`M` 移动、`Esc` 取消选择、`Delete` 删除所选、`Tab` 无边距、`F11` 全屏、`+/-` 缩放、方向键平移。
- 结论采纳两条：(a) 清屏必须绑到动作上、且默认按钮不许藏起来，否则就是 Lorien 这样——功能存在、入口没有；(b) `E` 被清屏占走后，橡皮必须挪位（本文挪到 `Q`）。

已核实（用户本机 Xournal++ 1.3.8 便携版实测，路径为绝对路径）：

- `E:\Xournal++\bin\xournalpp.exe` 的 PE 版本信息 `FileVersion 1.3.8`；目录只有 `DotCursor/ bin/ lib/ share/`、无卸载程序 → win64 便携 zip 布局。全盘找不到 `settings.xml`（`E:\Xournal++\.xournalpp` 与 `%APPDATA%\xournalpp` 都没有）→ 它跑的是**编译期默认值**，所以下面的默认行为就是他机器上的实际行为。
- 滚轮：`E:\Xournal++\share\xournalpp\ui\settings.glade:4298` 有「Speed for Ctrl + Scroll」(`spZoomStepScroll`)、`:4325`「Speed for a Zoomstep」，即 Ctrl+滚轮缩放、且只能调速度；该文件 45 个复选框 id 里**没有**任何「滚轮=缩放」开关，exe 与全部 `.mo` 里 `Zoom on scroll`/`wheel` 零命中 → 无修饰滚轮就是 GTK 滚动窗口的垂直平移。这直接坐实 R5（`E:\Xournal++\bin\xournalpp.exe`、`settings.glade`）。
- 橡皮三型：`E:\Xournal++\share\xournalpp\ui\settingsButtonConfig.glade` 的 `cbEraserType` 列出 Standard / Whiteout / **Delete stroke**，序列化值 `whiteout`、`deleteStroke`。本设计的橡皮 = 它的 `deleteStroke`（删整条）；Standard（在一条笔迹内部切断）明确排除，因为那是「局部擦除」，在 §2 的非目标里。
- 笔迹序列化：`.xopp` 是 zip（mimetype `application/xournal++`，正文 `content.xml`），`<stroke>` 属性含 `tool/color/width/fill/capStyle/pressures`，世界单位是**点**（内嵌模板 `595.275591x841.889764` = A4@72dpi）。本设计不跟它的单位（我们是无限画布，用世界像素），但跟它「每笔存一个线宽 + 属性表在笔迹级而非点级」的做法。
- 未核实（本机文件里查不到，本文不引用为事实）：Xournal++ 那三张数值表（`0.42/0.85/1.41/2.26/5.67`、`1.0/2.83/8.5/12/18`、`0.7/1.41/2.4/4/7`）哪张是笔、哪张是荧光笔；`.xopp` 里点坐标的确切写法（安装内没有样例文件）。它的 5 档命名是 `VERY_FINE…VERY_THICK`（`toolbar.ini:37` 有注释），本设计用 4 档，是有意的简化。

已核实（公开仓库/官方文档，一轮独立调研）：

| 软件 | 许可 | 与本设计相关的一条事实 | 本设计的取向 |
|---|---|---|---|
| Xournal++ | GPL-2.0 | 滚轮平移、Ctrl 滚轮缩放；无压感时可开「Enable Pressure Inference」用速度推算（issue #2781） | 采纳滚轮语义（R5）；速度推算不做（§2 非目标） |
| Excalidraw | MIT | 清空动作 `actionClearCanvas` 是**软删除**（`isDeleted`，可撤销、元素仍在文件里），带确认框，且动作文件里**没有默认键位** | 采纳「可撤销」；不采纳确认框（R3 用户点名不要）；必须给键位（R4） |
| Krita | GPL-3.0 | Edit▸Clear（Del）只清**当前层**且可撤销；平板设置里可切 WinTab / Windows Ink，官方文档点名第三方工具会打断事件 | 我们无图层，清屏=清整张；压感已出局，这条只作为「Windows 笔输入有两条 API 路线」的佐证 |
| OpenBoard | GPL-3.0 | issue #429：换页后重置撤销缓冲导致整页丢失；#537：Windows Ink 下短笔迹压力抖成点状 | 撤销缓冲是数据边界，清屏必须占**一步**且可回滚（R3 的实现理由） |
| rnote | GPL-3.0 | `.rnt` = gzip JSON，笔迹为 `PenPath + Style`；官方承认格式仍在破坏性变更 | 我们已撤销保存（§8）；格式带 `version` 的旧实现见 git 历史 |
| Saber | GPL-3.0 | issue #704：Flutter 桌面在 Windows 上检测不到笔 | 不选该路线，与本设计无关 |
| MyPaint | GPL | 无限画布但**栅格**笔刷（只存用过的瓦片），笔画不可再编辑；文档强烈建议常开自动备份 | 我们保持矢量可编辑（§5），故与它不同 |

采纳的共性结论：一，主流同类软件里「清空」全部是**可撤销**动作，且作用域明确（Krita 限层、OpenBoard 限页），没有一个是「不可逆地抹掉」——R3 按这条写死。二，「清空」普遍**没有默认键位**（Excalidraw 有确认框无键位、Lorien 连入口都没放出来），所以本设计把「可自定义键位」列为硬需求 R4，而不是顺手加个按钮。三，Windows 上笔输入是 API 分叉问题，因此实施计划第一步就是拿探针实测他的设备走哪条路，实测不过不写绘制代码。

不复制任何上述项目的代码；只借鉴设计取向，许可差异与此无关。

## 4. 总体架构

三层，单向依赖，渲染层可整体替换：

```
输入事件 → CanvasController(状态机：当前工具/当前笔) → Command(动作对象)
                                                        ↓
                                            History(撤销栈) ← Scene 模型(笔迹列表)
                                                        ↓
                                            渲染层(QGraphicsView + PathItem)
```

- 模型层（`app/strokes.py`）不 import Qt，纯数据结构 + JSON 编解码 + 抽稀。
- 视图层（`app/canvas.py`）只把模型画出来，不拥有业务规则。
- 好处：将来把 Qt 换成别的、或把压感加回来，只动视图层与 `Stroke` 的宽度计算，模型与撤销栈不动。

## 5. 数据模型

```python
Point = tuple[float, float]          # 世界坐标，1 单位 = 1 世界像素

@dataclass
class Stroke:
    points: list[Point]              # 至少 1 点；1 点 = 一个点画
    color: str                       # "#rrggbb"
    width: float                     # 世界像素，1.5/3/6/12 之一
    tool: Literal["pen"] = "pen"     # 预留字段，本版只有 "pen"

@dataclass
class Viewport:
    scale: float                     # 1.0 = 1 世界像素 : 1 屏幕像素，钳制在 MIN/MAX_SCALE
    origin: Point                    # 世界坐标 (0,0) 当前落在屏幕上的位置（逻辑像素）

@dataclass
class Document:
    strokes: list[Stroke]
    viewport: Viewport               # 重开时恢复到上次视角
```

规则：

- 坐标一律**世界坐标**，不存屏幕坐标（缩放/平移只改变换，笔迹永不重算）。
- 一次落笔到抬笔 = 一条 `Stroke` = 一个撤销单位。
- 抽稀：新点与上一个保留点距离 < 1.5 世界像素则丢弃；首点与末点始终保留。
- 平滑：渲染时用「相邻两点中点 + `quadTo`」生成 `QPainterPath`；模型里仍只存原始点列（可逆，撤销/存盘都简单）。
- 点上限：单条笔迹超过 4000 点时停止追加并状态栏提示（防悬笔/驱动异常刷点）。
- `tool` 字段的存在是唯一为将来压感留的口子；本版不读它。

## 6. 输入与键位

### 6.1 键位表（默认值）

| 键 | 动作 | 备注 |
|---|---|---|
| `E`（可改） | 清空全部笔迹 | 主窗与草稿窗都生效；不弹窗；进撤销栈 1 步 |
| `B` | 切到画笔 | 默认工具 |
| `Q` | 切到橡皮 | 因为 `E` 已被清屏占用 |
| 橡皮下点击/划过 | 删除整条命中笔迹 | 命中判定：点到笔迹折线距离 ≤ `max(width, 6)/2` 世界像素；一次划过删掉的所有条 = 1 个撤销单位 |
| `1`–`4` | 切粗细档 | 对应 1.5/3/6/12 |
| `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做 | 主窗与草稿窗都生效 |
| 鼠标（无键位） | 标题栏：置顶 / 最小化 / 最大化(还原) / 关闭 | 置顶图钉在 `−` 左边；双击标题栏最大化/还原（§2.1） |
| `滚轮` | 垂直平移，一格 = 视口高度 10% | R5 |
| `Shift+滚轮` | 水平平移，一格 = 视口宽度 10% | |
| `Ctrl+滚轮` | 以鼠标位置为锚缩放，一格 ×/÷ 1.08 | 上下限 8%–1600% |
| `0` | 缩放复位到 100%，视口回到笔迹包围盒中心 | |
| 中键拖动 / 空格+拖动 | 任意方向平移 | 与滚轮互为备份 |
| 方向键 上/下/左/右 | 垂直/水平平移，一步 = 视口 10% | 与滚轮同方向：`Down` = 内容向上（用户 2026-10-20 要求） |

冲突规则：快捷键一律挂在**画布视图**上，context 为 `Qt.WidgetWithChildrenShortcut`。因此焦点在取色器、颜色输入框或设置面板的键位捕获控件里时，`e` 就是字母 `e`，不会触发清屏。这条在 §12 第 9 步实测。

「一格」的定义：`QWheelEvent.angleDelta().y()` 累计满 120（标准滚轮一格的值）算一格；触控板给 `pixelDelta()` 时直接按像素位移，一格 10% 的规则不适用。

### 6.2 笔/鼠标准入（不依赖压感，但必须防幽灵线）

- 起笔条件：`mousePressEvent` / `QTabletEvent` 且 `buttons() == LeftButton`。
- **禁止**用 `TabletProximity`（笔悬空靠近）或 hover 起笔，否则会画出幽灵线。
- 压力值本版不读；实现第一天要跑一次事件探针（§14 步骤 0），把该设备上实际收到的事件类型、坐标、`deviceType` 打印出来存档。理由：Qt6 在 Windows 上笔可能以 `QTabletEvent` 而非 `QMouseEvent` 到达，两条路都要能落笔，且不能双份画线。探针未跑通之前不写绘制逻辑。

**实测结论（2026-10-20，用户 GAOMON 数位板，探针日志 165 KB / 3493 条事件）：**

- 笔落到板面 → 只产生 `mousePress`/`mouseMove`/`mouseRelease`，**`QTabletEvent` 计数为 0**（tabletPress/Move/Release/Proximity 全无）。15 次按下对 15 次抬起，没有粘连。→ 落笔走鼠标分支即可；tablet 分支仍保留守卫（换成 Windows Ink 模式或换驱动后可能改道，不能假设永远如此）。
- 滚轮 `angleDelta` 为 ±120/格、`pixelDelta` 为 0 → §6.1 的「一格 = 120」在这台机器上成立。
- **悬空移动也会产生无按键的 `mouseMove`**（3492 条移动里绝大多数是悬停）。所以起笔必须判 `buttons() == LeftButton`，绝不能"有移动就画"。
- 探针自身两个坑（已修，写回计划 Task 8）：① 窗口对象必须留 Python 引用，`Probe().show()` 会被 GC，进程活着但窗口消失；② Qt6 的富枚举不能 `int()`，否则整行日志会因异常被吞掉。

### 6.3 清屏键自定义

- 配置项 `clear_key`，字符串键名（如 `"E"`、`"Delete"`、`"Ctrl+Shift+X"`），存 `settings.json`。
- **改绑只在设置面板里**：标题栏齿轮（或 `Ctrl+,`）→「清屏键」→「修改」→ 进入 3 秒捕获态（按钮文字变「请按键…」）→ 按下的组合键填回输入框，点「保存」才写进配置并立刻生效 → 捕获中按 `Esc` 取消。
- 工具栏的清屏按钮只是动作：点一下立刻清屏，草稿模式清整块画布、文档模式只清当前页，不进入任何捕获态；工具提示「清屏 / 清空本页 (E)」里的 `E` 是默认键。
- 捕获态内不响应其它快捷键；捕获到 `Ctrl+C/V/Z/Y/A` 或 `Space`、`0`、`B`、`Q` 直接拒收并提示——前 5 个是系统/编辑键，后 4 个是画布自己的平移/复位/画笔/橡皮键，装上清屏会让它们静默失效。
- 配置非法值（空、无法解析）→ 回退默认 `E` 并在状态栏提示一次，不崩。

## 7. 渲染

- `QGraphicsView` 子类 `SketchView`，`QGraphicsScene` 持有一条笔迹对应一个 `QGraphicsPathItem`。
- 画笔预览：当前笔迹走 `SketchView.drawForeground()` 实时绘制，抬笔才 `addItem` 固化——避免一条长笔迹生成上万个 item。
- 线宽按**世界单位**（放大后线变粗）。这是「墨在纸上」语义，跟无限画布 + 缩放后继续画不打架；代价：缩小后 1.5 世界的线会细到接近 1 屏幕像素，所以渲染时对 `width * scale < 1` 的笔迹钳到 1 像素下限，保证看得见。
- 抗锯齿：`QPainter.Antialiasing`；`renderHint`s 用 `AntialiasingWithQuality`。
- 画布背景：固定深灰 `#202124`（用户 2026-10-20 指定，参考 Lorien）；网格属于范围外。底色变深后默认笔色必须换浅色（`#e8eaed`），否则笔迹在自身上看不见——这条是 R1 预设色顺序的原因。
- 光标（R8）：`viewport().setCursor(Qt.BlankCursor)`，在 `drawForeground` 里于最后一次鼠标位置绘制圆点（画笔）/空心圆环（橡皮）。位置来源是 `mouseMoveEvent`——实测悬空移动也会来事件，正好用来更新光标；未进入画布或已移出则恢复 `Qt.ArrowCursor`。圆点用与笔迹同色、80% 不透明度，避免遮住落点；橡皮圆环固定 24 像素直径。
- 性能线（不是愿望，是验收线）：1000 条 × 平均 200 点的文档，滚轮平移与 Ctrl+缩放时不出现持续 > 16ms 的单帧。达不到就按 §11 的降级手段处理，不许把阈值调低。

## 8. 持久化（2026-10-20 撤销自动保存）

- **草稿不落盘**：内容只在内存里，关掉程序即丢。没有 `draft.json`、没有 `.bak`、没有保存按钮、没有启动恢复、没有 `Ctrl+S`。
- 随之删除：`app/storage.py`、`tests/test_storage.py`、`main.py` 里的自动保存/恢复/版本闸门接线；`tools/perf_fill.py` 产出的文件只用于性能测试，不参与日常使用。历史实现见 git `1dffb3c` 及之前。
- **配置仍落盘**：`settings.json`（`clear_key` / `color` / `width_index` / `scratch_hotkey`），位置 `data/settings.json`；目录不可写时回退 `%APPDATA%\SketchPad\settings.json`。配置读写失败只影响下次启动的默认值，不影响本次使用。
- `app/strokes.py` 的 `encode`/`decode` 保留：它们仍是性能造数工具的格式，也是将来若恢复保存时的现成实现（但当前主程序不调用）。

## 9. 错误处理与边界

| 情况 | 行为 |
|---|---|
| 撤销栈超 100 步 | 丢最老的；清屏这类大动作会连带释放它持有的笔迹对象，状态栏显示当前步数 |
| 清屏后立刻再清屏 | 第二次仍入栈（撤销会一步回到「空」再一步回到「有」），不做去重 |
| 单点笔迹（点一下） | 存成 1 点笔迹，按直径画实心圆点 |
| 窗口尺寸 0（最小化时缩放） | 跳过变换计算，不改 `viewport` |
| 缩放触边（8% / 1600%） | 停止变化，不弹提示 |
| 程序被强杀 / 正常退出 | 草稿随之消失（只在内存里，这是预期行为，不是 bug） |
| PySide6 未安装 | `main.py` 捕获 `ImportError`，打印一行安装命令后退出，不抛栈 |

## 10. 模块划分与接口（供并行实现，签名即契约）

每个模块单独可读、单独可测；除标注外都不 import Qt。

| 文件 | 职责 | 对外接口 |
|---|---|---|
| `app/strokes.py` | 数据模型 + 抽稀 + 距离命中 | `Point`, `Stroke`, `Document`, `Viewport`, `thin(points, min_dist=1.5)`, `encode(doc)->str`, `decode(text)->Document`（`decode` 抛 `DraftError` 子类） |
| `app/camera.py` | 世界↔屏幕纯算术（约定 `screen = world * scale + origin`） | `world_to_screen(vp, p)`, `screen_to_world(vp, p)`, `pan_by(vp, dx, dy)`, `zoom_at(vp, screen_pos, factor)`（保证 `screen_pos` 下的世界点变换后仍在 `screen_pos`）；常量 `MIN_SCALE=0.08`, `MAX_SCALE=16.0`, `WHEEL_ZOOM_STEP=1.08`, `WHEEL_PAN_RATIO=0.10` |
| `app/history.py` | 撤销/重做栈 | `History(limit=100)`: `push(cmd)`, `pop_undo()->cmd|None`, `pop_redo()->cmd|None`, `can_undo`, `can_redo`, `depth`；任何新动作清空 redo 分支 |
| `app/commands.py` | 三种动作的 apply/unapply | `Protocol Command{apply(), unapply()}`；`AddStroke(doc, stroke)`, `EraseStrokes(doc, strokes)`, `ClearAll(doc)`——构造时注入 `Document`，直接增删 `doc.strokes`，`EraseStrokes`/`ClearAll` 自己记住原索引 |
| ~~`app/storage.py`~~ | 已删除（2026-10-20 撤销保存；历史实现见 git `1dffb3c`） | — |
| `app/settings.py` | 配置读写 + 默认值合并 | `Settings(clear_key, color, width_index, scratch_hotkey)`, `load(path)`, `save(path, s)`；非法值回退默认 |
| `app/canvas.py` | 视图 + 输入状态机（**依赖 Qt**） | `SketchView(QGraphicsView)`：`set_tool()`, `set_color()`, `set_width()`, `clear_all()` 走 `ClearAll` 命令，`wheelEvent`/`mouseEvent` 按 §6 |
| `app/toolbox.py` | 工具栏 UI（**依赖 Qt**） | `Toolbox`：信号 `colorPicked(str)`, `widthChosen(float)`, `toolChosen(str)`, `clearRequested()` |
| `app/probe.py` | §6.2 的事件探针，一次性工具 | 独立运行，打印事件类型/坐标/deviceType，不参与主程序 |
| `main.py` | 接线：`AppController`（文档/历史/配置）+ `MainWindow`（工具栏+画布+状态栏） | 读配置 → 建 Document/History → 建 Toolbox/SketchView → 连信号（无定时器、无落盘） |

> 口径更正（2026-10-20）：原定「`main.py` < 80 行」做不到——`AppController` 与 `MainWindow` 合在一个文件里约 215 行。按职责拆成两个类而不是两个文件，是因为它们共享同一个 `Document` 生命周期，拆文件会让接线更绕；接受行数超标。

依赖方向：`main → toolbox/canvas → commands/history/camera/settings → strokes`。禁止反向 import；`canvas` 不读 `settings`（值由 `main` 注入），这样每个模块都能被单独测。

## 11. 性能降级预案（触发才做，不预先实现）

若 §7 的 1000 条阈值实测不过：① 抬笔时把已固化笔迹合并进若干「瓦片」`QGraphicsPathItem`（每 200 条合一）；② 视口外的 item 设 `ItemIgnoresTransformations` 无关，改用 `setVisible(False)` 粗筛包围盒。两条都只在实测失败后启用，避免为不存在的瓶颈做优化。

## 12. 测试与验收

### 逻辑层（pytest，无 Qt）

- `strokes`：抽稀阈值边界（正好 1.5 / 1.49 / 1.51）；`encode→decode` 往返后点数、颜色、宽度逐字段相等（等值断言，不写「应该差不多」）。
- `camera`：`zoom_at` 的锚点不变量——放大后鼠标下的世界点仍在同一屏幕位置；`MIN/MAX` 钳制。
- `history`：push 新动作清空 redo；超 100 丢最老；空栈 `pop_undo()` 返回 `None` 不抛。
- `commands`：`ClearAll` 对 300 条文档 apply 后 `all()` 为空，unapply 后**顺序与颜色完全一致**（不只是条数对）。
- ~~`storage`~~：随保存功能一并删除（2026-10-20）。
- `settings`：非法 `clear_key` 回退 `E`。

### 界面层（用户手工清单）

清单已迁移并持续维护在 `docs/verification/2026-10-20-acceptance.md`（20 步，每步带前置条件与硬判据）。本文不再重复，避免两份清单漂移。

## 13. 环境与安装（需单独批准，本文档不含安装动作）

- 已装在项目内 `.venv`（`E:\SketchPad\.venv`，PySide6 6.12.0 + pytest）；不写系统 Python。
- 已知坑（本机历史）：清华源偶发 403 会让 pip 报 `from versions: none`，此时不能判定「没有 wheel」，要换源核对 Python 3.13 对应的 PySide6 wheel 是否存在后再装。
- Windows 高 DPI：需要显式设 DPI 感知，否则笔坐标与鼠标位置错位（这是坐标 bug，不是显示问题）。

## 14. 实施顺序（下一步交给 writing-plans 细化）

0. PySide6 便携环境安装 + `app/probe.py` 事件探针实测（决定 §6.2 的落笔分支）
1. `strokes` + `camera` + 单测（纯逻辑，可并行）
2. `history` + `commands` + 单测（依赖 1 的数据结构）
3. `settings` + 单测（依赖 1）；`storage` 已删除
4. `canvas` + `toolbox` + `main`（依赖 1–3）
5. 手工清单 §12 全过一遍，缺口回补

## 15. 草稿纸弹窗（R9，排在 MVP 之后）

用户场景：在 Xournal++/Edge 里看 PDF 做题时，随时按一个键弹出一块空白草稿纸算题，做完收起来，不打断看题。

- **进程模型**：主窗关闭 = 隐藏到托盘，**不退出进程**（否则全局热键失效）。托盘菜单：显示主窗 / 新建草稿 / 退出。
- **全局热键**：`RegisterHotKey`（Win32，经 ctypes）+ `QAbstractNativeEventFilter` 收 `WM_HOTKEY`；默认 `Alt+Q`，配置项 `scratch_hotkey`（v1 只支持改 `settings.json`，v1.1 再接键位捕获 UI）。注册失败（被其它软件占用）→ 状态栏/托盘气泡写明"热键被占用"，并回退到程序内快捷键（同键）（仅主窗有焦点时生效），不静默失败。
- **窗口**：`ScratchWindow` 900×600，无边框 + 自绘标题栏（`草稿纸`，齿轮/最小化/最大化/关闭；**置顶图钉隐藏**，因为它恒置顶），与主窗共用 `app/frameless.py` 的边缘缩放和 Win11 圆角；首次弹出位置 = 鼠标所在屏幕的中心（钳在屏幕可用区内），之后记住上次位置。`Esc` 与关闭按钮都只隐藏；再按热键切换显示/隐藏。草稿窗的齿轮打开同一个设置面板，保存后颜色/粗细/清屏键同时作用于两个窗口。
- **内容**：每次切换**不清空**（同一次做题过程里可以反复收起再打开）；退出进程即丢。草稿窗有自己的 `History(100)`；`E` 清屏同样可撤销。
- **工具栏**：复用 Toolbox 的精简版（撤销 / 重做 / 画笔 / 橡皮 / 4 档粗细 / 8 色 / 清屏）；撤销/重做图标驱动草稿窗自己的 `History(100)`，清屏图标清空草稿画布，都不碰主窗。**没有置顶图钉**——草稿窗固定 `WindowStaysOnTopHint`，始终置顶。不放"保存"。
- **不做**：草稿窗存盘、多草稿标签页、把草稿抄进主草稿。

分模块：
- `app/hotkey.py`：`parse_hotkey(text) -> (mods, vk)`（纯函数，可测："Ctrl+Alt+S" → MOD_CONTROL|MOD_ALT, ord("S")）；`GlobalHotkey(QObject)` 负责注册/注销与信号 `activated`。
- `app/scratch.py`：`ScratchWindow(QWidget)` + `TrayIcon`。
- `main.py`：接线、托盘、`closeEvent` 改隐藏。

验收（追加到 §12 清单）：
- 在 Xournal++ 窗口里按 `Alt+Q` → 草稿窗浮到最前且置顶；再按一次 → 隐藏；再按 → 内容还在。
- 在草稿窗里画一笔 → 主窗文档不变（草稿窗是独立的内存文档，两窗都不写盘）。
- 关掉主窗 → 托盘图标还在，热键仍能弹草稿窗；托盘"退出"后进程结束、热键失效。
