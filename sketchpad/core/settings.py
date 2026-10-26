"""settings.json：坏字段各自回退默认，不整份丢弃。导入期不依赖 Qt（校验热键时才延迟导入）。"""
from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


DEFAULT_CLEAR_KEY = "E"
DEFAULT_COLOR = "#e8eaed"   # 深灰画布上的默认笔色，与 #202124 有对比
HIGHLIGHTER_COLOR = "#d4a017"   # 深黄：荧光笔默认色，与画笔颜色分开保存
DEFAULT_WIDTH_INDEX = 1
DEFAULT_HOTKEY = "Alt+Q"        # 源码/打包共用同一组热键
DEFAULT_MAIN_HOTKEY = "Alt+W"   # 呼出主窗；与草稿纸 Alt+Q 相邻
LEGACY_DEFAULT_HOTKEY = "Ctrl+Alt+D"   # 旧默认：读到它时自动迁到新默认
# 自动保存（崩溃恢复缓存）间隔：分钟。默认关闭；开启后默认 5 分钟。
AUTOSAVE_INTERVAL_PRESETS = (1, 5, 10, 20, 60)
DEFAULT_AUTOSAVE_INTERVAL_MIN = 5
MIN_AUTOSAVE_INTERVAL_MIN = 1
MAX_AUTOSAVE_INTERVAL_MIN = 24 * 60
WIDTHS = (1.5, 2.0, 4.0, 8.0)
# 阅读模式（PDF 底图）：正常 / 夜间反相 / 护眼暖色；存在这里让 settings 保持无 Qt 依赖
PAGE_MODES = ("normal", "night", "eye")
# 抬笔后的笔迹平滑强度；off 保留原始折线，high/ultra 在抬笔后做曲线圆角消除颗粒感
INK_SMOOTHING_LEVELS = ("off", "low", "medium", "high", "ultra")
# 清屏键不能占用的组合：前 5 个是系统/编辑快捷键，随后 7 个是画布/文档工具键（含笔记 N），
# 末尾 8 个是 v2 文档模式快捷键（app.py 的 QShortcut 表）
RESERVED_KEYS = ("Ctrl+C", "Ctrl+V", "Ctrl+Z", "Ctrl+Y", "Ctrl+A",
                 "Space", "0", "B", "H", "Q", "V", "N",
                 "Ctrl+O", "Ctrl+S", "Ctrl+Shift+E", "PgUp", "PgDown",
                 "Ctrl+G", "Ctrl+F", "Ctrl+,",
                 "Ctrl+=", "Ctrl+-", "Ctrl+0", "Ctrl+9", "Ctrl+Shift+Z",
                 "F3", "Shift+F3", "F11", "Home", "End")   # 设计文档 §6.3 + v2.4 UX 新键
# 自定义键只认这些：单个 ASCII 字母数字、F1-F24、具名编辑键
KEY_NAMES = frozenset({"space", "delete", "esc", "tab", "home", "end", "pageup",
                       "pagedown", "insert", "up", "down", "left", "right"})
MODIFIERS = frozenset({"ctrl", "shift", "alt", "meta", "win"})
_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
# 同一个键的不同写法：Qt 规范名（PgUp）与配置里的手写名（PageUp/PgDn）等价
_KEY_ALIASES = {"pageup": "pgup", "pagedown": "pgdown", "pgdn": "pgdown",
                "escape": "esc", "del": "delete", "control": "ctrl"}


