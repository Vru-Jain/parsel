"""
engine/pdf_extractor.py
-----------------------
Layout-aware PDF table extraction. PyMuPDF only — no Ghostscript, no OpenCV,
and no pdfplumber (it added nothing on these manuals yet `pdfplumber.open()`
could take ~13 min on some files, e.g. Book 1.pdf).

Strategy (fast-path, never returns silently-empty):
  Pass 1: text-block reconstruction (~15 ms) — for digital PDFs whose "tables"
          are just column-less text runs (e.g. MAN B&W "Item No./Designation").
  Pass 2: PyMuPDF native table finder — only if Pass 1 isn't already confident.

Scanned (image-only) pages have NO text layer; PyMuPDF cannot read them. We
DETECT these explicitly and (a) report them clearly, and (b) OCR them with the
BUILT-IN engine RapidOCR (ONNX models ship in the pip package, run offline on
CPU, no system binary and no LLM). Tesseract is an optional fallback if present.
Detection means a scanned manual is never mistaken for "empty".

Per page we also scrape the top 15% / bottom 15% bands with PyMuPDF to capture
context (Manufacturer, page title / Spare Group) and stamp it onto every row
extracted from that page.

Every page is wrapped in try/except: one bad page cannot abort the whole file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

import fitz  # PyMuPDF
import pandas as pd


@dataclass
class PageResult:
    page_number: int                 # 1-based
    dataframe: pd.DataFrame          # raw extracted table (string cells)
    manufacturer: str = ""
    spare_group: str = ""            # page title / section heading
    method: str = ""                 # which pass succeeded
    warning: str = ""                # non-fatal note for the UI/log


@dataclass
class ExtractionResult:
    frames: list[PageResult] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)   # "page N: <reason>"
    total_pages: int = 0
    scanned_pages: list[int] = field(default_factory=list)  # 1-based, no text layer
    ocr_used: bool = False
    # reconciliation: pages where an independent item-number count exceeded the
    # rows we actually extracted -> possible missed rows. (page_no, seen, got)
    reconciliation: list[tuple[int, int, int]] = field(default_factory=list)

    @property
    def combined(self) -> pd.DataFrame:
        non_empty = [pr.dataframe for pr in self.frames if not pr.dataframe.empty]
        if not non_empty:
            return pd.DataFrame()
        return pd.concat(non_empty, ignore_index=True)

    @property
    def is_scanned(self) -> bool:
        """True if most pages are image-only (a scanned manual)."""
        if self.total_pages == 0:
            return False
        return len(self.scanned_pages) >= max(1, self.total_pages * 0.6)


ProgressCb = Optional[Callable[[int, int, str], None]]  # (current, total, message)


# ---------------------------------------------------------------------------
# Context band scraping (header / footer)
# ---------------------------------------------------------------------------

def _collect_band_lines(page: "fitz.Page") -> tuple[list[str], list[str]]:
    """Return (top_lines, bottom_lines) from the top/bottom 18% bands."""
    rect = page.rect
    h = rect.height
    top_band = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + h * 0.18)
    bottom_band = fitz.Rect(rect.x0, rect.y1 - h * 0.18, rect.x1, rect.y1)

    def _lines(band: "fitz.Rect") -> list[str]:
        try:
            txt = page.get_text("text", clip=band) or ""
        except Exception:
            txt = ""
        return [ln.strip() for ln in txt.splitlines() if ln.strip()]

    return _lines(top_band), _lines(bottom_band)


_MAKER_HINTS = ("co.,", "ltd", "inc", "gmbh", "kabushiki", "kaisha", "industries",
                "mfg", "manufactur", "corporation", "company", "b&w")

# lines that are never a section title
_TITLE_STOPWORDS = ("plate", "page", "table of contents", "chapter", "preface",
                    "item no", "item designation", "designation", "description",
                    "part no", "pos no", "drawing", "edition", "section")
_TITLE_LOCATORS = ("plate", "drawing", "title", "group")
_CODE_LINE = re.compile(r"^[\d\s\-./()]+$")           # numbers/codes only
_DATE_LINE = re.compile(r"\d{4}-\d{2}-\d{2}|-\s*en\b", re.I)


def _guess_manufacturer(lines: list[str]) -> str:
    for line in lines:
        low = line.lower()
        if any(hint in low for hint in _MAKER_HINTS):
            return line.strip()
    return ""


def _is_title_candidate(line: str) -> bool:
    low = line.lower().strip()
    if len(low) < 3:
        return False
    if _CODE_LINE.match(line) or _DATE_LINE.search(line):
        return False
    if any(sw in low for sw in _TITLE_STOPWORDS):
        return False
    if any(h in low for h in _MAKER_HINTS):
        return False
    # need at least a couple of letters
    if sum(c.isalpha() for c in low) < 3:
        return False
    return True


def _extract_plate_info(top: list[str], bottom: list[str],
                        all_lines: list[str] | None,
                        plate_re: "re.Pattern") -> tuple[str, str]:
    """
    Return (section_title, plate_number) from the plate caption block, e.g.

        Plate
        Piston Cooling Arrangement   -> title (line just above the number)
        1072-1400-0002               -> plate number (drawing no)

    We anchor on the PLATE NUMBER, not the word "Plate" — because these manuals
    contain parts literally named "Plate" (an item designation) that would
    otherwise be mistaken for the locator. The section title is the nearest
    title-candidate line directly above the plate number. We deliberately do NOT
    fall back to "longest line" (that pulls part descriptions from the table
    body); an empty title beats a wrong one.
    """
    # Full-page lines first (complete caption structure), then the bottom band.
    blocks = []
    if all_lines:
        blocks.append(all_lines)
    blocks.append(bottom)

    first_plate = ""
    for block in blocks:
        for idx, line in enumerate(block):
            m = plate_re.search(line)
            if not m:
                continue
            if not first_plate:
                first_plate = m.group(0)
            # Walk up to 3 lines above the number; the title is the first
            # title-candidate, but only TRUST it if a 'Plate'/'Drawing' anchor
            # sits just above (a genuine caption block). The number is repeated
            # as a footer and appears on parts-list pages without the caption,
            # so keep scanning until an anchored caption is found.
            title, anchored = "", False
            for back in range(idx - 1, max(-1, idx - 4), -1):
                low = block[back].lower().strip()
                if any(low == loc or low.startswith(loc + " ") or low == loc + ":"
                       for loc in _TITLE_LOCATORS):
                    anchored = True
                    break
                if not title and _is_title_candidate(block[back]):
                    title = block[back].strip()
            if anchored and title:
                return title, m.group(0)
    return "", first_plate


# ---------------------------------------------------------------------------
# Table -> DataFrame helpers
# ---------------------------------------------------------------------------

def _rows_to_df(rows: list[list]) -> pd.DataFrame:
    """First non-empty row becomes the header; remaining rows are data."""
    cleaned = []
    for row in rows:
        cleaned.append(["" if c is None else str(c).strip() for c in row])
    cleaned = [r for r in cleaned if any(cell for cell in r)]
    if len(cleaned) < 2:
        return pd.DataFrame()

    header = cleaned[0]
    # de-duplicate / fill blank header cells so pandas doesn't choke
    seen: dict[str, int] = {}
    norm_header = []
    for i, name in enumerate(header):
        name = name or f"col_{i}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        norm_header.append(name)

    width = len(norm_header)
    body = []
    for r in cleaned[1:]:
        if len(r) < width:
            r = r + [""] * (width - len(r))
        elif len(r) > width:
            r = r[:width]
        body.append(r)

    return pd.DataFrame(body, columns=norm_header)


def _best_df(candidates: list[pd.DataFrame]) -> pd.DataFrame:
    """Pick the candidate with the most cells filled (rows*cols, weighted by fill)."""
    best = pd.DataFrame()
    best_score = 0.0
    for df in candidates:
        if df.empty:
            continue
        cells = df.shape[0] * df.shape[1]
        if cells == 0:
            continue
        filled = (df.astype(str).apply(lambda s: s.str.strip() != "")).sum().sum()
        score = filled  # raw count of non-empty cells
        if score > best_score:
            best_score = score
            best = df
    return best


# ---------------------------------------------------------------------------
# Quality scoring: choose the extraction whose HEADERS look most like real
# spare-parts columns. This lets the fast text-block pass win when it produces
# a clean "No./Description" and fall back to PyMuPDF's finder otherwise.
# ---------------------------------------------------------------------------

_GARBAGE_HEADER = re.compile(r"^(col_\d+|man\s*b&w|\d[\d\-/]{4,}|\d+\s*\(\d+\))$")


def _norm_header(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def build_known_headers(config: dict) -> set[str]:
    """Normalized set of every schema column + every alias, for header scoring."""
    known: set[str] = set()
    for col in config.get("target_schema", []):
        known.add(_norm_header(col))
    for col, aliases in (config.get("header_aliases", {}) or {}).items():
        known.add(_norm_header(col))
        for a in aliases:
            known.add(_norm_header(a))
    known.discard("")
    return known


def score_dataframe(df: pd.DataFrame, known_headers: set[str]) -> float:
    """
    Higher = better extraction. Rewards recognizable headers + filled cells,
    penalizes garbage headers (drawing numbers, 'MAN B&W', 'col_N', single chars).
    """
    if df is None or df.empty:
        return 0.0

    score = 0.0
    headers = [str(c) for c in df.columns]
    for h in headers:
        nh = _norm_header(h)
        hl = h.strip().lower()
        if not nh:
            score -= 2
        elif _GARBAGE_HEADER.match(hl):
            score -= 8                     # strong penalty: clearly wrong header
        elif nh in known_headers:
            score += 12                    # exact recognizable column name
        elif len(nh) <= 1:
            score -= 3                     # fragment like "s", "t"
        else:
            # partial credit if a known header is contained / contains it
            if any(nh in k or k in nh for k in known_headers if len(k) > 2):
                score += 4
            else:
                score += 0.5

    # cell-fill reward (normalized so a huge garbage table can't dominate)
    cells = df.shape[0] * df.shape[1]
    if cells:
        filled = (df.astype(str).apply(lambda s: s.str.strip() != "")).sum().sum()
        score += 3.0 * (filled / cells)

    # penalize tables that are basically one fragmented column of noise
    if df.shape[1] == 1 and df.shape[0] < 2:
        score -= 5

    return score


# ---------------------------------------------------------------------------
# Per-page extraction passes
# ---------------------------------------------------------------------------

def _extract_pymupdf_page(mu_page: "fitz.Page") -> tuple[pd.DataFrame, str]:
    """PyMuPDF's built-in table finder as the final fallback."""
    try:
        finder = mu_page.find_tables()
    except Exception:
        return pd.DataFrame(), ""

    candidates = []
    for tbl in getattr(finder, "tables", []) or []:
        try:
            rows = tbl.extract()
        except Exception:
            continue
        df = _rows_to_df(rows)
        if not df.empty:
            candidates.append(df)

    best = _best_df(candidates)
    return best, ("pymupdf:find_tables" if not best.empty else "")


