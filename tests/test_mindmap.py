"""思维导图查看：FreeMind/OPML/XMind 解析、大纲、查找、缩放、主窗接线。"""
import json
import os
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import main
from sketchpad.reader import install as install_readers
from sketchpad.reader.mindmap import MindMapView, parse_mindmap
from sketchpad.workspace.viewers import clear_viewers

_ALIVE: list = []

FREEMIND = """<map version="1.0.1">
  <node TEXT="根主题">
    <node TEXT="第一章"><node TEXT="子节点一"/><node TEXT="子节点二"/></node>
    <node TEXT="第二章"/>
  </node>
</map>"""

OPML = """<opml version="2.0"><body>
  <outline text="根主题">
    <outline text="分支 A"><outline text="叶子"/></outline>
    <outline text="分支 B"/>
  </outline>
</body></opml>"""


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def readers():
    install_readers()
    yield
    clear_viewers()


@pytest.fixture
def freemind(tmp_path):
    path = tmp_path / "学习.mm"
    path.write_text(FREEMIND, encoding="utf-8")
    return path


@pytest.fixture
def opml(tmp_path):
    path = tmp_path / "计划.opml"
    path.write_text(OPML, encoding="utf-8")
    return path


@pytest.fixture
def xmind(tmp_path):
    path = tmp_path / "会议.xmind"
    content = [{"rootTopic": {"title": "会议", "children": {"attached": [
        {"title": "议程", "children": {"attached": [{"title": "议题一"}]}},
        {"title": "结论"},
    ]}}}]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.json", json.dumps(content, ensure_ascii=False))
    return path


@pytest.fixture
def xmind_legacy(tmp_path):
    path = tmp_path / "旧版.xmind"
    xml = """<xmap-content><sheet><topic><title>旧根</title><children>
      <topics type="attached"><topic><title>旧子</title></topic></topics>
    </children></topic></sheet></xmap-content>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.xml", xml)
    return path


def test_freemind_parse_tree(freemind):
    root = parse_mindmap(freemind)
    assert root.title == "根主题"
    assert [child.title for child in root.children] == ["第一章", "第二章"]
    assert [child.title for child in root.children[0].children] == ["子节点一", "子节点二"]


def test_opml_parse_tree(opml):
    root = parse_mindmap(opml)
    assert root.title == "根主题"
    assert [child.title for child in root.children] == ["分支 A", "分支 B"]


def test_xmind_json_and_legacy(xmind, xmind_legacy):
    root = parse_mindmap(xmind)
    assert root.title == "会议"
    assert [child.title for child in root.children] == ["议程", "结论"]
    legacy = parse_mindmap(xmind_legacy)
    assert legacy.title == "旧根"
    assert legacy.children[0].title == "旧子"


def test_xmind_children_list_bom_and_rich_title(tmp_path):
    """XMind Zen 变体：children 可能是列表、文件带 BOM、title 可能是富文本对象。"""
    path = tmp_path / "变体.xmind"
    content = [{"rootTopic": {
        "title": {"text": "根主题"},
        "children": [
            {"title": "子一", "children": [{"title": "叶子"}]},
            {"title": {"text": "子二"}},
        ],
    }}]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.json",
                         ("\ufeff" + json.dumps(content, ensure_ascii=False)).encode("utf-8"))
    root = parse_mindmap(path)
    assert root.title == "根主题"
    assert [child.title for child in root.children] == ["子一", "子二"]
    assert root.children[0].children[0].title == "叶子"


def test_xmind_legacy_with_namespace(tmp_path):
    path = tmp_path / "命名空间.xmind"
    xml = """<xmap-content xmlns="urn:xmind:xmap:xmlns:content:2.0">
      <sheet><topic><title>根</title><children><topics type="attached">
        <topic><title>子 A</title></topic>
        <topic><title>子 B</title><children><topics type="attached">
          <topic><title>孙</title></topic>
        </topics></children></topic>
      </topics></children></topic></sheet>
    </xmap-content>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.xml", xml)
    root = parse_mindmap(path)
    assert root.title == "根"
    assert [child.title for child in root.children] == ["子 A", "子 B"]
    assert root.children[1].children[0].title == "孙"


