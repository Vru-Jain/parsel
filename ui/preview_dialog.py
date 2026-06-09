"""
ui/preview_dialog.py
--------------------
Preview the generated spare-parts sheet(s) BEFORE anything is written to disk.

The user reviews the exact table that will be exported (main "Spare Parts" sheet
plus the "Unmapped (review)" sheet if any), then clicks "Save Excel" to write it
— so nothing is downloaded until they're happy with it.
"""
from __future__ import annotations

import os
import sys

import pandas as pd
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QTabWidget,
    QTableView, QPushButton, QMessageBox, QHeaderView,
)

from engine.pipeline import write_excel_file, ExcelLockedError, _safe_output_path


class _PandasModel(QAbstractTableModel):
    """Read-only Qt model over a pandas DataFrame (fast for large sheets)."""

    def __init__(self, df: pd.DataFrame):
        super().__init__()
        self._df = df.reset_index(drop=True)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._df)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else self._df.shape[1]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.ToolTipRole):
            return None
        val = self._df.iat[index.row(), index.column()]
        return "" if val is None else str(val)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return str(self._df.columns[section])
        return str(section + 1)   # 1-based row numbers


def _make_table(df: pd.DataFrame) -> QTableView:
    view = QTableView()
    view.setModel(_PandasModel(df))
    view.setAlternatingRowColors(True)
    view.setSelectionBehavior(QTableView.SelectRows)
    view.setEditTriggers(QTableView.NoEditTriggers)
    hdr = view.horizontalHeader()
    hdr.setSectionResizeMode(QHeaderView.Interactive)
    hdr.setStretchLastSection(True)
    # IMPORTANT: only sample a few rows when auto-sizing columns. The default
    # resizeColumnsToContents() scans EVERY cell, which freezes the UI for many
    # seconds on a 700+ row sheet. Sampling ~25 rows is instant.
    hdr.setResizeContentsPrecision(25)
    view.resizeColumnsToContents()
    # cap absurdly wide columns so one long cell can't blow out the layout
    for c in range(min(df.shape[1], 64)):
        if view.columnWidth(c) > 320:
            view.setColumnWidth(c, 320)
    view.verticalHeader().setDefaultSectionSize(22)
    return view