@dataclass
class Settings:
    clear_key: str = DEFAULT_CLEAR_KEY
    color: str = DEFAULT_COLOR
    width_index: int = DEFAULT_WIDTH_INDEX
    scratch_hotkey: str = DEFAULT_HOTKEY
    width: float = WIDTHS[DEFAULT_WIDTH_INDEX]     # 自定义粗细（滑杆），预设仍由 width_index 记
    pdf_autosave: bool = False                     # PDF 批注自动保存（崩溃恢复缓存，默认关）
    pdf_autosave_interval_min: int = DEFAULT_AUTOSAVE_INTERVAL_MIN
    recent_files: list[str] = field(default_factory=list)   # 最近打开的 PDF，最多 10 条
    toolbar_position: str = "top"                  # 工具栏位置：top=顶部横排 / right=右侧竖排
    toolbar_collapsed: bool = False                # 工具栏收起状态（v2.8 折叠，v2.9 随位置一起记）
    main_hotkey: str = DEFAULT_MAIN_HOTKEY         # 全局热键：呼出主窗
    # 每份 PDF 的阅读位置：归一化绝对路径 → page/offset/zoom/auto_fit/saved_at
    pdf_positions: dict[str, dict] = field(default_factory=dict)
    page_mode: str = "normal"                      # PDF 底图阅读模式：normal/night/eye
    ink_smoothing: str = "low"                     # 抬笔平滑：off/low/medium/high/ultra
    highlighter_color: str = HIGHLIGHTER_COLOR     # 荧光笔颜色，与画笔 color 分开存
    pressure_enabled: bool = True                  # 数位板压感（鼠标输入自动退化为恒定线宽）
    note_paper: str = "dark"                      # 新建笔记默认纸色：dark/light
    close_to_tray: bool = True                     # 关闭主窗：True=收进托盘 / False=直接退出
    close_prompt: bool = True                      # 关闭主窗时先弹「取消 / 退出 / 最小化」确认
    open_export_folder: bool = False               # 导出 PDF 后自动打开所在文件夹
    # settings.json 里未来/用户自定义的未知字段，原样保留并写回（扩展点，不参与业务）
    extra: dict = field(default_factory=dict)


