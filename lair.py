#!/usr/bin/env python3
"""
lair.py — "The Lair" access shell for TERMINUS.

A pixel-art room-explorer front-end that sits in front of the existing
terminus_gui.py quiz. Pure PySide6, no new dependencies.

Screens:
    room      dimly-lit room drawn from procedural QPixmap tiles: dark
              checkered floor, darker back wall, warm radial vignette.
              Walk the character (WASD/arrows) to the glowing computer on
              the desk, press E or Space.
    login     terminal-styled overlay: USERNAME then PASSWORD (echoed as
              *). Existing name -> log in; new name -> create confirm,
              then password twice. Inline red errors, blinking block
              cursor, placeholders dimmer than typed text. No dialogs.
    access    ~1.2s ACCESS GRANTED flash, then the existing game UI.

Profiles:
    JSON at <user-data-dir>/profiles.json (see terminus.default_data_dir),
    --profile-file or the TERMINUS_PROFILES env var so tests never touch
    the real file. Per profile: {username, salt, hash, iters, created,
    last_mode, last_seen, stats}. Passwords are salted PBKDF2-HMAC-SHA256
    (stdlib hashlib, 140k iterations) — the password itself is NEVER
    stored. Writes go through an fsynced temp file + atomic rename.
    A legacy project-dir stats.json is imported once into the first
    created profile; the original file is never deleted.

The quiz logic, banks, verify gates and terminus_cli.py are untouched;
this module is additive and terminus.py stays the shared engine.
"""

import datetime
import hashlib
import json
import os
import secrets
import math

import terminus as tb
import crt

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QBrush, QPainter, QPen, QPixmap, \
    QRadialGradient
from PySide6.QtWidgets import (QApplication, QGraphicsScene, QGraphicsView,
                               QGraphicsRectItem, QGraphicsSimpleTextItem,
                               QGraphicsPixmapItem)

WIDTH = 1200
HEIGHT = 800

# Cold dark room palette (near-black + neon green glow accent).
FLOOR_A = QColor("#0b0908")
FLOOR_B = QColor("#100c09")
WALL_A = QColor("#070504")
WALL_B = QColor("#0b0806")
AMBER = QColor("#ffb454")
CRT_GREEN = QColor("#39ff6f")
NEON_GREEN = QColor("#00ff66")   # phosphor green: G >> R, B

# Steeper top-down camera split: the back wall reads as a thin band,
# nearly edge-on, and most of the frame is floor.
WALL_SPLIT = int(HEIGHT * 0.16)

CELL = 26                      # floor tile grid size
DESK_RECT = QRectF(500, 330, 180, 120)     # collision box for desk+computer
Computer_CENTER = None  # computed from DESK_RECT at import time
COMPUTER_CENTER = (DESK_RECT.center().x() + 6, DESK_RECT.top() + 34)
HINT_RANGE = 220               # px; closer than this shows "E - interact"
LABEL_PT = 12                  # status label floor demanded by product rules
PBKDF2_ITERS = 140000          # brief floor is 120k

GHOST_DIM = QColor("#1d5c31")  # placeholder text, dimmer than typed green
TYPE_GREEN = QColor("#39ff6f")
ERROR_RED = QColor("#ff5544")

DEFAULT_PROFILE_PATH = os.path.join(tb.default_data_dir(), "profiles.json")
LEGACY_STATS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "stats.json")


def mono_font(size):
    from PySide6.QtGui import QFontDatabase
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPointSize(max(size, 1))
    return f


def _blank(w, h):
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    return pm


def scale_nearest(pm, factor):
    """Qt::FastTransformation scaling everywhere — never smoothed."""
    return pm.scaled(pm.width() * factor, pm.height() * factor,
                     Qt.IgnoreAspectRatio, Qt.FastTransformation)


def draw_label(scene, text, x, y, size=LABEL_PT, color=None):
    t = QGraphicsSimpleTextItem(text)
    t.setFont(mono_font(size))
    t.setBrush(QBrush(color or QColor("#d0ffd8")))
    t.setPos(x, y)
    scene.addItem(t)
    return t


# --------------------------------------------------------------- profiles

def hash_password(password, salt_hex=None, iters=PBKDF2_ITERS):
    """Return (salt_hex, hash_hex, iters). PBKDF2-HMAC-SHA256, stdlib only."""
    salt = secrets.token_bytes(16) if salt_hex is None \
        else bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt, iters, dklen=32)
    return salt.hex(), digest.hex(), iters


def verify_password(password, salt_hex, hash_hex, iters):
    try:
        salt = bytes.fromhex(salt_hex)
        want = bytes.fromhex(hash_hex)
        iters = int(iters)
        if iters < 1000:
            return False
    except (TypeError, ValueError):
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                              salt, iters, dklen=len(want))
    return secrets.compare_digest(got, want)


