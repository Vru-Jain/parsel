"""
Unit tests for engine/pdf_extractor.py — header scoring (the logic that picks
the clean extraction over garbled ones), row->DataFrame conversion, and the
text-block reconstruction heuristic.
"""
from __future__ import annotations

import pandas as pd

from engine.pdf_extractor import (
    _rows_to_df, score_dataframe, build_known_headers, _norm_header,
    _boxes_to_table, parse_page_spec,
)


def _box(cx, cy, text, score=0.95, w=40, h=14):
    """Build a RapidOCR-style ([4 points], text, score) detection at (cx,cy)."""
    x0, y0 = cx - w / 2, cy - h / 2
    x1, y1 = cx + w / 2, cy + h / 2
    return ([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], text, score)


class TestOcrTableReconstruction:
    def test_two_column_table_rebuilt_with_headers(self):
        res = [
            _box(100, 50, "No."), _box(300, 50, "NAME"),
            _box(100, 100, "101"), _box(300, 100, "Pump"),
            _box(100, 150, "102"), _box(300, 150, "Valve"),
        ]
        df = _boxes_to_table(res, page_w=500)
        assert "No." in df.columns and "NAME" in df.columns
        names = df["NAME"].astype(str).tolist()
        assert "Pump" in names and "Valve" in names
        assert "101" in df["No."].astype(str).tolist()

    def test_low_confidence_boxes_dropped(self):
        res = [
            _box(100, 50, "No."), _box(300, 50, "NAME"),
            _box(100, 100, "101"), _box(300, 100, "Pump"),
            _box(300, 150, "garbage", score=0.10),
        ]
        df = _boxes_to_table(res, page_w=500)
        assert "garbage" not in df.to_string()

    def test_too_few_boxes_returns_empty(self):
        assert _boxes_to_table([_box(10, 10, "x")], page_w=500).empty

    def test_title_block_does_not_bridge_columns(self):
        """Regression: title-block cells sitting between two adjacent columns must
        not chain-merge them. With page_w=600 → colgap=40. The title-block cells
        at cx=130 and cx=160 bridge the 100→190 gap via 30-pt hops (each ≤ 40),
        so whole-page clustering merges both table columns into one centroid≈145.
        Table-region-only clustering (the fix) sees only [100,190] and keeps them
        separate, producing two correct columns."""
        # page_w=600 → colgap = max(40.0, 600*0.035=21.0) = 40.0
        res = [
            # title-block cells at cx=130 and cx=160: each 30 pts apart, bridging
            # the gap between the item-no column (cx=100) and name column (cx=190).
            _box(130, 100, "Assembly Name", w=60),
            _box(160, 140, "1234-5678-90", w=50),
            # header row
            _box(100, 300, "No.", w=40), _box(190, 300, "NAME", w=60),
            # data rows
            _box(100, 350, "1"), _box(190, 350, "Pump", w=60),
            _box(100, 400, "2"), _box(190, 400, "Valve", w=60),
        ]
        df = _boxes_to_table(res, page_w=600)
        assert "No." in df.columns, f"columns merged into one: {list(df.columns)}"
        assert "NAME" in df.columns
        assert "Pump" in df["NAME"].astype(str).tolist()

    def test_unheadered_text_column_named_description(self):
        """An un-captioned free-text column is renamed 'Description' so it maps
        to Part Name downstream (Rolls-Royce parts lists omit the name header)."""
        res = [
            _box(100, 50, "No.", w=40), _box(700, 50, "Qty", w=40),
            _box(100, 100, "1"), _box(400, 100, "FLANGE", w=80), _box(700, 100, "2"),
            _box(100, 150, "2"), _box(400, 150, "BEARING", w=80), _box(700, 150, "1"),
        ]
        df = _boxes_to_table(res, page_w=900)
        assert "Description" in df.columns, f"got {list(df.columns)}"
        assert "FLANGE" in df["Description"].astype(str).tolist()


class TestRowsToDf:
    def test_first_row_becomes_header(self):
        df = _rows_to_df([["Item No.", "Designation"], ["015", "Jack"]])
        assert list(df.columns) == ["Item No.", "Designation"]
        assert df.iloc[0].tolist() == ["015", "Jack"]

    def test_blank_header_cells_filled(self):
        df = _rows_to_df([["A", "", "C"], ["1", "2", "3"]])
        assert len(df.columns) == 3
        assert "" not in [str(c) for c in df.columns]  # blanks renamed

    def test_duplicate_headers_disambiguated(self):
        df = _rows_to_df([["No.", "No.", "Name"], ["1", "2", "x"]])
        assert len(set(df.columns)) == 3  # all unique

    def test_ragged_rows_padded_and_truncated(self):
        df = _rows_to_df([["A", "B"], ["1"], ["1", "2", "3"]])
        assert df.shape[1] == 2

    def test_too_few_rows_returns_empty(self):
        assert _rows_to_df([["only header"]]).empty
        assert _rows_to_df([]).empty


