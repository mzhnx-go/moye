"""PDF 批注 sidecar 的读写：页面尺寸 + 每页笔迹 + 笔记卡片（含富内容）。

版本：v1 只有笔迹；v2 加笔记卡片；v3 加卡片里的文字/图片/附件；v4 加页面级
文字/图片，以及文字的粗体/斜体/下划线（`"b"/"i"/"u"`，为真才写）。本模块不导入 Qt。
"""
from __future__ import annotations

import json
import math
import os
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from sketchpad.core.notes import (NOTE_PAPERS, Note, NoteAttachment, NoteImage,
                                  TextBlock, clamp_note_anchor,
                                  note_has_rich_content, text_block_has_format)
from sketchpad.core.strokes import Stroke, has_pressure

FORMAT_VERSION = 4          # v4 = 页面级文字/图片（v3 = 笔记卡里的富内容）
LEGACY_VERSION = 1
SIDECAR_SUFFIX = ".sketch.json"   # 源码/打包共用批注文件

TEXT_MAX_LENGTH = 20000     # 单个文字块的字符上限（含换行）
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")


class PdfFormatError(Exception):
    """sidecar 读不出来：JSON 坏了、字段缺失、数值非法。"""


class PdfVersionTooNew(PdfFormatError):
    """sidecar 由更新的版本写入，本版不猜它的格式。"""


class PdfPageCountMismatch(PdfFormatError):
    """sidecar 的页数与 PDF 实际页数不一致。"""


@dataclass
class Page:
    """一页批注；width/height 是旋转后的页面尺寸（PDF 点）。"""

    width: float
    height: float
    rotation: int = 0
    strokes: list[Stroke] = field(default_factory=list)
    # notes 放在 strokes 之后：旧代码 Page(w, h, r, strokes) 的位置参数不受影响
    notes: list[Note] = field(default_factory=list)
    # 页面级富内容（v4）：直接写在页面上，不属于任何笔记卡
    texts: list[TextBlock] = field(default_factory=list)
    images: list[NoteImage] = field(default_factory=list)


@dataclass
class PdfDocument:
    """整份 PDF 的批注；页面顺序与 PDF 页序一致。"""

    pages: list[Page] = field(default_factory=list)


def sidecar_path(pdf_path: Path) -> Path:
    """`a.pdf` → `a.sketch.json`（同目录）；只去掉 .pdf 再追加完整后缀。"""
    path = Path(pdf_path)
    # 不用 with_suffix：它按"最后一个点"换后缀，遇到无后缀名或非 .pdf 名会误伤
    return path.with_name(f"{path.stem}{SIDECAR_SUFFIX}")


