"""思维导图解析：FreeMind / OPML / XMind（8 与 Zen）/ MindManager / MindNode / iThoughts。

解析层不依赖 Qt，所有异常统一收敛为 `MindMapError`；调用方只需要处理这一种。
XMind 文件是 ZIP：Zen 用 content.json，XMind 8 用 content.xml；两者都可能带
命名空间、BOM、children 为列表或多 sheet，这里都做兼容。
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

MAX_DEPTH = 200
MAX_XML_BYTES = 64 * 1024 * 1024      # 思维导图再大也不该超过 64MB
MAX_ZIP_ENTRY_BYTES = 128 * 1024 * 1024


class MindMapError(ValueError):
    pass


def _safe_xml(raw: bytes) -> ElementTree.Element:
    """拒绝 DTD/实体，避免 XXE / 实体膨胀；解析失败统一成 MindMapError。"""
    if len(raw) > MAX_XML_BYTES:
        raise MindMapError("思维导图 XML 过大，已拒绝解析")
    head = raw[:65536].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise MindMapError("思维导图 XML 包含不允许的 DTD/实体")
    try:
        return ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise MindMapError(f"思维导图 XML 解析失败：{exc}") from exc


def _read_xml_file(path: Path) -> ElementTree.Element:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise MindMapError(f"无法读取思维导图：{exc}") from exc
    return _safe_xml(raw)


def _read_zip_entry(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        size = archive.getinfo(name).file_size
    except KeyError:
        size = 0
    if size > MAX_ZIP_ENTRY_BYTES:
        raise MindMapError("思维导图解压后过大，已拒绝解析")
    return archive.read(name)


@dataclass(eq=False)
class MindNode:
    title: str
    children: list["MindNode"] = field(default_factory=list)
    index: int = 0
    parent: "MindNode | None" = None
    depth: int = 0


def _node(title: str, counter: list[int]) -> MindNode:
    node = MindNode(title=(title or "").strip() or "未命名")
    node.index = counter[0]
    counter[0] += 1
    return node


def _local(tag) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def _text(value) -> str:
    """XMind 的 title 可能是字符串，也可能是 {"text": ...} 富文本。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "title", "content", "name"):
            text = _text(value.get(key))
            if text:
                return text
    return ""


# ---------- 通用 JSON 树 ----------


def _topic_children(topic: dict) -> list:
    children = topic.get("children")
    if isinstance(children, list):
        return children
    if isinstance(children, dict):
        result: list = []
        for key in ("attached", "detached", "summary", "callout"):
            group = children.get(key)
            if isinstance(group, list):
                result.extend(group)
        if not result:
            for group in children.values():
                if isinstance(group, list):
                    result.extend(group)
        return result
    topics = topic.get("topics")
    return topics if isinstance(topics, list) else []


def _xmind_topic(topic, counter: list[int], parent=None, depth: int = 0):
    if not isinstance(topic, dict) or depth > MAX_DEPTH:
        return None
    title = _text(topic.get("title")) or _text(topic.get("text")) or "未命名"
    node = _node(title, counter)
    node.parent = parent
    node.depth = depth
    for child in _topic_children(topic):
        if not isinstance(child, dict):
            continue
        if "title" not in child and "text" not in child \
                and isinstance(child.get("topic"), dict):
            child = child["topic"]
        child_node = _xmind_topic(child, counter, node, depth + 1)
        if child_node is not None:
            node.children.append(child_node)
    return node


def _find_json_root(data):
    """找到 XMind content.json / 通用树 JSON 的根主题。"""
    if isinstance(data, list):
        for sheet in data:
            if isinstance(sheet, dict):
                for key in ("rootTopic", "root", "topic"):
                    if isinstance(sheet.get(key), dict):
                        return sheet[key]
        for item in data:
            if isinstance(item, dict) and ("title" in item or "children" in item):
                return item
        return None
    if isinstance(data, dict):
        for key in ("rootTopic", "root", "topic"):
            if isinstance(data.get(key), dict):
                return data[key]
        sheets = data.get("sheets")
        if isinstance(sheets, list):
            return _find_json_root(sheets)
        if "title" in data or "children" in data:
            return data
    return None


