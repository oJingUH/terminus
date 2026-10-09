#!/usr/bin/env python3
"""
desktop.py — retro fake-desktop hub shown after login.

A QGraphicsScene that reads as an old terminal OS: a dark dithered wallpaper
with a subtle grid, a bottom taskbar with a TERMINUS "start" button (opens a
menu listing every mode + Log Out + Power Off), a live HH:MM clock, and
"logged in as <username>". Desktop icons (pixel-art glyphs drawn in code, each
with a label) are single-click selected, arrow-key navigated, and opened on
Enter or double-click. The whole desktop renders inside a CRT (crt.py).

Modes (order = icon order):
    Daily Uplink, Sector Sweep, Cipher Grind, Port Scanner, Crypto Vault,
    Recover Log, Full Breach, Player File
plus Log Out + Power Off in the start menu.

This module is additive: the quiz logic and engine stay in terminus.py, and
the routing callbacks point back into terminus_gui.py.
"""

import datetime
import os
import random

from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import (QBrush, QColor, QFontDatabase, QImage, QPainter,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QGraphicsPixmapItem,
                               QGraphicsRectItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView)

import crt

WIDTH = 1200
HEIGHT = 800
BEZEL = crt.BEZEL

NEON = QColor("#00ff66")
DIM = QColor("#1a8f3a")
DARK = QColor("#0b3d1c")
AMBER = QColor("#ffb454")
TXT = QColor("#9fffc0")
GHOST = QColor("#3d8f5a")
TASKBAR_BG = QColor("#0a1510")

COLS = 4
ICON_W = 130
ICON_H = 100

MODES = [
    ("daily", "Daily Uplink"),
    ("sector", "Sector Sweep"),
    ("cipher", "Cipher Grind"),
    ("ports", "Port Scanner"),
    ("crypto", "Crypto Vault"),
    ("recover", "Recover Log"),
    ("breach", "Full Breach"),
    ("player", "Player File"),
]


def mono_font(size):
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPointSize(max(size, 1))
    return f


# ---------------------------------------------------------------- glyphs

GLYPH_PAL = {
    "#": QColor("#00ff66"),
    "+": QColor("#1a8f3a"),
    "o": QColor("#0b3d1c"),
    "a": QColor("#ffb454"),
    "w": QColor("#d0ffd8"),
}

GLYPHS = {
    "daily": [
        "....##......",
        "....##......",
        "....##......",
        "....##......",
        "...####.....",
        "..##..##....",
        ".##....##...",
        "..##..##....",
        "...####.....",
        "....##......",
        "....##......",
        "....##......",
    ],
    "sector": [
        "..#######...",
        ".##.....##..",
        "##.......##.",
        "#.........#.",
        "#.....##..#.",
        "#.....##..#.",
        "#....##...#.",
        "#...##....#.",
        "#..##.....#.",
        "##.##....##.",
        ".###.....##.",
        "..#......#..",
    ],
    "cipher": [
        "....###.....",
        "...#...#....",
        "...#...#....",
        "...#...#....",
        "....###.....",
        "......##....",
        "......##....",
        "......##....",
        "....###.....",
        "....#.......",
        "....#.......",
        "............",
    ],
    "ports": [
        "..#....#....",
        "..#....#....",
        "..######....",
        "..#....#....",
        "..#....#....",
        "..#....#....",
        "..######....",
        "..#....#....",
        "..#....#....",
        "..#....#....",
        "..######....",
        "............",
    ],
    "crypto": [
        "...#####....",
        "...#...#....",
        "...#...#....",
        "...#...#....",
        "..#######...",
        "..#.....#...",
        "..#..##..#..",
        "..#.....#...",
        "..#.....#...",
        "..#######...",
        "............",
        "............",
    ],
    "recover": [
        "..######....",
        ".##.....#...",
        "#.......##..",
        "#.........#.",
        "#.........#.",
        "#.......###.",
        ".##.....#...",
        "..######....",
        "............",
        "............",
        "............",
        "............",
    ],
    "breach": [
        ".....a......",
        "....aaa.....",
        "....a#a.....",
        "...a###a....",
        "...a###a....",
        "..a#####a...",
        "..a#a#a#a...",
        ".a#######a..",
        ".a###a###a..",
        "a#########a.",
        "............",
        "............",
    ],
    "player": [
        ".....###....",
        "....#####...",
        "....#####...",
        ".....###....",
        ".....###....",
        "....#####...",
        "..#########.",
        "..#########.",
        ".###########",
        ".###########",
        "............",
        "............",
    ],
}


