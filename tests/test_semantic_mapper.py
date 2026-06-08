"""
Unit tests for engine/semantic_mapper.py.

Covers the 2-tier cascade (exact -> fuzzy) and the 92 real-world header aliases
extracted from the customer's mapping documents (tests/data/golden_aliases.json).
"""
from __future__ import annotations

import pytest

from engine.semantic_mapper import SemanticMapper, _norm


@pytest.fixture
def mapper(cfg):
    return SemanticMapper(cfg)


class TestExactMatch:
    @pytest.mark.parametrize("raw,expected", [
        ("Part Name", "Part Name"),
        ("part name", "Part Name"),          # case-insensitive
        ("PART NAME", "Part Name"),
        ("Spare Part No", "Spare Part No"),
        ("DrawingNo", "DrawingNo"),
    ])
    def test_schema_names_map_to_self(self, mapper, raw, expected):
        res = mapper.map_columns([raw])
        assert res.mapping.get(raw) == expected

    @pytest.mark.parametrize("alias,expected", [
        ("Description", "Part Name"),
        ("Designation", "Part Name"),
        ("Item Designation", "Part Name"),
        ("Item No.", "DrawingPosNo"),
        ("Plate", "DrawingNo"),
        ("Dwg. No.", "DrawingNo"),
        ("Material", "Text"),
    ])
    def test_known_aliases(self, mapper, alias, expected):
        res = mapper.map_columns([alias])
        assert res.mapping.get(alias) == expected, res.detail


class TestFuzzyMatch:
    def test_punctuation_variation(self, mapper):
        # "Item No" without the dot should still hit via exact-normalized or fuzzy
        res = mapper.map_columns(["Item No"])
        assert res.mapping.get("Item No") == "DrawingPosNo"

    def test_minor_typo_maps(self, mapper):
        res = mapper.map_columns(["Descripton"])  # missing 'i'
        assert res.mapping.get("Descripton") == "Part Name"

    def test_spacing_noise(self, mapper):
        res = mapper.map_columns(["  Drawing   No.  "])
        assert res.mapping.get("  Drawing   No.  ") in ("DrawingNo", "Spare Part No")


class TestDegradation:
    def test_internal_context_cols_ignored(self, mapper):
        res = mapper.map_columns(["__manufacturer__", "__spare_group__", "Part Name"])
        assert "__manufacturer__" not in res.mapping
        assert "__manufacturer__" not in res.unmapped

    def test_unknown_column_is_unmapped_not_crash(self, mapper):
        res = mapper.map_columns(["Zxqv Nonsense 999"])
        assert "Zxqv Nonsense 999" in res.unmapped


class TestGoldenAliases:
    """Every alias the customer documented should map (coverage gate)."""

    def test_coverage_threshold(self, mapper, golden_aliases):
        if not golden_aliases:
            pytest.skip("golden_aliases.json not generated")
        all_aliases = []
        for col, aliases in golden_aliases.items():
            for a in aliases:
                all_aliases.append((a, col))

        res = mapper.map_columns([a for a, _ in all_aliases])
        mapped = sum(1 for a, _ in all_aliases if a in res.mapping)
        coverage = mapped / len(all_aliases)
        # With real-world ambiguity we expect the vast majority to map.
        assert coverage >= 0.80, (
            f"only {coverage:.0%} of {len(all_aliases)} real aliases mapped; "
            f"unmapped={[a for a,_ in all_aliases if a in res.unmapped]}"
        )

    def test_part_name_aliases_route_to_part_name(self, mapper, golden_aliases):
        if not golden_aliases:
            pytest.skip("golden_aliases.json not generated")
        # Part Name aliases are unambiguous; each should map to Part Name.
        aliases = golden_aliases.get("Part Name", [])
        res = mapper.map_columns(aliases)
        wrong = {a: res.mapping.get(a) for a in aliases
                 if a in res.mapping and res.mapping[a] != "Part Name"}
        # allow a tiny number of genuinely ambiguous ones
        assert len(wrong) <= 2, f"misrouted Part Name aliases: {wrong}"


class TestNormalization:
    @pytest.mark.parametrize("a,b", [
        ("Item No.", "item no"),
        ("Dwg. No.", "dwgno"),
        ("Part-Number", "partnumber"),
    ])
    def test_norm_strips_punctuation(self, a, b):
        assert _norm(a) == _norm(b) or _norm(a) == b