def _number(value: object, what: str) -> float:
    """只收有限实数；bool 是 int 的子类，单独挡掉。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PdfFormatError(f"{what} must be a number, got {type(value).__name__}")
    out = float(value)
    if not math.isfinite(out):
        raise PdfFormatError(f"{what} must be finite, got {value!r}")
    return out


def _rotation(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PdfFormatError(f"{what} must be an int, got {type(value).__name__}")
    return value


def _parse_stroke(raw: object, where: str) -> Stroke:
    """解析一条笔迹；`where` 说明位置（页/笔记），错误消息直接带上它。"""
    if not isinstance(raw, dict):
        raise PdfFormatError(f"{where} must be an object, got {type(raw).__name__}")
    try:
        points = [(_number(x, f"{where} x"), _number(y, f"{where} y")) for x, y in raw["p"]]
        color = raw["c"]
        width = _number(raw["w"], f"{where} w")
        tool = raw.get("t", "pen")
        # 旧 sidecar 没有这个字段：缺省无压感。"pr" 一旦出现（哪怕是 null）
        # 就必须是合法压力列表，不能静默当成"没有"。
        pressures_present = "pr" in raw
        raw_pressures = raw.get("pr")
    except (KeyError, TypeError, ValueError) as exc:
        raise PdfFormatError(f"{where} malformed: {exc}") from exc
    if not isinstance(color, str):
        raise PdfFormatError(f"{where} color must be a string, got {type(color).__name__}")
    if not isinstance(tool, str):
        raise PdfFormatError(f"{where} tool must be a string, got {type(tool).__name__}")
    pressures = _pressures(raw_pressures, len(points), where, pressures_present)
    return Stroke(points, color, width, tool, pressures)


def _pressures(raw: object, point_count: int, where: str,
               present: bool = True) -> list[float] | None:
    """解析压力序列：缺省 None；出现时必须与点数等长、逐项有限且 0..1。"""
    if not present:
        return None
    if not isinstance(raw, list):
        raise PdfFormatError(
            f"{where} pressure must be a list, got {type(raw).__name__}")
    if len(raw) != point_count:
        raise PdfFormatError(
            f"{where} pressure length {len(raw)} != point count {point_count}")
    pressures: list[float] = []
    for index, value in enumerate(raw):
        pressure = _number(value, f"{where} pressure #{index}")
        if not 0.0 <= pressure <= 1.0:
            raise PdfFormatError(
                f"{where} pressure #{index} out of range 0..1: {pressure!r}")
        pressures.append(pressure)
    return pressures


def _stroke(page_index: int, index: int, raw: object) -> Stroke:
    """页面笔迹：位置是第几页第几笔。"""
    return _parse_stroke(raw, f"page #{page_index} stroke #{index}")


# ---- 富内容（v3）：文字 / 图片 / 附件 ----


def _plain_asset_name(value: object, where: str) -> str:
    """asset 必须是 assets 目录内的纯文件名；带路径/盘符/`..` 一律拒绝。

    这里刻意不 import noteassets：sidecar 解析只做"这个字符串能不能安全拼接"
    的判定，与 noteassets 的校验思路一致但互不依赖。
    """
    if not isinstance(value, str) or not value.strip():
        raise PdfFormatError(f"{where} asset must be a non-empty string")
    name = value.strip()
    try:
        bad = (name in (".", "..") or ".." in name
               or any(ch in name for ch in "/\\:") or Path(name).is_absolute()
               or Path(name).name != name)
    except (OSError, ValueError):       # 控制字符等非法路径
        bad = True
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):   # 控制字符（含 NUL）
        bad = True
    if bad:
        raise PdfFormatError(f"{where} asset must be a plain file name: {value!r}")
    return name


def _color(value: object, where: str) -> str:
    if not isinstance(value, str) or not _HEX_COLOR.match(value):
        raise PdfFormatError(f"{where} color must be #rrggbb, got {value!r}")
    return value


def _count(value: object, where: str) -> int:
    """非负整数；bool 是 int 的子类，单独挡掉。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise PdfFormatError(f"{where} must be an int, got {type(value).__name__}")
    if value < 0:
        raise PdfFormatError(f"{where} must be >= 0, got {value!r}")
    return value


def _positive(value: object, where: str) -> float:
    number = _number(value, where)
    if number <= 0:
        raise PdfFormatError(f"{where} must be positive, got {value!r}")
    return number


def _item_id(raw: dict, where: str) -> str:
    value = raw.get("id")
    if not isinstance(value, str) or not value.strip():
        raise PdfFormatError(f"{where} id must be a non-empty string")
    return value.strip()


def _format_flag(raw: dict, key: str, where: str) -> bool:
    """可选整段格式开关 `b` / `i` / `u`：缺省 False（v1–v4 旧文件可读）。

    手改出来的非布尔值（`"b": "yes"`、`"b": 1`、`"b": null`）按 False 收下并
    提示，不丢整份 sidecar——与"重复 note id 只提示不中断"同一套容错策略；
    严格抛错会让一块手滑的格式字段报销整份批注。
    """
    if key not in raw:
        return False
    value = raw[key]
    if isinstance(value, bool):
        return value
    warnings.warn(
        f"{where} {key} must be a bool, got {type(value).__name__}; treated as false",
        UserWarning,
    )
    return False


def _text_block(where: str, raw: object) -> TextBlock:
    if not isinstance(raw, dict):
        raise PdfFormatError(f"{where} must be an object, got {type(raw).__name__}")
    try:
        block_id = _item_id(raw, where)
        x = _number(raw["x"], f"{where} x")
        y = _number(raw["y"], f"{where} y")
        w = _positive(raw["w"], f"{where} w")
    except KeyError as exc:
        raise PdfFormatError(f"{where} missing field {exc}") from exc
    text = raw.get("text", "")
    if not isinstance(text, str):
        raise PdfFormatError(f"{where} text must be a string, got {type(text).__name__}")
    if len(text) > TEXT_MAX_LENGTH:
        raise PdfFormatError(
            f"{where} text too long: {len(text)} > {TEXT_MAX_LENGTH} characters")
    size = _positive(raw.get("size", 16.0), f"{where} size")
    color = _color(raw.get("color", "#e8eaed"), f"{where} color")
    bold = _format_flag(raw, "b", where)
    italic = _format_flag(raw, "i", where)
    underline = _format_flag(raw, "u", where)
    return TextBlock(block_id, x, y, w, text, size, color, bold, italic, underline)


