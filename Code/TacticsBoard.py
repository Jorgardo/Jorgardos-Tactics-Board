#!/usr/bin/env python3
"""
Tactics Board – football tactics board for Linux and Windows
=============================================================
* Dark board with orange lines, white / orange player discs
* Views: top-down, 3D perspective, behind the goal, freely rotatable and tiltable
* 33 formations as a starting point (selectable separately for each team)
* Players and ball can be moved freely – with magnet (alignment, spacing, zone centers)
* Player numbers and names are editable (double-click, right-click or sidebar)
* Overlays: 20 zones (Guardiola) and the 3 thirds
* Team lines (defense / midfield / attack) at the push of a button
* Pass network: connects teammates with a free passing lane (View menu)
* Zones, run and pass paths, text notes on the board + notepad
* Animations: record scenes, play them back, timeline, show run paths
* Save / load (JSON) and export as PNG

Controls: Magnet, Animation, 2D/3D, Perspective and Menu float as individual switches at the top of the
board. At the bottom floats the collapsible "Draw / Zones" bar; the animation player only appears when needed.

Start:  python3 taktiktafel.py
"""
import json
import math
import os
import re
import sys

from PySide6.QtCore import (QAbstractAnimation, QParallelAnimationGroup, QEasingCurve, QEvent, QLineF, QPoint,
                            QPointF, QPropertyAnimation, QRect, QRectF, Qt, QTimer,
                            QSize, QVariantAnimation, Signal)
from PySide6.QtGui import (QAction, QActionGroup, QColor, QFont, QFontMetricsF, QIcon, QKeySequence, QPixmap,
                           QLinearGradient, QPainter, QPainterPath,
                           QPainterPathStroker, QPen, QPolygonF,
                           QRadialGradient, QShortcut, QTransform)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDoubleSpinBox, QFileDialog, QGraphicsItem,
                               QGraphicsOpacityEffect, QGraphicsPathItem, QGraphicsScene,
                               QGraphicsView,
                               QGridLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QSlider, QSpinBox, QStyle,
                               QFrame, QSizePolicy, QStackedWidget, QTextBrowser,
                               QToolBar, QToolButton, QVBoxLayout, QWidget,
                               QWidgetAction) 


# --------------------------------------------------------------------------
# Dimensions & colors (1 m = 10 scene units)
# --------------------------------------------------------------------------
PL, PW = 1050.0, 680.0          # pitch length / width
HL, HW = PL / 2, PW / 2
BOARD = QRectF(-590, -400, 1180, 800)

ORANGE = "#f08a2c"
WHITE = "#f4f3ef"
BOARD_TOP = "#222a36"
BOARD_BOTTOM = "#171c25"
PERSPECTIVE_DISTANCE = 1500.0
DRAWER_W = 480
RAIL_W = 106
DRAW_COLORS = ["#f08a2c", "#f4f3ef", "#45c4e8", "#ee4b4b", "#6fd36f", "#f2d13b"]

TEXT_FLAGS = int(Qt.AlignmentFlag.AlignLeft.value) | int(Qt.TextFlag.TextWordWrap.value)


PERF = {"on": False}   # performance mode: opaque menus, no animations

FILE_VERSION = 2
DRAW_KINDS = ("rect", "ellipse", "free", "run", "pass", "text")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_pt(v):
    return isinstance(v, (list, tuple)) and len(v) == 2 and _num(v[0]) and _num(v[1])


def _is_pts(v):
    return isinstance(v, (list, tuple)) and len(v) >= 1 and all(_is_pt(q) for q in v)


def _is_color(v):
    return isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v) is not None


def validate_tactic(data, pids):
    """Validates a loaded tactics file BEFORE anything on the board is changed. Raises ValueError."""
    if not isinstance(data, dict):
        raise ValueError("The file does not contain a tactic.")
    ver = data.get("version", 1)
    if not isinstance(ver, int) or ver > FILE_VERSION:
        raise ValueError(f"The file comes from a newer program version (format {ver}).")
    players = data.get("players")
    if not isinstance(players, dict):
        raise ValueError("The player list is missing.")
    for pid, v in players.items():
        if pid not in pids:
            continue
        if not (isinstance(v, dict) and _num(v.get("x")) and _num(v.get("y")) and _num(v.get("n"))):
            raise ValueError(f"Invalid data for player {pid}.")
        if not isinstance(v.get("name", ""), str):
            raise ValueError(f"Invalid name for player {pid}.")
    if not _is_pt(data.get("ball")):
        raise ValueError("The ball position is missing or invalid.")
    scenes = data.get("scenes", [])
    if not isinstance(scenes, list):
        raise ValueError("The scene list is invalid.")
    for i, s in enumerate(scenes, 1):
        if not (isinstance(s, dict) and isinstance(s.get("p"), dict) and _is_pt(s.get("b"))):
            raise ValueError(f"Scene {i} is corrupted.")
        for pid in pids:
            if not _is_pt(s["p"].get(pid)):
                raise ValueError(f"Scene {i}: position of {pid} is missing.")
        dd = s.get("d")
        if dd is not None:
            if not isinstance(dd, dict):
                raise ValueError(f"Scene {i}: drawing data is invalid.")
            for key, e in dd.items():
                if not (str(key).isdigit() and isinstance(e, dict) and e.get("k") in DRAW_KINDS
                        and _is_color(e.get("c")) and _is_pts(e.get("pts")) and _is_pt(e.get("pos"))):
                    raise ValueError(f"Scene {i}: drawing {key} is corrupted.")
    drawings = data.get("drawings", [])
    if not isinstance(drawings, list):
        raise ValueError("The drawing list is invalid.")
    for n, d in enumerate(drawings, 1):
        if not (isinstance(d, dict) and d.get("kind") in DRAW_KINDS and _is_color(d.get("color"))
                and _is_pts(d.get("points")) and _is_pt(d.get("pos", [0, 0]))
                and isinstance(d.get("text", ""), str)
                and (d.get("uid") is None or (isinstance(d.get("uid"), int) and d.get("uid") >= 0))):
            raise ValueError(f"Drawing {n} is corrupted.")
    view = data.get("view")
    if view is not None and not (isinstance(view, (list, tuple)) and len(view) == 3 and all(_num(x) for x in view)):
        raise ValueError("The view data is invalid.")
    fm = data.get("formations")
    if fm is not None and not (isinstance(fm, (list, tuple)) and len(fm) == 2):
        raise ValueError("The formation data is invalid.")
    if not _num(data.get("step", 1.5)):
        raise ValueError("The step duration is invalid.")
    if not isinstance(data.get("notes", ""), str):
        raise ValueError("The notes are invalid.")


class Style:
    """Global rendering mode: flat=True is the lightweight 2D mode."""
    flat = False
    tilt = 0.0     # current tilt of the view (degrees) – controls shadow and thickness


def tilt_k():
    """0 = straight from above (almost no shadow, no visible thickness), 1 = strongly tilted."""
    return max(0.0, min(1.0, Style.tilt / 45.0))


def _icon_pixmap(name, color, size=64):
    """Draws a simple vector icon (instead of emoji / symbol characters)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = QColor(color)
    S, R = Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap
    J = Qt.PenJoinStyle.RoundJoin
    thick = QPen(col, 5.5, S, R, J)
    thin = QPen(col, 2.5, S, R, J)
    none = Qt.BrushStyle.NoBrush
    P = QPointF
    p.setPen(thick)
    p.setBrush(none)

    def poly(pts):
        return QPolygonF([P(x, y) for x, y in pts])

    def filled(pts):
        p.setPen(thin)
        p.setBrush(col)
        p.drawPolygon(poly(pts))
        p.setBrush(none)
        p.setPen(thick)

    def arrow(a, b, head=17):
        ang = math.atan2(b.y() - a.y(), b.x() - a.x())
        base = P(b.x() - math.cos(ang) * head * 0.6, b.y() - math.sin(ang) * head * 0.6)
        p.drawLine(a, base)
        left = P(b.x() - math.cos(ang - 0.5) * head, b.y() - math.sin(ang - 0.5) * head)
        right = P(b.x() - math.cos(ang + 0.5) * head, b.y() - math.sin(ang + 0.5) * head)
        pen = p.pen()
        p.setPen(thin)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([b, left, right]))
        p.setBrush(none)
        p.setPen(pen)

    if name == "cursor":
        filled([(16, 7), (16, 51), (27, 41), (35, 58), (43, 54), (35, 38), (50, 37)])
    elif name == "rect":
        p.drawRoundedRect(QRectF(9, 15, 46, 34), 6, 6)
    elif name == "ellipse":
        p.drawEllipse(QRectF(8, 14, 48, 36))
    elif name == "free":
        path = QPainterPath(P(14, 42))
        path.cubicTo(P(4, 20), P(26, 8), P(40, 16))
        path.cubicTo(P(58, 26), P(54, 50), P(34, 52))
        path.cubicTo(P(26, 53), P(20, 48), P(14, 42))
        p.drawPath(path)
    elif name == "run":
        dash = QPen(thick)
        dash.setDashPattern([1.3, 1.7])
        dash.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(dash)
        arrow(P(9, 52), P(53, 13))
    elif name == "pass":
        arrow(P(9, 52), P(53, 13))
    elif name == "text":
        p.drawLine(P(14, 15), P(50, 15))
        p.drawLine(P(32, 15), P(32, 52))
    elif name == "undo":
        p.drawArc(QRectF(20, 20, 30, 30), 90 * 16, -180 * 16)
        p.drawLine(P(35, 20), P(24, 20))
        p.drawLine(P(35, 50), P(20, 50))
        filled([(10, 20), (24, 9), (24, 31)])
    elif name == "trash":
        p.drawLine(P(12, 18), P(52, 18))
        p.drawPolyline(poly([(25, 18), (25, 10), (39, 10), (39, 18)]))
        p.drawPolyline(poly([(17, 18), (20, 54), (44, 54), (47, 18)]))
    elif name == "check":
        p.drawPolyline(poly([(12, 34), (26, 48), (53, 16)]))
    elif name == "close":
        p.drawLine(P(16, 16), P(48, 48))
        p.drawLine(P(48, 16), P(16, 48))
    elif name == "menu":
        for y in (17, 32, 47):
            p.drawLine(P(12, y), P(52, y))
    elif name == "play":
        filled([(20, 11), (20, 53), (54, 32)])
    elif name == "pause":
        p.setPen(thin)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(16, 12, 11, 40), 3, 3)
        p.drawRoundedRect(QRectF(37, 12, 11, 40), 3, 3)
    elif name == "first":
        p.setPen(thin)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(12, 12, 7, 40), 3, 3)
        filled([(52, 12), (52, 52), (24, 32)])
    elif name == "prev":
        filled([(32, 14), (32, 50), (9, 32)])
        filled([(55, 14), (55, 50), (32, 32)])
    elif name == "next":
        filled([(32, 14), (32, 50), (55, 32)])
        filled([(9, 14), (9, 50), (32, 32)])
    elif name == "loop":
        p.drawArc(QRectF(12, 17, 40, 30), 40 * 16, 280 * 16)
        t = math.radians(320)
        ex, ey = 32 + 20 * math.cos(t), 32 - 15 * math.sin(t)
        dx, dy = -20 * math.sin(t), -15 * math.cos(t)
        ln = math.hypot(dx, dy)
        dx, dy = dx / ln, dy / ln
        arrow(P(ex - dx, ey - dy), P(ex + dx * 9, ey + dy * 9), 15)
    elif name == "record":
        p.drawEllipse(QRectF(10, 10, 44, 44))
        p.setPen(thin)
        p.setBrush(col)
        p.drawEllipse(QRectF(22, 22, 20, 20))
    elif name == "magnet":
        wide = QPen(col, 9, S, Qt.PenCapStyle.FlatCap, J)
        p.setPen(wide)
        p.drawArc(QRectF(15, 8, 34, 40), 0, 180 * 16)
        p.drawLine(P(15, 28), P(15, 52))
        p.drawLine(P(49, 28), P(49, 52))
    elif name == "persp":
        p.drawPolygon(poly([(6, 49), (58, 49), (46, 15), (18, 15)]))
        p.drawLine(P(32, 15), P(32, 49))
    elif name == "film":
        p.drawRoundedRect(QRectF(8, 13, 48, 38), 8, 8)
        filled([(26, 23), (26, 41), (42, 32)])
    elif name == "pen":
        p.setPen(QPen(col, 9, S, Qt.PenCapStyle.FlatCap, J))
        p.drawLine(P(25, 39), P(46, 18))
        filled([(11, 53), (17, 37), (27, 47)])
    elif name == "grid":
        p.drawRoundedRect(QRectF(10, 12, 44, 40), 5, 5)
        p.setPen(QPen(col, 3.5, S, R, J))
        p.drawLine(P(10, 32), P(54, 32))
        p.drawLine(P(25, 12), P(25, 52))
        p.drawLine(P(39, 12), P(39, 52))
    p.end()
    return pm


def make_icon(name, light="#e9e6e1", dark="#14181e"):
    """Icon with a light normal state and a dark state for activated (orange) switches."""
    icon = QIcon()
    icon.addPixmap(_icon_pixmap(name, light), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_icon_pixmap(name, dark), QIcon.Mode.Normal, QIcon.State.On)
    return icon

# --------------------------------------------------------------------------
# Formations (goalkeeper + lines from back to front)
# --------------------------------------------------------------------------
FORMATIONS = {
    # Back four
    "4-4-2": [1, 4, 4, 2],
    "4-4-2 Diamond": [1, 4, 1, 2, 1, 2],
    "4-4-1-1": [1, 4, 4, 1, 1],
    "4-3-3": [1, 4, 3, 3],
    "4-3-3 Defensive (4-1-2-3)": [1, 4, 1, 2, 3],
    "4-2-3-1": [1, 4, 2, 3, 1],
    "4-2-1-3": [1, 4, 2, 1, 3],
    "4-1-4-1": [1, 4, 1, 4, 1],
    "4-5-1": [1, 4, 5, 1],
    "4-3-2-1 (Christmas Tree)": [1, 4, 3, 2, 1],
    "4-3-1-2": [1, 4, 3, 1, 2],
    "4-2-2-2": [1, 4, 2, 2, 2],
    "4-1-3-2": [1, 4, 1, 3, 2],
    "4-2-4": [1, 4, 2, 4],
    # Back three
    "3-5-2": [1, 3, 5, 2],
    "3-4-3": [1, 3, 4, 3],
    "3-4-2-1": [1, 3, 4, 2, 1],
    "3-4-1-2": [1, 3, 4, 1, 2],
    "3-5-1-1": [1, 3, 5, 1, 1],
    "3-1-4-2": [1, 3, 1, 4, 2],
    "3-3-3-1": [1, 3, 3, 3, 1],
    "3-2-4-1": [1, 3, 2, 4, 1],
    "3-6-1": [1, 3, 6, 1],
    "3-3-4": [1, 3, 3, 4],
    "3-3-1-3": [1, 3, 3, 1, 3],
    # Back five
    "5-3-2": [1, 5, 3, 2],
    "5-4-1": [1, 5, 4, 1],
    "5-2-3": [1, 5, 2, 3],
    "5-2-1-2": [1, 5, 2, 1, 2],
    "5-3-1-1": [1, 5, 3, 1, 1],
    # Classics
    "2-3-5 (Pyramid)": [1, 2, 3, 5],
    "3-2-2-3 (WM)": [1, 3, 2, 2, 3],
    "1-3-3-3 (Catenaccio)": [1, 1, 3, 3, 3],
}
assert all(sum(v) == 11 for v in FORMATIONS.values())

# Formation names from files saved by earlier (German) versions of the program
LEGACY_FORMATION_NAMES = {
    "4-4-2 Raute": "4-4-2 Diamond",
    "4-3-3 Defensiv (4-1-2-3)": "4-3-3 Defensive (4-1-2-3)",
    "4-3-2-1 (Tannenbaum)": "4-3-2-1 (Christmas Tree)",
    "2-3-5 (Pyramide)": "2-3-5 (Pyramid)",
}

LINE_SPAN = {1: 0.0, 2: 0.30, 3: 0.56, 4: 0.70, 5: 0.80, 6: 0.86}


def to_frame(side, x, y):
    """Scene coordinate -> meters in a team's reference frame
    (u = meters from own goal, v = meters from the team's left touchline)."""
    if side > 0:
        return (x + HL) / 10.0, (y + HW) / 10.0
    return (HL - x) / 10.0, (HW - y) / 10.0


def from_frame(side, u, v):
    if side > 0:
        return QPointF(-HL + u * 10.0, -HW + v * 10.0)
    return QPointF(HL - u * 10.0, HW - v * 10.0)


def frect(side, u0, u1, v0, v1):
    return QRectF(from_frame(side, u0, v0), from_frame(side, u1, v1)).normalized()


def formation_positions(lines, side):
    """List of (x, y, line) for 11 players. side=+1: home (left, attacking to the right)."""
    k = len(lines) - 1
    fracs = [0.035] + [0.19 + (0.455 - 0.19) * i / max(k - 1, 1) for i in range(k)]
    result = []
    for li, (frac, n) in enumerate(zip(fracs, lines)):
        span = LINE_SPAN[n] * PW
        for j in range(n):
            y = 0.0 if n == 1 else -span / 2 + span * j / (n - 1)
            if side > 0:
                result.append((-HL + frac * PL, y, li))
            else:
                result.append((HL - frac * PL, -y, li))
    return result


# --------------------------------------------------------------------------
# Zones (Guardiola's 20 zones) and thirds
# --------------------------------------------------------------------------
ROWS = [0, 16.5, 34.5, 52.5, 70.5, 88.5, 105.0]
V_WIDE = 13.84
V_HS = 24.84


def zone_rects(side):
    """[(number, QRectF, kind)] – zones 1-20, numbered from the attacking team's point of view."""
    left = [1, 4, 9, 11, 13, 18]
    right = [3, 8, 10, 12, 17, 20]
    z = []
    for i in range(6):
        z.append((left[i], frect(side, ROWS[i], ROWS[i + 1], 0, V_WIDE), "w"))
        z.append((right[i], frect(side, ROWS[i], ROWS[i + 1], 68 - V_WIDE, 68), "w"))
    z.append((2, frect(side, 0, 16.5, V_WIDE, 68 - V_WIDE), "b"))
    z.append((19, frect(side, 88.5, 105, V_WIDE, 68 - V_WIDE), "b"))
    for base, u0, u1 in ((5, 16.5, 52.5), (14, 52.5, 88.5)):
        z.append((base, frect(side, u0, u1, V_WIDE, V_HS), "h"))
        z.append((base + 1, frect(side, u0, u1, V_HS, 68 - V_HS), "c"))
        z.append((base + 2, frect(side, u0, u1, 68 - V_HS, 68 - V_WIDE), "h"))
    return z


ZONE_KIND = {"w": "Wide", "b": "Central", "h": "Half-space", "c": "Center axis"}


def zone_text(side, n, kind, r):
    c = r.center()
    u, _ = to_frame(side, c.x(), c.y())
    third = "Defensive third" if u < 35 else ("Middle third" if u < 70 else "Attacking third")
    return f"Zone {n}  ·  {ZONE_KIND.get(kind, '')}  ·  {third}"


class OverlayBase(QGraphicsItem):
    def __init__(self, z):
        super().__init__()
        self.side = 1
        self.label_rot = 0.0
        self.setZValue(z)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self):
        return BOARD.adjusted(-40, -40, 80, 80)

    def set_side(self, side):
        self.side = side
        self.update()

    def set_label_rot(self, r):
        if abs(r - self.label_rot) > 1e-6:
            self.label_rot = r
            self.update()

    def draw_text(self, p, center, rect, text):
        p.save()
        p.translate(center)
        p.rotate(self.label_rot)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        p.restore()