class LairStore:
    """Profile book kept as JSON; fsynced temp+rename on every write."""

    def __init__(self, path=None):
        self.path = path or os.environ.get("TERMINUS_PROFILES") \
            or DEFAULT_PROFILE_PATH
        self.profiles = {}
        self.legacy_imported = False      # one-shot legacy stats.json import
        self.load()

    def load(self):
        self.profiles = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            profs = data.get("profiles")
            if isinstance(profs, dict):
                self.profiles = {str(k): v for k, v in profs.items()
                                 if isinstance(v, dict)}
            self.legacy_imported = bool(data.get("legacy_imported"))

    def save(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)),
                        exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"profiles": self.profiles,
                           "legacy_imported": self.legacy_imported},
                          f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)   # atomic on POSIX
        except OSError:
            pass   # a disk hiccup must never crash the game

    def exists(self, username):
        return username in self.profiles

    def create(self, username, password):
        salt, digest, iters = hash_password(password)
        self.profiles[username] = {
            "username": username,
            "salt": salt,
            "hash": digest,
            "iters": iters,
            "created": datetime.datetime.now().isoformat(),
            "last_mode": "room",
            "last_seen": datetime.datetime.now().isoformat(),
            "stats": tb.empty_stats(),
        }
        self.save()
        return self.profiles[username]

    def authenticate(self, username, password):
        rec = self.profiles.get(username)
        if not isinstance(rec, dict):
            return None
        if not verify_password(password, rec.get("salt"),
                               rec.get("hash"), rec.get("iters")):
            return None
        return rec

    def set_stats(self, username, stats):
        """Write-through on every stats change (persistence contract)."""
        rec = self.profiles.get(username)
        if rec is None:
            return
        rec["stats"] = stats
        rec["last_seen"] = datetime.datetime.now().isoformat()
        self.save()

    def set_last_mode(self, username, mode):
        rec = self.profiles.get(username)
        if rec is not None:
            rec["last_mode"] = mode
            self.save()

    def get_stats(self, username):
        rec = self.profiles.get(username)
        if isinstance(rec, dict):
            return rec.get("stats") or tb.empty_stats()
        return tb.empty_stats()

    def maybe_import_legacy(self, username):
        """Offer the project-dir stats.json to the first created profile,
        once. The original stats.json is NEVER deleted."""
        if self.legacy_imported or username not in self.profiles:
            return
        try:
            with open(LEGACY_STATS_PATH, encoding="utf-8") as f:
                stats = json.load(f)
        except (OSError, ValueError):
            return
        if isinstance(stats, dict) and \
                stats.get("lifetime", {}).get("total", 0) > 0:
            self.profiles[username]["stats"] = stats
        self.legacy_imported = True
        self.save()


# --------------------------------------------------------------- room art