def _note_image(where: str, raw: object) -> NoteImage:
    if not isinstance(raw, dict):
        raise PdfFormatError(f"{where} must be an object, got {type(raw).__name__}")
    try:
        image_id = _item_id(raw, where)
        x = _number(raw["x"], f"{where} x")
        y = _number(raw["y"], f"{where} y")
        w = _positive(raw["w"], f"{where} w")
        h = _positive(raw["h"], f"{where} h")
    except KeyError as exc:
        raise PdfFormatError(f"{where} missing field {exc}") from exc
    asset = _plain_asset_name(raw.get("asset"), where)
    return NoteImage(image_id, x, y, w, h, asset)


def _note_attachment(where: str, raw: object) -> NoteAttachment:
    if not isinstance(raw, dict):
        raise PdfFormatError(f"{where} must be an object, got {type(raw).__name__}")
    try:
        attachment_id = _item_id(raw, where)
        x = _number(raw["x"], f"{where} x")
        y = _number(raw["y"], f"{where} y")
    except KeyError as exc:
        raise PdfFormatError(f"{where} missing field {exc}") from exc
    asset = _plain_asset_name(raw.get("asset"), where)
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PdfFormatError(f"{where} name must be a non-empty string")
    size = _count(raw.get("size", 0), f"{where} size")
    return NoteAttachment(attachment_id, x, y, asset, name.strip(), size)


def _rich_list(raw: dict, key: str, where: str) -> list:
    """取 texts/images/attachments；缺省 []，出现就必须是 list。"""
    if key not in raw:
        return []
    value = raw[key]
    if not isinstance(value, list):
        raise PdfFormatError(
            f"{where} {key} must be a list, got {type(value).__name__}")
    return value


def _parse_items(raw: dict, key: str, where: str, parser) -> list:
    """逐项解析并挡掉重复 id（重复 id 会让 UI 里后一项永远选不中）。"""
    items = []
    seen: set[str] = set()
    for index, item in enumerate(_rich_list(raw, key, where)):
        where_item = f"{where} {key} #{index}"
        parsed = parser(where_item, item)
        if parsed.id in seen:
            raise PdfFormatError(f"{where_item} duplicate id {parsed.id!r}")
        seen.add(parsed.id)
        items.append(parsed)
    return items


def _note(page_index: int, note_index: int, raw: object) -> Note:
    """解析一张笔记卡；缺字段/尺寸非法就报第几页第几张。"""
    where = f"page #{page_index} note #{note_index}"
    if not isinstance(raw, dict):
        raise PdfFormatError(f"{where} must be an object, got {type(raw).__name__}")
    try:
        note_id = raw["id"]
        x = _number(raw["x"], f"{where} x")
        y = _number(raw["y"], f"{where} y")
        w = _number(raw["w"], f"{where} w")
        h = _number(raw["h"], f"{where} h")
        paper = raw.get("paper", "dark")
        raw_strokes = raw.get("strokes", [])
        name = raw.get("name", "")      # 旧 sidecar 没有这个字段：缺省空名
    except KeyError as exc:
        raise PdfFormatError(f"{where} missing field {exc}") from exc
    if not isinstance(note_id, str) or not note_id.strip():
        raise PdfFormatError(f"{where} id must be a non-empty string")
    if paper not in NOTE_PAPERS:
        raise PdfFormatError(f"{where} paper must be one of {NOTE_PAPERS}")
    if w <= 0 or h <= 0:
        raise PdfFormatError(f"{where} size must be positive")
    if not isinstance(raw_strokes, list):
        raise PdfFormatError(f"{where} strokes must be a list, got {type(raw_strokes).__name__}")
    if not isinstance(name, str):
        raise PdfFormatError(f"{where} name must be a string, got {type(name).__name__}")
    strokes = [_parse_stroke(s, f"{where} stroke #{j}") for j, s in enumerate(raw_strokes)]
    # v3 富内容：v1/v2 sidecar 没有这三个键，缺省空列表
    texts = _parse_items(raw, "texts", where, _text_block)
    images = _parse_items(raw, "images", where, _note_image)
    attachments = _parse_items(raw, "attachments", where, _note_attachment)
    return Note(note_id, x, y, w, h, paper, strokes, name.strip(),
                texts, images, attachments)


