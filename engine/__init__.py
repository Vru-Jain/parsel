"""Data-processing engine for Parsel (Offline Spare-Parts Manual Parser).

Strictly separated from the UI layer. All public entry points are pure
functions / classes that accept config dicts and return DataFrames or
plain data — no Qt imports here.
"""
