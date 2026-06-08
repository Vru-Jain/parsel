"""
engine/qc_validator.py
---------------------
Quality-control + workflow logging.

  1. Foreign-language detection (OPTIONAL, off by default): flag non-English
     Part Name values. langdetect is unreliable on short technical strings, so
     it is gated behind config.options.enable_language_detection AND only runs
     on strings long enough to be meaningful.
  2. WIP tracker: append start/finish log lines to WIP_Tracker.txt.
  3. QC reporting: rows missing Spare Part No or DrawingPosNo are written to a
     separate <name>_QC_Report.csv and their indices returned.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd


_FOREIGN_TAG = "[FOREIGN LANGUAGE - VERIFY]"
_MIN_LEN_FOR_LANGDETECT = 12   # below this langdetect is basically noise


@dataclass
class QCResult:
    failed_indices: list[int] = field(default_factory=list)
    qc_report_path: str = ""
    foreign_count: int = 0
    notes: list[str] = field(default_factory=list)


def _try_load_langdetect():
    try:
        from langdetect import detect, DetectorFactory
        DetectorFactory.seed = 0  # deterministic
        return detect
    except Exception:
        return None


def flag_foreign_language(df: pd.DataFrame, config: dict) -> int:
    """Append a foreign-language marker to Internal Remark. Returns count flagged."""
    opts = config.get("options", {}) or {}
    if not opts.get("enable_language_detection", False):
        return 0
    if "Part Name" not in df.columns:
        return 0

    detect = _try_load_langdetect()
    if detect is None:
        return 0

    if "Internal Remark" not in df.columns:
        df["Internal Remark"] = ""

    count = 0
    remarks = list(df["Internal Remark"].astype(str))
    for pos, val in enumerate(df["Part Name"].astype(str)):
        text = val.strip()
        # skip short strings & strings dominated by digits/codes
        alpha = [c for c in text if c.isalpha()]
        if len(text) < _MIN_LEN_FOR_LANGDETECT or len(alpha) < len(text) * 0.5:
            continue
        try:
            if detect(text) != "en":
                if _FOREIGN_TAG not in remarks[pos]:
                    cur = remarks[pos].strip()
                    remarks[pos] = f"{cur} {_FOREIGN_TAG}".strip() if cur else _FOREIGN_TAG
                    count += 1
        except Exception:
            continue
    df["Internal Remark"] = remarks
    return count


def log_wip(wip_path: str, status: str, filename: str, rows: int) -> None:
    """Append a line to WIP_Tracker.txt. Never raises."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] | [Status: {status}] | [{os.path.basename(filename)}] | [Rows Processed: {rows}]\n"
        with open(wip_path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass  # logging must never break processing


def run_qc(
    df: pd.DataFrame,
    config: dict,
    source_path: str,
    output_dir: str,
) -> QCResult:
    """
    Validate the processed DataFrame. Rows where Spare Part No OR DrawingPosNo
    is empty are exported to <name>_QC_Report.csv.
    """
    res = QCResult()

    # language flag (mutates df in place)
    res.foreign_count = flag_foreign_language(df, config)
    if res.foreign_count:
        res.notes.append(f"{res.foreign_count} row(s) flagged as non-English")

    required = [c for c in ("Spare Part No", "DrawingPosNo") if c in df.columns]
    if not required or df.empty:
        return res

    def is_empty(v) -> bool:
        return str(v).strip() == ""

    failed_mask = pd.Series(False, index=df.index)
    for col in required:
        failed_mask = failed_mask | df[col].apply(is_empty)

    failed = df[failed_mask]
    res.failed_indices = list(failed.index)

    if not failed.empty:
        base = os.path.splitext(os.path.basename(source_path))[0]
        out_path = os.path.join(output_dir, f"{base}_QC_Report.csv")
        try:
            failed.to_csv(out_path, index=True, encoding="utf-8-sig")
            res.qc_report_path = out_path
            res.notes.append(
                f"{len(failed)} row(s) failed QC (missing key fields) -> {os.path.basename(out_path)}"
            )
        except Exception as exc:
            res.notes.append(f"Could not write QC report: {exc}")

    return res
