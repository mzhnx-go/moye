# SketchPad v4：多格式阅读器 + 转换中心（方案）

> **状态**：规划中，未开始实现（2026-10-22）。
> **目标**：一个窗口里看 PDF / Markdown / TXT / Word / 思维导图；Word 与 PDF 一样可批注，并能在软件内做**基础文字修改**；内置常用文档转换，不再为不同格式装一堆软件。
> **产品边界**：本方案只进入 **SketchPad Standard**。Lite 保持现有 PDF/笔记/压感功能、只做轻量化维护。双产品拓扑与迁移顺序见 `docs/architecture/2026-10-22-lite-standard/`。
> **参考**：MarkText（CommonMark/GFM 阅读体验、大纲导航）、iLovePDF（转换功能清单与操作流）；只借产品思路，不复制代码。

---


## 0. 双产品边界（2026-10-22 追加）

- 本方案只属于 **SketchPad Standard**；**Lite** 保持现有 PDF/笔记/压感功能，只做轻量化维护，不引入本方案依赖。
- 仓库采用 **monorepo + 共享核心 + 双产品入口**（不是长期双分支/复制仓库）：
  - `sketchpad/` 共享核心：现有 PDF、笔迹、笔记、设置、通用 UI。
  - `sketchpad/reader|word|convert` 仅 Standard 导入。
  - `products/lite`、`products/standard` 各自入口、构建 spec、产品测试。
- 产品命名/配置/单实例锁/默认热键按 **product × channel** 隔离；Lite 与 Standard 可同时运行。
- 详细决策、迁移切片与验收门：`docs/architecture/2026-10-22-lite-standard/`。
- 版本线：Lite 3.x；Standard 4.x（单一完整依赖包，含随包 LibreOffice 与 OCR）。
- **多文档标签页 + 双文档分屏是共享核心功能**（Lite/Standard 都有），先于 Standard 查看器实现；方案见 `docs/architecture/2026-10-22-lite-standard/migration-roadmap.md` 的 M2.5。

## 1. 范围与非目标

### 做

- 阅读：PDF、`.md/.markdown`、`.txt`、`.docx/.doc`、`.mm`、`.opml`、`.xmind`。
- **Word 与 PDF 同等的批注能力**：手写、荧光笔、橡皮、文字选择复制、查找、笔记卡片、导出带批注的 PDF。
- **Word 基础编辑**：改文字、粗/斜体/下划线、标题、列表、基础图片；保存 docx/另存副本。不做完整 Word。
- **转换中心**：合并、拆分、旋转、压缩、PDF↔图片、Office→PDF、PDF→TXT、PDF→DOCX（文字版）、Markdown/HTML→PDF、加密/解密、页码/水印、OCR（扫描件）。
- 非 PDF 格式的大纲侧栏、查找、缩放、拖放打开、最近打开、命令行/单实例转发。

### 不做（v4）

- 编辑 Markdown / 思维导图正文（只读）；完整 Word 能力（页眉页脚、批注、修订、复杂域/宏）不做；`.doc` 先转 docx 再编辑。
- PDF→Excel / PDF→PPT（免费方案版式损失大，先不做）。
- 在线服务、云同步、账号体系。
- 插件框架；扩展仍走 `settings.json` 未知字段保留 + 清晰的适配器接口。

---

## 2. 关键决策

1. **Word 双模式**：
   - **批注模式**：Word → 缓存 PDF → 现有 `PdfView`，手写/高亮/笔记/sidecar/导出全部复用；原 Word 永不修改，导出“带批注的 PDF”。
   - **编辑模式**：DOCX 为基础数据源，QTextDocument/QTextEdit WYSIWYG 编辑；默认保存副本，覆盖前备份；`.doc` 先转 docx。
   - 两模式切换会提示：编辑改变版式后，旧 PDF 批注可能错位，不回写批注。
2. **默认零安装、可分发**：标准版**不要求用户装 Word / LibreOffice / Tesseract**。docx 用 Mammoth（MIT）转 HTML，再用 Qt `QTextDocument` 生成缓存 PDF；纯 Python 依赖随 PyInstaller 一起打包。
3. **Standard 单一完整包**：Python 依赖全装（开发环境同同一份 requirements），随包 **LibreOffice Portable（MPL-2.0）** 用于 `.doc/.docx/.xls/.xlsx/.ppt/.pptx` 高保真转换与 `.doc` 批注，OCR 模型随包；不再区分标准/完整两个发行物。
4. **系统引擎只作加速**：检测到系统 LibreOffice / Microsoft Word 时可优先用，但**不是分发前提**；没有也能用标准版。
5. 本机现状（2026-10-22 实测）：未安装 LibreOffice/pandoc；有 Microsoft Word 16；Tesseract 在 `D:\Tesseract`（chi_sim/eng）。本机测试可用 Word COM，但方案不能依赖它。
4. **只读阅读器**：MD / TXT / 导图不做编辑；Word 只做基础编辑。不引入 Qt WebEngine，保持包体可控。
5. **转换输出永不覆盖源文件**：默认写在源文件同目录，文件名加操作后缀；失败不产生半截文件。

