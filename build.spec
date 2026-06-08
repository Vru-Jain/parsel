# -*- mode: python ; coding: utf-8 -*-
r"""
PyInstaller spec for Parsel — the Offline Spare-Parts Manual Parser.

Build with:
    pyinstaller build.spec

Design choices (reliability over a single fat file):
  * --onedir style (COLLECT) — NOT --onefile. Avoids the %TEMP%\_MEIxxxx
    re-extraction on every launch that Windows Defender flags. Faster startup,
    fewer DLL issues.
  * --windowed (no console window).
  * No torch / sentence-transformers / embedding model — column mapping is
    exact + fuzzy only. That keeps the bundle small (~150 MB vs ~1 GB) and
    sidesteps the torch c10.dll / numpy bundling problems entirely.
  * config.json is COPIED next to the .exe by the post-build step below, NOT
    frozen inside, so end-users can edit it after compilation.
"""
import os
import shutil
from PyInstaller.utils.hooks import (
    collect_submodules, collect_data_files, collect_dynamic_libs,
)

block_cipher = None
PROJECT_DIR = os.path.abspath(os.getcwd())

datas = []
# RapidOCR ships its ONNX models + config YAMLs as package data — bundle them so
# OCR works out of the box in the frozen exe (no Tesseract, no system binary).
datas += collect_data_files("rapidocr_onnxruntime")
datas += collect_data_files("onnxruntime")

# numpy is still needed (pandas / rapidocr / opencv). numpy 2.x reorganized its
# internals into numpy._core; PyInstaller's stock hook can miss some, causing
# "No module named 'numpy._core._exceptions'". Collect it fully to be safe.
binaries = []
hiddenimports = []
hiddenimports += collect_submodules("numpy")
binaries += collect_dynamic_libs("numpy")
datas += collect_data_files("numpy")

hiddenimports += collect_submodules("rapidocr_onnxruntime")
hiddenimports += ["fitz", "openpyxl", "pandas", "rapidfuzz", "PySide6",
                  "onnxruntime", "cv2"]

a = Analysis(
    ["main.py"],
    pathex=[PROJECT_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "PyQt5", "PyQt6",  # trim size
        # the ML stack is no longer used — exclude it so it can't bloat the build
        "torch", "torchvision", "torchaudio", "sentence_transformers",
        "transformers", "sklearn", "scipy",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Parsel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # --windowed
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/app.ico",  # baked file icon (generate via: python scripts/make_ico.py)
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Parsel",   # -> dist/Parsel/Parsel.exe
)

# --- post-build: drop an editable config.json beside the exe ---------------
def _copy_external_config():
    src = os.path.join(PROJECT_DIR, "config.json")
    dist = os.path.join(PROJECT_DIR, "dist", "Parsel")
    if os.path.exists(src) and os.path.isdir(dist):
        shutil.copy2(src, os.path.join(dist, "config.json"))

_copy_external_config()