class PreviewDialog(QDialog):
    """results: list of (display_name, PipelineResult). Shows each file's sheets
    and lets the user save them to Excel."""

    def __init__(self, results: list, parent=None):
        super().__init__(parent)
        self.results = [(n, r) for n, r in results if r.main_df is not None]
        self.saved_paths: list[str] = []
        self.setWindowTitle("Preview — review before saving")
        self.resize(960, 640)
        self._build_ui()
        if self.results:
            self._show_file(0)

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("File:"))
        self.file_combo = QComboBox()
        for name, res in self.results:
            self.file_combo.addItem(f"{name}  ({res.rows} rows)")
        self.file_combo.currentIndexChanged.connect(self._show_file)
        top.addWidget(self.file_combo, stretch=1)
        root.addLayout(top)

        self.summary = QLabel("")
        self.summary.setObjectName("caption")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        # Show where the .xlsx will be written (updated to actual path after save)
        self.path_label = QLabel("")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path_label.setStyleSheet("color:#15663f;")
        root.addWidget(self.path_label)

        # Amber banner: shown when any column couldn't be mapped — lets the user
        # act on the issue while still in the preview, before committing to save.
        self.unmapped_banner = QLabel("")
        self.unmapped_banner.setWordWrap(True)
        self.unmapped_banner.setStyleSheet(
            "background:#fffbeb; color:#92400e;"
            " border:1px solid #f59e0b; border-radius:6px; padding:6px 10px;"
        )
        self.unmapped_banner.setVisible(False)
        root.addWidget(self.unmapped_banner)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, stretch=1)

        hint = QLabel(
            'This is exactly what will be written. Nothing is saved until you '
            'click "Save Excel". Wrong columns? Close, fix in Settings ▸ '
            'Mappings, and re-process.'
        )
        hint.setObjectName("caption")
        hint.setWordWrap(True)
        root.addWidget(hint)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.show_folder_btn = QPushButton("📁 Show in Folder")
        self.show_folder_btn.clicked.connect(self._open_saved_folder)
        self.show_folder_btn.setVisible(False)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton("💾 Save Excel")
        self.save_btn.setObjectName("primaryButton")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._save_all)
        btns.addWidget(self.show_folder_btn)
        btns.addWidget(self.close_btn)
        btns.addWidget(self.save_btn)
        root.addLayout(btns)

    # ------------------------------------------------------------------ #
    def _show_file(self, idx: int):
        if idx < 0 or idx >= len(self.results):
            return
        name, res = self.results[idx]
        self.tabs.clear()
        self.tabs.addTab(_make_table(res.main_df),
                         f"Spare Parts  ({len(res.main_df)})")
        if res.unmapped_df is not None and not res.unmapped_df.empty:
            self.tabs.addTab(_make_table(res.unmapped_df),
                             f"Unmapped (review)  ({len(res.unmapped_df)})")

        bits = [f"{res.rows} rows"]
        if res.unmapped:
            bits.append(f"{len(res.unmapped)} unmapped column(s)")
        if res.warnings:
            bits.append(f"{len(res.warnings)} warning(s)")
        if res.page_errors:
            bits.append(f"{len(res.page_errors)} page issue(s)")
        self.summary.setText(" · ".join(bits))

        # Unmapped banner: visible only when the current file has unmapped columns.
        if res.unmapped:
            self.unmapped_banner.setText(
                f"⚠  {len(res.unmapped)} column(s) couldn't be mapped: "
                f"{', '.join(res.unmapped)}  — fix in Settings ▸ Mappings "
                f"and re-process."
            )
            self.unmapped_banner.setVisible(True)
        else:
            self.unmapped_banner.setVisible(False)

        self._update_path_label()

    def _update_path_label(self, saved_path: str | None = None):
        """Show the actual saved path after a successful save, or the
        estimated destination before."""
        if saved_path:
            if len(self.saved_paths) == 1:
                self.path_label.setText(f"✔ Saved to:  {saved_path}")
            else:
                folder = os.path.dirname(saved_path) or "."
                self.path_label.setText(
                    f"✔ Saved {len(self.saved_paths)} file(s) to: {folder}")
            self.path_label.setStyleSheet("color:#0a7c3c; font-weight:600;")
            return

        if not self.results:
            self.path_label.setText("")
            return
        if len(self.results) == 1:
            _, res = self.results[0]
            out_dir = os.path.dirname(res.source_path) or "."
            target = _safe_output_path(res.source_path, out_dir)
            self.path_label.setText(f"💾 Will save to:  {target}")
        else:
            dirs = {os.path.dirname(r.source_path) or "." for _, r in self.results}
            where = next(iter(dirs)) if len(dirs) == 1 else "each PDF's own folder"
            self.path_label.setText(
                f"💾 Will save {len(self.results)} files (one per PDF) to: {where}")
        self.path_label.setStyleSheet("color:#15663f;")

    # ------------------------------------------------------------------ #
    def _save_all(self):
        saved, failed = [], []
        for name, res in self.results:
            out_dir = os.path.dirname(res.source_path) or "."
            try:
                path = write_excel_file(res, out_dir)
                saved.append(path)
            except ExcelLockedError as exc:
                failed.append(
                    f"{name}: open in Excel ({os.path.basename(str(exc))}) "
                    f"— close it and retry"
                )
            except Exception as exc:
                failed.append(f"{name}: {exc}")

        self.saved_paths = saved
        if failed:
            QMessageBox.warning(
                self, "Some files not saved",
                "Saved {} of {} file(s).\n\nProblems:\n{}".format(
                    len(saved), len(self.results),
                    "\n".join(f"• {f}" for f in failed)),
            )
            if not saved:
                return

        if saved:
            # Update path label with the actual saved location, reveal the
            # "Show in Folder" shortcut, disable Save so it can't be double-clicked,
            # and turn the close button into a "Done" confirm.
            self._update_path_label(saved[-1])
            self.show_folder_btn.setVisible(True)
            self.save_btn.setEnabled(False)
            self.close_btn.setText("Done")
            try:
                self.close_btn.clicked.disconnect()
            except RuntimeError:
                pass
            self.close_btn.clicked.connect(self.accept)

    def _open_saved_folder(self):
        if not self.saved_paths:
            return
        folder = os.path.dirname(self.saved_paths[-1]) or "."
        try:
            if os.name == "nt":
                os.startfile(folder)  # noqa: S606
            else:
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                import subprocess
                subprocess.Popen([opener, folder])
        except Exception:
            pass