class ZoneOverlay(OverlayBase):
    """Guardiola's 20 zones (positional play)."""

    def __init__(self):
        super().__init__(1)
        self.hover = None

    def centers(self):
        return [r.center() for _, r, _ in zone_rects(self.side)]

    def set_hover(self, p):
        """Highlight the zone under the mouse pointer; returns (number, kind, rect) or None."""
        h = None
        if p is not None:
            for n, r, kind in zone_rects(self.side):
                if r.contains(p):
                    h = (n, kind, r)
                    break
        if (h[0] if h else None) != (self.hover[0] if self.hover else None):
            self.hover = h
            self.update()
        return h

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = self.side
        zones = zone_rects(side)
        for n, r, kind in zones:
            if kind in ("h", "c"):
                col = QColor(ORANGE)
                col.setAlpha(34 if kind == "h" else 18)
            else:
                col = QColor(255, 255, 255, 12 if n % 2 else 24)
            p.setBrush(col)
            p.setPen(QPen(QColor(255, 255, 255, 55), 1.6))
            p.drawRect(r)
        blue = QPen(QColor(69, 196, 232, 200), 2.4)
        p.setPen(blue)
        for u in (16.5, 34.5, 52.5, 70.5, 88.5):
            for v0, v1 in ((0, V_WIDE), (68 - V_WIDE, 68)):
                p.drawLine(from_frame(side, u, v0), from_frame(side, u, v1))
        yellow = QPen(QColor(242, 209, 59, 215), 2.8)
        yellow.setDashPattern([3.0, 2.5])
        p.setPen(yellow)
        for v in (V_WIDE, V_HS, 68 - V_HS, 68 - V_WIDE):
            p.drawLine(from_frame(side, 16.5, v), from_frame(side, 88.5, v))
        f = QFont()
        f.setBold(True)
        f.setPixelSize(30)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 235))
        for n, r, kind in zones:
            self.draw_text(p, r.center(), QRectF(-32, -22, 64, 44), str(n))
        if self.hover is not None:
            hl = QColor(ORANGE)
            hl.setAlpha(80)
            p.setBrush(hl)
            p.setPen(QPen(QColor(ORANGE), 4))
            p.drawRect(self.hover[2])


class ThirdsOverlay(OverlayBase):
    """The three thirds: defense, midfield, attack + direction of play."""

    def __init__(self):
        super().__init__(1)

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = self.side
        tints = [(69, 196, 232, 20), (255, 255, 255, 12), (240, 138, 44, 30)]
        for i, tint in enumerate(tints):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(*tint))
            p.drawRect(frect(side, i * 35, (i + 1) * 35, 0, 68))
        pen = QPen(QColor(255, 255, 255, 235), 4.2)
        pen.setDashPattern([2.6, 2.4])
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        for u in (35, 70):
            p.drawLine(from_frame(side, u, 0), from_frame(side, u, 68))
        names = [("DEFENSE", "Defensive third"), ("MIDFIELD", "Middle third"),
                 ("ATTACK", "Attacking third")]
        big = QFont()
        big.setBold(True)
        big.setPixelSize(40)
        small = QFont()
        small.setPixelSize(19)
        for i, (t1, t2) in enumerate(names):
            c = from_frame(side, i * 35 + 17.5, 34)
            p.setFont(big)
            p.setPen(QColor(255, 255, 255, 120))
            self.draw_text(p, c, QRectF(-170, -40, 340, 60), t1)
            p.setFont(small)
            p.setPen(QColor(255, 255, 255, 140))
            self.draw_text(p, c, QRectF(-170, 18, 340, 28), t2)
        # direction of play
        c = from_frame(side, 52.5, 9.5)
        d = side
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 230))
        head = QPolygonF([QPointF(c.x() + 46 * d, c.y()), QPointF(c.x() + 6 * d, c.y() - 30),
                          QPointF(c.x() + 6 * d, c.y() + 30)])
        p.drawPolygon(head)
        for k in range(3):
            off = -14 - k * 20
            p.setPen(QPen(QColor(255, 255, 255, 230), 7, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawLine(QPointF(c.x() + (off + 10) * d, c.y() - 18), QPointF(c.x() + (off + 22) * d, c.y()))
            p.drawLine(QPointF(c.x() + (off + 22) * d, c.y()), QPointF(c.x() + (off + 10) * d, c.y() + 18))
        f = QFont()
        f.setBold(True)
        f.setPixelSize(19)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 230))
        self.draw_text(p, c, QRectF(-110, -70, 220, 30), "DIRECTION OF PLAY")


def depth_groups(discs, team, tol_m):
    """Group a team's outfield players by their ACTUAL position: those at (almost) the same depth
    form a line. Returns: groups from own goal towards the attack, each group from left to right."""
    side = 1 if team == "H" else -1
    gk = f"{team}1"
    pts = []
    for d in discs.values():
        if d.team != team or not d.isVisible() or d.pid == gk:
            continue
        u, v = to_frame(side, d.x(), d.y())
        pts.append((u, v, d))
    pts.sort(key=lambda t: t[0])
    groups = []
    for u, v, d in pts:
        if groups and abs(u - sum(g[0] for g in groups[-1]) / len(groups[-1])) <= tol_m:
            groups[-1].append((u, v, d))
        else:
            groups.append([(u, v, d)])
    return [[d for _, _, d in sorted(g, key=lambda t: t[1])] for g in groups]


class LinesOverlay(QGraphicsItem):
    """Connects players who stand at (almost) the same depth – defense, midfield, attack."""

    def __init__(self, discs):
        super().__init__()
        self.discs = discs
        self.teams = set()
        self.cross = False
        self.tol = 5.0   # tolerance in meters: how far players may be offset in depth
        self.setZValue(9)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self):
        return BOARD.adjusted(-40, -40, 80, 80)

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for team in ("H", "A"):
            show = team in self.teams
            if not show and not self.cross:
                continue
            col = QColor(WHITE if team == "H" else ORANGE)
            groups = depth_groups(self.discs, team, self.tol)
            if show:
                line_col = QColor(col)
                line_col.setAlpha(220)
                p.setPen(QPen(line_col, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.RoundJoin))
                p.setBrush(Qt.BrushStyle.NoBrush)
                for g in groups:
                    if len(g) < 2:
                        continue
                    path = QPainterPath(g[0].pos())
                    for d in g[1:]:
                        path.lineTo(d.pos())
                    p.drawPath(path)
            if self.cross:
                dot = QColor(col)
                dot.setAlpha(120)
                pen = QPen(dot, 2.0, Qt.PenStyle.DotLine)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                chain = list(groups)
                gk = self.discs.get(f"{team}1")
                if gk is not None and gk.isVisible():
                    chain.insert(0, [gk])
                for ga, gb in zip(chain, chain[1:]):
                    for d in ga:
                        near = sorted(gb, key=lambda e: math.hypot(e.x() - d.x(), e.y() - d.y()))[:2]
                        for e in near:
                            p.drawLine(d.pos(), e.pos())


class GuidesOverlay(QGraphicsItem):
    """Guide lines that appear briefly when the magnet snaps."""

    def __init__(self):
        super().__init__()
        self.lines = []
        self.setZValue(20)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self):
        return BOARD.adjusted(-40, -40, 80, 80)

    def set_lines(self, lines):
        if lines != self.lines:
            self.lines = lines
            self.update()

    def paint(self, p, option, widget=None):
        if not self.lines:
            return
        pen = QPen(QColor(240, 138, 44, 190), 1.8, Qt.PenStyle.DashLine)
        p.setPen(pen)
        for ln in self.lines:
            p.drawLine(ln)


class PassNetOverlay(QGraphicsItem):
    """Pass network: connects teammates whose direct passing lane is free (no opponent near the line)."""
    MAX_LEN = 280.0     # 28 m
    LANE = 40.0         # 4 m distance to the pass line

    def __init__(self):
        super().__init__()
        self.setZValue(9.5)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setVisible(False)

    def boundingRect(self):
        return BOARD.adjusted(-40, -40, 80, 80)

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        R = Disc.R
        discs = [d for d in Disc.registry if d.isVisible()]
        for team, col in (("H", QColor(255, 255, 255, 125)), ("A", QColor(240, 138, 44, 175))):
            mates = [d for d in discs if d.team == team]
            opp = [d for d in discs if d.team != team]
            p.setPen(QPen(col, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i, a in enumerate(mates):
                ax, ay = a.x(), a.y()
                for b in mates[i + 1:]:
                    bx, by = b.x(), b.y()
                    dx, dy = bx - ax, by - ay
                    L = math.hypot(dx, dy)
                    if L < 2 * R + 4 or L > self.MAX_LEN:
                        continue
                    blocked = False
                    for o in opp:
                        t = ((o.x() - ax) * dx + (o.y() - ay) * dy) / (L * L)
                        if t < 0.04 or t > 0.96:
                            continue
                        if math.hypot(o.x() - (ax + t * dx), o.y() - (ay + t * dy)) < self.LANE:
                            blocked = True
                            break
                    if blocked:
                        continue
                    ux, uy = dx / L, dy / L
                    p.drawLine(QPointF(ax + ux * (R + 2), ay + uy * (R + 2)),
                               QPointF(bx - ux * (R + 2), by - uy * (R + 2)))


# --------------------------------------------------------------------------
# Graphics items
# --------------------------------------------------------------------------
class PitchItem(QGraphicsItem):
    """Dark board with orange pitch lines."""

    def __init__(self):
        super().__init__()
        self.label_rot = 0.0
        self.setZValue(0)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def set_label_rot(self, r):
        """Shadow always falls the same way relative to the screen, no matter how the board is rotated."""
        if abs(r - self.label_rot) > 1e-6:
            self.label_rot = r
            self.update()

    def boundingRect(self):
        return BOARD.adjusted(-40, -40, 80, 80)

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if Style.flat:
            p.setBrush(QColor("#1b222c"))
            p.drawRoundedRect(BOARD, 28, 28)
        else:
            a = math.radians(self.label_rot)
            ca, sa = math.cos(a), math.sin(a)

            def off(dx, dy):
                return QPointF(dx * ca - dy * sa, dx * sa + dy * ca)
            k = tilt_k()
            s = 0.3 + 0.7 * k
            p.setBrush(QColor(0, 0, 0, 120))
            p.drawRoundedRect(BOARD.translated(off(16 * s, 26 * s)), 28, 28)
            if k > 0.02:
                p.setBrush(QColor("#0f1319"))
                p.drawRoundedRect(BOARD.translated(off(0, 11 * k)), 28, 28)
            grad = QLinearGradient(BOARD.topLeft(), BOARD.bottomRight())
            grad.setColorAt(0, QColor(BOARD_TOP))
            grad.setColorAt(1, QColor(BOARD_BOTTOM))
            p.setBrush(grad)
            p.drawRoundedRect(BOARD, 28, 28)
            p.setBrush(QColor(255, 255, 255, 8))
            sw = PL / 10
            for i in range(0, 10, 2):
                p.drawRect(QRectF(-HL + i * sw, -HW, sw, PW))

        pen = QPen(QColor(ORANGE), 3.2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(-HL, -HW, PL, PW))
        p.drawLine(QPointF(0, -HW), QPointF(0, HW))
        p.drawEllipse(QPointF(0, 0), 91.5, 91.5)
        p.setBrush(QColor(ORANGE))
        p.drawEllipse(QPointF(0, 0), 4.5, 4.5)
        p.setBrush(Qt.BrushStyle.NoBrush)

        for s in (-1, 1):
            x0 = s * HL
            for depth, width in ((165, 403.2), (55, 183.2)):
                left = min(x0, x0 - s * depth)
                p.drawRect(QRectF(left, -width / 2, depth, width))
            spot = QPointF(x0 - s * 110, 0)
            p.setBrush(QColor(ORANGE))
            p.drawEllipse(spot, 4.5, 4.5)
            p.setBrush(Qt.BrushStyle.NoBrush)
            center_angle = 0 if s < 0 else 180
            p.drawArc(QRectF(spot.x() - 91.5, -91.5, 183, 183),
                      int((center_angle - 53) * 16), int(106 * 16))
            goal_x = -HL - 24 if s < 0 else HL
            p.drawRect(QRectF(goal_x, -36.6, 24, 73.2))

        corner_angle = {(-1, -1): -90, (1, -1): 180, (-1, 1): 0, (1, 1): 90}
        for (sx, sy), ang in corner_angle.items():
            c = QPointF(sx * HL, sy * HW)
            p.drawArc(QRectF(c.x() - 10, c.y() - 10, 20, 20), ang * 16, 90 * 16)


class Disc(QGraphicsItem):
    """Player disc (disc with shadow, number, optional name) with magnet when dragging."""
    R = 19.0
    show_numbers = True
    show_names = True
    registry = []
    magnet_on = True
    magnet_strength = 22.0
    zone_centers_fn = None
    guides_item = None
    name_overlay = None
    moved_cb = None
    changed_cb = None

    def __init__(self, pid, team, number, color):
        super().__init__()
        self.pid, self.team, self.number = pid, team, number
        self.name = ""
        self.line = 0
        self.base = QColor(color)
        self.label_rot = 0.0
        self._dragging = False
        self._free = False
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setZValue(10)
        Disc.registry.append(self)

    # ---- Geometry / drawing ---------------------------------------------------
    def boundingRect(self):
        r = self.R
        return QRectF(-r - 95, -r - 70, 2 * r + 190, 2 * r + 140)

    def shape(self):
        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), self.R, self.R)
        return path

    def set_label_rot(self, r):
        if abs(r - self.label_rot) > 1e-6:
            self.label_rot = r
            self.update()

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.R
        if Style.flat:
            p.setPen(QPen(self.base.darker(170), 2.2))
            p.setBrush(self.base)
            p.drawEllipse(QPointF(0, 0), r, r)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.save()
            p.rotate(self.label_rot)      # shadow/light always relative to the screen, not the board
            k = tilt_k()
            s = 0.45 + 0.55 * k
            p.setBrush(QColor(0, 0, 0, 120))
            p.drawEllipse(QPointF(4 * s, 7 * s), r, r)
            if k > 0.02:
                p.setBrush(self.base.darker(190))
                p.drawEllipse(QPointF(0, 3.5 * k), r, r)
            grad = QRadialGradient(QPointF(-r * 0.35, -r * 0.4), r * 1.5)
            grad.setColorAt(0, self.base.lighter(125))
            grad.setColorAt(1, self.base.darker(112))
            p.setBrush(grad)
            p.drawEllipse(QPointF(0, 0), r, r)
            p.restore()
        if self.isSelected():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 230), 2.2, Qt.PenStyle.DashLine))
            p.drawEllipse(QPointF(0, 0), r + 5, r + 5)
        if Disc.show_numbers:
            f = p.font()
            f.setBold(True)
            f.setPixelSize(17)
            p.setFont(f)
            p.setPen(QColor("#1b222c"))
            p.save()
            p.rotate(self.label_rot)
            p.drawText(QRectF(-r, -r, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, str(self.number))
            p.restore()
    def _paint_name_tag(self, p):
        """Name tag as on TV: rectangular field in team color, number + NAME, always upright relative to the screen.
        Position and size follow the perspective (near players larger, tag stays below the disc).
        Drawn by the NameTagOverlay (scene coordinates) so no disc covers the tag."""
        r = self.R
        W = p.worldTransform()
        pos = self.pos()
        c = W.map(pos)
        a = math.radians(self.label_rot)           # screen axes in scene coordinates
        ex = W.map(pos + QPointF(r * math.cos(a), r * math.sin(a)))
        ey = W.map(pos + QPointF(-r * math.sin(a), r * math.cos(a)))
        k = math.hypot(ex.x() - c.x(), ex.y() - c.y()) / r      # screen pixels per scene unit
        if k < 1e-6:
            return
        ry = math.hypot(ey.x() - c.x(), ey.y() - c.y()) / k      # apparent height of the disc (in units)
        text = self.name.upper()
        f = QFont(p.font())
        f.setBold(True)
        f.setPixelSize(14)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 103)
        fm = QFontMetricsF(f)
        w = fm.horizontalAdvance(text) + 14
        h = 20.0
        side_view = ry < 0.78 * r                                # oblique / side view: tag above the disc
        top = -(ry + 12 + h) if side_view else ry + 9
        box = QRectF(-w / 2, top, w, h)
        p.save()
        p.resetTransform()
        p.translate(c)
        p.scale(k, k)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 90))
        p.drawRect(box.translated(1.5, 2.0))
        p.setBrush(self.base)
        p.drawRect(box)
        p.setFont(f)
        p.setPen(QColor("#14181e"))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        p.restore()

    # ---- Magnet --------------------------------------------------------------
    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            if self._dragging and Disc.magnet_on and not self._free:
                return Disc._snap(self, value)
            if self._dragging:
                return QPointF(max(-HL - 40, min(HL + 40, value.x())),
                               max(-HW - 40, min(HW + 40, value.y())))
        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            if Disc.moved_cb:
                Disc.moved_cb()
        return super().itemChange(change, value)

    @staticmethod
    def _snap(disc, pos):
        s = Disc.magnet_strength
        x = max(-HL - 40, min(HL + 40, pos.x()))
        y = max(-HW - 40, min(HW + 40, pos.y()))
        others = [o for o in Disc.registry if o is not disc and o.isVisible()]
        mates = [o for o in others if o.team == disc.team]
        lines = []
        if s > 0:
            joint = None
            if Disc.zone_centers_fn:
                best = None
                for c in Disc.zone_centers_fn():
                    dist = math.hypot(c.x() - x, c.y() - y)
                    if dist < s * 1.5 and (best is None or dist < best[0]):
                        best = (dist, c)
                if best:
                    joint = best[1]
            if joint is not None:
                x, y = joint.x(), joint.y()
                lines.append(QLineF(x, -HW, x, HW))
                lines.append(QLineF(-HL, y, HL, y))
            else:
                cand_x = [o.x() for o in mates]
                cand_y = [o.y() for o in mates]
                same = [o for o in mates if o.line == disc.line and disc.line > 0]
                if same:
                    cand_x.append(sum(o.x() for o in same) / len(same))
                    ys = sorted(o.y() for o in same)
                    lo = [v for v in ys if v < y]
                    hi = [v for v in ys if v > y]
                    if lo and hi:
                        cand_y.append((max(lo) + min(hi)) / 2)
                    if len(ys) >= 2:
                        gaps = sorted(b - a for a, b in zip(ys, ys[1:]))
                        gap = gaps[len(gaps) // 2]
                        if y > ys[-1]:
                            cand_y.append(ys[-1] + gap)
                        if y < ys[0]:
                            cand_y.append(ys[0] - gap)
                cand_x += [0.0]
                cand_y += [0.0]
                if cand_x:
                    bx = min(cand_x, key=lambda c: abs(c - x))
                    if abs(bx - x) <= s:
                        x = bx
                        lines.append(QLineF(x, -HW, x, HW))
                if cand_y:
                    by = min(cand_y, key=lambda c: abs(c - y))
                    if abs(by - y) <= s:
                        y = by
                        lines.append(QLineF(-HL, y, HL, y))
        # keep distance: discs do not overlap
        min_d = 2 * Disc.R + 3
        for _ in range(2):
            for o in others:
                dx, dy = x - o.x(), y - o.y()
                dist = math.hypot(dx, dy)
                if dist < min_d:
                    if dist < 0.01:
                        dx, dy, dist = 1.0, 0.0, 1.0
                    x = o.x() + dx / dist * min_d
                    y = o.y() + dy / dist * min_d
        if Disc.guides_item:
            Disc.guides_item.set_lines(lines)
        return QPointF(x, y)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self._free = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if Disc.guides_item:
            Disc.guides_item.set_lines([])

    # ---- Number / name ---------------------------------------------------------
    def set_number(self, n):
        self.number = int(n)
        self.update()

    def set_name(self, name):
        self.name = name
        self.update()
        if Disc.name_overlay is not None:
            Disc.name_overlay.update()

    def ask_number(self):
        n, ok = QInputDialog.getInt(None, "Player number", "New number:", self.number, 0, 99)
        if ok:
            self.set_number(n)
            if Disc.changed_cb:
                Disc.changed_cb()

    def ask_name(self):
        t, ok = QInputDialog.getText(None, "Player name", "Name:", text=self.name)
        if ok:
            self.set_name(t.strip())
            if Disc.changed_cb:
                Disc.changed_cb()

    def mouseDoubleClickEvent(self, event):
        self.ask_number()
        event.accept()

    def contextMenuEvent(self, event):
        sc = self.scene()
        if sc:
            sc.clearSelection()
        self.setSelected(True)
        m = QMenu()
        a1 = m.addAction("Change number …")
        a2 = m.addAction("Change name …")
        chosen = m.exec(event.screenPos())
        if chosen is a1:
            self.ask_number()
        elif chosen is a2:
            self.ask_name()
        event.accept()


class NameTagOverlay(QGraphicsItem):
    """Draws all name tags above discs and ball so they are never covered."""

    def __init__(self):
        super().__init__()
        self.label_rot = 0.0
        self.setZValue(14)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self):
        return BOARD.adjusted(-140, -140, 280, 280)

    def set_label_rot(self, r):
        if abs(r - self.label_rot) > 1e-6:
            self.label_rot = r
            self.update()

    def paint(self, p, option, widget=None):
        if not Disc.show_names:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        discs = [d for d in Disc.registry if d.name and d.isVisible()]
        # back players first, front players last (tags then overlap naturally)
        a = math.radians(self.label_rot)
        discs.sort(key=lambda d: d.y() * math.cos(a) - d.x() * math.sin(a))
        for d in discs:
            d.label_rot = self.label_rot
            d._paint_name_tag(p)