def _raw(path: Path) -> dict:
    """读不出内容或结构不是对象时给空字典，让每个字段各自走默认值。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _binding_signature(key: str) -> tuple[str, ...]:
    """比较用的签名：小写、去空格、修饰键排序、键名别名归一。

    这样 "Shift+Ctrl+E"、"ctrl + shift + e"、"PgUp"/"PageUp" 都能命中黑名单。
    """
    parts = [part.strip().lower() for part in key.split("+")]
    last = _KEY_ALIASES.get(parts[-1], parts[-1])
    return (*sorted(parts[:-1]), last)


_RESERVED_SIGNATURES = frozenset(_binding_signature(key) for key in RESERVED_KEYS)


def _is_reserved(key: str) -> bool:
    return _binding_signature(key) in _RESERVED_SIGNATURES


# 对话框也用这同一份判定，公开别名免得跨模块引私有名
is_reserved_key = _is_reserved


def _normalise_key(key: str) -> str:
    """去掉 + 两侧空白：QKeySequence 会这么规范化，先统一再比较。"""
    return "+".join(part.strip() for part in key.split("+"))


def _is_key_token(token: str) -> bool:
    """末位必须是真按键：单个 ASCII 字母数字、F1-F24 或具名编辑键。"""
    if len(token) == 1:
        return token.isascii() and token.isalnum()
    low = token.lower()
    if low in KEY_NAMES:
        return True
    return low[:1] == "f" and low[1:].isdigit() and 1 <= int(low[1:]) <= 24


def _is_binding(key: str) -> bool:
    """修饰键（可无）+ 一个真按键；"Mouse"、"Ctrl"、"Ctrl+" 都不算。"""
    parts = key.split("+")
    return _is_key_token(parts[-1]) and all(p.lower() in MODIFIERS for p in parts[:-1])


def _clear_key(data: dict) -> str:
    raw = data.get("clear_key")
    key = _normalise_key(raw.strip()) if isinstance(raw, str) else ""
    if not key or _is_reserved(key) or not _is_binding(key):
        return DEFAULT_CLEAR_KEY
    return key


def _color(data: dict) -> str:
    raw = data.get("color", DEFAULT_COLOR)
    if isinstance(raw, str) and _COLOR_RE.fullmatch(raw):
        return raw
    return DEFAULT_COLOR


def _highlighter_color(data: dict) -> str:
    """荧光笔颜色独立校验；坏值只回自己的默认，不影响画笔颜色。"""
    raw = data.get("highlighter_color", HIGHLIGHTER_COLOR)
    if isinstance(raw, str) and _COLOR_RE.fullmatch(raw):
        return raw
    return HIGHLIGHTER_COLOR


def _width_index(data: dict) -> int:
    raw = data.get("width_index", DEFAULT_WIDTH_INDEX)
    try:
        if isinstance(raw, float) and not math.isfinite(raw):
            raise ValueError(raw)          # NaN/±inf 都当坏值，int(inf) 会抛 OverflowError
        index = int(raw)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_WIDTH_INDEX
    return max(0, min(len(WIDTHS) - 1, index))


def _scratch_hotkey(data: dict) -> str:
    raw = data.get("scratch_hotkey")
    if not isinstance(raw, str) or not raw.strip():
        return DEFAULT_HOTKEY
    if raw.strip() == LEGACY_DEFAULT_HOTKEY:
        return DEFAULT_HOTKEY       # 旧默认一次性迁移
    from sketchpad.services.hotkey import parse_hotkey        # 延迟导入，保持导入期无 Qt
    try:
        parse_hotkey(raw)
    except ValueError:
        return DEFAULT_HOTKEY
    return raw


def _main_hotkey(data: dict) -> str:
    """主窗热键：缺字段/坏值回默认；与解析器同一套合法性判定。"""
    raw = data.get("main_hotkey")
    if not isinstance(raw, str) or not raw.strip():
        return DEFAULT_MAIN_HOTKEY
    from sketchpad.services.hotkey import parse_hotkey        # 延迟导入，保持导入期无 Qt
    try:
        parse_hotkey(raw)
    except ValueError:
        return DEFAULT_MAIN_HOTKEY
    return raw


def _width(data: dict) -> float:
    """自定义粗细：0.5–10，非有限数或越界一律回默认。"""
    raw = data.get("width", WIDTHS[DEFAULT_WIDTH_INDEX])
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return WIDTHS[DEFAULT_WIDTH_INDEX]
    if not math.isfinite(value) or not (0.5 <= value <= 10.0):
        return WIDTHS[DEFAULT_WIDTH_INDEX]
    return round(value, 1)



RECENT_LIMIT = 10
# 工具栏只支持两个位置：顶部横排、右侧竖排（v2.9）
TOOLBAR_POSITIONS = ("top", "right")

# 阅读位置：最多记 50 份 PDF、30 天没打开就丢（文件删了也丢），磁盘占用有界
POSITION_TTL_DAYS = 30
POSITION_TTL_SECONDS = POSITION_TTL_DAYS * 86400
POSITION_LIMIT = 50
# 与 pdfview.MIN_ZOOM / MAX_ZOOM 同值；settings.py 不导入 Qt，故用字面量
POSITION_MIN_ZOOM = 0.1
POSITION_MAX_ZOOM = 8.0


def _toolbar_position(data: dict) -> str:
    """工具栏位置只认 top/right；缺字段或坏值一律回顶部横排。"""
    raw = data.get("toolbar_position")
    if isinstance(raw, str) and raw in TOOLBAR_POSITIONS:
        return raw
    return "top"


def _toolbar_collapsed(data: dict) -> bool:
    """收起状态只认真正的布尔值；"false"/1 之类的坏值一律当没收起。"""
    raw = data.get("toolbar_collapsed", False)
    return raw if isinstance(raw, bool) else False


def _pdf_autosave(data: dict) -> bool:
    """有显式布尔值就照它；旧版没有 interval 字段的配置一次性迁到默认关。"""
    if "pdf_autosave_interval_min" not in data:
        return False        # 旧版 2 秒默认：升级后关闭，避免停笔写盘卡顿
    raw = data.get("pdf_autosave", False)
    return raw if isinstance(raw, bool) else False


def _autosave_interval(data: dict) -> int:
    """崩溃恢复缓存间隔（分钟）：1–1440；缺字段/坏值回默认 5 分钟。"""
    raw = data.get("pdf_autosave_interval_min", DEFAULT_AUTOSAVE_INTERVAL_MIN)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULT_AUTOSAVE_INTERVAL_MIN
    if isinstance(raw, float) and not raw.is_integer():
        return DEFAULT_AUTOSAVE_INTERVAL_MIN
    try:
        minutes = int(raw)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_AUTOSAVE_INTERVAL_MIN
    return max(MIN_AUTOSAVE_INTERVAL_MIN,
               min(MAX_AUTOSAVE_INTERVAL_MIN, minutes))


def _pressure_enabled(data: dict) -> bool:
    """压感开关只认真正的布尔值；缺字段/坏值回默认开。"""
    raw = data.get("pressure_enabled", True)
    return raw if isinstance(raw, bool) else True


def _note_paper(data: dict) -> str:
    """新建笔记纸色只认 dark/light；坏值回深色。"""
    raw = data.get("note_paper", "dark")
    return raw if raw in ("dark", "light") else "dark"


def _close_to_tray(data: dict) -> bool:
    raw = data.get("close_to_tray", True)
    return raw if isinstance(raw, bool) else True


def _close_prompt(data: dict) -> bool:
    raw = data.get("close_prompt", True)
    return raw if isinstance(raw, bool) else True


def _open_export_folder(data: dict) -> bool:
    raw = data.get("open_export_folder", False)
    return raw if isinstance(raw, bool) else False


_KNOWN_FIELDS = frozenset(item.name for item in fields(Settings))


def _extra_fields(data: dict) -> dict:
    """保留未知字段（用户自定义/未来版本），只在保存时原样写回，不参与业务。"""
    return {key: value for key, value in data.items()
            if key not in _KNOWN_FIELDS and key != "extra"}


def _page_mode(data: dict) -> str:
    """阅读模式只认 PAGE_MODES 里的字符串；缺字段或坏值一律回正常，绝不抛。"""
    raw = data.get("page_mode")
    return raw if isinstance(raw, str) and raw in PAGE_MODES else "normal"


def _ink_smoothing(data: dict) -> str:
    """平滑强度只认 off/low/medium；缺字段或坏值回默认低强度。"""
    raw = data.get("ink_smoothing")
    return raw if isinstance(raw, str) and raw in INK_SMOOTHING_LEVELS else "low"


def _norm_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def _recent_files(data: dict, limit: int = RECENT_LIMIT) -> list[str]:
    """绝对路径、去重（大小写不敏感）、保序、截断到 limit。"""
    raw = data.get("recent_files")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        path = item.strip()
        if not os.path.isabs(path):
            continue
        key = _norm_path(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
        if len(out) >= limit:
            break
    return out


def push_recent(recent: list[str], path: str, limit: int = RECENT_LIMIT) -> list[str]:
    """把 path 放到最近列表最前；相对路径忽略；返回新列表，不改入参。"""
    value = str(path).strip()
    if not os.path.isabs(value):
        return list(recent)
    key = _norm_path(value)
    rest = [item for item in recent if _norm_path(item) != key]
    return ([value] + rest)[:limit]


def remove_recent(recent: list[str], path: str) -> list[str]:
    """按归一化路径移除一条最近记录；返回新列表，不改入参。"""
    value = str(path).strip()
    if not value:
        return list(recent)
    key = _norm_path(value)
    return [item for item in recent if _norm_path(item) != key]


def _position_key(path) -> str:
    """阅读位置的键：归一化绝对路径（大小写/分隔符/软链接都归一）。"""
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError):        # 软链环等异常路径也给出可比较的绝对路径
        resolved = Path(os.path.abspath(str(path)))
    return os.path.normcase(os.path.normpath(str(resolved)))


def _clean_position(entry, now: float) -> dict | None:
    """校验一条阅读位置；坏类型/非有限数/过期返回 None，offset/zoom 越界钳住。"""
    if not isinstance(entry, dict):
        return None
    page = entry.get("page")
    offset = entry.get("offset")
    zoom = entry.get("zoom")
    auto_fit = entry.get("auto_fit")
    saved_at = entry.get("saved_at")
    # bool 是 int 的子类：True/False 不能当页码/数值混进来
    if isinstance(page, bool) or not isinstance(page, int) or page < 0:
        return None
    for value in (offset, zoom, saved_at):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
    if not isinstance(auto_fit, bool):
        return None
    offset = float(offset)
    zoom = float(zoom)
    saved_at = float(saved_at)
    if not (math.isfinite(offset) and math.isfinite(zoom)
            and math.isfinite(saved_at)):
        return None
    if now - saved_at > POSITION_TTL_SECONDS:       # 太久没打开：丢弃
        return None
    return {
        "page": page,
        "offset": min(1.0, max(0.0, offset)),
        "zoom": min(POSITION_MAX_ZOOM, max(POSITION_MIN_ZOOM, zoom)),
        "auto_fit": auto_fit,
        "saved_at": saved_at,
    }


def _collect_positions(raw, now: float) -> dict[str, dict]:
    """清洗一整份位置表：只留文件还在、未过期、结构合法的条目，按 saved_at 取最新。"""
    if not isinstance(raw, dict):
        return {}
    picked: dict[str, dict] = {}
    for key, entry in raw.items():
        if not isinstance(key, str) or not os.path.isabs(key):
            continue
        norm = _position_key(key)
        if not os.path.isfile(norm):               # 文件删了/改名了：不留死条目
            continue
        cleaned = _clean_position(entry, now)
        if cleaned is None:
            continue
        previous = picked.get(norm)
        if previous is None or cleaned["saved_at"] >= previous["saved_at"]:
            picked[norm] = cleaned
    newest = sorted(picked.items(),
                    key=lambda item: item[1]["saved_at"], reverse=True)
    return dict(newest[:POSITION_LIMIT])


def _pdf_positions(data: dict, now: float | None = None) -> dict[str, dict]:
    """解析配置里的 pdf_positions；坏整表给空字典，坏条目逐条丢，绝不抛。"""
    if not isinstance(data, dict):
        return {}
    current = time.time() if now is None else float(now)
    try:
        return _collect_positions(data.get("pdf_positions"), current)
    except (TypeError, ValueError, OSError):
        return {}


def pdf_position(settings: Settings, path, now: float | None = None) -> dict | None:
    """取某份 PDF 的阅读位置；文件没了/过期/结构坏了返回 None（返回副本）。"""
    entries = getattr(settings, "pdf_positions", None)
    if not isinstance(entries, dict):
        return None
    key = _position_key(path)
    if not os.path.isfile(key):
        return None
    current = time.time() if now is None else float(now)
    cleaned = _clean_position(entries.get(key), current)
    return dict(cleaned) if cleaned is not None else None


def remember_pdf_position(settings: Settings, path, *, page, offset, zoom,
                          auto_fit, now: float | None = None) -> None:
    """记下阅读位置：归一化键、校验钳值、清掉失效条目，只留最新 POSITION_LIMIT 条。"""
    current = time.time() if now is None else float(now)
    key = _position_key(path)
    if not os.path.isfile(key):              # 文件不在就不记，免得一进配置就是死条目
        return
    cleaned = _clean_position({
        "page": page, "offset": offset, "zoom": zoom,
        "auto_fit": auto_fit, "saved_at": current,
    }, current)
    if cleaned is None:
        return
    entries = _collect_positions(getattr(settings, "pdf_positions", None), current)
    entries[key] = cleaned
    newest = sorted(entries.items(),
                    key=lambda item: item[1]["saved_at"], reverse=True)
    settings.pdf_positions = dict(newest[:POSITION_LIMIT])


def prune_pdf_positions(settings: Settings, now: float | None = None) -> None:
    """丢掉文件不存在/过期的条目并按 saved_at 截断到 POSITION_LIMIT。"""
    current = time.time() if now is None else float(now)
    settings.pdf_positions = _collect_positions(
        getattr(settings, "pdf_positions", None), current)


def load(path: Path) -> Settings:
    data = _raw(path)
    width_index = _width_index(data)
    # 老配置只有 width_index：粗细取对应预设；新配置以 width 为准
    width = _width(data) if "width" in data else WIDTHS[width_index]
    return Settings(_clear_key(data), _color(data), width_index,
                    _scratch_hotkey(data), width,
                    pdf_autosave=_pdf_autosave(data),
                    pdf_autosave_interval_min=_autosave_interval(data),
                    recent_files=_recent_files(data),
                    toolbar_position=_toolbar_position(data),
                    toolbar_collapsed=_toolbar_collapsed(data),
                    main_hotkey=_main_hotkey(data),
                    pdf_positions=_pdf_positions(data),
                    page_mode=_page_mode(data),
                    ink_smoothing=_ink_smoothing(data),
                    highlighter_color=_highlighter_color(data),
                    pressure_enabled=_pressure_enabled(data),
                    note_paper=_note_paper(data),
                    close_to_tray=_close_to_tray(data),
                    close_prompt=_close_prompt(data),
                    open_export_folder=_open_export_folder(data),
                    extra=_extra_fields(data))


def save(path: Path, settings: Settings) -> None:
    """先写同目录 <name>.tmp 再 os.replace：写到一半被杀也不会留下半截配置。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(settings)
    extras = data.pop("extra", {}) or {}
    if not data.get("pdf_positions"):
        data.pop("pdf_positions", None)        # 空表不写盘：旧配置格式保持不变
    # 未知字段原样写回，但不允许覆盖程序认识的键（防止手改配置把类型搞坏）
    for key, value in extras.items():
        if key not in data:
            data[key] = value
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)