# ---------- XML 树 ----------


def _direct(element, names: tuple[str, ...]):
    return [child for child in list(element) if _local(child.tag) in names]


def _xml_title(element) -> str:
    for child in list(element):
        if _local(child.tag) in ("title", "text", "name"):
            if (child.text or "").strip():
                return child.text.strip()
    for attr in ("TEXT", "text", "title", "name", "Name"):
        value = element.get(attr)
        if value and value.strip():
            return value.strip()
    return ""


def _immediate_xml_topics(container):
    """只取 container 下第一层的 topic，不钻进子 topic 里重复计数。"""
    result = []
    for child in list(container):
        name = _local(child.tag)
        if name == "topic":
            result.append(child)
        elif name in ("topics", "subtopics", "children", "nodes"):
            result.extend(_immediate_xml_topics(child))
    return result


def _xml_tree(topic, counter: list[int], parent=None, depth: int = 0,
              node_names=("topic",)) -> MindNode:
    element = topic
    node = _node(_xml_title(element), counter)
    node.parent = parent
    node.depth = depth
    children = []
    for child in list(element):
        name = _local(child.tag)
        if name in ("children", "topics", "subtopics"):
            children.extend(_immediate_xml_topics(child))
        elif name in node_names:
            # MindNode/MindManager 变体可能直接嵌套 <node>/<topic>
            children.append(child)
    for child in children:
        if _local(child.tag) not in node_names or depth >= MAX_DEPTH:
            continue
        node.children.append(_xml_tree(child, counter, node, depth + 1,
                                       node_names=node_names))
    return node


# ---------- 各格式 ----------


def _from_freemind(path: Path) -> MindNode:
    root = _read_xml_file(path)
    root_node = next((el for el in root.iter() if _local(el.tag) == "node"), None)
    if root_node is None:
        raise MindMapError("FreeMind 文件里没有节点")
    counter = [0]

    def build(element, parent=None, depth=0):
        title = (element.get("TEXT") or element.get("text") or "未命名").strip()
        node = _node(title, counter)
        node.parent = parent
        node.depth = depth
        for child in _direct(element, ("node",)):
            node.children.append(build(child, node, depth + 1))
        return node

    return build(root_node)


def _from_opml(path: Path) -> MindNode:
    root = _read_xml_file(path)
    body = next((el for el in root.iter() if _local(el.tag) == "body"), None)
    container = body if body is not None else root
    outline = next((el for el in container.iter()
                    if _local(el.tag) == "outline"), None)
    if outline is None:
        raise MindMapError("OPML 文件里没有大纲节点")
    counter = [0]

    def build(element, parent=None, depth=0):
        title = (element.get("text") or element.get("title") or "未命名").strip()
        node = _node(title, counter)
        node.parent = parent
        node.depth = depth
        for child in _direct(element, ("outline",)):
            node.children.append(build(child, node, depth + 1))
        return node

    return build(outline)


def _from_xmind_json(raw: bytes) -> MindNode:
    try:
        text = raw.decode("utf-8-sig")
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MindMapError(f"XMind 内容解析失败：{exc}") from exc
    root_topic = _find_json_root(data)
    if not isinstance(root_topic, dict):
        raise MindMapError("XMind 文件里没有根主题")
    root = _xmind_topic(root_topic, [0])
    if root is None:
        raise MindMapError("XMind 文件里没有根主题")
    return root


def _from_xmind_xml(raw: bytes) -> MindNode:
    root = _safe_xml(raw)
    topic = next((el for el in root.iter() if _local(el.tag) == "topic"), None)
    if topic is None:
        raise MindMapError("XMind 文件里没有主题")
    return _xml_tree(topic, [0])