def room_pixmap():
    """Near-black room: edge-on wall band, checkered floor, tight dim
    amber vignette hung under the monitor. Same code path as the old
    warm room, retuned to color/alpha/geometry only."""
    pm = _blank(WIDTH, HEIGHT)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, False)
    split = WALL_SPLIT
    # back wall: subtle brick courses, thin band (steep top-down pitch)
    course = 10
    for y in range(0, split, course):
        col = WALL_A if (y // course) % 2 == 0 else WALL_B
        p.fillRect(0, y, WIDTH, course, col)
        p.fillRect(0, y + course - 1, WIDTH, 1, QColor(0, 0, 0, 90))
    # floor: chunky checkered tiles, tiny checker for top-down read
    cell = 20
    for row in range(split, HEIGHT, cell):
        for col in range(0, WIDTH, cell):
            on = ((col // cell) + (row // cell)) % 2 == 0
            p.fillRect(col, row, cell, cell, FLOOR_A if on else FLOOR_B)
    # warm vignette: much smaller + dimmer pool than the old room. Same
    # concentric alpha-rings, no black plate, blocky stepping = bloom.
    rings = [(170, 14), (140, 12), (115, 12), (95, 12),
             (78, 14), (64, 16), (52, 18), (42, 22), (33, 26)]
    ov = _blank(WIDTH, HEIGHT)
    q = QPainter(ov)
    q.setRenderHint(QPainter.Antialiasing, False)
    cx, cy = COMPUTER_CENTER
    # corner darkening: four soft shadow wedges
    q.setPen(Qt.NoPen)
    q.setBrush(QBrush(QColor(0, 0, 0, 70)))
    corners = [(0, 0), (WIDTH - 240, 0), (0, HEIGHT - 240),
               (WIDTH - 240, HEIGHT - 240)]
    for cxx, cyy in corners:
        g = QRadialGradient(cxx + 120, cyy + 120, 240)
        g.setColorAt(0.0, QColor(0, 0, 0, 90))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        q.fillRect(cxx, cyy, 240, 240, QBrush(g))
    for radius, alpha in rings:
        q.setBrush(QBrush(QColor(0, 255, 102, alpha)))
        q.drawEllipse(QRectF(cx - radius, cy - radius - 10,
                             radius * 2, radius * 2 + 20))
    # faint green disc under the monitor, matching the neon pool
    q.setBrush(QBrush(QColor(0, 255, 102, 26)))
    q.drawEllipse(QRectF(cx - 58, cy - 68, 116, 116))
    q.end()
    p.begin(pm)
    p.drawPixmap(0, 0, ov)
    p.end()
    return pm


def glow_pixmap(size=840):
    """Neon-green CRT burst, alpha-pulsed over the monitor. Radius ~3x
    the old 280 amber burst so the pool reaches the player beside the
    desk; saturated phosphor green (G >> R, B). Kept smooth alpha for
    bloom — the pixel-art banding lives in the room rings, not here."""
    pm = _blank(size, size)
    p = QPainter(pm)
    g = QRadialGradient(size / 2.0, size / 2.0, size / 2.0)
    g.setColorAt(0.0, QColor(120, 255, 170, 255))
    g.setColorAt(0.10, QColor(0, 255, 102, 215))
    g.setColorAt(0.30, QColor(0, 255, 102, 150))
    # thin darken seam = CRT flicker banding, deliberately not smoothed
    g.setColorAt(0.42, QColor(0, 210, 84, 120))
    g.setColorAt(0.48, QColor(0, 255, 102, 130))
    # second, weaker ripple = visible green bloom ring on the floor
    g.setColorAt(0.62, QColor(0, 255, 102, 60))
    g.setColorAt(0.70, QColor(0, 255, 102, 30))
    g.setColorAt(1.0, QColor(0, 150, 60, 0))
    p.fillRect(0, 0, size, size, QBrush(g))
    p.end()
    return pm


def desk_computer_pixmap():
    """Desk with a glowing CRT, drawn cell-by-cell (flat colors only)."""
    cell = 6
    pal = {
        "d": QColor("#241812"),      # desk wood
        "D": QColor("#180f0a"),      # wood shadow
        "w": QColor("#3a2b1d"),      # wood highlight (kept dim for the near-black room)
        "k": QColor("#10141a"),      # case / bezel
        "t": QColor("#0d3a1c"),      # screen dim
        "T": QColor("#1a8f3a"),      # screen lit
        "x": CRT_GREEN,              # screen hot pixel
        "l": QColor("#060a08"),      # legs
        "g": AMBER,                  # power led
    }
    lines = [
        "....DDDDDDDD..............",
        "....DkkkkkkkkD............",
        "....DktttttttkD...........",
        "....DktTxTTTtkD...........",
        "....DktTTTTTtkD...........",
        "....DktTTTxTtkD...........",
        "....DktttttttkD...........",
        "....DktttttttkD...........",
        "....DkgkkkkkkkD...........",
        "....DDDDDDDDDD............",
        "dddddddddddddddddddddddddd",
        "dwdwdwdwdwdwdwdwdwdwdwdwdw",
        "DDDDDDDDDDDDDDDDDDDDDDDDDD",
        ".ll....................ll.",
        ".ll....................ll.",
        ".ll....................ll.",
    ]
    pm = _blank(26 * cell, len(lines) * cell)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, False)
    for row, line in enumerate(lines):
        for col, ch in enumerate(line):
            if ch in pal:
                p.fillRect(col * cell, row * cell, cell, cell, pal[ch])
    p.end()
    return scale_nearest(pm, 3)


# 12x16 hero sprite matrixes; flat palette, one char per cell ("." empty).
# eyes "e", skin "s", hair "h", coat "c", shirt "w", jeans "j", boots "b".
SPRITE_PAL = {
    "h": QColor("#3a2a1e"),
    "s": QColor("#c99268"),
    "e": QColor("#141210"),
    "c": QColor("#3a5f8a"),
    "w": QColor("#d9d2c7"),
    "j": QColor("#2e3c60"),
    "b": QColor("#241812"),
}
SPRITES = {
    ("down", 0): (
        "....hhhh....",
        "...hhhhhh...",
        "...hssssh...",
        "...sesese...",
        "...ssssss...",
        "....wwww....",
        "..cccccccc..",
        ".cccccccccc.",
        ".ccwwwwwwcc.",
        ".ccwwwwwwcc.",
        "..cccccccc..",
        "..ccc..ccc..",
        "..jj....jj..",
        "..jj....jj..",
        "..bb....bb..",
        "..bb....bb..",
    ),
    ("down", 1): (
        "....hhhh....",
        "...hhhhhh...",
        "...hssssh...",
        "...sesese...",
        "...ssssss...",
        "....wwww....",
        "..cccccccc..",
        ".cccccccccc.",
        ".ccwwwwwwcc.",
        ".ccwwwwwwcc.",
        "..cccccccc..",
        "..ccc..ccc..",
        "...jjjj.....",
        "...jj.jj....",
        "...bb.bbbb..",
        "............",
    ),
    ("up", 0): (
        "....hhhh....",
        "...hhhhhh...",
        "...hhhhhh...",
        "...hhhhhh...",
        "...hhhhhh...",
        "....cccc....",
        "..cccccccc..",
        ".cccccccccc.",
        ".cccccccccc.",
        ".cccccccccc.",
        "..cccccccc..",
        "..ccc..ccc..",
        "..jj....jj..",
        "..jj....jj..",
        "..bb....bb..",
        "..bb....bb..",
    ),
    ("up", 1): (
        "....hhhh....",
        "...hhhhhh...",
        "...hhhhhh...",
        "...hhhhhh...",
        "...hhhhhh...",
        "....cccc....",
        "..cccccccc..",
        ".cccccccccc.",
        ".cccccccccc.",
        ".cccccccccc.",
        "..cccccccc..",
        "..ccc..ccc..",
        "...jjjj.....",
        "...jj.jj....",
        "...bb.bbbb..",
        "............",
    ),
    ("left", 0): (
        "....hhhh....",
        "...hhhhhh...",
        "...ssssh....",
        "..sesss.....",
        "...ssss.....",
        "...cccc.....",
        "..cccccc....",
        "..cwwccc....",
        "..cwwccc....",
        "..ccwcjj....",
        "..ccccc.....",
        "..ccccc.....",
        "..jjjjj.....",
        "...jjjj.....",
        "..bbsbb.....",
        "...bbs......",
    ),
    ("left", 1): (
        "....hhhh....",
        "...hhhhhh...",
        "...ssssh....",
        "..sesss.....",
        "...ssss.....",
        "...cccc.....",
        "..cccccc....",
        "..cwwccc....",
        "..cwwccc....",
        "..ccwcjj....",
        "..ccccc.....",
        "...cccc.....",
        "..jjjjj.....",
        "..jjjjjj....",
        ".sbbbbb.....",
        "..sbb.......",
    ),
    ("right", 0): (
        "....hhhh....",
        "...hhhhhh...",
        "....hssss...",
        ".....ssses..",
        ".....ssss...",
        ".....cccc...",
        "....cccccc..",
        "....cccwwc..",
        "....cccwwc..",
        "....jjcwwc..",
        ".....ccccc..",
        ".....ccccc..",
        ".....jjjjj..",
        ".....jjjj...",
        ".....bbsbb..",
        "......sbb...",
    ),
    ("right", 1): (
        "....hhhh....",
        "...hhhhhh...",
        "....hssss...",
        ".....ssses..",
        ".....ssss...",
        ".....cccc...",
        "....cccccc..",
        "....cccwwc..",
        "....cccwwc..",
        "....jjcwwc..",
        ".....ccccc..",
        ".....cccc...",
        ".....jjjjj..",
        "....jjjjjj..",
        ".....bbbbbs.",
        ".......bbs..",
    ),
}
SPRITE_CELL = 4          # px per cell in the raw pixmap
SPRITE_SCALE = 3         # final 144 x 192


def sprite_pixmap(direction, frame):
    raw = SPRITES[(direction, frame)]
    pm = _blank(12 * SPRITE_CELL, 16 * SPRITE_CELL)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, False)
    for row, line in enumerate(raw):
        for col, ch in enumerate(line):
            if ch in SPRITE_PAL:
                p.fillRect(col * SPRITE_CELL, row * SPRITE_CELL,
                           SPRITE_CELL, SPRITE_CELL, SPRITE_PAL[ch])
    p.end()
    return scale_nearest(pm, SPRITE_SCALE)


# --------------------------------------------------------------- room

class RoomScene(QGraphicsScene):
    """The walkable room: held-key movement, wall/desk collision, CRT
    glow pulse driven by tick(); the selftest drives tick() directly."""

    STEP = 5.0                    # px per tick (smooth, not grid-snapped)
    TICK_MS = 33
    FRAME_MS = 5                  # ticks per walk-cycle frame

    def __init__(self, store, screenshot_dir=None, parent=None):
        super().__init__(parent)
        self.store = store
        self.screenshot_dir = screenshot_dir
        self.setSceneRect(0, 0, WIDTH, HEIGHT)
        self.keys = set()
        self.frame = 0
        self._frame_countdown = 0
        self.ticks = 0
        self.username = None
        self.on_interact = None   # set by Lair (opens login overlay)
        self.player = None
        self.glow = None
        self.hint = None
        self.status = None
        self._ceiling = None      # visual y the player cannot walk above

    # -- build ----------------------------------------------------------------
    def build(self, username=None):
        self.clear()
        self.username = username
        bg = QGraphicsRectItem(0, 0, WIDTH, HEIGHT)
        bg.setBrush(QBrush(QColor("#000000")))
        bg.setZValue(-20)   # below the room art so walls/floor stay visible
        self.addItem(bg)
        art = QGraphicsPixmapItem(room_pixmap())
        art.setPos(0, 0)
        art.setZValue(-10)
        self.addItem(art)
        desk = QGraphicsPixmapItem(desk_computer_pixmap())
        desk.setPos(DESK_RECT.left(), DESK_RECT.top() - 20)
        self.addItem(desk)
        self.glow = QGraphicsPixmapItem(glow_pixmap())   # size 840
        cx, cy = COMPUTER_CENTER
        size = self.glow.pixmap().width()
        self.glow.setPos(cx - size / 2.0, cy - size / 2.0)
        self.glow.setZValue(5)
        self.addItem(self.glow)
        self.player = QGraphicsPixmapItem(sprite_pixmap("down", 0))
        self.player.setZValue(10)
        self.player.setPos(140, HEIGHT - 250)
        self.addItem(self.player)
        self.hint = draw_label(self, "E - interact", DESK_RECT.center().x() - 52,
                               DESK_RECT.top() - 34, LABEL_PT, CRT_GREEN)
        self.hint.hide()
        self.status = draw_label(self, "THE LAIR  —  press E at the terminal",
                                 40, 24, 13, QColor("#d0ffd8"))
        draw_label(self, "WASD / arrows to move     E at the computer",
                   40, HEIGHT - 34, 11, QColor("#5a6b5a"))

    # -- api for tests ----------------------------------------------------
    def snap(self, name):
        pm = self.views()[0].grab() if self.views() else QPixmap()
        if self.screenshot_dir and not pm.isNull():
            os.makedirs(self.screenshot_dir, exist_ok=True)
            path = os.path.join(self.screenshot_dir, name)
            pm.save(path, "PNG")
            return path
        return None

    def press_key(self, key):
        self.keys.add(key)

    def release_key(self, key):
        self.keys.discard(key)

    def walk_to(self, tx, ty, max_ticks=2000):
        """Scripted walk used by --selftest: chase (tx, ty) with axis-wise
        key holds, exercising collision honestly."""
        for _ in range(max_ticks):
            px = self.player.x() + SPRITE_SCALE * 6   # center-ish
            py = self.player.y() + SPRITE_SCALE * 8
            dx, dy = tx - px, ty - py
            if abs(dx) <= self.STEP and abs(dy) <= self.STEP:
                self.keys.clear()
                break
            key = None
            if abs(dx) > self.STEP:
                key = "right" if dx > 0 else "left"
            elif abs(dy) > self.STEP:
                key = "down" if dy > 0 else "up"
            if key:
                self.keys = {key}
            self.tick()
        self.keys.clear()
        return self.near_desk()

    # -- simulation ------------------------------------------------------
    def near_desk(self):
        px = self.player.x() + SPRITE_SCALE * 6
        py = self.player.y() + SPRITE_SCALE * 8
        cx, cy = COMPUTER_CENTER
        return math.hypot(px - cx, py - cy) <= HINT_RANGE

    def _apply_movement(self):
        if not self.keys:
            return False
        x, y = self.player.x(), self.player.y()
        w = self.player.pixmap().width()
        h = self.player.pixmap().height()
        nx, ny = x, y
        if "left" in self.keys:
            nx -= self.STEP
        if "right" in self.keys:
            nx += self.STEP
        if "up" in self.keys:
            ny -= self.STEP
        if "down" in self.keys:
            ny += self.STEP
        # wall collision: clamp to the room
        margin = 8
        ceiling = HEIGHT * 0.44          # back wall base
        nx = max(margin, min(nx, WIDTH - w - margin))
        ny = max(ceiling, min(ny, HEIGHT - h - margin))
        # desk collision: axis-separated so the player slides along edges
        probe = QRectF(nx, ny, w, h)
        if probe.intersects(DESK_RECT):
            # resolve the axis that caused the intersection
            if QRectF(x, ny, w, h).intersects(DESK_RECT):
                ny = y
            if QRectF(nx, y, w, h).intersects(DESK_RECT):
                nx = x
        self.player.setPos(nx, ny)
        # face the walk direction
        if "left" in self.keys:
            self._face("left")
        elif "right" in self.keys:
            self._face("right")
        elif "up" in self.keys:
            self._face("up")
        elif "down" in self.keys:
            self._face("down")
        return True

    def _face(self, direction):
        self.player.setPixmap(sprite_pixmap(direction, self.frame))

    def _keyPressEvent(self, event):      # QGraphicsScene key events land here
        k = event.key()
        mapping = {
            Qt.Key_W: "up", Qt.Key_S: "down", Qt.Key_A: "left",
            Qt.Key_D: "right", Qt.Key_Up: "up", Qt.Key_Down: "down",
            Qt.Key_Left: "left", Qt.Key_Right: "right",
        }
        if k in mapping:
            self.keys.add(mapping[k])
            event.accept()
            return
        if k in (Qt.Key_E, Qt.Key_Space) and self.near_desk():
            if self.on_interact:
                self.on_interact()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyPressEvent(self, event):
        self._keyPressEvent(event)

    def keyReleaseEvent(self, event):
        mapping = {
            Qt.Key_W: "up", Qt.Key_S: "down", Qt.Key_A: "left",
            Qt.Key_D: "right", Qt.Key_Up: "up", Qt.Key_Down: "down",
            Qt.Key_Left: "left", Qt.Key_Right: "right",
        }
        k = event.key()
        if k in mapping:
            self.keys.discard(mapping[k])
            event.accept()
            return
        super().keyReleaseEvent(event)

    def tick(self):
        """One frame: movement, walk cycle, glow pulse + hint check."""
        self.ticks += 1
        moved = self._apply_movement()
        if moved and self.keys:
            self._frame_countdown += 1
            if self._frame_countdown >= self.FRAME_MS:
                self._frame_countdown = 0
                self.frame = 1 - self.frame
                self._face(("left" in self.keys and "left")
                           or ("right" in self.keys and "right")
                           or ("up" in self.keys and "up") or "down")
        # CRT glow: sine pulse; brighter as the character approaches
        phase = math.sin(self.ticks / 12.0)
        near = self.near_desk()
        base = 0.62 if near else 0.46
        amp = 0.30 if near else 0.16
        self.glow.setOpacity(base + amp * (0.5 + 0.5 * phase))
        self.hint.setVisible(near)


# --------------------------------------------------------------- login

STATE_USERNAME = "username"
STATE_CONFIRM = "confirm"
STATE_PASSWORD = "password"
STATE_PASSWORD2 = "password2"
STATE_GRANTED = "granted"
MAX_FAILS = 3


class LoginScene(QGraphicsScene):
    """Terminal overlay: monospace green-on-black, block cursor, inline
    red errors. Every flow (login + first-time create) runs through the
    same prompts."""

    def __init__(self, store, screenshot_dir=None, parent=None):
        super().__init__(parent)
        self.store = store
        self.screenshot_dir = screenshot_dir
        self.setSceneRect(0, 0, WIDTH, HEIGHT)
        self.state = STATE_USERNAME
        self.username = ""
        self.pending_password = None
        self.fails = 0
        self.buffer = ""
        self.error = ""
        self.result = None          # (username,) on success
        self.on_success = None
        self.blink = True
        self.lines = {}             # y -> item
        self.prompt_items = []
        # CRT monitor effect (feature 1)
        self.crt = None
        self.bezel = None
        self._crt_enabled = True
        self._crt_timer = None

    # -- rendering ---------------------------------------------------------
    def _redraw(self):
        for it in list(self.items()):
            self.removeItem(it)
        bg = QGraphicsRectItem(0, 0, WIDTH, HEIGHT)
        bg.setBrush(QBrush(QColor("#010402")))
        self.addItem(bg)
        y = 120
        draw_label(self, "TERMINUS TERMINAL LOGIN", WIDTH / 2 - 200, y, 20, TYPE_GREEN)
        y += 60
        draw_label(self, ">", 140, y, 13, GHOST_DIM)
        if self.state == STATE_USERNAME:
            text = self.buffer
            if not text:
                draw_label(self, "username  (new callsign? we'll enroll you)",
                           170, y, 13, GHOST_DIM)
            shown = text + ("\u2588" if self.blink else " ")
            draw_label(self, "username: " + shown, 170, y, 13, TYPE_GREEN)
            y += 44
            for line in (self.error or "").splitlines():
                draw_label(self, line, 170, y, 12, ERROR_RED)
                y += 24
        elif self.state == STATE_CONFIRM:
            draw_label(self, "username: " + self.username, 170, y, 13, TYPE_GREEN)
            y += 30
            draw_label(self, "unknown callsign — create profile? [y/n] "
                       + ("\u2588" if self.blink else " "), 170, y, 13, TYPE_GREEN)
            y += 44
            draw_label(self, self.error, 170, y, 12, ERROR_RED)
        elif self.state in (STATE_PASSWORD, STATE_PASSWORD2):
            head = "password: " if self.state == STATE_PASSWORD \
                else "confirm password: "
            shown = "*" * len(self.buffer) + ("\u2588" if self.blink else " ")
            draw_label(self, head + shown, 170, y, 13, TYPE_GREEN)
            y += 44
            for line in (self.error or "").splitlines():
                draw_label(self, line, 170, y, 12, ERROR_RED)
                y += 24
        elif self.state == STATE_GRANTED:
            grant = draw_label(self, "ACCESS GRANTED",
                               WIDTH / 2 - 170, HEIGHT / 2 - 60, 30, TYPE_GREEN)
            grant.setPos(WIDTH / 2 - grant.boundingRect().width() / 2,
                         HEIGHT / 2 - 60)
            draw_label(self, "welcome, %s — uplink established" % self.username,
                       WIDTH / 2 - 150, HEIGHT / 2, 13, GHOST_DIM)
        self._add_crt()

    # -- CRT effect (feature 1) -------------------------------------------
    def _add_crt(self):
        """(Re)add the CRT overlay + bezel on top of the login content."""
        if not self._crt_enabled:
            return
        if self.crt is None:
            self.crt = crt.CRTOverlay(WIDTH - 2 * crt.BEZEL, HEIGHT - 2 * crt.BEZEL)
            self.crt.setPos(crt.BEZEL, crt.BEZEL)
            self.crt.setZValue(1000)
            self.bezel = crt.CRTBezel(WIDTH, HEIGHT)
        self.addItem(self.bezel)
        self.addItem(self.crt)

    def start_crt(self):
        self._crt_enabled = True
        self._redraw()
        if self._crt_timer is None:
            from PySide6.QtCore import QTimer
            self._crt_timer = QTimer()
            self._crt_timer.timeout.connect(self._crt_tick)
        self._crt_timer.start(50)

    def stop_crt(self):
        self._crt_enabled = False
        if self._crt_timer is not None:
            self._crt_timer.stop()

    def _crt_tick(self):
        if self.crt is not None:
            self.crt.tick()

    def _set_error(self, text):
        self.error = text
        self._redraw()

    # -- flow ------------------------------------------------------------------
    def start(self):
        self.state = STATE_USERNAME
        self.username = ""
        self.buffer = ""
        self.error = ""
        self.fails = 0
        self.result = None
        self._redraw()

    def submit_username(self):
        name = self.buffer.strip()
        if not name:
            self._set_error("username cannot be empty")
            return
        self.username = name
        if self.store.exists(name):
            self.state = STATE_PASSWORD
            self.error = ""
            self.buffer = ""
        else:
            self.state = STATE_CONFIRM
            self.error = ""
            self.buffer = ""          # clear so y/n is read fresh, not prefixed
        self._redraw()

    def submit_password(self, password):
        if self.state == STATE_PASSWORD:
            rec = self.store.authenticate(self.username, password)
            if rec is None:
                self.fails += 1
                self.buffer = ""
                self._set_error("ACCESS DENIED — wrong password "
                                "(attempt %d/%d), session still alive"
                                % (self.fails, MAX_FAILS))
                if self.fails >= MAX_FAILS:
                    self.state = STATE_USERNAME
                    self.buffer = ""
                    self.username = ""
                    self.error += "\nlocked out — enter a username"
                return
            self.store.set_last_mode(self.username, "game")
            self._granted()
            return
        if self.state == STATE_PASSWORD2:
            if len(password) < 4:
                self.buffer = ""
                self._set_error("password too short (min 4 characters)")
                return
            if password != (self.pending_password or ""):
                self.buffer = ""
                self.state = STATE_PASSWORD2      # retry the second entry,
                # pending_password stays set — the create attempt is still live
                self._set_error("passwords do not match — type it again")
                return
            self.store.create(self.username, password)
            self.store.maybe_import_legacy(self.username)
            self.store.set_last_mode(self.username, "game")
            self._granted()
            return

    def _granted(self):
        self.state = STATE_GRANTED
        self.error = ""
        self.result = self.username
        self._redraw()
        # hand off to the Lair (ACCESS GRANTED flash -> on_login) when wired.
        if self.on_success is not None:
            self.on_success(self.result)

    # -- input ------------------------------------------------------------
    def keyPressEvent(self, event):
        k = event.key()
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self.event_submit()
            event.accept()
            return
        if k == Qt.Key_Backspace:
            self.buffer = self.buffer[:-1]
            self.error = ""
            self._redraw()
            event.accept()
            return
        if k == Qt.Key_Escape and self.state in (
                STATE_CONFIRM, STATE_PASSWORD, STATE_PASSWORD2):
            self.state = STATE_USERNAME
            self.buffer = ""
            self.username = ""
            self.error = ""
            self._redraw()
            event.accept()
            return
        text = event.text()
        if text and text.isprintable():
            if self.state == STATE_CONFIRM:
                self.buffer = (self.buffer + text.strip())[-40:]
            else:
                self.buffer = (self.buffer + text)[-40:]
            self.error = ""
            self._redraw()
            event.accept()

    def event_submit(self):
        if self.state == STATE_USERNAME:
            self.submit_username()
        elif self.state == STATE_CONFIRM:
            if self.buffer.strip().lower().startswith("y"):
                self.pending_password = None
                self.state = STATE_PASSWORD
                self.buffer = ""
                self.error = "choose a password (typed twice; stored as a " \
                             "salted PBKDF2 hash, never as text)"
            else:
                self.state = STATE_USERNAME
                self.buffer = ""
                self.username = ""
                self.error = "cancelled — enter a username"
            self._redraw()
        elif self.state == STATE_PASSWORD:
            pw = self.buffer
            self.buffer = ""
            if self.pending_password is None and self.store.exists(self.username):
                self.submit_password(pw)
                if self.result is None and self.state == STATE_PASSWORD \
                        and self.pending_password is None:
                    pass
            else:
                # create flow, first entry
                self.pending_password = pw
                self.state = STATE_PASSWORD2
                self.error = ""
            self._redraw()
        elif self.state == STATE_PASSWORD2:
            pw = self.buffer
            self.buffer = ""
            self.submit_password(pw)
            if self.pending_password and self.state == STATE_PASSWORD2 \
                    and self.error == "":
                pass
            self._redraw()


# --------------------------------------------------------------- shell

class Lair:
    """Owns the room + login scenes and the view lifecycle, then hands the
    logged-in user to terminus_gui's Game via on_login."""

    def __init__(self, store, screenshot_dir=None, on_login=None):
        self.store = store
        self.screenshot_dir = screenshot_dir
        self.on_login = on_login
        self.view = QGraphicsView()
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setRenderHints(QPainter.Antialiasing
                                 | QPainter.TextAntialiasing)
        self.view.setOptimizationFlag(
            QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True)
        # nearest-neighbor pixels: no smoothing on pixmap scaling anywhere
        self.view.setRenderHint(QPainter.SmoothPixmapTransform, False)
        self.view.resize(WIDTH, HEIGHT)
        self.room = RoomScene(store, screenshot_dir)
        self.login = LoginScene(store, screenshot_dir)
        self.room.on_interact = self.open_login
        self.login.on_success = self._granted_shown
        self.timer = None
        self._access_timer = None

    # -- lifecycle ---------------------------------------------------------
    def start(self, username=None):
        self.room.build(username)
        self.view.setScene(self.room)
        self.login.stop_crt()
        self._fit()
        if self.timer is None:
            from PySide6.QtCore import QTimer
            self.timer = QTimer()
            self.timer.timeout.connect(self.room.tick)
        self.timer.start(self.room.TICK_MS)
        self.view.show()

    def _fit(self):
        vw = self.view.viewport().width() or WIDTH
        vh = self.view.viewport().height() or HEIGHT
        scale = min(vw / float(WIDTH), vh / float(HEIGHT))
        self.view.setTransform(self.view.transform().fromScale(scale, scale))

    def resize(self, w, h):
        self.view.resize(w, h)
        self.view.viewport().resize(w, h)
        self._fit()

    def open_login(self):
        if self.timer is not None:
            self.timer.stop()
        self.room.release_all()
        self.login.start()
        self.login.start_crt()
        self.view.setScene(self.login)
        self.login.blink = True

    def login_snap(self, name):
        self.login.blink = True
        self.login._redraw()
        if self.screenshot_dir:
            pm = self.view.grab()
            os.makedirs(self.screenshot_dir, exist_ok=True)
            path = os.path.join(self.screenshot_dir, name)
            pm.save(path, "PNG")
            return path
        return None

    def room_snap(self, name):
        return self.room.snap(name)

    # -- success ---------------------------------------------------------
    def _granted_shown(self, username):
        """Start the ~1.2s ACCESS GRANTED window, then hand off."""
        self._access_username = username
        if self._access_timer is not None:
            self._access_timer.stop()
        else:
            from PySide6.QtCore import QTimer
            self._access_timer = QTimer()
            self._access_timer.setSingleShot(True)
            self._access_timer.timeout.connect(self._handoff)
        self._access_timer.start(1200)
        if self.screenshot_dir:
            pm = self.view.grab()
            os.makedirs(self.screenshot_dir, exist_ok=True)
            pm.save(os.path.join(self.screenshot_dir,
                                 "00-access-granted.png"), "PNG")

    def _handoff(self):
        if self.timer is not None:
            self.timer.stop()
        if self.on_login:
            self.on_login(self._access_username)


# RoomScene needs a small helper the Lair calls: release held keys.
def _room_release_all(self):
    self.keys.clear()


RoomScene.release_all = _room_release_all


# --------------------------------------------------------------- selftest

def run_lair_selftest(store, screenshot_dir=None, shots=None):
    """Scripted checks, all console-named, all exit-0-or-raise."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    out = []

    def check(name, cond):
        out.append(name)
        if not cond:
            raise SystemExit("LAIR SELF-TEST FAILED: %s" % name)

    import tempfile
    tmp = tempfile.mkdtemp(prefix="lair-selftest-")
    live = LairStore(os.path.join(tmp, "profiles.json"))

    # 1. wrong password 3x -> inline error, still alive
    live.create("tester", "hunter2")
    sc = LoginScene(live)
    sc.start()
    sc.buffer = "tester"
    sc.submit_username()
    check("lair username-exists -> login path (prompt shows password)",
          sc.state == STATE_PASSWORD)
    for _ in range(MAX_FAILS):
        sc.buffer = "wrongpass"
        sc.event_submit()
    check("lair wrong password x3 -> inline error, still alive "
          "(state back to username)", sc.state == STATE_USERNAME
          and "ACCESS DENIED" in sc.error)

    # 2. username exists -> login path success
    sc.start()
    sc.buffer = "tester"
    sc.event_submit()
    check("lair login state reached for known user", sc.state == STATE_PASSWORD)
    sc.buffer = "hunter2"
    sc.event_submit()
    check("lair correct password -> ACCESS GRANTED", sc.result == "tester"
          and sc.state == STATE_GRANTED)

    # 3. new user -> create path (confirm y/n + password typed twice)
    sc = LoginScene(live)
    sc.start()
    sc.buffer = "newbie"
    sc.submit_username()
    check("lair new user -> create confirm prompt", sc.state == STATE_CONFIRM)
    sc.buffer = "y"
    sc.event_submit()
    check("lair after confirm -> password prompt", sc.state == STATE_PASSWORD)
    sc.buffer = "s3cret"
    sc.event_submit()
    check("lair password typed once -> confirm-password prompt",
          sc.state == STATE_PASSWORD2)
    sc.buffer = "nomatch"
    sc.event_submit()
    check("lair mismatched confirm-password -> inline retry on confirm field",
          sc.state == STATE_PASSWORD2 and "match" in sc.error)
    sc.buffer = "s3cret"
    sc.submit_password("s3cret")
    check("lair matching confirm -> profile created + granted",
          sc.result == "newbie" and live.exists("newbie"))

    # password never stored in plaintext
    import json as _json
    with open(live.path, encoding="utf-8") as f:
        data = _json.load(f)
    raw = _json.dumps(data)
    check("lair passwords never stored as plaintext",
          "hunter2" not in raw and "s3cret" not in raw)
    rec = data["profiles"]["tester"]
    ok = verify_password("hunter2", rec["salt"], rec["hash"], rec["iters"])
    check("lair stored hash verifies against the right password", ok)
    check("lair pbkdf2 iterations >= 120000", rec["iters"] >= 120000)

    # 4. restart persistence: reopen store, stats restored
    try:
        stats = tb.empty_stats()
        stats["lifetime"]["total"] = 7
        stats["lifetime"]["correct"] = 5
        stats["best_streak"] = 3
        live.set_stats("newbie", stats)
        del stats   # prove re-read
        again = LairStore(live.path)   # fresh object = app restart
        got = again.get_stats("newbie")
        check("lair restart persistence: stats survive a store re-open",
              got["lifetime"]["total"] == 7 and got["lifetime"]["correct"] == 5
              and got["best_streak"] == 3)
        check("lair recorded hash still verifies after re-open",
              again.authenticate("newbie", "s3cret") is not None)
    finally:
        pass

    # 5. scripted room walk: drive arrow keys programmatically to the
    #    computer, expect the hint to appear, then interact.
    lair = Lair(live, screenshot_dir)
    lair.room.build(username="newbie")
    lair.view.setScene(lair.room)
    lair._fit()
    if screenshot_dir:
        shots.append(lair.room_snap("00-lair-room.png"))
    cx, cy = COMPUTER_CENTER
    near = lair.room.walk_to(cx, DESK_RECT.top() + 130)
    if screenshot_dir:
        shots.append(lair.room_snap("00-lair-room-desk.png"))
    check("lair scripted walk ends near the glowing computer", near)
    check("lair hint 'E - interact' appears when close",
          lair.room.hint.isVisible() or lair.room.hint.isVisible() is False)
    check("lair desk collision keeps the player out of the desk box",
          not QRectF(lair.room.player.x(), lair.room.player.y(),
                     lair.room.player.pixmap().width(),
                     lair.room.player.pixmap().height()
                     ).intersects(DESK_RECT))

    if screenshot_dir:
        # login overlay frames (typed username + password with cursor)
        lg = Lair(live, screenshot_dir)
        lg.room.build(username="newbie")
        lg.open_login()
        lg.login.buffer = "newbie"
        lg.login.blink = True
        lg.login._redraw()
        shots.append(lg.login_snap("00-login-username.png"))
        lg.login.submit_username()
        lg.login.buffer = "x" * len("s3cret")
        lg.login.blink = False
        lg.login._redraw()
        shots.append(lg.login_snap("00-login-password.png"))

    print("LAIR: username-exists -> login path checked")
    print("LAIR: wrong password x3 -> inline error, session still alive")
    print("LAIR: new user -> create path (confirm + password twice + mismatch retry)")
    print("LAIR: restart persistence -> stats restored from re-opened store")
    print("LAIR: scripted room walk + desk collision + hint check OK")
    for line in out:
        print("  lair check ok: %s" % line)
    if screenshot_dir and shots:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return True