# ---------------------------------------------------------------------------
# Scanned-page detection + optional OCR (Tesseract, offline, no LLM)
# ---------------------------------------------------------------------------

# RapidOCR (ONNX) is the BUILT-IN engine: pip-installed, models ship inside the
# package, runs offline on CPU, NO system binary and NO LLM. Tesseract is an
# optional fallback if someone has it installed.
_RAPIDOCR = None
_RAPIDOCR_TRIED = False


def _get_rapidocr():
    """Lazy singleton. Loads the ONNX models once; returns engine or None."""
    global _RAPIDOCR, _RAPIDOCR_TRIED
    if _RAPIDOCR_TRIED:
        return _RAPIDOCR
    _RAPIDOCR_TRIED = True
    try:
        from rapidocr_onnxruntime import RapidOCR
        _RAPIDOCR = RapidOCR()
    except Exception:
        _RAPIDOCR = None
    return _RAPIDOCR


def _tesseract_available() -> bool:
    try:
        import pytesseract  # noqa: F401
        from shutil import which
        cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
        return bool(which(cmd) or which("tesseract"))
    except Exception:
        return False


def _ocr_available() -> bool:
    """True if any built-in OCR engine is usable (RapidOCR bundled, or Tesseract).
    Cheap: only checks import availability, does not load the models."""
    try:
        import importlib.util
        if importlib.util.find_spec("rapidocr_onnxruntime") is not None:
            return True
    except Exception:
        pass
    return _tesseract_available()