class Ball(QGraphicsItem):
    R = 10.0

    def __init__(self):
        super().__init__()
        self._dragging = False
        self._free = False
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setZValue(12)

    def boundingRect(self):
        return QRectF(-16, -16, 36, 38)

    def shape(self):
        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), self.R + 3, self.R + 3)
        return path

    def set_label_rot(self, r):
        if abs(r - getattr(self, "label_rot", 0.0)) > 1e-6:
            self.label_rot = r
            self.update()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self._dragging:
            x = max(-HL - 40, min(HL + 40, value.x()))
            y = max(-HW - 40, min(HW + 40, value.y()))
            if Disc.magnet_on and not self._free and Disc.magnet_strength > 0:
                reach = Disc.R + self.R + 4 + Disc.magnet_strength
                best = None
                for d in Disc.registry:
                    if not d.isVisible():
                        continue
                    dist = math.hypot(x - d.x(), y - d.y())
                    if dist < reach and (best is None or dist < best[0]):
                        best = (dist, d)
                if best:
                    d = best[1]
                    dx, dy = x - d.x(), y - d.y()
                    dist = math.hypot(dx, dy)
                    if dist < 0.01:
                        dx, dy, dist = 1.0, 0.0, 1.0
                    off = Disc.R + self.R + 3
                    x, y = d.x() + dx / dist * off, d.y() + dy / dist * off
            return QPointF(x, y)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and Disc.moved_cb:
            Disc.moved_cb()
        return super().itemChange(change, value)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self._free = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        self._dragging = False

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.R
        p.setPen(Qt.PenStyle.NoPen)
        if Style.flat:
            p.setBrush(QColor("#ffffff"))
        else:
            p.save()
            p.rotate(getattr(self, "label_rot", 0.0))
            p.setBrush(QColor(0, 0, 0, 130))
            s = 0.45 + 0.55 * tilt_k()
            p.drawEllipse(QPointF(3 * s, 5 * s), r, r)
            p.restore()
            grad = QRadialGradient(QPointF(-3, -4), r * 1.6)
            grad.setColorAt(0, QColor("#ffffff"))
            grad.setColorAt(1, QColor("#c9c9c9"))
            p.setBrush(grad)
        p.setPen(QPen(QColor("#1b222c"), 1.6))
        p.drawEllipse(QPointF(0, 0), r, r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1b222c"))
        pent = QPolygonF([QPointF(r * 0.42 * math.cos(math.radians(a)),
                                  r * 0.42 * math.sin(math.radians(a)))
                          for a in range(-90, 270, 72)])
        p.drawPolygon(pent)
        if self.isSelected():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 230), 2, Qt.PenStyle.DashLine))
            p.drawEllipse(QPointF(0, 0), r + 6, r + 6)


def smooth_path(pts):
    path = QPainterPath(pts[0])
    if len(pts) < 3:
        path.lineTo(pts[-1])
        return path
    for i in range(1, len(pts) - 1):
        mid = (pts[i] + pts[i + 1]) / 2
        path.quadTo(pts[i], mid)
    path.lineTo(pts[-1])
    return path


class DrawItem(QGraphicsItem):
    """Zones (rect, ellipse, free), paths (run = dashed, pass = arrow) and text notes."""
    ZONES = ("rect", "ellipse", "free")
    _uid_counter = 0
    delete_cb = None     # set by the main window: deleting also removes the item from the scenes

    @classmethod
    def next_uid(cls):
        cls._uid_counter += 1
        return cls._uid_counter

    def __init__(self, kind, points, color, text="", uid=None):
        super().__init__()
        if uid is None:
            uid = DrawItem.next_uid()
        else:
            DrawItem._uid_counter = max(DrawItem._uid_counter, int(uid))
        self.uid = int(uid)
        self.reveal = 1.0          # 0..1: the path is drawn gradually
        self.scene_hidden = False  # not present in the current scene
        self.kind = kind
        self.text = text
        self.label_rot = 0.0
        self.color = QColor(color)
        self.points = [QPointF(pt) for pt in points]
        self._path = QPainterPath()
        self._head = None
        self._stem = None
        self._trect = QRectF()
        self._shape = QPainterPath()
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setZValue(5 if kind in self.ZONES else (13 if kind == "text" else 8))
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._rebuild()

    def set_points(self, pts):
        self.prepareGeometryChange()
        self.points = [QPointF(pt) for pt in pts]
        self._rebuild()
        self.update()

    def set_label_rot(self, r):
        if self.kind == "text" and abs(r - self.label_rot) > 1e-6:
            self.prepareGeometryChange()
            self.label_rot = r
            self._rebuild()
            self.update()

    def set_text(self, text):
        self.prepareGeometryChange()
        self.text = text
        self._rebuild()
        self.update()

    @staticmethod
    def _font():
        f = QFont()
        f.setBold(True)
        f.setPixelSize(22)
        return f

    def _rebuild(self):
        pts = self._eff_points()
        path = QPainterPath()
        self._head = None
        self._stem = None
        if self.kind == "text":
            fm = QFontMetricsF(self._font())
            r = fm.boundingRect(QRectF(0, 0, 320, 2000), TEXT_FLAGS, self.text or " ")
            self._trect = QRectF(0, 0, r.width(), r.height()).adjusted(-10, -7, 10, 7)
            path.addRoundedRect(self._trect, 8, 8)
            tf = QTransform()
            tf.translate(pts[0].x(), pts[0].y())
            tf.rotate(self.label_rot)
            self._path = path
            self._shape = tf.map(path)
            return
        if self.kind in ("rect", "ellipse"):
            rect = QRectF(pts[0], pts[-1]).normalized()
            if self.kind == "rect":
                path.addRoundedRect(rect, 8, 8)
            else:
                path.addEllipse(rect)
        elif self.kind == "free":
            if len(pts) >= 3:
                path.addPolygon(QPolygonF(pts))
                path.closeSubpath()
        else:
            full = QPainterPath(pts[0]) if self.kind == "pass" else smooth_path(pts)
            if self.kind == "pass":
                full.lineTo(pts[-1])
            L = full.length()
            head_len = 20.0
            if L < 1.0:
                path = full
            else:
                tip = full.pointAtPercent(1.0)
                # direction from the actual curve end (smoothed), not from raw mouse points
                ref = full.pointAtPercent(full.percentAtLength(max(0.0, L - 26.0)))
                dx, dy = tip.x() - ref.x(), tip.y() - ref.y()
                ln = math.hypot(dx, dy)
                if ln < 1e-6:
                    dx, dy, ln = tip.x() - pts[0].x(), tip.y() - pts[0].y(), 1.0
                    ln = math.hypot(dx, dy) or 1.0
                dx, dy = dx / ln, dy / ln
                base = QPointF(tip.x() - dx * head_len, tip.y() - dy * head_len)
                nx, ny = -dy * 9, dx * 9
                self._head = QPolygonF([tip, QPointF(base.x() + nx, base.y() + ny),
                                        QPointF(base.x() - nx, base.y() - ny)])

                def piece(s0, s1):
                    """Section of the curve between lengths s0 and s1."""
                    out = QPainterPath(full.pointAtPercent(full.percentAtLength(s0)))
                    s = s0
                    while s < s1 - 0.01:
                        s = min(s1, s + 4.0)
                        out.lineTo(full.pointAtPercent(full.percentAtLength(s)))
                    return out
                cut = max(0.0, L - head_len + 2.0)      # line ends below the arrowhead base
                path = piece(0.0, cut) if cut > 0.5 else QPainterPath(pts[0])
                if self.kind == "run" and cut > 0.5:
                    self._stem = piece(max(0.0, cut - 14.0), cut)   # last piece solid
        self._path = path
        stroker = QPainterPathStroker()
        stroker.setWidth(16)
        shape = stroker.createStroke(path)
        if self.kind in self.ZONES:
            shape = shape.united(path)
        if self._head is not None:
            shape.addPolygon(self._head)
        self._shape = shape

    def boundingRect(self):
        return self._shape.boundingRect().adjusted(-8, -8, 8, 8)

    def shape(self):
        return self._shape

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self.color)
        if self.kind == "text":
            p.save()
            p.translate(self.points[0])
            p.rotate(self.label_rot)
            bg = QColor("#1b222c")
            bg.setAlpha(238)
            p.setBrush(bg)
            p.setPen(QPen(c, 2.4))
            p.drawRoundedRect(self._trect, 8, 8)
            p.setFont(self._font())
            p.setPen(c)
            p.drawText(self._trect.adjusted(10, 7, -10, -7), TEXT_FLAGS, self.text)
            p.restore()
        elif self.kind in self.ZONES:
            fill = QColor(c)
            fill.setAlpha(72)
            pen = QPen(c, 3.2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(fill)
            p.drawPath(self._path)
        else:
            pen = QPen(c, 4.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            if self.kind == "run":
                pen.setDashPattern([2.4, 2.2])
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(self._path)
            if self._stem is not None:
                solid = QPen(pen)
                solid.setStyle(Qt.PenStyle.SolidLine)
                p.setPen(solid)
                p.drawPath(self._stem)
            if self._head is not None:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c)
                p.drawPolygon(self._head)
        if self.isSelected():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 210), 1.6, Qt.PenStyle.DashLine))
            p.drawRect(self._shape.boundingRect().adjusted(-4, -4, 4, 4))

    # ---- Scene support --------------------------------------------------------
    def _eff_points(self):
        pts = self.points
        if self.reveal >= 0.999 or self.kind not in ("run", "pass") or len(pts) < 2:
            return pts
        total = sum(math.hypot(b.x() - a.x(), b.y() - a.y()) for a, b in zip(pts, pts[1:]))
        target = total * max(0.0, self.reveal)
        out = [pts[0]]
        acc = 0.0
        for a, b in zip(pts, pts[1:]):
            seg = math.hypot(b.x() - a.x(), b.y() - a.y())
            if acc + seg >= target:
                f = (target - acc) / seg if seg > 0 else 0.0
                out.append(QPointF(a.x() + (b.x() - a.x()) * f, a.y() + (b.y() - a.y()) * f))
                return out
            out.append(b)
            acc += seg
        return out

    def set_reveal(self, rv):
        if self.kind in ("run", "pass") and abs(rv - self.reveal) > 1e-3:
            self.prepareGeometryChange()
            self.reveal = rv
            self._rebuild()
            self.update()

    def snap(self):
        return {"k": self.kind, "c": self.color.name(), "t": self.text,
                "pts": [[q.x(), q.y()] for q in self.points],
                "pos": [self.pos().x(), self.pos().y()]}

    def apply_snap(self, data):
        if self.scene_hidden:
            self.scene_hidden = False
            self.setEnabled(True)
        pts = [(float(q[0]), float(q[1])) for q in data["pts"]]
        if pts != [(q.x(), q.y()) for q in self.points]:
            self.set_points([QPointF(*q) for q in pts])
        if data.get("t", "") != self.text and self.kind == "text":
            self.set_text(data.get("t", ""))
        self.setPos(QPointF(*data["pos"]))
        self.setOpacity(float(data.get("op", 1.0)))
        self.set_reveal(float(data.get("rv", 1.0)))

    def hide_by_scene(self):
        if not self.scene_hidden:
            self.scene_hidden = True
            self.setSelected(False)
            self.setEnabled(False)
        self.setOpacity(0.0)

    def show_by_scene(self):
        """Counterpart to hide_by_scene (also to rescue formerly 'orphaned' drawings)."""
        if self.scene_hidden:
            self.scene_hidden = False
            self.setEnabled(True)
        self.setOpacity(1.0)

    @staticmethod
    def from_snap(key, data):
        item = DrawItem(data["k"], [QPointF(*q) for q in data["pts"]], data["c"], data.get("t", ""), uid=int(key))
        item.setPos(QPointF(*data["pos"]))
        return item

    def edit_text(self):
        t, ok = QInputDialog.getMultiLineText(None, "Note", "Text:", self.text)
        if ok and t.strip():
            self.set_text(t.strip())

    def mouseDoubleClickEvent(self, event):
        if self.kind == "text":
            self.edit_text()
        event.accept()

    def contextMenuEvent(self, event):
        sc = self.scene()
        if sc:
            sc.clearSelection()
        self.setSelected(True)
        m = QMenu()
        a_edit = m.addAction("Edit text …") if self.kind == "text" else None
        a_del = m.addAction("Delete")
        chosen = m.exec(event.screenPos())
        if chosen is a_del and sc:
            if DrawItem.delete_cb is not None:
                DrawItem.delete_cb(self)
            else:
                sc.removeItem(self)
        elif a_edit is not None and chosen is a_edit:
            self.edit_text()
        event.accept()

    def to_dict(self):
        return {"kind": self.kind, "color": self.color.name(), "text": self.text,
                "points": [[q.x(), q.y()] for q in self.points],
                "pos": [self.pos().x(), self.pos().y()],
                "uid": self.uid, "hidden": self.scene_hidden}

    @staticmethod
    def from_dict(d):
        item = DrawItem(d["kind"], [QPointF(*q) for q in d["points"]], d["color"], d.get("text", ""),
                        uid=d.get("uid"))
        item.setPos(QPointF(*d.get("pos", [0, 0])))
        if d.get("hidden"):
            item.hide_by_scene()
        return item