class TestHeaderScoring:
    def test_known_headers_beat_garbage(self, cfg):
        known = build_known_headers(cfg)
        good = pd.DataFrame({"Item No.": ["1"], "Item Designation": ["x"]})
        garbage = pd.DataFrame({"1070-0200-0010": ["a"], "col_1": ["b"],
                                "MAN B&W": ["c"]})
        assert score_dataframe(good, known) > score_dataframe(garbage, known)

    def test_drawing_number_header_penalized(self, cfg):
        known = build_known_headers(cfg)
        garbage = pd.DataFrame({"0570-0100-0002": ["a"], "col_1": ["b"]})
        assert score_dataframe(garbage, known) < 0

    def test_empty_frame_scores_zero(self, cfg):
        known = build_known_headers(cfg)
        assert score_dataframe(pd.DataFrame(), known) == 0.0

    def test_real_columns_score_positive(self, cfg):
        known = build_known_headers(cfg)
        df = pd.DataFrame({"Description": ["x"], "Part No.": ["y"], "Plate": ["z"]})
        assert score_dataframe(df, known) > 10

    def test_known_headers_include_aliases(self, cfg):
        known = build_known_headers(cfg)
        assert _norm_header("Description") in known
        assert _norm_header("Plate") in known
        assert _norm_header("Item No.") in known


class TestPageSpec:
    def test_ranges_and_singles(self):
        assert parse_page_spec("1-5, 12, 20-22") == {1, 2, 3, 4, 5, 12, 20, 21, 22}

    def test_blank_is_empty(self):
        assert parse_page_spec("") == set()
        assert parse_page_spec("   ") == set()

    def test_open_ended_range_uses_total(self):
        assert parse_page_spec("100-", total=103) == {100, 101, 102, 103}
        assert parse_page_spec("-3") == {1, 2, 3}

    def test_clamped_to_total(self):
        assert parse_page_spec("1, 50, 999", total=100) == {1, 50}

    def test_reversed_range_normalized(self):
        assert parse_page_spec("9-7") == {7, 8, 9}

    def test_whitespace_and_commas_mixed(self):
        assert parse_page_spec("3 5  7-8") == {3, 5, 7, 8}

    def test_malformed_raises(self):
        import pytest
        with pytest.raises(ValueError):
            parse_page_spec("abc")


class TestTextblockParser:
    """The column-anchored item/designation parser must recover rows that a
    naive 'line starts with a number' rule loses (regression for the page-96/110
    losses: a left-column sub-heading clustering onto a parts row)."""

    def _page(self):
        import fitz
        doc = fitz.open()
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((60, 100), "Item No.")
        pg.insert_text((180, 100), "Item Designation")
        pg.insert_text((60, 130), "015"); pg.insert_text((180, 130), "Hydraulic jack")
        pg.insert_text((60, 152), "064"); pg.insert_text((180, 152), "Support bracket")
        # contaminated row: a left-margin sub-heading on the same line as item 111
        pg.insert_text((18, 174), "Bolts")
        pg.insert_text((60, 174), "111"); pg.insert_text((180, 174), "Sealing ring")
        return doc, pg

    def test_recovers_row_with_left_heading(self):
        from engine.pdf_extractor import _extract_textblocks_page
        doc, pg = self._page()
        df, method = _extract_textblocks_page(pg)
        nos = [str(n) for n in df["No."]]
        assert "111" in nos, f"item 111 lost; got {nos}"
        desc = dict(zip(df["No."].astype(str), df["Description"].astype(str)))
        assert "Sealing ring" in desc["111"]
        assert "Bolts" not in desc["111"]      # left heading excluded
        doc.close()

    def test_count_item_anchors_independent(self):
        from engine.pdf_extractor import count_item_anchors
        doc, pg = self._page()
        assert count_item_anchors(pg) == 3     # 015, 064, 111
        doc.close()


class TestBuildKnownHeaders:
    def test_includes_schema_and_aliases(self, cfg):
        known = build_known_headers(cfg)
        # every schema column present
        for col in cfg["target_schema"]:
            assert _norm_header(col) in known
        # no empty entries
        assert "" not in known