def glyph_pixmap(key, scale=4):
    """Draw a pixel-art glyph (12x12 cells) and nearest-neighbour scale it."""
    rows = GLYPHS[key]
    cell = 1
    pm = QPixmap(12 * cell, 12 * cell)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, False)
    for r, line in enumerate(rows):
        for c, ch in enumerate(line):
            if ch in GLYPH_PAL:
                p.fillRect(c * cell, r * cell, cell, cell, GLYPH_PAL[ch])
    p.end()
    return pm.scaled(12 * cell * scale, 12 * cell * scale,
                     Qt.IgnoreAspectRatio, Qt.FastTransformation)


def wallpaper_pixmap():
    """Near-black dithered background with a subtle grid."""
    pm = QPixmap(WIDTH, HEIGHT)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, False)
    p.fillRect(0, 0, WIDTH, HEIGHT, QColor("#020704"))
    rng = random.Random(7)
    for y in range(0, HEIGHT, 4):
        for x in range(0, WIDTH, 4):
            if rng.random() < 0.5:
                p.fillRect(x, y, 2, 2, QColor(0, 22, 11, 40))
    p.setPen(QPen(QColor(0, 42, 20, 40), 1))
    for x in range(0, WIDTH, 64):
        p.drawLine(x, 0, x, HEIGHT)
    for y in range(0, HEIGHT, 64):
        p.drawLine(0, y, WIDTH, y)
    p.end()
    return pm


# ---------------------------------------------------------------- icon

class DesktopIcon(QGraphicsRectItem):
    """A pixel glyph + label. Selected state draws a highlight border."""

    def __init__(self, key, label, glyph_pm):
        super().__init__(0, 0, ICON_W, ICON_H)
        self.key = key
        self.setPen(Qt.NoPen)
        self.selected = False
        self._pm = QGraphicsPixmapItem(glyph_pm, self)
        self._pm.setPos((ICON_W - glyph_pm.width()) / 2.0, 6)
        self._text = QGraphicsSimpleTextItem(label, self)
        self._text.setFont(mono_font(10))
        self._text.setBrush(QBrush(TXT))
        tw = self._text.boundingRect().width()
        self._text.setPos((ICON_W - tw) / 2.0, 58)

    def set_selected(self, on):
        self.selected = on
        self.update()

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing, False)
        if self.selected:
            painter.setPen(QPen(NEON, 1))
            painter.setBrush(QBrush(QColor(0, 255, 102, 22)))
            painter.drawRect(self.rect())


# ---------------------------------------------------------------- start menu

class StartButton(QGraphicsRectItem):
    """The taskbar's TERMINUS 'start' button."""

    def __init__(self, w=118, h=32, on_click=None):
        super().__init__(0, 0, w, h)
        self.on_click = on_click
        self.setBrush(QBrush(DARK))
        self.setPen(QPen(DIM, 1))
        self._text = QGraphicsSimpleTextItem("TERMINUS", self)
        self._text.setFont(mono_font(11))
        self._text.setBrush(QBrush(NEON))
        self._text.setPos(14, 8)

    def mousePressEvent(self, event):
        if self.on_click:
            self.on_click()
        event.accept()


# ---------------------------------------------------------------- scene