def _ocr_page_to_df(mu_page: "fitz.Page") -> tuple[pd.DataFrame, str]:
    """OCR a scanned page into a table. Prefers RapidOCR (coordinate-aware
    row/column reconstruction); falls back to Tesseract plain-text lines."""
    eng = _get_rapidocr()
    if eng is not None:
        df = _rapidocr_page_to_df(mu_page, eng)
        if not df.empty:
            return df, "ocr:rapidocr"
        return pd.DataFrame(), ""
    if _tesseract_available():
        return _tesseract_page_to_df(mu_page)
    return pd.DataFrame(), ""


def _rapidocr_page_to_df(mu_page: "fitz.Page", eng) -> pd.DataFrame:
    try:
        import numpy as np
        pix = mu_page.get_pixmap(dpi=200)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = img[:, :, :3]
        elif pix.n == 1:
            img = np.repeat(img, 3, axis=2)
        res, _ = eng(np.ascontiguousarray(img))
    except Exception:
        return pd.DataFrame()
    if not res:
        return pd.DataFrame()
    return _boxes_to_table(res, pix.width)


# header tokens we expect on a parts page — used to find the header row
_OCR_HEADER_TOKENS = {
    "no", "name", "type", "qt", "qty", "plate", "remaker", "remark", "remarks",
    "designation", "description", "item", "itemno", "part", "partno",
    "partnumber", "pos", "posno", "drawing", "ref", "refno", "code", "material",
    "maker", "unit", "spec",
}

