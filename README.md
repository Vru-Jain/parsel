# Parsel

Parsel is a Windows desktop application that extracts spare-parts tables from
PDF manuals, maps the columns onto a fixed 14-column schema, cleans and
validates the data, and exports the result to Excel.

It is built for environments where documents cannot be sent to external
services: every stage of the pipeline runs locally, with no network access and
no generative AI involved.

## Highlights

- **100% offline.** No LLMs, no ML models, no generative-AI API calls. All
  processing is deterministic — layout-aware PDF parsing, exact and fuzzy
  column matching, and rule-based data transforms.
- **Built-in OCR for scanned manuals**, via RapidOCR (ONNX models bundled with
  the package — no external OCR engine to install).
- **Lightweight**, around 150 MB packaged, and starts instantly.
- **Self-contained Windows build.** The packaged `.exe` requires nothing on the
  target machine — not even Python.

## Pipeline

```
PDF ─▶ pdf_extractor ─▶ semantic_mapper ─▶ transformer ─▶ qc_validator ─▶ Excel
        PyMuPDF +         exact → fuzzy       regex /         language flag,
        OCR if scanned    matching            pandas          QC report
                │
                ▼
        Preview window — review before anything is written to disk
```

1. **`pdf_extractor`** — Reconstructs tables from PyMuPDF text blocks, scored
   by header quality, with PyMuPDF's table finder as a secondary pass. Pages
   without a text layer are detected automatically and routed through the
   built-in OCR engine, which rebuilds rows and columns from word-level
   bounding boxes. Supports optional page-range selection (e.g. `1-5, 12,
   20-30`) and a reconciliation check that compares detected item numbers
   against extracted rows, so silent row loss is surfaced rather than hidden.
   A failure on one page never aborts the rest of the file.
2. **`semantic_mapper`** — Maps extracted headers onto the target schema with a
   two-tier match: exact alias lookup, then fuzzy (edit-distance) matching.
   No model, no startup cost. Columns that don't match are preserved, never
   dropped silently.
3. **`transformer`** — Cleans and normalizes extracted data: title-casing with
   a configurable blacklist, dimension extraction (`L=`, `Dia.`), material
   tagging, and padding to the target schema.
4. **`qc_validator`** — Optional language detection, run-history logging
   (`WIP_Tracker.txt`), and a per-file QC report (`<name>_QC_Report.csv`)
   listing rows that are missing key fields.
5. **Preview window** — Extraction results are held in memory and shown in a
   review dialog — the exact "Spare Parts" and "Unmapped (review)" sheets,
   plus the destination path — before any file is written. The Excel file is
   produced only on confirmation.

## Getting started

### Run from source

```powershell
pip install -r requirements.txt
python main.py
```

On Windows, double-clicking `run.bat` sets up an isolated virtual environment
on first launch and starts the app without a console window.

### Build a standalone executable

```powershell
python scripts/make_ico.py     # regenerate assets/app.ico, only if the icon changed
pyinstaller build.spec
```

This produces `dist\Parsel\Parsel.exe`. The full `dist\Parsel\` directory is
the shippable application:

- `config.json` sits alongside the executable rather than being frozen inside
  it, so business rules remain editable after the build.
- The build uses `--onedir` and `--windowed`: no console window, fast startup,
  and fewer antivirus false positives than a `--onefile` build.
- RapidOCR and its ONNX models are bundled, so the target machine needs no
  separate OCR installation.

## Configuration

All business rules live in `config.json` and can be edited directly or through
the in-app Settings dialog:

- **Mappings** — link headers that went unmapped on the last run to schema
  columns.
- **Dictionaries** — edit the title-case blacklist and the recognized
  materials list.
- **Parameters** — material-prefix length, fuzzy-match threshold, and the
  optional language-detection toggle.

## Project layout

```
spare_parts_parser/
├─ main.py               entry point: config bootstrap, path resolution, splash screen
├─ config.json           business rules (editable; copied alongside the packaged .exe)
├─ requirements.txt
├─ run.bat               Windows launcher (creates .venv on first run, no console window)
├─ build.spec            PyInstaller spec (--onedir, --windowed, bundles OCR)
├─ pytest.ini
├─ engine/
│  ├─ pdf_extractor.py    PyMuPDF table extraction + built-in OCR
│  ├─ semantic_mapper.py  exact → fuzzy column mapping
│  ├─ transformer.py      cleaning and normalization (pandas / regex)
│  ├─ qc_validator.py     QC checks, language detection, run logging
│  └─ pipeline.py         orchestration (locked-file handling, error isolation)
├─ ui/
│  ├─ main_window.py      dashboard, background worker, drag-and-drop, onboarding
│  ├─ preview_dialog.py   pre-save review of extracted data and output sheets
│  ├─ settings_dialog.py  configuration editor
│  ├─ theme.py            application theme and onboarding copy
│  └─ icon.py             application icon and splash screen
├─ assets/
│  └─ app.ico             application icon
├─ scripts/
│  ├─ make_ico.py              regenerates assets/app.ico
│  └─ generate_demo_output.py  produces a sample Excel export from a manual
├─ docs/
│  └─ TESTING.md          test suite and demo-readiness notes
└─ tests/                 pytest suite
```

## Testing

```powershell
python -m pytest                   # full suite (117 tests)
python -m pytest -m "not slow"     # fast unit/integration tests
python -m pytest -m "slow"         # tests against real reference documents
```

Tests that depend on real reference documents read their location from the
`PARSEL_DEMO_DIR` environment variable and skip cleanly when it isn't set. See
[`docs/TESTING.md`](docs/TESTING.md) for the full test-suite breakdown and
demo-readiness notes.

## Design decisions

A few choices in this codebase intentionally diverge from a more conventional
stack:

| Area | Choice | Why |
|---|---|---|
| PDF parsing | PyMuPDF only — no Camelot, no pdfplumber | Camelot requires a separate Ghostscript installation on Windows and is GPL-licensed. pdfplumber took roughly 13 minutes to open a 492-page manual versus 0.2 seconds for PyMuPDF, with no gain in extraction quality. |
| OCR | RapidOCR (ONNX), bundled | Ships its models inside the package and runs fully offline on CPU, with no Tesseract installation required. Tesseract remains available as an optional fallback. |
| Column mapping | Exact + fuzzy matching, no ML model | A semantic-embedding tier (sentence-transformers + PyTorch) produced byte-identical results to exact + fuzzy matching on real manuals, while adding roughly 1 GB to the package, ~18 seconds to startup, and a DLL conflict with Qt. It was removed. |
| Packaging | PyInstaller `--onedir`, not `--onefile` | `--onefile` re-extracts to a temporary directory on every launch — slower, more likely to trigger antivirus warnings, and prone to DLL conflicts. |
| Language detection | Optional, off by default | Unreliable on short part-name strings; gated behind a setting and a minimum-length guard. |