class DesktopScene(QGraphicsScene):
    """The retro OS desktop: wallpaper, icons, taskbar, start menu, CRT."""

    def __init__(self, store, username, screenshot_dir=None, parent=None):
        super().__init__(parent)
        self.store = store
        self.username = username
        self.screenshot_dir = screenshot_dir
        self.setSceneRect(0, 0, WIDTH, HEIGHT)
        self.icons = []
        self.selection = 0
        self.on_launch = None
        self.on_logout = None
        self.on_poweroff = None
        self.taskbar = None
        self.clock_item = None
        self.user_item = None
        self.start_button = None
        self.menu = None
        self._menu_rows = []
        self.crt = None
        self.bezel = None
        self._crt_timer = None
        self._clock_timer = None
        self._build()

    # -- build -----------------------------------------------------------
    def _build(self):
        art = QGraphicsPixmapItem(wallpaper_pixmap())
        art.setPos(0, 0)
        art.setZValue(-10)
        self.addItem(art)

        for i, (key, label) in enumerate(MODES):
            col = i % COLS
            row = i // COLS
            icon = DesktopIcon(key, label, glyph_pixmap(key))
            icon.setPos(BEZEL + 28 + col * 220, BEZEL + 26 + row * 160)
            self.addItem(icon)
            self.icons.append(icon)
        self._select(0)

        self._build_taskbar()
        self._add_crt()

    def _build_taskbar(self):
        bar_y = HEIGHT - BEZEL - 44
        tb = QGraphicsRectItem(BEZEL, bar_y, WIDTH - 2 * BEZEL, 44)
        tb.setBrush(QBrush(TASKBAR_BG))
        tb.setPen(QPen(DIM, 1))
        tb.setZValue(50)
        self.addItem(tb)
        self.taskbar = tb

        self.start_button = StartButton(on_click=self._toggle_menu)
        self.start_button.setPos(BEZEL + 6, bar_y + 6)
        self.start_button.setZValue(51)
        self.addItem(self.start_button)

        self.user_item = QGraphicsSimpleTextItem(
            "logged in as %s" % self.username)
        self.user_item.setFont(mono_font(11))
        self.user_item.setBrush(QBrush(GHOST))
        self.user_item.setPos(BEZEL + 140, bar_y + 14)
        self.user_item.setZValue(51)
        self.addItem(self.user_item)

        self.clock_item = QGraphicsSimpleTextItem(self._clock_text())
        self.clock_item.setFont(mono_font(13))
        self.clock_item.setBrush(QBrush(NEON))
        self.clock_item.setZValue(51)
        self.addItem(self.clock_item)
        self._place_clock()

    def _add_crt(self):
        self.crt = crt.CRTOverlay(WIDTH - 2 * BEZEL, HEIGHT - 2 * BEZEL)
        self.crt.setPos(BEZEL, BEZEL)
        self.crt.setZValue(1000)
        self.addItem(self.crt)
        self.bezel = crt.CRTBezel(WIDTH, HEIGHT)
        self.addItem(self.bezel)   # z 1001 set inside CRTBezel

    # -- clock / timers --------------------------------------------------
    def _clock_text(self):
        return datetime.datetime.now().strftime("%H:%M")

    def _place_clock(self):
        w = self.clock_item.boundingRect().width()
        self.clock_item.setPos(WIDTH - BEZEL - 24 - w, HEIGHT - BEZEL - 44 + 12)

    def start(self):
        if self._crt_timer is None:
            self._crt_timer = QTimer()
            self._crt_timer.timeout.connect(self._crt_tick)
        self._crt_timer.start(50)
        if self._clock_timer is None:
            self._clock_timer = QTimer()
            self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)

    def stop(self):
        if self._crt_timer is not None:
            self._crt_timer.stop()
        if self._clock_timer is not None:
            self._clock_timer.stop()

    def _crt_tick(self):
        if self.crt is not None:
            self.crt.tick()

    def _update_clock(self):
        if self.clock_item is not None:
            self.clock_item.setText(self._clock_text())
            self._place_clock()

    # -- start menu ------------------------------------------------------
    def _toggle_menu(self):
        if self.menu is not None:
            self._close_menu()
        else:
            self._open_menu()

    def _open_menu(self):
        items = list(MODES) + [("logout", "Log Out"), ("poweroff", "Power Off")]
        row_h = 30
        mw = 250
        mh = len(items) * row_h + 8
        mx = BEZEL + 6
        my = HEIGHT - BEZEL - 44 - mh - 6
        menu = QGraphicsRectItem(mx, my, mw, mh)
        menu.setBrush(QBrush(TASKBAR_BG))
        menu.setPen(QPen(DIM, 1))
        menu.setZValue(60)
        self.addItem(menu)
        self.menu = menu
        self._menu_rows = []
        for i, (key, label) in enumerate(items):
            t = QGraphicsSimpleTextItem(label)
            t.setFont(mono_font(12))
            t.setBrush(QBrush(TXT))
            t.setPos(mx + 10, my + 4 + i * row_h)
            t.setZValue(61)
            self.addItem(t)
            self._menu_rows.append(
                (key, QRectF(mx, my + i * row_h, mw, row_h)))

    def _close_menu(self):
        if self.menu is not None:
            self.removeItem(self.menu)
            self.menu = None
        self._menu_rows = []

    def _dispatch(self, key):
        if key == "logout":
            if self.on_logout:
                self.on_logout()
        elif key == "poweroff":
            if self.on_poweroff:
                self.on_poweroff()
        else:
            if self.on_launch:
                self.on_launch(key)

    # -- icon selection --------------------------------------------------
    def _icon_at(self, pos):
        for icon in self.icons:
            if icon.sceneBoundingRect().contains(pos):
                return icon
        return None

    def _select(self, idx):
        self.selection = idx % len(self.icons)
        for i, ic in enumerate(self.icons):
            ic.set_selected(i == self.selection)

    def _move(self, delta):
        self._select(self.selection + delta)

    def _open(self, icon):
        if icon is None and self.icons:
            icon = self.icons[self.selection]
        if icon is not None:
            self._dispatch(icon.key)

    # -- input -----------------------------------------------------------
    def mousePressEvent(self, event):
        if self.menu is not None:
            for key, r in self._menu_rows:
                if r.contains(event.scenePos()):
                    self._close_menu()
                    self._dispatch(key)
                    event.accept()
                    return
            self._close_menu()
            event.accept()
            return
        icon = self._icon_at(event.scenePos())
        if icon is not None:
            self._select(self.icons.index(icon))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        icon = self._icon_at(event.scenePos())
        if icon is not None:
            self._select(self.icons.index(icon))
            self._open(icon)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        k = event.key()
        if k in (Qt.Key_Up, Qt.Key_W):
            self._move(-COLS)
            event.accept()
            return
        if k in (Qt.Key_Down, Qt.Key_S):
            self._move(COLS)
            event.accept()
            return
        if k in (Qt.Key_Left, Qt.Key_A):
            self._move(-1)
            event.accept()
            return
        if k in (Qt.Key_Right, Qt.Key_D):
            self._move(1)
            event.accept()
            return
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self._open(None)
            event.accept()
            return
        if k == Qt.Key_Escape:
            self._close_menu()
            event.accept()
            return
        super().keyPressEvent(event)

    # -- screenshot ------------------------------------------------------
    def snap(self, name):
        # Render the scene directly to a pixmap. An offscreen QGraphicsView
        # that has not been realized can grab its un-rendered white background;
        # QGraphicsScene.render() is deterministic and captures the bezel,
        # wallpaper, icons and CRT overlay in z-order.
        img = QImage(WIDTH, HEIGHT, QImage.Format_ARGB32)
        img.fill(QColor("#0b0d10"))
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing, False)
        self.render(painter)
        painter.end()
        pm = QPixmap.fromImage(img)
        if self.screenshot_dir and not pm.isNull():
            os.makedirs(self.screenshot_dir, exist_ok=True)
            path = os.path.join(self.screenshot_dir, name)
            pm.save(path, "PNG")
            return path
        return None