def _zip_entry(archive: zipfile.ZipFile, names: set[str],
               wanted: tuple[str, ...]) -> str | None:
    for target in wanted:
        for name in names:
            if Path(name).name.lower() == target:
                return name
    return None


def _parse_generic_raw(raw: bytes, entry: str,
                       node_names=("topic", "node")) -> MindNode:
    if entry.lower().endswith(".json"):
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MindMapError(f"思维导图 JSON 解析失败：{exc}") from exc
        root_topic = _find_json_root(data)
        if not isinstance(root_topic, dict):
            raise MindMapError("思维导图里没有根主题")
        root = _xmind_topic(root_topic, [0])
        if root is None:
            raise MindMapError("思维导图里没有根主题")
        return root
    xml_root = _safe_xml(raw)
    topic = next((el for el in xml_root.iter()
                  if _local(el.tag) in node_names), None)
    if topic is None:
        raise MindMapError("思维导图里没有主题节点")
    return _xml_tree(topic, [0], node_names=node_names)


def _from_zip_generic(path: Path, wanted: tuple[str, ...],
                      node_names=("topic", "node"),
                      child_names=("children", "subtopics", "topics", "nodes")) -> MindNode:
    """MindManager/MindNode/iThoughts 的通用 ZIP/目录 + XML/JSON 兜底解析。"""
    path = Path(path)
    if path.is_dir():
        # macOS 的 .mindnode 是 package 目录，不是 ZIP
        entry = next((path / name for name in wanted if (path / name).is_file()), None)
        if entry is None:
            entry = next((item for item in path.iterdir()
                          if item.is_file()
                          and item.suffix.lower() in (".xml", ".json")), None)
        if entry is None:
            raise MindMapError("思维导图目录里没有 XML/JSON 内容")
        raw = entry.read_bytes()
        return _parse_generic_raw(raw, entry.name, node_names)
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise MindMapError(f"无法读取思维导图：{exc}") from exc
    with archive:
        names = set(archive.namelist())
        entry = _zip_entry(archive, names, wanted)
        if entry is None:
            entry = next((name for name in names
                          if name.lower().endswith((".xml", ".json"))), None)
        if entry is None:
            raise MindMapError("思维导图文件里没有 XML/JSON 内容")
        raw = _read_zip_entry(archive, entry)
    return _parse_generic_raw(raw, entry, node_names)


def parse_mindmap(path) -> MindNode:
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix == ".mm":
            return _from_freemind(path)
        if suffix == ".opml":
            return _from_opml(path)
        if suffix == ".xmind":
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                json_entry = _zip_entry(archive, names, ("content.json",))
                xml_entry = _zip_entry(archive, names, ("content.xml",))
                errors = []
                for entry, parser in ((json_entry, _from_xmind_json),
                                      (xml_entry, _from_xmind_xml)):
                    if entry is None:
                        continue
                    try:
                        return parser(_read_zip_entry(archive, entry))
                    except MindMapError as exc:
                        errors.append(f"{Path(entry).name}: {exc}")
                if errors:
                    raise MindMapError("；".join(errors))
            raise MindMapError("XMind 文件里没有 content.json/content.xml")
        if suffix == ".mmap":
            return _from_zip_generic(path, ("document.xml", "content.xml"),
                                     node_names=("topic", "node"))
        if suffix in (".mindnode", ".itmz"):
            return _from_zip_generic(path, ("contents.xml", "document.xml",
                                            "content.xml", "document.json"))
    except MindMapError:
        raise
    except Exception as exc:
        # 第三方格式的 XML 结构千奇百怪；这里统一变成可提示的错误，绝不把
        # AttributeError/TypeError 抛进 Qt 事件循环（那会留下半开的会话）。
        raise MindMapError(f"无法读取思维导图：{exc}") from exc
    raise MindMapError(f"不支持的思维导图格式：{path.suffix or path.name}")
