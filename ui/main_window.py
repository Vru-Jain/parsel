"""
ui/main_window.py
-----------------
PySide6 dashboard. Drag-and-drop PDFs, watch progress, export Excel.

The engine runs on a QThread (ProcessWorker) so the UI never freezes during
extraction or vector processing. The worker communicates only via signals.
"""
from __future__ import annotations

import os
import sys
import json
import traceback

from PySide6.QtCore import Qt, QThread, Signal, QObject, QElapsedTimer, QTimer
from PySide6.QtGui import QFont, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QPlainTextEdit, QFileDialog, QListWidget, QListWidgetItem,
    QMessageBox, QFrame, QLineEdit, QApplication,
)

from engine.semantic_mapper import SemanticMapper

from ui.icon import app_icon


def _fmt_dur(seconds: float) -> str:
    """Human duration: 9s, 1m 04s, 1h 02m."""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


# --------------------------------------------------------------------------- #
# Pre-warm worker: load the embedding model once at startup, off the UI thread,
# so the first "Process" click is instant instead of a multi-second freeze.

# The app always runs from the project root (main.py is the entry point and
# PyInstaller bundles with the root on sys.path), so this absolute import works
# in both dev and frozen builds. The old relative-import fallback was invalid
# ("beyond top-level package") and only ever masked the real import error.
from engine.pipeline import process_file, ExcelLockedError


# --------------------------------------------------------------------------- #
# Worker: runs the whole batch on a background thread.
# --------------------------------------------------------------------------- #
class ProcessWorker(QObject):
    progress = Signal(int, int, str)          # current, total, message
    file_started = Signal(str)
    file_done = Signal(str)                   # filename (result stored on .results)
    file_error = Signal(str, str)             # filename, error message
    log = Signal(str)
    finished = Signal()

    def __init__(self, files, config, paths, mapper, pages=None):
        super().__init__()
        self.files = list(files)
        self.config = config
        self.paths = paths
        self.mapper = mapper
        self.pages = pages          # set of 1-based page numbers, or None = all
        self._abort = False
        # (name, PipelineResult) collected here; the UI reads it when finished.
        # We do NOT push DataFrames through a cross-thread signal.
        self.results: list = []
        self.unmapped: list = []

    def abort(self):
        self._abort = True

    def run(self):
        try:
            for path in self.files:
                if self._abort:
                    self.log.emit("Aborted by user.")
                    break
                name = os.path.basename(path)
                self.file_started.emit(name)
                self.log.emit(f"▶ Processing {name}")

                def cb(cur, total, msg, _name=name):
                    self.progress.emit(cur, total, f"{_name}: {msg}")

                try:
                    # write_excel=False: produce the sheets in memory so the UI
                    # can PREVIEW them; the actual Excel is written later, from
                    # the preview window's "Save Excel" button.
                    result = process_file(
                        path, self.config, self.paths, self.mapper, progress_cb=cb,
                        pages=self.pages, write_excel=False,
                    )
                    for w in result.warnings:
                        self.log.emit(f"  ⚠ {w}")
                    for note in result.qc_notes:
                        self.log.emit(f"  • {note}")
                    if result.page_errors:
                        self.log.emit(
                            f"  • {len(result.page_errors)} page(s) had no table "
                            f"(see status)."
                        )
                    if result.unmapped:
                        self.unmapped = result.unmapped
                        self.log.emit(
                            f"  ⚠ {name}: {len(result.unmapped)} unmapped column(s): "
                            f"{', '.join(result.unmapped)}  → fix in Settings ▸ Mappings"
                        )
                    if result.main_df is not None and result.rows:
                        self.results.append((name, result))
                        self.log.emit(f"✔ {name}: {result.rows} rows ready to preview")
                    else:
                        self.log.emit(f"✖ {name}: no parts found.")
                    self.file_done.emit(name)

                except ExcelLockedError as exc:
                    msg = (f"Output file is open in Excel: {exc}. "
                           f"Close it and run again.")
                    self.log.emit(f"✖ {name}: {msg}")
                    self.file_error.emit(name, msg)
                except RuntimeError as exc:
                    # clean, expected failures (corrupt/encrypted pdf)
                    self.log.emit(f"✖ {name}: {exc}")
                    self.file_error.emit(name, str(exc))
                except Exception as exc:  # unexpected — log traceback, keep going
                    tb = traceback.format_exc(limit=3)
                    self.log.emit(f"✖ {name}: unexpected error\n{tb}")
                    self.file_error.emit(name, str(exc))
        finally:
            self.finished.emit()