# ---------------------------------------------------------------- shell

class Desktop:
    """Owns the desktop view + scene lifecycle (mirrors lair.Lair)."""

    def __init__(self, store, screenshot_dir=None):
        self.store = store
        self.screenshot_dir = screenshot_dir
        self.view = QGraphicsView()
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setRenderHints(QPainter.Antialiasing
                                 | QPainter.TextAntialiasing)
        self.view.setRenderHint(QPainter.SmoothPixmapTransform, False)
        self.view.setBackgroundBrush(QBrush(QColor("#020704")))
        self.view.resize(WIDTH, HEIGHT)
        self.scene = None
        self.username = None
        self.on_launch = None
        self.on_logout = None
        self.on_poweroff = None

    def show(self, username):
        if self.scene is not None:
            self.scene.stop()
        self.username = username
        self.scene = DesktopScene(self.store, username, self.screenshot_dir)
        self.scene.on_launch = self.on_launch
        self.scene.on_logout = self.on_logout
        self.scene.on_poweroff = self.on_poweroff
        self.view.setScene(self.scene)
        self._fit()
        self.scene.start()
        self.view.show()

    def hide(self):
        if self.scene is not None:
            self.scene.stop()
        self.view.hide()

    def _fit(self):
        vw = self.view.viewport().width() or WIDTH
        vh = self.view.viewport().height() or HEIGHT
        scale = min(vw / float(WIDTH), vh / float(HEIGHT))
        self.view.setTransform(self.view.transform().fromScale(scale, scale))

    def resize(self, w, h):
        self.view.resize(w, h)
        self.view.viewport().resize(w, h)
        self._fit()

    def snap(self, name):
        if self.scene is not None:
            return self.scene.snap(name)
        return None
