"""
generate_demo_output.py
-----------------------
Produce a real Excel from a demo manual so you have a tangible artifact to show.
Runs the SAME pipeline the GUI uses (no shortcuts).

Usage (from the project root):
    python scripts/generate_demo_output.py "C:\\path\\to\\Book 1.pdf"
    python scripts/generate_demo_output.py "C:\\path\\to\\Book 1.pdf" --pages 30-120

Output: <name>_PROCESSED.xlsx in the project root (plus a QC report if any rows
fail key-field checks).
"""
from __future__ import annotations

import os
import sys
import json
import argparse

# This script lives in scripts/; the project root is one level up.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.semantic_mapper import SemanticMapper
from engine.pipeline import process_file


def _parse_pages(spec: str):
    """'30-120' -> (30, 120); '50' -> (50, 50); blank/invalid -> None."""
    if not spec:
        return None
    try:
        if "-" in spec:
            a, b = spec.split("-", 1)
            return (int(a), int(b))
        n = int(spec)
        return (n, n)
    except ValueError:
        print(f"Ignoring invalid --pages '{spec}' (use e.g. 30-120).")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="Path to the manual PDF")
    ap.add_argument("--out", default=ROOT, help="Output directory")
    ap.add_argument("--pages", default="", help="Page range, e.g. 30-120 (1-based)")
    args = ap.parse_args()

    if not os.path.exists(args.pdf):
        print(f"File not found: {args.pdf}")
        return 1

    page_range = _parse_pages(args.pages)
    config = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    paths = {
        "app_dir": ROOT,
        "config_path": os.path.join(ROOT, "config.json"),
        "wip_tracker": os.path.join(ROOT, "WIP_Tracker.txt"),
        "models_dir": os.path.join(ROOT, "models"),
    }
    mapper = SemanticMapper(config, paths["models_dir"])

    def cb(cur, total, msg):
        if total:
            pct = int(100 * cur / total)
            print(f"\r[{pct:3d}%] {msg[:70]:70}", end="", flush=True)

    print(f"Processing: {args.pdf}")
    res = process_file(args.pdf, config, paths, mapper, progress_cb=cb,
                       output_dir=args.out, page_range=page_range)
    print()
    print("-" * 60)
    print(f"Rows exported : {res.rows}")
    print(f"Output        : {res.output_path or '(none)'}")
    print(f"Model status  : {res.model_status}")
    if res.unmapped:
        print(f"Unmapped cols : {res.unmapped}")
    for w in res.warnings:
        print(f"WARNING       : {w}")
    for n in res.qc_notes:
        print(f"QC            : {n}")
    if res.page_errors:
        print(f"Page issues   : {len(res.page_errors)} (e.g. {res.page_errors[:3]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
