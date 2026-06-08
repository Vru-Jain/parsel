"""
engine/semantic_mapper.py
-------------------------
Map raw extracted column headers onto the fixed target schema.

Deterministic 2-tier cascade (NO LLM, NO ML model, fully offline, instant):
  1. Exact match (case-insensitive, punctuation-normalized) against schema + aliases
  2. RapidFuzz token/ratio match  (edit-distance, threshold from config)

A third semantic tier (sentence-transformers / torch) was removed on purpose:
on the real manuals it produced byte-for-byte identical mappings to tiers 1+2
while costing ~1 GB, ~18 s of startup, and a torch/Qt DLL conflict. The 92
configured header aliases cover the real-world variants; anything genuinely
novel is returned as `unmapped` for the user to link in Settings.

Unmapped columns are NEVER dropped: they are returned so the UI can let the
user link them, and the transformer keeps their data under the raw name.

The class name and constructor signature are kept for API stability with the
rest of the app; `models_dir` is accepted but unused.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process


@dataclass
class MappingResult:
    # raw column name -> target schema column
    mapping: dict[str, str] = field(default_factory=dict)
    unmapped: list[str] = field(default_factory=list)
    # raw col -> (method, score) for transparency / debugging
    detail: dict[str, tuple[str, float]] = field(default_factory=dict)
    # kept for compatibility with callers/UI; always "ready" now (no model)
    model_status: str = "ready"


def _norm(s: str) -> str:
    """lowercase, strip punctuation/whitespace for robust comparison."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


# Columns whose value is injected by the extractor, not mapped from headers.
_INTERNAL_COLS = {"__manufacturer__", "__spare_group__", "__ref_page__", "__drawing_no__"}


class SemanticMapper:
    """Exact + fuzzy column-header mapper. Construct once, reuse across files."""

    def __init__(self, config: dict, models_dir: str | None = None):
        self.config = config
        self.models_dir = models_dir  # unused; kept for signature compatibility
        rules = config.get("rules", {}) or {}
        self.fuzzy_threshold = float(rules.get("fuzzy_threshold", 88))

        # Precompute alias lookup: normalized alias text -> target column
        self._alias_index = self._build_alias_index()
        # Flat list of (target_col, phrase) used for fuzzy matching
        self._phrases = self._build_phrase_list()

    # ----- index construction -------------------------------------------
    def _build_alias_index(self) -> dict[str, str]:
        idx: dict[str, str] = {}
        for col in self.config.get("target_schema", []):
            idx[_norm(col)] = col
        for col, aliases in (self.config.get("header_aliases", {}) or {}).items():
            for alias in aliases:
                idx[_norm(alias)] = col
        return idx

    def _build_phrase_list(self) -> list[tuple[str, str]]:
        phrases: list[tuple[str, str]] = []
        for col in self.config.get("target_schema", []):
            phrases.append((col, col))
        for col, aliases in (self.config.get("header_aliases", {}) or {}).items():
            for alias in aliases:
                phrases.append((col, alias))
        return phrases

    # ----- no-op model hook (kept so the UI pre-warm call still works) ----
    def ensure_model(self) -> str:
        """No model to load anymore — mapping is instant exact+fuzzy."""
        return "ready"

    # ----- the cascade ---------------------------------------------------
    def map_columns(self, raw_columns: list[str]) -> MappingResult:
        result = MappingResult()
        used_targets: set[str] = set()
        phrase_texts = [p[1] for p in self._phrases]

        for raw in raw_columns:
            if raw in _INTERNAL_COLS:
                continue

            # Tier 1: exact (normalized) match
            key = _norm(raw)
            if key in self._alias_index:
                target = self._alias_index[key]
                result.mapping[raw] = target
                result.detail[raw] = ("exact", 100.0)
                used_targets.add(target)
                continue

            # Tier 2: fuzzy match against all phrases
            best = process.extractOne(
                raw, phrase_texts, scorer=fuzz.token_sort_ratio,
            )
            if best and best[1] >= self.fuzzy_threshold:
                target = self._phrases[best[2]][0]
                result.mapping[raw] = target
                result.detail[raw] = ("fuzzy", float(best[1]))
                used_targets.add(target)
                continue

            # Nothing matched: preserve as unmapped (never drop)
            result.unmapped.append(raw)

        return result
