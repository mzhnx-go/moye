# 墨页 · Moye

![Moye icon](docs/assets/icon.png)

**本地优先的 Windows PDF 批注器与无限草稿板。**  
用 PySide6 + PyMuPDF 写的桌面应用：把 PDF 当纸批注、把草稿纸当草稿纸，所有内容留在本机，不上传、不需要 Office。

Moye is a local-first PDF annotation and scratchpad desktop app for Windows, built with PySide6 and PyMuPDF.

- **不修改原 PDF**：批注写入同目录的 `<文件名>.sketch.json`，导出时另存带笔迹的副本。
- **不需要 Office**：Word/PDF/图片/OCR/格式转换全部本地完成。
- **离线**：没有云端、账号、遥测；图片和 OCR 也不出本机。
- **单文件可搬**：源码或 PyInstaller 便携版，配置跟着程序目录走。

## 功能

| 类别 | 内容 |
|---|---|
| PDF 批注 | 钢笔 / 荧光笔 / 橡皮 / 文字 / 图片 / 笔记；逐页撤销；导出带笔迹的 PDF |
| 笔记 | 可拖动、缩放、分屏的笔记卡片；手写 + 富文本 + 图片 + 附件 |
| 多格式阅读 | PDF、TXT、Markdown、Word（`.docx`）、FreeMind `.mm`、OPML、XMind（新旧格式） |
| Word | 批注模式只读原文件；应用内编辑器支持基础文字/标题/项目符号/图片/表格编辑 |
| 截图 | 区域/窗口/全屏/上一区域截图，截图内直接标注、复制、保存、贴图、OCR |
| 全屏批注 | 覆盖在所有窗口之上的透明批注层，支持穿透模式 |
| 转换中心 | PDF 合并/拆分/旋转/压缩、PDF↔图片、Word/Markdown/HTML→PDF、加密/解密、页码/水印、OCR |
| 桌面体验 | 多标签、双文档分屏、最近打开、目录/缩略图、查找、阅读位置记忆、启动动画 |

### 截图与全屏批注

- 主实例全局 `Ctrl+1`：任何程序前台时都能呼出区域截图。
- `Ctrl+Alt+D`：全屏批注层显隐/穿透切换；`Ctrl+Alt+H`：隐藏整层。
- 截图和批注都在本机完成，OCR 使用本地模型。

## 运行环境

- Windows 10 / 11（当前只支持 Windows）
- Python **3.13**
- 依赖版本已固定：PySide6 6.12.0、PyMuPDF 2.0

## 快速开始

```bat
git clone https://github.com/mzhnx-go/moye.git
cd moye
cd moye
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe launch.py
```

也可以双击 `run.bat`：先运行启动自检，再用 `pythonw.exe` 打开，不弹控制台。

只检查依赖与导入（不建窗口、不写配置）：

```bat
.venv\Scripts\python.exe launch.py --check
```

## 打包

```bat
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.venv\Scripts\python.exe tools\build_app.py
```

输出在 `dist\墨页\`。构建脚本会把 `sketchpad/assets` 一起带进 `_internal`。

## 数据放在哪里

| 数据 | 位置 |
|---|---|
| 设置、最近打开、阅读位置、自动暂存、启动日志 | 程序目录下的 `data/`（便携版是 exe 同级） |
| PDF 批注 | PDF 同目录的 `<文件名>.sketch.json` |
| 笔记图片/附件 | PDF 同目录的 `<文件名>.sketch-assets/` |
| 草稿 | 仅内存，退出即丢（有意为之） |

分享便携版前请删除 `data/`，里面可能有本机最近打开记录。

## 常用快捷键

| 键 | 作用 |
|---|---|
| `Ctrl+O` | 打开文档 |
| `B` / `Q` | 画笔 / 橡皮 |
| `H` / `V` | 荧光笔 / 鼠标（平移、选字、移动笔记图标） |
| `T` / `N` | 页面文字 / 插入笔记 |
| `Ctrl+S` | 保存批注（sidecar） |
| `Ctrl+Shift+E` | 导出带笔迹的 PDF |
| `Ctrl+F` | 查找（回车搜索，F3/Shift+F3 切换命中） |
| `Ctrl+1` | 全局区域截图（仅第一个启动的实例） |
| `Alt+Q` / `Alt+W` | 全局呼出草稿纸 / 主窗（仅主实例） |
| `Ctrl+Alt+D` | 全屏批注显隐/穿透 |
| `Ctrl+Alt+H` | 隐藏全屏批注层 |

更多按键见应用内「设置 → 快捷键」和「帮助 → 快捷键一览」。

## 多开与退出

- 每次启动都是独立进程；第一个启动的实例（主实例）独占托盘、全局热键和全屏批注，后续实例标题带「（副实例）」。
- 同一份 PDF/Word 只允许一个实例打开，避免批注互相覆盖。
- 文件菜单/托盘里的 **退出全部实例** 会通过跨进程消息关闭所有墨页进程；关窗时选择「最小化到托盘」则会保留后台进程。
- 更新代码后若要验证新版本，请从托盘「退出全部实例」，不要只关窗口。

## 目录结构

```
launch.py                 启动自检、崩溃日志、错误弹窗
main.py                   兼容入口
run.bat                   双击启动
sketchpad/
  app.py                  窗口、控制器、接线
  core/                   笔迹、命令、sidecar、配置
  pdf/                    PDF 渲染、搜索、导出
  reader/                 Markdown/TXT/思维导图阅读器
  word/                   Word 批注与基础编辑器
  convert/                本地转换中心
  annotate/               全屏批注层
  screenshot/             截图、贴图、OCR、历史
  services/               全局热键、单实例、文档锁、自动暂存、搜索线程
  ui/                     画布、PDF 视图、工具栏、对话框、启动动画