---

## 3. 版本分段

| 版本 | 内容 | 验收 |
|---|---|---|
| **v4.0 产品骨架 + 阅读器外壳 + TXT/Markdown** | 双产品入口/构建/隔离；多查看器分发、工具栏自适应、大纲/查找/缩放、拖放/最近/命令行/单实例 | Lite 功能与体积不回归；md/txt 打开即读；非 PDF 不创建 sidecar |
| **v4.1 Word 阅读 + 批注 + 基础编辑** | docx/doc → 缓存 PDF → PdfView；sidecar 绑原文件 + 指纹；DOCX WYSIWYG 基础编辑（另存/备份）；导出批注 PDF | Word 能落笔/高亮/笔记；能改文字并保存；源文件 sha256 不变（未确认覆盖时）；源文件改动后提示批注可能错位 |
| **v4.2 转换中心** | 第 5 节矩阵中的 PDF/图片/文本/Markdown/Office 转换 | 每项有 fixtures 测试；批量进度/取消；不覆盖源文件 |
| **v4.3 思维导图** | FreeMind `.mm`、OPML、XMind（Zen/8）只读图形树 + 大纲 | 三种格式可打开、可缩放/平移、节点标题可搜索 |
| **v4.4 打磨** | 每文件记住阅读位置/缩放、转换结果打开目录、错误提示统一 | 全量回归 + 人工验收 |

依赖顺序：**v4.0 → v4.1 → v4.2 → v4.3 → v4.4**。v4.1 的 Word→PDF 转换器与 v4.2 的 Office→PDF 共用同一个 `OfficeConverter`。

---

## 4. 架构

### 4.1 查看器分发

```
MainWindow
 ├─ 草稿模式（SketchView，不变）
 └─ 文档模式
     ├─ pdf       → PdfView（现有；批注/笔记/sidecar 原样）
     ├─ text      → TextView（TXT）
     ├─ markdown  → TextView（markdown-it-py → HTML）
     ├─ word      → 转换后的 PDF → PdfView；失败时 TextView（Mammoth HTML，只读）
     └─ mindmap   → MindMapView（mm/opml/xmind 只读图形树）
```

统一接口（`sketchpad/ui/viewers/base.py`）：

```python
class DocumentViewer(QWidget):
    def open(self, path: Path) -> None: ...
    def outline(self) -> list[tuple[int, str, object]]: ...
    def find(self, text: str) -> int: ...
    def find_next(self) -> int: ...
    def set_zoom(self, factor: float) -> None: ...
    def fit(self) -> None: ...
    def position(self) -> dict: ...          # 供 v4.4 记忆
    def restore_position(self, data: dict) -> None: ...
```

主窗只记 `viewer_kind` 与当前文件；PDF 专属动作（保存批注/导出/自动暂存）只在 `pdf` 与 `word`（转 PDF 后）启用。

### 4.2 Word 流水线

```
打开 .docx/.doc
  → 计算指纹 sha256(path + mtime_ns + size)[:16]
  → 缓存命中 data/converted/<指纹>/<原名>.pdf ？直接打开
  → 转换优先级（标准版也至少有一条自带路径）：
      1) 随包/系统的 LibreOffice（Standard 随包，MPL）
      2) 系统 Microsoft Word COM（本机有则用，非分发前提）
      3) 内置 Mammoth → HTML → QTextDocument → PDF（docx 兜底，纯 Python + Qt）
  → PdfView.open(缓存 PDF)
  → sidecar 写在原 Word 旁边：<原名>.docx.sketch.json（带 fingerprint 字段）
```

- **Standard**：随包 LibreOffice Portable，`.doc/.docx/.xls/.xlsx/.ppt/.pptx` 高保真转换与批注，无任何外部安装；LibreOffice 不可用时仍用 Mammoth+Qt 兜底 docx。
- 兜底路径生成 PDF 用 Qt `QTextDocument.print_` 到 `QPrinter`（系统 CJK 字体，中文可选中）；转换在 UI 线程做并显示进度，超大文档分批。

