"""Standard-only: local conversion center dialog (single window, no cloud)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QProgressBar,
                               QPushButton, QSpinBox, QVBoxLayout)

from sketchpad.convert import service

PDF_FILTER = "PDF (*.pdf)"
IMAGE_FILTER = "图片 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)"
FILTERS = {
    "images_pdf": IMAGE_FILTER,
    "docx_pdf": "Word (*.docx)",
    "md_pdf": "Markdown (*.md *.markdown)",
    "html_pdf": "HTML (*.html *.htm)",
}

OPERATIONS = (
    ("merge", "合并 PDF"),
    ("split", "拆分 PDF"),
    ("rotate", "旋转 PDF"),
    ("compress", "压缩 PDF"),
    ("pdf_images", "PDF → 图片"),
    ("images_pdf", "图片 → PDF"),
    ("pdf_text", "PDF → TXT"),
    ("pdf_docx", "PDF → Word"),
    ("docx_pdf", "Word → PDF"),
    ("md_pdf", "Markdown → PDF"),
    ("html_pdf", "HTML → PDF"),
    ("numbers", "加页码"),
    ("watermark", "加水印"),
    ("encrypt", "加密 PDF"),
    ("decrypt", "解密 PDF"),
    ("ocr", "OCR（扫描件加文字层）"),
)

MULTI_INPUT = {"merge", "images_pdf"}


class ConvertWorker(QThread):
    progressed = Signal(int, int, str)
    succeeded = Signal(list)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, operation: str, inputs: list[Path], out_dir: Path,
                 options: dict, parent=None) -> None:
        super().__init__(parent)
        self.operation = operation
        self.inputs = [Path(item) for item in inputs]
        self.out_dir = Path(out_dir)
        self.options = dict(options)
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def _progress(self, done, total, message) -> None:
        self.progressed.emit(int(done), int(total), str(message))

    def _should_cancel(self) -> bool:
        return self._cancel

    def _target(self, source: Path, name: str) -> Path:
        return service._unique_path(self.out_dir / name)

    def run(self) -> None:
        try:
            results = self._perform()
        except service.ConvertError as exc:
            if self._cancel:
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc))
            return
        except Exception as exc:
            self.failed.emit(f"转换失败：{exc}")
            return
        self.succeeded.emit([str(item) for item in results])

    def _perform(self) -> list[Path]:
        operation = self.operation
        out = self.out_dir
        out.mkdir(parents=True, exist_ok=True)
        progress, cancel = self._progress, self._should_cancel
        if operation == "merge":
            if len(self.inputs) < 2:
                raise service.ConvertError("合并至少需要两份 PDF")
            target = self._target(self.inputs[0], f"{self.inputs[0].stem}-合并.pdf")
            return [service.merge_pdfs(self.inputs, target, progress, cancel)]
        if operation == "images_pdf":
            if not self.inputs:
                raise service.ConvertError("请先添加图片")
            target = self._target(self.inputs[0], f"{self.inputs[0].stem}-图片.pdf")
            return [service.images_to_pdf(self.inputs, target, progress, cancel)]

        results: list[Path] = []
        for index, source in enumerate(self.inputs):
            if cancel():
                raise service.ConvertError("已取消")
            stem = source.stem
            if operation == "split":
                target_dir = service.unique_dir(out / f"{stem}-拆分")
                results.extend(service.split_pdf(
                    source, target_dir, self.options.get("ranges", ""),
                    progress, cancel))
            elif operation == "rotate":
                target = self._target(source, f"{stem}-旋转.pdf")
                results.append(service.rotate_pdf(
                    source, target, int(self.options.get("angle", 90)), progress, cancel))
            elif operation == "compress":
                target = self._target(source, f"{stem}-压缩.pdf")
                results.append(service.compress_pdf(source, target, progress, cancel))
            elif operation == "pdf_images":
                target_dir = service.unique_dir(out / f"{stem}-图片")
                results.extend(service.pdf_to_images(
                    source, target_dir, int(self.options.get("dpi", 144)),
                    self.options.get("image_format", "png"), progress, cancel))
            elif operation == "pdf_text":
                target = self._target(source, f"{stem}.txt")
                results.append(service.pdf_to_text(source, target, progress, cancel))
            elif operation == "pdf_docx":
                target = self._target(source, f"{stem}.docx")
                results.append(service.pdf_to_docx(source, target, progress, cancel))
            elif operation == "docx_pdf":
                target = self._target(source, f"{stem}.pdf")
                results.append(service.docx_to_pdf(source, target, progress, cancel))
            elif operation == "md_pdf":
                target = self._target(source, f"{stem}.pdf")
                results.append(service.markdown_to_pdf(source, target, progress, cancel))
            elif operation == "html_pdf":
                target = self._target(source, f"{stem}.pdf")
                html = source.read_text(encoding="utf-8", errors="replace")
                results.append(service.html_to_pdf_text(html, target))
            elif operation == "numbers":
                target = self._target(source, f"{stem}-页码.pdf")
                results.append(service.add_page_numbers(
                    source, target, self.options.get("position", "bottom-center"),
                    progress, cancel))
            elif operation == "watermark":
                target = self._target(source, f"{stem}-水印.pdf")
                results.append(service.add_watermark(
                    source, target, self.options.get("watermark", "草稿"),
                    progress, cancel))
            elif operation == "encrypt":
                target = self._target(source, f"{stem}-加密.pdf")
                results.append(service.encrypt_pdf(
                    source, target, self.options.get("user_password", ""),
                    self.options.get("owner_password", ""), progress, cancel))
            elif operation == "decrypt":
                target = self._target(source, f"{stem}-解密.pdf")
                results.append(service.decrypt_pdf(
                    source, target, self.options.get("password", ""), progress, cancel))
            elif operation == "ocr":
                target = self._target(source, f"{stem}-OCR.pdf")
                results.append(service.ocr_pdf(source, target, progress, cancel))
            else:
                raise service.ConvertError(f"未知操作：{operation}")
            self.progressed.emit(index + 1, len(self.inputs),
                                 f"{source.name} 完成")
        return results


class ConvertCenterDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ConvertCenter")
        self.setWindowTitle("转换中心")
        self.resize(760, 640)
        self.worker: ConvertWorker | None = None
        self._last_dir = Path.home()
        self._build_ui()
        self._sync_options()

    # --- UI ---

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        top = QFormLayout()
        self.operation_combo = QComboBox(self)
        for key, label in OPERATIONS:
            self.operation_combo.addItem(label, key)
        self.operation_combo.currentIndexChanged.connect(
            lambda _index: self._sync_options())
        top.addRow("操作", self.operation_combo)
        layout.addLayout(top)

        self.files_label = QLabel("输入文件（可多选）", self)
        layout.addWidget(self.files_label)
        self.files = QListWidget(self)
        layout.addWidget(self.files, 1)
        file_buttons = QHBoxLayout()
        self.add_button = QPushButton("添加文件…", self)
        self.add_button.setToolTip("选择要转换的输入文件")
        self.add_button.clicked.connect(self._add_files)
        self.remove_button = QPushButton("移除所选", self)
        self.remove_button.setToolTip("从列表移除选中的输入文件")
        self.remove_button.clicked.connect(self._remove_selected)
        file_buttons.addWidget(self.add_button)
        file_buttons.addWidget(self.remove_button)
        file_buttons.addStretch(1)
        layout.addLayout(file_buttons)

        row = QHBoxLayout()
        row.addWidget(QLabel("输出文件夹", self))
        self.output_edit = QLineEdit(self)
        row.addWidget(self.output_edit, 1)
        browse = QPushButton("浏览…", self)
        browse.setToolTip("选择输出文件夹（不会覆盖源文件）")
        browse.clicked.connect(self._choose_output)
        row.addWidget(browse)
        layout.addLayout(row)

        self.options_group = QGroupBox("选项", self)
        form = QFormLayout(self.options_group)
        self.angle_spin = QSpinBox(self)
        self.angle_spin.setRange(0, 270)
        self.angle_spin.setSingleStep(90)
        self.angle_spin.setValue(90)
        form.addRow("旋转角度", self.angle_spin)
        self.ranges_edit = QLineEdit(self)
        self.ranges_edit.setPlaceholderText("留空=每页一个文件；例：1-3,5")
        form.addRow("页码范围", self.ranges_edit)
        self.dpi_spin = QSpinBox(self)
        self.dpi_spin.setRange(72, 600)
        self.dpi_spin.setValue(144)
        form.addRow("图片 DPI", self.dpi_spin)
        self.image_format = QComboBox(self)
        self.image_format.addItems(["png", "jpg"])
        form.addRow("图片格式", self.image_format)
        self.position_combo = QComboBox(self)
        self.position_combo.addItem("页脚居中", "bottom-center")
        self.position_combo.addItem("页脚右侧", "bottom-right")
        self.position_combo.addItem("页眉居中", "top-center")
        form.addRow("页码位置", self.position_combo)
        self.watermark_edit = QLineEdit("草稿", self)
        form.addRow("水印文字", self.watermark_edit)
        self.user_password = QLineEdit(self)
        self.user_password.setEchoMode(QLineEdit.Password)
        self.user_password.setPlaceholderText("打开文件时需要输入")
        form.addRow("打开密码", self.user_password)
        self.owner_password = QLineEdit(self)
        self.owner_password.setEchoMode(QLineEdit.Password)
        form.addRow("权限密码", self.owner_password)
        self.password_edit = QLineEdit(self)
        self.password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("解密密码", self.password_edit)
        layout.addWidget(self.options_group)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.result_label = QLabel("", self)
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        buttons = QHBoxLayout()
        self.open_button = QPushButton("打开输出文件夹", self)
        self.open_button.setToolTip("在资源管理器中查看转换结果")
        self.open_button.clicked.connect(self._open_output)
        self.open_button.setEnabled(False)
        self.run_button = QPushButton("开始转换", self)
        self.run_button.setToolTip("在后台执行当前操作；输出永不覆盖源文件")
        self.run_button.clicked.connect(self._start)
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.setToolTip("停止当前转换任务")
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.setEnabled(False)
        self.close_button = QPushButton("关闭", self)
        self.close_button.setToolTip("关闭转换中心（正在运行时会先取消）")
        self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    def _operation(self) -> str:
        return self.operation_combo.currentData()

    def _sync_options(self) -> None:
        operation = self._operation()
        show = {
            "rotate": self.angle_spin,
            "split": self.ranges_edit,
            "pdf_images": self.dpi_spin,
            "numbers": self.position_combo,
            "watermark": self.watermark_edit,
            "encrypt": self.user_password,
            "decrypt": self.password_edit,
        }
        for widget in (self.angle_spin, self.ranges_edit, self.dpi_spin,
                       self.image_format, self.position_combo, self.watermark_edit,
                       self.user_password, self.owner_password, self.password_edit):
            widget.setVisible(False)
        for widget in show.values():
            widget.setVisible(True)
        self.image_format.setVisible(operation == "pdf_images")
        self.owner_password.setVisible(operation == "encrypt")
        form = self.options_group.layout()
        visible_rows = {
            "rotate", "split", "pdf_images", "numbers", "watermark",
            "encrypt", "decrypt",
        }
        for row in range(form.rowCount()):
            item = form.itemAt(row, QFormLayout.LabelRole)
            if item is not None and item.widget() is not None:
                item.widget().setVisible(row in self._visible_rows(operation))
        self.options_group.setVisible(bool(self._visible_rows(operation)))
        self.files_label.setText("输入文件（合并/图片→PDF 可多选）"
                                 if operation in MULTI_INPUT else "输入文件")
        self.files.setSelectionMode(
            QListWidget.ExtendedSelection if operation in MULTI_INPUT
            else QListWidget.SingleSelection)

    @staticmethod
    def _visible_rows(operation: str) -> set[int]:
        return {
            "rotate": {0},
            "split": {1},
            "pdf_images": {2, 3},
            "numbers": {4},
            "watermark": {5},
            "encrypt": {6, 7},
            "decrypt": {8},
        }.get(operation, set())

    # --- inputs ---

    def _add_files(self) -> None:
        file_filter = FILTERS.get(self._operation(), PDF_FILTER)
        chosen, _filter = QFileDialog.getOpenFileNames(
            self, "选择文件", str(self._last_dir), file_filter)
        for item in chosen:
            path = Path(item)
            labels = [self.files.item(i).text() for i in range(self.files.count())]
            if str(path) not in labels:
                self.files.addItem(str(path))
            self._last_dir = path.parent
        if chosen and not self.output_edit.text():
            self.output_edit.setText(str(Path(chosen[0]).parent))

    def _remove_selected(self) -> None:
        for item in self.files.selectedItems():
            self.files.takeItem(self.files.row(item))

    def _choose_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择输出文件夹", self.output_edit.text() or str(self._last_dir))
        if folder:
            self.output_edit.setText(folder)

    # --- run ---

    def _options(self) -> dict:
        operation = self._operation()
        if operation == "rotate":
            return {"angle": self.angle_spin.value()}
        if operation == "split":
            return {"ranges": self.ranges_edit.text().strip()}
        if operation == "pdf_images":
            return {"dpi": self.dpi_spin.value(),
                    "image_format": self.image_format.currentText()}
        if operation == "numbers":
            return {"position": self.position_combo.currentData()}
        if operation == "watermark":
            return {"watermark": self.watermark_edit.text()}
        if operation == "encrypt":
            return {"user_password": self.user_password.text(),
                    "owner_password": self.owner_password.text()}
        if operation == "decrypt":
            return {"password": self.password_edit.text()}
        return {}

    def _start(self) -> None:
        inputs = [Path(self.files.item(i).text()) for i in range(self.files.count())]
        if not inputs:
            self._warn("请先添加输入文件")
            return
        if self._operation() == "merge" and len(inputs) < 2:
            self._warn("合并至少需要两份 PDF")
            return
        output = self.output_edit.text().strip()
        if not output:
            output = str(inputs[0].parent)
            self.output_edit.setText(output)
        self.progress.setRange(0, 0)
        self.result_label.setText("转换中…")
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.open_button.setEnabled(False)
        self.worker = ConvertWorker(
            self._operation(), inputs, Path(output), self._options(), self)
        self.worker.progressed.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_failed)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.start()

    def _on_progress(self, done: int, total: int, message: str) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(min(done, total))
        self.result_label.setText(message)

    def _on_success(self, results: list) -> None:
        self._finish()
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.result_label.setText(f"完成 {len(results)} 个文件")
        self.open_button.setEnabled(bool(results))

    def _on_failed(self, message: str) -> None:
        self._finish()
        self.result_label.setText(message)
        QMessageBox.warning(self, "转换失败", message)

    def _on_cancelled(self) -> None:
        self._finish()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.result_label.setText("已取消")

    def _finish(self) -> None:
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.worker = None

    def _cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.result_label.setText("正在取消…")

    def _open_output(self) -> None:
        folder = self.output_edit.text().strip()
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _warn(self, text: str) -> None:
        QMessageBox.warning(self, "转换中心", text)

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        super().closeEvent(event)
