"""
ui/icon.py
----------
Generate the Parsel application logo at runtime (no binary asset files to ship).

The mark: a serpent (a nod to "Parsel" / Parseltongue) rendered as a single
clean stroke with a head and a forked tongue, in white on an emerald rounded
tile. Used for the window title bar, the taskbar, and the splash screen.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QFont, QBrush, QPen,
    QPainterPath, QLinearGradient,
)

# Parseltongue green
TILE_TOP = "#23a96b"
TILE_BOTTOM = "#0e5234"
MARK = "#ffffff"


def _stroke_pen(color: str, w: float) -> QPen:
    pen = QPen(QColor(color))
    pen.setWidthF(w)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _snake(p: QPainter, x: float, y: float, s: float, color: str) -> None:
    """Draw the serpent mark inside the s×s box at (x, y)."""
    def X(u): return x + u / 100 * s
    def Y(v): return y + v / 100 * s
    w = s * 0.13

    body = QPainterPath()
    body.moveTo(X(30), Y(84))
    body.cubicTo(X(28), Y(60), X(74), Y(64), X(72), Y(44))
    body.cubicTo(X(70), Y(24), X(34), Y(30), X(46), Y(16))
    p.setPen(_stroke_pen(color, w))
    p.setBrush(Qt.NoBrush)
    p.drawPath(body)

    # head
    hx, hy = X(46), Y(16)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(color)))
    p.drawEllipse(QPointF(hx, hy), w * 0.62, w * 0.62)

    # forked tongue flicking up (only at sizes where it won't turn to mush)
    if s >= 40:
        tw = max(1.0, w * 0.32)
        p.setPen(_stroke_pen(color, tw))
        tip = QPointF(hx + w * 0.15, hy - w * 0.55)
        p.drawLine(tip, QPointF(tip.x() - s * 0.05, tip.y() - s * 0.07))
        p.drawLine(tip, QPointF(tip.x() + s * 0.05, tip.y() - s * 0.07))


def _tile(p: QPainter, x: float, y: float, s: float) -> None:
    radius = s * 0.22
    grad = QLinearGradient(x, y, x, y + s)
    grad.setColorAt(0, QColor(TILE_TOP))
    grad.setColorAt(1, QColor(TILE_BOTTOM))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(QRectF(x, y, s, s), radius, radius)


def _draw(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    _tile(p, 0, 0, size)
    _snake(p, 0, 0, size, MARK)
    p.end()
    return pm


def app_icon() -> QIcon:
    """Multi-resolution QIcon for crisp display at every size."""
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_draw(s))
    return icon


def splash_pixmap(w: int = 460, h: int = 260) -> QPixmap:
    """Branded splash image shown while heavy libraries load."""
    pm = QPixmap(w, h)
    pm.fill(QColor("#0c3f26"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    # logo mark, top-left
    p.drawPixmap(28, 28, _draw(72))

    # wordmark
    p.setPen(QPen(QColor("white")))
    tf = QFont("Segoe UI", 22)
    tf.setBold(True)
    p.setFont(tf)
    p.drawText(118, 60, "Parsel")

    sf = QFont("Segoe UI", 11)
    p.setFont(sf)
    p.setPen(QPen(QColor("#bfe6d2")))
    p.drawText(120, 84, "Offline manual to Excel")

    # footer
    p.setPen(QPen(QColor("#7fc4a1")))
    p.setFont(QFont("Segoe UI", 9))
    p.drawText(QRectF(0, h - 38, w - 20, 24),
               Qt.AlignRight | Qt.AlignVCenter,
               "100% offline · no data leaves this PC")
    p.end()
    return pm
