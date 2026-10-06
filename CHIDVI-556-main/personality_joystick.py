"""Hold-and-drag personality joystick for CHIDVI-556.

The widget discovers personality profiles dynamically and never owns a separate
personality registry. The small control lives in the bottom-right corner.
Holding it opens a full-screen radial selector; releasing the mouse commits the
currently highlighted personality and immediately dismisses the selector.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

from Personality.loader import discover_personalities


def _qcolor(value: str | None, fallback: str = "#00d4ff") -> QColor:
    try:
        color = QColor(value or fallback)
        return color if color.isValid() else QColor(fallback)
    except Exception:
        return QColor(fallback)


def _mix(color: QColor, amount: float, target: QColor = QColor("#000000")) -> QColor:
    amount = max(0.0, min(1.0, amount))
    return QColor(
        int(color.red() * (1 - amount) + target.red() * amount),
        int(color.green() * (1 - amount) + target.green() * amount),
        int(color.blue() * (1 - amount) + target.blue() * amount),
        color.alpha(),
    )


def _circular_angle_distance(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


class PersonalitySelector(QWidget):
    """Full-window joystick surface shown only while the handle is held."""

    selected = pyqtSignal(str)

    def __init__(self, parent: QWidget, profiles: dict, current_id: str):
        super().__init__(parent)
        self._profiles = dict(profiles)
        self._ids = list(self._profiles)
        self._current_id = current_id if current_id in self._profiles else (self._ids[0] if self._ids else "")
        self._highlighted = self._current_id
        self._dragging = False
        self._dead_zone = 42.0
        self._ring_radius = 205.0
        self._node_radius = 58.0
        self._center = QPointF()
        self._pointer = QPointF()
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.hide()

    def set_profiles(self, profiles: dict):
        self._profiles = dict(profiles)
        self._ids = list(self._profiles)
        if self._current_id not in self._profiles:
            self._current_id = self._ids[0] if self._ids else ""
        self._highlighted = self._current_id
        self._pointer = QPointF(self._center)
        self.update()

    def set_current(self, personality_id: str):
        if personality_id in self._profiles:
            self._current_id = personality_id
            self._highlighted = personality_id
            self.update()

    def open(self):
        if not self._ids:
            return
        self.setGeometry(self.parentWidget().rect())
        self._center = QPointF(self.width() / 2, self.height() / 2)
        self._pointer = QPointF(self._center)
        self._highlighted = self._current_id
        self._dragging = True
        self.show()
        self.raise_()
        self.activateWindow()
        self.grabMouse()
        self.update()

    def _angles(self) -> list[float]:
        # Start at the top and go clockwise, like a physical radial control.
        count = len(self._ids)
        return [90.0 - (360.0 * i / count) for i in range(count)]

    def _node_position(self, index: int) -> QPointF:
        angle = math.radians(self._angles()[index])
        return QPointF(
            self._center.x() + self._ring_radius * math.cos(angle),
            self._center.y() - self._ring_radius * math.sin(angle),
        )

    def _pick_from_position(self, pos: QPointF) -> None:
        dx = pos.x() - self._center.x()
        dy = pos.y() - self._center.y()
        distance = math.hypot(dx, dy)

        # Move the virtual joystick cap toward the cursor, but keep the stem
        # inside the selector ring so it always reads as one physical control.
        max_pointer = self._ring_radius * 0.68
        if distance > max_pointer:
            scale = max_pointer / distance
            dx *= scale
            dy *= scale
        self._pointer = QPointF(
            self._center.x() + dx,
            self._center.y() + dy,
        )
        if distance <= self._dead_zone:
            return

        angle = math.degrees(math.atan2(dy, dx))
        best_index = min(
            range(len(self._ids)),
            key=lambda i: _circular_angle_distance(angle, self._angles()[i]),
        )
        self._highlighted = self._ids[best_index]
        self.update()

    def _primary(self, personality_id: str) -> QColor:
        profile = self._profiles.get(personality_id) or {}
        return _qcolor((profile.get("theme") or {}).get("primary"))

    def _name(self, personality_id: str) -> str:
        profile = self._profiles.get(personality_id) or {}
        return str(profile.get("name") or personality_id)

    def _description(self, personality_id: str) -> str:
        profile = self._profiles.get(personality_id) or {}
        return str(profile.get("description") or "")

    def paintEvent(self, _event):
        if not self._ids:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dim and soften the existing command center while the selector is held.
        p.fillRect(self.rect(), QColor(0, 3, 7, 208))

        cx, cy = self._center.x(), self._center.y()
        center = QPointF(cx, cy)

        # Outer control rings.
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(80, 210, 255, 65), 1))
        p.drawEllipse(center, self._ring_radius + 88, self._ring_radius + 88)
        p.setPen(QPen(QColor(80, 210, 255, 95), 1))
        p.drawEllipse(center, self._ring_radius + 18, self._ring_radius + 18)
        p.setPen(QPen(QColor(80, 210, 255, 35), 1))
        p.drawEllipse(center, self._dead_zone + 22, self._dead_zone + 22)

        # Crosshair / joystick rails.
        p.setPen(QPen(QColor(70, 205, 240, 35), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(cx - self._ring_radius - 90, cy), QPointF(cx + self._ring_radius + 90, cy))
        p.drawLine(QPointF(cx, cy - self._ring_radius - 90), QPointF(cx, cy + self._ring_radius + 90))

        # Radial personality nodes.
        for i, personality_id in enumerate(self._ids):
            pos = self._node_position(i)
            color = self._primary(personality_id)
            selected = personality_id == self._highlighted
            active = personality_id == self._current_id

            # Connector.
            connector_alpha = 155 if selected else 70
            p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), connector_alpha), 2 if selected else 1))
            p.drawLine(center, pos)

            # Node.
            radius = self._node_radius + (9 if selected else 0)
            fill = QColor(color.red(), color.green(), color.blue(), 45 if not selected else 125)
            if active and not selected:
                fill.setAlpha(78)
            p.setBrush(QBrush(fill))
            p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 235 if selected else 110), 3 if selected else 1))
            p.drawEllipse(pos, radius, radius)

            # Node label.
            name = self._name(personality_id)
            p.setFont(QFont("Courier New", 9, QFont.Weight.Bold if selected else QFont.Weight.Normal))
            text_color = QColor("#f2feff") if selected else QColor("#8ab6c1")
            p.setPen(QPen(text_color))
            text_rect_x = pos.x() - 82
            text_rect_y = pos.y() + radius + 7
            p.drawText(int(text_rect_x), int(text_rect_y), 164, 20, Qt.AlignmentFlag.AlignCenter, name[:24])

        # Center joystick base + moving stick cap.
        current_color = self._primary(self._highlighted)
        p.setBrush(QBrush(QColor(current_color.red(), current_color.green(), current_color.blue(), 42)))
        p.setPen(QPen(QColor(current_color.red(), current_color.green(), current_color.blue(), 185), 2))
        p.drawEllipse(center, 72, 72)
        p.setPen(QPen(QColor(220, 250, 255, 65), 1))
        p.drawEllipse(center, 53, 53)

        p.setPen(QPen(QColor(current_color.red(), current_color.green(), current_color.blue(), 150), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(center, self._pointer)
        p.setBrush(QBrush(QColor(current_color.red(), current_color.green(), current_color.blue(), 190)))
        p.setPen(QPen(QColor("#eaffff"), 2))
        p.drawEllipse(self._pointer, 20, 20)
        p.setBrush(QBrush(QColor("#eaffff")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(self._pointer, 5, 5)

        # Center label.
        p.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        p.setPen(QPen(QColor("#eaffff")))
        p.drawText(
            int(cx - 150), int(cy - 8), 300, 24,
            Qt.AlignmentFlag.AlignCenter, self._name(self._highlighted).upper()[:24],
        )
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(QColor("#6ea9b6")))
        p.drawText(
            int(cx - 170), int(cy + 15), 340, 18,
            Qt.AlignmentFlag.AlignCenter, "HOLD  •  DRAG  •  RELEASE",
        )

        # Selected personality description.
        desc = self._description(self._highlighted)
        if desc:
            p.setPen(QPen(QColor("#8dbac3")))
            p.drawText(
                int(cx - 240), int(cy + 103), 480, 42,
                Qt.AlignmentFlag.AlignCenter,
                desc[:110],
            )

        p.end()

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._pick_from_position(event.position())

    def mouseReleaseEvent(self, event):
        if not self._dragging:
            return
        self._pick_from_position(event.position())
        selected_id = self._highlighted
        self._dragging = False
        self._pointer = QPointF(self._center)
        try:
            self.releaseMouse()
        except Exception:
            pass
        self.hide()
        if selected_id:
            self.selected.emit(selected_id)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._dragging = False
            self._pointer = QPointF(self._center)
            try:
                self.releaseMouse()
            except Exception:
                pass
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)


class PersonalityHandle(QPushButton):
    """Small bottom-right joystick handle."""

    pressed_for_switch = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setDown(True)
            self.pressed_for_switch.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class PersonalityJoystick(QWidget):
    """Bottom-right handle + center radial selector."""

    selected = pyqtSignal(str)

    def __init__(self, parent: QWidget, current_id: str = "tony"):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(72, 72)

        self._profiles = discover_personalities()
        self._current_id = current_id if current_id in self._profiles else (next(iter(self._profiles), ""))

        self._handle = PersonalityHandle(self)
        self._handle.setFixedSize(58, 58)
        self._handle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._handle.setToolTip("Hold to switch personality")
        self._handle.pressed_for_switch.connect(self._open_selector)

        self._selector = PersonalitySelector(parent, self._profiles, self._current_id)
        self._selector.selected.connect(self._on_selected)

        self._layout_handle()

    def _layout_handle(self):
        x = (self.width() - self._handle.width()) // 2
        y = (self.height() - self._handle.height()) // 2
        self._handle.move(x, y)

    def set_profiles(self, profiles: dict):
        self._profiles = dict(profiles)
        self._selector.set_profiles(self._profiles)
        self._style_handle()

    def set_current(self, personality_id: str):
        if personality_id not in self._profiles:
            return
        self._current_id = personality_id
        self._selector.set_current(personality_id)
        self._style_handle()

    def _style_handle(self):
        profile = self._profiles.get(self._current_id) or {}
        color = _qcolor((profile.get("theme") or {}).get("primary"))
        name = str(profile.get("name") or self._current_id)
        glow = _mix(color, 0.38)
        self._handle.setText("◉")
        self._handle.setStyleSheet(
            f"""
            QPushButton {{
                background: rgba({color.red()}, {color.green()}, {color.blue()}, 36);
                color: {color.name()};
                border: 2px solid {color.name()};
                border-radius: 29px;
                font: bold 20px "Courier New";
            }}
            QPushButton:hover {{
                background: rgba({color.red()}, {color.green()}, {color.blue()}, 72);
                border: 2px solid {glow.name()};
            }}
            QPushButton:pressed {{
                background: rgba({color.red()}, {color.green()}, {color.blue()}, 110);
                border: 3px solid {color.name()};
            }}
            """
        )
        self._handle.setToolTip(f"Hold to switch personality — {name}")

    def _open_selector(self):
        self._selector.set_profiles(self._profiles)
        self._selector.set_current(self._current_id)
        self._selector.open()

    def _on_selected(self, personality_id: str):
        self._current_id = personality_id
        self._handle.setDown(False)
        self._style_handle()
        self.selected.emit(personality_id)

    def sync_geometry(self, parent_size):
        margin = 16
        self.setGeometry(
            max(0, parent_size.width() - self.width() - margin),
            max(0, parent_size.height() - self.height() - margin),
            self.width(),
            self.height(),
        )
