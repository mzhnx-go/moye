"""v3.0 异步全文搜索（offscreen）：进度上屏、结果落地、旧代作废、取消、UI 不冻结。

自包含（不 import 别的测试模块）；样例 PDF 在 tmp_path 现造。接线测试全部走
真实路径（find_bar 回车 → 后台 worker → 结果落地）；另有几条直测
`PdfSearchWorker` 的信号语义（与同步版 search_document 逐值对齐/取消/打不开）。
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import main
from sketchpad.pdf.document import PdfSource
from sketchpad.services.search import PdfSearchWorker

# 600 页合成讲义：每页几十行正文（搜索耗时接近真实文档），"needle" 只放末页，
# 逼「找到命中」也走完整篇扫描（真实 996 页讲义实测同步 1.26s 的缩小版）。
BIG_PAGES = 600
HIT_PAGE = BIG_PAGES - 1
_LINE = ("The quick brown fox jumps over the lazy dog near the river bank while "
         "students take notes about differential equations and linear algebra")
_BLOCK = "\n".join([_LINE] * 40)

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def big_pdf(tmp_path_factory):
    """600 页合成 PDF（末页含 needle）；模块内共享，构建一次。"""
    path = tmp_path_factory.mktemp("search") / "big.pdf"
    doc = pymupdf.open()
    for index in range(BIG_PAGES):
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 60), _BLOCK, fontsize=9)
        if index == HIT_PAGE:
            page.insert_text((50, 800), "needle", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def win(qapp, tmp_path):
    ctrl = main.AppController(tmp_path / "cfg")
    w = main.MainWindow(ctrl)
    w._confirm_unsaved = lambda action: QMessageBox.Discard   # 离屏不弹模态框
    w.show()
    QTest.qWaitForWindowExposed(w)
    _ALIVE.append(w)
    yield w
    w.scratch.hide()
    w.pdf_autosave.cancel()
    w._wait_find_workers()          # 搜索线程不允许活着离开用例
    w.pdf_view.close_document()
    w._quitting = True
    w.close()


def _wait_until(predicate, timeout_ms: int = 10000) -> bool:
    """轮询等条件成立；每步转一下事件循环让跨线程信号投递进来。"""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if predicate():
            return True
        QTest.qWait(5)
    return predicate()


# ---------- 1. 接线：进度上屏 + 结果落地 ----------

def test_search_shows_progress_then_result_lands(win, big_pdf):
    """搜索期间会推进度（0/600 → n/600），结束时命中落地为 1/1 并跳到命中页。"""
    assert win._enter_document(big_pdf) is True
    win._show_find()

    # 进度标签更新是“尽力而为”的：600 页合成扫描在负载高时可能来不及逐个上屏，
    # 直接断言标签文本会抖。这里拦 set_searching 的调用记录，证明进度确实在推。
    seen: list[tuple[int, int]] = []
    original = win.find_bar.set_searching

    def spy_set_searching(done: int, total: int) -> None:
        seen.append((done, total))
        original(done, total)

    win.find_bar.set_searching = spy_set_searching
    win.find_bar.input.setText("needle")
    QTest.keyClick(win.find_bar.input, Qt.Key_Return)   # 真实路径：回车 → worker
    assert _wait_until(lambda: win.find_bar.count_label.text() == "1/1")
    assert seen, "搜索期间没有设置过进度"
    # 只断言 0/total 会被 _run_find 自己的初始化满足；必须看到 worker 真扫过页
    assert any(total == BIG_PAGES and done > 0 for done, total in seen), seen[-5:]
    assert [page for page, _ in win._find_hits] == [HIT_PAGE]
    assert win.pdf_view.current_page == HIT_PAGE   # 命中页已跳过去、高亮已显示
    assert len(win.pdf_view._hit_items) > 0


# ---------- 2. 代号作废：新查询取消旧搜索、旧结果丢弃 ----------

def test_new_query_cancels_old_and_stale_result_ignored(win, big_pdf):
    assert win._enter_document(big_pdf) is True

    win._find_query = "zzz-no-such-text"
    win._run_find()                                  # 慢查询：全篇无命中
    stale = win._find_worker
    stale_gen = win._find_generation
    assert stale is not None and stale.is_cancelled is False

    win._find_query = "needle"
    win._run_find()                                  # 新查询：先取消旧的
    assert stale.is_cancelled is True
    assert win._find_worker is not stale
    assert win._find_generation > stale_gen          # 代号推进：旧代作废

    assert _wait_until(lambda: win.find_bar.count_label.text() == "1/1")
    assert [page for page, _ in win._find_hits] == [HIT_PAGE]

    # 迟到的旧代结果不许覆盖新结果（直接喂给落地守卫，等价于旧 worker 晚到）
    win._on_search_finished(stale_gen, [(0, (0.0, 0.0, 1.0, 1.0))], False)
    assert [page for page, _ in win._find_hits] == [HIT_PAGE]
    assert win.find_bar.count_label.text() == "1/1"

    # 旧 worker 自己退场后也不留任何状态
    assert _wait_until(lambda: stale not in win._find_retired)
    QTest.qWait(50)
    assert [page for page, _ in win._find_hits] == [HIT_PAGE]
    assert win.find_bar.count_label.text() == "1/1"


def test_typing_does_not_start_worker(win, big_pdf):
    """用户要求：打字不能每键一搜；只有回车/上一个/下一个才启动 worker。"""
    assert win._enter_document(big_pdf) is True
    win._show_find()
    win.find_bar.input.setText("need")
    QTest.qWait(50)
    assert win._find_worker is None
    assert win.find_bar.count_label.text() == "0/0"

    win.find_bar.input.setText("needle")
    QTest.qWait(50)
    assert win._find_worker is None                 # 继续打字也不搜

    # 全套一起跑时焦点可能还留在上一个窗口；不显式激活，回车会丢
    win.activateWindow()
    win.find_bar.input.setFocus()
    QTest.qWait(20)
    QTest.keyClick(win.find_bar.input, Qt.Key_Return)
    assert win._find_worker is not None
    assert _wait_until(lambda: win.find_bar.count_label.text() == "1/1")


# ---------- 3. 空查询立即取消 ----------

def test_empty_query_cancels_running_search(win, big_pdf):
    assert win._enter_document(big_pdf) is True
    win._show_find()
    win.find_bar.input.setText("zzz-no-such-text")
    QTest.keyClick(win.find_bar.input, Qt.Key_Return)   # 回车启动搜索
    assert _wait_until(lambda: win._find_worker is not None), "worker 没起来"
    worker = win._find_worker

    win.find_bar.input.clear()                       # 清空输入 = 空查询
    assert worker.is_cancelled is True               # 立即停手，不等它扫完
    assert win._find_worker is None
    assert win.find_bar.count_label.text() == "0/0"
    assert win._find_hits == []

    # 被取消的 worker 不发结果：退场后状态保持清空
    assert _wait_until(lambda: worker not in win._find_retired)
    QTest.qWait(50)
    assert win._find_hits == []
    assert win.find_bar.count_label.text() == "0/0"
    assert win.pdf_view._hit_items == []


# ---------- 4. UI 不冻结：600 页搜索期间事件循环照转 ----------

def test_ui_stays_responsive_during_600_page_search(win, big_pdf):
    assert win._enter_document(big_pdf) is True

    # 只记「搜索还在跑」时的定时器回声：同步版扫描期间主线程被占，这个表会空
    ticks_in_flight: list[int] = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(
        lambda: ticks_in_flight.append(1) if win._find_worker is not None else None)
    timer.start()
    try:
        gen = win._find_generation
        win._find_query = "zzz-no-such-text"         # 无命中：扫满 600 页
        started = time.monotonic()
        win._run_find()
        worker = win._find_worker
        assert worker is not None
        progress_spy = QSignalSpy(worker.progress)
        assert _wait_until(lambda: win._find_generation > gen
                           and win._find_worker is None, timeout_ms=15000)
        elapsed = time.monotonic() - started
    finally:
        timer.stop()

    assert win.find_bar.count_label.text() == "0/0"
    # 确认真的扫完全篇（而不是中途夭折）：最后一次进度 = (总页数, 总页数)
    assert progress_spy.count() > 0
    assert progress_spy.at(progress_spy.count() - 1) == [BIG_PAGES, BIG_PAGES]
    # 搜索进行中主线程事件循环照转：期间 5ms 定时器有回声（同步版跑满 1s 界面早冻死）
    assert len(ticks_in_flight) >= 1, f"搜索期间事件循环没转：elapsed={elapsed:.3f}s"
    # 这里不设墙钟上限：并行跑测试/机器负载会让 600 页合成扫描在 3–4s 间波动，
    # 真正要钉住的是「事件循环在跑」+「扫满全篇」；性能线由验收探针单独量。


# ---------- 5. worker 直测：信号语义与同步版逐值对齐 ----------

def test_worker_truncates_at_500_like_sync_search(qapp, tmp_path):
    """一页 ~590 个命中：worker 必须和同步版一样给 500 条 + truncated 标记。"""
    path = tmp_path / "many.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((30, 40), "\n".join([" ".join(["hit"] * 10)] * 70), fontsize=10)
    doc.save(str(path))
    doc.close()

    with PdfSource(path) as source:              # 参照物：同步版口径
        sync_raw = source.search_document("hit", max_hits=501)
    assert len(sync_raw) > 500                   # 样例真的封顶

    worker = PdfSearchWorker(7, path, "hit")
    spy = QSignalSpy(worker.finished)
    worker.start()
    try:
        assert _wait_until(lambda: spy.count() == 1)
        generation, hits, truncated = spy.at(0)
        assert generation == 7
        assert truncated is True
        assert hits == sync_raw[:500]            # 逐值一致（含每条的命中矩形）
    finally:
        worker.wait(2000)


def test_worker_cancelled_before_start_emits_only_cancelled(qapp, tmp_path):
    """取消后不发 finished、不发 progress，只发一次 cancelled。"""
    worker = PdfSearchWorker(3, tmp_path / "never-opened.pdf", "x")
    finished_spy = QSignalSpy(worker.finished)
    cancelled_spy = QSignalSpy(worker.cancelled)
    progress_spy = QSignalSpy(worker.progress)
    worker.cancel()
    worker.start()
    try:
        assert _wait_until(lambda: cancelled_spy.count() == 1)
        assert cancelled_spy.at(0) == [3]
        assert finished_spy.count() == 0
        assert progress_spy.count() == 0
    finally:
        worker.wait(2000)


def test_worker_unopenable_file_finishes_empty(qapp, tmp_path):
    """打不开的 PDF：收成空结果（与同步版 PdfError → [] 的容错一致），线程不炸。"""
    missing = tmp_path / "missing.pdf"
    worker = PdfSearchWorker(4, missing, "x")
    spy = QSignalSpy(worker.finished)
    worker.start()
    try:
        assert _wait_until(lambda: spy.count() == 1)
        generation, hits, truncated = spy.at(0)
        assert (generation, hits, truncated) == (4, [], False)
    finally:
        worker.wait(2000)