- 源 Word 改动（指纹变化）→ 重新转换；若已有批注，提示“版式可能变化，批注位置可能错位”，让用户选择继续/另存旧批注。
- 导出：默认 `<原名>-批注.pdf`（由转换后的 PDF 生成）；**不回写 Word**（v4 明确不做）。
- 无 Word/LO：用 `mammoth` 渲染 HTML 到 TextView，页面上方提示“只读：未检测到 Word/LibreOffice，无法批注与转换”。
- 缓存清理：设置里“转换缓存上限（MB）”，启动/退出按 LRU 清理；不缓存用户目录之外的临时文件。

### 4.3 转换服务

- `sketchpad/services/convert.py`：无 Qt，纯文件操作，可单测。
- `sketchpad/services/office.py`：探测 LibreOffice / Word COM，统一 `to_pdf(src, out_dir) -> Path`。
- `sketchpad/ui/convert_dialog.py`：选操作 → 选文件（可多选）→ 选项 → 输出目录 → 进度条/取消。
- 长任务放 `QThread`（参考现有 `services/search.py` 的取消/generation 模式），UI 不冻结。
- 所有中间文件写 `%TEMP%/SketchPad-convert-<pid>/`，结束清理。

---

## 5. 转换功能矩阵（v4.2）

| 操作 | 实现 | 依赖/许可 | 备注 |
|---|---|---|---|
| PDF 合并 | PyMuPDF `insert_pdf` | 已有（AGPL） | 按选择顺序；输出 `<首文件名>-合并.pdf` |
| PDF 拆分 | PyMuPDF 提取页范围 | 已有 | 支持“每 N 页一份 / 指定范围（如 1-3,5,8-）” |
| PDF 旋转 | PyMuPDF `set_rotation` | 已有 | 90/180/270，可整份或指定页 |
| PDF 压缩 | PyMuPDF `save(garbage=4, deflate=True)` + 图片降采样 | 已有 | 提供“轻/中/高”三档；显示前后大小 |
| PDF→图片 | PyMuPDF `get_pixmap` | 已有 | PNG/JPG，DPI 可调（默认 150） |
| 图片→PDF | PyMuPDF `insert_image` | 已有 | 多图按文件名排序；可选页面大小 |
| Office→PDF | Standard 内置 LibreOffice；Mammoth+Qt 作 docx 兜底 | Mammoth（MIT）/ LO（MPL） | 无外部安装要求；LO 异常时 docx 仍可读 |
| PDF→TXT | PyMuPDF `get_text` | 已有 | 保留分页分隔线 |
| PDF→DOCX（文字版） | PyMuPDF 取文本 + python-docx | python-docx（MIT） | 低保真：只保留段落/分页，不保证版式/图片 |
| Markdown/HTML→PDF | markdown-it-py + PyMuPDF `Story` | markdown-it-py（MIT） | 支持标题/列表/表格/代码块/图片；不追求浏览器级 CSS |
| PDF 加密/解密 | PyMuPDF `save(encryption=...)` / 打开后另存 | 已有 | 加密需用户输入密码；解密需已知密码 |
| 页码/水印 | PyMuPDF `insert_text` | 已有 | 页码位置/字体；水印文字+透明度 |
| OCR（扫描件→可搜索 PDF） | 渲染页 → **随包 RapidOCR（ONNX，Apache-2.0）**；检测到系统 Tesseract 也可用 | RapidOCR/Apache-2.0（模型随包） | 不要求用户安装；只对纯图片页启用；中文+英文模型随标准版打包 |

**先不做**：PDF→Word（版式还原）、PDF→Excel、PDF→PPT、电子签名、云转换。它们要么依赖商业引擎，要么免费方案失真过大。

---

## 6. UI 与交互

- **打开文件**过滤：PDF / Markdown / 文本 / Word / 思维导图；拖放、最近、命令行、单实例转发同一套。
- **多文档工作区**：草稿固定第一页；每个文档一个标签，独立撤销/缩放/滚动/未保存状态；两个文档可合并为 `文件1 | 文件2` 的左右分屏，点击某栏切换活动文档。
- **工具栏自适应**：
  - PDF / Word(转 PDF)：现有批注工具全保留。
  - TXT / Markdown / 导图：隐藏画笔/高亮/橡皮/颜色/粗细/笔记，保留查找/大纲/缩放。
- **大纲侧栏**：Markdown 标题、Word 转换后 PDF 的目录（若有）、导图节点树。
- **转换中心入口**：文件菜单「转换…」+ 工具栏一个转换图标；对话框不是设置面板。
- **分屏**：现有 PDF/笔记分屏保持不变；非 PDF 查看器不参与笔记分屏（v4 不做跨格式并排）。