def _page(index: int, raw: object) -> Page:
    if not isinstance(raw, dict):
        raise PdfFormatError(f"page #{index} must be an object, got {type(raw).__name__}")
    try:
        width = _number(raw["w"], f"page #{index} w")
        height = _number(raw["h"], f"page #{index} h")
        rotation = _rotation(raw.get("r", 0), f"page #{index} r")
        raw_strokes = raw.get("strokes", [])
        raw_notes = raw.get("notes", [])   # v1 里没有这个字段，缺省为空
    except KeyError as exc:
        raise PdfFormatError(f"page #{index} missing field {exc}") from exc
    if not isinstance(raw_strokes, list):
        raise PdfFormatError(f"page #{index} strokes must be a list, got {type(raw_strokes).__name__}")
    if not isinstance(raw_notes, list):
        raise PdfFormatError(f"page #{index} notes must be a list, got {type(raw_notes).__name__}")
    # 侧车可能被手改：id 去空白、锚点钳回页内；重复 id 会让 UI 里后一张永远点不到，
    # 所以按页内 id 去重保留首个（顺序不变），并给出提示——不丢整份文件。
    notes: list[Note] = []
    seen_ids: set[str] = set()
    for j, raw_note in enumerate(raw_notes):
        note = _note(index, j, raw_note)
        note.id = note.id.strip()   # _note 已保证去空白后非空
        note.x, note.y = clamp_note_anchor(note.x, note.y, width, height)
        if note.id in seen_ids:
            warnings.warn(
                f"duplicate note id {note.id!r} on page #{index}; keeping the first",
                UserWarning,
            )
            continue
        seen_ids.add(note.id)
        notes.append(note)
    return Page(
        width,
        height,
        rotation,
        [_stroke(index, j, s) for j, s in enumerate(raw_strokes)],
        notes,
        _parse_items(raw, "texts", f"page #{index}", _text_block),
        _parse_items(raw, "images", f"page #{index}", _note_image),
    )


def _stroke_json(stroke: Stroke) -> dict[str, object]:
    """一条笔迹的编码；点坐标保留 2 位小数，尺寸与线宽原样写出。

    只有真正该按压感画的笔迹才写 `"pr"`（压力保留 3 位小数）：无压力的
    旧格式逐字节不变，旧版程序读新文件也不会被多余字段影响。
    """
    data: dict[str, object] = {
        "p": [[round(float(x), 2), round(float(y), 2)] for x, y in stroke.points],
        "c": stroke.color,
        "w": stroke.width,
        "t": stroke.tool,
    }
    if has_pressure(stroke):
        data["pr"] = [round(float(p), 3) for p in stroke.pressures or []]
    return data


def _text_block_json(block: TextBlock) -> dict[str, object]:
    """一块文字的编码；字段固定写全，读回时不依赖缺省值。

    格式开关只在为真时写 `"b"` / `"i"` / `"u"`：没有格式的 sidecar 与
    Phase B 之前**逐字节相同**，也不会因为多写 `"b":false` 让旧版程序困惑。
    """
    data: dict[str, object] = {
        "id": block.id,
        "x": block.x,
        "y": block.y,
        "w": block.w,
        "text": block.text,
        "size": block.size,
        "color": block.color,
    }
    if getattr(block, "bold", False):
        data["b"] = True
    if getattr(block, "italic", False):
        data["i"] = True
    if getattr(block, "underline", False):
        data["u"] = True
    return data


def _image_json(image: NoteImage) -> dict[str, object]:
    return {
        "id": image.id,
        "x": image.x,
        "y": image.y,
        "w": image.w,
        "h": image.h,
        "asset": image.asset,
    }


def _attachment_json(attachment: NoteAttachment) -> dict[str, object]:
    return {
        "id": attachment.id,
        "x": attachment.x,
        "y": attachment.y,
        "asset": attachment.asset,
        "name": attachment.name,
        "size": attachment.size,
    }


