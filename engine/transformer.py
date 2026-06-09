"""
engine/transformer.py
---------------------
Deterministic Pandas + regex transformation pipeline. No LLM.

Given a raw extracted DataFrame and a column mapping, produce a clean DataFrame
that conforms exactly to config.target_schema. Steps (config-driven):

  0. Apply mapping (raw header -> schema column), stamp page context columns.
  1. Title-Case normalization of Part Name (blacklist words forced lowercase).
  2. Dimension extraction from text -> "L= .." / "Dia. .." into Text column.
  3. Material tagging: short material -> "Matl. " prefix on Part Name;
     long material -> moved to Internal Remark.
  4. Null padding: ensure every schema column exists (fill "").

Every row-level transform is wrapped so one malformed cell cannot abort the run.
"""
from __future__ import annotations

import re
from typing import Optional

import pandas as pd


_DEFAULT_DIM_RE = r"(\d+(?:[.,]\d+)?)\s*(?:mm|cm|meter|metre|inch|in|\")(?![A-Za-z0-9])"

# Control characters openpyxl refuses to write to a worksheet. PDF text often
# carries stray ones (e.g. a form-feed inside "MAN B&W"); leaving them in makes
# the whole Excel export crash with IllegalCharacterError, so we strip them.
_ILLEGAL_XLSX_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class Transformer:
    def __init__(self, config: dict):
        self.config = config
        self.schema: list[str] = list(config.get("target_schema", []))
        rules = config.get("rules", {}) or {}
        self.blacklist = {w.lower() for w in rules.get("title_case_blacklist", [])}
        self.materials = list(rules.get("recognized_materials", []))
        self.max_material_len = int(rules.get("max_material_length_for_prefix", 15))
        self.dim_re = re.compile(rules.get("dimension_regex", _DEFAULT_DIM_RE), re.IGNORECASE)
        # longest materials first so "Stainless Steel" wins over "Steel"
        self._materials_sorted = sorted(self.materials, key=len, reverse=True)

    # ------------------------------------------------------------------ #
    def transform(self, raw_df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
        if raw_df is None or raw_df.empty:
            return self._empty_schema_frame()

        df = raw_df.copy()

        # ---- Step 0: rename mapped columns, capture context, keep unmapped
        page_ctx = self._pop_context(df)

        # Build every column into a dict first, then construct the DataFrame in
        # ONE shot. Assigning column-by-column to a DataFrame fragments it and is
        # O(rows) per insert — on a 492-page book that means 100+ inserts and a
        # storm of pandas PerformanceWarnings. One construction = one fast frame.
        built: dict[str, pd.Series] = {}
        for raw_col in df.columns:
            target = mapping.get(raw_col)
            cleaned = self._clean_series(df[raw_col])
            if target and target in self.schema:
                # Several source columns can map to one schema column — most
                # often Material / Type / Maker / Spec / Size / Remark / Standard
                # all routing into `Text`. Operators bundle those into one cell,
                # so we join them with "; " and skip blanks (no "A; " or "; B").
                if target in built:
                    built[target] = self._join_series(built[target], cleaned)
                else:
                    built[target] = cleaned
            else:
                # unmapped: keep under a clearly-flagged column (never dropped)
                built[f"[UNMAPPED] {raw_col}"] = cleaned

        out = pd.DataFrame(built, index=df.index)

        # stamp page-level context (only where schema column is empty/missing)
        self._apply_context(out, page_ctx)
        out = out.copy()   # defragment after the few context inserts

        # ---- Step 1..3 row transforms
        out = self._title_case(out)
        out = self._extract_dimensions(out)
        out = self._tag_materials(out)

        # ---- Step 3b: stitch continuation rows (wrapped descriptions)
        out = self._stitch_continuation_rows(out)

        # ---- Step 4: null padding + column order (schema first, unmapped after)
        out = self._pad_and_order(out)

        # drop noise rows (page numbers, stray headers, blank/"nan" lines)
        out = self._drop_noise_rows(out)

        return out

    # ------------------------------------------------------------------ #
    @staticmethod
    def _clean_series(s: pd.Series) -> pd.Series:
        """Stringify a column, mapping NaN/'nan'/'none' to empty string."""
        out = s.astype(str)
        lowered = out.str.strip().str.lower()
        return out.mask(lowered.isin(["nan", "none", "nat", ""]), "")

    @staticmethod
    def _join_series(a: pd.Series, b: pd.Series) -> pd.Series:
        """Row-wise bundle two text columns with '; ', skipping blanks and
        avoiding duplicate fragments (so re-bundling is idempotent)."""
        av = a.astype(str).str.strip()
        bv = b.astype(str).str.strip()
        out = []
        for x, y in zip(av, bv):
            parts = [p for p in (x, y) if p]
            seen, uniq = set(), []
            for p in parts:
                if p.lower() not in seen:
                    seen.add(p.lower())
                    uniq.append(p)
            out.append("; ".join(uniq))
        return pd.Series(out, index=a.index)

    _NOISE_RE = re.compile(r"^[\s\-_.()\[\]/|]*\(?\d{0,4}\)?[\s\-_.()\[\]/|]*$")

    def _drop_noise_rows(self, out: pd.DataFrame) -> pd.DataFrame:
        """
        Remove rows that carry no real spare-part content: every schema cell
        empty, OR a row whose only content is a page-number / footer fragment
        like '(2)' with no identifying Part Name / Spare Part No.
        """
        schema_present = [c for c in self.schema if c in out.columns]
        if not schema_present or out.empty:
            return out.reset_index(drop=True)

        def keep(row) -> bool:
            vals = {c: str(row[c]).strip() for c in schema_present}
            if not any(vals.values()):
                return False
            pname = vals.get("Part Name", "")
            spno = vals.get("Spare Part No", "")
            text = vals.get("Text", "")
            # if there's no name and no part number, it's almost certainly noise
            identifying = pname or spno
            if not identifying:
                return bool(text and len(text) > 3)
            # name that is only a number / parenthetical page marker -> noise
            if not spno and self._NOISE_RE.match(pname):
                return False
            return True

        mask = out.apply(keep, axis=1)
        return out[mask].reset_index(drop=True)

    def _empty_schema_frame(self) -> pd.DataFrame:
        return pd.DataFrame(columns=self.schema)

    def _pop_context(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        ctx = {}
        for raw_key, schema_col in (
            ("__manufacturer__", "Manufacturer"),
            ("__spare_group__", "Spare Group"),
            ("__drawing_no__", "DrawingNo"),
            ("__ref_page__", "RefPage"),
        ):
            if raw_key in df.columns:
                ctx[schema_col] = df.pop(raw_key)
        return ctx

    def _apply_context(self, out: pd.DataFrame, ctx: dict[str, pd.Series]) -> None:
        for schema_col, series in ctx.items():
            if schema_col not in self.schema:
                continue
            if schema_col not in out.columns:
                out[schema_col] = series.values
            else:
                existing = out[schema_col].astype(str).str.strip()
                out[schema_col] = existing.where(existing != "", series.values)

    # ---- Step 1 ------------------------------------------------------- #
    def _title_case(self, df: pd.DataFrame) -> pd.DataFrame:
        if "Part Name" not in df.columns:
            return df

        def fix(text) -> str:
            try:
                s = str(text).strip()
                # strip footnote/recommended-spare markers (e.g. "O-ring*",
                # "Relief Valve, Complete*") that aren't part of the name
                s = s.strip("*").strip()
                if not s:
                    return s
                words = s.split()
                out_words = []
                for i, w in enumerate(words):
                    low = w.lower()
                    # keep tokens that look like codes (have digits) untouched
                    if any(ch.isdigit() for ch in w):
                        out_words.append(w)
                    elif low in self.blacklist and i != 0:
                        out_words.append(low)
                    else:
                        out_words.append(w[:1].upper() + w[1:].lower())
                return " ".join(out_words)
            except Exception:
                return str(text)

        df["Part Name"] = df["Part Name"].apply(fix)
        return df

    # ---- Step 2 ------------------------------------------------------- #
    def _extract_dimensions(self, df: pd.DataFrame) -> pd.DataFrame:
        if "Text" not in df.columns:
            df["Text"] = ""
        # search the Text column and the Part Name column for dimensions
        source_cols = [c for c in ("Text", "Part Name") if c in df.columns]

        def process_cell(val) -> tuple[str, Optional[str]]:
            try:
                s = str(val)
                m = self.dim_re.search(s)
                if not m:
                    return s, None
                number = m.group(1)
                unit_part = m.group(0)[len(m.group(1)):].strip()
                # heuristic: diameter if a 'dia'/'Ø'/'D' hint nearby, else length
                lowered = s.lower()
                prefix = "Dia. " if ("dia" in lowered or "ø" in lowered or "⌀" in s) else "L= "
                dim_text = f"{prefix}{number}{unit_part}"
                # strip the matched dimension + special chars from the source
                cleaned = self.dim_re.sub("", s)
                cleaned = re.sub(r"[^\w\s./-]", " ", cleaned)
                cleaned = re.sub(r"\s+", " ", cleaned).strip()
                return cleaned, dim_text
            except Exception:
                return str(val), None

        for col in source_cols:
            results = df[col].apply(process_cell)
            df[col] = results.apply(lambda t: t[0])
            dims = results.apply(lambda t: t[1])
            has_dim = dims.notna()
            if has_dim.any():
                existing = df["Text"].astype(str).str.strip()
                merged = existing.copy()
                for idx in df.index[has_dim]:
                    add = dims.loc[idx]
                    if not add:
                        continue
                    cur = str(merged.loc[idx]).strip()
                    merged.loc[idx] = f"{cur} {add}".strip() if cur and cur != add else add
                df["Text"] = merged
        return df

    # ---- Step 3 ------------------------------------------------------- #
    def _tag_materials(self, df: pd.DataFrame) -> pd.DataFrame:
        if "Part Name" not in df.columns:
            return df
        if "Internal Remark" not in df.columns:
            df["Internal Remark"] = ""

        def find_material(text: str) -> Optional[str]:
            low = text.lower()
            for mat in self._materials_sorted:
                if mat.lower() in low:
                    return mat
            return None

        new_part = []
        new_remark = list(df["Internal Remark"].astype(str))
        for pos, (idx, row) in enumerate(df.iterrows()):
            try:
                pname = str(row["Part Name"])
                # also scan Text for materials
                scan = pname + " " + str(row.get("Text", ""))
                mat = find_material(scan)
                if not mat:
                    new_part.append(pname)
                    continue
                if len(mat) <= self.max_material_len:
                    tag = f"Matl. {mat}"
                    new_part.append(pname if tag in pname else f"{pname} {tag}".strip())
                else:
                    new_part.append(pname)
                    cur = new_remark[pos]
                    new_remark[pos] = f"{cur} {mat}".strip() if cur.strip() else mat
            except Exception:
                new_part.append(str(row.get("Part Name", "")))
        df["Part Name"] = new_part
        df["Internal Remark"] = new_remark
        return df

    # ---- Step 3b --------------------------------------------------------- #
    def _stitch_continuation_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Append continuation rows (wrapped descriptions) to the row above.

        Engineering manuals frequently let a part name overflow into the next
        row. These continuation rows have an empty DrawingPosNo (no item number)
        but carry text in the Part Name column. Appending them fixes broken
        multi-line descriptions without creating spurious extra rows.
        """
        if df.empty:
            return df
        if "DrawingPosNo" not in df.columns or "Part Name" not in df.columns:
            return df

        drop_idx = []
        prev_valid = None
        for idx in df.index:
            key = str(df.at[idx, "DrawingPosNo"]).strip()
            name = str(df.at[idx, "Part Name"]).strip()
            if not key and name and prev_valid is not None:
                prev_key = str(df.at[prev_valid, "DrawingPosNo"]).strip()
                # Only stitch when the preceding row has a valid primary key.
                # If the preceding row is also keyless (broken table / garbled
                # column names), stitching cascades and loses rows.
                if prev_key:
                    prev = str(df.at[prev_valid, "Part Name"]).strip()
                    df.at[prev_valid, "Part Name"] = (prev + " " + name).strip()
                    drop_idx.append(idx)
                else:
                    prev_valid = idx
            else:
                prev_valid = idx

        if drop_idx:
            df = df.drop(index=drop_idx).reset_index(drop=True)
        return df

    # ---- Step 4 ------------------------------------------------------- #
    def _pad_and_order(self, df: pd.DataFrame) -> pd.DataFrame:
        # add all missing schema columns in one shot (concat, not repeated
        # inserts) so the frame doesn't fragment
        missing = [c for c in self.schema if c not in df.columns]
        if missing:
            pad = pd.DataFrame("", index=df.index, columns=missing)
            df = pd.concat([df, pad], axis=1)
        unmapped_cols = [c for c in df.columns if c not in self.schema]
        ordered = self.schema + unmapped_cols
        df = df[ordered].copy()        # .copy() defragments
        df = df.fillna("")
        # final safety net: strip Excel-illegal control characters from every
        # cell so openpyxl never crashes mid-export on a single bad value
        for col in df.columns:
            df[col] = df[col].astype(str).str.replace(_ILLEGAL_XLSX_RE, "", regex=True)
        return df
