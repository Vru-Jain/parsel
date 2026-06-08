"""
Unit tests for engine/qc_validator.py — QC reporting, WIP logging, and the
optional foreign-language flag.
"""
from __future__ import annotations

import os

import pandas as pd

from engine import qc_validator


class TestWIPTracker:
    def test_log_line_format(self, tmp_path):
        wip = str(tmp_path / "WIP_Tracker.txt")
        qc_validator.log_wip(wip, "STARTED", r"C:\x\Book 1.pdf", 0)
        qc_validator.log_wip(wip, "FINISHED", r"C:\x\Book 1.pdf", 123)
        content = open(wip, encoding="utf-8").read()
        assert "STARTED" in content and "FINISHED" in content
        assert "Book 1.pdf" in content
        assert "Rows Processed: 123" in content

    def test_logging_never_raises_on_bad_path(self):
        # invalid path must not raise (logging must never break processing)
        qc_validator.log_wip("Z:\\nonexistent\\dir\\wip.txt", "STARTED", "f.pdf", 0)


class TestQCReport:
    def test_rows_missing_keys_flagged(self, cfg, tmp_path):
        df = pd.DataFrame({
            "Part Name": ["a", "b", "c"],
            "Spare Part No": ["P1", "", "P3"],
            "DrawingPosNo": ["1", "2", ""],
        })
        # ensure all schema cols exist
        for col in cfg["target_schema"]:
            if col not in df.columns:
                df[col] = ""
        res = qc_validator.run_qc(df, cfg, "Book 1.pdf", str(tmp_path))
        # row b (missing Spare Part No) and row c (missing DrawingPosNo) fail
        assert len(res.failed_indices) == 2
        assert os.path.exists(res.qc_report_path)

    def test_all_valid_no_report(self, cfg, tmp_path):
        df = pd.DataFrame({
            "Part Name": ["a"],
            "Spare Part No": ["P1"],
            "DrawingPosNo": ["1"],
        })
        for col in cfg["target_schema"]:
            if col not in df.columns:
                df[col] = ""
        res = qc_validator.run_qc(df, cfg, "ok.pdf", str(tmp_path))
        assert res.failed_indices == []
        assert res.qc_report_path == ""


class TestLanguageDetection:
    def test_off_by_default(self, cfg):
        df = pd.DataFrame({"Part Name": ["Ventildeckel Dichtung komplett"],
                           "Internal Remark": [""]})
        cfg["options"]["enable_language_detection"] = False
        count = qc_validator.flag_foreign_language(df, cfg)
        assert count == 0
        assert "[FOREIGN" not in df.loc[0, "Internal Remark"]

    def test_short_strings_skipped_when_enabled(self, cfg):
        cfg["options"]["enable_language_detection"] = True
        df = pd.DataFrame({"Part Name": ["Nut", "M10", "O-ring"],
                           "Internal Remark": ["", "", ""]})
        # short / code-like strings must not be flagged even when enabled
        qc_validator.flag_foreign_language(df, cfg)
        assert not df["Internal Remark"].str.contains("FOREIGN").any()