---

## 7. 数据与设置

| 数据 | 位置 | 说明 |
|---|---|---|
| 转换缓存 | `data/converted/<指纹>/` | Word/Office→PDF 的中间 PDF；可清 |
| Word 批注 | 原文件旁 `<原名>.docx.sketch.json` | 含源指纹；批注坐标基于转换后的 PDF |
| 阅读位置 | `settings.json` 的 `doc_positions` | 所有格式共用；v4.4 |
| 转换输出 | 源目录（可改） | 永不覆盖源文件 |

新增设置（简单项）：
- Word 转换引擎：自动 / LibreOffice / Microsoft Word（默认自动）。
- 转换后打开所在文件夹（复用现有 `open_export_folder`）。
- OCR 语言：中文+英文 / 仅英文（默认 chi_sim+eng）。
- 转换缓存上限：512MB（默认），LRU 清理。

---

## 8. 测试策略

- fixtures 全部现场生成：md/txt、docx（zip 构造）、mm/opml/xmind（xml/json/zip）、图片、两页 PDF。
- 单元：格式识别、编码回退、Markdown 标题提取、docx 转换器探测（mock `soffice`/COM）、XMind 两种结构解析、转换选项校验。
- 集成（offscreen）：打开各格式、查找、缩放、大纲、Word 无引擎提示、转换输出存在且源文件哈希不变、批量取消。
- 不把 Word/LO 作为 CI 必装依赖：转换器用 fake/monkeypatch；本机人工验真实转换。
- 全量仍分两半跑；每批结束报“集合数 = 通过数”。

---

## 9. 风险与应对

| 风险 | 应对 |
|---|---|
| Word 版式依赖转换器，与 Word 里看到的不完全一致 | 明确“批注 PDF 是转换后的版式”；导出也是 PDF；需要原版式时用 Word 打开 |
| 源 Word 修改后批注错位 | 指纹变化时提示；旧批注 sidecar 保留/可另存，不静默覆盖 |
| 分发用户没有 Word/LibreOffice | Standard 随包 LibreOffice，另有 Mammoth+Qt 的 docx 兜底；外部安装只作加速 |
| OCR 慢/误识别 | 只对无文字页启用；后台线程 + 可取消；语言包缺失时明确提示 |
| 包体变大 | Standard 接受约 700–800MB（含 LibreOffice + OCR 模型）；Lite 不背这些依赖（约 250–350MB） |
| AGPL（PyMuPDF）已有约束 | 沿用现有许可说明；新增依赖逐项记录 MIT/BSD/Apache |

---

## 10. 里程碑验收

1. **v4.0**：md/txt 打开、目录树、查找、缩放、拖放/最近；非 PDF 不建 sidecar；全量绿。
2. **v4.1**：本机用 Word COM 打开 docx/doc → 批注 → 导出 PDF；源文件哈希不变；无引擎模拟下只读提示。
3. **v4.2**：表格中 13 项转换各有成功/失败用例；批量取消；输出命名与不覆盖。
4. **v4.3**：mm/opml/xmind 图形树可读、可缩放、标题搜索。
5. **v4.4**：位置记忆、错误提示、完整回归与人工验收。


---

## 11. 可分发性（2026-10-22 追加）

| 发行包 | 包含 | 体积（估） | Word 批注 | 适用 |
|---|---|---|---|---|
| **标准版** | PySide6、PyMuPDF、markdown-it-py、mammoth、python-docx、openpyxl、python-pptx、RapidOCR | 约 350–450MB | docx：Mammoth+Qt 阅读版式；doc：只读提示 | 普通用户，解压即用，零安装 |
| **Standard** | 全部 Python 依赖 + LibreOffice Portable + OCR 模型 | 约 700–800MB | docx/doc 高保真；Office 全家桶→PDF；可编辑 docx | 需要多格式/Word/转换的用户 |

- 依赖全部为 MIT / BSD / Apache-2.0 / MPL-2.0；PyMuPDF 仍是 AGPL-3.0（项目既有约束，分发时按 AGPL 合规）。
- PyInstaller 打包时把字体/模型目录一并收进 one-folder；OCR 模型放 `models/`，可单独替换。
- 自动选择：随包 LibreOffice → 系统 LibreOffice → Word COM → Mammoth；标准版永远有最后一条，不会因缺组件变砖。
- Standard 只有一个 zip；构建脚本 `tools/build_standard.py` 负责收进 LibreOffice 与模型目录。Lite 单独由 `tools/build_lite.py` 产出。