# --------------------------------------------------------------------------
# View with 3D perspective and drawing tools
# --------------------------------------------------------------------------
class BoardView(QGraphicsView):
    viewChanged = Signal()
    drawn = Signal(object)
    hovered = Signal(object)

    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setBackgroundBrush(QColor("#0d1015"))
        self.tilt, self.rotz, self.zoom = 0.0, 0.0, 1.0
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.tool = "move"
        self.color = DRAW_COLORS[0]
        self._preview = None
        self._drawing = False
        self._insets = (0, 0, 0, 0)     # edges covered by the floating menu (left, top, right, bottom)

    # ---- View -----------------------------------------------------------
    def set_insets(self, left, top, right, bottom):
        """The board is fitted into the area that floating menus do not cover."""
        new = (int(round(left)), int(round(top)), int(round(right)), int(round(bottom)))
        if new != self._insets:
            self._insets = new
            self.apply_view()

    def set_view(self, tilt=None, rotz=None, zoom=None):
        if tilt is not None:
            self.tilt = max(0.0, min(70.0, tilt))
        if rotz is not None:
            self.rotz = rotz
        if zoom is not None:
            self.zoom = max(0.4, min(3.0, zoom))
        self.apply_view()
        self.viewChanged.emit()

    def apply_view(self):
        t = QTransform()
        t.rotate(self.tilt, Qt.Axis.XAxis, PERSPECTIVE_DISTANCE)
        t.rotate(self.rotz)
        r = t.mapRect(BOARD.adjusted(-40, -40, 40, 40))
        full_w, full_h = max(self.viewport().width(), 1), max(self.viewport().height(), 1)
        il, it, ir, ib = self._insets
        vw = max(full_w - il - ir, 0.3 * full_w)
        vh = max(full_h - it - ib, 0.3 * full_h)
        fit = min(vw / r.width(), vh / r.height()) * 0.97 * self.zoom
        Style.tilt = self.tilt
        self.setTransform(t * QTransform.fromScale(fit, fit))
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate if self.tilt < 0.01
                                   else QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        # scene center (0, 0) should appear in the middle of the free area
        tot = self.transform()
        origin = tot.map(QPointF(0, 0))
        free_c = QPointF(il + (full_w - il - ir) / 2.0, it + (full_h - it - ib) / 2.0)
        target = QPointF(full_w / 2.0 - free_c.x(), full_h / 2.0 - free_c.y()) + origin
        inv, ok = tot.inverted()
        self.centerOn(inv.map(target) if ok else QPointF(0, 0))
        for item in self.scene().items():
            f = getattr(item, "set_label_rot", None)
            if f:
                f(-self.rotz)
        self.viewport().update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.apply_view()

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120.0
        self.set_view(zoom=self.zoom * (1.1 ** steps))

    # ---- Drawing ----------------------------------------------------------
    def mousePressEvent(self, e):
        if self.tool == "move" or e.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        p = self.mapToScene(e.position().toPoint())
        self.scene().clearSelection()
        if self.tool == "text":
            t, ok = QInputDialog.getMultiLineText(self, "Note on the board", "Text:")
            if ok and t.strip():
                item = DrawItem("text", [p], self.color, t.strip())
                self.scene().addItem(item)
                item.set_label_rot(-self.rotz)
                self.drawn.emit(item)
            return
        self._preview = DrawItem(self.tool, [p, p], self.color)
        self.scene().addItem(self._preview)
        self._drawing = True

    def leaveEvent(self, e):
        self.hovered.emit(None)
        super().leaveEvent(e)

    def mouseMoveEvent(self, e):
        if not self._drawing:
            self.hovered.emit(self.mapToScene(e.position().toPoint()))
            return super().mouseMoveEvent(e)
        p = self.mapToScene(e.position().toPoint())
        it = self._preview
        if it.kind in ("rect", "ellipse", "pass"):
            it.set_points([it.points[0], p])
        else:
            last = it.points[-1]
            if math.hypot(p.x() - last.x(), p.y() - last.y()) >= 7:
                it.set_points(it.points + [p])

    def mouseReleaseEvent(self, e):
        if not self._drawing:
            return super().mouseReleaseEvent(e)
        self._drawing = False
        it, self._preview = self._preview, None
        pts = it.points
        if it.kind in ("rect", "ellipse"):
            r = QRectF(pts[0], pts[-1]).normalized()
            ok = r.width() > 14 and r.height() > 14
        elif it.kind == "pass":
            ok = math.hypot(pts[-1].x() - pts[0].x(), pts[-1].y() - pts[0].y()) > 28
        else:
            length = sum(math.hypot(a.x() - b.x(), a.y() - b.y()) for a, b in zip(pts, pts[1:]))
            ok = len(pts) >= 3 and length > 30
        if ok:
            self.drawn.emit(it)
        else:
            self.scene().removeItem(it)


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------
STYLE = f"""
QWidget {{ background:#14181e; color:#e9e6e1; font-size:13px; }}
QMainWindow, QScrollArea {{ background:#14181e; }}
QMenuBar, QMenu {{ background:#14181e; }}
QMenu {{ border:1px solid #333c49; padding:5px; }}
QMenu::item {{ padding:7px 22px 7px 12px; border-radius:5px; }}
QMenu::separator {{ height:1px; background:#2c333d; margin:5px 8px; }}
QMenu::indicator {{ width:14px; height:14px; margin-left:6px; border:1px solid #3a4452; border-radius:4px; background:#1d232c; }}
QMenu::indicator:checked {{ background:{ORANGE}; border-color:{ORANGE}; }}
QMenu::item:selected, QMenuBar::item:selected {{ background:{ORANGE}; color:#14181e; }}
QToolBar {{ background:#14181e; border:0; spacing:6px; padding:5px; }}
QToolButton {{ background:#232a35; border:1px solid #333c49; border-radius:6px; padding:6px 11px; }}
QToolButton:hover {{ border-color:{ORANGE}; }}
QToolButton:checked {{ background:{ORANGE}; color:#14181e; font-weight:bold; }}
QGroupBox {{ border:1px solid #2c333d; border-radius:8px; margin-top:16px;
            padding:12px 8px 8px 8px; font-weight:bold; }}
QGroupBox::title {{ subcontrol-origin:margin; left:10px; color:{ORANGE}; }}
QPushButton {{ background:#232a35; border:1px solid #333c49; border-radius:6px; padding:6px 8px; }}
QPushButton:hover {{ border-color:{ORANGE}; }}
QPushButton:checked {{ background:{ORANGE}; color:#14181e; font-weight:bold; }}
QPushButton:disabled {{ color:#667; }}
QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit, QListWidget, QPlainTextEdit, QTextBrowser {{
            background:#1d232c; border:1px solid #333c49; border-radius:6px; padding:4px; }}
QComboBox QAbstractItemView {{ background:#1d232c; selection-background-color:{ORANGE};
            selection-color:#14181e; }}
QListWidget::item:selected {{ background:{ORANGE}; color:#14181e; }}
QSlider::groove:horizontal {{ height:4px; background:#2c333d; border-radius:2px; }}
QSlider::sub-page:horizontal {{ background:{ORANGE}; border-radius:2px; }}
QSlider::handle:horizontal {{ background:{ORANGE}; width:14px; margin:-6px 0; border-radius:7px; }}
QCheckBox::indicator {{ width:16px; height:16px; border-radius:4px; border:1px solid #333c49; background:#1d232c; }}
QCheckBox::indicator:checked {{ background:{ORANGE}; }}
QTabWidget::pane {{ border:1px solid #2c333d; border-radius:6px; top:-1px; }}
QTabBar::tab {{ background:#1d232c; border:1px solid #2c333d; padding:6px 7px; margin-right:2px;
            border-top-left-radius:6px; border-top-right-radius:6px; }}
QTabBar::tab:selected {{ background:{ORANGE}; color:#14181e; font-weight:bold; }}
QToolButton::menu-indicator {{ image:none; width:0; }}
QFrame#rail {{ background:rgba(18,22,28,242); border:1px solid rgba(255,255,255,55); border-radius:16px; }}
QFrame#rail QWidget {{ background:transparent; }}
QFrame#rail QFrame#pill {{ background:{ORANGE}; border-radius:8px; }}
QFrame#drawer {{ background:rgba(20,24,31,247); border:1px solid rgba(255,255,255,55); border-radius:16px; }}
QFrame#drawer QWidget {{ background:transparent; font-size:14px; }}
QFrame#drawer QComboBox, QFrame#drawer QDoubleSpinBox, QFrame#drawer QSpinBox, QFrame#drawer QLineEdit,
QFrame#drawer QListWidget, QFrame#drawer QPlainTextEdit, QFrame#drawer QTextBrowser {{ background:rgba(29,35,44,215); }}
QFrame#drawer QComboBox QAbstractItemView {{ background:#1d232c; }}
QFrame#drawer QPushButton {{ background:rgba(35,42,53,225); }}
QFrame#drawer QPushButton:checked {{ background:{ORANGE}; color:#14181e; }}
QFrame#drawer QToolButton#flat {{ background:transparent; }}
QFrame#floatbar {{ background:rgba(18,22,28,218); border:1px solid rgba(255,255,255,45); border-radius:13px; }}
QFrame#floatbar QWidget {{ background:transparent; }}
QFrame#floatbar QToolButton#fbtn {{ background:rgba(35,42,53,235); border:1px solid #333c49; border-radius:8px; padding:5px 10px; }}
QFrame#floatbar QToolButton#fbtn:hover {{ border-color:{ORANGE}; }}
QFrame#floatbar QToolButton#fbtn:checked {{ background:{ORANGE}; color:#14181e; font-weight:bold; }}
QFrame#floatbar QFrame#pill {{ background:{ORANGE}; border-radius:8px; }}
QFrame#floatbar QToolButton#modebtn {{ background:transparent; border:1px solid transparent; border-radius:8px;
            padding:5px 10px; font-weight:bold; }}
QFrame#floatbar QToolButton#modebtn:hover {{ border-color:#3a4452; }}
QFrame#floatbar QToolButton#modebtn:checked {{ color:#14181e; }}
QFrame#drawer QFrame#segbox {{ background:#232a35; border:1px solid #333c49; border-radius:10px; }}
QFrame#drawer QToolButton#seg {{ background:transparent; border:0; border-radius:8px; font-weight:bold; }}
QFrame#drawer QToolButton#seg:checked {{ color:#14181e; }}
QFrame#drawer QFrame#pill {{ background:{ORANGE}; border-radius:8px; }}
QFrame#floatbar QLabel {{ color:#8d97a3; font-size:10px; font-weight:bold; }}
QLabel#chip {{ background:rgba(18,22,28,225); border:1px solid rgba(255,255,255,45); border-radius:10px;
            padding:5px 11px; color:#e9e6e1; font-size:12px; }}
QFrame#hidehint {{ background:rgba(18,22,28,225); border:1px solid rgba(255,255,255,45); border-radius:10px; }}
QFrame#hidehint QLabel {{ background:transparent; border:none; padding:0; color:#e9e6e1; font-size:12px; }}
QFrame#hidehint QToolButton {{ background:transparent; border:none; border-radius:9px; color:#aab3bd;
            font-size:14px; font-weight:bold; padding:0 4px; }}
QFrame#hidehint QToolButton:hover {{ background:rgba(255,255,255,30); color:#ffffff; }}
QFrame#transport {{ background:rgba(18,22,28,240); border:1px solid rgba(255,255,255,55); border-radius:14px; }}
QFrame#transport QLabel, QFrame#transport QSlider {{ background:transparent; }}
QFrame#transport QToolButton#tbtn {{ background:#232a35; border:1px solid #333c49; border-radius:8px;
            padding:4px 9px; font-size:15px; min-width:20px; }}
QFrame#transport QToolButton#tbtn:hover {{ border-color:{ORANGE}; }}
QFrame#transport QToolButton#tbtn:checked {{ background:{ORANGE}; color:#14181e; }}
QFrame#transport QToolButton#tplay {{ background:{ORANGE}; color:#14181e; border:0; border-radius:17px;
            font-size:16px; font-weight:bold; min-width:34px; }}
QToolButton#railbtn {{ background:transparent; border:1px solid transparent; border-radius:8px; padding:4px; }}
QToolButton#railbtn:hover {{ border-color:#3a4452; }}
QToolButton#railbtn:checked {{ background:transparent; color:#14181e; font-weight:bold; }}
QToolButton#tool {{ font-size:17px; padding:2px; background:transparent; border:1px solid transparent; border-radius:8px; }}
QToolButton#tool:hover {{ border-color:#3a4452; }}
QToolButton#tool:checked {{ background:transparent; color:#14181e; }}
QToolButton#dockhandle {{ background:#12161c; border:0; border-top:1px solid #262d37; border-radius:0;
            color:#8d97a3; font-size:11px; padding:2px; min-height:0; }}
QToolButton#dockhandle:hover {{ color:{ORANGE}; }}
QFrame#pill {{ background:{ORANGE}; border-radius:8px; }}
QFrame#segbox {{ background:rgba(18,22,28,238); border:1px solid rgba(255,255,255,55); border-radius:14px; }}
QToolButton#seg {{ background:transparent; border:0; border-radius:8px; }}
QToolButton#seg:checked {{ background:transparent; color:#14181e; font-weight:bold; }}
QFrame#popover {{ background:rgba(26,31,39,232); border:1px solid rgba(255,255,255,45); border-radius:12px; }}
QFrame#popover QWidget {{ background:transparent; }}
QFrame#popover QComboBox {{ background:#232a35; }}
QFrame#popover QAbstractItemView {{ background:#232a35; }}
QLabel#poptitle {{ color:#8d97a3; font-size:11px; font-weight:bold; padding:6px 12px 3px 12px; }}
QPushButton#poprow {{ background:transparent; border:0; text-align:left; padding:8px 12px; border-radius:7px; }}
QPushButton#poprow:hover {{ background:#262d38; }}
QCheckBox#poprow {{ padding:7px 10px; border-radius:7px; spacing:10px; }}
QCheckBox#poprow:hover {{ background:#262d38; }}
QToolButton#flat {{ background:transparent; border:0; font-size:15px; padding:4px 8px; }}
QToolButton#flat:hover {{ color:{ORANGE}; }}
QToolButton#topbtn {{ background:rgba(18,22,28,238); border:1px solid rgba(255,255,255,55); border-radius:14px;
            padding:7px 14px 7px 11px; font-weight:bold; }}
QToolButton#topbtn:hover {{ border-color:{ORANGE}; }}
QToolButton#topbtn:checked {{ background:{ORANGE}; color:#14181e; border-color:{ORANGE}; }}
"""


def _opaque_style(css):
    """Opaque variant of the stylesheet (performance mode): no transparency in the menus."""
    css = re.sub(r"rgba\(255,\s*255,\s*255,\s*\d+\)", "#3a424e", css)
    return re.sub(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*\d+\)", r"rgb(\1,\2,\3)", css)


STYLE_PERF = _opaque_style(STYLE)



class SceneSlider(QSlider):
    """Timeline like in video editing: a click jumps directly, scenes are visible as markers."""

    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.marks = 0
        self._drag = False
        self.setFixedHeight(30)

    def _val(self, x):
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(x) - 8, max(1, self.width() - 16))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.maximum() > 0:
            self._drag = True
            self.setValue(self._val(e.position().x()))
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag:
            self.setValue(self._val(e.position().x()))
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = False
        e.accept()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.marks < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#14181e"), 1.5))
        p.setBrush(QColor(WHITE))
        for i in range(self.marks):
            x = 8 + (self.width() - 16) * i / (self.marks - 1)
            p.drawEllipse(QPointF(x, self.height() - 5), 3.2, 3.2)


class Pill(QFrame):
    """Marker that glides smoothly to the active button (like Apple segmented controls)."""

    def __init__(self, host, buttons):
        super().__init__(host)
        self.setObjectName("pill")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.buttons = buttons
        self._collapse = False
        self.anim = QPropertyAnimation(self, b"geometry", self)
        self.anim.setDuration(300)
        self.anim.setEasingCurve(QEasingCurve.Type.OutQuart)
        self.anim.finished.connect(self._done)
        self.hide()

    def _done(self):
        if self._collapse:
            self.hide()

    def sync(self, animate=True):
        animate = animate and not PERF["on"]
        btn = next((b for b in self.buttons if b.isChecked()), None)
        self.anim.stop()
        if btn is None:
            if not self.isVisible():
                return
            if animate:
                c = self.geometry().center()
                self._collapse = True
                self.anim.setStartValue(self.geometry())
                self.anim.setEndValue(QRect(c.x(), c.y(), 0, 0))
                self.anim.start()
            else:
                self.hide()
            return
        self._collapse = False
        r = btn.geometry()
        if not self.isVisible():
            if animate:
                c = r.center()
                self.setGeometry(QRect(c.x(), c.y(), 0, 0))
            else:
                self.setGeometry(r)
            self.show()
            self.lower()
        if animate and self.geometry() != r:
            self.anim.setStartValue(self.geometry())
            self.anim.setEndValue(r)
            self.anim.start()
        else:
            self.setGeometry(r)


def _mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))


class AnimButton(QToolButton):
    """Floating switch with a smooth color transition (hover / active) and a small press animation."""

    def __init__(self, text, icon_name, parent=None, inner=False):
        super().__init__(parent)
        self._text = text
        self._inner = inner
        self._icon = make_icon(icon_name) if icon_name else None
        self._p = 0.0      # 0 = off, 1 = active (orange)
        self._h = 0.0      # hover
        self._ap = QVariantAnimation(self)
        self._ap.valueChanged.connect(self._set_p)
        self._ah = QVariantAnimation(self)
        self._ah.valueChanged.connect(self._set_h)
        self.toggled.connect(lambda _v: self._sync_checked())
        self.setObjectName("animbtn")
        # The global stylesheet would otherwise put a background under the drawing
        self.setStyleSheet("QToolButton#animbtn { background:transparent; border:0; padding:0; margin:0; }")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        super().setText(text)

    def setText(self, t):
        self._text = t
        super().setText(t)
        self.updateGeometry()
        self.update()

    def _set_p(self, v):
        self._p = float(v)
        self.update()

    def _set_h(self, v):
        self._h = float(v)
        self.update()

    def _go(self, anim, cur, target, ms, setter):
        anim.stop()
        if PERF["on"] or not self.isVisible():
            setter(target)
            return
        anim.setStartValue(float(cur))
        anim.setEndValue(float(target))
        anim.setDuration(ms)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()

    def set_active(self, on):
        """For switches without their own checked state (e.g. popover open)."""
        self._go(self._ap, self._p, 1.0 if on else 0.0, 220, self._set_p)

    def checkStateSet(self):
        super().checkStateSet()
        self._sync_checked()

    def _sync_checked(self):
        """Always derive the active state from isChecked() (regardless of which signals/hooks fire)."""
        if not self.isCheckable():
            return
        t = 1.0 if self.isChecked() else 0.0
        if t != getattr(self, "_tgt", None):
            self._tgt = t
            self.set_active(t > 0.5)

    def enterEvent(self, e):
        self._go(self._ah, self._h, 1.0, 150, self._set_h)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._go(self._ah, self._h, 0.0, 220, self._set_h)
        super().leaveEvent(e)

    def sizeHint(self):
        f = QFont(self.font())
        f.setBold(True)
        fm = QFontMetricsF(f)
        w = fm.horizontalAdvance(self._text)
        iw = 25 if self._icon else 0
        padx, pady = (10, 6) if self._inner else (14, 8)
        return QSize(int(2 * padx + iw + w + 3), int(max(18.0, fm.height()) + 2 * pady + 2))

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, e):
        self._sync_checked()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.isDown():
            r = r.adjusted(1.5, 1.5, -1.5, -1.5)
        solid = PERF["on"]
        if self._inner:
            base = QColor(35, 42, 53, 255 if solid else 235)
            edge = QColor(51, 60, 73)
        else:
            base = QColor(18, 22, 28, 255 if solid else 238)
            edge = QColor(58, 66, 78) if solid else QColor(255, 255, 255, 55)
        orange = QColor(ORANGE)
        if solid:
            bg = _mix(base, orange, self._p)
        else:
            # inactive: transparent (border only), active: orange faded in
            bg = QColor(orange)
            bg.setAlpha(int(255 * self._p))
            if self._h > 0.0 and self._p < 1.0:
                tint = QColor(255, 255, 255, int(26 * self._h * (1.0 - self._p)))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(tint)
                p.drawRoundedRect(r, 8 if self._inner else 14, 8 if self._inner else 14)
        border = _mix(_mix(edge, orange, self._h), orange, self._p)
        p.setPen(QPen(border, 1.0))
        p.setBrush(bg)
        rad = 8 if self._inner else 14
        p.drawRoundedRect(r, rad, rad)
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetricsF(f)
        tw = fm.horizontalAdvance(self._text)
        iw = 25 if self._icon else 0
        x = (self.width() - (iw + tw)) / 2.0
        if self._icon:
            ir = QRectF(x, (self.height() - 18) / 2.0, 18, 18)
            sz = QSize(36, 36)
            off = self._icon.pixmap(sz, QIcon.Mode.Normal, QIcon.State.Off)
            on = self._icon.pixmap(sz, QIcon.Mode.Normal, QIcon.State.On)
            p.setOpacity(1.0 - self._p)
            p.drawPixmap(ir, off, QRectF(off.rect()))
            p.setOpacity(self._p)
            p.drawPixmap(ir, on, QRectF(on.rect()))
            p.setOpacity(1.0)
        p.setPen(_mix(QColor("#e9e6e1"), QColor("#14181e"), self._p))
        p.drawText(QRectF(x + iw, 0, tw + 4, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)


class Popover(QFrame):
    """Menu card that fades in smoothly and slides down slightly (instead of a hard popup)."""
    instances = []
    visibleChanged = Signal(bool)

    def __init__(self, host, builder=None):
        super().__init__(host)
        self.host = host
        self.builder = builder
        self.anchor = None
        self.align = "left"
        self._p = 0.0
        self._closing = False
        self._x = self._y = 0
        self._fx = None
        self.setObjectName("popover")
        self.setMinimumWidth(250)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(8, 8, 8, 8)
        self._lay.setSpacing(2)
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._tick)
        self._anim.finished.connect(self._done)
        self.hide()
        Popover.instances.append(self)

    # ---- Content ----------------------------------------------------------------
    def set_items(self, items):
        while self._lay.count():
            it = self._lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                if w.property("popOwned"):
                    w.deleteLater()
        for it in items:
            kind = it[0]
            if kind == "sep":
                line = QFrame()
                line.setFixedHeight(1)
                line.setStyleSheet("background:#2c333d;")
                line.setProperty("popOwned", True)
                self._lay.addSpacing(4)
                self._lay.addWidget(line)
                self._lay.addSpacing(4)
            elif kind == "title":
                lab = QLabel(it[1])
                lab.setObjectName("poptitle")
                lab.setProperty("popOwned", True)
                self._lay.addWidget(lab)
            elif kind in ("btn", "act"):
                text, fn = (it[1], it[2]) if kind == "btn" else (it[1].text(), it[1].trigger)
                b = QPushButton(text)
                b.setObjectName("poprow")
                b.setProperty("popOwned", True)
                b.clicked.connect(lambda _=False, f=fn: self._activate(f))
                self._lay.addWidget(b)
            elif kind == "choice":
                b = QPushButton(it[1])
                b.setObjectName("poprow")
                b.setProperty("popOwned", True)
                b.setIconSize(QSize(16, 16))
                b.setIcon(make_icon("check") if it[3] else make_icon("blank"))
                b.clicked.connect(lambda _=False, f=it[2]: self._activate(f))
                self._lay.addWidget(b)
            elif kind == "check":
                act = it[1]
                cb = QCheckBox(act.text())
                cb.setObjectName("poprow")
                cb.setProperty("popOwned", True)
                cb.setChecked(act.isChecked())
                cb.toggled.connect(act.setChecked)
                act.toggled.connect(lambda v, c=cb: (c.blockSignals(True), c.setChecked(v), c.blockSignals(False)))
                self._lay.addWidget(cb)
            elif kind == "widget":
                self._lay.addWidget(it[1])
                it[1].show()

    def _activate(self, fn):
        self.close_pop()
        QTimer.singleShot(0, fn)

    # ---- Open / close ---------------------------------------------------------
    def toggle(self, anchor, align="left"):
        if self.isVisible() and not self._closing and self.anchor is anchor:
            self.close_pop()
        else:
            self.open_at(anchor, align)

    def open_at(self, anchor, align="left"):
        for p in Popover.instances:
            if p is not self and p.isVisible():
                p.close_pop()
        if self.builder:
            self.set_items(self.builder())
        self.anchor, self.align = anchor, align
        self._lay.activate()
        self.adjustSize()
        pt = anchor.mapTo(self.host, QPoint(0, anchor.height() + 6))
        if align == "right":
            x = pt.x() + anchor.width() - self.width()
        elif align == "center":
            x = pt.x() + (anchor.width() - self.width()) // 2
        else:
            x = pt.x()
        x = max(8, min(x, self.host.width() - self.width() - 8))
        self._x, self._y = x, pt.y()
        self._closing = False
        self.visibleChanged.emit(True)
        self._anim.stop()
        if PERF["on"]:
            self.setGraphicsEffect(None)
            self._fx = None
            self._p = 1.0
            self.move(self._x, self._y)
            self.show()
            self.raise_()
            QApplication.instance().installEventFilter(self)
            return
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._tick(0.0)
        self.show()
        self.raise_()
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        QApplication.instance().installEventFilter(self)

    def close_pop(self):
        if not self.isVisible() or self._closing:
            return
        self.visibleChanged.emit(False)
        if PERF["on"]:
            self._anim.stop()
            self.hide()
            self._closing = False
            self.setGraphicsEffect(None)
            self._fx = None
            QApplication.instance().removeEventFilter(self)
            return
        self._closing = True
        self._anim.stop()
        if self._fx is None:
            self._fx = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(self._fx)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._anim.setStartValue(float(self._p))
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _tick(self, v):
        self._p = float(v)
        if self._fx is not None:
            self._fx.setOpacity(self._p)
        self.move(self._x, int(self._y - 12 * (1.0 - self._p)))

    def _done(self):
        if self._closing:
            self.hide()
            self._closing = False
            QApplication.instance().removeEventFilter(self)
        self.setGraphicsEffect(None)
        self._fx = None

    def eventFilter(self, obj, ev):
        if (ev.type() == QEvent.Type.MouseButtonPress and self.isVisible() and not self._closing
                and QApplication.activePopupWidget() is None and hasattr(ev, "globalPosition")):
            gp = ev.globalPosition().toPoint()
            inside = self.rect().contains(self.mapFromGlobal(gp))
            on_anchor = self.anchor is not None and self.anchor.rect().contains(self.anchor.mapFromGlobal(gp))
            if not inside and not on_anchor:
                self.close_pop()
        return False