# --------------------------------------------------------------------------- #
# Drag-and-drop zone
# --------------------------------------------------------------------------- #
class DropZone(QFrame):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")
        self.setMinimumHeight(118)
        lay = QVBoxLayout(self)
        lay.setSpacing(2)
        icon = QLabel("⬇")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 26px; color: #1f8a5b;")
        lbl = QLabel("Drop your manual PDF here")
        lbl.setAlignment(Qt.AlignCenter)
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        lbl.setFont(f)
        hint = QLabel("or click “Add Files” below   ·   PDF only")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #6b7280; font-size: 11px;")
        lay.addWidget(icon)
        lay.addWidget(lbl)
        lay.addWidget(hint)
        self._idle_css = (
            "#dropZone { border: 2px dashed #c7ccd4; border-radius: 12px;"
            " background: #ffffff; }"
        )
        self._hover_css = (
            "#dropZone { border: 2px dashed #1f8a5b; border-radius: 12px;"
            " background: #eaf6ef; }"
        )
        self.setStyleSheet(self._idle_css)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setStyleSheet(self._hover_css)

    def dragLeaveEvent(self, e):
        self.setStyleSheet(self._idle_css)

    def dropEvent(self, e: QDropEvent):
        paths = []
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p.lower().endswith(".pdf"):
                paths.append(p)
        self.dragLeaveEvent(e)
        if paths:
            self.files_dropped.emit(paths)


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self, config: dict, paths: dict):
        super().__init__()
        self.config = config
        self.paths = paths
        self.files: list[str] = []
        self.last_unmapped: list[str] = []
        self.thread: QThread | None = None
        self.worker: ProcessWorker | None = None

        # one mapper instance reused across files (exact + fuzzy, no model)
        self.mapper = SemanticMapper(config)

        # run-timing state (for ETA / throughput / elapsed)
        self._run_timer = QElapsedTimer()
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._update_clock)
        self._last_msg = "Ready."
        self._last_eta = ""
        self.last_output: str | None = None
        self._results: list = []   # (name, PipelineResult) collected for preview

        self.setWindowTitle("Parsel — Offline Spare-Parts Manual Parser")
        self.setWindowIcon(app_icon())
        self.resize(880, 760)
        self.setMinimumSize(720, 640)
        self._build_ui()

    # ----- UI construction -------------------------------------------- #
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(12)

        # Header: identity + one-line purpose + Help
        root.addLayout(self._build_header())
        # Plain-language 3-step guide
        root.addWidget(self._build_steps())

        # Section: choose files
        root.addWidget(self._section_label("1 · Choose your manual"))

        # Top: drop zone + file controls
        self.drop = DropZone()
        self.drop.files_dropped.connect(self.add_files)
        root.addWidget(self.drop)

        file_row = QHBoxLayout()
        self.add_btn = QPushButton("Add Files…")
        self.add_btn.clicked.connect(self._pick_files)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self._clear_files)
        self.settings_btn = QPushButton("⚙ Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        file_row.addWidget(self.add_btn)
        file_row.addWidget(self.clear_btn)
        file_row.addStretch(1)
        # Flexible page picker: choose exactly the pages that hold parts tables.
        file_row.addWidget(QLabel("Pages:"))
        self.pages_input = QLineEdit()
        self.pages_input.setPlaceholderText("e.g. 1-5, 12, 20-30   (blank = all)")
        self.pages_input.setFixedWidth(230)
        self.pages_input.setToolTip(
            "Pick which pages to read. Single pages and ranges, comma-separated:\n"
            "  1-5, 12, 20-30\n"
            "Leave blank to process the whole manual. Choosing only the parts\n"
            "pages is faster and avoids junk from instruction/diagram pages."
        )
        file_row.addWidget(self.pages_input)
        file_row.addWidget(self.settings_btn)
        root.addLayout(file_row)

        pages_hint = QLabel("Tip: leave Pages blank for the whole manual, or list just "
                            "the pages with parts tables — e.g. 1-5, 12, 20-30.")
        pages_hint.setObjectName("caption")
        root.addWidget(pages_hint)

        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(110)
        root.addWidget(self.file_list)

        # Section: progress
        root.addWidget(self._section_label("2 · Convert"))

        # Middle: progress + status console
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("Ready")
        root.addWidget(self.progress)

        status_row = QHBoxLayout()
        self.status_label = QLabel("Ready.")
        status_row.addWidget(self.status_label, stretch=1)
        # column-matcher chip — instant exact + fuzzy (no model to load)
        self.matcher_chip = QLabel("● Matching: exact + fuzzy")
        self.matcher_chip.setStyleSheet("color: #1c7c3c;")
        self.matcher_chip.setToolTip(
            "Offline column matcher: exact alias match + fuzzy (edit-distance). "
            "Unrecognized columns are listed so you can link them in Settings."
        )
        status_row.addWidget(self.matcher_chip)
        root.addLayout(status_row)

        console_cap = QLabel("Activity log — what the app is doing. "
                             "You can safely ignore this; it helps if you need support.")
        console_cap.setObjectName("caption")
        root.addWidget(console_cap)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(2000)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.console.setFont(mono)
        root.addWidget(self.console, stretch=1)

        # Bottom: action buttons
        action_row = QHBoxLayout()
        # "&&" so Qt shows a literal "&" instead of treating it as a mnemonic
        self.process_btn = QPushButton("Process && Preview")
        self.process_btn.setObjectName("primaryButton")
        self.process_btn.setMinimumHeight(44)
        self.process_btn.setToolTip("Read the PDF and create the Excel sheet.")
        self.process_btn.clicked.connect(self._start_processing)
        self.abort_btn = QPushButton("Abort")
        self.abort_btn.setEnabled(False)
        self.abort_btn.clicked.connect(self._abort_processing)
        # post-run actions: revealed once an Excel has been produced
        self.open_btn = QPushButton("📂 Open Excel")
        self.open_btn.setObjectName("openButton")
        self.open_btn.clicked.connect(self._open_output)
        self.open_btn.setVisible(False)
        self.folder_btn = QPushButton("Open Folder")
        self.folder_btn.clicked.connect(self._open_folder)
        self.folder_btn.setVisible(False)
        action_row.addWidget(self.process_btn, stretch=1)
        action_row.addWidget(self.open_btn)
        action_row.addWidget(self.folder_btn)
        action_row.addWidget(self.abort_btn)
        root.addLayout(action_row)

        self._log(f"Config: {self.paths.get('config_path')}")
        self._log("Ready. Add a PDF, optionally pick pages, then Process & Preview.")

    # ----- onboarding pieces ------------------------------------------ #
    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel("Parsel")
        title.setObjectName("h1")
        sub = QLabel("Turn a spare-parts manual PDF into a ready-to-use Excel sheet "
                     "— offline, no typing.")
        sub.setObjectName("sub")
        col.addWidget(title)
        col.addWidget(sub)
        row.addLayout(col, stretch=1)
        self.help_btn = QPushButton("?  Help")
        self.help_btn.setToolTip("How the app works, in plain language.")
        self.help_btn.clicked.connect(self._show_help)
        row.addWidget(self.help_btn, alignment=Qt.AlignTop)
        return row

    def _build_steps(self) -> QFrame:
        from ui.theme import STEPS, ACCENT, MUTED, LINE
        bar = QFrame()
        bar.setObjectName("stepsBar")
        bar.setStyleSheet(
            f"#stepsBar {{ background:#ffffff; border:1px solid {LINE};"
            f" border-radius:10px; }}"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)
        for i, (num, head, desc) in enumerate(STEPS):
            badge = QLabel(num)
            badge.setFixedSize(24, 24)
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(
                f"background:{ACCENT}; color:white; border-radius:12px;"
                f" font-weight:700;"
            )
            txt = QVBoxLayout()
            txt.setSpacing(0)
            h = QLabel(head)
            h.setStyleSheet("font-weight:600;")
            d = QLabel(desc)
            d.setStyleSheet(f"color:{MUTED}; font-size:11px;")
            txt.addWidget(h)
            txt.addWidget(d)
            cell = QHBoxLayout()
            cell.setSpacing(8)
            cell.addWidget(badge, alignment=Qt.AlignVCenter)
            cell.addLayout(txt)
            lay.addLayout(cell)
            if i < len(STEPS) - 1:
                arrow = QLabel("→")
                arrow.setStyleSheet(f"color:{MUTED}; font-size:16px;")
                lay.addStretch(1)
                lay.addWidget(arrow, alignment=Qt.AlignVCenter)
                lay.addStretch(1)
        return bar

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("sectionLabel")
        return lbl

    def _show_help(self):
        from PySide6.QtWidgets import QDialog, QVBoxLayout as _V, QTextBrowser
        from ui.theme import HELP_HTML
        dlg = QDialog(self)
        dlg.setWindowTitle("Help — How to use this app")
        dlg.setWindowIcon(app_icon())
        dlg.resize(560, 600)
        v = _V(dlg)
        v.setContentsMargins(16, 16, 16, 16)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(HELP_HTML)
        v.addWidget(browser)
        close = QPushButton("Got it")
        close.setObjectName("primaryButton")
        close.setMinimumHeight(38)
        close.clicked.connect(dlg.accept)
        v.addWidget(close)
        dlg.exec()

    # ----- file management -------------------------------------------- #
    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select spare-parts PDF files", "", "PDF files (*.pdf)"
        )
        if files:
            self.add_files(files)

    def add_files(self, paths):
        for p in paths:
            if p not in self.files and p.lower().endswith(".pdf"):
                self.files.append(p)
                item = QListWidgetItem(os.path.basename(p))
                item.setToolTip(p)
                self.file_list.addItem(item)
        self.status_label.setText(f"{len(self.files)} file(s) queued.")

    def _clear_files(self):
        self.files.clear()
        self.file_list.clear()
        self.status_label.setText("Ready.")

    # ----- processing ------------------------------------------------- #
    def _parse_pages(self):
        """Return a set of 1-based page numbers from the Pages field, or None
        (= whole document). Shows a clear warning on malformed input."""
        from engine.pdf_extractor import parse_page_spec
        text = self.pages_input.text().strip()
        if not text:
            return None
        try:
            pages = parse_page_spec(text)
            return pages or None
        except ValueError:
            QMessageBox.warning(
                self, "Invalid page selection",
                "Use single pages and ranges, comma-separated — e.g. 1-5, 12, 20-30.\n"
                "Processing the whole manual instead.",
            )
            return None

    def _start_processing(self):
        if not self.files:
            QMessageBox.information(self, "No files", "Add at least one PDF first.")
            return
        if self.thread is not None:
            return

        pages = self._parse_pages()
        if pages:
            self._log(f"Pages selected: {len(pages)} "
                      f"({min(pages)}–{max(pages)})")
        self._results = []   # collected per-file results for the preview

        self.process_btn.setEnabled(False)
        self.add_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.abort_btn.setEnabled(True)
        self.open_btn.setVisible(False)
        self.folder_btn.setVisible(False)
        self.drop.setEnabled(False)
        # indeterminate (marquee) until the first page-count arrives
        self.progress.setRange(0, 0)
        self.progress.setFormat("Preparing…")
        self._run_timer.restart()
        self._last_eta = ""
        self._tick.start()
        QApplication.setOverrideCursor(Qt.BusyCursor)

        self.thread = QThread()
        self.worker = ProcessWorker(self.files, self.config, self.paths, self.mapper,
                                    pages=pages)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self._log)
        self.worker.file_error.connect(self._on_file_error)
        # worker done -> stop the thread's event loop; the UI finalization runs
        # on QThread.finished (after the thread truly stops) so we NEVER call
        # thread.wait() on the main thread — that deadlocks against quit().
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self._on_finished)

        self.thread.start()

    def _abort_processing(self):
        if self.worker:
            self.worker.abort()
            self.status_label.setText("Aborting after current file…")

    def _on_progress(self, cur, total, msg):
        # progress now arrives on a single 0-100 percent scale (see pipeline);
        # the human-readable phase (e.g. "Extracting page 100 (3/4)") is in msg
        # and shown in the status line below the bar.
        self._last_msg = msg
        if total > 0:
            if self.progress.maximum() == 0:           # leave marquee mode
                self.progress.setRange(0, total)
            self.progress.setMaximum(total)
            self.progress.setValue(min(cur, total))
            elapsed = self._run_timer.elapsed() / 1000.0
            if cur > 0 and elapsed > 0:
                eta = elapsed * (total - cur) / cur
                self._last_eta = f"~{_fmt_dur(eta)} left" if eta > 1 else ""
            else:
                self._last_eta = ""
            self.progress.setFormat("%p%")
        self._refresh_status()

    def _refresh_status(self):
        elapsed = _fmt_dur(self._run_timer.elapsed() / 1000.0)
        tail = " · ".join(x for x in (self._last_eta, f"elapsed {elapsed}") if x)
        self.status_label.setText(f"{self._last_msg}    —    {tail}"
                                  if tail else self._last_msg)

    def _update_clock(self):
        # keep elapsed/ETA ticking even between page callbacks
        if self.thread is not None:
            self._refresh_status()

    def _on_file_error(self, name, msg):
        # already logged in console; keep going with the batch
        pass

    def _on_finished(self):
        # Runs on QThread.finished — the worker has fully stopped, so it's safe
        # to read its results and tear it down WITHOUT thread.wait().
        self._tick.stop()
        QApplication.restoreOverrideCursor()
        if self.progress.maximum() == 0:               # never left marquee
            self.progress.setRange(0, 1)
        self.progress.setValue(self.progress.maximum())
        self.progress.setFormat("Done")
        elapsed = _fmt_dur(self._run_timer.elapsed() / 1000.0)
        self.process_btn.setEnabled(True)
        self.add_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)
        self.abort_btn.setEnabled(False)
        self.drop.setEnabled(True)

        # pull results off the worker, then schedule it + the thread for deletion
        if self.worker is not None:
            self._results = list(self.worker.results)
            self.last_unmapped = list(self.worker.unmapped)
            self.worker.deleteLater()
        if self.thread is not None:
            self.thread.deleteLater()
        self.thread = None
        self.worker = None

        # ---- show the PREVIEW so the user reviews before any Excel is saved ----
        if self._results:
            total_rows = sum(r.rows for _, r in self._results)
            self.status_label.setText(
                f"Parsed in {elapsed} — {total_rows} rows ready. Review & save."
            )
            from ui.preview_dialog import PreviewDialog
            dlg = PreviewDialog(self._results, self)
            dlg.exec()
            if dlg.saved_paths:
                self.last_output = dlg.saved_paths[-1]
                self.open_btn.setVisible(True)
                self.folder_btn.setVisible(True)
                n = len(dlg.saved_paths)
                self.status_label.setText(
                    f"✔ Saved {n} file(s)  —  {os.path.basename(self.last_output)}"
                )
                self._log(f"✔ Saved: {', '.join(os.path.basename(p) for p in dlg.saved_paths)}")
            else:
                self.status_label.setText("Preview closed — nothing saved.")
                self._log("Preview closed without saving.")
        else:
            self.status_label.setText(f"Finished in {elapsed} — no parts found.")

        if self.last_unmapped:
            QMessageBox.information(
                self, "Unmapped columns",
                "Some columns couldn't be mapped automatically:\n\n"
                + "\n".join(f"• {c}" for c in self.last_unmapped)
                + "\n\nOpen Settings ▸ Mappings to link them, then reprocess.",
            )

    # ----- settings --------------------------------------------------- #
    def _open_settings(self):
        from ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.config, self.paths, self.last_unmapped, self)
        if dlg.exec():
            # config saved -> reload from disk + rebuild mapper
            try:
                with open(self.paths["config_path"], "r", encoding="utf-8") as fh:
                    self.config = json.load(fh)
                self.mapper = SemanticMapper(self.config)
                self._log("Configuration reloaded.")
            except Exception as exc:
                QMessageBox.warning(self, "Reload failed", str(exc))

    # ----- open results ----------------------------------------------- #
    def _open_output(self):
        if self.last_output and os.path.exists(self.last_output):
            self._open_path(self.last_output)
        else:
            QMessageBox.information(self, "Not found",
                                    "The Excel file is no longer available.")

    def _open_folder(self):
        if self.last_output and os.path.exists(self.last_output):
            self._open_path(os.path.dirname(self.last_output))

    @staticmethod
    def _open_path(path: str):
        try:
            if os.name == "nt":
                os.startfile(path)  # noqa: S606 (intended: open in default app)
            else:
                import subprocess
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([opener, path])
        except Exception:
            pass

    # ----- helpers ---------------------------------------------------- #
    def _log(self, text: str):
        self.console.appendPlainText(text)

    def closeEvent(self, event):
        # quit() is called DIRECTLY here (not via a queued signal), so wait()
        # won't deadlock; bounded so closing is never hung.
        if self.thread is not None and self.thread.isRunning():
            if self.worker is not None:
                self.worker.abort()
            self.thread.quit()
            self.thread.wait(2000)
        event.accept()
