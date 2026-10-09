#!/usr/bin/env python3
"""
terminus_gui.py — TERMINUS 3D terminal-lure edition.

PySide6 / Qt6. All game logic lives in terminus.py, so this GUI and
terminus_cli.py share one code path and cannot drift.

The "3D" is a QGraphicsView with a projective perspective transform: question
panels are QGraphicsRectItems drawn in a scene, tilted and slowly rotated in
3D space, with a mouse-driven parallax so panels shift as the pointer moves.
Panels are fully interactive (hover + click), never decorative. A phosphor
green CRT scanline overlay sits on top as an equivalent post-effect.

Screens:
    - boot sequence   each game's kernel "loads" with its real bank count,
                      ending in ACCESS GRANTED
    - question        panels rotating in 3D, one per option, click to answer
    - feedback        ACCESS GRANTED (green) / TRACE DETECTED (red) flash
    - breach bar      a streak progress bar under the question
    - achievements    ASCII badges rendered from achievements.py

Flags:
    --selftest                 scripted round, no stdin
    --selftest --screenshot DIR  render PNG screenshots to DIR, then exit 0
    --seed N                   deterministic shuffling
    --bundle PATH              explicit bundle path
    --stats PATH               explicit stats path

Run offscreen for headless screenshots:
    QT_QPA_PLATFORM=offscreen /usr/bin/python3 terminus_gui.py \
        --selftest --screenshot ./preview
"""

import argparse
import datetime
import os
import random
import sys

import terminus as tb
import achievements as ach
import lair
import desktop

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QPainter, QPen,
                           QBrush, QPixmap, QTransform, QLinearGradient)
from PySide6.QtWidgets import (QApplication, QGraphicsView, QGraphicsScene,
                               QGraphicsRectItem, QGraphicsSimpleTextItem,
                               QGraphicsItem, QGraphicsTextItem)

# Phosphor-green CRT palette.
GREEN = QColor("#33ff66")
GREEN_DIM = QColor("#1a8f3a")
GREEN_DARK = QColor("#0b3d1c")
BG = QColor("#010503")
RED = QColor("#ff5544")
AMBER = QColor("#ffb454")

WIDTH = 1200
HEIGHT = 800


def mono_font(size):
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPointSize(size)
    return f


