#!/usr/bin/env python3
"""
traceback_gui.py — TRACEBACK 3D terminal-lure edition.

PySide6 / Qt6. All game logic lives in traceback.py, so this GUI and
traceback_cli.py share one code path and cannot drift.

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
    QT_QPA_PLATFORM=offscreen /usr/bin/python3 traceback_gui.py \
        --selftest --screenshot /home/ojinguh/traceback/preview
"""

import argparse
import datetime
import os
import random
import sys

import traceback as tb
import achievements as ach

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

    def __init__(self, bundle, seed=None, screenshot_dir=None):
        self.bundle = bundle
        self.rng = random.Random(seed if seed is not None else 42)
        self.screenshot_dir = screenshot_dir
        self.scene = Scene()
        self.view = TermView(self.scene)
        self.view.resize(WIDTH, HEIGHT)
        self.scene.setSceneRect(0, 0, WIDTH, HEIGHT)
        self.scene.set_click_handler(self._on_click)
        self.stats = tb.empty_stats()
        self.q_index = 0
        self.questions = tb.select_daily(bundle["questions"], self.rng, 10)
        self.current = None
        self.panels = []
        self.flash = None
        self._shots = []
        self._breach_timer = None

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
            draw_title(self.scene, "TRACEBACK BOOT", 60, GREEN, 26)
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
        draw_title(self.scene, "TRACEBACK", 300, GREEN, 40)
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
        draw_title(self.scene, "DAILY UPLINK", 30, GREEN, 18)
        draw_line(self.scene, "Q%d/%d  [%s]  %s"
                  % (self.q_index + 1, len(self.questions),
                     tb.GAME_TAGS[q["game"]], q["difficulty"]), 70, GREEN_DIM, 11)
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
        for k, p in self.panels:
            if p is panel:
                self.answer(k)
                return

    def answer(self, key):
        ok = (key == self._correct_key)
        self._flash_feedback(ok)
        q = self.current
        if ok:
            tb.record_mc(self.stats, q, True)
        else:
            tb.record_mc(self.stats, q, False)
        tb.evaluate_achievements(self.stats)

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

    # ---- selftest driver ----
    def selftest(self):
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
        return self._shots


def main(argv=None):
    ap = argparse.ArgumentParser(description="TRACEBACK 3D terminal-lure quiz")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--screenshot", default=None, metavar="DIR")
    ap.add_argument("--size", default=None, metavar="WxH",
                    help="window size to render screenshots at (e.g. 560x420)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--bundle", default=tb.DEFAULT_BUNDLE)
    ap.add_argument("--stats", default=None)
    args = ap.parse_args(argv)

    app = QApplication.instance() or QApplication(sys.argv)

    bundle = tb.load_bundle(args.bundle)

    game = Game(bundle, seed=args.seed, screenshot_dir=args.screenshot)

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

    # interactive: show the first question and run the loop
    game.boot()
    game.q_index = 0
    game.show_question(game.questions[0])

    # animate rotation
    timer = QTimer()
    timer.timeout.connect(game.view.rotate_step)
    timer.start(40)

    game.view.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