# Name of the icon file (next to taktiktafel.py, or bundled by PyInstaller)
ICON_FILE = "image.ico"


def resource_path(relative_path):
    """Absolute path to a resource - works in the script and in the PyInstaller exe."""
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tactics Board")
        self.setWindowIcon(QIcon(resource_path(ICON_FILE)))
        self.resize(1640, 940)
        self.setMinimumSize(1120, 680)

        self.scene = QGraphicsScene(self)
        self.scene.setSceneRect(-4000, -3000, 8000, 6000)
        self.scene.addItem(PitchItem())
        self.zone_overlay = ZoneOverlay()
        self.thirds_overlay = ThirdsOverlay()
        self.zone_overlay.setVisible(False)
        self.thirds_overlay.setVisible(False)
        self.scene.addItem(self.zone_overlay)
        self.scene.addItem(self.thirds_overlay)

        Disc.registry = []
        self.discs = {}
        for team, col in (("H", WHITE), ("A", ORANGE)):
            for i in range(1, 12):
                d = Disc(f"{team}{i}", team, i, col)
                self.scene.addItem(d)
                self.discs[d.pid] = d
        self.ball = Ball()
        self.scene.addItem(self.ball)

        self.lines_overlay = LinesOverlay(self.discs)
        self.scene.addItem(self.lines_overlay)
        self.pass_net = PassNetOverlay()
        self.scene.addItem(self.pass_net)
        self.guides = GuidesOverlay()
        self.scene.addItem(self.guides)
        self.name_tags = NameTagOverlay()
        self.scene.addItem(self.name_tags)

        Disc.guides_item = self.guides
        Disc.name_overlay = self.name_tags
        Disc.moved_cb = self.on_discs_moved
        Disc.changed_cb = self.refresh_player_editor
        Disc.zone_centers_fn = self._zone_snap_centers
        DrawItem.delete_cb = self.delete_drawing

        self.view = BoardView(self.scene)
        self.scenes = []
        self.trail_items = []
        self.draw_history = []
        self.formation_of = {"H": "4-2-3-1", "A": "4-4-2"}
        self._pos_anim = None
        self._view_anim = None
        self._play_anim = None
        self._seek_anim = None

        self._build_ui()
        self._build_toolbar()

        self.view.viewChanged.connect(self._sync_view_sliders)
        self.view.hovered.connect(self._on_hover)
        self.view.drawn.connect(self._on_drawn)
        self.scene.selectionChanged.connect(self.refresh_player_editor)

        self.apply_formation("H", "4-2-3-1", animate=False)
        self.apply_formation("A", "4-4-2", animate=False)
        self.ball.setPos(0, 0)
        self.home_combo.setCurrentText("4-2-3-1")
        self.away_combo.setCurrentText("4-4-2")
        self.view.set_view(tilt=0, rotz=0, zoom=1)
        self.refresh_player_editor()

    # ---- UI helpers ------------------------------------------------------------
    def _group(self, title):
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        lay.setSpacing(7)
        return box, lay

    def _page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        return w, lay

    def _slider_row(self, label, lo, hi, val, callback):
        row = QHBoxLayout()
        lab = QLabel(label)
        lab.setFixedWidth(78)
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        s.valueChanged.connect(callback)
        row.addWidget(lab)
        row.addWidget(s)
        return row, s

    def _hint(self, text):
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setStyleSheet("color:#8d97a3; font-weight:normal;")
        return lab

    def _add_drawer(self, key, title, widget):
        self.stack.addWidget(widget)
        self.drawer_keys.append(key)
        self.drawer_titles[key] = title

    def _build_ui(self):
        self.mode_flat = False
        self._last_tilt = 45.0
        self.current_drawer = None
        self.stack = QStackedWidget()
        self.drawer_keys = []
        self.drawer_titles = {}
        self._pills = {}

        # ===== Menu: View =====
        page, pl = self._page()
        box, lay = self._group("Viewing angle")
        row, self.tilt_slider = self._slider_row("Tilt", 0, 70, 0, lambda v: self.view.set_view(tilt=v))
        lay.addLayout(row)
        row, self.rot_slider = self._slider_row("Rotation", -180, 180, 0, lambda v: self.view.set_view(rotz=v))
        lay.addLayout(row)
        row, self.zoom_slider = self._slider_row("Zoom", 40, 300, 100, lambda v: self.view.set_view(zoom=v / 100))
        lay.addLayout(row)
        b = QPushButton("Reset view")
        b.clicked.connect(lambda: self.animate_view(0, 0, 1.0))
        lay.addWidget(b)
        lay.addWidget(self._hint("Quick switch at the top of the bar: 2D / 3D and “Perspective”. "
                                 "Mouse wheel = zoom. In 2D mode the tilt is locked."))
        pl.addWidget(box)
        view_page, view_pl = page, pl      # the show/hide options go further down into the same page

        # ===== Menu: Team =====
        page, pl = self._page()
        self.team_view = "H"
        seg = QFrame()
        seg.setObjectName("segbox")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(0)
        tgrp = QButtonGroup(self)
        tgrp.setExclusive(True)
        self.team_btns = {}
        for key, text in (("H", "Home"), ("A", "Away")):
            b = QToolButton()
            b.setObjectName("seg")
            b.setText(text)
            b.setCheckable(True)
            b.setFixedSize(140, 32)
            tgrp.addButton(b)
            sl.addWidget(b)
            b.clicked.connect(lambda _=False, k=key: self._set_team_view(k))
            self.team_btns[key] = b
        self.team_btns["H"].setChecked(True)
        self.team_seg = seg
        self.team_pill = Pill(seg, list(self.team_btns.values()))
        self._pills[seg] = self.team_pill
        seg.installEventFilter(self)
        pl.addWidget(seg, 0, Qt.AlignmentFlag.AlignHCenter)

        box, lay = self._group("Players")
        self.team_box = box
        self.player_rows = []
        for i in range(11):
            row = QHBoxLayout()
            slot = QLabel(str(i + 1))
            slot.setFixedWidth(22)
            slot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            slot.setToolTip("Position in the lineup (1 = goalkeeper)")
            spin = QSpinBox()
            spin.setRange(0, 99)
            spin.setMinimumWidth(64)
            spin.valueChanged.connect(lambda v, k=i: self._on_row_number(k, v))
            edit = QLineEdit()
            edit.setPlaceholderText("Name (optional)")
            edit.textEdited.connect(lambda t, k=i: self._on_row_name(k, t))
            row.addWidget(slot)
            row.addWidget(spin)
            row.addWidget(edit, 1)
            lay.addLayout(row)
            self.player_rows.append((slot, spin, edit))
        b1 = QPushButton("Reset numbers 1–11")
        b1.clicked.connect(self.reset_numbers)
        lay.addWidget(b1)
        pl.addWidget(box)
        pl.addStretch(1)
        self._add_drawer("team", "Team", self._scroll(page))

        # ===== Menu: Animation =====
        page, pl = self._page()
        box, lay = self._group("Scenes")
        lay.addWidget(self._hint("Position players → “Record” → next position → … "
                                 "Playback, forward/back and the timeline are in the video bar at the bottom."))
        self.scene_list = QListWidget()
        self.scene_list.setFixedHeight(130)
        self.scene_list.currentRowChanged.connect(self._on_scene_selected)
        lay.addWidget(self.scene_list)
        row = QHBoxLayout()
        for text, fn in (("Record", self.add_scene),
                         ("Overwrite", self.overwrite_scene),
                         ("Delete", self.delete_scene)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Seconds/step"))
        self.step_spin = QDoubleSpinBox()
        self.step_spin.setRange(0.3, 10.0)
        self.step_spin.setSingleStep(0.25)
        self.step_spin.setValue(1.5)
        self.step_spin.valueChanged.connect(lambda _v: self._update_time())
        row.addWidget(self.step_spin)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.chk_ease = QCheckBox("Smooth")
        self.chk_ease.setChecked(True)
        self.chk_trails = QCheckBox("Run paths")
        self.chk_trails.toggled.connect(self.rebuild_trails)
        for c in (self.chk_ease, self.chk_trails):
            row.addWidget(c)
        lay.addLayout(row)
        self.chk_anim_draw = QCheckBox("Include drawings in scenes")
        self.chk_anim_draw.setChecked(True)
        lay.addWidget(self.chk_anim_draw)
        lay.addWidget(self._hint("Zones, paths and texts belong to the scene in which you record them: "
                                 "during playback they fade in and out smoothly, runs and passes are drawn "
                                 "progressively, moved drawings glide to their new place. "
                                 "Apply changes to a scene with “Overwrite”."))
        pl.addWidget(box)
        pl.addStretch(1)
        self._add_drawer("play", "Animation", self._scroll(page))

        # ===== Menu: Notes =====
        page, pl = self._page()
        box, lay = self._group("Tactics notes")
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes on tactics, training plan, opponent analysis … "
                                      "Saved together with the tactic (File → Save).")
        lay.addWidget(self.notes, 1)
        lay.addWidget(self._hint("Notes directly on the board: “T” tool in the draw bar below the board."))
        pl.addWidget(box, 1)
        self._add_drawer("notes", "Notes", page)

        # ===== Menu: File =====
        page, pl = self._page()
        box, lay = self._group("Project")
        file_acts = []
        for text, fn, key in (("New", self.new_board, "Ctrl+N"),
                              ("Open …", self.load_file, "Ctrl+O"),
                              ("Save …", self.save_file, "Ctrl+S"),
                              ("Export as PNG …", self.export_png, "Ctrl+E")):
            a = QAction(text, self)
            a.setShortcut(key)
            a.triggered.connect(fn)
            self.addAction(a)
            b = QPushButton(text)
            b.setToolTip(key)
            b.clicked.connect(fn)
            lay.addWidget(b)
        pl.addWidget(box)
        box, lay = self._group("Drawings")
        b = QPushButton("Delete all drawings")
        b.clicked.connect(self.ask_clear_drawings)
        lay.addWidget(b)
        pl.addWidget(box)
        pl.addStretch(1)
        self._add_drawer("file", "File", self._scroll(page))

        # ===== Menu: Program =====
        page, pl = self._page()
        a_quit = QAction("Quit", self)
        a_quit.setShortcut("Ctrl+Q")
        a_quit.triggered.connect(self.close)
        self.addAction(a_quit)

        box, lay = self._group("Board")
        self.btn_perf = QPushButton("Performance mode")
        self.btn_perf.setCheckable(True)
        self.btn_perf.setToolTip("Opaque menus and no animations – faster on weaker computers")
        self.btn_perf.toggled.connect(self.set_performance_mode)
        lay.addWidget(self.btn_perf)
        row = QHBoxLayout()
        row.setSpacing(8)
        b = QPushButton("Ball to center")
        b.clicked.connect(lambda: self.ball.setPos(0, 0))
        row.addWidget(b)
        b = QPushButton("Hide menus")
        b.setToolTip("Ctrl+H – show only the board and players")
        b.clicked.connect(self.toggle_ui_hidden)
        row.addWidget(b)
        lay.addLayout(row)
        pl.addWidget(box)

        box, lay = self._group("Magnet")
        self.chk_magnet = QCheckBox("Magnet enabled (Shift while dragging = off)")
        self.chk_magnet.setChecked(True)
        self.chk_magnet.toggled.connect(self._set_magnet)
        lay.addWidget(self.chk_magnet)
        row, self.mag_slider = self._slider_row("Strength", 0, 45, 22, self._set_magnet_strength)
        lay.addLayout(row)
        self.chk_zone_snap = QCheckBox("Snap to zone centers (when zones are visible)")
        self.chk_zone_snap.setChecked(True)
        lay.addWidget(self.chk_zone_snap)
        lay.addWidget(self._hint("The magnet aligns players with teammates, keeps equal spacing, "
                                 "prevents overlaps and pulls the ball towards players."))
        pl.addWidget(box)

        pl.addStretch(1)
        box, lay = self._group("Help")
        row = QHBoxLayout()
        row.setSpacing(8)
        b = QPushButton("Keyboard shortcuts …")
        b.setToolTip("F1")
        b.clicked.connect(self.show_shortcuts)
        row.addWidget(b)
        lay.addLayout(row)
        pl.addWidget(box)
        QShortcut(QKeySequence(Qt.Key.Key_F1), self, activated=self.show_shortcuts) 
        b = QPushButton("Quit")
        b.setToolTip("Ctrl+Q")
        b.clicked.connect(self.close)
        pl.addWidget(b)
        self._add_drawer("program", "Program", self._scroll(page))

        # ===== Menu: Formation =====
        page, pl = self._page()
        box, lay = self._group("Teams")
        for label, attr, team, default in (("Home (white)", "home_combo", "H", "4-3-3"),
                                           ("Away (orange)", "away_combo", "A", "4-4-2")):
            lay.addWidget(QLabel(label))
            cb = QComboBox()
            cb.addItems(FORMATIONS.keys())
            cb.setMaxVisibleItems(24)
            cb.setCurrentText(default)
            cb.setToolTip(f"Formation {label}")
            cb.textActivated.connect(lambda name, t=team: self.apply_formation(t, name))
            setattr(self, attr, cb)
            lay.addWidget(cb)
        lay.addWidget(self._hint("The formation is only a starting point – afterwards you can move every player "
                                 "freely."))
        pl.addWidget(box)
        pl.addStretch(1)
        self._add_drawer("formation", "Formation", self._scroll(page))

        # ===== Menu: View – part 2 (zones, lines, pass network, display) =====
        def chk(text, handler, layout, checked=False):
            act = QAction(text, self)
            act.setCheckable(True)
            act.setChecked(checked)
            act.toggled.connect(handler)
            cbx = QCheckBox(text)
            cbx.setChecked(checked)
            cbx.toggled.connect(act.setChecked)

            def sync(v, c=cbx):
                c.blockSignals(True)
                c.setChecked(v)
                c.blockSignals(False)
            act.toggled.connect(sync)
            if layout is not None:
                layout.addWidget(cbx)
            return act

        page, pl = view_page, view_pl
        # Zones & thirds are toggled below in the “Zones” bar – only the actions behind them live here
        self.act_zones = chk("20 zones (Guardiola)", lambda v: self._set_overlay(self.zone_overlay, v), None)
        self.act_thirds = chk("3 thirds", lambda v: self._set_overlay(self.thirds_overlay, v), None)
        self.side_combo = QComboBox()
        self.side_combo.addItems(["Home attacks to the right", "Away attacks to the left"])
        self.side_combo.currentIndexChanged.connect(self._set_overlay_side)
        box, lay = self._group("Lines")
        self.act_lines_h = chk("Connect home lines", lambda v: self._set_lines("H", v), lay)
        self.act_lines_a = chk("Connect away lines", lambda v: self._set_lines("A", v), lay)
        self.act_cross = chk("Staggering between the lines", self._set_cross, lay)
        row = QHBoxLayout()
        lab = QLabel("Tolerance")
        lab.setFixedWidth(78)
        self.tol_slider = QSlider(Qt.Orientation.Horizontal)
        self.tol_slider.setRange(2, 15)
        self.tol_slider.setValue(5)
        self.tol_label = QLabel("5 m")
        self.tol_label.setFixedWidth(36)
        self.tol_slider.valueChanged.connect(self._set_line_tol)
        row.addWidget(lab)
        row.addWidget(self.tol_slider)
        row.addWidget(self.tol_label)
        lay.addLayout(row)
        lay.addWidget(self._hint("Players are connected if they are offset in depth by at most this much. "
                                 "Smaller = stricter, larger = more generous."))
        pl.addWidget(box)
        box, lay = self._group("Pass network")
        self.act_net = chk("Show pass network", lambda v: self._set_overlay(self.pass_net, v), lay)
        lay.addWidget(self._hint("Connects teammates whose direct passing lane is free – no opponent closer than "
                                 "4 m to the line, at most 28 m apart. Updates while you drag players."))
        pl.addWidget(box)
        box, lay = self._group("Display")
        self.act_home = chk("Home players", lambda v: self._show_team("H", v), lay, True)
        self.act_away = chk("Away players", lambda v: self._show_team("A", v), lay, True)
        self.act_num = chk("Player numbers", self._show_numbers, lay, True)
        self.act_names = chk("Player names", self._show_names, lay, True)
        self.act_draw = chk("Drawings", self._show_drawings, lay, True)
        pl.addWidget(box)
        pl.addStretch(1)
        self._add_drawer("view", "View", self._scroll(page))

        # ===== Drawer (slides in smoothly next to the board) =====
        self._pills = getattr(self, "_pills", {})
        self.drawer = QFrame(self.view)
        self.drawer.setObjectName("drawer")
        self.drawer.setFixedWidth(0)
        self.drawer_inner = QWidget(self.drawer)
        self.drawer_inner.setFixedWidth(DRAWER_W)
        dl = QVBoxLayout(self.drawer_inner)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(0)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(14, 10, 8, 4)
        self.drawer_title = QLabel("")
        self.drawer_title.setStyleSheet(f"color:{ORANGE}; font-size:16px; font-weight:bold;")
        close = QToolButton()
        close.setIcon(make_icon("close"))
        close.setIconSize(QSize(16, 16))
        close.setObjectName("flat")
        close.clicked.connect(self.close_drawer)
        hl.addWidget(self.drawer_title, 1)
        hl.addWidget(close)
        dl.addWidget(head)
        dl.addWidget(self.stack, 1)
        self.drawer.installEventFilter(self)
        self.drawer.hide()
        self._drawer_p = 0.0
        self._drawer_fx = None
        self._drawer_anim = QVariantAnimation(self)
        self._drawer_anim.valueChanged.connect(self._drawer_tick)
        self._drawer_anim.finished.connect(self._drawer_done)
        self._swap_anim = QVariantAnimation(self)
        self._swap_anim.setDuration(260)
        self._swap_anim.setStartValue(0.0)
        self._swap_anim.setEndValue(1.0)
        self._swap_anim.valueChanged.connect(self._swap_tick)
        self._swap_anim.finished.connect(self._swap_done)
        self._swap_key = None
        self._swap_switched = True

        def vsep():
            f = QFrame()
            f.setFixedSize(1, 26)
            f.setStyleSheet("QFrame{background:#333c49;border:0;}")
            return f

        def fbutton(text, icon_name, tip, checkable=False, mode=False):
            if mode:
                b = QToolButton()
                b.setObjectName("modebtn")
                b.setText(text)
                b.setIcon(make_icon(icon_name))
                b.setIconSize(QSize(18, 18))
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                b = AnimButton(text, icon_name, inner=True)
            b.setCheckable(checkable)
            b.setToolTip(tip)
            return b

        # ===== floating bar at the bottom: either Draw OR Zones (collapsible) =====
        self.draw_bar = QFrame(self.view)
        self.draw_bar.setObjectName("floatbar")
        dl = QHBoxLayout(self.draw_bar)
        dl.setContentsMargins(7, 6, 7, 6)
        dl.setSpacing(5)
        self.mode_btns = {}
        for key, text, icon_name, tip in (("draw", "Draw", "pen", "Show / hide drawing tools"),
                                          ("zones", "Zones", "grid", "Show / hide zones and thirds")):
            b = fbutton(text, icon_name, tip, True, mode=True)
            b.clicked.connect(lambda _=False, k=key: self.toggle_bar_mode(k))
            self.mode_btns[key] = b
            dl.addWidget(b)
        self.mode_btns["draw"].setChecked(True)
        self._bar_mode = "draw"
        self.mode_pill = Pill(self.draw_bar, list(self.mode_btns.values()))
        self._pills[self.draw_bar] = self.mode_pill
        self.draw_bar.installEventFilter(self)
        self._rail_p = {"draw": 1.0, "zones": 0.0}
        self._rail_from = dict(self._rail_p)
        self._rail_to = dict(self._rail_p)
        self._rail_anim = QVariantAnimation(self)
        self._rail_anim.setStartValue(0.0)
        self._rail_anim.setEndValue(1.0)
        self._rail_anim.setDuration(300)
        self._rail_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._rail_anim.valueChanged.connect(self._rail_tick)
        self._rail_anim.finished.connect(lambda: self._rail_tick(1.0))

        # --- Drawing tools
        self.tool_rail = QWidget()
        tl = QHBoxLayout(self.tool_rail)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(4)
        tl.addWidget(vsep())
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        tools = [("cursor", "move", "Move / select"), ("rect", "rect", "Zone: rectangle"),
                 ("ellipse", "ellipse", "Zone: ellipse"), ("free", "free", "Zone: freehand"),
                 ("run", "run", "Run path"), ("pass", "pass", "Pass"), ("text", "text", "Text note on the board")]
        for icon_name, key, tip in tools:
            b = QToolButton()
            b.setObjectName("tool")
            b.setIcon(make_icon(icon_name))
            b.setIconSize(QSize(22, 22))
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setChecked(key == "move")
            b.setFixedSize(40, 34)
            b.clicked.connect(lambda _=False, k=key: self._set_tool(k))
            self.tool_group.addButton(b)
            tl.addWidget(b)
        self.tool_pill = Pill(self.tool_rail, list(self.tool_group.buttons()))
        self.tool_group.buttonClicked.connect(lambda _b: self.tool_pill.sync())
        self._pills[self.tool_rail] = self.tool_pill
        self.tool_rail.installEventFilter(self)
        tl.addSpacing(3)
        tl.addWidget(vsep())
        tl.addSpacing(3)
        self.color_group = QButtonGroup(self)
        for i, col in enumerate(DRAW_COLORS):
            b = QPushButton()
            b.setCheckable(True)
            b.setFixedSize(22, 22)
            b.setToolTip("Drawing color")
            b.setStyleSheet(f"QPushButton{{background:{col};border:2px solid #14181e;border-radius:11px;padding:0;}}"
                            f"QPushButton:checked{{border:2px solid #ffffff;}}")
            b.setChecked(i == 0)
            b.clicked.connect(lambda _=False, c=col: setattr(self.view, "color", c))
            self.color_group.addButton(b)
            tl.addWidget(b)
        tl.addSpacing(3)
        tl.addWidget(vsep())
        tl.addSpacing(3)
        for text, icon_name, tip, fn in (("Undo", "undo", "Undo last drawing (Ctrl+Z)", self.undo_draw),
                                         ("Delete", "trash", "Delete selected drawing (Del)",
                                          self.delete_selected)):
            b = fbutton(text, icon_name, tip)
            b.clicked.connect(lambda _=False, f=fn: f())
            tl.addWidget(b)
        self.tool_clip = QWidget()
        self.tool_rail.setParent(self.tool_clip)
        dl.addWidget(self.tool_clip)

        # --- Zones
        self.zone_rail = QWidget()
        zl = QHBoxLayout(self.zone_rail)
        zl.setContentsMargins(0, 0, 0, 0)
        zl.setSpacing(5)
        zl.addWidget(vsep())

        def link(btn, act):
            btn.toggled.connect(act.setChecked)

            def sync(v, b=btn):
                b.blockSignals(True)
                b.setChecked(v)
                b.blockSignals(False)
            act.toggled.connect(sync)

        for text, act, tip in (("20 zones", self.act_zones, "Toggle 20 zones (Guardiola)"),
                               ("3 thirds", self.act_thirds, "Toggle the three thirds")):
            b = fbutton(text, None, tip, True)
            b.setChecked(act.isChecked())
            link(b, act)
            zl.addWidget(b)
        self.zb_side = fbutton("View: home", None, "Show zones from the home or away team's point of view")
        self.zb_side.clicked.connect(lambda: self.side_combo.setCurrentIndex(1 - self.side_combo.currentIndex()))
        self.side_combo.currentIndexChanged.connect(self._side_text)
        zl.addWidget(self.zb_side)
        self.zone_clip = QWidget()
        self.zone_rail.setParent(self.zone_clip)
        dl.addWidget(self.zone_clip)
        self._apply_rails()

        self.zone_info = QLabel("", self.view)
        self.zone_info.setObjectName("chip")
        self.zone_info.hide()

        # ===== Player: floats at the bottom, only visible when needed =====
        self.transport = QFrame(self.view)
        self.transport.setObjectName("transport")
        self.transport.setFixedHeight(54)
        tr = QHBoxLayout(self.transport)
        tr.setContentsMargins(12, 6, 12, 6)
        tr.setSpacing(6)

        def tbtn(icon_name, tip, fn, checkable=False, name="tbtn", text="", dark_icon=False):
            b = QToolButton()
            b.setObjectName(name)
            b.setIcon(make_icon(icon_name, "#14181e", "#14181e") if dark_icon else make_icon(icon_name))
            b.setIconSize(QSize(18, 18))
            if text:
                b.setText(text)
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            if fn:
                b.clicked.connect(lambda _=False, f=fn: f())
            return b

        tr.addWidget(tbtn("first", "To start", lambda: self.seek_to(0)))
        tr.addWidget(tbtn("prev", "Previous scene (left arrow)", lambda: self.step_scene(-1)))
        self.play_btn = tbtn("play", "Play / pause (space)", self.toggle_play, name="tplay", dark_icon=True)
        self.play_btn.setFixedSize(40, 34)
        self.icon_play = make_icon("play", "#14181e", "#14181e")
        self.icon_pause = make_icon("pause", "#14181e", "#14181e")
        tr.addWidget(self.play_btn)
        tr.addWidget(tbtn("next", "Next scene (right arrow)", lambda: self.step_scene(1)))
        self.timeline = SceneSlider()
        self.timeline.setRange(0, 0)
        self.timeline.valueChanged.connect(self._on_timeline)
        tr.addWidget(self.timeline, 1)
        self.time_label = QLabel("No animation yet")
        self.time_label.setMinimumWidth(130)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tr.addWidget(self.time_label)
        self.speed_combo = QComboBox()
        for txt, val in (("0,5×", 0.5), ("1×", 1.0), ("1,5×", 1.5), ("2×", 2.0)):
            self.speed_combo.addItem(txt, val)
        self.speed_combo.setCurrentIndex(1)
        self.speed_combo.setToolTip("Playback speed")
        self.speed_combo.activated.connect(self._on_speed)
        tr.addWidget(self.speed_combo)
        self.chk_loop = tbtn("loop", "Repeat", None, checkable=True)
        tr.addWidget(self.chk_loop)
        tr.addWidget(tbtn("record", "Record current lineup as a new scene", self.add_scene, text=" Scene"))
        self.transport.hide()
        self._player_p = 0.0
        self._player_anim = QVariantAnimation(self)
        self._player_anim.valueChanged.connect(self._player_tick)
        self._player_anim.finished.connect(self._player_done)

        # ===== Menu on the right: its own rounded box =====
        self.nav_rail = QFrame(self.view)
        self.nav_rail.setObjectName("rail")
        self.nav_rail.setFixedWidth(RAIL_W)
        nl = QVBoxLayout(self.nav_rail)
        nl.setContentsMargins(8, 10, 8, 10)
        nl.setSpacing(6)
        self.nav_btns = {}
        for key, label in (("file", "File"), ("formation", "Formation"), ("view", "View"),
                           ("team", "Team"), ("play", "Animation"),
                           ("notes", "Notes"), ("program", "Program")):
            b = QToolButton()
            b.setObjectName("railbtn")
            b.setText(label)
            b.setCheckable(True)
            b.setFixedSize(RAIL_W - 18, 40)
            b.clicked.connect(lambda _=False, k=key: self.toggle_drawer(k))
            self.nav_btns[key] = b
            nl.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
        self.nav_pill = Pill(self.nav_rail, list(self.nav_btns.values()))
        self._pills[self.nav_rail] = self.nav_pill
        self.nav_rail.installEventFilter(self)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.view, 1)
        self.view.installEventFilter(self)
        self.setCentralWidget(central)

        QShortcut(QKeySequence("Ctrl+H"), self, activated=self.toggle_ui_hidden)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self.toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, activated=self.delete_selected)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo_draw)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.escape)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self.step_scene(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self.step_scene(1)) 
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, activated=self.toggle_fullscreen) 

    # ---- floating elements on the board ------------------------------------------------
    def set_performance_mode(self, on):
        """Performance mode: menus without transparency, no menu/marker animations."""
        PERF["on"] = bool(on)
        self._drawer_anim.stop()
        self._swap_anim.stop()
        self._drawer_fx_off()
        for a in (self._rail_anim, self._player_anim):
            if a.state() == QAbstractAnimation.State.Running:
                a.stop()
        self._rail_p = {k: round(v) * 1.0 for k, v in self._rail_p.items()}
        self._apply_rails()
        self._player_p = 1.0 if self._player_p > 0.5 else 0.0
        self._player_done()
        for b in self.findChildren(AnimButton):
            b.update()
        for p in Popover.instances:
            p.close_pop()
        QApplication.instance().setStyleSheet(STYLE_PERF if on else STYLE)
        for p in self._pills.values():
            p.sync(False)
        if self.current_drawer is not None:
            self._drawer_p = 1.0
            self._drawer_tick(1.0)
        self._layout_overlays()

    def toggle_bar_mode(self, mode):
        self.set_bar_mode(None if self._bar_mode == mode else mode)

    def _side_text(self, i):
        self.zb_side.setText("View: home" if i == 0 else "View: away")
        self._layout_overlays()

    def _apply_rails(self):
        """The bars are revealed from left to right (content keeps its full size, only the visible clip grows)."""
        for key, rail, clip in (("draw", self.tool_rail, self.tool_clip), ("zones", self.zone_rail, self.zone_clip)):
            p = self._rail_p[key]
            if p <= 0.003:
                clip.hide()
                continue
            hint = rail.layout().sizeHint()
            rail.setFixedSize(hint.width(), hint.height())
            clip.setFixedSize(max(1, int(round(hint.width() * min(1.0, p)))), hint.height())
            rail.move(0, 0)
            rail.show()
            clip.show()
        self.draw_bar.layout().activate()

    def _rail_tick(self, v):
        v = float(v)
        for k in self._rail_p:
            a, b = self._rail_from[k], self._rail_to[k]
            self._rail_p[k] = a + (b - a) * v
        self._apply_rails()
        self._layout_overlays()

    def _run_bar_anim(self, target):
        self._rail_anim.stop()
        self._rail_from = dict(self._rail_p)
        self._rail_to = dict(target)
        if PERF["on"] or not self.isVisible():
            self._rail_tick(1.0)
        else:
            self._rail_anim.start()

    def set_bar_mode(self, mode):
        """Bottom bar: 'draw' (drawing tools), 'zones' (zones/thirds) or None (collapsed)."""
        if self._bar_mode == "draw" and mode != "draw":
            self.tool_group.buttons()[0].click()  # "Move" tool, so nothing draws invisibly
        self._bar_mode = mode
        for k, b in self.mode_btns.items():
            b.setChecked(k == mode)
        self.mode_pill.sync()
        self._run_bar_anim({"draw": 1.0 if mode == "draw" else 0.0, "zones": 1.0 if mode == "zones" else 0.0})
        if mode == "draw":
            QTimer.singleShot(0, lambda: self.tool_pill.sync(False))

    def set_player_visible(self, on):
        on = bool(on)
        if self.btn_anim.isChecked() != on:
            self.btn_anim.blockSignals(True)
            self.btn_anim.setChecked(on)
            self.btn_anim.blockSignals(False)
        if not on:
            self._stop_play()
        self._player_anim.stop()
        target = 1.0 if on else 0.0
        if PERF["on"] or not self.isVisible():
            self._player_p = target
            self._player_done()
            return
        self._player_anim.setStartValue(float(self._player_p))
        self._player_anim.setEndValue(target)
        self._player_anim.setDuration(int(360 * max(0.35, abs(target - self._player_p))))
        self._player_anim.setEasingCurve(QEasingCurve.Type.OutCubic if on else QEasingCurve.Type.InCubic)
        self.transport.show()
        self._player_anim.start()

    def _player_tick(self, v):
        self._player_p = float(v)
        self._layout_overlays()

    def _player_done(self):
        want = self._player_p > 0.5
        if self.btn_anim.isChecked() != want:
            self.btn_anim.blockSignals(True)
            self.btn_anim.setChecked(want)
            self.btn_anim.blockSignals(False)
        self.transport.setVisible(self._player_p > 0.001)
        self._layout_overlays()

    def _set_play_icon(self, playing):
        self.play_btn.setIcon(self.icon_pause if playing else self.icon_play)

    def _layout_overlays(self):
        if not getattr(self, "_ui_ready", False):
            return
        M = 12
        vw, vh = self.view.width(), self.view.height()
        if self._ui_hidden and self._fade_pending:
            return                      # fade-out animation still running: only then really hide
        if self._ui_hidden:
            for w in self._ui_widgets():
                w.hide()
            self.view.set_insets(0, 0, 0, 0)
            if self._hint_on:
                self.hide_hint.adjustSize()
                self.hide_hint.move((vw - self.hide_hint.width()) // 2, vh - self.hide_hint.height() - 18)
                self.hide_hint.show()
                self.hide_hint.raise_()
            else:
                self.hide_hint.hide()
            return
        self.btn_menu.adjustSize()
        self.btn_menu.move(vw - self.btn_menu.width() - M, M)
        top = M + self.btn_menu.height() + 8
        right = vw - M
        if self.nav_rail.isVisibleTo(self.view):
            self.nav_rail.adjustSize()
            self.nav_rail.move(vw - M - self.nav_rail.width(), top)
            right = self.nav_rail.x() - 8
        dw = self.drawer.width() if self.drawer.isVisibleTo(self.view) else 0
        left_edge = right
        if dw > 0:
            left_edge = right - dw - 8
        # center: without an open menu exactly the window center, with a menu gliding to the center of the free area
        k = min(1.0, dw / float(DRAWER_W)) if dw > 0 else 0.0
        cx = vw / 2.0 + ((M + left_edge) / 2.0 - vw / 2.0) * k

        # top left: Magnet, Animation
        x = M
        for b in (self.btn_magnet, self.btn_anim):
            b.adjustSize()
            b.move(x, M)
            x += b.width() + 8
        # top center: 2D/3D + Perspective as a group, vertically on one line
        self.seg_box.adjustSize()
        self.btn_persp.adjustSize()
        gap = 8
        cw = self.seg_box.width() + gap + self.btn_persp.width()
        hh = max(self.seg_box.height(), self.btn_persp.height(), self.btn_magnet.height())
        sx = int(round(cx - cw / 2.0))
        sx = max(x + 8, min(sx, self.btn_menu.x() - 8 - cw))
        self.seg_box.move(sx, M + (hh - self.seg_box.height()) // 2)
        self.btn_persp.move(sx + self.seg_box.width() + gap, M + (hh - self.btn_persp.height()) // 2)
        self.zone_info.adjustSize()
        self.zone_info.move(M, M + self.btn_magnet.height() + 8)

        # bottom: player (slides in/out), above it the draw / zones bar
        by = vh - M
        pp = getattr(self, "_player_p", 0.0)
        if pp > 0.001:
            th = self.transport.height()
            tw = max(620, min(900, int(left_edge - 2 * M)))
            self.transport.setFixedWidth(tw)
            tx = max(M, int(round(cx - tw / 2.0)))
            self.transport.move(tx, int(by - th + (1.0 - pp) * (th + M + 8)))
            by -= int((th + 8) * pp)
        self.draw_bar.layout().activate()
        self.draw_bar.adjustSize()
        bw = self.draw_bar.width()
        bx = int(round(cx - bw / 2.0))
        bx = max(M, min(bx, int(left_edge) - bw))
        self.draw_bar.move(max(M, bx), by - self.draw_bar.height())
        # Menu: if the bottom bars reach under the menu, it ends above them (nothing is covered)
        if dw > 0:
            bottom = vh - M
            for w in (self.draw_bar, self.transport):
                if w.isVisibleTo(self.view) and w.x() + w.width() > left_edge + 1:
                    bottom = min(bottom, w.y() - 8)
            self.drawer.setGeometry(right - dw, top, dw, max(120, bottom - top))
        for w in (self.zone_info, self.draw_bar, self.transport, self.btn_magnet, self.btn_anim,
                  self.seg_box, self.btn_persp, self.nav_rail, self.btn_menu, self.drawer):
            w.raise_()
        # fit the board into the free area (menu on the right, bars at the bottom)
        inset_r = (vw - left_edge) * k if dw > 0 else 0.0
        inset_b = max(0.0, vh - self.draw_bar.y() - 30) if self.draw_bar.y() > 0 else 0.0
        self.view.set_insets(0, 0, inset_r, min(inset_b, vh * 0.4))

    def _end_hide_hint(self):
        self._hint_on = False
        self.hide_hint.hide()

    def _ui_widgets(self):
        return [self.zone_info, self.draw_bar, self.transport, self.btn_magnet, self.btn_anim,
                self.seg_box, self.btn_persp, self.nav_rail, self.btn_menu, self.drawer, self.toast]

    def _fade_widgets(self, widgets, start, end, on_done=None):
        """Fades widgets in (start=0, end=1) or out (start=1, end=0) smoothly."""
        self._stop_fade()
        widgets = [w for w in widgets if w.graphicsEffect() is None]
        if PERF["on"] or not widgets:          # performance mode: no animation
            if on_done:
                on_done()
            return
        group = QParallelAnimationGroup(self)
        for w in widgets:
            fx = QGraphicsOpacityEffect(w)
            fx.setOpacity(start)
            w.setGraphicsEffect(fx)
            a = QPropertyAnimation(fx, b"opacity", group)
            a.setStartValue(start)
            a.setEndValue(end)
            a.setDuration(220)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(a)

        def finished():
            self._cleanup_fade()
            if on_done:
                on_done()

        self._fade_group = group
        self._fade_targets = widgets
        group.finished.connect(finished)
        group.start()

    def _cleanup_fade(self):
        for w in self._fade_targets:
            w.setGraphicsEffect(None)       # removes (and deletes) the opacity effect again
        self._fade_targets = []
        self._fade_group = None

    def _stop_fade(self):
        if self._fade_group is not None:
            self._fade_group.stop()
            self._cleanup_fade() 

    def toggle_fullscreen(self):
        """F11: switch between full screen and the maximized window."""
        if self.isFullScreen():
            self.showMaximized()
        else:
            self.showFullScreen() 

    def toggle_ui_hidden(self):
        """Ctrl+H: show / hide all menus, bars and switches with an animation – only the board with the players remains."""
        if not self._ui_hidden:
            for p in Popover.instances:
                p.close_pop()
            if self.current_drawer is not None:          # close an open menu immediately
                self.current_drawer = None
                for b in self.nav_btns.values():
                    b.setChecked(False)
                self.nav_pill.sync(False)
                self._swap_anim.stop()
                self._drawer_anim.stop()
                self._drawer_p = 0.0
                self.drawer.setFixedWidth(0)
                self._drawer_done()
            self._ui_saved = [w for w in self._ui_widgets() if w.isVisibleTo(self.view) and w is not self.drawer]
            self._ui_hidden = True
            self._hint_on = True               # the hint stays until it is closed with the small x
            self._fade_pending = True

            def after_fade_out():
                self._fade_pending = False
                self._layout_overlays()
                if self._hint_on:
                    self._fade_widgets([self.hide_hint], 0.0, 1.0)

            self._fade_widgets(self._ui_saved, 1.0, 0.0, after_fade_out)
        else:
            self._stop_fade()
            self._fade_pending = False
            self._ui_hidden = False
            self._hint_on = False
            self.hide_hint.hide()
            restored = [w for w in self._ui_saved if w is not self.toast]
            self._ui_saved = []
            self._fade_widgets(restored, 0.0, 1.0)   # sets the widgets to invisible immediately, then fades in
            for w in restored:
                w.show()
            QTimer.singleShot(0, lambda: [p.sync(False) for p in self._pills.values()])
        self._layout_overlays()

    def _flash(self, msg):
        """Short message at the top of the board (replaces the status bar)."""
        if self._ui_hidden:
            return
        self.toast.setText(msg)
        self.toast.adjustSize()
        self.toast.move((self.view.width() - self.toast.width()) // 2, 70)
        self.toast.show()
        self.toast.raise_()
        self._toast_timer.start(3500)

    def show_shortcuts(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard shortcuts")
        dlg.resize(660, 700)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(14, 14, 14, 14)
        sections = [
            ("Board", [
                ("Drag with the mouse", "Move a player or the ball"),
                ("Shift while dragging", "Turn the magnet off for this movement only"),
                ("Double-click on a player", "Change number"),
                ("Right-click on a player", "Change number or name"),
                ("Mouse wheel", "Zoom"),
                ("Esc", "Close the open selection or the open menu"),
            ]),
            ("Draw", [
                ("Drag in draw mode", "Draw a zone, run path or pass"),
                ("Ctrl + Z", "Undo last drawing"),
                ("Del", "Delete selected drawing"),
            ]),
            ("Animation", [
                ("Space", "Play / pause"),
                ("Left / right arrow", "Previous / next scene"),
            ]),
            ("File and program", [
                ("Ctrl + N", "New board"),
                ("Ctrl + O", "Open tactic"),
                ("Ctrl + S", "Save tactic"),
                ("Ctrl + E", "Export as PNG (board only)"),
                ("Ctrl + H", "Hide / show all menus and bars"),
                ("Ctrl + Q", "Quit program"),
                ("F1", "Show these keyboard shortcuts"),
                ("F11", "Toggle full screen mode"), 
            ]),
            ("Switches at the top", [
                ("Magnet", "Turn the magnet permanently on or off"),
                ("Animation", "Show or hide the player for recording and playing back scenes"),
                ("2D / 3D", "Flat or spatial display"),
                ("Perspective", "Choose the viewing angle (a check mark shows the active one)"),
                ("Menu", "Show or hide the menu bar with all settings"),
                ("Draw / Zones", "Expand or collapse the bottom bar for tools or zone overlays"),
            ]),
        ]
        html = ["<style>td{padding:5px 8px;} th{color:%s;text-align:left;padding:12px 8px 4px 8px;font-size:15px;}"
                "td.k{color:#14181e;background:%s;font-weight:bold;}</style>" % (ORANGE, "#e9e6e1"),
                "<table cellspacing='0' width='100%'>"]
        for title, rows in sections:
            html.append(f"<tr><th colspan='2'>{title}</th></tr>")
            for key, text in rows:
                html.append(f"<tr><td width='36%'><b>{key}</b></td><td>{text}</td></tr>")
        html.append("</table>")
        view = QTextBrowser()
        view.setOpenLinks(False)
        view.setHtml("".join(html))
        lay.addWidget(view, 1)
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        lay.addWidget(close)
        dlg.exec()

    def _on_hover(self, p):
        txt = ""
        if self.zone_overlay.isVisible() and p is not None:
            h = self.zone_overlay.set_hover(p)
            if h:
                txt = zone_text(self.zone_overlay.side, *h)
        else:
            self.zone_overlay.set_hover(None)
        if txt != self.zone_info.text():
            self.zone_info.setText(txt)
            self.zone_info.setVisible(bool(txt) and not self._ui_hidden)
            self._layout_overlays()

    def set_menu_visible(self, on):
        if not on:
            self.close_drawer()
        self.nav_rail.setVisible(on)
        self._layout_overlays()
        if on:
            QTimer.singleShot(0, lambda: self.nav_pill.sync(False))

    def _scroll(self, page):
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(page)
        sc.setFrameShape(QScrollArea.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return sc

    # ---- Window events (drawer layout, markers) -------------------------------
    def eventFilter(self, obj, ev):
        t = ev.type()
        if obj is getattr(self, "drawer", None):
            if t == QEvent.Type.Resize:
                self.drawer_inner.setGeometry(self.drawer.width() - DRAWER_W, 0, DRAWER_W, self.drawer.height())
        elif obj is getattr(self, "view", None):
            if t == QEvent.Type.Resize:
                self._layout_overlays()
        else:
            pill = getattr(self, "_pills", {}).get(obj)
            if (pill is not None and t in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest)
                    and pill.anim.state() != QAbstractAnimation.State.Running):
                pill.sync(False)
        return False

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, lambda: [p.sync(False) for p in self._pills.values()])
        QTimer.singleShot(0, self._layout_overlays)

    def escape(self):
        opened = [p for p in Popover.instances if p.isVisible()]
        if opened:
            for p in opened:
                p.close_pop()
        else:
            self.close_drawer()

    # ---- Open / close menus (with animation) ----------------------------------------
    def _drawer_fx_on(self):
        if self._drawer_fx is None:
            self._drawer_fx = QGraphicsOpacityEffect(self.drawer_inner)
            self.drawer_inner.setGraphicsEffect(self._drawer_fx)
        return self._drawer_fx

    def _drawer_fx_off(self):
        running = QAbstractAnimation.State.Running
        if (self._drawer_fx is not None and self._drawer_anim.state() != running
                and self._swap_anim.state() != running):
            self.drawer_inner.setGraphicsEffect(None)
            self._drawer_fx = None

    def _drawer_tick(self, p):
        self._drawer_p = float(p)
        self.drawer.setFixedWidth(int(round(DRAWER_W * self._drawer_p)))
        self._layout_overlays()
        if self._drawer_fx is not None:
            self._drawer_fx.setOpacity(max(0.0, min(1.0, (self._drawer_p - 0.15) / 0.85)))

    def _run_drawer(self, target):
        self._drawer_anim.stop()
        if PERF["on"]:
            self._drawer_p = float(target)
            self._drawer_tick(target)
            self._drawer_done()
            return
        self._drawer_fx_on()
        dist = abs(target - self._drawer_p)
        self._drawer_anim.setStartValue(float(self._drawer_p))
        self._drawer_anim.setEndValue(float(target))
        self._drawer_anim.setDuration(max(140, int((340 if target > 0 else 240) * dist)))
        self._drawer_anim.setEasingCurve(QEasingCurve.Type.OutQuart if target > 0
                                         else QEasingCurve.Type.InOutCubic)
        self._drawer_anim.start()

    def _drawer_done(self):
        if self._drawer_p <= 0.001:
            self.drawer.hide()
        else:
            self._drawer_tick(1.0)
        self._drawer_fx_off()

    def _swap_tick(self, v):
        v = float(v)
        if not self._swap_switched and v >= 0.5 and self._swap_key is not None:
            self._swap_switched = True
            self.stack.setCurrentIndex(self.drawer_keys.index(self._swap_key))
            self.drawer_title.setText(self.drawer_titles[self._swap_key])
        if self._drawer_fx is not None:
            self._drawer_fx.setOpacity(abs(2.0 * v - 1.0))

    def _swap_done(self):
        self._swap_tick(1.0)
        self._drawer_fx_off()

    def toggle_drawer(self, key):
        if self.current_drawer == key:
            self.close_drawer()
        else:
            self.open_drawer(key)

    def open_drawer(self, key):
        if key == "play":
            self.set_player_visible(True)
        idx = self.drawer_keys.index(key)
        if self.current_drawer is None:
            self.current_drawer = key
            self.stack.setCurrentIndex(idx)
            self.drawer_title.setText(self.drawer_titles[key])
            self.drawer.show()
            self._run_drawer(1.0)
        elif key != self.current_drawer:
            self.current_drawer = key
            if PERF["on"] or self._drawer_anim.state() == QAbstractAnimation.State.Running or self._drawer_p < 0.99:
                self.stack.setCurrentIndex(idx)
                self.drawer_title.setText(self.drawer_titles[key])
            else:
                self._swap_key = key
                self._swap_switched = False
                self._drawer_fx_on()
                self._swap_anim.stop()
                self._swap_anim.start()
        for k, b in self.nav_btns.items():
            b.setChecked(k == key)
        self.nav_pill.sync()

    def close_drawer(self):
        if self.current_drawer is None:
            return
        self.current_drawer = None
        for b in self.nav_btns.values():
            b.setChecked(False)
        self.nav_pill.sync()
        self._swap_anim.stop()
        self._run_drawer(0.0)

    # ---- Floating individual switches at the top ---------------------------------------------------
    def _seg_set(self, btn, v):
        btn.setChecked(v)
        if v:
            self.seg_pill.sync()

    def _persp_items(self):
        if self.mode_flat:
            head = "2D orientation"
            items = [("Landscape – home attacks to the right", 0, 0),
                     ("Landscape – away attacks to the right", 0, 180),
                     ("Portrait – home attacks upwards", 0, -90),
                     ("Portrait – away attacks upwards", 0, 90)]
        else:
            head = "3D perspective"
            items = [("Top view", 0, 0), ("3D angled", 50, 0), ("Grandstand low", 64, 0),
                     ("Opposite side", 55, 180), ("Behind the goal (home)", 58, -90),
                     ("Behind the goal (away)", 58, 90), ("Diagonal left", 55, -40),
                     ("Diagonal right", 55, 40)]
        rows = [("title", head)]
        ct = float(self.view.tilt)
        cr = float(self.view.rotz) % 360.0
        for name, tilt, rot in items:
            dr = abs((cr - (rot % 360.0) + 180.0) % 360.0 - 180.0)
            active = abs(ct - tilt) < 2.0 and dr < 2.0
            rows.append(("choice", name, lambda t=tilt, r=rot: self.animate_view(t, r, 1.0), active))
        return rows

    def _rebuild_view_menu(self):
        pass  # the perspective popover is rebuilt from scratch every time it opens

    def _top_button(self, text, icon_name, tip, checkable=False):
        b = AnimButton(text, icon_name, self.view)
        b.setCheckable(checkable)
        b.setToolTip(tip)
        return b

    def _build_toolbar(self):
        """No bar any more: Magnet, Animation, 2D/3D, Perspective and Menu float individually on the board."""
        # --- Magnet (Shift while dragging turns it off only briefly)
        self.btn_magnet = self._top_button(
            "Magnet", "magnet", "Magnet on/off – Shift while dragging turns it off only briefly", True)
        self.btn_magnet.setChecked(True)
        self.btn_magnet.toggled.connect(self._toolbar_magnet)

        # --- Animation (show / hide player)
        self.btn_anim = self._top_button("Animation", "film", "Show / hide animation player", True)
        self.btn_anim.toggled.connect(self.set_player_visible)

        # --- 2D / 3D as a sliding switch
        self._seg_actgrp = QActionGroup(self)
        self._seg_actgrp.setExclusive(True)
        self.act_2d = QAction("2D", self)
        self.act_3d = QAction("3D", self)
        for a, tip in ((self.act_2d, "Flat 2D view – lightweight, without shadows and perspective"),
                       (self.act_3d, "3D view with perspective and shading")):
            a.setCheckable(True)
            a.setToolTip(tip)
            self._seg_actgrp.addAction(a)
        self.act_3d.setChecked(True)
        self.act_2d.toggled.connect(lambda v: v and self.set_mode(True))
        self.act_3d.toggled.connect(lambda v: v and self.set_mode(False))
        self.seg_box = QFrame(self.view)
        self.seg_box.setObjectName("segbox")
        sl = QHBoxLayout(self.seg_box)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(0)
        seg_group = QButtonGroup(self)
        seg_group.setExclusive(True)
        for act in (self.act_2d, self.act_3d):
            b = QToolButton()
            b.setObjectName("seg")
            b.setText(act.text())
            b.setToolTip(act.toolTip())
            b.setCheckable(True)
            b.setChecked(act.isChecked())
            b.setFixedSize(52, 30)
            seg_group.addButton(b)
            sl.addWidget(b)
            b.clicked.connect(lambda _=False, a=act: a.setChecked(True))
            act.toggled.connect(lambda v, bb=b: self._seg_set(bb, v))
        self.seg_pill = Pill(self.seg_box, list(seg_group.buttons()))
        self._pills[self.seg_box] = self.seg_pill
        self.seg_box.installEventFilter(self)

        # --- Perspective
        self.pop_persp = Popover(self, builder=self._persp_items)
        self.btn_persp = self._top_button("Perspective", "persp", "Choose the viewing angle on the board")
        self.btn_persp.clicked.connect(lambda: self.pop_persp.toggle(self.btn_persp, "center"))

        # --- Menu
        self.btn_menu = self._top_button("Menu", "menu",
                                         "Show / hide menu (File, Formation, View, Team, Animation …)", True)
        self.btn_menu.setChecked(True)
        self.btn_menu.toggled.connect(self.set_menu_visible)
        self.toast = QLabel("", self.view)
        self.toast.setObjectName("chip")
        self.toast.hide()
        self.hide_hint = QFrame(self.view)
        self.hide_hint.setObjectName("hidehint")
        hint_lay = QHBoxLayout(self.hide_hint)
        hint_lay.setContentsMargins(11, 5, 6, 5)
        hint_lay.setSpacing(8)
        hint_lay.addWidget(QLabel("Menus hidden – press Ctrl+H again to show them"))
        hint_close = QToolButton()
        hint_close.setText("×")
        hint_close.setToolTip("Close hint")
        hint_close.setCursor(Qt.CursorShape.PointingHandCursor)
        hint_close.clicked.connect(self._end_hide_hint)
        hint_lay.addWidget(hint_close)
        self.hide_hint.hide()
        self._ui_hidden = False
        self._fade_pending = False
        self._fade_group = None
        self._fade_targets = []
        self._ui_saved = []
        self._hint_on = False
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast.hide)
        self._ui_ready = True

    def set_mode(self, flat):
        """2D = flat & lightweight (no shadows/gradients/perspective), 3D = perspective & shading."""
        if flat == self.mode_flat:
            return
        self.mode_flat = flat
        Style.flat = flat
        self.tilt_slider.setEnabled(not flat)
        self._rebuild_view_menu()
        if flat:
            if self.view.tilt > 1:
                self._last_tilt = self.view.tilt
            rot = round(self.view.rotz / 90.0) * 90.0
            self.animate_view(0, rot, 1.0)
        else:
            self.animate_view(self._last_tilt, self.view.rotz, 1.0)
        self.scene.update()

    # ---- Overlays / switches -------------------------------------------------------
    def _set_lines(self, team, on):
        if on:
            self.lines_overlay.teams.add(team)
        else:
            self.lines_overlay.teams.discard(team)
        self.lines_overlay.update()

    def _set_line_tol(self, v):
        self.lines_overlay.tol = float(v)
        self.tol_label.setText(f"{v} m")
        self.lines_overlay.update()

    def _set_cross(self, on):
        self.lines_overlay.cross = on
        self.lines_overlay.update()

    def _set_overlay(self, item, on):
        item.setVisible(on)

    def _set_overlay_side(self, idx):
        side = 1 if idx == 0 else -1
        self.zone_overlay.set_side(side)
        self.thirds_overlay.set_side(side)

    def _zone_snap_centers(self):
        if self.zone_overlay.isVisible() and self.chk_zone_snap.isChecked():
            return self.zone_overlay.centers()
        return []

    def _set_magnet(self, on):
        Disc.magnet_on = on
        self.btn_magnet.blockSignals(True)
        self.btn_magnet.setChecked(on)
        self.btn_magnet.blockSignals(False)

    def _toolbar_magnet(self, on):
        self.chk_magnet.setChecked(on)

    def _set_magnet_strength(self, v):
        Disc.magnet_strength = float(v)

    def on_discs_moved(self):
        self.lines_overlay.update()
        tags = getattr(self, "name_tags", None)
        if tags is not None:
            tags.update()
        net = getattr(self, "pass_net", None)
        if net is not None and net.isVisible():
            net.update()

    # ---- View ----------------------------------------------------------
    def animate_view(self, tilt, rotz, zoom):
        if self._view_anim:
            self._view_anim.stop()
        v = self.view
        a0 = (v.tilt, v.rotz, v.zoom)
        a1 = (tilt, rotz, zoom)
        anim = QVariantAnimation(self)
        anim.setDuration(650)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.valueChanged.connect(lambda f: v.set_view(*[a + (b - a) * f for a, b in zip(a0, a1)]))
        self._view_anim = anim
        anim.start()

    def _sync_view_sliders(self):
        for s, val in ((self.tilt_slider, self.view.tilt), (self.rot_slider, self.view.rotz),
                       (self.zoom_slider, self.view.zoom * 100)):
            s.blockSignals(True)
            s.setValue(int(round(val)))
            s.blockSignals(False)

    def _show_team(self, team, visible):
        for d in self.discs.values():
            if d.team == team:
                d.setVisible(visible)
        self.lines_overlay.update()

    def _show_numbers(self, visible):
        Disc.show_numbers = visible
        self.scene.update()

    def _show_names(self, visible):
        Disc.show_names = visible
        self.scene.update()

    def _show_drawings(self, visible):
        for it in self._drawing_items():
            it.setVisible(visible)

    # ---- Player: number / name -------------------------------------------------
    def _selected_disc(self):
        sel = [i for i in self.scene.selectedItems() if isinstance(i, Disc)]
        return sel[0] if len(sel) == 1 else None

    def _set_team_view(self, team, animate=True):
        self.team_view = team
        self.team_btns[team].setChecked(True)
        self.team_pill.sync(animate)
        self.refresh_player_editor()

    def refresh_player_editor(self):
        if not hasattr(self, "player_rows"):
            return
        team = self.team_view
        sel = self._selected_disc()
        if sel is not None and sel.team != team:      # a selection on the board switches the team too
            self.team_view = team = sel.team
            self.team_btns[team].setChecked(True)
            self.team_pill.sync(True)
        self.team_box.setTitle("Home (white)" if team == "H" else "Away (orange)")
        for i, (slot, spin, edit) in enumerate(self.player_rows):
            d = self.discs.get(f"{team}{i + 1}")
            if d is None:
                continue
            spin.blockSignals(True)
            edit.blockSignals(True)
            spin.setValue(d.number)
            if edit.text() != d.name:
                edit.setText(d.name)
            spin.blockSignals(False)
            edit.blockSignals(False)
            slot.setStyleSheet(f"color:{ORANGE}; font-weight:bold;" if d is sel else "color:#8d97a3;")

    def _on_row_number(self, i, v):
        d = self.discs.get(f"{self.team_view}{i + 1}")
        if d:
            d.set_number(v)

    def _on_row_name(self, i, text):
        d = self.discs.get(f"{self.team_view}{i + 1}")
        if d:
            d.set_name(text)

    def reset_numbers(self):
        for pid, d in self.discs.items():
            d.set_number(int(pid[1:]))
        self.refresh_player_editor()

    # ---- Formations & positions ----------------------------------------------
    def snapshot(self):
        s = {"p": {pid: [d.x(), d.y()] for pid, d in self.discs.items()},
             "b": [self.ball.x(), self.ball.y()]}
        if self.chk_anim_draw.isChecked():
            s["d"] = {str(it.uid): it.snap() for it in self._drawing_items()
                      if not it.scene_hidden and it.opacity() > 0.5}
        return s

    def set_snapshot(self, s):
        for pid, (x, y) in s["p"].items():
            self.discs[pid].setPos(x, y)
        self.ball.setPos(*s["b"])
        dd = s.get("d")
        if dd is not None:
            items = {str(it.uid): it for it in self._drawing_items()}
            for key, data in dd.items():
                it = items.get(key)
                if it is None:
                    it = DrawItem.from_snap(key, data)
                    self.scene.addItem(it)
                    it.set_label_rot(-self.view.rotz)
                    it.setVisible(self.act_draw.isChecked())
                    items[key] = it
                it.apply_snap(data)
            # Only hide drawings that belong to some scene. Drawings that have never been
            # recorded stay visible (otherwise they would be lost after clicking a scene).
            recorded = self._recorded_uids()
            for key, it in items.items():
                if key in dd:
                    continue
                if key in recorded:
                    it.hide_by_scene()
                elif it.scene_hidden:
                    it.show_by_scene()

    def _recorded_uids(self):
        return {k for s in self.scenes for k in (s.get("d") or {})}

    @staticmethod
    def blend(a, b, f):
        out = {"p": {pid: [a["p"][pid][0] + (b["p"][pid][0] - a["p"][pid][0]) * f,
                           a["p"][pid][1] + (b["p"][pid][1] - a["p"][pid][1]) * f]
                     for pid in a["p"]},
               "b": [a["b"][0] + (b["b"][0] - a["b"][0]) * f,
                     a["b"][1] + (b["b"][1] - a["b"][1]) * f]}
        da, db = a.get("d"), b.get("d")
        if da is not None and db is not None:
            d = {}
            for k in set(da) | set(db):
                if k in da and k in db:
                    e = dict(da[k])
                    pa, pb = da[k]["pos"], db[k]["pos"]
                    e["pos"] = [pa[0] + (pb[0] - pa[0]) * f, pa[1] + (pb[1] - pa[1]) * f]
                    e["op"], e["rv"] = 1.0, 1.0
                elif k in da:          # verschwindet: ausblenden
                    e = dict(da[k])
                    e["op"], e["rv"] = 1.0 - f, 1.0
                else:                  # appears: fade in, paths draw themselves progressively
                    e = dict(db[k])
                    e["op"] = min(1.0, f * 2.5)
                    e["rv"] = f if e["k"] in ("run", "pass") else 1.0
                if e["op"] > 0.001:
                    d[k] = e
            out["d"] = d
        return out

    def assign_roles(self, team, name):
        if name not in FORMATIONS:
            return
        self.formation_of[team] = name
        for i, (_, _, li) in enumerate(formation_positions(FORMATIONS[name], 1 if team == "H" else -1), start=1):
            self.discs[f"{team}{i}"].line = li

    def apply_formation(self, team, name, animate=True):
        self.assign_roles(team, name)
        pos = formation_positions(FORMATIONS[name], 1 if team == "H" else -1)
        start = self.snapshot()
        target = self.snapshot()
        for i, (x, y, _) in enumerate(pos, start=1):
            target["p"][f"{team}{i}"] = [x, y]
        if not animate:
            self.set_snapshot(target)
            return
        self._stop_play()
        if self._pos_anim:
            self._pos_anim.stop()
        anim = QVariantAnimation(self)
        anim.setDuration(550)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.valueChanged.connect(lambda f: self.set_snapshot(self.blend(start, target, f)))
        self._pos_anim = anim
        anim.start()

    # ---- Drawing ------------------------------------------------------------
    def _set_tool(self, key):
        self.view.tool = key
        cur = Qt.CursorShape.ArrowCursor if key == "move" else Qt.CursorShape.CrossCursor
        self.view.setCursor(cur)
        self.view.viewport().setCursor(cur)

    def _drawing_items(self):
        return [i for i in self.scene.items() if isinstance(i, DrawItem)]

    def _on_drawn(self, item):
        self.draw_history.append(item)
        if not self.act_draw.isChecked():
            self.act_draw.setChecked(True)

    def delete_drawing(self, item):
        """Removes the drawing only from the board – recorded scenes keep it,
        it reappears when the scene is played or jumped to."""
        if item.scene() is self.scene:
            self.scene.removeItem(item)
        if item in self.draw_history:
            self.draw_history.remove(item)

    def undo_draw(self):
        while self.draw_history:
            it = self.draw_history.pop()
            if it.scene() is self.scene:
                self.delete_drawing(it)
                return

    def delete_selected(self):
        for it in list(self.scene.selectedItems()):
            if isinstance(it, DrawItem):
                self.delete_drawing(it)

    def clear_drawings(self):
        for it in self._drawing_items():
            self.delete_drawing(it)
        self.draw_history.clear()

    def ask_clear_drawings(self):
        if self._drawing_items() and self._confirm("Delete all drawings from the board? "
                                                   "They are kept in scenes that have already been recorded.",
                                                   "Delete all"):
            self.clear_drawings()

    def _confirm(self, text, ok_text="Continue"):
        box = QMessageBox(QMessageBox.Icon.Question, "Tactics Board", text,
                          QMessageBox.StandardButton.NoButton, self)
        yes = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is yes

    def _has_content(self):
        return bool(self.scenes or self._drawing_items() or self.notes.toPlainText().strip())

    # ---- Animation / scenes --------------------------------------------------
    def _refresh_scene_list(self, select=None):
        self.scene_list.blockSignals(True)
        self.scene_list.clear()
        for i in range(len(self.scenes)):
            self.scene_list.addItem(f"Scene {i + 1}")
        if select is not None and 0 <= select < len(self.scenes):
            self.scene_list.setCurrentRow(select)
        self.scene_list.blockSignals(False)
        self.timeline.blockSignals(True)
        self.timeline.setRange(0, max(0, (len(self.scenes) - 1) * 100))
        self.timeline.blockSignals(False)
        self._update_time()
        self.rebuild_trails()

    def add_scene(self):
        self.set_player_visible(True)
        self._stop_play()
        row = self.scene_list.currentRow()
        idx = row + 1 if row >= 0 else len(self.scenes)
        self.scenes.insert(idx, self.snapshot())
        self._refresh_scene_list(select=idx)
        self._set_timeline(idx)

    def overwrite_scene(self):
        row = self.scene_list.currentRow()
        if row < 0:
            return
        self._stop_play()
        self.scenes[row] = self.snapshot()
        self.rebuild_trails()

    def delete_scene(self):
        row = self.scene_list.currentRow()
        if row < 0:
            return
        self._stop_play()
        del self.scenes[row]
        self._refresh_scene_list(select=min(row, len(self.scenes) - 1))

    def _set_timeline(self, t):
        self.timeline.blockSignals(True)
        self.timeline.setValue(int(round(t * 100)))
        self.timeline.blockSignals(False)
        self._update_time()

    def _update_time(self):
        n = len(self.scenes)
        self.timeline.marks = n
        self.timeline.setEnabled(n >= 2)
        self.timeline.update()
        if n == 0:
            self.time_label.setText("No animation yet")
        elif n == 1:
            self.time_label.setText("1 scene")
        else:
            step = self.step_spin.value()

            def fmt(s):
                return f"{int(s // 60)}:{s % 60:04.1f}"
            self.time_label.setText(f"{fmt(self.timeline.value() / 100.0 * step)} / {fmt((n - 1) * step)}")

    def _speed(self):
        v = self.speed_combo.currentData()
        return float(v) if v else 1.0

    def _on_speed(self, _i=None):
        if self._play_anim is not None:
            self.start_play_from_current()

    def start_play_from_current(self):
        self._stop_play()
        self.start_play()

    def step_scene(self, d):
        n = len(self.scenes)
        if n == 0:
            return
        t = self.timeline.value() / 100.0
        target = math.floor(t + 0.02) + 1 if d > 0 else math.ceil(t - 0.02) - 1
        self.seek_to(max(0, min(n - 1, target)))

    def seek_to(self, t, animate=True):
        self._stop_play()
        n = len(self.scenes)
        if n == 0:
            return
        t = max(0.0, min(float(n - 1), float(t)))
        cur = self.timeline.value() / 100.0
        if not animate or abs(t - cur) < 1e-3 or n < 2:
            self._set_timeline(t)
            self.apply_t(t)
            return
        a = QVariantAnimation(self)
        a.setStartValue(float(cur))
        a.setEndValue(float(t))
        a.setDuration(int(220 + 160 * abs(t - cur)))
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.valueChanged.connect(self._on_play_tick)
        self._seek_anim = a
        a.start()

    def _on_scene_selected(self, row):
        if row < 0 or row >= len(self.scenes):
            return
        self._stop_play()
        self.set_snapshot(self.scenes[row])
        self._set_timeline(row)

    def _on_timeline(self, v):
        self._stop_play()
        self.apply_t(v / 100.0)
        self._update_time()

    def apply_t(self, t):
        n = len(self.scenes)
        if n == 0:
            return
        if n == 1:
            self.set_snapshot(self.scenes[0])
            return
        t = max(0.0, min(float(n - 1), t))
        i = min(int(t), n - 2)
        f = t - i
        if self.chk_ease.isChecked():
            f = f * f * (3 - 2 * f)
        if f <= 0.0005:
            self.set_snapshot(self.scenes[i])
        elif f >= 0.9995:
            self.set_snapshot(self.scenes[i + 1])
        else:
            self.set_snapshot(self.blend(self.scenes[i], self.scenes[i + 1], f))
        near = int(round(t))
        if self.scene_list.currentRow() != near:
            self.scene_list.blockSignals(True)
            self.scene_list.setCurrentRow(near)
            self.scene_list.blockSignals(False)

    def toggle_play(self):
        if self._play_anim is not None:
            self._stop_play()
        else:
            self.start_play()

    def start_play(self):
        n = len(self.scenes)
        if n < 2:
            self._flash("Please record at least two scenes first.")
            return
        if self._pos_anim:
            self._pos_anim.stop()
        t0 = self.timeline.value() / 100.0
        if t0 >= n - 1 - 0.01:
            t0 = 0.0
        step = self.step_spin.value()
        anim = QVariantAnimation(self)
        anim.setStartValue(float(t0))
        anim.setEndValue(float(n - 1))
        anim.setDuration(max(50, int((n - 1 - t0) * step * 1000 / self._speed())))
        anim.setEasingCurve(QEasingCurve.Type.Linear)
        anim.valueChanged.connect(self._on_play_tick)
        anim.finished.connect(self._on_play_finished)
        self._play_anim = anim
        self._set_play_icon(True)
        anim.start()

    def _on_play_tick(self, t):
        self.apply_t(float(t))
        self._set_timeline(float(t))

    def _on_play_finished(self):
        if self._play_anim is None:
            return
        if self.chk_loop.isChecked():
            self._play_anim = None
            self.timeline.blockSignals(True)
            self.timeline.setValue(0)
            self.timeline.blockSignals(False)
            self.start_play()
        else:
            self._play_anim = None
            self._set_play_icon(False)

    def _stop_play(self):
        if self._play_anim is not None:
            anim, self._play_anim = self._play_anim, None
            anim.stop()
        if self._seek_anim is not None:
            seek, self._seek_anim = self._seek_anim, None
            seek.stop()
        self._set_play_icon(False)

    def rewind(self):
        self.seek_to(0, animate=False)

    def rebuild_trails(self, *_):
        for it in self.trail_items:
            self.scene.removeItem(it)
        self.trail_items.clear()
        if not self.chk_trails.isChecked() or len(self.scenes) < 2:
            return
        for pid, d in list(self.discs.items()) + [("b", self.ball)]:
            pts = [QPointF(*(s["b"] if pid == "b" else s["p"][pid])) for s in self.scenes]
            moved = max(math.hypot(a.x() - b.x(), a.y() - b.y()) for a, b in zip(pts, pts[1:]))
            if moved < 10:
                continue
            path = QPainterPath(pts[0])
            for q in pts[1:]:
                path.lineTo(q)
            item = QGraphicsPathItem(path)
            col = QColor(WHITE if (pid != "b" and d.team == "H") else ORANGE)
            if pid == "b":
                col = QColor("#45c4e8")
            col.setAlpha(170)
            pen = QPen(col, 3.5, Qt.PenStyle.DotLine)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            item.setPen(pen)
            item.setZValue(3)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene.addItem(item)
            self.trail_items.append(item)

    # ---- File ------------------------------------------------------------------
    def to_dict(self):
        return {
            "version": FILE_VERSION,
            "players": {pid: {"x": d.x(), "y": d.y(), "n": d.number, "name": d.name}
                        for pid, d in self.discs.items()},
            "ball": [self.ball.x(), self.ball.y()],
            "scenes": self.scenes,
            "drawings": [d.to_dict() for d in self._drawing_items()],
            "step": self.step_spin.value(),
            "view": [self.view.tilt, self.view.rotz, self.view.zoom],
            "formations": [self.formation_of["H"], self.formation_of["A"]],
            "notes": self.notes.toPlainText(),
        }

    def from_dict(self, data):
        # Validate first and pre-build the drawings – only then is the board touched
        validate_tactic(data, set(self.discs))
        new_items = [DrawItem.from_dict(d) for d in data.get("drawings", [])]
        self._stop_play()
        self.scene.clearSelection()
        self.clear_drawings()
        for pid, v in data["players"].items():
            if pid in self.discs:
                d = self.discs[pid]
                d.setPos(float(v["x"]), float(v["y"]))
                d.number = int(v["n"])
                d.name = v.get("name", "")
                d.update()
        self.ball.setPos(float(data["ball"][0]), float(data["ball"][1]))
        self.scenes = data.get("scenes", [])
        recorded = self._recorded_uids()
        for it in new_items:
            if it.scene_hidden and str(it.uid) not in recorded:
                it.show_by_scene()      # not present in any scene: do not leave it permanently invisible
            self.scene.addItem(it)
        self.step_spin.setValue(float(data.get("step", 1.5)))
        h, a = data.get("formations", ["4-3-3", "4-4-2"])
        for team, name, combo in (("H", h, self.home_combo), ("A", a, self.away_combo)):
            name = LEGACY_FORMATION_NAMES.get(name, name)
            if name in FORMATIONS:
                self.assign_roles(team, name)
                combo.setCurrentText(name)
        self.notes.setPlainText(data.get("notes", ""))
        self._refresh_scene_list(select=0 if self.scenes else None)
        if len(self.scenes) >= 2:
            self.set_player_visible(True)
        tilt, rotz, zoom = data.get("view", [0, 0, 1])
        self.view.set_view(0 if self.mode_flat else tilt, rotz, zoom)
        self.refresh_player_editor()

    def new_board(self):
        if self._has_content() and not self._confirm(
                "Start a new board? Scenes, drawings and notes will be lost.", "New board"):
            return
        self._stop_play()
        self.scene.clearSelection()
        self.clear_drawings()
        self.scenes = []
        self.notes.clear()
        for d in self.discs.values():
            d.set_name("")
        self.reset_numbers()
        self._refresh_scene_list()
        self.apply_formation("H", self.home_combo.currentText(), animate=False)
        self.apply_formation("A", self.away_combo.currentText(), animate=False)
        self.ball.setPos(0, 0)
        self.animate_view(0, 0, 1.0)

    def save_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save tactic", "tactic.json", "Tactic (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        tmp = path + ".tmp"
        try:
            text = json.dumps(self.to_dict(), indent=1, ensure_ascii=False)
            with open(tmp, "w", encoding="utf-8") as f:   # first into a temp file, then replace:
                f.write(text)                              # an existing file stays intact on errors
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError) as exc:
            try:
                os.remove(tmp)
            except OSError:
                pass
            QMessageBox.warning(self, "Error", f"Saving failed:\n{exc}")
            return
        self._flash(f"Saved: {path}")

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open tactic", "", "Tactic (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            validate_tactic(data, set(self.discs))        # does not change anything on the board yet
        except (OSError, ValueError) as exc:              # JSON and Unicode errors are ValueError
            QMessageBox.warning(self, "Error", f"File could not be loaded:\n{exc}")
            return
        if self._has_content() and not self._confirm(
                "The current board will be replaced by the loaded tactic.", "Load"):
            return
        try:
            self.from_dict(data)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Error", f"File could not be loaded:\n{exc}")

    def export_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export as image", "tactic.png", "PNG (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        self.scene.clearSelection()
        self.view.set_insets(0, 0, 0, 0)       # always export with the full, centered board (even with an open menu)
        try:
            ok = self.view.viewport().grab().save(path, "PNG")   # board only, no floating menus
        finally:
            self._layout_overlays()
        if ok:
            self._flash(f"Exported: {path}")
        else:
            QMessageBox.warning(self, "Error", f"Image could not be saved:\n{path}")


def main():
    if sys.platform == "win32":
        # So that Windows shows the app's own icon in the taskbar (not python.exe's)
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("taktiktafel.app")
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(resource_path(ICON_FILE)))
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
