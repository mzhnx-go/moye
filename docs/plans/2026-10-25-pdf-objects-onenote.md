# PDF 图文对象交互续做方案（OneNote 式）

日期：2026-10-25
状态：已完成（2026-10-25；多选与富文本已实现，单文字 Ctrl+B/I/U 真机验证；多选/框选真机待补）
前置：v4.7 已支持 PDF 页面文字/图片插入、单选、移动、缩放、删除、复制、双击编辑、字号修改。

## 1. 现状与 UX 缺口

已具备：

- 图片/文字点击选中，图片 8 手柄、文字左右 2 手柄；拖身体移动、拖手柄缩放。
- Delete 删除、Ctrl+C 复制、方向键微调、Esc 取消、右键复制/删除/字号。
- 双击文字进入编辑；Ctrl+V 贴图 / 工具栏插图 / 拖入图片文件。
- 所有修改走 `ReplacePageItems`，可撤销/重做。

对照 OneNote 仍缺：

| 能力 | 现状 | 本轮处理 |
|---|---|---|
| 多选（Ctrl/Shift 点选、框选） | 只能单选 | Phase A |
| 批量移动/删除/微调 | 单对象 | Phase A |
| 粗体/斜体/下划线 | 无 | Phase B |
| 字号预设 | 只有右键输入字号 | Phase B |
| 旋转/裁剪/替换图片 | 无 | 暂不做 |
| 多选复制到剪贴板 | 无 | 仅单选复制；多选复制暂不做 |
| 图层顺序/文字绕排 | 固定层级 | 暂不做 |

## 2. Phase A：多选与批量操作

### 交互

- 单击对象：只选它（原行为不变）。
- `Ctrl+单击`：切换该对象是否在选中集合中；`Shift+单击`：加入选中集合。
- 选中集合 > 1：画一个整体蓝色包围框，不显示缩放手柄；拖任一对象移动全部选中对象。
- `Delete/Backspace`：删除全部选中对象（一条撤销记录）。
- 方向键：全部选中对象一起微调 1pt（Shift 10pt）。
- `Esc`：清空选中集合。
- `Ctrl+C`：仅单选时复制（图片进剪贴板 / 文字进剪贴板）；多选时不复制，状态栏提示“多选暂不支持复制”。
- 右键：多选时显示“删除选中项”。
- `Ctrl+左键拖空白`：框选（虚线蓝框），选中与框相交的图片/文字；普通左键拖空白仍是平移，不改原有习惯。

### 实现

- 选择状态改为 `list[(page_index, kind, id)]`，保留单选手柄逻辑。
- 新增 `ReplaceMany` 组合命令（`core/pdfcommands.py`）：一次 apply/unapply 多个 `(page, field, before, after)` 快照；`page_index = None` 让历史回调重绑全部页。
- 批量移动/删除/微调/格式修改都用一条 `ReplaceMany`。
- 多选包围框复用现有选中 overlay；单选时仍显示 8/2 手柄。
- 只改 `PdfView` 的选择/鼠标/键盘/右键逻辑，不动文字渲染、图片渲染和导出。

### 测试

- Ctrl 点选两个对象；Shift 加入；Esc 清空。
- 框选相交对象；空白普通拖动仍平移。
- 多选移动、方向键、Delete、撤销/重做。
- 单选缩放手柄仍工作；原单选测试全部通过。

## 3. Phase B：文字格式

### 数据

`core.notes.TextBlock` 末尾新增：

```python
bold: bool = False
italic: bool = False
underline: bool = False
```

- `pdfstore` 解析可选 `"b" / "i" / "u"`；为 `False` 时不写 JSON，保持旧输出字节不变；只在有格式时仍写 v4。
- 位置参数兼容旧代码。

### 交互

- 选中单个文字对象：`Ctrl+B / Ctrl+I / Ctrl+U` 切换整段格式；右键菜单加“粗体 / 斜体 / 下划线 / 字号 ▸ (12/16/20/24/32)”。
- 文字编辑中：Ctrl+B/I/U 切换整段格式，编辑器字体即时变化；提交时写入模型。
- 多选文字：格式切换应用到全部选中文字（一条撤销记录）。
- 图片不参与格式操作。

### 渲染与导出

- 屏幕：`QFont.setBold/setItalic/setUnderline`，编辑器与只读投影一致。
- 导出：
  - 无格式：保持现有 `insert_textbox` 文本路径（文字可搜索）。
  - 有格式：用 `QTextDocument` 按当前字号/颜色/粗斜下划线渲染成透明 PNG（约 300dpi），插入同一页面矩形；保证中文粗斜下划线与屏幕一致。该块导出后不是文本层，这是保真换来的取舍。
- 不引入外部字体/依赖。

### 测试

- 模型/JSON 往返，旧 v1–v4 无格式字段仍可读；无格式输出字节不变。
- QFont 标志、编辑器标志、模型写入。
- Ctrl+B/I/U 对单选与多选、撤销/重做。
- 导出：无格式仍能 `get_text()` 找到；有格式导出后图片数增加且页面可见。

## 4. 任务拆分（并行 worktree）

- Agent A（multi-select）：`sketchpad/ui/pdfview.py` 选择/鼠标/键盘/右键、`sketchpad/core/pdfcommands.py` 组合命令、`tests/test_pdf_multi_select.py`。
- Agent B（rich-text）：`sketchpad/core/notes.py`、`sketchpad/core/pdfstore.py`、`sketchpad/pdf/document.py`、`sketchpad/ui/pdfview.py` 文字投影/编辑器/格式快捷键/右键、`tests/test_pdf_rich_text.py`。
- 冲突点预计在 `PdfView.keyPressEvent` 与右键菜单：按“各加各的分支”方式合并。

## 5. 验收

- 原有 PDF/图文对象测试全过。
- 新增多选/格式测试全过。
- 真机副屏：Ctrl 多选、框选、批量移动/删除；粗体/斜体/下划线实时显示；导出副本外观一致。
- 不打包；完成后更新 `docs/REPORT.md` 与项目记忆。

## 6. 本轮不做

- 旋转、裁剪、替换图片
- 图层顺序、文字绕排
- 多选复制到剪贴板
- 富文本段落级格式（局部加粗/标题/列表）
