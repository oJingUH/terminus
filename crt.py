#!/usr/bin/env python3
"""
crt.py — CRT monitor post-effect + bezel for TERMINUS.

Pure PySide6, no new dependencies. Two reusable pieces:

  CRTOverlay(w, h)  a QGraphicsRectItem (z ~1000) whose paint() draws OVER the
                    content: horizontal scanlines (2px, darker than the old
                    3px ScanlineOverlay), a subtle vertical RGB phosphor mask
                    (~15% alpha), a radial vignette with a slight center bulge
                    (curvature illusion), a slowly travelling brighter sweep
                    band, and a faint curved glass reflection top-left.
                    tick() advances the sweep and adds a gentle flicker that
                    never drops text legibility below readable.

  CRTBezel(w, h)    draws the monitor frame around the screen aperture — dark
                    rounded plastic with a screen aperture, a small power LED,
                    and "TERMINUS" molded on the lower bezel. Crisp, no
                    smoothing. Content sits inside the aperture.

Coordinate model: the QGraphicsScene stays 1200x800 = the full monitor. The
outer BEZEL-pixel band is plastic; the inner rectangle is the screen aperture
where content + CRTOverlay render. BEZEL is exported so callers can inset
their own layout.
"""

import math
import random

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (QBrush, QColor, QFontDatabase, QLinearGradient,
                           QPainter, QPen, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import QGraphicsRectItem

# plastic bezel thickness (px) around the screen aperture.
BEZEL = 44

SCANLINE_DARK = QColor(0, 0, 0, 74)   # darker than the old 3px overlay
SCANLINE_SPACING = 4                  # 2px line + 2px gap
SCANLINE_H = 2

MASK_ALPHA = 40                       # ~15% of 255, text stays readable

SWEEP_SPEED = 2.2
FLICKER_MAX = 16                      # dark overlay alpha ceiling (subtle)


class CRTOverlay(QGraphicsRectItem):
    """Post-effect drawn over screen content: scanlines, RGB mask, vignette,
    travelling sweep band, glass reflection, and a gentle flicker."""

    def __init__(self, w, h):
        super().__init__()
        self._w = w
        self._h = h
        self.setRect(0, 0, w, h)
        self.setPen(Qt.NoPen)
        self._sweep_y = h * 0.30
        self._flicker = 0.0
        self._ticks = 0

    def tick(self):
        self._ticks += 1
        self._sweep_y += SWEEP_SPEED
        if self._sweep_y > self._h + 140:
            self._sweep_y = -140.0
        # gentle flicker: a small random darkening, never above FLICKER_MAX so
        # text never drops below readable contrast.
        self._flicker = random.uniform(0.0, FLICKER_MAX)
        self.update()

    def paint(self, painter, option, widget=None):
        p = painter
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self._w, self._h

        # 1. vertical RGB phosphor mask (thin stripes, ~15% alpha)
        for x in range(0, w, 3):
            comp = x % 3
            if comp == 0:
                p.fillRect(x, 0, 1, h, QColor(255, 45, 45, MASK_ALPHA))
            elif comp == 1:
                p.fillRect(x, 0, 1, h, QColor(45, 255, 45, MASK_ALPHA))
            else:
                p.fillRect(x, 0, 1, h, QColor(45, 80, 255, MASK_ALPHA))

        # 2. horizontal scanlines (2px, darker than the old overlay)
        for y in range(0, h, SCANLINE_SPACING):
            p.fillRect(0, y, w, SCANLINE_H, SCANLINE_DARK)

        # 3. radial vignette (edges darker) + slight center bulge
        g = QRadialGradient(w / 2.0, h / 2.0, math.hypot(w, h) / 2.0 * 1.05)
        g.setColorAt(0.0, QColor(0, 0, 0, 0))
        g.setColorAt(0.62, QColor(0, 0, 0, 0))
        g.setColorAt(0.85, QColor(0, 0, 0, 66))
        g.setColorAt(1.0, QColor(0, 0, 0, 150))
        p.fillRect(0, 0, w, h, QBrush(g))
        # faint brighter ellipse near center = curvature bulge illusion
        bulge = QRadialGradient(w / 2.0, h / 2.0, min(w, h) * 0.5)
        bulge.setColorAt(0.0, QColor(0, 255, 102, 12))
        bulge.setColorAt(1.0, QColor(0, 255, 102, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(bulge))
        p.drawEllipse(QRectF(0, 0, w, h))

        # 4. travelling sweep band
        sy = self._sweep_y
        sweep = QLinearGradient(0, sy - 60, 0, sy + 60)
        sweep.setColorAt(0.0, QColor(0, 255, 102, 0))
        sweep.setColorAt(0.5, QColor(180, 255, 205, 26))
        sweep.setColorAt(1.0, QColor(0, 255, 102, 0))
        p.fillRect(0, sy - 60, w, 120, QBrush(sweep))

        # 5. curved glass reflection top-left
        refl = QLinearGradient(0, 0, w * 0.7, h * 0.5)
        refl.setColorAt(0.0, QColor(255, 255, 255, 22))
        refl.setColorAt(0.5, QColor(255, 255, 255, 8))
        refl.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(refl))
        p.setPen(Qt.NoPen)
        poly = QPolygonF([
            QPointF(0, 0), QPointF(w * 0.55, 0),
            QPointF(w * 0.30, h * 0.35), QPointF(0, h * 0.20),
        ])
        p.drawPolygon(poly)

        # 6. flicker darkening (alpha wobbles, bounded)
        if self._flicker > 0:
            p.fillRect(0, 0, w, h, QColor(0, 0, 0, int(self._flicker)))


class CRTBezel(QGraphicsRectItem):
    """Monitor frame drawn in the outer BEZEL band of the scene. The aperture
    (inner rectangle) is left transparent so content shows through. Crisp, no
    smoothing."""

    def __init__(self, w, h):
        super().__init__()
        self._w = w
        self._h = h
        self.setRect(0, 0, w, h)
        self.setPen(Qt.NoPen)
        self.setZValue(1001)   # above the CRTOverlay (1000) and content
        self.led_on = True

    def paint(self, painter, option, widget=None):
        p = painter
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self._w, self._h
        b = BEZEL
        body = QColor("#0b0d10")

        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(body))
        # top band
        p.drawRect(0, 0, w, b)
        # bottom band
        p.drawRect(0, h - b, w, b)
        # left band
        p.drawRect(0, b, b, h - 2 * b)
        # right band
        p.drawRect(w - b, b, b, h - 2 * b)

        # recessed seam around the aperture (crisp, two-tone)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#1d2429"), 2))
        p.drawRect(b - 2, b - 2, w - 2 * (b - 2), h - 2 * (b - 2))
        p.setPen(QPen(QColor("#000000"), 1))
        p.drawRect(b, b, w - 2 * b, h - 2 * b)

        # power LED (bottom-right of the bezel)
        led_x = w - b + 16
        led_y = h - b + 16
        led = QColor("#00ff66") if self.led_on else QColor("#0d3a1c")
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(0, 255, 102, 60)))
        p.drawRect(led_x - 2, led_y - 2, 10, 10)
        p.setBrush(QBrush(led))
        p.drawRect(led_x, led_y, 6, 6)

        # molded TERMINUS on the lower bezel (crisp, no text AA)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(11)
        p.setFont(font)
        p.setPen(QColor("#242b31"))
        p.drawText(QRectF(b, h - b, w - 2 * b, b), Qt.AlignCenter, "TERMINUS")
