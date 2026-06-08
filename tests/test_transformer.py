"""
Unit tests for engine/transformer.py — every business rule from the
"CHECKLIST FOR ENTERING SPARE PARTS" sheet.
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine.transformer import Transformer


@pytest.fixture
def tr(cfg):
    return Transformer(cfg)


# --------------------------------------------------------------------------- #
# Rule 3 & 4: Proper case; conjunctions (of/with/for/and/in/the) lowercase
# --------------------------------------------------------------------------- #
class TestTitleCase:
    def test_basic_title_case(self, tr):
        df = pd.DataFrame({"Part Name": ["hydraulic jack"]})
        out = tr.transform(df, {"Part Name": "Part Name"})
        assert out.loc[0, "Part Name"] == "Hydraulic Jack"

    @pytest.mark.parametrize("raw,expected", [
        ("support for hydraulic jack", "Support for Hydraulic Jack"),
        ("sealing ring with back-up", "Sealing Ring with Back-up"),
        ("nut and bolt", "Nut and Bolt"),
        ("cover of cylinder", "Cover of Cylinder"),
        ("valve in line", "Valve in Line"),
        ("guide for the pump", "Guide for the Pump"),
    ])
    def test_conjunctions_forced_lowercase(self, tr, raw, expected):
        out = tr.transform(pd.DataFrame({"Part Name": [raw]}), {"Part Name": "Part Name"})
        assert out.loc[0, "Part Name"] == expected

    def test_leading_conjunction_capitalized(self, tr):
        # a blacklisted word at position 0 should still be capitalized
        out = tr.transform(pd.DataFrame({"Part Name": ["for engine"]}), {"Part Name": "Part Name"})
        assert out.loc[0, "Part Name"].split()[0] == "For"

    def test_codes_with_digits_preserved(self, tr):
        # tokens containing digits (part codes) must not be case-mangled
        out = tr.transform(pd.DataFrame({"Part Name": ["o-ring SUS304 M10x20"]}),
                           {"Part Name": "Part Name"})
        val = out.loc[0, "Part Name"]
        assert "M10x20" in val
        assert "SUS304" in val


# --------------------------------------------------------------------------- #
# Rule 7: Length & Dia formatted as 'L= ' / 'Dia.'; Rule 6: no special chars
# --------------------------------------------------------------------------- #
class TestDimensions:
    def test_length_extracted_to_text(self, tr):
        df = pd.DataFrame({"Part Name": ["shaft"], "Text": ["length 250 mm"]})
        out = tr.transform(df, {"Part Name": "Part Name", "Text": "Text"})
        assert "L=" in out.loc[0, "Text"]
        assert "250" in out.loc[0, "Text"]

    def test_diameter_detected(self, tr):
        df = pd.DataFrame({"Part Name": ["pin"], "Text": ["dia 30mm"]})
        out = tr.transform(df, {"Part Name": "Part Name", "Text": "Text"})
        assert "Dia." in out.loc[0, "Text"]

    def test_inch_unit(self, tr):
        df = pd.DataFrame({"Part Name": ["pipe"], "Text": ['3 inch']})
        out = tr.transform(df, {"Part Name": "Part Name", "Text": "Text"})
        assert "3" in out.loc[0, "Text"]

    def test_decimal_dimension(self, tr):
        df = pd.DataFrame({"Part Name": ["rod"], "Text": ["12.5 mm"]})
        out = tr.transform(df, {"Part Name": "Part Name", "Text": "Text"})
        assert "12.5" in out.loc[0, "Text"]


# --------------------------------------------------------------------------- #
# Rule 11: Material -> "Matl. " prefix on Part Name if short, else Internal Remark
# --------------------------------------------------------------------------- #
class TestMaterialTagging:
    def test_short_material_prefixed_on_part_name(self, tr):
        df = pd.DataFrame({"Part Name": ["gasket EPDM"]})
        out = tr.transform(df, {"Part Name": "Part Name"})
        assert "Matl. EPDM" in out.loc[0, "Part Name"]

    def test_recognized_material_sus304(self, tr):
        df = pd.DataFrame({"Part Name": ["bolt SUS304"]})
        out = tr.transform(df, {"Part Name": "Part Name"})
        assert "Matl. SUS304" in out.loc[0, "Part Name"]

    def test_long_material_goes_to_internal_remark(self, cfg):
        cfg["rules"]["max_material_length_for_prefix"] = 5
        cfg["rules"]["recognized_materials"] = ["Stainless Steel"]
        tr = Transformer(cfg)
        df = pd.DataFrame({"Part Name": ["plate Stainless Steel"]})
        out = tr.transform(df, {"Part Name": "Part Name"})
        assert "Stainless Steel" in str(out.loc[0, "Internal Remark"])
        assert "Matl." not in str(out.loc[0, "Part Name"])

    def test_no_false_material_tag(self, tr):
        df = pd.DataFrame({"Part Name": ["ordinary widget"]})
        out = tr.transform(df, {"Part Name": "Part Name"})
        assert "Matl." not in out.loc[0, "Part Name"]


# --------------------------------------------------------------------------- #
# Null padding & schema conformance (target_schema must match output exactly)
# --------------------------------------------------------------------------- #
class TestSchemaConformance:
    def test_all_schema_columns_present(self, tr, cfg):
        df = pd.DataFrame({"Part Name": ["x"]})
        out = tr.transform(df, {"Part Name": "Part Name"})
        for col in cfg["target_schema"]:
            assert col in out.columns

    def test_schema_columns_come_first_in_order(self, tr, cfg):
        df = pd.DataFrame({"Part Name": ["x"], "Item No.": ["1"]})
        out = tr.transform(df, {"Part Name": "Part Name", "Item No.": "DrawingPosNo"})
        assert list(out.columns)[: len(cfg["target_schema"])] == cfg["target_schema"]

    def test_missing_columns_filled_empty_not_nan(self, tr):
        df = pd.DataFrame({"Part Name": ["x"]})
        out = tr.transform(df, {"Part Name": "Part Name"})
        assert out["Measuring Unit"].iloc[0] == ""
        assert not out.isna().any().any()

    def test_unmapped_columns_preserved_not_dropped(self, tr):
        df = pd.DataFrame({"Part Name": ["x"], "Mystery Col": ["keep me"]})
        out = tr.transform(df, {"Part Name": "Part Name"})  # Mystery unmapped
        unmapped = [c for c in out.columns if c.startswith("[UNMAPPED]")]
        assert any("Mystery Col" in c for c in unmapped)
        assert (out.filter(like="[UNMAPPED]").iloc[0] == "keep me").any()

    def test_empty_input_returns_schema_frame(self, tr, cfg):
        out = tr.transform(pd.DataFrame(), {})
        assert list(out.columns) == cfg["target_schema"]
        assert len(out) == 0


# --------------------------------------------------------------------------- #
# Text-column bundling: several aux source columns -> one readable Text cell
# --------------------------------------------------------------------------- #
class TestTextBundling:
    def test_multiple_aux_columns_bundle_into_text(self, tr):
        df = pd.DataFrame({
            "Type": ["Ball", "Gate"],
            "Maker Spec": ["ACME", ""],
            "Part Name": ["Valve", "Seal"],
        })
        out = tr.transform(df, {"Type": "Text", "Maker Spec": "Text",
                                "Part Name": "Part Name"})
        texts = out["Text"].tolist()
        assert texts[0] == "Ball; ACME"   # joined with "; "
        assert texts[1] == "Gate"         # blank skipped — no trailing "; "

    def test_bundle_dedupes_repeats(self, tr):
        df = pd.DataFrame({"A": ["Bronze"], "B": ["Bronze"], "Part Name": ["Plate"]})
        out = tr.transform(df, {"A": "Text", "B": "Text", "Part Name": "Part Name"})
        # same value from two columns must not double up as "Bronze; Bronze"
        assert "Bronze" in out["Text"].iloc[0]
        assert "Bronze; Bronze" not in out["Text"].iloc[0]


# --------------------------------------------------------------------------- #
# Context stamping (Manufacturer / Spare Group / RefPage from page bands)
# --------------------------------------------------------------------------- #
class TestContextStamping:
    def test_context_columns_applied(self, tr):
        df = pd.DataFrame({
            "Part Name": ["x", "y"],
            "__manufacturer__": ["MAN B&W", "MAN B&W"],
            "__spare_group__": ["Cylinder Cover", "Cylinder Cover"],
            "__ref_page__": ["96", "96"],
        })
        out = tr.transform(df, {"Part Name": "Part Name"})
        assert (out["Manufacturer"] == "MAN B&W").all()
        assert (out["Spare Group"] == "Cylinder Cover").all()
        assert (out["RefPage"] == "96").all()

    def test_fully_empty_rows_dropped(self, tr):
        df = pd.DataFrame({"Part Name": ["real", "", "  "]})
        out = tr.transform(df, {"Part Name": "Part Name"})
        assert len(out) == 1