tests/                    离屏单元测试
tools/                    构建、性能与验收脚本
docs/                     设计与验证文档
data/                     运行时配置（不提交）
```

## 开发与测试

```bat
rem 全量测试（Qt 使用 offscreen 平台）
.venv\Scripts\python.exe -m pytest tests/ -q

rem 性能脚本（会创建真实窗口）
.venv\Scripts\python.exe tools\perf_pdf.py

rem 启动自检
.venv\Scripts\python.exe launch.py --check
```

## 素材说明

本仓库默认使用一个**通用文档+钢笔图标**。公开仓库不包含任何第三方动漫角色素材；如果你要替换自己的图标/启动立绘：

| 文件 | 用途 | 是否必需 |
|---|---|---|
| `sketchpad/assets/app.png` | 应用图标 / 启动动画缺省头像 | 可选，缺失时回退到代码绘制图标 |
| `sketchpad/assets/app.ico` | Windows 打包图标 | 可选，构建前请自行生成 |
| `sketchpad/assets/splash_girl.png` | 启动动画圆形头像 | 可选，缺失时回退到 `app.png` |

请只使用你有权分发的素材。启动动画代码会裁剪圆形头像并叠加墨环、星光、音符和加载进度。

## 许可

本项目以 **GNU Affero General Public License v3.0 or later（AGPL-3.0-or-later）** 发布，见 [`LICENSE`](LICENSE)。

这不是随意选择：项目使用 **PyMuPDF**（AGPL-3.0）进行 PDF 渲染。若你要闭源分发或商用，需要：

1. 取得 PyMuPDF 商业授权，或
2. 替换为许可兼容的 PDF 引擎（例如 pypdfium2 + pypdf），再重新评估项目许可证。

请不要直接把本项目改成 MIT 后分发包含 PyMuPDF 的二进制。

### 第三方组件

| 组件 | 用途 | 许可 |
|---|---|---|
| PySide6 / Qt | GUI | LGPL-3.0（动态链接） |
| PyMuPDF | PDF 渲染/导出 | AGPL-3.0 |
| RapidOCR / ONNX Runtime | 本地 OCR | Apache-2.0 / MIT（模型与依赖以各自发布页为准） |
| markdown-it-py | Markdown 渲染 | MIT |
| Mammoth | Word → HTML | BSD-2-Clause |
| python-docx / openpyxl / python-pptx | Office 文档处理 | MIT |
| PyInstaller | 打包（仅构建期） | GPL-2.0-or-later with exception |

具体版权与许可文本以各上游项目为准；分发二进制时请一并附带对应许可通知。

## 已知限制

- 仅支持 Windows；全局热键、托盘、多开是 Windows 桌面能力。
- 纯扫描 PDF 没有文本层，查找/选字需要先 OCR（这是文档本身限制，不是选择功能失效）。
- 草稿不落盘；笔记/全屏批注也有会话边界，退出前请按提示保存需要保留的内容。
- 便携版配置和最近记录不跨目录共享；重新打包后请把旧 `data/` 复制到新目录。
- OCR 与 Word 能力依赖较大的本地模型/库，便携包体积约数百 MB。

## 贡献

欢迎 Issue / PR。提交前请至少运行：

```bat
.venv\Scripts\python.exe -m pytest tests/ -q
.venv\Scripts\python.exe launch.py --check
```

涉及 GUI 的改动请附上实际窗口截图或说明真机验证步骤；涉及批注数据格式的改动请同时补测试与兼容性说明。
