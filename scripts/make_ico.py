"""
make_ico.py
-----------
Generate `app.ico` (the file icon baked into the built Parsel.exe) from the
same runtime serpent mark in ui/icon.py — so the on-disk icon and the in-app
icon never drift apart.

Run once before building (from the project root):
    python scripts/make_ico.py
PyInstaller then picks it up via build.spec (icon="assets/app.ico").
"""
from __future__ import annotations

import io
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# This script lives in scripts/; put the project root on sys.path so `ui` imports.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QBuffer, QByteArray

from ui.icon import _draw

SIZES = [16, 24, 32, 48, 64, 128, 256]
OUT = os.path.join(_ROOT, "assets", "app.ico")


def _qpixmap_to_pil(pm):
    from PIL import Image
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    pm.save(buf, "PNG")
    buf.close()
    return Image.open(io.BytesIO(bytes(ba))).convert("RGBA")


def main() -> int:
    # A QApplication must exist before any QPixmap is created.
    if QApplication.instance() is None:
        QApplication([])

    import importlib.util
    if importlib.util.find_spec("PIL") is None:
        # Fallback: let Qt write the ICO directly (single best size).
        _draw(256).save(OUT, "ICO")
        print(f"Wrote {OUT} via Qt (install Pillow for multi-size).")
        return 0

    imgs = [_qpixmap_to_pil(_draw(s)) for s in SIZES]
    largest = imgs[-1]
    largest.save(
        OUT,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=imgs[:-1],
    )
    print(f"Wrote {OUT} with sizes {SIZES}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
