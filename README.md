# Parsel — Offline Spare-Parts Manual Parser

Desktop Windows app that extracts spare-parts tables from PDF manuals, maps the
columns onto a fixed 14-column schema, cleans the data, runs QC, and exports
Excel. The name is a play on *parse* + *Parseltongue* (hence the snake logo).

**100% offline. No LLMs, no ML model, no generative-AI API calls.** All
intelligence is deterministic: layout-aware PDF parsing (PyMuPDF), exact + fuzzy
column matching, built-in OCR for scanned manuals (RapidOCR), and regex/pandas
transforms. Lightweight (~150 MB packaged) and launches instantly.

---

## Why this stack (differs from the original spec — on purpose)

| Original | Now | Reason |
|---|---|---|
| `camelot-py[cv]` + `opencv` | **`PyMuPDF` only** | Camelot needs **Ghostscript** installed separately on Windows — silent failures, GPL. PyMuPDF is fast, MIT, zero system deps. (pdfplumber was tried and dropped: `pdfplumber.open()` took ~13 min on Book 1.pdf and added nothing PyMuPDF didn't already get.) |
| Tesseract (separate install) for OCR | **RapidOCR (ONNX), bundled** | Models ship inside the wheel: scanned-PDF OCR works out of the box, offline, no system binary. Tesseract is an optional fallback only. |
| PyTorch + sentence-transformers (semantic column matching) | **exact + fuzzy only** | On the real manuals the semantic tier produced *byte-for-byte identical* mappings while costing ~1 GB, ~18s startup, and a torch/Qt DLL crash. Removed entirely. |
| `--onefile` | **`--onedir`** build | `--onefile` re-extracts to `%TEMP%` each launch (AV flags it, slow, DLL clashes). `--onedir` is stable and fast. |
| `langdetect` (always on) | **optional**, off by default | Unreliable on short part-name strings. Now gated + length-guarded. |
| crash on bad page / locked Excel | **graceful handling** | One bad page never aborts a file; locked Excel auto-saves a copy. |

---

## Quick start (development)

```powershell
# 1. install deps
pip install -r requirements.txt

# 2. run
python main.py
```

> No model download step — column matching is exact + fuzzy (instant). Scanned-
> PDF OCR works out of the box (RapidOCR ONNX models ship with the package).

On Windows you can also just double-click **`run.bat`** (it creates the isolated
`.venv` on first run and launches the app with no console window).

---

## Build the Windows .exe (downloadable, double-click to run)

```powershell
# from the project root, inside the environment that has the deps installed
python scripts/make_ico.py          # (re)generate assets/app.ico  — only if the logo changed
pyinstaller build.spec
```

Output: **`dist\Parsel\Parsel.exe`**

- The whole `dist\Parsel\` folder is the shippable app — zip it and share it.
- An **editable `config.json`** is placed next to the .exe (not frozen inside),
  so business rules can be tweaked after compilation.
- `--onedir` + `--windowed`: no console window, fast startup, AV-friendly.
- The OCR engine (RapidOCR — ONNX models ship inside its wheel) is bundled, so
  the target machine needs **nothing installed** — not even Python. There is
  no separate model directory: column mapping is exact + fuzzy (deterministic,
  no ML model to bundle or download).

---

## How it works

```
PDF ─▶ pdf_extractor ─▶ semantic_mapper ─▶ transformer ─▶ qc_validator ─▶ Excel
        PyMuPDF +         exact → fuzzy       regex/          lang opt,
        OCR if scanned    (instant)           pandas          QC report
                │
                ▼
        Preview window (review before saving; pick pages first if you like)
```

- **pdf_extractor** — text-block reconstruction → PyMuPDF table finder, scored by
  header quality (best pass wins). Scanned pages are detected and run through
  **built-in OCR** (RapidOCR), with table rows/columns rebuilt from word boxes.
  Optional **page selection** (`"1-5, 12, 20-30"`) lets the user point the
  parser at exactly the pages that hold spare-parts tables. A reconciliation
  check independently counts item-numbers per page and flags any page where
  more were seen than rows extracted, so silent row loss is visible, not silent.
  Never returns silently empty; per-page errors are collected, not fatal.
- **semantic_mapper** — 2-tier cascade (exact alias match → fuzzy edit-distance).
  Instant, no ML model. Unmapped columns are **preserved**, never dropped.
- **transformer** — Title-Case (blacklist), dimension extraction (`L=` / `Dia.`),
  material tagging (`Matl.` prefix or → Internal Remark), null padding to schema.
- **qc_validator** — optional language flag, `WIP_Tracker.txt` log, and a
  `<name>_QC_Report.csv` for rows missing key fields.
- **Preview window** — processing fills the result tables in memory and writes
  nothing; a preview dialog shows the exact "Spare Parts" / "Unmapped (review)"
  sheets and the save path *before* anything touches disk. Excel is written
  only when the user clicks Save.

## Configuration

Everything is in **`config.json`** (no hardcoded business rules). Edit it
directly or use the in-app **Settings** dialog:

- **Mappings** — link last-run unmapped headers to schema columns.
- **Dictionaries** — edit `title_case_blacklist` and `recognized_materials`.
- **Parameters** — material-prefix length, fuzzy-match threshold, and the
  optional foreign-language-detection toggle.

## Project layout

```
spare_parts_parser/
├─ main.py              entry point, config bootstrap, path resolution, splash
├─ config.json          all business rules (editable; also copied next to the .exe)
├─ requirements.txt
├─ run.bat              double-click launcher (creates .venv, windowless)
├─ build.spec           PyInstaller (--onedir, --windowed, bundles OCR)
├─ pytest.ini
├─ engine/
│  ├─ pdf_extractor.py  PyMuPDF table extraction + built-in OCR
│  ├─ semantic_mapper.py exact → fuzzy column mapping (instant, no model)
│  ├─ transformer.py    pandas/regex cleaning pipeline
│  ├─ qc_validator.py   QC + language + WIP logging
│  └─ pipeline.py       orchestration (locked-file safe)
├─ ui/
│  ├─ main_window.py    dashboard + QThread worker + drag/drop + onboarding
│  ├─ preview_dialog.py review table + save-path display before writing Excel
│  ├─ settings_dialog.py 3-tab visual config editor
│  ├─ theme.py          light Fusion theme + onboarding copy
│  └─ icon.py           runtime-generated serpent logo + splash
├─ assets/
│  └─ app.ico           baked file icon for the .exe
├─ scripts/
│  ├─ make_ico.py              regenerate assets/app.ico from the runtime logo
│  └─ generate_demo_output.py  produce a real Excel from a manual (CLI)
├─ docs/
│  └─ TESTING.md
└─ tests/               pytest suite (built from the real demo documents)
```

See **`docs/TESTING.md`** for the test suite and demo-readiness notes.
