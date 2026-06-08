# Test Suite & Demo Readiness

Exhaustive tests built from the **actual customer documents** in
`Spare parts documents/` so the app behaves correctly on the real demo files.

## Run the tests

```powershell
cd spare_parts_parser
pip install -r requirements.txt
pip install pytest                 # if not already installed

python -m pytest -m "not slow"     # fast unit/integration
python -m pytest -m "slow"         # real-PDF integration (Book 1 section)
python -m pytest                   # everything (117 tests)

# `slow` / `requires_demo` tests need the real demo documents — point
# PARSEL_DEMO_DIR at your local copy (they skip cleanly if it's unset/missing):
#   $env:PARSEL_DEMO_DIR = "C:\path\to\Spare parts documents"
```

## What the demo documents told us (and what we changed)

Analyzing the real files surfaced issues that would have **broken the live demo**:

| Finding | File | Impact | Fix |
|---|---|---|---|
| **Scanned, image-only PDF** (no text layer) | `05 1.pdf` (46 pp) | Text extraction returns **0 rows silently** | Added scanned-page **detection** + **built-in OCR** (RapidOCR, bundled — no Tesseract needed) that rebuilds rows/columns from word boxes, plus a clear warning if a page still yields no parts table |
| **`pdfplumber.open()` took ~13 min on a 492-pp manual** (fitz opens it in 0.2s) — froze the whole app on "Opening PDF…" | `Book 1.pdf` (492 pp) | App appeared hung for 13+ min per file | **Removed pdfplumber entirely**; PyMuPDF table finder + text-block pass give the same/better result in seconds (217 rows, 100% Part Name/Pos filled on the parts section) |
| **Dimension regex ate part codes**: `SUS304 M10x20` → `SUS10x20` (bare `m` unit matched the `M`) | rule logic | Corrupted part names | Tightened regex: unit must be followed by a word boundary |
| **Alias conflict**: `Drawing No.` listed under `Text` overwrote the real `DrawingNo` mapping | config | `DrawingNo` mis-mapped to `Text` | Removed conflicting aliases; schema names protected |

These four were caught **before** the demo, by the tests.

### Output-quality fixes (found by generating a real Excel from `Book 1.pdf`)

| Problem in first real output | Fix |
|---|---|
| Main sheet had extra `[UNMAPPED]` columns (page titles, `col_1`) → not the strict 14-column format | Main "Spare Parts" sheet is now **exactly the 14-column schema**; unmapped data moves to a separate "Unmapped (review)" sheet |
| `Spare Group` showed `"MAN B&W \| Item No. \| Item Designation"` (header junk) | Section title now found via the **"Plate"/"Drawing" locator** scanned across the whole page → correct titles like `End-Chock Bolts Tools` |
| Noise rows: `"nan"`, `"(2)"` footer page numbers | Noise-row filter drops page-number / `nan` / empty rows |
| `nan` strings appearing in cells | NaN / `"nan"` / `"none"` normalized to empty |
| Speed: 492-page book ≈ 27 min | Fast-path escalation (cheap pass first) + **page-range** option; a section runs in seconds |

Verified clean output (page 96 of `Book 1.pdf`):

```
Part Name                    Pos   Spare Group             RefPage
Hydraulic Jack, Complete     015   End-Chock Bolts Tools   96
Support for Hydraulic Jack   064   End-Chock Bolts Tools   96   (note: "for" lowercased)
Sealing Ring with Back-up    123   End-Chock Bolts Tools   96   (note: "with" lowercased)
```

## Coverage of real-world headers

The two mapping Word docs (`Part Name 1.docx`, `Description of Spares.docx`)
document **92 real header aliases** seen across many makers' manuals. Extracted
to `tests/data/golden_aliases.json` and asserted in `test_semantic_mapper.py`.

- **89%** of the 92 aliases map automatically with **exact + fuzzy** (no ML model).
- The remainder are genuinely ambiguous compound headers
  (`"Dimension / Material / Remark"`) or label collisions (`"Part No."` means
  *Spare Part No* in most manuals but *Drawing Pos No* in one) — these are
  resolved one-click in **Settings ▸ Mappings** per manual.

## Test files

| File | Covers |
|---|---|
| `test_transformer.py` | Every checklist rule: proper case, conjunction lowercase, `L=`/`Dia.` dimensions, `Matl.` prefix vs Internal Remark, null padding, schema order, unmapped-column preservation, context stamping |
| `test_semantic_mapper.py` | Exact / fuzzy 2-tier cascade; all 92 golden aliases; ambiguity handling; internal-column skipping |
| `test_qc_validator.py` | WIP_Tracker format, QC-report CSV for missing key fields, language-flag off-by-default + short-string guard |
| `test_pdf_extractor.py` | Header **scoring** (clean beats garbage), row→DataFrame (blank/dup/ragged headers), known-header index |
| `test_pipeline.py` | Full extract→map→transform→QC→Excel on synthetic PDFs; corrupt PDF, scanned PDF, locked Excel, empty table; **output schema == target xlsx** |
| `test_output_quality.py` | Noise-row removal, NaN handling, strict 14-col main sheet + separate unmapped sheet, section-title extraction via "Plate" locator, candidate filtering |
| `test_real_documents.py` (`slow`) | The actual `Book 1.pdf` (clean headers, real part names map to schema) and `05 1.pdf` (detected as scanned), plus schema match vs `New Format (1) 2.xlsx` |

## Known limitations (state these honestly in the demo)

1. **Scanned manuals** are read with **built-in OCR** (RapidOCR, bundled — no
   Tesseract needed). OCR output is approximate and flagged for QC; row quality
   on dense/text-heavy scanned pages is rougher than on digital PDFs.
2. **Ambiguous headers** default to the most common meaning; correct per-manual
   exceptions via Settings ▸ Mappings.
3. **Unusual headers** not in the 92-alias list are listed as unmapped for
   one-click linking in Settings ▸ Mappings (column mapping is exact + fuzzy,
   ~89% auto-coverage on real data; no ML model).

## Generate a real demo artifact

```powershell
python scripts/generate_demo_output.py "<path to your manual>.pdf"
```

Produces `Book 1_PROCESSED.xlsx` with the exact 14-column schema, ready to show.
