"""
engine/pipeline.py
------------------
Orchestrates the full processing run for one PDF:

    extract -> map columns -> transform -> QC -> Excel export

Exposes a single `process_file` generator-style function that reports progress
through a callback so the UI thread stays responsive. Pure engine code — no Qt.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd

from .pdf_extractor import extract_pdf
from .semantic_mapper import SemanticMapper
from .transformer import Transformer
from . import qc_validator


ProgressCb = Optional[Callable[[int, int, str], None]]


@dataclass
class PipelineResult:
    output_path: str = ""
    rows: int = 0
    mapping: dict[str, str] = field(default_factory=dict)
    unmapped: list[str] = field(default_factory=list)
    model_status: str = "not_loaded"
    page_errors: list[str] = field(default_factory=list)
    qc_notes: list[str] = field(default_factory=list)
    qc_report_path: str = ""
    warnings: list[str] = field(default_factory=list)
    source_path: str = ""
    # the generated sheets, kept in memory so the UI can PREVIEW them before
    # anything is written to disk (and so export can reuse them).
    main_df: Optional["pd.DataFrame"] = None
    unmapped_df: Optional["pd.DataFrame"] = None


class ExcelLockedError(Exception):
    """Raised when the destination .xlsx is open in Excel (locked)."""


def _safe_output_path(source_path: str, output_dir: str) -> str:
    base = os.path.splitext(os.path.basename(source_path))[0]
    return os.path.join(output_dir, f"{base}_PROCESSED.xlsx")


def _is_locked(path: str) -> bool:
    if not os.path.exists(path):
        return False
    try:
        with open(path, "a"):
            return False
    except (PermissionError, OSError):
        return True


def process_file(
    source_path: str,
    config: dict,
    paths: dict,
    mapper: SemanticMapper,
    progress_cb: ProgressCb = None,
    output_dir: Optional[str] = None,
    overwrite_locked_as_copy: bool = True,
    page_range: Optional[tuple] = None,
    pages: Optional[set] = None,
    write_excel: bool = True,
) -> PipelineResult:
    """
    Full run for a single PDF. Raises RuntimeError on unrecoverable file errors
    (caller should catch and show to the user). Per-page errors are collected,
    not raised.

    pages: optional set of 1-based page numbers to process (user's page picks).
    write_excel: when False, the result carries the generated sheets in memory
    (`main_df` / `unmapped_df`) but nothing is written — used for the PREVIEW
    flow. Call `write_excel_file(result, output_dir)` afterwards to save.
    """
    result = PipelineResult()
    result.source_path = source_path
    output_dir = output_dir or os.path.dirname(source_path) or paths.get("app_dir", ".")
    wip_path = paths.get("wip_tracker", os.path.join(output_dir, "WIP_Tracker.txt"))

    qc_validator.log_wip(wip_path, "STARTED", source_path, 0)

    # All progress is reported on ONE 0-100 percent scale so the UI bar never
    # flips scales. Extraction (the long part) is mapped onto 2..85 %.
    def report(cur, total, msg):
        if progress_cb:
            progress_cb(cur, total, msg)

    def extract_report(done, n_pages, msg):
        pct = 2 + int(done / max(1, n_pages) * 83)
        report(pct, 100, msg)

    # ---- 1. Extract (raises if the file itself is unreadable) ----
    report(2, 100, "Opening PDF…")
    extraction = extract_pdf(source_path, config, progress_cb=extract_report,
                             page_range=page_range, pages=pages)
    result.page_errors = extraction.page_errors

    # Surface scanned-PDF status clearly (the #1 cause of "empty output").
    if extraction.scanned_pages:
        n_scan = len(extraction.scanned_pages)
        if extraction.is_scanned and not extraction.ocr_used:
            status = getattr(extraction, "ocr_status", "ran")
            if status == "disabled":
                reason = ("OCR is turned OFF in config.json (options.enable_ocr = "
                          "false) — turn it on to read scanned pages.")
            elif status == "unavailable":
                reason = ("the OCR engine isn't installed — run "
                          "'pip install rapidocr-onnxruntime' in the app's venv.")
            else:  # "ran"
                reason = ("OCR ran but couldn't read a parts table from them — they "
                          "may be drawing/caution pages, or the scan is too faint.")
            result.warnings.append(
                f"This PDF appears to be SCANNED (image-only): {n_scan} of "
                f"{extraction.total_pages} pages have no text layer, and {reason}"
            )
        elif extraction.ocr_used:
            result.warnings.append(
                f"{n_scan} scanned page(s) were read with built-in OCR — verify these "
                f"rows carefully (OCR output is approximate)."
            )
        else:
            result.warnings.append(
                f"{n_scan} page(s) were image-only and skipped."
            )

    # Reconciliation: flag pages where more item numbers were seen than rows
    # extracted (a possible silent row loss) so they can be reviewed.
    if extraction.reconciliation:
        examples = ", ".join(
            f"p{pg} (saw {seen}, got {got})"
            for pg, seen, got in extraction.reconciliation[:5]
        )
        more = "" if len(extraction.reconciliation) <= 5 else \
            f" +{len(extraction.reconciliation) - 5} more"
        result.warnings.append(
            f"{len(extraction.reconciliation)} page(s) may have missed rows — "
            f"item-number count exceeded extracted rows: {examples}{more}. "
            f"Review these pages in the preview."
        )

    raw = extraction.combined
    if raw.empty:
        qc_validator.log_wip(wip_path, "FINISHED", source_path, 0)
        if not result.warnings:
            result.warnings.append(
                "No tables could be extracted from any page. "
                "The PDF may be scanned/image-only or use an unsupported layout."
            )
        return result

    # ---- 2. Map columns (exact + fuzzy, instant) ----
    report(88, 100, "Mapping columns…")
    raw_cols = [c for c in raw.columns]
    mapping_res = mapper.map_columns(raw_cols)
    result.mapping = mapping_res.mapping
    result.unmapped = mapping_res.unmapped
    result.model_status = mapping_res.model_status

    # ---- 3. Transform (single pass; raw is already fully in memory, so
    # chunking it would not save memory and risks column misalignment) ----
    report(92, 100, f"Transforming {len(raw)} rows…")
    transformer = Transformer(config)
    processed = transformer.transform(raw, mapping_res.mapping)
    result.rows = len(processed)

    # ---- 4. QC ----
    report(96, 100, "Running QC checks…")
    qc = qc_validator.run_qc(processed, config, source_path, output_dir)
    result.qc_notes = qc.notes
    result.qc_report_path = qc.qc_report_path

    # ---- 5. Build the output sheets (kept in memory for preview/export) ----
    # Split: main sheet is the STRICT target schema only; any unmapped columns
    # (page titles, stray cols) go to a separate review sheet so the primary
    # output matches the required 14-column format exactly.
    schema_cols = [c for c in config.get("target_schema", []) if c in processed.columns]
    main_df = processed[schema_cols].copy()
    unmapped_cols = [c for c in processed.columns if str(c).startswith("[UNMAPPED]")]
    unmapped_df = processed[unmapped_cols].copy() if unmapped_cols else None
    if unmapped_cols:
        # drop unmapped rows that are entirely empty
        nonblank = unmapped_df.apply(lambda r: any(str(v).strip() for v in r), axis=1)
        unmapped_df = unmapped_df[nonblank]
        if not unmapped_df.empty:
            result.warnings.append(
                f"{len(unmapped_cols)} column(s) couldn't be mapped; their data is on "
                f"the 'Unmapped (review)' sheet. Link them in Settings ▸ Mappings."
            )
        else:
            unmapped_df = None
    result.main_df = main_df
    result.unmapped_df = unmapped_df

    # ---- 6. Export Excel (optional — preview flow defers this) ----
    if write_excel:
        report(98, 100, "Writing Excel…")
        write_excel_file(result, output_dir,
                         overwrite_locked_as_copy=overwrite_locked_as_copy)

    report(100, 100, "Done.")
    qc_validator.log_wip(wip_path, "FINISHED", source_path, result.rows)
    return result


def write_excel_file(result: PipelineResult, output_dir: str,
                     overwrite_locked_as_copy: bool = True) -> str:
    """Write result.main_df (+ unmapped_df) to an .xlsx in output_dir.

    Used both by process_file (when write_excel=True) and by the UI's preview
    "Save Excel" button. Sets result.output_path and returns it. Handles a
    locked destination by saving under an alternate name (or raising
    ExcelLockedError if overwrite_locked_as_copy is False)."""
    if result.main_df is None:
        raise RuntimeError("Nothing to export — no processed data.")

    out_path = _safe_output_path(result.source_path, output_dir)
    if _is_locked(out_path):
        if not overwrite_locked_as_copy:
            raise ExcelLockedError(out_path)
        i = 1
        base, ext = os.path.splitext(out_path)
        while _is_locked(f"{base}_{i}{ext}") or os.path.exists(f"{base}_{i}{ext}"):
            i += 1
        out_path = f"{base}_{i}{ext}"
        result.warnings.append(
            f"Original output was open in Excel — saved as {os.path.basename(out_path)} instead."
        )

    try:
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            result.main_df.to_excel(writer, index=False, sheet_name="Spare Parts")
            if result.unmapped_df is not None:
                result.unmapped_df.to_excel(writer, index=False,
                                            sheet_name="Unmapped (review)")
    except PermissionError as exc:
        raise ExcelLockedError(out_path) from exc

    result.output_path = out_path
    return out_path
