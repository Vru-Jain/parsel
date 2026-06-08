"""
Parsel — Offline Spare-Parts Manual Parser
Entry point. Bootstraps configuration, resolves paths (dev + frozen .exe),
and launches the PySide6 dashboard.

NO LLMs, no ML model. 100% offline. Deterministic layout parsing (PyMuPDF) +
exact/fuzzy column mapping. Scanned pages use built-in RapidOCR (ONNX).
"""
from __future__ import annotations

import os
import sys
import json
import shutil
from pathlib import Path


def _ensure_std_streams() -> None:
    """pythonw.exe and a PyInstaller --windowed build (runw.exe) run with NO
    console, so sys.stdout / sys.stderr are None. Any library that writes to
    stderr would then crash on None. Redirect the missing streams to a log file
    beside the app (so we keep a debuggable trace), falling back to the null
    device if even that can't be opened."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    sink = None
    try:
        base = os.path.dirname(
            sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)
        )
        sink = open(os.path.join(base, "parsel.log"), "a", encoding="utf-8",
                    buffering=1, errors="replace")
    except Exception:
        try:
            sink = open(os.devnull, "w")
        except Exception:
            return
    if sys.stdout is None:
        sys.stdout = sink
    if sys.stderr is None:
        sys.stderr = sink


_ensure_std_streams()

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QColor

# ---------------------------------------------------------------------------
# Path resolution: works both as a normal script and as a PyInstaller --onedir
# build. config.json and WIP_Tracker.txt always live NEXT TO the executable so
# the end-user can edit config after compilation (per packaging directive).
# ---------------------------------------------------------------------------

def app_dir() -> Path:
    """Directory where the .exe (or main.py) lives — user-editable files go here."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundle_dir() -> Path:
    """Directory of bundled read-only resources (defaults). _MEIPASS when frozen."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return Path(__file__).resolve().parent


APP_DIR = app_dir()
BUNDLE_DIR = bundle_dir()
CONFIG_PATH = APP_DIR / "config.json"
WIP_TRACKER_PATH = APP_DIR / "WIP_Tracker.txt"


# Default config used only if no config.json and no bundled default exist.
_DEFAULT_CONFIG = {
    "target_schema": [
        "Part Name", "Measuring Unit", "Spare Group", "DrawingNo", "DrawingPosNo",
        "Spare Part No", "Classification", "ClassificationNo.", "Type", "Text",
        "Ref.No", "Manufacturer", "Internal Remark", "RefPage",
    ],
    "header_aliases": {
        "Part Name": ["Description", "Name of Parts", "Designation", "Name"],
        "DrawingPosNo": ["No.", "Item", "Key No.", "Series No.", "Item No.", "Pos No"],
        "Spare Part No": ["Part No.", "Code No.", "Part Number", "Article / Ref. No."],
        "DrawingNo": ["Plate", "Dwg. No.", "Drawing No.", "Page", "Dimension Drawing"],
        "Text": ["Material", "Size & Material", "Dimension", "Model", "Type & Remarks"],
    },
    "rules": {
        "title_case_blacklist": ["of", "with", "for", "and", "in", "the"],
        "recognized_materials": ["SUS304", "EPDM", "Bronze", "FC200", "A135-M65", "PTFE"],
        "max_material_length_for_prefix": 15,
        "dimension_regex": r"(\d+(?:\.\d+)?)\s*(?:mm|meter|inch|\")",
        "fuzzy_threshold": 88,
    },
    "options": {
        "enable_language_detection": False,
    },
}


def ensure_config() -> dict:
    """
    Guarantee an editable config.json exists alongside the app.
    Priority: existing user config -> bundled default config -> hardcoded default.
    """
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            # Corrupt config: back it up, regenerate, but don't crash.
            backup = CONFIG_PATH.with_suffix(".json.bak")
            try:
                shutil.copy2(CONFIG_PATH, backup)
            except OSError:
                pass
            _show_warning(
                "Configuration error",
                f"config.json was unreadable ({exc}).\n"
                f"A backup was saved as {backup.name} and defaults were restored.",
            )

    # Seed from bundled default if present (frozen build ships one), else hardcoded.
    bundled_default = BUNDLE_DIR / "config.json"
    if bundled_default.exists() and bundled_default != CONFIG_PATH:
        try:
            shutil.copy2(bundled_default, CONFIG_PATH)
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            pass

    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(_DEFAULT_CONFIG, fh, indent=2)
    return dict(_DEFAULT_CONFIG)


def _show_warning(title: str, text: str) -> None:
    """Show a warning even if QApplication isn't fully up yet."""
    app = QApplication.instance()
    created = False
    if app is None:
        app = QApplication(sys.argv)
        created = True
    QMessageBox.warning(None, title, text)
    if created:
        app.quit()


def _set_windows_app_id() -> None:
    """Give Windows a stable AppUserModelID so the taskbar shows our own icon
    (and groups our windows) instead of the generic python.exe icon."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Parsel.ManualParser.1"
        )
    except Exception:
        pass


def main() -> int:
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("Parsel")
    app.setOrganizationName("Parsel")

    # App identity (window + taskbar). Generated at runtime — no asset files.
    from ui.icon import app_icon, splash_pixmap
    from ui.theme import STYLESHEET, light_palette
    # Fusion + a fixed light palette so the app looks identical regardless of
    # the OS light/dark setting (prevents dark-on-dark on unstyled widgets).
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setWindowIcon(app_icon())
    app.setStyleSheet(STYLESHEET)

    # Splash screen: importing the engine (PyMuPDF, pandas, RapidOCR) takes a
    # moment. Show branding immediately so launch never looks frozen.
    from PySide6.QtWidgets import QSplashScreen
    from PySide6.QtCore import Qt as _Qt
    splash = QSplashScreen(splash_pixmap())
    splash.show()

    def _say(msg: str) -> None:
        splash.showMessage(f"   {msg}",
                           _Qt.AlignBottom | _Qt.AlignLeft, QColor("#e9f2fb"))
        app.processEvents()

    _say("Loading…")
    config = ensure_config()

    # Import here so a missing optional dep surfaces as a dialog, not a silent exit.
    _say("Starting engine…")
    from ui.main_window import MainWindow

    paths = {
        "app_dir": str(APP_DIR),
        "config_path": str(CONFIG_PATH),
        "wip_tracker": str(WIP_TRACKER_PATH),
    }

    window = MainWindow(config=config, paths=paths)
    # Gentle launch fade-in. Launch is a rare event, so a touch of delight is
    # warranted; strong ease-out keeps it feeling instant, not sluggish.
    from PySide6.QtCore import QPropertyAnimation, QEasingCurve
    window.setWindowOpacity(0.0)
    window.show()
    splash.finish(window)
    fade = QPropertyAnimation(window, b"windowOpacity")
    fade.setDuration(160)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.setEasingCurve(QEasingCurve.OutCubic)
    fade.start()
    window._launch_fade = fade   # keep a reference so it isn't garbage-collected
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
