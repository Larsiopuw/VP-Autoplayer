"""Antialiased buttons with a smooth, theme-aware hover surface."""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QLineF, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QPushButton


def _mix(first: QColor, second: QColor, amount: float) -> QColor:
    amount = max(0.0, min(1.0, amount))
    return QColor.fromRgbF(
        first.redF() * (1 - amount) + second.redF() * amount,
        first.greenF() * (1 - amount) + second.greenF() * amount,
        first.blueF() * (1 - amount) + second.blueF() * amount,
        first.alphaF() * (1 - amount) + second.alphaF() * amount,
    )


class PremiumButton(QPushButton):
    """Paint surface, highlight and content together to avoid square native edges."""

    def __init__(self, text: str = "", parent=None, *, objectName: str = ""):
        super().__init__(text, parent)
        if objectName:
            self.setObjectName(objectName)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = 0.0
        self._symbol = ""
        self.motion = QPropertyAnimation(self, b"hoverAmount", self)
        self.motion.setDuration(190)
        self.motion.setEasingCurve(QEasingCurve.OutCubic)

    def set_symbol(self, symbol: str) -> None:
        self._symbol = symbol
        self.update()

    def get_hover_amount(self) -> float:
        return self._hover

    def set_hover_amount(self, value: float) -> None:
        self._hover = float(value)
        self.update()

    hoverAmount = Property(float, get_hover_amount, set_hover_amount)

    def _animate(self, target: float) -> None:
        self.motion.stop()
        self.motion.setStartValue(self._hover)
        self.motion.setEndValue(target)
        self.motion.start()

    def enterEvent(self, event) -> None:
        self._animate(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._animate(0.0)
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.update()

    def paintEvent(self, _event) -> None:
        app = QApplication.instance()
        colors = app.property("vpUiColors") if app else None
        colors = colors if isinstance(colors, dict) else {
            "field": "#142B43", "card_hover": "#1B3550", "border": "#29435F",
            "accent": "#67E8F9", "blue": "#2695F2", "text": "#EDF7FF",
            "muted": "#A1BED3", "danger": "#FB7185", "primary_text": "#061625",
            "soft": "#123E5A",
        }
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        role = self.objectName()
        hover = self._hover if self.isEnabled() else 0.0
        if self.isDown():
            hover *= 0.5
        radius = min(15.0 if role == "Primary" else 12.0, self.height() / 2.0 - 1)
        surface = self.rect().adjusted(1, 1, -2, -2)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(surface), radius, radius)

        primary = role == "Primary"
        chrome = role in ("WindowButton", "CloseButton")
        mode = role == "ModeButton"
        selected = mode and self.isChecked()
        accent = QColor(colors["accent"])
        border = QColor(colors["border"])
        if primary:
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            gradient.setColorAt(0, _mix(QColor(colors["blue"]), QColor("#FFFFFF"), hover * 0.14))
            gradient.setColorAt(1, _mix(accent, QColor("#FFFFFF"), hover * 0.13))
            painter.fillPath(shape, gradient)
            border = _mix(accent, QColor("#FFFFFF"), hover * 0.30)
        else:
            base = QColor(colors["soft"] if selected else colors["field"])
            if chrome:
                base.setAlpha(95)
            elif mode:
                base.setAlpha(0 if not selected else 210)
            raised = _mix(QColor(colors["card_hover"]), accent, 0.20)
            if role == "CloseButton":
                raised = QColor("#7D2B40")
                raised.setAlpha(230)
            elif chrome or mode:
                raised.setAlpha(180)
            painter.fillPath(shape, _mix(base, raised, hover))
            if selected:
                border = accent
            elif role == "CloseButton":
                border = _mix(border, QColor(colors["danger"]), hover * 0.8)
            elif chrome:
                border.setAlphaF(0.55 + 0.35 * hover)
            else:
                border = _mix(border, accent, hover * 0.75)

        if hover:
            painter.save()
            painter.setClipPath(shape)
            glow = QRadialGradient(self.width() * 0.28, 0, max(self.width() * 0.8, 50))
            glow_color = QColor("#FFFFFF" if primary else colors["accent"])
            glow_color.setAlphaF(0.16 * hover if primary else 0.13 * hover)
            glow.setColorAt(0, glow_color)
            glow_color.setAlpha(0)
            glow.setColorAt(1, glow_color)
            painter.fillRect(surface, glow)
            painter.restore()
        painter.setPen(QPen(border, 1.0 if not hover else 1.2))
        painter.drawPath(shape)

        if not self.isEnabled():
            ink = QColor(colors["muted"])
            ink.setAlpha(130)
        elif role == "CloseButton" and hover:
            ink = _mix(QColor(colors["muted"]), QColor("#FFFFFF"), hover)
        elif primary:
            ink = QColor(colors["primary_text"])
        elif selected:
            ink = accent
        else:
            ink = _mix(QColor(colors["text"]), accent, hover * 0.42)

        painter.save()
        if self._symbol:
            text = self.text()
            if text:
                metrics = painter.fontMetrics()
                text_width = metrics.horizontalAdvance(text)
                left = max(8, (self.width() - text_width - 25) / 2)
                self._paint_symbol(painter, ink, left + 9)
                painter.setPen(ink)
                text_rect = self.rect().adjusted(int(left + 25), 0, -8, 0)
                painter.drawText(
                    text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                    metrics.elidedText(text, Qt.ElideRight, max(0, text_rect.width())),
                )
            else:
                self._paint_symbol(painter, ink)
        else:
            icon = self.icon()
            text = self.text()
            metrics = painter.fontMetrics()
            icon_size = self.iconSize()
            icon_width = icon_size.width() if not icon.isNull() else 0
            gap = 7 if icon_width and text else 0
            if not icon_width and text and role != "ContextAction":
                painter.setPen(ink)
                text_rect = self.rect().adjusted(8, 0, -8, 0)
                painter.drawText(text_rect, Qt.AlignCenter, metrics.elidedText(text, Qt.ElideRight, text_rect.width()))
            else:
                available = max(0, self.width() - 16 - icon_width - gap)
                display = metrics.elidedText(text, Qt.ElideRight, available)
                text_width = metrics.horizontalAdvance(display)
                total = icon_width + gap + text_width
                left = 13 if role == "ContextAction" else (self.width() - total) / 2
                if icon_width:
                    pixmap = icon.pixmap(icon_size)
                    painter.drawPixmap(round(left), round((self.height() - icon_size.height()) / 2), pixmap)
                if text:
                    painter.setPen(ink)
                    text_rect = QRectF(left + icon_width + gap, 0, text_width + 1, self.height())
                    painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, display)
        painter.restore()

    def _paint_symbol(self, painter: QPainter, ink: QColor, center_x: float | None = None) -> None:
        painter.save()
        painter.translate(self.width() / 2 if center_x is None else center_x, self.height() / 2)
        painter.setPen(QPen(ink, 1.75, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        symbol = self._symbol
        if symbol == "close":
            painter.drawLine(QLineF(-5.2, -5.2, 5.2, 5.2))
            painter.drawLine(QLineF(5.2, -5.2, -5.2, 5.2))
        elif symbol == "minimize":
            painter.drawLine(QLineF(-5.5, 1, 5.5, 1))
        elif symbol == "maximize":
            painter.drawRoundedRect(QRectF(-5.5, -5.5, 11, 11), 1, 1)
        elif symbol == "restore":
            painter.drawRoundedRect(QRectF(-4, -3, 9, 9), 1, 1)
            painter.drawLine(QLineF(-1, -6, 6, -6))
            painter.drawLine(QLineF(6, -6, 6, 2))
        elif symbol in ("folder", "folder-open"):
            folder = QPainterPath()
            folder.moveTo(-8, -5)
            folder.lineTo(-2, -5)
            folder.lineTo(0, -2.8)
            folder.lineTo(7, -2.8)
            folder.quadTo(8, -2.8, 8, -1)
            folder.lineTo(8, 5)
            folder.quadTo(8, 7, 6, 7)
            folder.lineTo(-7, 7)
            folder.quadTo(-8, 7, -8, 5)
            folder.closeSubpath()
            fill = QColor(ink)
            fill.setAlpha(45)
            painter.setBrush(fill)
            painter.drawPath(folder)
            if symbol == "folder-open":
                painter.setBrush(Qt.NoBrush)
                painter.drawLine(QLineF(1, 3, 8, -4))
                painter.drawLine(QLineF(4, -4, 8, -4))
                painter.drawLine(QLineF(8, -4, 8, 0))
        painter.restore()


# -- by Larsiopuw
