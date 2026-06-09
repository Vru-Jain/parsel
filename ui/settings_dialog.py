"""
ui/settings_dialog.py
---------------------
Visual config editor. Three tabs, all writing back to config.json on Save:

  Tab 1 — Mappings:    link last-run unmapped headers to a target schema column.
  Tab 2 — Dictionaries: edit title_case_blacklist and recognized_materials.
  Tab 3 — Parameters:  numeric rules (max material length, thresholds, toggles).
"""
from __future__ import annotations

import json
import copy

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget, QLabel, QPushButton,
    QListWidget, QComboBox, QTableWidget, QTableWidgetItem, QSpinBox,
    QCheckBox, QFormLayout, QMessageBox, QDialogButtonBox,
    QInputDialog, QHeaderView,
)


class SettingsDialog(QDialog):
    def __init__(self, config: dict, paths: dict, unmapped: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings — Visual Config Editor")
        self.resize(640, 520)
        self.paths = paths
        # work on a deep copy; only commit to disk on Save
        self.config = copy.deepcopy(config)
        self.unmapped = list(unmapped or [])

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        self.tabs.addTab(self._build_mappings_tab(), "Mappings")
        self.tabs.addTab(self._build_dictionaries_tab(), "Dictionaries")
        self.tabs.addTab(self._build_parameters_tab(), "Parameters")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------ #
    # Tab 1: Mappings
    # ------------------------------------------------------------------ #
    def _build_mappings_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(
            'Link an unmapped header (left) to a target schema column (right), '
            'then click "Link". This adds it to header_aliases.'
        ))

        body = QHBoxLayout()
        self.unmapped_list = QListWidget()
        self.unmapped_list.addItems(self.unmapped)
        if not self.unmapped:
            self.unmapped_list.addItem("(no unmapped headers from last run)")
            self.unmapped_list.setEnabled(False)
        body.addWidget(self.unmapped_list, stretch=1)

        right = QVBoxLayout()
        self.target_combo = QComboBox()
        self.target_combo.addItems(self.config.get("target_schema", []))
        right.addWidget(QLabel("Target column:"))
        right.addWidget(self.target_combo)
        link_btn = QPushButton("← Link")
        link_btn.clicked.connect(self._link_alias)
        right.addWidget(link_btn)
        right.addStretch(1)
        body.addLayout(right, stretch=1)
        lay.addLayout(body)

        self.alias_preview = QLabel("")
        self.alias_preview.setWordWrap(True)
        self.alias_preview.setStyleSheet("color:#2a7;")
        lay.addWidget(self.alias_preview)
        return w

    def _link_alias(self):
        if not self.unmapped_list.isEnabled():
            return
        item = self.unmapped_list.currentItem()
        if not item:
            QMessageBox.information(self, "Pick a header", "Select an unmapped header first.")
            return
        raw = item.text()
        target = self.target_combo.currentText()
        aliases = self.config.setdefault("header_aliases", {})

        # Check if this alias already lives under a DIFFERENT target.
        existing_target = None
        for t, bucket in aliases.items():
            if t != target and raw in bucket:
                existing_target = t
                break

        if existing_target is not None:
            reply = QMessageBox.question(
                self,
                "Alias already mapped",
                f'"{raw}" is already linked to "{existing_target}".\n\n'
                f'Move it to "{target}" instead?',
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            # Remove from the old bucket before adding to the new one.
            aliases[existing_target].remove(raw)

        bucket = aliases.setdefault(target, [])
        if raw not in bucket:
            bucket.append(raw)
        self.alias_preview.setText(f'Linked "{raw}" → {target}')
        # Remove from the unmapped list so it isn't linked twice.
        self.unmapped_list.takeItem(self.unmapped_list.row(item))

    # ------------------------------------------------------------------ #
    # Tab 2: Dictionaries
    # ------------------------------------------------------------------ #
    def _build_dictionaries_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        rules = self.config.setdefault("rules", {})

        self.blacklist_table = self._string_table(
            "Title-Case Blacklist", rules.get("title_case_blacklist", [])
        )
        self.materials_table = self._string_table(
            "Recognized Materials", rules.get("recognized_materials", [])
        )
        lay.addWidget(self._table_group("Title-Case Blacklist", self.blacklist_table))
        lay.addWidget(self._table_group("Recognized Materials", self.materials_table))
        return w

    def _string_table(self, _title: str, values: list[str]) -> QTableWidget:
        t = QTableWidget(len(values), 1)
        t.setHorizontalHeaderLabels(["Value"])
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i, v in enumerate(values):
            t.setItem(i, 0, QTableWidgetItem(str(v)))
        return t

    def _table_group(self, title: str, table: QTableWidget) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.addWidget(QLabel(title))
        v.addWidget(table)
        row = QHBoxLayout()
        add = QPushButton("Add")
        rem = QPushButton("Delete")
        add.clicked.connect(lambda: self._table_add(table))
        rem.clicked.connect(lambda: self._table_del(table))
        row.addWidget(add)
        row.addWidget(rem)
        v.addLayout(row)
        return box

    def _table_add(self, table: QTableWidget):
        text, ok = QInputDialog.getText(self, "Add value", "New entry:")
        if ok and text.strip():
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, QTableWidgetItem(text.strip()))

    def _table_del(self, table: QTableWidget):
        r = table.currentRow()
        if r >= 0:
            table.removeRow(r)

    def _table_values(self, table: QTableWidget) -> list[str]:
        out = []
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            if item and item.text().strip():
                out.append(item.text().strip())
        return out

    # ------------------------------------------------------------------ #
    # Tab 3: Parameters
    # ------------------------------------------------------------------ #
    def _build_parameters_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        rules = self.config.setdefault("rules", {})
        opts = self.config.setdefault("options", {})

        self.max_mat = QSpinBox()
        self.max_mat.setRange(1, 100)
        self.max_mat.setValue(int(rules.get("max_material_length_for_prefix", 15)))
        self.max_mat.setToolTip(
            'Longest string (characters) to treat as a material prefix — e.g. '
            '"Matl." or "SS316".\n'
            'Increase if material codes are being cut short; decrease to avoid '
            'pulling in long descriptions.\n'
            'Scroll wheel to adjust.'
        )
        form.addRow("Max material length for prefix:", self.max_mat)

        self.fuzzy = QSpinBox()
        self.fuzzy.setRange(50, 100)
        self.fuzzy.setValue(int(rules.get("fuzzy_threshold", 88)))
        self.fuzzy.setToolTip(
            "Minimum similarity score (50–100%) for fuzzy column-name matching.\n"
            "Higher = stricter (fewer false links, more unmapped columns).\n"
            "Lower = more aggressive (fewer unmapped, but risk of wrong mapping).\n"
            "Default 88% works for most manuals. Scroll wheel to adjust."
        )
        form.addRow("Fuzzy match threshold (%):", self.fuzzy)

        self.lang_toggle = QCheckBox("Enable foreign-language detection (Part Name)")
        self.lang_toggle.setChecked(bool(opts.get("enable_language_detection", False)))
        form.addRow(self.lang_toggle)

        note = QLabel(
            "Tip: language detection is unreliable on short part names and is off "
            "by default. Lowering thresholds maps more aggressively (more false links)."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#888;")
        form.addRow(note)
        return w

    # ------------------------------------------------------------------ #
    # Save
    # ------------------------------------------------------------------ #
    def _save(self):
        rules = self.config.setdefault("rules", {})
        opts = self.config.setdefault("options", {})

        rules["title_case_blacklist"] = self._table_values(self.blacklist_table)
        rules["recognized_materials"] = self._table_values(self.materials_table)
        rules["max_material_length_for_prefix"] = self.max_mat.value()
        rules["fuzzy_threshold"] = self.fuzzy.value()
        opts["enable_language_detection"] = self.lang_toggle.isChecked()

        try:
            with open(self.paths["config_path"], "w", encoding="utf-8") as fh:
                json.dump(self.config, fh, indent=2)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", f"Could not write config.json:\n{exc}")
            return
        self.accept()