class Panel(QGraphicsRectItem):
    """A selectable, clickable terminal panel in the 3D scene."""

    def __init__(self, text, w=520, h=64, color=GREEN, key_label=""):
        super().__init__()
        self.setRect(0, 0, w, h)
        self.text = text
        self.key_label = key_label
        self.color = color
        self._hover = False
        self.setAcceptHoverEvents(True)
        self.setFlags(QGraphicsItem.ItemIsSelectable
                      | QGraphicsItem.ItemIsFocusable)
        self.label = QGraphicsSimpleTextItem(self)
        self.label.setFont(mono_font(11))
        self.label.setBrush(QBrush(QColor("#d0ffd8")))
        self.label.setPos(14, 14)
        self._set_text()

    def _set_text(self):
        prefix = ("[%s]  " % self.key_label) if self.key_label else ""
        shown = prefix + self.text
        if len(shown) > 58:
            shown = shown[:57] + "\u2026"
        self.label.setText(shown)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        fill = GREEN_DARK if self._hover or self.isSelected() else BG
        edge = self.color if (self._hover or self.isSelected()) else GREEN_DIM
        painter.setPen(QPen(QColor("#02150a"), 1))
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(self.rect(), 4, 4)
        painter.setPen(QPen(edge, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(self.rect(), 4, 4)

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        self.setSelected(True)
        if self.scene() is not None:
            self.scene().panel_clicked(self)
        super().mousePressEvent(event)


class ScanlineOverlay(QGraphicsRectItem):
    """Phosphor scanline post-effect drawn over the whole scene."""

    def __init__(self, w, h):
        super().__init__()
        self.setRect(0, 0, w, h)
        self.setZValue(1000)
        self.setPen(Qt.NoPen)
        self._w = w
        self._h = h

    def paint(self, painter, option, widget=None):
        painter.setPen(Qt.NoPen)
        # faint horizontal scanlines
        for y in range(0, self._h, 3):
            painter.setBrush(QBrush(QColor(0, 0, 0, 60)))
            painter.drawRect(0, y, self._w, 1)


class Scene(QGraphicsScene):
    """The game scene. Routes panel clicks to the active handler."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._click = None
        self.on_key = None   # optional key handler (drill / player file)

    def set_click_handler(self, fn):
        self._click = fn

    def panel_clicked(self, panel):
        if self._click is not None:
            self._click(panel)


class TermView(QGraphicsView):
    """QGraphicsView with a projective perspective transform + parallax."""

    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHints(QPainter.Antialiasing
                            | QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setBackgroundBrush(QBrush(BG))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._angle = 0.0
        self._parallax_x = 0.0
        self._parallax_y = 0.0

    def set_pose(self, angle, parallax_x=0.0, parallax_y=0.0):
        self._angle = angle
        self._parallax_x = parallax_x
        self._parallax_y = parallax_y
        self._apply()

    def _apply(self):
        # A projective transform: rotate around Z, then tilt with perspective
        # coefficients in the third column so panels recede like a 3D terminal.
        # The rotation/tilt is applied about the scene center (so content stays
        # in frame instead of swinging off the top-left origin), then the whole
        # scene is scaled to fit the current viewport.
        import math
        rad = math.radians(self._angle)
        c, s = math.cos(rad), math.sin(rad)
        cx = WIDTH / 2.0
        cy = HEIGHT / 2.0
        # m13/m23 give a perspective shear; kept small so text never clips.
        pose = QTransform(c, s, -0.0003, -s, c, -0.0002, 0, 0, 1)
        vw = self.viewport().width() or WIDTH
        vh = self.viewport().height() or HEIGHT
        scale = min(vw / float(WIDTH), vh / float(HEIGHT))
        ox = (vw - WIDTH * scale) / 2.0
        oy = (vh - HEIGHT * scale) / 2.0
        # QTransform's * composes in reversed order (the right operand acts on
        # the point first), so write the point-space chain back to front:
        # parallax -> recenter -> pose -> uncenter -> fit-scale -> fit-offset.
        t = (QTransform.fromTranslate(self._parallax_x, self._parallax_y)
             * QTransform.fromTranslate(-cx, -cy)
             * pose
             * QTransform.fromTranslate(cx, cy)
             * QTransform.fromScale(scale, scale)
             * QTransform.fromTranslate(ox, oy))
        self.setTransform(t)

    def mouseMoveEvent(self, event):
        # Parallax: panels shift with the pointer.
        cx = WIDTH / 2.0
        cy = HEIGHT / 2.0
        self._parallax_x = (event.position().x() - cx) * 0.06
        self._parallax_y = (event.position().y() - cy) * 0.04
        self._apply()
        super().mouseMoveEvent(event)

    def rotate_step(self):
        self.set_pose((self._angle + 1.5) % 360,
                      self._parallax_x, self._parallax_y)

    def keyPressEvent(self, event):
        # Route keyboard to the scene's optional key handler (drill text
        # entry / player-file return), falling back to the default behaviour.
        sc = self.scene()
        handler = getattr(sc, "on_key", None)
        if handler is not None and handler(event):
            return
        super().keyPressEvent(event)


def draw_title(scene, text, y=40, color=GREEN, size=22):
    t = QGraphicsSimpleTextItem(text)
    t.setFont(mono_font(size))
    t.setBrush(QBrush(color))
    t.setPos(WIDTH / 2 - t.boundingRect().width() / 2, y)
    scene.addItem(t)
    return t


def draw_line(scene, text, y, color=GREEN_DIM, size=12, x=None):
    t = QGraphicsSimpleTextItem(text)
    t.setFont(mono_font(size))
    t.setBrush(QBrush(color))
    t.setPos(x if x is not None else WIDTH / 2 - t.boundingRect().width() / 2, y)
    scene.addItem(t)
    return t


class Game:
    """Drives the whole app: boot, question, feedback, achievements screens."""

    def __init__(self, bundle, seed=None, screenshot_dir=None,
                 profile_user=None, store=None, given_profile_path=None):
        self.bundle = bundle
        self.rng = random.Random(seed if seed is not None else 42)
        self.screenshot_dir = screenshot_dir
        self.profile_user = profile_user
        self.store = store
        self._given_profile_path = given_profile_path
        self.scene = Scene()
        self.view = TermView(self.scene)
        self.view.resize(WIDTH, HEIGHT)
        self.scene.setSceneRect(0, 0, WIDTH, HEIGHT)
        self.scene.set_click_handler(self._on_click)
        # stats come from the logged-in profile when there is one
        if profile_user and store is not None:
            self.stats = store.get_stats(profile_user)
        else:
            self.stats = tb.empty_stats()
        self.q_index = 0
        self.questions = tb.select_daily(bundle["questions"], self.rng, 10)
        self.current = None
        self.panels = []
        self.flash = None
        self._shots = []
        self._breach_timer = None
        # mode state (feature 2)
        self.mode = None
        self.mode_title = "DAILY UPLINK"
        self._drill_pool = None
        self._drill_index = 0
        self._drill_kind = None
        self._drill_active = False
        self._drill_current = None
        self._buffer = ""
        self._last_feedback = None
        self._fb_color = GREEN
        self._round_results = []
        self._pick_state = None
        self._pick_target = None
        self._game_panels = []
        self._breach_remaining = 0
        self._countdown_item = None
        self._advance_timer = None
        self._return_timer = None
        self._mode_done_cb = None

    def save_stats(self):
        """Write-through: every stats mutation lands in the profile right away."""
        if self.profile_user and self.store is not None:
            self.store.set_stats(self.profile_user, self.stats)

    def _profile_path(self):
        return os.environ.get("TERMINUS_PROFILES") or self._given_profile_path

    # ---- screenshot helper ----
    def snap(self, name):
        self.scene.update()
        self.view.viewport().update()
        QApplication.processEvents()
        self.view._apply()          # refresh transform against current viewport
        pm = self.view.grab()
        if self.screenshot_dir:
            os.makedirs(self.screenshot_dir, exist_ok=True)
            path = os.path.join(self.screenshot_dir, name)
            pm.save(path, "PNG")
            self._shots.append(path)
        return pm

    def resize_window(self, w, h):
        self.view.resize(w, h)
        self.view.viewport().resize(w, h)
        self.view._apply()

    def _clear(self):
        for it in list(self.scene.items()):
            self.scene.removeItem(it)
        self.panels = []
        self.flash = None
        self.view.set_pose(0)

    # ---- boot sequence ----
    def boot(self, frames=3):
        games = [("a+1", "A+ CORE 1", "220-1101"),
                 ("a+2", "A+ CORE 2", "220-1102"),
                 ("net", "NETWORK+", "N10-009"),
                 ("sec", "SECURITY+", "SY0-701")]
        for i, (game, name, code) in enumerate(games):
            count = sum(1 for q in self.bundle["questions"] if q["game"] == game)
            self._clear()
            draw_title(self.scene, "TERMINUS BOOT", 60, GREEN, 26)
            draw_line(self.scene, "loading kernels...", 110, GREEN_DIM, 12)
            for j, (g2, n2, c2) in enumerate(games):
                mark = "[ loaded ]" if j < i else "[        ]"
                color = GREEN if j < i else GREEN_DIM
                if j == i:
                    mark = "[ loading ]"
                    color = AMBER
                draw_line(self.scene, "%-6s %-10s %-8s %s" % (g2.upper(), n2, c2, mark),
                          150 + j * 44, color, 13)
            draw_line(self.scene, "bank: %d questions for %s" % (count, name),
                      350, GREEN, 13)
            self.view.set_pose(6 * i)
            self.snap("01-boot-%d-%s.png" % (i, game))
        self._clear()
        draw_title(self.scene, "TERMINUS", 300, GREEN, 40)
        draw_line(self.scene, "ACCESS GRANTED", 370, GREEN, 22)
        draw_line(self.scene, "a+1=%d  a+2=%d  net=%d  sec=%d"
                  % tuple(sum(1 for q in self.bundle["questions"]
                              if q["game"] == g) for g in tb.GAMES), 420, GREEN_DIM, 13)
        self.view.set_pose(0)
        self.snap("01-boot-access-granted.png")

    # ---- question screen ----
    def show_question(self, q):
        self._clear()
        self.current = q
        draw_title(self.scene, self.mode_title, 30, GREEN, 18)
        draw_line(self.scene, "Q%d/%d  [%s]  %s"
                  % (self.q_index + 1, len(self.questions),
                     tb.GAME_TAGS[q["game"]], q["difficulty"]), 70, GREEN_DIM, 11)
        if self.mode == "breach":
            self._countdown_item = draw_line(
                self.scene, "TIME LEFT %d:%02d"
                % divmod(self._breach_remaining, 60), 96, AMBER, 13)
        else:
            self._countdown_item = None
        # question text as a wrapped title
        words = q["question"].split()
        lines, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if len(trial) <= 64:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        for n, line in enumerate(lines):
            draw_line(self.scene, line, 120 + n * 26, QColor("#d0ffd8"), 13)

        # option panels, arranged in a slight arc for the 3D feel
        keys = "abcd"
        opts = list(q["options"])
        order = list(range(len(opts)))
        self.rng.shuffle(order)
        self._correct_key = None
        for k, oi in zip(keys, order):
            p = Panel(opts[oi], w=520, h=56, key_label=k.upper())
            if oi == q["answer"]:
                self._correct_key = k
            p.setPos(WIDTH / 2 - 260, 250 + order.index(oi) * 68)
            self.scene.addItem(p)
            self.panels.append((k, p))

        # breach / streak bar
        bar = QGraphicsRectItem(QRectF(WIDTH / 2 - 200, HEIGHT - 70, 400, 18))
        bar.setPen(QPen(GREEN_DIM, 1))
        bar.setBrush(QBrush(GREEN_DARK))
        self.scene.addItem(bar)
        fill = QGraphicsRectItem(QRectF(WIDTH / 2 - 200, HEIGHT - 70, 0, 18))
        fill.setPen(Qt.NoPen)
        fill.setBrush(QBrush(GREEN))
        fill.setRect(QRectF(WIDTH / 2 - 200, HEIGHT - 70,
                            400 * (self.stats["current_streak"] % 10) / 10.0, 18))
        self.scene.addItem(fill)
        draw_line(self.scene, "BREACH %d/10" % (self.stats["current_streak"] % 10),
                  HEIGHT - 48, GREEN_DIM, 11)

        self.overlay = ScanlineOverlay(WIDTH, HEIGHT)
        self.scene.addItem(self.overlay)
        self.view.set_pose(8)
        self.snap("02-question.png")

    def _on_click(self, panel):
        if self._pick_state == "game":
            for gkey, p in self._game_panels:
                if p is panel:
                    self._choose_game(gkey)
                    return
        for k, p in self.panels:
            if p is panel:
                self.answer(k)
                return

    def answer(self, key):
        ok = (key == self._correct_key)
        q = self.current
        if ok:
            tb.record_mc(self.stats, q, True)
        else:
            tb.record_mc(self.stats, q, False)
        tb.evaluate_achievements(self.stats)
        self.save_stats()   # per-profile write-through
        if self.mode is not None:
            self._round_results.append((q, ok))
            if self.mode == "breach":
                # full breach: no per-question feedback until the end
                self._advance_mode()
            else:
                self._flash_feedback(ok)
                self._schedule_mode_advance()
        else:
            self._flash_feedback(ok)
        return ok

    def _flash_feedback(self, ok):
        self._clear()
        label = "ACCESS GRANTED" if ok else "TRACE DETECTED"
        color = GREEN if ok else RED
        draw_title(self.scene, label, 340, color, 36)
        correct_text = self.current["answer_text"]
        draw_line(self.scene, "answer: %s" % correct_text, 420, GREEN_DIM, 13)
        if not ok:
            draw_line(self.scene, "your breach was logged", 450, RED, 12)
        self.overlay = ScanlineOverlay(WIDTH, HEIGHT)
        self.scene.addItem(self.overlay)
        self.snap("03-%s.png" % ("granted" if ok else "detected"))

    # ---- achievements screen ----
    def show_achievements(self):
        self._clear()
        draw_title(self.scene, "ACHIEVEMENTS", 30, GREEN, 22)
        y = 90
        for a in ach.ACHIEVEMENTS:
            earned = a["id"] in self.stats["achievements"]
            color = GREEN if earned else GREEN_DIM
            draw_line(self.scene, "%s %s" % (a["glyph"], a["name"]), y, color, 14)
            draw_line(self.scene, "    %s" % a["line"], y + 20, GREEN_DIM, 10)
            y += 52
        self.overlay = ScanlineOverlay(WIDTH, HEIGHT)
        self.scene.addItem(self.overlay)
        self.snap("04-achievements.png")

    # ---- mode engine (feature 2) --------------------------------------
    def _wrap(self, text, width):
        words = str(text).split()
        lines, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if len(trial) <= width:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines or [""]

    def set_mode_done_callback(self, fn):
        self._mode_done_cb = fn

    def launch(self, mode_key):
        """Enter a game mode from the desktop hub."""
        self.mode = mode_key
        self._drill_active = False
        self._buffer = ""
        self._round_results = []
        self._pick_state = None
        self._game_panels = []
        if mode_key == "daily":
            day = datetime.date.today()
            self.questions = tb.select_daily(self.bundle["questions"],
                                             tb.daily_rng(day), 10)
            self.mode_title = "DAILY UPLINK"
            self.q_index = 0
            self._next()
        elif mode_key == "sector":
            self._pick_target = "sector"
            self._show_game_picker("SECTOR SWEEP")
        elif mode_key == "breach":
            self._pick_target = "breach"
            self._show_game_picker("FULL BREACH")
        elif mode_key == "recover":
            self._start_recover()
        elif mode_key in ("cipher", "ports", "crypto"):
            self._start_drill(mode_key)
        elif mode_key == "player":
            self._show_player_file()

    def _next(self):
        if self._drill_active:
            if self._drill_index < len(self._drill_pool):
                self.show_drill_question(self._drill_pool[self._drill_index])
            else:
                self._end_mode()
        else:
            if self.q_index < len(self.questions):
                self.show_question(self.questions[self.q_index])
            else:
                self._end_mode()

    def _advance_mode(self):
        if self._advance_timer is not None:
            self._advance_timer.stop()
        if not self._drill_active:
            self.q_index += 1
        self._next()

    def _schedule_mode_advance(self):
        if self._advance_timer is None:
            self._advance_timer = QTimer()
            self._advance_timer.setSingleShot(True)
            self._advance_timer.timeout.connect(self._advance_mode)
        self._advance_timer.start(1100)

    def _schedule_return(self, ms=2500):
        if self._return_timer is None:
            self._return_timer = QTimer()
            self._return_timer.setSingleShot(True)
            self._return_timer.timeout.connect(self._finish_return)
        self._return_timer.start(ms)

    def _finish_return(self):
        self.mode = None
        self._drill_active = False
        if self._mode_done_cb:
            self._mode_done_cb()

    def _stop_timers(self):
        for t in (self._breach_timer, self._advance_timer, self._return_timer):
            if t is not None:
                t.stop()

    def _end_mode(self):
        self._stop_timers()
        self.scene.on_key = None
        was = self.mode
        self.mode = None
        self._drill_active = False
        if was == "breach":
            self._show_breach_summary()
            self._schedule_return()
        elif self._mode_done_cb:
            self._mode_done_cb()

    # -- game picker -----------------------------------------------------
    def _show_game_picker(self, title):
        self._clear()
        draw_title(self.scene, title, 60, GREEN, 24)
        draw_line(self.scene, "select a target cert:", 130, GREEN_DIM, 13)
        self._pick_state = "game"
        self._game_panels = []
        for i, (name, gkey) in enumerate(tb.GAME_CHOICES):
            p = Panel("%-24s %s" % (name, tb.GAME_TAGS[gkey]), w=520, h=56,
                      key_label=str(i + 1))
            p.setPos(WIDTH / 2 - 260, 200 + i * 68)
            self.scene.addItem(p)
            self._game_panels.append((gkey, p))
        self.overlay = ScanlineOverlay(WIDTH, HEIGHT)
        self.scene.addItem(self.overlay)
        self.view.set_pose(6)
        self.snap("05-pick-game.png")

    def _choose_game(self, gkey):
        self._pick_state = None
        self._game_panels = []
        if self._pick_target == "sector":
            pool = tb.filter_game(self.bundle, gkey)
            self.questions = self.rng.sample(pool, min(10, len(pool)))
            self.mode_title = "SECTOR SWEEP"
            self.mode = "sector"
            self.q_index = 0
            self._next()
        elif self._pick_target == "breach":
            pool = tb.filter_game(self.bundle, gkey)
            self.questions = self.rng.sample(pool, min(50, len(pool)))
            self.mode_title = "FULL BREACH"
            self.mode = "breach"
            self.q_index = 0
            self._breach_remaining = 20 * 60
            self._round_results = []
            self._start_breach_timer()
            self._next()

    def _start_breach_timer(self):
        if self._breach_timer is None:
            self._breach_timer = QTimer()
            self._breach_timer.timeout.connect(self._breach_tick)
        self._breach_timer.start(1000)

    def _breach_tick(self):
        self._breach_remaining -= 1
        if self._breach_remaining <= 0:
            self._breach_timer.stop()
            self._end_mode()
        elif self._countdown_item is not None:
            self._countdown_item.setText(
                "TIME LEFT %d:%02d" % divmod(self._breach_remaining, 60))

    # -- recover log -----------------------------------------------------
    def _start_recover(self):
        qs = tb.select_review(self.bundle, self.stats)
        self.mode = "recover"
        self.mode_title = "RECOVER LOG"
        self.q_index = 0
        if not qs:
            self._clear()
            draw_title(self.scene, "RECOVER LOG", 60, GREEN, 24)
            draw_line(self.scene, "nothing to recover - no missed questions yet",
                      200, GREEN_DIM, 13)
            self.overlay = ScanlineOverlay(WIDTH, HEIGHT)
            self.scene.addItem(self.overlay)
            self.snap("07-recover-empty.png")
            self._schedule_return()
            return
        self.questions = qs
        self._next()

    # -- drill modes (free-text entry) -----------------------------------
    def _start_drill(self, mode_key):
        kind = {"cipher": "acronyms", "ports": "ports", "crypto": "crypto"}[mode_key]
        table = self.bundle["drill_tables"][kind]
        if kind == "acronyms":
            table = tb.filter_scope(table, None)
        items = tb.build_drill_items(table, kind)
        items = tb.select_drill_items(items, self.rng, 10)
        self._drill_pool = items
        self._drill_index = 0
        self._drill_kind = kind
        self._drill_active = True
        self.mode_title = tb.KIND_TITLES[kind]
        self._buffer = ""
        self._last_feedback = None
        self._next()

    def show_drill_question(self, item):
        self._drill_current = item
        self._buffer = ""
        self._last_feedback = None
        self._redraw_drill()

    def _redraw_drill(self):
        item = self._drill_current
        self._clear()
        draw_title(self.scene, self.mode_title, 40, GREEN, 22)
        draw_line(self.scene, "%d/%d  [%s]"
                  % (self._drill_index + 1, len(self._drill_pool),
                     tb.direction_label(item["direction"])), 90, GREEN_DIM, 11)
        y = 150
        for ln in self._wrap(item["prompt"], 64):
            draw_line(self.scene, ln, y, QColor("#d0ffd8"), 13)
            y += 26
        y += 14
        draw_line(self.scene, ">", y, GREEN_DIM, 13, x=140)
        shown = self._buffer + "\u2588"
        draw_line(self.scene, shown, y, GREEN, 13, x=170)
        if self._last_feedback:
            draw_line(self.scene, self._last_feedback, y + 30, self._fb_color,
                      12, x=170)
        self.overlay = ScanlineOverlay(WIDTH, HEIGHT)
        self.scene.addItem(self.overlay)
        self.scene.on_key = self._drill_key

    def _drill_key(self, event):
        k = event.key()
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self._drill_submit()
            return True
        if k == Qt.Key_Backspace:
            self._buffer = self._buffer[:-1]
            self._redraw_drill()
            return True
        if k == Qt.Key_Escape:
            self._end_mode()
            return True
        text = event.text()
        if text and text.isprintable():
            self._buffer = (self._buffer + text)[-60:]
            self._redraw_drill()
            return True
        return False

    def _drill_submit(self):
        item = self._drill_current
        raw = self._buffer
        self._buffer = ""
        if raw.strip().lower() in ("q", "quit"):
            self._end_mode()
            return
        correct, correct_answer, _, _ = tb.grade_drill(item, raw)
        tb.record_drill(self.stats, self._drill_kind, item["id"], correct)
        tb.evaluate_achievements(self.stats)
        self.save_stats()
        self._round_results.append((item, correct))
        self._last_feedback = ("ACCESS GRANTED" if correct
                               else "TRACE DETECTED  ->  %s" % correct_answer)
        self._fb_color = GREEN if correct else RED
        self._redraw_drill()
        self._drill_index += 1
        self._schedule_mode_advance()

    # -- player file -----------------------------------------------------
    def _show_player_file(self):
        self.mode = "player"
        self._clear()
        draw_title(self.scene, "PLAYER FILE", 30, GREEN, 22)
        lt = self.stats["lifetime"]
        tot, corr = lt["total"], lt["correct"]
        acc = (100.0 * corr / tot) if tot else 0.0
        y = 90
        for r in ("lifetime accuracy  : %d/%d  (%.1f%%)" % (corr, tot, acc),
                  "current streak    : %d" % self.stats["current_streak"],
                  "best streak       : %d" % self.stats["best_streak"],
                  "missed questions  : %d" % len(self.stats["missed_ids"])):
            draw_line(self.scene, r, y, GREEN_DIM, 13, x=140)
            y += 28
        y += 8
        draw_line(self.scene, "per-domain accuracy:", y, GREEN, 13, x=140)
        y += 26
        for d in tb.DOMAIN_KEYS:
            b = self.stats["per_domain"][d]
            draw_line(self.scene, "  %-10s %d/%d"
                      % (tb.DOMAIN_NAMES[d], b["correct"], b["total"]),
                      y, GREEN_DIM, 12, x=140)
            y += 24
        y += 8
        for key, label in (("ports", "Port Scanner"), ("acronyms", "Cipher Grind"),
                           ("crypto", "Crypto Vault")):
            b = self.stats[key]
            draw_line(self.scene, "  %-14s %d/%d" % (label, b["correct"], b["total"]),
                      y, GREEN_DIM, 12, x=140)
            y += 24
        y += 8
        draw_line(self.scene, "achievements: %d unlocked"
                  % len(self.stats["achievements"]), y, GREEN, 13, x=140)
        y += 26
        for aid in self.stats["achievements"]:
            for a in ach.ACHIEVEMENTS:
                if a["id"] == aid:
                    draw_line(self.scene, "  %s %s" % (a["glyph"], a["name"]),
                              y, GREEN_DIM, 12, x=140)
                    y += 22
        self.overlay = ScanlineOverlay(WIDTH, HEIGHT)
        self.scene.addItem(self.overlay)
        self.scene.on_key = self._player_key
        self.snap("08-player-file.png")

    def _player_key(self, event):
        k = event.key()
        if k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape, Qt.Key_Backspace):
            self._end_mode()
            return True
        return False

    # -- breach summary --------------------------------------------------
    def _show_breach_summary(self):
        self._clear()
        total = len(self._round_results)
        score = sum(1 for _, ok in self._round_results if ok)
        draw_title(self.scene, "FULL BREACH RESULTS", 60, GREEN, 24)
        draw_line(self.scene, "score: %d/%d  (%.0f%%)"
                  % (score, total, 100.0 * score / total if total else 0.0),
                  180, GREEN, 16)
        y = 240
        for q, ok in self._round_results:
            if ok:
                continue
            draw_line(self.scene, "- %s" % q["question"][:58], y, RED, 12, x=140)
            draw_line(self.scene, "  -> %s" % q["answer_text"], y + 20,
                      GREEN_DIM, 11, x=160)
            y += 44
        self.overlay = ScanlineOverlay(WIDTH, HEIGHT)
        self.scene.addItem(self.overlay)
        self.snap("09-breach-results.png")

    # ---- selftest driver ----
    def selftest(self):
        # the Lair leg runs first (scripted room walk + login/persist checks),
        # then the original quiz leg below — both exit 0 or raise.
        lair.run_lair_selftest(lair.LairStore(self._profile_path()),
                               self.screenshot_dir, self._shots)
        self.boot()
        # answer the first question wrong, then two right, to exercise both
        # flashes and the streak bar
        self.q_index = 0
        self.show_question(self.questions[0])
        self.answer(next(k for k, _p in self.panels if k != self._correct_key))
        self.q_index = 1
        self.show_question(self.questions[1])
        self.answer(self._correct_key)
        self.q_index = 2
        self.show_question(self.questions[2])
        self.answer(self._correct_key)
        self.show_achievements()
        run_desktop_selftest(self.bundle, self.screenshot_dir, self._shots)
        return self._shots


def run_desktop_selftest(bundle, screenshot_dir=None, shots=None):
    """Scripted desktop-hub leg: login-with-CRT -> desktop -> daily -> drill
    -> player file -> logout. Uses a temp profile store, never the real one."""
    import shutil
    import tempfile
    out = []

    def check(name, cond):
        out.append(name)
        if not cond:
            raise SystemExit("DESKTOP SELF-TEST FAILED: %s" % name)

    tmp = tempfile.mkdtemp(prefix="terminus-desktop-")
    live = lair.LairStore(os.path.join(tmp, "profiles.json"))
    live.create("selftest_user", "pw1234")

    g = Game(bundle, seed=42, screenshot_dir=screenshot_dir,
             profile_user="selftest_user", store=live)
    shell = lair.Lair(live, screenshot_dir)
    desk = desktop.Desktop(live, screenshot_dir)
    seen = {}

    def enter_desktop(username):
        g.profile_user = username
        g.store = live
        g.stats = live.get_stats(username)
        shell.view.hide()
        desk.show(username)
        seen["desktop"] = True

    def launch_mode(m):
        desk.hide()
        g.view.show()
        g.launch(m)

    def return_to_desktop():
        g.save_stats()
        g.view.hide()
        if g.profile_user:
            desk.show(g.profile_user)

    def logout():
        g.save_stats()
        g.view.hide()
        desk.hide()
        shell.start(g.profile_user)
        seen["room"] = True

    shell.on_login = enter_desktop
    desk.on_launch = launch_mode
    desk.on_logout = logout
    desk.on_poweroff = lambda: None
    g.set_mode_done_callback(return_to_desktop)

    # 1. login with CRT overlay present
    shell.room.build(username="selftest_user")
    shell.view.setScene(shell.room)
    shell.open_login()
    check("login CRT overlay + bezel present",
          shell.login.crt is not None and shell.login.bezel is not None)
    shell.login.buffer = "selftest_user"
    shell.login.blink = True
    shell.login._redraw()
    if screenshot_dir:
        shots.append(shell.login_snap("10-login-crt.png"))
    shell.login.submit_username()
    shell.login.buffer = "pw1234"
    shell.login.event_submit()
    check("login -> ACCESS GRANTED", shell.login.result == "selftest_user")
    shell._handoff()
    check("post-login shows the desktop", seen.get("desktop") is True
          and desk.scene is not None)
    check("desktop renders 8 icons", len(desk.scene.icons) == 8)
    check("desktop taskbar + clock render", desk.scene.taskbar is not None
          and desk.scene.clock_item is not None
          and desk.scene.user_item is not None)
    check("desktop CRT overlay + bezel present",
          desk.scene.crt is not None and desk.scene.bezel is not None)
    if screenshot_dir:
        shots.append(desk.snap("11-desktop.png"))

    # 3. launch Daily Uplink + answer one question
    launch_mode("daily")
    check("daily uplink shows a question", g.mode == "daily"
          and g.current is not None)
    check("daily answer graded + recorded",
          g.answer(g._correct_key) is True
          and g.stats["lifetime"]["total"] >= 1)
    g._advance_mode()

    # 4. drill mode (Port Scanner) with typed input
    launch_mode("ports")
    check("drill shows a prompt", g._drill_active
          and g._drill_current is not None)
    g._buffer = g._drill_current["answer"]
    g._drill_submit()
    check("drill typed answer graded correct",
          g._round_results and g._round_results[-1][1] is True)
    check("drill recorded to profile stats", g.stats["ports"]["total"] >= 1)
    if screenshot_dir:
        g._drill_index = 0
        g.show_drill_question(g._drill_pool[0])
        g.snap("12-drill.png")
        shots.append(os.path.join(screenshot_dir, "12-drill.png"))
    g._end_mode()

    # 5. Player File
    launch_mode("player")
    check("player file shown", g.mode == "player")
    if screenshot_dir:
        g.snap("13-player-file.png")
        shots.append(os.path.join(screenshot_dir, "13-player-file.png"))
    g._end_mode()

    # 6. log out back to the room
    logout()
    check("logout returns to the room", seen.get("room") is True
          and shell.view.scene() is shell.room)

    print("DESKTOP: login-CRT -> desktop -> daily -> drill -> player file "
          "-> logout, all green")
    for line in out:
        print("  desktop check ok: %s" % line)
    shutil.rmtree(tmp, ignore_errors=True)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="TERMINUS 3D terminal-lure quiz")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--screenshot", default=None, metavar="DIR")
    ap.add_argument("--size", default=None, metavar="WxH",
                    help="window size to render screenshots at (e.g. 560x420)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--bundle", default=tb.DEFAULT_BUNDLE)
    ap.add_argument("--stats", default=None)
    ap.add_argument("--profile-file", dest="profile_file", default=None,
                    metavar="PATH",
                    help="profiles.json path (default: user data dir, see "
                         "terminus.default_data_dir)")
    args = ap.parse_args(argv)

    app = QApplication.instance() or QApplication(sys.argv)

    bundle = tb.load_bundle(args.bundle)

    if args.profile_file:
        os.environ["TERMINUS_PROFILES"] = args.profile_file

    game = Game(bundle, seed=args.seed, screenshot_dir=args.screenshot,
                given_profile_path=args.profile_file)

    if args.size:
        try:
            w, h = (int(x) for x in args.size.lower().split("x"))
        except ValueError:
            print("invalid --size %r (expected WxH)" % args.size, file=sys.stderr)
            return 2
        game.resize_window(w, h)

    if args.selftest:
        shots = game.selftest()
        for s in shots:
            print("screenshot: %s" % s)
        print("GUI SELF-TEST COMPLETE")
        return 0

    # interactive: the Lair room first; after ACCESS GRANTED the desktop hub
    # shows. Modes open in the game view and return to the desktop on close.
    store = lair.LairStore(game._profile_path())
    desk = desktop.Desktop(store, args.screenshot)

    def enter_desktop(username):
        game.profile_user = username
        game.store = store
        game.stats = store.get_stats(username)
        shell.view.hide()
        desk.show(username)

    def launch_mode(mode_key):
        desk.hide()
        game.view.show()
        game.launch(mode_key)

    def return_to_desktop():
        game.save_stats()
        game.view.hide()
        if getattr(game, "profile_user", None):
            desk.show(game.profile_user)

    def logout_to_room():
        """Logging out returns to the room (stats stay persisted per user)."""
        game.save_stats()
        game.view.hide()
        desk.hide()
        shell.start(getattr(game, "profile_user", None))

    game.set_mode_done_callback(return_to_desktop)
    desk.on_launch = launch_mode
    desk.on_logout = logout_to_room
    desk.on_poweroff = lambda: app.quit()

    shell = lair.Lair(store, args.screenshot, on_login=enter_desktop)
    if args.size:
        w, h = (int(x) for x in args.size.lower().split("x"))
        shell.resize(w, h)
        game.resize_window(w, h)
        desk.resize(w, h)
    shell.start()

    # animate rotation
    timer = QTimer()
    timer.timeout.connect(game.view.rotate_step)
    timer.start(40)

    # NOTE: do NOT show game.view here. The Lair shell is the single visible
    # window until login; enter_game() shows the game view and hides the shell.
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