# A scanned row whose text begins with one of these is a caution/instruction/
# note block, not a spare part — dropped so it doesn't pollute the output.
_OCR_ROW_SKIP_PREFIXES = (
    "caution", "warning", "danger", "notice", "note", "attention",
    "instruction", "please", "do not", "important", "remark",
)


def _boxes_to_table(res, page_w: int) -> pd.DataFrame:
    """Reconstruct a table from RapidOCR boxes: cluster cells into rows (by y)
    and columns (by x), detect the header row, return a DataFrame."""
    cells, heights = [], []
    for box, text, score in res:
        t = (text or "").strip()
        if not t or (score is not None and score < 0.4):
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        cells.append({"cx": sum(xs) / 4.0, "cy": sum(ys) / 4.0, "t": t})
        heights.append(max(ys) - min(ys))
    if len(cells) < 4:
        return pd.DataFrame()

    hmed = sorted(heights)[len(heights) // 2] or 12
    ytol = max(8.0, hmed * 0.7)

    # ---- cluster rows by y (running mean) ----
    cells.sort(key=lambda c: c["cy"])
    rows, cur, cur_y = [], [cells[0]], cells[0]["cy"]
    for c in cells[1:]:
        if abs(c["cy"] - cur_y) <= ytol:
            cur.append(c)
            cur_y = sum(x["cy"] for x in cur) / len(cur)
        else:
            rows.append(cur)
            cur, cur_y = [c], c["cy"]
    rows.append(cur)

    # ---- cluster columns by x (gap split) ----
    centers = sorted(c["cx"] for c in cells)
    colgap = max(40.0, page_w * 0.035)
    grp, colcenters = [centers[0]], []
    for x in centers[1:]:
        if x - grp[-1] <= colgap:
            grp.append(x)
        else:
            colcenters.append(sum(grp) / len(grp))
            grp = [x]
    colcenters.append(sum(grp) / len(grp))
    ncol = len(colcenters)

    def col_of(cx):
        return min(range(ncol), key=lambda i: abs(cx - colcenters[i]))

    grid = []
    for row in rows:
        bycol = {}
        for c in sorted(row, key=lambda c: c["cx"]):
            i = col_of(c["cx"])
            bycol[i] = (bycol.get(i, "") + " " + c["t"]).strip()
        grid.append(bycol)

    # ---- find the header row (most cells that look like known headers) ----
    def _norm(s):
        return re.sub(r"[^a-z0-9]", "", s.lower())

    best_i, best_score = -1, 0
    for ri, g in enumerate(grid):
        sc = sum(1 for v in g.values() if _norm(v) in _OCR_HEADER_TOKENS)
        if sc > best_score:
            best_score, best_i = sc, ri

    # No recognizable parts header (caution page, nameplate/spec sheet, prose)
    # -> not a parts table. Returning empty makes the page report "no table"
    # instead of dumping generic, jumbled col0/col1 rows.
    if best_score < 2:
        return pd.DataFrame()

    header, data_rows = grid[best_i], grid[best_i + 1:]

    colnames, seen = [], {}
    for i in range(ncol):
        nm = (header.get(i, "") or f"col{i}").strip() or f"col{i}"
        if nm in seen:
            seen[nm] += 1
            nm = f"{nm}_{seen[nm]}"
        else:
            seen[nm] = 0
        colnames.append(nm)

    records = []
    for g in data_rows:
        cells = [g.get(i, "").strip() for i in range(ncol)]
        nonempty = [c for c in cells if c]
        if not nonempty:
            continue
        low = [c.lower() for c in nonempty]
        # drop a leaked secondary header row (e.g. the "TYPE / QT." band)
        if sum(1 for c in low if _norm(c) in _OCR_HEADER_TOKENS) >= 2:
            continue
        # drop caution / instruction / note prose
        if any(c.startswith(_OCR_ROW_SKIP_PREFIXES) for c in low):
            continue
        records.append({colnames[i]: cells[i] for i in range(ncol)})
    return pd.DataFrame(records) if records else pd.DataFrame()


def _tesseract_page_to_df(mu_page: "fitz.Page") -> tuple[pd.DataFrame, str]:
    """Fallback: Tesseract plain-text lines under a synthetic Description col."""
    try:
        import io
        import pytesseract
        from PIL import Image
        pix = mu_page.get_pixmap(dpi=300)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(img) or ""
    except Exception:
        return pd.DataFrame(), ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return pd.DataFrame(), ""
    return pd.DataFrame({"Description": lines}), "ocr:tesseract"


# ---------------------------------------------------------------------------
# Text-block reconstruction (digital PDFs with column-less text "tables")
# ---------------------------------------------------------------------------

_ITEM_NO_RE = re.compile(r"^\d{1,4}[A-Za-z]?$")


def _extract_textblocks_page(mu_page: "fitz.Page") -> tuple[pd.DataFrame, str]:
    """
    Reconstruct a parts table from positioned words for the common MAN B&W
    "Item No. / Item Designation" layout.

    LAYOUT-ANCHORED (not line-anchored): we find the item-number COLUMN by the
    x-position of the short numeric tokens, then read each item's description as
    the words to the RIGHT of its number on the same row. This recovers rows
    that a naive "line must start with a number" rule loses when a left-column
    sub-heading (e.g. "Bolts") clusters onto the row, and it ignores left-margin
    group headings and page footers automatically.

    Rows are clustered by vertical center with a running mean (robust to the
    small y-jitter that a fixed-bin rounding would split across two rows).
    """
    try:
        words = mu_page.get_text("words")  # (x0,y0,x1,y1,word,block,line,wordno)
    except Exception:
        return pd.DataFrame(), ""
    toks = [(w[0], w[1], w[3], str(w[4]).strip()) for w in words if str(w[4]).strip()]
    if len(toks) < 4:
        return pd.DataFrame(), ""

    # ---- cluster tokens into rows by vertical center (running mean) ----
    toks.sort(key=lambda t: (t[1] + t[2]) / 2.0)
    heights = sorted(t[2] - t[1] for t in toks if t[2] > t[1])
    hmed = heights[len(heights) // 2] if heights else 10.0
    ytol = max(4.0, hmed * 0.6)
    bands: list[list[tuple[float, str]]] = []
    cur = [toks[0]]
    cur_y = (toks[0][1] + toks[0][2]) / 2.0
    for t in toks[1:]:
        cy = (t[1] + t[2]) / 2.0
        if abs(cy - cur_y) <= ytol:
            cur.append(t)
            cur_y = sum((x[1] + x[2]) / 2.0 for x in cur) / len(cur)
        else:
            bands.append(sorted([(c[0], c[3]) for c in cur], key=lambda p: p[0]))
            cur = [t]
            cur_y = cy
    bands.append(sorted([(c[0], c[3]) for c in cur], key=lambda p: p[0]))

    # ---- locate the item-number column (median x of numeric tokens) ----
    num_xs = sorted(x for b in bands for (x, t) in b if _ITEM_NO_RE.match(t))
    if len(num_xs) < 2:
        return pd.DataFrame(), ""
    numcol = num_xs[len(num_xs) // 2]
    xtol = 30.0

    # ---- build rows; description = words to the right of the number ----
    rows: list[list[str]] = []
    cur_row: list[str] | None = None
    for b in bands:
        numtok = next(((x, t) for (x, t) in b
                       if _ITEM_NO_RE.match(t) and abs(x - numcol) <= xtol), None)
        if numtok:
            desc = " ".join(t for (x, t) in b if x > numtok[0] + 2)
            cur_row = [numtok[1], desc.strip()]
            rows.append(cur_row)
        elif cur_row is not None:
            # continuation line: a wrapped description sits in the description
            # column (right of the number column) with no number of its own.
            cont = " ".join(t for (x, t) in b
                            if x > numcol + 2 and not _ITEM_NO_RE.match(t))
            if cont:
                cur_row[1] = (cur_row[1] + " " + cont).strip()

    rows = [r for r in rows if r[1]]   # drop bare numbers with no description
    if len(rows) >= 2:
        return pd.DataFrame(rows, columns=["No.", "Description"]), "textblocks:item-designation"
    return pd.DataFrame(), ""


def count_item_anchors(mu_page: "fitz.Page") -> int:
    """Independent estimate of how many parts rows a born-digital page holds:
    the number of row-bands that carry a numeric token in the dominant numeric
    (item-number) column. Used purely for reconciliation — compared against the
    rows we actually extracted so silent losses become a visible QC flag. Does
    NOT depend on the extraction parser succeeding."""
    try:
        words = mu_page.get_text("words")
    except Exception:
        return 0
    toks = [(w[0], w[1], w[3], str(w[4]).strip()) for w in words if str(w[4]).strip()]
    if len(toks) < 4:
        return 0
    num_tok = [(w[0], (w[1] + w[2]) / 2.0) for w in toks if _ITEM_NO_RE.match(w[3])]
    if len(num_tok) < 2:
        return 0
    xs = sorted(x for x, _ in num_tok)
    numcol = xs[len(xs) // 2]
    # distinct rows = numeric tokens near the column, clustered by y (>6pt apart)
    ys = sorted(cy for x, cy in num_tok if abs(x - numcol) <= 30.0)
    rows = 1
    for prev, nxt in zip(ys, ys[1:]):
        if nxt - prev > 6.0:
            rows += 1
    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_page_spec(text: str, total: int | None = None) -> set[int]:
    """Parse a human page selection into a set of 1-based page numbers.

    Accepts comma/space separated single pages and ranges, e.g.:
        "1-5, 12, 20-30"   ->  {1,2,3,4,5,12,20,...,30}
        "7"                ->  {7}
    Open-ended ranges are supported: "100-" means 100..total, "-5" means 1..5.
    Empty / whitespace input returns an empty set (caller treats that as "all").
    `total`, if given, clamps the result to [1, total]. Raises ValueError on
    genuinely malformed input so the UI can show a clear message.
    """
    if not text or not text.strip():
        return set()
    pages: set[int] = set()
    for chunk in re.split(r"[,\s]+", text.strip()):
        if not chunk:
            continue
        if "-" in chunk:
            lo_s, hi_s = chunk.split("-", 1)
            lo = int(lo_s) if lo_s.strip() else 1
            hi = int(hi_s) if hi_s.strip() else (total or 10 ** 9)
            if hi < lo:
                lo, hi = hi, lo
            pages.update(range(lo, hi + 1))
        else:
            pages.add(int(chunk))
    if total is not None:
        pages = {p for p in pages if 1 <= p <= total}
    else:
        pages = {p for p in pages if p >= 1}
    return pages


def extract_pdf(
    filepath: str,
    config: dict,
    progress_cb: ProgressCb = None,
    page_range: tuple[int, int] | None = None,
    pages: set[int] | None = None,
) -> ExtractionResult:
    """
    Extract all tables from `filepath`. Robust to per-page failures and to a
    fully corrupt/encrypted file (raises a clean RuntimeError in that case).

    pages: optional set of 1-based page numbers to process (lets the user pick
    exactly the pages that hold spare-parts tables). Takes precedence over
    page_range. None/empty = use page_range, or the whole document.
    page_range: optional (first, last) 1-based inclusive range — kept for
    back-compat and as a convenient "just a section" shortcut.
    """
    result = ExtractionResult()

    # Open with PyMuPDF (context bands + fallback). Fail loudly if unreadable.
    try:
        mu_doc = fitz.open(filepath)
    except Exception as exc:  # encrypted / corrupt / not a pdf
        raise RuntimeError(f"Cannot open PDF: {exc}") from exc

    if mu_doc.needs_pass:
        mu_doc.close()
        raise RuntimeError("PDF is password-protected. Remove the password and retry.")

    total = mu_doc.page_count
    result.total_pages = total

    # resolve the pages to process -> a sorted list of 0-based indices.
    # priority: explicit `pages` set > `page_range` > whole document.
    if pages:
        selected_idx = sorted(p - 1 for p in pages if 1 <= p <= total)
        if not selected_idx:                       # all out of range -> whole doc
            selected_idx = list(range(total))
    elif page_range:
        first, last = page_range
        start_idx = max(0, int(first) - 1)
        end_idx = min(total, int(last))
        if start_idx >= end_idx:
            start_idx, end_idx = 0, total
        selected_idx = list(range(start_idx, end_idx))
    else:
        selected_idx = list(range(total))
    n_selected = len(selected_idx)

    opts = config.get("options", {}) or {}
    min_text_chars = int(opts.get("min_text_chars_per_page", 20))
    enable_ocr = bool(opts.get("enable_ocr", True))
    ocr_ok = enable_ocr and _ocr_available()
    score_confident = float(opts.get("score_confident", 18.0))
    plate_re = re.compile(opts.get("plate_number_regex", r"\b\d{3,4}-\d{3,4}-\d{2,4}\b"))
    skip_markers = [m.lower() for m in opts.get("skip_page_markers", [])]

    known_headers = build_known_headers(config)
    # plate number -> section title, learned from drawing/caption pages and
    # propagated to the parts-list pages that share the same plate number.
    plate_to_title: dict[str, str] = {}

    try:
        for done, i in enumerate(selected_idx, start=1):
            page_no = i + 1
            if progress_cb:
                progress_cb(done, n_selected,
                            f"Extracting page {page_no} ({done}/{n_selected})")

            try:
                mu_page = mu_doc.load_page(i)
                # Extract the page text ONCE and reuse it for scan-detection,
                # title and context (avoids re-parsing the page 3-4×/page).
                try:
                    page_text = mu_page.get_text("text") or ""
                except Exception:
                    page_text = ""

                drawing_no = ""
                # --- scanned-page detection (no text layer) ---
                if len(page_text.strip()) < min_text_chars:
                    result.scanned_pages.append(page_no)
                    manufacturer = spare_group = ""
                    if not ocr_ok:
                        result.page_errors.append(
                            f"page {page_no}: scanned/image-only (no text layer)"
                        )
                        continue
                    df, method = _ocr_page_to_df(mu_page)
                    result.ocr_used = result.ocr_used or (not df.empty)
                    if df.empty:
                        result.page_errors.append(
                            f"page {page_no}: scanned, OCR found no text"
                        )
                        continue
                else:
                    # --- skip non-parts pages (work cards, TOC, instructions) ---
                    page_low = page_text.lower()
                    if skip_markers and any(mk in page_low for mk in skip_markers):
                        result.page_errors.append(
                            f"page {page_no}: skipped (non-parts page)"
                        )
                        continue

                    # context only needed for real (text) pages
                    all_lines = [ln.strip() for ln in page_text.splitlines()
                                 if ln.strip()]
                    top_lines, bot_lines = _collect_band_lines(mu_page)
                    manufacturer = _guess_manufacturer(top_lines + bot_lines)
                    spare_group, drawing_no = _extract_plate_info(
                        top_lines, bot_lines, all_lines, plate_re)
                    # learn title<->plate even if this page has no parts table
                    # (drawing pages carry the title; parts pages carry the rows)
                    if spare_group and drawing_no:
                        plate_to_title.setdefault(drawing_no, spare_group)

                    # --- fast-path escalation: cheap pass first, only run the
                    # slower PyMuPDF table finder if not yet confident. This
                    # avoids paying PyMuPDF's ~1s/page on the many pages the
                    # ~15ms text-block pass already handles well. ---
                    df, method, best = pd.DataFrame(), "", float("-inf")

                    def _consider(d, m):
                        nonlocal df, method, best
                        if d is not None and not d.empty:
                            s = score_dataframe(d, known_headers)
                            if s > best:
                                best, df, method = s, d, m

                    # 1) text-block reconstruction (~15 ms)
                    d, m = _extract_textblocks_page(mu_page)
                    _consider(d, m)

                    # 2) PyMuPDF table finder (~1 s) unless already confident
                    if best < score_confident:
                        d, m = _extract_pymupdf_page(mu_page)
                        _consider(d, m)

                    if df.empty:
                        result.page_errors.append(f"page {page_no}: no table detected")
                        continue

                    # reconciliation: did we extract a row per item number?
                    seen = count_item_anchors(mu_page)
                    if seen > len(df):
                        result.reconciliation.append((page_no, seen, len(df)))

                # stamp page-level context onto every row
                df = df.copy()
                df["__manufacturer__"] = manufacturer
                df["__spare_group__"] = spare_group
                df["__drawing_no__"] = drawing_no
                df["__ref_page__"] = str(page_no)

                result.frames.append(
                    PageResult(
                        page_number=page_no,
                        dataframe=df,
                        manufacturer=manufacturer,
                        spare_group=spare_group,
                        method=method,
                    )
                )
            except Exception as exc:  # never let one page kill the run
                result.page_errors.append(f"page {page_no}: {exc}")
                continue
    finally:
        mu_doc.close()

    # second pass: propagate section titles across the drawing/parts-list spread
    # via the shared plate number, so parts rows inherit the correct Spare Group.
    if plate_to_title:
        for fr in result.frames:
            d = fr.dataframe
            if "__drawing_no__" not in d.columns or d.empty:
                continue
            plate = str(d["__drawing_no__"].iloc[0])
            title = plate_to_title.get(plate)
            if title:
                d["__spare_group__"] = title
                fr.spare_group = title

    return result