def _note_json(note: Note) -> dict[str, object]:
    """一张笔记卡的编码；name 只在非空时写出（空名 = 用「笔记 N」兜底）。

    不写空 name 让没有自定义名的 sidecar 与旧版完全一致，也不影响版本判定。
    texts/images/attachments 同理：**只在非空时写**，所以没有富内容的 v2
    输出与旧版逐字节相同，版本号也不会因为空列表被误判成 v3。
    """
    data: dict[str, object] = {
        "id": note.id,
        "x": note.x,
        "y": note.y,
        "w": note.w,
        "h": note.h,
        "paper": note.paper,
        "strokes": [_stroke_json(s) for s in note.strokes],
    }
    name = (note.name or "").strip()
    if name:
        data["name"] = name
    if getattr(note, "texts", None):
        data["texts"] = [_text_block_json(b) for b in note.texts]
    if getattr(note, "images", None):
        data["images"] = [_image_json(i) for i in note.images]
    if getattr(note, "attachments", None):
        data["attachments"] = [_attachment_json(a) for a in note.attachments]
    return data


def to_json(doc: PdfDocument) -> str:
    """紧凑编码；笔迹点坐标保留 2 位小数，尺寸与线宽原样写出。

    版本按内容分档：任一页有页面级文字/图片，或任一块文字带粗/斜/下划线 → v4；
    任一笔记带富内容 → v3；否则有笔记 → v2；否则 v1。低版本只写自己认识的
    字段，旧版程序读新文件最多丢掉富内容/格式，不会读坏。
    """
    has_notes = any(page.notes for page in doc.pages)
    has_note_rich = any(note_has_rich_content(note)
                        for page in doc.pages for note in page.notes)
    has_page_rich = any(page.texts or page.images for page in doc.pages)
    # 格式字段（b/i/u）是 v4 语义：哪怕只出现在笔记卡的文字里，也按 v4 落盘
    has_text_format = any(
        text_block_has_format(block)
        for page in doc.pages
        for block in list(page.texts)
        + [b for note in page.notes for b in getattr(note, "texts", None) or ()]
    )
    if has_page_rich or has_text_format:
        version = 4
    elif has_note_rich:
        version = 3
    elif has_notes:
        version = 2
    else:
        version = LEGACY_VERSION
    pages = []
    for page in doc.pages:
        raw_page: dict[str, object] = {
            "w": page.width,
            "h": page.height,
            "r": page.rotation,
            "strokes": [_stroke_json(s) for s in page.strokes],
        }
        if has_notes:
            raw_page["notes"] = [_note_json(n) for n in page.notes]
        if page.texts:
            raw_page["texts"] = [_text_block_json(b) for b in page.texts]
        if page.images:
            raw_page["images"] = [_image_json(i) for i in page.images]
        pages.append(raw_page)
    return json.dumps(
        {"version": version, "pages": pages},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def from_json(text: str, expected_page_count: int | None = None) -> PdfDocument:
    """解析 sidecar；版本过新抛 PdfVersionTooNew，页数对不上抛 PdfPageCountMismatch。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PdfFormatError(f"invalid json: {exc}") from exc
    if not isinstance(data, dict):
        raise PdfFormatError(f"root must be an object, got {type(data).__name__}")
    version = data.get("version")
    # bool 是 int 的子类，{"version":true} 不能当版本 1 收下
    if isinstance(version, bool) or not isinstance(version, int):
        raise PdfFormatError(f"missing or bad version: {version!r}")
    if version > FORMAT_VERSION:
        raise PdfVersionTooNew(f"sidecar written by newer version {version}")
    if "pages" not in data:
        raise PdfFormatError("missing pages")
    raw_pages = data["pages"]
    if not isinstance(raw_pages, list):
        raise PdfFormatError(f"pages must be a list, got {type(raw_pages).__name__}")
    pages = [_page(i, raw) for i, raw in enumerate(raw_pages)]
    if expected_page_count is not None and len(pages) != expected_page_count:
        raise PdfPageCountMismatch(
            f"page count mismatch: sidecar has {len(pages)} pages, pdf has {expected_page_count}"
        )
    return PdfDocument(pages)


def load_sidecar(pdf_path: Path, expected_page_count: int | None = None) -> PdfDocument | None:
    """读 sidecar；文件不存在返回 None，存在但读不了/坏了就抛错，绝不静默返回空文档。"""
    path = sidecar_path(pdf_path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise PdfFormatError(f"cannot read sidecar {path}: {exc}") from exc
    return from_json(text, expected_page_count)


def save_sidecar(pdf_path: Path, doc: PdfDocument) -> Path:
    """原子写：同目录 `.tmp` 写完再 os.replace；失败时旧文件保持原样。"""
    path = sidecar_path(pdf_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(to_json(doc))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        # 半截临时文件不留在目录里；旧 sidecar 未被 replace 动过
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path