def test_xmind_multi_sheet_uses_first_root(tmp_path):
    path = tmp_path / "多画布.xmind"
    content = [
        {"title": "Sheet 1", "rootTopic": {"title": "第一张"}},
        {"title": "Sheet 2", "rootTopic": {"title": "第二张"}},
    ]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.json", json.dumps(content, ensure_ascii=False))
    assert parse_mindmap(path).title == "第一张"


def test_mmap_mindmanager_generic(tmp_path):
    path = tmp_path / "项目.mmap"
    xml = """<ap:Map xmlns:ap="urn:mindjet">
      <ap:Topic><ap:Text>项目根</ap:Text><ap:SubTopics>
        <ap:Topic><ap:Text>阶段一</ap:Text></ap:Topic>
        <ap:Topic><ap:Text>阶段二</ap:Text></ap:Topic>
      </ap:SubTopics></ap:Topic>
    </ap:Map>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Document.xml", xml)
    root = parse_mindmap(path)
    assert root.title == "项目根"
    assert [child.title for child in root.children] == ["阶段一", "阶段二"]


def test_mindnode_and_itmz_generic(tmp_path):
    mindnode = tmp_path / "想法.mindnode"
    with zipfile.ZipFile(mindnode, "w") as archive:
        archive.writestr(
            "contents.xml",
            '<document><node text="根"><node text="子"/></node></document>')
    assert parse_mindmap(mindnode).children[0].title == "子"

    itmz = tmp_path / "大纲.itmz"
    with zipfile.ZipFile(itmz, "w") as archive:
        archive.writestr(
            "document.xml",
            "<map><topic><title>根</title><children><topic><title>子</title>"
            "</topic></children></topic></map>")
    assert parse_mindmap(itmz).children[0].title == "子"


def test_mindnode_package_directory(tmp_path):
    package = tmp_path / "包.mindnode"
    package.mkdir()
    (package / "contents.xml").write_text(
        '<document><node text="包根"><node text="包子"/></node></document>',
        encoding="utf-8")
    root = parse_mindmap(package)
    assert root.title == "包根"
    assert root.children[0].title == "包子"


def test_bad_xmind_raises_mindmap_error(tmp_path):
    path = tmp_path / "坏.xmind"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.json", "{bad json")
    with pytest.raises(ValueError):
        parse_mindmap(path)


def test_xml_dtd_is_rejected(tmp_path):
    path = tmp_path / "dtd.mm"
    path.write_text(
        '<!DOCTYPE map [<!ENTITY x "boom">]><map><node TEXT="&x;"/></map>',
        encoding="utf-8")
    with pytest.raises(ValueError):
        parse_mindmap(path)


def test_viewer_outline_find_zoom(qapp, freemind):
    viewer = MindMapView()
    viewer.resize(800, 600)
    viewer.show()
    qapp.processEvents()
    viewer.open(freemind)
    assert viewer.node_count == 5
    assert viewer._auto_fit is True          # 小图整图适配
    outline = viewer.outline()
    assert outline[0][0] == 1 and outline[0][1] == "根主题"
    assert (3, "子节点一", 2) in outline

    hits = viewer.search("子节点")
    assert len(hits) == 2
    viewer.show_hits(0, [index for _, index in hits], current=1)
    highlighted = [index for index, (item, _text) in viewer.canvas.node_items.items()
                   if item.brush().color().name() == "#ffd43b"]
    assert highlighted == [hits[1][1]]
    viewer.clear_hits()

    viewer.set_zoom(1.5)
    assert viewer.zoom == pytest.approx(1.5)
    assert "思维导图" in viewer.status_text()
    assert viewer.document is None and viewer.source is None
    viewer.close_document()
    assert viewer.pdf_path is None


def test_mindmap_creates_no_sidecar(qapp, freemind):
    viewer = MindMapView()
    viewer.open(freemind)
    viewer.close_document()
    assert list(freemind.parent.glob("*.sketch.json")) == []


def test_large_map_opens_readable_instead_of_fitting_to_tiny(qapp, tmp_path):
    """230 节点大图整图适配只有 6%，必须改成 100% + 滚动。"""
    leaves = "".join(f'<node TEXT="分支 {index}"/>' for index in range(200))
    path = tmp_path / "大图.mm"
    path.write_text(f'<map><node TEXT="根">{leaves}</node></map>',
                    encoding="utf-8")
    viewer = MindMapView()
    viewer.resize(900, 700)
    viewer.show()
    viewer.open(path)
    assert viewer.node_count == 201
    assert viewer._auto_fit is False
    assert viewer.canvas.transform().m11() >= 0.95
    assert "100%" in viewer.status_text()
    # 窗口后续 resize 也不能把大图重新缩成看不见
    viewer.resize(1200, 800)
    assert viewer.canvas.transform().m11() >= 0.95


def test_mindmap_style_layers_and_white_canvas(qapp, tmp_path):
    """一级实心分支色、二级白底彩边、三级淡色底；画布白底。"""
    path = tmp_path / "层级.mm"
    path.write_text(
        '<map><node TEXT="根"><node TEXT="一级"><node TEXT="二级">'
        '<node TEXT="三级"/></node></node></node></map>',
        encoding="utf-8")
    viewer = MindMapView()
    viewer.resize(800, 600)
    viewer.show()
    viewer.open(path)
    styles = viewer.canvas.node_styles
    root = styles[0]
    level1 = styles[1]
    level2 = styles[2]
    level3 = styles[3]
    assert root[0].name() == "#2d3436" and root[2].name() == "#ffffff"
    assert level1[0].name() == "#7b61ff" and level1[2].name() == "#ffffff"
    assert level2[0].name() == "#ffffff"
    assert level2[1].name() == "#7b61ff"
    assert level2[2].name() == "#1c1c1e"
    assert level3[0].name() != "#ffffff"      # 分支色淡底
    assert level3[1].name() == "#7b61ff"
    assert viewer.canvas.backgroundBrush().color().name() == "#ffffff"


@pytest.fixture
def win(qapp, tmp_path):
    ctrl = main.AppController(tmp_path / "cfg")
    w = main.MainWindow(ctrl)
    w._confirm_unsaved = lambda action: QMessageBox.Discard
    w.show()
    QTest.qWaitForWindowExposed(w)
    _ALIVE.append(w)
    yield w
    w.scratch.hide()
    w._quitting = True
    w.close()


def test_app_opens_mindmap_as_text_reader(win, freemind):
    assert win._enter_document(freemind) is True
    assert isinstance(win.pdf_view, MindMapView)
    assert win.mode == "document"
    assert win.tabs.current_entry.title == freemind.name
    assert win.toolbox.pen_button.isHidden() is True
    assert win.toolbox.select_button.isHidden() is False   # Lite 的鼠标工具在 Standard 也保留
    assert win.menubar.select_action.isEnabled() is True
    assert win.menubar.edit_word_action.isEnabled() is False
    tree = win.toc_panel.tree
    assert tree.topLevelItemCount() == 1
    assert tree.topLevelItem(0).text(0) == "根主题"
    win._queue_find("子节点一")
    assert len(win._find_hits) == 1


def test_bad_xmind_does_not_leave_half_session(win, tmp_path, freemind):
    bad = tmp_path / "坏文件.xmind"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("content.json", "{broken")
    before = len(win.tabs.entries)
    assert win._enter_document(bad) is False
    assert win.mode == "draft"
    assert len(win.tabs.entries) == before
    assert "损坏" in win.message_label.text() or "解析" in win.message_label.text() \
        or "无法" in win.message_label.text()
    # 失败后仍能正常打开另一个导图
    assert win._enter_document(freemind) is True
    assert isinstance(win.pdf_view, MindMapView)
