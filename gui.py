#!/usr/bin/env python3
"""Native Qt interface for VP Autoplayer with smooth frameless window chrome."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError

from pynput.keyboard import Key, Listener
from PySide6.QtCore import Property, QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation, QSize, Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor, QCloseEvent, QFont, QIcon, QKeySequence, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QDialog, QFileDialog, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QStyle, QTextEdit, QVBoxLayout, QWidget,
)

import autoplayer_core as core
from batch_import import SUPPORTED_EXTENSIONS, BatchOutcome, run_batch_import
from transcription import (
    AUDIO_EXTENSIONS, AUTO_QUALITY_PROFILE, DRUM_ARRANGEMENT_PROFILE,
    DRUM_ORIGINAL_PROFILE, MIDI_EXTENSIONS, MIDI_QUALITY_PROFILE, QUALITY_PROFILES,
    import_song, transcribe_audio,
)
from studio_features import ProfileStore, analyze_quality, ensure_daily_backup, replace_section, trash_song
from midi_hub import HubItem, download_midi, fetch_catalog, hub_error_message
from midi_preview import MidiPreviewPlayer
from ui_effects import PremiumButton

CFG = core.CFG


def parse_combo(expression: str) -> frozenset[Key | str]:
    modifiers = {
        "ctrl": Key.ctrl, "alt": Key.alt, "shift": Key.shift,
        "cmd": Key.cmd, "meta": Key.cmd,
    }
    named = {f"f{number}": getattr(Key, f"f{number}") for number in range(1, 13)}
    named.update({
        "space": Key.space, "enter": Key.enter, "tab": Key.tab,
        "backspace": Key.backspace, "esc": Key.esc, "escape": Key.esc,
    })
    parts = [part.strip() for part in expression.casefold().split("+") if part.strip()]
    return frozenset(modifiers.get(part) or named.get(part) or part for part in parts)


def palette(dark: bool) -> dict[str, str]:
    if dark:
        return {
            "bg": "#07111D", "shell": "#0A1626", "card": "#11243A",
            "card_hover": "#1B3550", "field": "#142B43", "border": "#29435F",
            "text": "#EDF7FF", "muted": "#A1BED3", "muted2": "#7695AE",
            "soft": "#123E5A", "soft_hover": "#1B5270", "blue_text": "#80E4F1",
            "blue": "#2695F2", "blue_hover": "#1783DD", "scroll": "#47708A",
            "white": "#FFFFFF", "danger": "#FB7185", "green": "#54DFB3",
            "accent": "#67E8F9", "shell_end": "#0B1D2D", "card_end": "#0E1B2D",
            "primary_text": "#061625",
        }
    return {
        "bg": "#EAF0F7", "shell": "#F5F8FC", "card": "#FFFFFF",
        "card_hover": "#EFF5FC", "field": "#E8EFF7", "border": "#CED9E7",
        "text": "#101828", "muted": "#56657A", "muted2": "#8290A4",
        "soft": "#D9EAFE", "soft_hover": "#C8E0FC", "blue_text": "#1268CC",
        "blue": "#1976ED", "blue_hover": "#0D66D4", "scroll": "#B5C3D4",
        "white": "#FFFFFF", "danger": "#D93E54", "green": "#11996D",
        "accent": "#087A9D", "shell_end": "#E7F1FA", "card_end": "#F6FAFF",
        "primary_text": "#FFFFFF",
    }


def app_stylesheet(colors: dict[str, str], maximized: bool = False) -> str:
    radius = 0 if maximized else 22
    return f"""
    QWidget {{ color: {colors['text']}; font-family: 'Segoe UI Variable', 'Segoe UI'; font-size: 10pt; }}
    QMainWindow, QWidget#TransparentRoot {{ background: transparent; }}
    QFrame#WindowShell {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {colors['shell']},stop:1 {colors['shell_end']}); border: 1px solid {colors['border']}; border-radius: {radius}px; }}
    QFrame#TitleBar, QFrame#Header, QFrame#Clear {{ background: transparent; border: none; }}
    QFrame#Card, QFrame#LibraryCard, QFrame#HeroCard, QFrame#PlaybackCard, QFrame#TargetCard {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {colors['card']},stop:1 {colors['card_end']}); border: 1px solid {colors['border']}; border-radius: 20px; }}
    QFrame#HeroCard {{ border: 1px solid {colors['accent']}; }}
    QFrame#Field {{ background: {colors['field']}; border: none; border-radius: 13px; }}
    QLabel {{ background: transparent; border: none; }}
    QLabel#Title {{ font-size: 24pt; font-weight: 750; letter-spacing: 0.3px; }}
    QLabel#SongTitle {{ font-size: 21pt; font-weight: 750; }}
    QLabel#CardTitle {{ font-size: 13pt; font-weight: 700; }}
    QLabel#Eyebrow {{ color: {colors['accent']}; font-size: 8pt; font-weight: 750; letter-spacing: 1.2px; }}
    QLabel#Muted {{ color: {colors['muted']}; }}
    QLabel#MutedSmall {{ color: {colors['muted2']}; font-size: 8.5pt; }}
    QLabel#Badge {{ background: {colors['soft']}; color: {colors['blue_text']}; border-radius: 9px; padding: 4px 9px; font-size: 8pt; font-weight: 700; }}
    QLabel#HeaderBadge {{ background: {colors['soft']}; color: {colors['blue_text']}; border-radius: 10px; padding: 0 11px; font-size: 8.5pt; font-weight: 700; }}
    QLineEdit, QTextEdit {{ background: {colors['field']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 13px; padding: 10px 13px; selection-background-color: {colors['blue']}; }}
    QLineEdit:focus, QTextEdit:focus {{ border: 1px solid {colors['accent']}; }}
    QLineEdit:disabled {{ color: {colors['muted']}; }}
    QPushButton {{ background: {colors['field']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 12px; padding: 9px 14px; font-weight: 650; }}
    QPushButton:hover {{ background: {colors['card_hover']}; border: 1px solid {colors['accent']}; }}
    QPushButton:pressed {{ background: {colors['soft_hover']}; }}
    QPushButton:disabled {{ color: {colors['muted2']}; background: {colors['field']}; border: 1px solid {colors['border']}; }}
    QPushButton#Primary {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {colors['blue']},stop:1 {colors['accent']}); color: {colors['primary_text']}; border: 1px solid {colors['accent']}; border-radius: 16px; font-size: 11pt; font-weight: 750; }}
    QPushButton#Primary:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {colors['accent']},stop:1 {colors['blue']}); }}
    QPushButton#Primary:disabled {{ background: {colors['field']}; color: {colors['muted2']}; border: 1px solid {colors['border']}; }}
    QPushButton#WindowButton {{ background: transparent; color: {colors['muted']}; border-radius: 10px; padding: 0; }}
    QPushButton#WindowButton:hover {{ background: {colors['card_hover']}; color: {colors['text']}; }}
    QPushButton#CloseButton {{ background: transparent; color: {colors['muted']}; border-radius: 10px; padding: 0; }}
    QPushButton#CloseButton:hover {{ background: {colors['danger']}; color: #FFFFFF; }}
    QPushButton#SongButton {{ background: transparent; color: {colors['muted']}; text-align: left; border-radius: 11px; font-weight: 500; }}
    QPushButton#SongButton:hover {{ background: {colors['card_hover']}; color: {colors['text']}; }}
    QPushButton#SongButton[active='true'] {{ background: {colors['soft']}; color: {colors['blue_text']}; }}
    QListWidget {{ background: transparent; border: none; outline: none; padding: 5px; }}
    QListWidget#BatchFiles, QListWidget#HubFiles {{ background: {colors['field']}; border: 1px solid {colors['border']}; border-radius: 14px; padding: 7px; }}
    QListWidget#HubFiles::item {{ padding: 7px 10px; margin: 1px; border-radius: 9px; }}
    QListWidget::item {{ color: {colors['text']}; border-radius: 10px; padding: 10px 12px; margin: 2px; }}
    QListWidget::item:hover {{ background: {colors['card_hover']}; }}
    QListWidget::item:selected {{ background: {colors['soft']}; color: {colors['blue_text']}; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 7px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {colors['scroll']}; border-radius: 3px; min-height: 32px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ background: {colors['field']}; height: 8px; margin: 0 5px; border-radius: 4px; }}
    QScrollBar::handle:horizontal {{ background: {colors['blue']}; border-radius: 4px; min-width: 52px; }}
    QScrollBar::handle:horizontal:hover {{ background: {colors['blue_hover']}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; height: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    QProgressBar#BatchProgress {{ background: {colors['field']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 10px; height: 20px; text-align: center; font-weight: 700; }}
    QProgressBar#BatchProgress::chunk {{ background: {colors['blue']}; border-radius: 9px; }}
    QSlider::groove:horizontal {{ height: 5px; background: {colors['field']}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {colors['blue']}; border-radius: 3px; }}
    QSlider::handle:horizontal {{ background: {colors['white']}; border: 2px solid {colors['blue']}; width: 14px; height: 14px; margin: -6px 0; border-radius: 8px; }}
    QSlider#SeekSlider::groove:horizontal {{ height: 8px; background: {colors['field']}; border-radius: 4px; }}
    QSlider#SeekSlider::sub-page:horizontal {{ background: {colors['blue']}; border-radius: 4px; }}
    QSlider#SeekSlider::handle:horizontal {{ background: {colors['white']}; border: 2px solid {colors['blue']}; width: 14px; height: 14px; margin: -4px 0; border-radius: 8px; }}
    QFrame#ComboSurface {{ background: {colors['field']}; border: 1px solid transparent; border-radius: 13px; }}
    QFrame#ComboSurface:hover {{ border: 1px solid {colors['blue']}; }}
    QFrame#PopupFrame {{ background: {colors['card']}; border: 1px solid {colors['border']}; border-radius: 16px; }}
    QDialog#RoundedDialog {{ background: transparent; }}
    QToolTip {{ background: {colors['card']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 8px; padding: 7px; }}
    QWidget#ContextPopup {{ background: transparent; }}
    QFrame#ContextPopupFrame {{ background: {colors['card']}; border: 1px solid {colors['border']}; border-radius: 14px; }}
    QPushButton#ContextAction {{ background: transparent; color: {colors['text']}; border: none; border-radius: 9px; padding: 9px 13px; text-align: left; font-weight: 500; }}
    QPushButton#ContextAction:hover {{ background: {colors['soft']}; color: {colors['blue_text']}; }}
    QPushButton#HotkeyCapture {{ background: {colors['field']}; color: {colors['text']}; border: 1px solid transparent; border-radius: 13px; padding: 0 7px; font-size: 9pt; }}
    QPushButton#HotkeyCapture:hover {{ border: 1px solid {colors['blue']}; }}
    QPushButton#HotkeyCapture[capturing='true'] {{ background: {colors['soft']}; color: {colors['blue_text']}; border: 1px solid {colors['blue']}; }}
    QPushButton#ModeButton {{ background: transparent; color: {colors['muted']}; border-radius: 10px; padding: 7px 13px; }}
    QPushButton#ModeButton:hover {{ background: {colors['card_hover']}; color: {colors['text']}; }}
    QPushButton#ModeButton:checked {{ background: {colors['soft']}; color: {colors['accent']}; border: 1px solid {colors['accent']}; }}
    """


class RoundedTooltip(QWidget):
    """Paint the whole tooltip surface so native square corners cannot show."""

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 9, 13, 9)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(320)
        layout.addWidget(self.label)
        self._colors = palette(True)

    def show_for(self, message: str, position: QPoint, dark: bool) -> None:
        self._colors = palette(dark)
        self.label.setText(message)
        self.label.setStyleSheet(f"color: {self._colors['text']}; background: transparent;")
        self.adjustSize()
        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        x = min(position.x() + 12, bounds.right() - self.width())
        y = min(position.y() + 20, bounds.bottom() - self.height())
        self.move(max(bounds.left(), x), max(bounds.top(), y))
        self.show()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(self._colors["border"]), 1))
        painter.setBrush(QColor(self._colors["card"]))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)


class TooltipManager(QObject):
    def __init__(self, owner: "MainWindow"):
        super().__init__(owner)
        self.owner = owner
        self.popup = RoundedTooltip()
        self.target: QWidget | None = None

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.ToolTip and isinstance(watched, QWidget):
            message = watched.toolTip()
            if isinstance(watched.parentWidget(), QListWidget):
                item = watched.parentWidget().itemAt(event.pos())
                message = item.toolTip() if item else ""
            if message:
                self.target = watched
                self.popup.show_for(message, event.globalPos(), self.owner.dark)
                event.accept()
                return True
        if watched is self.target and event.type() in (QEvent.Leave, QEvent.Hide, QEvent.MouseButtonPress):
            self.popup.hide()
            self.target = None
        return False


class ToggleSwitch(QAbstractButton):
    """Animated switch with explicit dark and light theme colors."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(40, 22)
        self.setCursor(Qt.PointingHandCursor)
        self._offset = 20.0 if checked else 2.0
        self._colors = palette(True)
        self.animation = QPropertyAnimation(self, b"offset", self)
        self.animation.setDuration(150)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def apply_theme(self, colors: dict[str, str]) -> None:
        self._colors = colors
        self.update()

    def _animate(self, checked: bool) -> None:
        self.animation.stop()
        self.animation.setStartValue(self._offset)
        self.animation.setEndValue(20.0 if checked else 2.0)
        self.animation.start()

    def get_offset(self) -> float:
        return self._offset

    def set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, get_offset, set_offset)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._colors["blue"] if self.isChecked() else self._colors["border"]))
        painter.drawRoundedRect(self.rect(), 11, 11)
        painter.setBrush(QColor("#FFFFFF" if self.isChecked() else self._colors["muted"]))
        painter.drawEllipse(int(self._offset), 2, 18, 18)


class SeekSlider(QSlider):
    """Progress slider that seeks immediately on clicks and while dragging."""

    seekRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setObjectName("SeekSlider")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(26)
        self._dragging = False

    def _value_at(self, x_position: float) -> int:
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), round(x_position), max(1, self.width())
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self._dragging = True
        self.setSliderDown(True)
        self.setValue(self._value_at(event.position().x()))
        self.sliderPressed.emit()
        self.sliderMoved.emit(self.value())
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._dragging:
            super().mouseMoveEvent(event)
            return
        self.setValue(self._value_at(event.position().x()))
        self.sliderMoved.emit(self.value())
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton or not self._dragging:
            super().mouseReleaseEvent(event)
            return
        self.setValue(self._value_at(event.position().x()))
        self._dragging = False
        self.setSliderDown(False)
        self.sliderReleased.emit()
        self.seekRequested.emit(self.value())
        event.accept()


class ComboPopup(QWidget):
    selected = Signal(str)

    def __init__(self, owner: "ModernCombo"):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.owner = owner
        self.setAttribute(Qt.WA_TranslucentBackground)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.frame = QFrame(objectName="PopupFrame")
        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(7, 7, 7, 7)
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.itemClicked.connect(self._choose)
        frame_layout.addWidget(self.list)
        layout.addWidget(self.frame)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.frame.setGraphicsEffect(shadow)

    def set_values(self, values: list[str]) -> None:
        self.list.clear()
        self.list.addItems(values)

    def _choose(self, item: QListWidgetItem) -> None:
        self.selected.emit(item.text())
        self.close()

    def hideEvent(self, event) -> None:
        self.owner.popup_hidden()
        super().hideEvent(event)


class ComboChevron(QWidget):
    """Small antialiased chevron that stays crisp at every display scale."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(22, 22)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._rotation = 0.0
        self.animation = QPropertyAnimation(self, b"rotation", self)
        self.animation.setDuration(190)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)

    def get_rotation(self) -> float:
        return self._rotation

    def set_rotation(self, value: float) -> None:
        self._rotation = float(value)
        self.update()

    rotation = Property(float, get_rotation, set_rotation)

    def set_opened(self, opened: bool) -> None:
        self.animation.stop()
        self.animation.setStartValue(self._rotation)
        self.animation.setEndValue(180.0 if opened else 0.0)
        self.animation.start()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        hovered = bool(self.parentWidget() and self.parentWidget().underMouse())
        painter.setPen(QPen(
            QColor("#3A9BFF" if hovered else "#86A6CC"),
            2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin,
        ))
        painter.translate(11, 11)
        painter.rotate(self._rotation)
        painter.drawLine(-6, -3, 0, 3)
        painter.drawLine(0, 3, 6, -3)


class AnimatedRefreshButton(PremiumButton):
    """Refresh control with visual feedback through a smooth full rotation."""

    def __init__(self, size: int, icon_size: QSize, parent=None):
        super().__init__("", parent)
        self.setFixedSize(size, size)
        self.setIconSize(icon_size)
        self._rotation = 0.0
        self._source = create_refresh_icon().pixmap(32, 32)
        self.animation = QPropertyAnimation(self, b"rotation", self)
        self.animation.setDuration(620)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.finished.connect(self._finish_spin)
        self.clicked.connect(self.spin)
        self._render_icon()

    def get_rotation(self) -> float:
        return self._rotation

    def set_rotation(self, value: float) -> None:
        self._rotation = float(value)
        self._render_icon()

    rotation = Property(float, get_rotation, set_rotation)

    def _render_icon(self) -> None:
        canvas = QPixmap(40, 40)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.translate(20, 20)
        painter.rotate(self._rotation)
        painter.drawPixmap(-17, -16, self._source)
        painter.end()
        self.setIcon(QIcon(canvas))

    def spin(self) -> None:
        start = self._rotation % 360.0
        self.animation.stop()
        self.animation.setStartValue(start)
        self.animation.setEndValue(start + 360.0)
        self.animation.start()

    def _finish_spin(self) -> None:
        self._rotation = 0.0
        self._render_icon()
        self.clearFocus()
        self._animate(0.0)


class HotkeyCaptureButton(QPushButton):
    hotkeyChanged = Signal(str)
    captureStateChanged = Signal(bool)

    def __init__(self, value: str, parent=None):
        super().__init__(value.upper(), parent)
        self.setObjectName("HotkeyCapture")
        self.setFixedSize(145, 44)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Anklicken und anschließend die gewünschte Taste drücken")
        self._value = value
        self._capturing = False
        self.clicked.connect(self.begin_capture)

    def begin_capture(self) -> None:
        if self._capturing:
            return
        self._capturing = True
        self.setProperty("capturing", True)
        self.setText("Taste drücken")
        self.setFocus(Qt.MouseFocusReason)
        self.grabKeyboard()
        self.style().unpolish(self)
        self.style().polish(self)
        self.captureStateChanged.emit(True)

    def finish_capture(self, value: str | None = None) -> None:
        if value:
            self._value = value.casefold()
            self.hotkeyChanged.emit(self._value)
        self._capturing = False
        self.releaseKeyboard()
        self.setProperty("capturing", False)
        self.setText(self._value.upper())
        self.style().unpolish(self)
        self.style().polish(self)
        self.captureStateChanged.emit(False)

    def keyPressEvent(self, event) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key_Escape:
            self.finish_capture()
            event.accept()
            return
        if event.key() in {Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta}:
            event.accept()
            return
        sequence = QKeySequence(event.keyCombination()).toString(QKeySequence.PortableText)
        if sequence:
            self.finish_capture(sequence)
        event.accept()

    def focusOutEvent(self, event) -> None:
        if self._capturing:
            self.finish_capture()
        super().focusOutEvent(event)


class ModernCombo(QFrame):
    currentTextChanged = Signal(str)

    def __init__(self, value: str = "", parent=None):
        super().__init__(parent, objectName="ComboSurface")
        self.setFixedHeight(44)
        self.setCursor(Qt.PointingHandCursor)
        self._values: list[str] = []
        self._popup_closed_at = 0.0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 13, 0)
        self.label = QLabel(value)
        self.label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.arrow = ComboChevron(self)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.arrow)
        self.popup = ComboPopup(self)
        self.popup.selected.connect(self.set_current)
        for widget in (self, self.label, self.arrow):
            widget.installEventFilter(self)

    def set_values(self, values: list[str]) -> None:
        self._values = values
        self.popup.set_values(values)

    def current_text(self) -> str:
        return self.label.text()

    def set_current(self, value: str, emit: bool = True) -> None:
        self.label.setText(value)
        if emit:
            self.currentTextChanged.emit(value)

    def show_popup(self) -> None:
        if not self._values:
            return
        if self.popup.isVisible():
            self.popup.close()
            return
        # Native Qt popups close on mouse-down. Suppress the following
        # mouse-release so a second click cannot immediately reopen it.
        if time.monotonic() - self._popup_closed_at < 0.22:
            return
        position = self.mapToGlobal(QPoint(0, self.height() + 6))
        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        height = min(330, max(70, len(self._values) * 40 + 30))
        width = max(180, self.width())
        x = min(position.x(), screen.availableGeometry().right() - width)
        y = position.y()
        if y + height > screen.availableGeometry().bottom():
            y = self.mapToGlobal(QPoint(0, 0)).y() - height - 6
        self.popup.setGeometry(x, y, width, height)
        self.popup.show()
        self.popup.raise_()
        self.arrow.set_opened(True)

    def popup_hidden(self) -> None:
        self._popup_closed_at = time.monotonic()
        self.arrow.set_opened(False)

    def eventFilter(self, watched, event) -> bool:
        if watched in (self, self.label, self.arrow) and event.type() == event.Type.MouseButtonRelease:
            self.show_popup()
            return True
        return super().eventFilter(watched, event)


class SongButton(QPushButton):
    """Antialiased library row with guaranteed rounding on both sides."""

    def __init__(self, text: str, colors: dict[str, str], favorite: bool = False, parent=None):
        super().__init__(text, parent)
        self._colors = colors
        self._favorite = favorite
        self._active = False
        self._hovered = False
        self.setFixedHeight(40)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)

    def apply_theme(self, colors: dict[str, str]) -> None:
        self._colors = colors
        self.update()

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        if self._active:
            painter.setBrush(QColor(self._colors["soft"]))
            painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 11, 11)
        elif self._hovered:
            painter.setBrush(QColor(self._colors["card_hover"]))
            painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 11, 11)
        left = 14
        if self._favorite:
            center_x, center_y = 18.0, self.height() / 2.0 + 1.0
            glow = QColor("#FFCA55")
            glow.setAlpha(38)
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(QPointF(center_x, center_y), 10.0, 10.0)
            star = QPainterPath()
            for index in range(10):
                angle = -math.pi / 2 + index * math.pi / 5
                radius = 7.2 if index % 2 == 0 else 3.2
                point = QPointF(
                    center_x + math.cos(angle) * radius,
                    center_y + math.sin(angle) * radius,
                )
                if index == 0:
                    star.moveTo(point)
                else:
                    star.lineTo(point)
            star.closeSubpath()
            painter.setPen(QPen(QColor("#FFE49A"), 1.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(QColor("#FFC247"))
            painter.drawPath(star)
            left = 32
        painter.setPen(QColor(self._colors["blue_text"] if self._active else self._colors["muted"]))
        text_rect = self.rect().adjusted(left, 0, -12, 0)
        text = painter.fontMetrics().elidedText(self.text(), Qt.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)


class SongContextPopup(QWidget):
    """Custom popup with a transparent native surface and rounded inner card."""

    actionSelected = Signal(str)

    def __init__(self, favorite: bool = False):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setObjectName("ContextPopup")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFixedWidth(225)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        frame = QFrame(objectName="ContextPopupFrame")
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)
        for label, action in (
            ("Aus Favoriten entfernen" if favorite else "Zu Favoriten hinzufügen", "favorite"),
            ("Im Explorer anzeigen", "reveal"),
            ("Song in Papierkorb", "remove"),
        ):
            button = PremiumButton(label, objectName="ContextAction")
            button.setFixedHeight(38)
            button.clicked.connect(lambda _checked=False, value=action: self.choose(value))
            layout.addWidget(button)

    def choose(self, action: str) -> None:
        self.close()
        self.actionSelected.emit(action)


class TitleBar(QFrame):
    def __init__(self, window: "MainWindow"):
        super().__init__(objectName="TitleBar")
        self.window = window
        self.setFixedHeight(48)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 3, 8, 3)
        icon = QLabel()
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(30, 30)
        icon.setPixmap(create_app_icon().pixmap(30, 30))
        name = QLabel("VP AUTOPLAYER", objectName="MutedSmall")
        name.setStyleSheet("font-weight:700;")
        layout.addWidget(icon)
        layout.addWidget(name)
        layout.addStretch()
        self.minimize = self._button("—", window.showMinimized)
        self.maximize = self._button("□", window.toggle_maximize)
        close = self._button("×", window.close, close=True)
        layout.addWidget(self.minimize)
        layout.addWidget(self.maximize)
        layout.addWidget(close)
        self.close_button = close
        self.apply_theme(palette(window.dark))

    @staticmethod
    def _button(text: str, callback, close: bool = False) -> QPushButton:
        button = PremiumButton("", objectName="CloseButton" if close else "WindowButton")
        button.setFixedSize(40, 34)
        button.clicked.connect(callback)
        return button

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and not self.window.isMaximized():
            handle = self.window.windowHandle()
            if handle:
                handle.startSystemMove()
        super().mousePressEvent(event)

    def apply_theme(self, colors: dict[str, str]) -> None:
        controls = (
            (self.minimize, "minimize", "Minimieren"),
            (self.maximize, "restore" if self.window.isMaximized() else "maximize", "Maximieren"),
            (self.close_button, "close", "Schlie\u00dfen"),
        )
        for button, kind, tooltip in controls:
            button.set_symbol(kind)
            button.setToolTip(tooltip)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.window.toggle_maximize()
        super().mouseDoubleClickEvent(event)


class LibraryFolderDialog(QDialog):
    """Browse folders while keeping the sheet files visible for orientation."""

    def __init__(self, parent: "MainWindow", initial: Path):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setObjectName("RoundedDialog")
        self.setFixedSize(680, 560)
        self.current_folder = (initial if initial.is_dir() else Path.home()).resolve()
        self.selected_folder: Path | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        card = QFrame(objectName="Card")
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 21, 24, 22)
        layout.setSpacing(13)

        heading = QHBoxLayout()
        heading.addWidget(QLabel("Bibliotheksordner auswählen", objectName="CardTitle"))
        heading.addStretch()
        close = PremiumButton("", objectName="CloseButton")
        close.set_symbol("close")
        close.setFixedSize(34, 34)
        close.clicked.connect(self.reject)
        heading.addWidget(close)
        layout.addLayout(heading)

        guidance = QLabel("Dateien sind zur Orientierung sichtbar. Öffne einen Ordner per Doppelklick.", objectName="Muted")
        layout.addWidget(guidance)

        navigation = QHBoxLayout()
        up = PremiumButton("↑")
        up.setFixedSize(42, 42)
        up.setToolTip("Eine Ordnerebene nach oben")
        up.clicked.connect(lambda: self.navigate(self.current_folder.parent))
        navigation.addWidget(up)
        self.path_entry = QLineEdit()
        self.path_entry.setFixedHeight(42)
        self.path_entry.returnPressed.connect(lambda: self.navigate(Path(self.path_entry.text())))
        navigation.addWidget(self.path_entry, 1)
        layout.addLayout(navigation)

        self.filter_entry = QLineEdit()
        self.filter_entry.setPlaceholderText("Dateien und Ordner suchen …")
        self.filter_entry.setFixedHeight(42)
        self.filter_entry.textChanged.connect(self.populate)
        layout.addWidget(self.filter_entry)

        self.entries = QListWidget()
        self.entries.setSpacing(2)
        self.entries.itemDoubleClicked.connect(self.open_entry)
        layout.addWidget(self.entries, 1)

        footer = QHBoxLayout()
        self.count_label = QLabel(objectName="MutedSmall")
        footer.addWidget(self.count_label, 1)
        cancel = PremiumButton("Abbrechen")
        cancel.setFixedHeight(44)
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        choose = PremiumButton("Diesen Ordner auswählen", objectName="Primary")
        choose.setFixedHeight(44)
        choose.clicked.connect(self.accept_current)
        footer.addWidget(choose)
        layout.addLayout(footer)
        self.navigate(self.current_folder)

    def navigate(self, folder: Path) -> None:
        folder = folder.expanduser()
        if not folder.is_dir():
            self.path_entry.setText(str(self.current_folder))
            return
        self.current_folder = folder.resolve()
        self.path_entry.setText(str(self.current_folder))
        self.path_entry.setCursorPosition(0)
        self.filter_entry.clear()
        self.populate()

    def populate(self) -> None:
        self.entries.clear()
        query = self.filter_entry.text().casefold().strip()
        try:
            children = sorted(self.current_folder.iterdir(), key=lambda path: (not path.is_dir(), path.name.casefold()))
        except OSError:
            children = []
        folders = files = 0
        for path in children:
            if query not in path.name.casefold():
                continue
            is_folder = path.is_dir()
            folders += is_folder
            files += not is_folder
            item = QListWidgetItem(f"{path.name}   ›" if is_folder else path.name)
            item.setData(Qt.UserRole, path)
            item.setToolTip(path.name)
            if not is_folder:
                item.setForeground(QColor(self.parent().colors["muted"]))
            self.entries.addItem(item)
        self.count_label.setText(f"{folders} Ordner  ·  {files} Dateien")

    def open_entry(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if isinstance(path, Path) and path.is_dir():
            self.navigate(path)

    def accept_current(self) -> None:
        self.selected_folder = self.current_folder
        self.accept()


class ImportDialog(QDialog):
    def __init__(self, parent: "MainWindow", source: Path):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setObjectName("RoundedDialog")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.is_midi = source.suffix.casefold() in MIDI_EXTENSIONS
        self.is_drums = CFG.instrument_mode == "drums"
        self.setFixedSize(540, 660 if self.is_drums else (520 if self.is_midi else 620))
        self.result_values: tuple[
            int | None, float | None, int, bool, str, float, float | None, bool,
        ] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        shell = QFrame(objectName="Card")
        outer.addWidget(shell)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 120))
        shell.setGraphicsEffect(shadow)
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(26, 22, 26, 24)

        top = QHBoxLayout()
        title = QLabel("Song importieren", objectName="CardTitle")
        close = PremiumButton("", objectName="CloseButton")
        close.set_symbol("close")
        close.setFixedSize(38, 34)
        close.clicked.connect(self.reject)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(close)
        layout.addLayout(top)
        heading = QLabel(
            "Spielbares Drum-Pattern erstellen" if self.is_drums else "Spielbares Sheet erstellen",
            objectName="SongTitle",
        )
        layout.addWidget(heading)
        source_label = QLabel(source.name, objectName="Muted")
        source_label.setWordWrap(True)
        layout.addWidget(source_label)
        auto_label = QLabel(
            "Drum-Anschläge, Tempo und Raster werden automatisch erkannt."
            if self.is_drums else "Tempo und Raster werden automatisch aus der Datei analysiert.",
            objectName="Muted",
        )
        auto_label.setWordWrap(True)
        layout.addWidget(auto_label)
        layout.addSpacing(12)

        fields = QHBoxLayout()
        self.bpm = self._field(fields, "BPM", "AUTO", readonly=True)
        self.subdiv = self._field(fields, "RASTER", "AUTO", readonly=True)
        self.transpose = QLineEdit("0") if self.is_drums else self._field(fields, "TRANSPONIEREN", "0")
        layout.addLayout(fields)
        layout.addSpacing(14)
        self.quality: QListWidget | None = None
        if self.is_drums:
            layout.addWidget(QLabel("DRUM-IMPORTART", objectName="MutedSmall"))
            self.quality = QListWidget()
            self.quality.setFixedHeight(100)
            self.quality.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.quality.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.quality.addItems([DRUM_ORIGINAL_PROFILE, DRUM_ARRANGEMENT_PROFILE])
            self.quality.setCurrentRow(0)
            layout.addWidget(self.quality)
            drum_mode = QLabel(
                "AUTOMATISCHE DRUM-ZUORDNUNG\n"
                "A Hi-Hat  ·  S Snare  ·  D/F Toms  ·  H Bassdrum  ·  J/K/L Becken  ·  ; Percussion",
                objectName="InfoLabel",
            )
            drum_mode.setText(
                "Standard nutzt nur die echte Schlagzeugspur. Optional werden Melodie, "
                "Bass und Akkorde zusätzlich auf Toms und Becken verteilt. "
                "Die Tasten je Spiel änderst du im Instrument Studio → Spielprofile."
            )
            drum_mode.setWordWrap(True)
            layout.addWidget(drum_mode)
        elif self.is_midi:
            midi_mode = QLabel(
                "MIDI-Direktimport: Noten, Akkorde und exaktes Timing werden automatisch aus der Datei übernommen.",
                objectName="InfoLabel",
            )
            midi_mode.setWordWrap(True)
            layout.addWidget(midi_mode)
        else:
            layout.addWidget(QLabel("ERKENNUNGSPROFIL", objectName="MutedSmall"))
            self.quality = QListWidget()
            self.quality.setFixedHeight(144)
            self.quality.addItems([AUTO_QUALITY_PROFILE, *QUALITY_PROFILES])
            self.quality.setCurrentRow(0)
            layout.addWidget(self.quality)
        section_fields = QHBoxLayout()
        self.section_start = self._field(section_fields, "STARTSEKUNDE", "0")
        self.section_end = self._field(section_fields, "ENDSEKUNDE", "")
        layout.addLayout(section_fields)
        self.fold = ToggleSwitch(True)
        self.fold.apply_theme(parent.colors)
        if not self.is_drums:
            fold_row = QHBoxLayout()
            fold_row.addWidget(self.fold)
            fold_row.addWidget(QLabel("Noten außerhalb C2–C7 automatisch verschieben", objectName="Muted"))
            fold_row.addStretch()
            layout.addLayout(fold_row)
        replace_row = QHBoxLayout()
        self.replace = ToggleSwitch(False)
        self.replace.apply_theme(parent.colors)
        replace_row.addWidget(self.replace)
        replace_row.addWidget(QLabel("Diesen Abschnitt im aktuellen Song ersetzen", objectName="Muted"))
        replace_row.addStretch()
        layout.addLayout(replace_row)
        layout.addStretch()
        actions = QHBoxLayout()
        cancel = PremiumButton("Abbrechen")
        confirm = PremiumButton("Importieren", objectName="Primary")
        cancel.setFixedHeight(46)
        confirm.setFixedHeight(46)
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self.confirm)
        actions.addWidget(cancel)
        actions.addWidget(confirm)
        layout.addLayout(actions)

    @staticmethod
    def _field(layout: QHBoxLayout, label: str, value: str, readonly: bool = False) -> QLineEdit:
        box = QVBoxLayout()
        box.addWidget(QLabel(label, objectName="MutedSmall"))
        entry = QLineEdit(value)
        entry.setReadOnly(readonly)
        entry.setAlignment(Qt.AlignCenter)
        entry.setFixedHeight(44)
        box.addWidget(entry)
        layout.addLayout(box, 1)
        return entry

    def confirm(self) -> None:
        try:
            values = (
                None,
                None,
                int(self.transpose.text()),
                False if self.is_drums else self.fold.isChecked(),
                (
                    self.quality.currentItem().text()
                    if self.is_drums and self.quality and self.quality.currentItem() else
                    DRUM_ORIGINAL_PROFILE if self.is_drums else
                    MIDI_QUALITY_PROFILE if self.is_midi else
                    self.quality.currentItem().text()
                    if self.quality and self.quality.currentItem() else AUTO_QUALITY_PROFILE
                ),
                max(0.0, float(self.section_start.text().replace(",", ".") or "0")),
                float(self.section_end.text().replace(",", ".")) if self.section_end.text().strip() else None,
                self.replace.isChecked(),
            )
        except ValueError:
            QMessageBox.warning(self, "Ungültige Werte", "Bitte trage gültige Zahlen ein.")
            return
        if values[6] is not None and values[6] <= values[5]:
            QMessageBox.warning(self, "Abschnitt", "Die Endsekunde muss hinter der Startsekunde liegen.")
            return
        if values[7] and not CFG.sheet:
            QMessageBox.warning(self, "Abschnitt", "Wähle zuerst den Song, dessen Abschnitt ersetzt werden soll.")
            return
        if values[7] and values[6] is None:
            QMessageBox.warning(self, "Abschnitt", "Zum Ersetzen wird eine Endsekunde benötigt.")
            return
        self.result_values = values
        self.accept()


class BatchImportDialog(QDialog):
    """Review, run and inspect a multi-file import without blocking the UI."""

    def __init__(self, parent: "MainWindow", sources: list[Path]):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setObjectName("RoundedDialog")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedSize(610, 640)
        self.sources = list(dict.fromkeys(Path(path) for path in sources))
        self.cancelled = threading.Event()
        self.running = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        shell = QFrame(objectName="Card")
        outer.addWidget(shell)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 125))
        shell.setGraphicsEffect(shadow)
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(26, 22, 26, 24)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        heading.addWidget(QLabel("STAPELIMPORT", objectName="Eyebrow"))
        heading.addStretch()
        close = PremiumButton("", objectName="CloseButton")
        close.set_symbol("close")
        close.setFixedSize(36, 32)
        close.clicked.connect(self.reject)
        heading.addWidget(close)
        layout.addLayout(heading)
        layout.addWidget(QLabel("Mehrere Songs importieren", objectName="SongTitle"))
        self.summary = QLabel("Prüfe deine Auswahl und starte anschließend den Import.", objectName="Muted")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        list_heading = QHBoxLayout()
        self.count_label = QLabel(objectName="CardTitle")
        list_heading.addWidget(self.count_label)
        list_heading.addStretch()
        self.remove_button = PremiumButton("Auswahl entfernen")
        self.remove_button.clicked.connect(self.remove_selected)
        list_heading.addWidget(self.remove_button)
        layout.addLayout(list_heading)
        self.file_list = QListWidget()
        self.file_list.setObjectName("BatchFiles")
        self.file_list.setSelectionMode(QListWidget.ExtendedSelection)
        layout.addWidget(self.file_list, 1)
        self._refresh_list()

        self.mode_label = QLabel(
            "DRUM-IMPORTART" if CFG.instrument_mode == "drums" else "AUDIO-ERKENNUNG",
            objectName="Eyebrow",
        )
        layout.addWidget(self.mode_label)
        self.profile = ModernCombo(
            DRUM_ORIGINAL_PROFILE if CFG.instrument_mode == "drums" else AUTO_QUALITY_PROFILE,
        )
        self.profile.set_values(
            [DRUM_ORIGINAL_PROFILE, DRUM_ARRANGEMENT_PROFILE]
            if CFG.instrument_mode == "drums" else
            [AUTO_QUALITY_PROFILE, *QUALITY_PROFILES],
        )
        layout.addWidget(self.profile)
        hint = QLabel(
            "MIDI wird mit Originalnoten und Timing importiert. Das Profil gilt nur für Audiodateien."
            if CFG.instrument_mode != "drums" else
            "Standard: nur echte Drumspuren. Die komplette Liedumwandlung ist optional.",
            objectName="MutedSmall",
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.progress = QProgressBar()
        self.progress.setObjectName("BatchProgress")
        self.progress.setRange(0, len(self.sources))
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        layout.addWidget(self.progress)
        self.progress_label = QLabel("Bereit", objectName="Muted")
        layout.addWidget(self.progress_label)
        actions = QHBoxLayout()
        self.cancel_button = PremiumButton("Abbrechen")
        self.cancel_button.clicked.connect(self.reject)
        self.start_button = PremiumButton("Import starten", objectName="Primary")
        self.start_button.clicked.connect(self.begin)
        for button in (self.cancel_button, self.start_button):
            button.setFixedHeight(46)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.start_button)
        layout.addLayout(actions)

    def _refresh_list(self) -> None:
        self.file_list.clear()
        for path in self.sources:
            item = QListWidgetItem(f"  {path.name}")
            item.setToolTip(str(path))
            self.file_list.addItem(item)
        self.count_label.setText(f"{len(self.sources)} Dateien")
        if hasattr(self, "start_button"):
            self.start_button.setEnabled(bool(self.sources))

    def remove_selected(self) -> None:
        selected = {item.row() for item in self.file_list.selectedIndexes()}
        self.sources = [path for index, path in enumerate(self.sources) if index not in selected]
        self._refresh_list()

    def begin(self) -> None:
        if not self.sources or self.running:
            return
        self.running = True
        self.start_button.setEnabled(False)
        self.remove_button.setEnabled(False)
        self.file_list.setSelectionMode(QListWidget.NoSelection)
        self.profile.setEnabled(False)
        self.cancel_button.setText("Nach dieser Datei stoppen")
        self.progress_label.setText("Import läuft …")
        self.parent().start_batch_import(self.sources, self.profile.current_text(), self.cancelled)

    def update_progress(self, completed: int, total: int, outcome: BatchOutcome) -> None:
        self.progress.setValue(completed)
        label = "Übersprungen" if outcome.skipped else "Fehler" if outcome.error else "Importiert"
        item = self.file_list.item(completed - 1)
        if item:
            detail = f": {outcome.error}" if outcome.error and not outcome.skipped else ""
            item.setText(
                f"  {'✓' if outcome.result else '–' if outcome.skipped else '✕'}  "
                f"{outcome.source.name}  ·  {label}{detail}",
            )
            item.setToolTip(outcome.error or str(outcome.result.path if outcome.result else outcome.source))
            if outcome.error and not outcome.skipped:
                item.setForeground(QColor(self.parent().colors["danger"]))
            elif outcome.result:
                item.setForeground(QColor(self.parent().colors["green"]))
        self.progress_label.setText(f"{completed} / {total} verarbeitet · {outcome.source.name}")

    def finish(self, outcomes: list[BatchOutcome]) -> None:
        self.running = False
        imported = sum(outcome.result is not None for outcome in outcomes)
        skipped = sum(outcome.skipped for outcome in outcomes)
        errors = sum(bool(outcome.error and not outcome.skipped) for outcome in outcomes)
        self.summary.setText(
            f"{imported} importiert  ·  {skipped} übersprungen  ·  {errors} fehlgeschlagen"
            + ("  ·  vorzeitig gestoppt" if len(outcomes) < len(self.sources) else ""),
        )
        self.progress_label.setText("Fertig. Details stehen direkt in der Dateiliste.")
        self.cancel_button.setText("Schließen")
        self.start_button.hide()

    def reject(self) -> None:
        if self.running:
            self.cancelled.set()
            self.progress_label.setText("Stopp angefordert – laufende Datei wird noch fertiggestellt …")
            self.cancel_button.setEnabled(False)
            return
        super().reject()


class MidiHubDialog(QDialog):
    """Search the external catalog without placing all MIDI files on disk."""

    PAGE_SIZE = 18

    def __init__(self, parent: "MainWindow"):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setObjectName("RoundedDialog")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(800, 720)
        self.setModal(True)
        self.items: list[HubItem] = []
        self.filtered: list[HubItem] = []
        self.page = 0
        self.busy = False
        self.preview_generation = 0
        self.preview_workspace: tempfile.TemporaryDirectory | None = None
        self.preview_playing = False
        self.midi_player = MidiPreviewPlayer()
        self.unavailable_ids: set[int] = set()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        shell = QFrame(objectName="Card")
        outer.addWidget(shell)
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(28, 22, 28, 24)
        layout.setSpacing(11)
        top = QHBoxLayout()
        top.addWidget(QLabel("MIDI-HUB · NANO MIDI", objectName="Eyebrow"))
        top.addStretch()
        close = PremiumButton("", objectName="CloseButton")
        close.set_symbol("close")
        close.setFixedSize(36, 36)
        close.clicked.connect(self.reject)
        top.addWidget(close)
        layout.addLayout(top)
        layout.addWidget(QLabel("Musik entdecken", objectName="SongTitle"))
        self.status = QLabel("Katalog wird geladen …", objectName="Muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Titel, Künstler oder Arrangeur suchen …")
        self.search.setFixedHeight(44)
        self.search.textChanged.connect(self.apply_filter)
        search_row.addWidget(self.search, 1)
        self.sort = ModernCombo("Neueste")
        self.sort.set_values(["Neueste", "Beliebteste", "Titel A–Z"])
        self.sort.setFixedWidth(160)
        self.sort.currentTextChanged.connect(self.apply_filter)
        search_row.addWidget(self.sort)
        layout.addLayout(search_row)
        self.list = QListWidget()
        self.list.setObjectName("HubFiles")
        self.list.currentRowChanged.connect(self.show_selection)
        self.list.itemDoubleClicked.connect(lambda _item: self.import_selected())
        layout.addWidget(self.list, 1)
        pages = QHBoxLayout()
        self.previous = PremiumButton("← Zurück")
        self.previous.clicked.connect(lambda: self.set_page(self.page - 1))
        self.page_label = QLabel("0 / 0", objectName="Muted")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.next = PremiumButton("Weiter →")
        self.next.clicked.connect(lambda: self.set_page(self.page + 1))
        pages.addWidget(self.previous)
        pages.addWidget(self.page_label, 1)
        pages.addWidget(self.next)
        layout.addLayout(pages)
        self.details = QLabel("Wähle einen Eintrag aus.", objectName="Muted")
        self.details.setWordWrap(True)
        self.details.setFixedHeight(42)
        layout.addWidget(self.details)
        source = QLabel(
            "Quelle: nanoMIDI.net · Nutzer-Uploads sind nicht automatisch frei lizenziert. "
            "Nur ausgewählte Dateien werden heruntergeladen; der Katalog füllt deinen Ordner nicht.",
            objectName="MutedSmall",
        )
        source.setWordWrap(True)
        layout.addWidget(source)
        if CFG.instrument_mode == "drums":
            self.drum_profile = ModernCombo(DRUM_ORIGINAL_PROFILE)
            self.drum_profile.set_values([DRUM_ORIGINAL_PROFILE, DRUM_ARRANGEMENT_PROFILE])
            layout.addWidget(self.drum_profile)
        else:
            self.drum_profile = None
        actions = QHBoxLayout()
        cancel = PremiumButton("Schließen")
        cancel.clicked.connect(self.reject)
        self.import_button = PremiumButton("Ausgewählte MIDI importieren", objectName="Primary")
        self.import_button.clicked.connect(self.import_selected)
        self.import_button.setEnabled(False)
        self.preview_button = PremiumButton("▶  Vorschau anhören")
        self.preview_button.clicked.connect(self.toggle_preview)
        self.preview_button.setEnabled(False)
        cancel.setFixedHeight(44)
        self.preview_button.setFixedHeight(44)
        self.import_button.setFixedHeight(44)
        actions.addWidget(cancel)
        actions.addWidget(self.preview_button)
        actions.addWidget(self.import_button)
        layout.addLayout(actions)

    def set_items(self, items: list[HubItem]) -> None:
        self.items = items
        self.status.setText(f"{len(items)} MIDI-Dateien im Online-Katalog · lokal wird noch nichts gespeichert.")
        self.apply_filter()

    def apply_filter(self, _value: str = "") -> None:
        query = self.search.text().casefold().strip()
        self.filtered = [
            item for item in self.items
            if query in f"{item.title} {item.artists} {item.arranger} {item.uploader}".casefold()
        ]
        sort = self.sort.current_text()
        if sort == "Beliebteste":
            self.filtered.sort(key=lambda item: (item.downloads, item.identifier), reverse=True)
        elif sort == "Titel A–Z":
            self.filtered.sort(key=lambda item: item.title.casefold())
        else:
            self.filtered.sort(key=lambda item: item.identifier, reverse=True)
        self.set_page(0)

    def set_page(self, page: int) -> None:
        pages = max(1, (len(self.filtered) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = max(0, min(page, pages - 1))
        self.list.clear()
        for item in self.filtered[self.page * self.PAGE_SIZE:(self.page + 1) * self.PAGE_SIZE]:
            row = QListWidgetItem(self.row_text(item))
            row.setToolTip(f"Arrangeur: {item.arranger or '–'}\nUploader: {item.uploader or '–'}\nDownloads: {item.downloads:,}")
            if item.identifier in self.unavailable_ids:
                row.setForeground(QColor("#8397A8"))
            self.list.addItem(row)
        self.page_label.setText(f"{self.page + 1} / {pages}  ·  {len(self.filtered)} Treffer")
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page + 1 < pages)
        self.import_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.details.setText("Wähle einen Eintrag aus.")

    def row_text(self, item: HubItem) -> str:
        suffix = "   ·   Zurzeit nicht verfügbar" if item.identifier in self.unavailable_ids else ""
        return f"  {item.title}   ·   {item.artists or 'Unbekannter Künstler'}{suffix}"

    def selected_item(self) -> HubItem | None:
        index = self.page * self.PAGE_SIZE + self.list.currentRow()
        return self.filtered[index] if self.list.currentRow() >= 0 and index < len(self.filtered) else None

    def show_selection(self, _row: int) -> None:
        self.stop_preview()
        item = self.selected_item()
        available = bool(item) and item.identifier not in self.unavailable_ids
        self.import_button.setEnabled(available and not self.busy)
        self.preview_button.setEnabled(available and not self.busy)
        if item:
            self.details.setText(
                f"{item.title} · {item.artists or 'Unbekannt'}\n"
                f"Arr.: {item.arranger or '–'}  ·  Upload: {item.uploader or '–'}  ·  {item.downloads:,} Downloads",
            )
            if item.identifier in self.unavailable_ids:
                self.status.setText("Diese MIDI-Datei ist derzeit nicht verfügbar. Wähle einen anderen Song.")

    def stop_preview(self) -> None:
        self.preview_generation += 1
        self.midi_player.stop()
        self.preview_playing = False
        self.preview_button.setText("▶  Vorschau anhören")
        if self.preview_workspace:
            self.preview_workspace.cleanup()
            self.preview_workspace = None

    def toggle_preview(self) -> None:
        if self.preview_playing:
            self.stop_preview()
            return
        item = self.selected_item()
        if not item or self.busy:
            return
        self.stop_preview()
        generation = self.preview_generation
        self.preview_button.setEnabled(False)
        self.status.setText(f"{item.title}: Original-MIDI wird geladen …")

        def worker() -> None:
            workspace = tempfile.TemporaryDirectory(prefix="vp-midi-preview-")
            try:
                root = Path(workspace.name)
                source = download_midi(item, root / item.filename)
                self.parent().queue.put(("hub_preview_ready", generation, item.identifier, workspace, source))
            except Exception as exc:
                workspace.cleanup()
                self.parent().queue.put(("hub_preview_error", generation, item.identifier, exc))

        threading.Thread(target=worker, daemon=True, name="vp-midi-hub-preview").start()

    def preview_ready(self, generation: int, identifier: int, workspace: tempfile.TemporaryDirectory, midi: Path) -> None:
        if generation != self.preview_generation or not self.isVisible() or not self.selected_item() or self.selected_item().identifier != identifier:
            workspace.cleanup()
            return
        self.preview_workspace = workspace
        try:
            self.midi_player.play(midi)
        except OSError:
            self.stop_preview()
            self.status.setText("Vorschau kann auf diesem PC nicht abgespielt werden. Prüfe den Windows-MIDI-Ausgang.")
            return
        self.preview_playing = True
        self.preview_button.setEnabled(True)
        self.preview_button.setText("■  Vorschau stoppen")
        self.status.setText("Original-MIDI läuft mit Windows-Instrumentklängen · max. 25 Sekunden.")
        QTimer.singleShot(26000, lambda: self.stop_preview() if self.preview_playing and generation == self.preview_generation else None)

    def preview_failed(self, generation: int, identifier: int, error: Exception) -> None:
        if generation != self.preview_generation or not self.selected_item() or self.selected_item().identifier != identifier:
            return
        if isinstance(error, HTTPError) and error.code in (404, 410):
            self.unavailable_ids.add(identifier)
            self.import_button.setEnabled(False)
            self.preview_button.setEnabled(False)
            row = self.list.currentItem()
            if row:
                row.setText(self.row_text(self.selected_item()))
                row.setForeground(QColor("#8397A8"))
        else:
            self.preview_button.setEnabled(True)
        self.status.setText(hub_error_message(error))

    def import_selected(self) -> None:
        item = self.selected_item()
        if not item or self.busy or item.identifier in self.unavailable_ids:
            return
        self.stop_preview()
        self.busy = True
        self.import_button.setEnabled(False)
        self.status.setText(f"{item.title}: Download und MIDI-Import laufen …")
        profile = self.drum_profile.current_text() if self.drum_profile else MIDI_QUALITY_PROFILE
        self.parent().start_hub_import(item, profile)

    def import_finished(self, message: str) -> None:
        self.busy = False
        self.status.setText(message)
        self.show_selection(self.list.currentRow())

    def reject(self) -> None:
        if self.busy:
            self.status.setText("Der laufende Import wird noch abgeschlossen. Danach kannst du schließen.")
            return
        self.stop_preview()
        super().reject()


class Bridge(QObject):
    toggle_requested = Signal()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        moved_sheets = core.ensure_library_layout(CFG.folder)
        if CFG.sheet in moved_sheets:
            CFG.sheet = moved_sheets[CFG.sheet]
            core.save_cfg()
        self.dark = bool(CFG.dark)
        self.colors = palette(self.dark)
        self.setWindowTitle("VP Autoplayer")
        self.setWindowIcon(create_app_icon())
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinMaxButtonsHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.resize(1180, 780)
        self.setMinimumSize(1040, 700)

        self.queue: Queue = Queue()
        self.player: core.Player | None = None
        self.total_tokens = 0
        self.completed_tokens = 0
        self.token_timings: list[float] = []
        self.token_holds: list[float] = []
        self._scrubbing = False
        self.filtered_sheets: list[Path] = []
        self.song_buttons: dict[Path, QPushButton] = {}
        self._pressed: set[Key | str] = set()
        self._capturing_hotkey = False
        self._toggle_combo = parse_combo(CFG.toggle)
        self._loading_sheet = False
        self._listener: Listener | None = None
        self.profile_store = ProfileStore()
        self.profile_store.activate(CFG.active_profile)

        self.root = QWidget(objectName="TransparentRoot")
        self.root_layout = QVBoxLayout(self.root)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.shell = QFrame(objectName="WindowShell")
        self.root_layout.addWidget(self.shell)
        self.setCentralWidget(self.root)
        self.shadow = QGraphicsDropShadowEffect(self)
        self.shadow.setBlurRadius(34)
        self.shadow.setOffset(0, 7)
        self.shadow.setColor(QColor(0, 0, 0, 115))
        self.shell.setGraphicsEffect(self.shadow)
        self.shadow.setEnabled(False)
        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setContentsMargins(14, 5, 14, 16)
        shell_layout.setSpacing(0)
        self.titlebar = TitleBar(self)
        shell_layout.addWidget(self.titlebar)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 7, 10, 0)
        body_layout.setSpacing(16)
        shell_layout.addWidget(body, 1)
        self._build_header(body_layout)
        content = QHBoxLayout()
        content.setSpacing(18)
        body_layout.addLayout(content, 1)
        self._build_library(content)
        self._build_workspace(content)

        self.bridge = Bridge()
        self.bridge.toggle_requested.connect(self.play_pressed)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_queue)
        self.poll_timer.start(60)
        self.note_timer = QTimer(self)
        self.note_timer.setSingleShot(True)
        self.note_timer.timeout.connect(self.save_note)
        self.apply_theme()
        self.tooltip_manager = TooltipManager(self)
        QApplication.instance().installEventFilter(self.tooltip_manager)
        self.refresh_windows()
        self.refresh_sheets(CFG.sheet)
        self.start_hotkey_listener()

    def _build_header(self, parent: QVBoxLayout) -> None:
        header = QFrame(objectName="Header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.header_title = QLabel("", objectName="Title")
        self.header_subtitle = QLabel("", objectName="Muted")
        text.addWidget(self.header_title)
        text.addWidget(self.header_subtitle)
        layout.addLayout(text)
        layout.addStretch()
        self.piano_mode = PremiumButton("Piano", objectName="ModeButton")
        self.drum_mode = PremiumButton("Drums", objectName="ModeButton")
        for button in (self.piano_mode, self.drum_mode):
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setFixedHeight(36)
            layout.addWidget(button)
        self.piano_mode.clicked.connect(lambda: self.switch_instrument_mode("piano"))
        self.drum_mode.clicked.connect(lambda: self.switch_instrument_mode("drums"))
        layout.addSpacing(10)
        self.studio_button = PremiumButton("Instrument Studio")
        self.studio_button.setFixedHeight(36)
        self.studio_button.setToolTip("Editor, Vorschau, Profile, Loops und Bibliothekswerkzeuge")
        self.studio_button.clicked.connect(self.open_studio)
        layout.addWidget(self.studio_button)
        layout.addSpacing(10)
        self.profile_badge = QLabel(CFG.active_profile.upper(), objectName="Badge")
        self.profile_badge.setFixedHeight(36)
        layout.addWidget(self.profile_badge)
        layout.addSpacing(10)
        self.status_chip = QLabel("●  BEREIT", objectName="HeaderBadge")
        self.status_chip.setFixedHeight(36)
        self.status_chip.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        layout.addWidget(self.status_chip)
        layout.addSpacing(10)
        self.topmost_switch = ToggleSwitch(CFG.always_on_top)
        self.topmost_switch.toggled.connect(self.toggle_topmost)
        layout.addWidget(self.topmost_switch)
        layout.addWidget(QLabel("Oben halten", objectName="MutedSmall"))
        layout.addSpacing(10)
        self.theme_button = PremiumButton("")
        self.theme_button.setFixedSize(36, 36)
        self.theme_button.setIcon(create_theme_icon(self.dark))
        self.theme_button.setIconSize(QSize(20, 20))
        self.theme_button.setToolTip("Zum hellen Design wechseln" if self.dark else "Zum dunklen Design wechseln")
        self.theme_button.clicked.connect(self.toggle_theme)
        layout.addWidget(self.theme_button)
        self.update_instrument_ui()
        parent.addWidget(header)

    def _build_library(self, parent: QHBoxLayout) -> None:
        card = QFrame(objectName="LibraryCard")
        card.setFixedWidth(300)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 17, 17, 17)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("Bibliothek", objectName="CardTitle"))
        heading.addStretch()
        self.song_count = QLabel("0 SONGS", objectName="Badge")
        self.song_count.setFixedHeight(27)
        heading.addWidget(self.song_count)
        refresh_library = AnimatedRefreshButton(32, QSize(20, 20))
        refresh_library.setToolTip("Bibliothek aktualisieren")
        refresh_library.clicked.connect(lambda: self.refresh_sheets(CFG.sheet))
        heading.addWidget(refresh_library)
        layout.addLayout(heading)
        folder = QHBoxLayout()
        self.folder_entry = QLineEdit(str(CFG.folder))
        self.folder_entry.setReadOnly(True)
        self.folder_entry.setCursorPosition(0)
        self.folder_entry.setToolTip(str(CFG.folder))
        self.folder_entry.setFixedHeight(44)
        browse = PremiumButton("")
        browse.setFixedSize(44, 44)
        browse.set_symbol("folder")
        browse.setToolTip("Bibliotheksordner wählen · Dateien bleiben im Dialog sichtbar")
        browse.clicked.connect(self.choose_folder)
        folder.addWidget(self.folder_entry, 1)
        folder.addWidget(browse)
        layout.addLayout(folder)
        self.search_entry = QLineEdit()
        self.search_entry.setPlaceholderText("Songs durchsuchen …")
        self.search_entry.setFixedHeight(44)
        self.search_entry.textChanged.connect(lambda _text: self.refresh_sheets())
        layout.addWidget(self.search_entry)
        self.sort_combo = ModernCombo("Favoriten zuerst")
        self.sort_combo.set_values(["Favoriten zuerst", "Name A–Z", "Neueste zuerst", "Dauer"])
        self.sort_combo.currentTextChanged.connect(lambda _value: self.refresh_sheets(CFG.sheet))
        layout.addWidget(self.sort_combo)
        self.song_scroll = QScrollArea()
        self.song_scroll.setWidgetResizable(True)
        self.song_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.song_container = QWidget()
        self.song_layout = QVBoxLayout(self.song_container)
        self.song_layout.setContentsMargins(0, 4, 10, 4)
        self.song_layout.setSpacing(3)
        self.song_layout.addStretch()
        self.song_scroll.setWidget(self.song_container)
        layout.addWidget(self.song_scroll, 1)
        self.import_button = PremiumButton(
            "Drums importieren" if CFG.instrument_mode == "drums" else "Audio & MIDI importieren",
            objectName="Primary",
        )
        self.import_button.setFixedHeight(48)
        self.import_button.setStyleSheet("font-size: 11pt; padding-left: 12px; padding-right: 12px;")
        self.import_button.setIcon(create_upload_icon())
        self.import_button.setIconSize(QSize(18, 18))
        self.import_button.setToolTip(
            "Schlagzeug-Audio oder General-MIDI-Drumspur importieren"
            if CFG.instrument_mode == "drums" else "Piano-Audio oder MIDI-Datei importieren"
        )
        self.import_button.clicked.connect(self.choose_import)
        layout.addWidget(self.import_button)
        self.hub_button = PremiumButton("MIDI-Hub durchsuchen")
        self.hub_button.setFixedHeight(40)
        self.hub_button.setToolTip("Online-Katalog öffnen · Songs werden erst nach deiner Auswahl importiert")
        self.hub_button.clicked.connect(self.open_midi_hub)
        layout.addWidget(self.hub_button)
        formats = QLabel("MP3  ·  WAV  ·  FLAC  ·  OGG  ·  M4A  ·  MIDI", objectName="MutedSmall")
        formats.setAlignment(Qt.AlignCenter)
        layout.addWidget(formats)
        open_folder = PremiumButton("Bibliotheksordner \u00f6ffnen")
        open_folder.setFixedHeight(38)
        open_folder.set_symbol("folder")
        open_folder.clicked.connect(self.open_library_folder)
        layout.addWidget(open_folder)
        parent.addWidget(card)

    def _build_workspace(self, parent: QHBoxLayout) -> None:
        workspace = QVBoxLayout()
        workspace.setSpacing(14)
        hero = QFrame(objectName="HeroCard")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(21, 18, 21, 18)
        top = QHBoxLayout()
        top.addWidget(QLabel("AKTUELLER SONG", objectName="Badge"))
        top.addStretch()
        self.live_badge = QLabel("●  IDLE", objectName="MutedSmall")
        top.addWidget(self.live_badge)
        hero_layout.addLayout(top)
        self.song_title = QLabel("Wähle einen Song", objectName="SongTitle")
        hero_layout.addWidget(self.song_title)
        self.song_meta = QLabel("Deine Sheets erscheinen links in der Bibliothek", objectName="Muted")
        hero_layout.addWidget(self.song_meta)
        hero_layout.addSpacing(8)
        hero_layout.addWidget(QLabel("Notiz zum Sheet", objectName="MutedSmall"))
        self.note_text = QTextEdit()
        self.note_text.setFixedHeight(70)
        self.note_text.textChanged.connect(lambda: self.note_timer.start(350))
        hero_layout.addWidget(self.note_text)
        progress_row = QHBoxLayout()
        self.progress = SeekSlider()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setToolTip("Klicken oder ziehen, um vor- und zurückzuspulen")
        self.progress.sliderPressed.connect(self.begin_seek)
        self.progress.sliderMoved.connect(self.preview_seek)
        self.progress.seekRequested.connect(self.seek_to)
        self.time_label = QLabel("00:00 / 00:00", objectName="MutedSmall")
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.time_label)
        hero_layout.addLayout(progress_row)
        workspace.addWidget(hero, 1)

        playback = QFrame(objectName="PlaybackCard")
        playback_layout = QVBoxLayout(playback)
        playback_layout.setContentsMargins(19, 16, 19, 17)
        playback_layout.addWidget(QLabel("Playback", objectName="CardTitle"))
        controls = QHBoxLayout()
        controls.setSpacing(12)
        self.bpm_entry = self._number_control(controls, "BPM", str(CFG.bpm))
        self.subdiv_entry = self._number_control(controls, "RASTER", str(CFG.subdiv))
        self.human_slider, self.human_label = self._slider_control(controls, "HUMANIZE", round(CFG.human * 100))
        self.human_slider.setToolTip(
            "Variiert Einsätze und Notenlängen dezent. 0 % spielt exakt; 100 % setzt hörbare, kontrollierte Akzente. "
            "Wirkt auf die Tasten im Zielfenster, nicht auf die Original-MIDI-Vorschau. "
            "Die Tastatur kann keine Anschlagstärke an das Spiel übertragen."
        )
        self.hold_slider, self.hold_label = self._slider_control(controls, "TASTENDAUER", round(CFG.hold * 100))
        playback_layout.addLayout(controls)
        self.bpm_entry.textChanged.connect(self.timing_changed)
        self.subdiv_entry.textChanged.connect(self.timing_changed)
        self.human_slider.valueChanged.connect(self.expression_changed)
        self.hold_slider.valueChanged.connect(self.expression_changed)
        workspace.addWidget(playback)

        target = QFrame(objectName="TargetCard")
        target_layout = QHBoxLayout(target)
        target_layout.setContentsMargins(18, 12, 18, 12)
        target_layout.addWidget(QLabel("Zielfenster", objectName="CardTitle"))
        self.window_combo = ModernCombo(CFG.target_title or "")
        self.window_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.window_combo.currentTextChanged.connect(lambda _value: self.target_changed())
        target_layout.addWidget(self.window_combo, 1)
        refresh = AnimatedRefreshButton(44, QSize(18, 18))
        refresh.setToolTip("Zielfenster aktualisieren")
        refresh.clicked.connect(self.refresh_windows)
        target_layout.addWidget(refresh)
        self.focus_switch = ToggleSwitch(CFG.auto_pause)
        self.focus_switch.toggled.connect(self.target_changed)
        target_layout.addWidget(self.focus_switch)
        target_layout.addWidget(QLabel("Nur im Fokus", objectName="MutedSmall"))
        self.hotkey_entry = HotkeyCaptureButton(CFG.toggle)
        self.hotkey_entry.hotkeyChanged.connect(self.hotkey_changed)
        self.hotkey_entry.captureStateChanged.connect(self.set_hotkey_capture_state)
        target_layout.addWidget(self.hotkey_entry)
        workspace.addWidget(target)

        self.play_button = PremiumButton("▶   Wiedergabe starten     F4", objectName="Primary")
        self.play_button.setFixedHeight(60)
        self.play_button.clicked.connect(self.play_pressed)
        workspace.addWidget(self.play_button)
        parent.addLayout(workspace, 1)

    @staticmethod
    def _number_control(parent: QHBoxLayout, label: str, value: str) -> QLineEdit:
        box = QVBoxLayout()
        box.addWidget(QLabel(label, objectName="MutedSmall"))
        entry = QLineEdit(value)
        entry.setAlignment(Qt.AlignCenter)
        entry.setFixedHeight(44)
        box.addWidget(entry)
        parent.addLayout(box, 1)
        return entry

    @staticmethod
    def _slider_control(parent: QHBoxLayout, name: str, value: int) -> tuple[QSlider, QLabel]:
        box = QVBoxLayout()
        label = QLabel(f"{name}  {value}%", objectName="MutedSmall")
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(value)
        slider.setFixedHeight(28)
        slider.valueChanged.connect(lambda current: label.setText(f"{name}  {current}%"))
        box.addWidget(label)
        box.addWidget(slider)
        parent.addLayout(box, 1)
        return slider, label

    def apply_theme(self) -> None:
        self.colors = palette(self.dark)
        QApplication.instance().setProperty("vpUiColors", self.colors)
        QApplication.instance().setStyleSheet(app_stylesheet(self.colors, self.isMaximized()))
        for switch in (self.topmost_switch, self.focus_switch):
            switch.apply_theme(self.colors)
        for button in self.song_buttons.values():
            button.apply_theme(self.colors)
        self.titlebar.apply_theme(self.colors)
        self.theme_button.setIcon(create_theme_icon(self.dark))
        self.theme_button.setToolTip("Zum hellen Design wechseln" if self.dark else "Zum dunklen Design wechseln")

    def toggle_theme(self) -> None:
        self.dark = not self.dark
        CFG.dark = self.dark
        self.apply_theme()

    def update_instrument_ui(self) -> None:
        drums = CFG.instrument_mode == "drums"
        self.piano_mode.setChecked(not drums)
        self.drum_mode.setChecked(drums)
        self.header_title.setText("Dein Beat. Dein Flow." if drums else "Dein Piano. Dein Flow.")
        self.header_subtitle.setText(
            "Schlagzeug-Autoplayer für Spiele mit automatischer Drum-Zuordnung."
            if drums else "Universeller Instrument-Autoplayer für Spiele und virtuelle Pianos."
        )
        if hasattr(self, "import_button"):
            self.import_button.setText("Drums importieren" if drums else "Audio & MIDI importieren")
            self.import_button.setToolTip(
                "Schlagzeug-Audio oder General-MIDI-Drumspur importieren"
                if drums else "Piano-Audio oder MIDI-Datei importieren"
            )

    def switch_instrument_mode(self, mode: str) -> None:
        mode = "drums" if mode == "drums" else "piano"
        if CFG.instrument_mode == mode:
            self.update_instrument_ui()
            return
        self.stop_player()
        CFG.instrument_mode = mode
        CFG.sheet = None
        core.save_cfg()
        self.update_instrument_ui()
        self.song_title.setText("Wähle einen Drum-Track" if mode == "drums" else "Wähle einen Song")
        self.song_meta.setText("Deine Drum-Patterns erscheinen links" if mode == "drums" else "Deine Sheets erscheinen links")
        self.note_text.clear()
        self.token_timings = []
        self.token_holds = []
        self.total_tokens = 0
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.update_time()
        self.refresh_sheets()

    def toggle_topmost(self, checked: bool) -> None:
        CFG.always_on_top = checked
        core.save_cfg()
        try:
            set_window_pos = ctypes.windll.user32.SetWindowPos
            set_window_pos.argtypes = [
                wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, wintypes.UINT,
            ]
            set_window_pos.restype = wintypes.BOOL
            insert_after = ctypes.c_void_p(-1 if checked else -2)
            flags = 0x0001 | 0x0002 | 0x0010 | 0x0020  # NOSIZE | NOMOVE | NOACTIVATE | FRAMECHANGED
            if not set_window_pos(wintypes.HWND(int(self.winId())), insert_after, 0, 0, 0, 0, flags):
                raise ctypes.WinError()
            if checked:
                self.raise_()
        except Exception:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
            self.show()

    def toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self.root_layout.setContentsMargins(0, 0, 0, 0)
            self.shadow.setEnabled(False)
            self.titlebar.maximize.setText("□")
        else:
            self.shadow.setEnabled(False)
            self.root_layout.setContentsMargins(0, 0, 0, 0)
            self.showMaximized()
            self.titlebar.maximize.setText("❐")
        self.apply_theme()

    def choose_folder(self) -> None:
        dialog = LibraryFolderDialog(self, CFG.folder)
        if dialog.exec() == QDialog.Accepted and dialog.selected_folder:
            selected = dialog.selected_folder
            try:
                core.ensure_library_layout(selected)
            except OSError as exc:
                QMessageBox.warning(self, "Bibliotheksordner", f"Der Ordner konnte nicht vorbereitet werden: {exc}")
                return
            self.stop_player()
            CFG.folder = selected
            CFG.sheet = None
            self.clear_selected_sheet()
            self.folder_entry.setText(str(selected))
            self.folder_entry.setCursorPosition(0)
            self.folder_entry.setToolTip(str(selected))
            self.refresh_sheets()
            core.save_cfg()

    def open_library_folder(self) -> None:
        folder = core.mode_sheet_folder(CFG.folder, CFG.instrument_mode)
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def refresh_sheets(self, select: Path | None = None) -> None:
        query = self.search_entry.text().casefold().strip()
        try:
            sheets = core.discover_sheets(core.mode_sheet_folder(CFG.folder, CFG.instrument_mode))
        except OSError:
            sheets = []
        metadata = {path: core.read_sheet_meta(path) for path in sheets}
        if CFG.sheet and CFG.sheet not in sheets:
            self.stop_player()
            CFG.sheet = None
            self.clear_selected_sheet()
        elif not sheets and CFG.sheet is None:
            self.clear_selected_sheet()
        sheets = [
            path for path in sheets
            if str(metadata[path].get("instrument_mode", "piano")) == CFG.instrument_mode
        ]
        sort_mode = self.sort_combo.current_text()
        if sort_mode == "Neueste zuerst":
            sheets.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
        elif sort_mode == "Dauer":
            sheets.sort(key=lambda path: sum(metadata[path].get("timings", [])))
        elif sort_mode == "Name A–Z":
            sheets.sort(key=lambda path: path.stem.casefold())
        else:
            sheets.sort(key=lambda path: (
                not bool(metadata[path].get("favorite")),
                path.stem.casefold(),
            ))
        self.filtered_sheets = [
            path for path in sheets
            if query in " ".join([
                path.stem, *[str(tag) for tag in metadata[path].get("tags", [])],
            ]).casefold()
        ]
        self.song_count.setText(f"{len(self.filtered_sheets)} SONGS")
        while self.song_layout.count() > 1:
            item = self.song_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.song_buttons.clear()
        for path in self.filtered_sheets:
            favorite = bool(metadata[path].get("favorite"))
            button = SongButton(path.stem, self.colors, favorite)
            button.setToolTip("Rechtsklick: Favorit, Datei anzeigen oder Song l\u00f6schen")
            button.clicked.connect(lambda _checked=False, chosen=path: self.load_sheet(chosen))
            button.setContextMenuPolicy(Qt.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda point, chosen=path, owner=button: self.show_song_menu(owner, point, chosen)
            )
            self.song_layout.insertWidget(self.song_layout.count() - 1, button)
            self.song_buttons[path] = button
        desired = select or CFG.sheet
        if desired:
            match = next((path for path in self.filtered_sheets if path.resolve() == Path(desired).resolve()), None)
            if match:
                self.load_sheet(match)
                return
        if self.filtered_sheets and not query:
            self.load_sheet(self.filtered_sheets[0])

    def clear_selected_sheet(self) -> None:
        self.song_title.setText("Wähle einen Drum-Track" if CFG.instrument_mode == "drums" else "Wähle einen Song")
        self.song_meta.setText("Deine Drum-Patterns erscheinen links" if CFG.instrument_mode == "drums" else "Deine Sheets erscheinen links")
        self.note_text.blockSignals(True)
        self.note_text.clear()
        self.note_text.blockSignals(False)
        self.token_timings = []
        self.token_holds = []
        self.total_tokens = 0
        self.completed_tokens = 0
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.update_time()

    def show_song_menu(self, owner: QWidget, point: QPoint, path: Path) -> None:
        popup = SongContextPopup(bool(core.read_sheet_meta(path).get("favorite")))
        popup.actionSelected.connect(lambda action, chosen=path: self.handle_song_action(action, chosen))
        popup.adjustSize()
        position = owner.mapToGlobal(point)
        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        x = min(position.x(), bounds.right() - popup.width())
        y = min(position.y(), bounds.bottom() - popup.height())
        popup.move(max(bounds.left(), x), max(bounds.top(), y))
        self._song_popup = popup
        popup.show()
        popup.raise_()

    def handle_song_action(self, action: str, path: Path) -> None:
        if action == "favorite":
            meta = core.read_sheet_meta(path)
            core.write_sheet_meta(path, extra={"favorite": not bool(meta.get("favorite"))})
            self.refresh_sheets(path)
        elif action == "reveal":
            target = str(path.resolve())
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "open", "explorer.exe", f'/select,"{target}"', None, 1,
            )
            if result <= 32:
                os.startfile(path.parent)
        elif action == "remove":
            answer = QMessageBox.question(
                self, "Song in Papierkorb verschieben",
                f'"{path.name}" in den Papierkorb verschieben?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer == QMessageBox.Yes:
                if CFG.sheet and Path(CFG.sheet) == path:
                    self.stop_player()
                    CFG.sheet = None
                try:
                    trash_song(path)
                except OSError as exc:
                    QMessageBox.critical(self, "Verschieben fehlgeschlagen", str(exc))
                    return
                self.refresh_sheets()

    def load_sheet(self, path: Path) -> None:
        if self.player:
            self.stop_player()
        CFG.sheet = path
        for sheet, button in self.song_buttons.items():
            button.set_active(sheet == path)
        meta = core.read_sheet_meta(path)
        CFG.instrument_mode = "drums" if meta.get("instrument_mode") == "drums" else "piano"
        self.update_instrument_ui()
        profile_name = str(meta.get("game_profile", CFG.active_profile))
        if profile_name in self.profile_store.profiles:
            self.profile_store.activate(profile_name)
        self.profile_badge.setText(CFG.active_profile.upper())
        CFG.human = max(0.0, min(1.0, float(meta.get("human", CFG.human))))
        CFG.hold = max(0.0, min(1.0, float(meta.get("hold", CFG.hold))))
        CFG.playback_speed = max(0.25, min(2.0, float(meta.get("playback_speed", CFG.playback_speed))))
        self.human_slider.blockSignals(True)
        self.hold_slider.blockSignals(True)
        self.human_slider.setValue(round(CFG.human * 100))
        self.hold_slider.setValue(round(CFG.hold * 100))
        self.human_label.setText(f"HUMANIZE  {round(CFG.human * 100)}%")
        self.hold_label.setText(f"TASTENDAUER  {round(CFG.hold * 100)}%")
        self.human_slider.blockSignals(False)
        self.hold_slider.blockSignals(False)
        try:
            self.token_timings = [float(value) for value in meta.get("timings", [])]
            self.token_holds = [float(value) for value in meta.get("holds", [])]
        except (TypeError, ValueError):
            self.token_timings = []
            self.token_holds = []
        dynamic_timing = bool(self.token_timings and meta.get("dynamic_timing"))
        self._loading_sheet = True
        self.bpm_entry.setText(str(meta.get("bpm", CFG.bpm)))
        self.subdiv_entry.setReadOnly(False)
        self.subdiv_entry.setText(f"{float(meta.get('subdiv', CFG.subdiv)):g}")
        CFG.bpm = max(1, int(meta.get("bpm", CFG.bpm)))
        CFG.subdiv = max(0.1, float(meta.get("subdiv", CFG.subdiv)))
        self.note_text.blockSignals(True)
        self.note_text.setPlainText(meta.get("note", ""))
        self.note_text.blockSignals(False)
        self._loading_sheet = False
        self.song_title.setText(path.stem)
        try:
            self.total_tokens = len(list(core.tokenise(path.read_text(encoding="utf-8-sig"))))
            self.song_meta.setText(
                f"{self.total_tokens:,} Noten & Pausen   ·   {CFG.bpm} BPM   ·   Raster {CFG.subdiv:g}"
            )
        except OSError:
            self.total_tokens = 0
            self.song_meta.setText("Das Sheet konnte nicht gelesen werden")
        if dynamic_timing and len(self.token_timings) == self.total_tokens:
            self.song_meta.setText(
                f"{self.total_tokens:,} Spielschritte   \u00b7   {CFG.bpm} BPM   \u00b7   "
                f"Raster {CFG.subdiv:g}   \u00b7   Dynamisches Timing"
            )
        elif dynamic_timing:
            self.token_timings = []
            self.token_holds = []
            self._loading_sheet = True
            self.subdiv_entry.setReadOnly(False)
            self.subdiv_entry.setText(str(CFG.subdiv))
            self._loading_sheet = False
        self.completed_tokens = 0
        self.progress.setRange(0, max(1, self.total_tokens))
        self.progress.setValue(0)
        self.update_time()

    def timing_changed(self) -> None:
        if self._loading_sheet:
            return
        previous_bpm = max(1, int(CFG.bpm))
        previous_subdiv = max(0.1, float(CFG.subdiv))
        try:
            new_bpm = max(1, int(self.bpm_entry.text()))
            new_subdiv = max(0.1, float(self.subdiv_entry.text().replace(",", ".")))
        except ValueError:
            return
        if self.token_timings and (new_bpm != previous_bpm or new_subdiv != previous_subdiv):
            scale = (previous_bpm / new_bpm) * (previous_subdiv / new_subdiv)
            self.token_timings = [value * scale for value in self.token_timings]
            self.token_holds = [value * scale for value in self.token_holds]
        CFG.bpm = new_bpm
        CFG.subdiv = new_subdiv
        if CFG.sheet:
            core.write_sheet_meta(
                CFG.sheet, CFG.bpm, CFG.subdiv, self.note_text.toPlainText().strip(),
                self.token_timings or None, self.token_holds or None,
            )
            self.song_meta.setText(
                f"{self.total_tokens:,} Noten & Pausen   ·   {CFG.bpm} BPM   ·   Raster {CFG.subdiv:g}"
            )
            if self.token_timings:
                self.song_meta.setText(
                    f"{self.total_tokens:,} Spielschritte   \u00b7   {CFG.bpm} BPM   \u00b7   "
                    f"Raster {CFG.subdiv:g}   \u00b7   Dynamisches Timing"
                )
        self.update_time()

    def expression_changed(self) -> None:
        CFG.human = self.human_slider.value() / 100
        CFG.hold = self.hold_slider.value() / 100
        if CFG.sheet:
            core.write_sheet_meta(
                CFG.sheet,
                extra={
                    "human": CFG.human,
                    "hold": CFG.hold,
                    "playback_speed": CFG.playback_speed,
                    "game_profile": CFG.active_profile,
                },
            )

    def save_note(self) -> None:
        if CFG.sheet:
            core.write_sheet_meta(CFG.sheet, CFG.bpm, CFG.subdiv, self.note_text.toPlainText().strip())

    def refresh_windows(self) -> None:
        previous = self.window_combo.current_text()
        values = [title for _handle, title in core.windows() if title != self.windowTitle()]
        display = ["Zielfenster auswählen", *values] if values else ["Kein Fenster gefunden"]
        self.window_combo.set_values(display)
        if previous in values:
            selected = previous
        else:
            selected = display[0]
        self.window_combo.set_current(selected, emit=False)
        self.target_changed()

    def target_changed(self) -> None:
        selected = self.window_combo.current_text().strip()
        CFG.target_title = None if selected in {"Kein Fenster gefunden", "Zielfenster auswählen"} else selected or None
        CFG.auto_pause = self.focus_switch.isChecked()

    def hotkey_changed(self, text: str) -> None:
        combo = parse_combo(text.strip())
        if combo:
            CFG.toggle = text.strip()
            self._toggle_combo = combo
            self._pressed.clear()
            self.update_play_button()

    def set_hotkey_capture_state(self, active: bool) -> None:
        self._capturing_hotkey = bool(active)
        if active:
            self._pressed.clear()

    def start_hotkey_listener(self) -> None:
        self._listener = Listener(on_press=self.hotkey_press, on_release=self.hotkey_release)
        self._listener.daemon = True
        self._listener.start()

    @staticmethod
    def normalise_key(key) -> Key | str:
        return key.char.casefold() if hasattr(key, "char") and key.char else key

    def hotkey_press(self, key) -> None:
        if self._capturing_hotkey:
            return
        normalised = self.normalise_key(key)
        if normalised in self._toggle_combo:
            was_complete = self._toggle_combo.issubset(self._pressed)
            self._pressed.add(normalised)
            if not was_complete and self._toggle_combo.issubset(self._pressed):
                self.bridge.toggle_requested.emit()

    def hotkey_release(self, key) -> None:
        self._pressed.discard(self.normalise_key(key))

    def play_pressed(self) -> None:
        if not CFG.sheet or not CFG.sheet.exists():
            QMessageBox.information(self, "Song auswählen", "Wähle zuerst einen Song aus der Bibliothek.")
            return
        if self.focus_switch.isChecked() and not CFG.target_title:
            QMessageBox.warning(self, "Zielfenster auswählen", "Wähle zuerst das Fenster deines Spiels aus.")
            return
        if self.player:
            self.player.toggle()
            self.set_status("SPIELT" if self.player.is_playing else "PAUSE")
        else:
            if self.completed_tokens >= self.total_tokens:
                self.completed_tokens = 0
                self.progress.setValue(0)
                self.update_time()
            self.player = core.Player(CFG.sheet, self.queue, self.completed_tokens)
            self.player.start()
            self.set_status("SPIELT")
        self.update_play_button()

    def stop_player(self) -> None:
        if self.player:
            self.player.stop()
            self.player = None
        self.set_status("BEREIT")
        self.update_play_button()

    def update_play_button(self) -> None:
        hotkey = CFG.toggle.upper() or "F4"
        text = f"Ⅱ   Wiedergabe pausieren     {hotkey}" if self.player and self.player.is_playing \
            else f"▶   Wiedergabe starten     {hotkey}"
        self.play_button.setText(text)

    def set_status(self, value: str) -> None:
        self.status_chip.setText(f"●  {value}")
        self.live_badge.setText("●  LIVE" if value == "SPIELT" else f"●  {value}")
        self.live_badge.setStyleSheet(f"color:{self.colors['green'] if value == 'SPIELT' else self.colors['muted2']};")

    def update_time(self, token_position: int | None = None) -> None:
        completed = self.completed_tokens if token_position is None else token_position
        if self.token_timings and len(self.token_timings) == self.total_tokens:
            elapsed = sum(self.token_timings[:max(0, min(completed, self.total_tokens))])
            total = sum(self.token_timings)
        else:
            unit = CFG.sec_per_token()
            elapsed, total = completed * unit, self.total_tokens * unit
        display = lambda seconds: f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"
        self.time_label.setText(f"{display(elapsed)} / {display(total)}")

    def begin_seek(self) -> None:
        self._scrubbing = True

    def preview_seek(self, token_position: int) -> None:
        self.update_time(token_position)

    def _discard_playback_messages(self) -> None:
        retained: list[tuple] = []
        try:
            while True:
                item = self.queue.get_nowait()
                if item and item[0] not in {"total", "progress", "error", "done"}:
                    retained.append(item)
        except Empty:
            pass
        for item in retained:
            self.queue.put(item)

    def seek_to(self, token_position: int) -> None:
        target = max(0, min(int(token_position), self.total_tokens))
        was_active = self.player is not None
        was_playing = bool(self.player and self.player.is_playing)

        if self.player:
            old_player = self.player
            old_player.stop()
            old_player.join(timeout=1.0)
            self.player = None
            self._discard_playback_messages()

        self._scrubbing = False
        self.completed_tokens = target
        self.progress.setValue(target)
        self.update_time()

        if was_active and CFG.sheet and target < self.total_tokens:
            self.player = core.Player(CFG.sheet, self.queue, target)
            if not was_playing:
                self.player.resume_event.clear()
            self.player.start()
            self.set_status("SPIELT" if was_playing else "PAUSE")
        else:
            self.set_status("BEREIT")
        self.update_play_button()

    def open_studio(self) -> None:
        from studio import StudioDialog

        dialog = getattr(self, "_studio_window", None)
        if dialog is None:
            dialog = StudioDialog(self)
            self._studio_window = dialog
        else:
            dialog.reload()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def dragEnterEvent(self, event) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
        if any(path.suffix.casefold() in AUDIO_EXTENSIONS | MIDI_EXTENSIONS for path in paths):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        sources = [
            Path(url.toLocalFile()) for url in event.mimeData().urls()
            if Path(url.toLocalFile()).is_file()
            and Path(url.toLocalFile()).suffix.casefold() in SUPPORTED_EXTENSIONS
        ]
        if sources:
            self.open_import_selection(sources)
            event.acceptProposedAction()

    def start_practice_mode(self, start: float, end: float, speed: float, loops: int) -> None:
        if not CFG.sheet or end <= start:
            raise ValueError("Ungueltiger Uebungsbereich")
        timings = self.token_timings or [CFG.sec_per_token()] * self.total_tokens
        elapsed = 0.0
        start_index = 0
        end_index = self.total_tokens
        for index, duration in enumerate(timings):
            if elapsed <= start:
                start_index = index
            if elapsed >= end:
                end_index = index
                break
            elapsed += duration
        self.stop_player()
        self.player = core.Player(CFG.sheet, self.queue, start_index, end_index, speed, loops)
        self.player.start()
        self.set_status("UEBEN")
        self.update_play_button()

    def choose_import(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTENSIONS))
        sources, _filter = QFileDialog.getOpenFileNames(
            self, "Audio- und MIDI-Dateien auswählen", "", f"Audio und MIDI ({patterns})",
        )
        if not sources:
            return
        self.open_import_selection([Path(source) for source in sources])

    def open_midi_hub(self) -> None:
        dialog = MidiHubDialog(self)
        self.hub_dialog = dialog

        def fetch() -> None:
            try:
                self.queue.put(("hub_loaded", fetch_catalog()))
            except Exception as exc:
                self.queue.put(("hub_error", hub_error_message(exc, "Katalog")))

        threading.Thread(target=fetch, daemon=True, name="vp-midi-hub-catalog").start()
        try:
            dialog.exec()
        finally:
            self.hub_dialog = None

    def start_hub_import(self, item: HubItem, profile: str) -> None:
        destination = core.mode_sheet_folder(CFG.folder, CFG.instrument_mode)
        mode = CFG.instrument_mode
        existing = any(
            core.read_sheet_meta(path).get("hub_id") == item.identifier
            and core.read_sheet_meta(path).get("instrument_mode", "piano") == mode
            for path in core.discover_sheets(destination)
        )
        if existing:
            self.queue.put(("hub_import_error", "Dieser Hub-Song wurde in diesem Modus bereits importiert."))
            return
        self.set_status("HUB-IMPORT")
        self.play_button.setEnabled(False)

        def worker() -> None:
            try:
                with tempfile.TemporaryDirectory(prefix="vp-midi-hub-") as folder:
                    source = Path(folder) / item.filename
                    download_midi(item, source)
                    result = import_song(
                        source, destination, None, None, 0,
                        mode != "drums", profile, 0.0, None, mode,
                    )
                    core.write_sheet_meta(result.path, extra={
                        "hub_id": item.identifier,
                        "hub_title": item.title,
                        "hub_artist": item.artists,
                        "hub_source_url": item.url,
                        "source_path": item.url,
                    })
                self.queue.put(("hub_import_done", result.path, item.title))
            except Exception as exc:
                self.queue.put(("hub_import_error", hub_error_message(exc, "Import")))

        threading.Thread(target=worker, daemon=True, name="vp-midi-hub-import").start()

    def open_import_selection(self, sources: list[Path]) -> None:
        sources = list(dict.fromkeys(
            path for path in sources if path.is_file() and path.suffix.casefold() in SUPPORTED_EXTENSIONS
        ))
        if len(sources) == 1:
            self.open_import_dialog(sources[0])
        elif sources:
            dialog = BatchImportDialog(self, sources)
            self.batch_dialog = dialog
            try:
                dialog.exec()
            finally:
                self.batch_dialog = None

    def open_import_dialog(self, source: Path) -> None:
        duplicate = next((
            path for path in core.discover_sheets(core.mode_sheet_folder(CFG.folder, CFG.instrument_mode))
            if core.read_sheet_meta(path).get("source_path") == str(source)
        ), None)
        if duplicate and QMessageBox.question(
            self, "Datei bereits importiert",
            f"{source.name} wurde bereits als {duplicate.name} importiert. Trotzdem fortfahren?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        dialog = ImportDialog(self, Path(source))
        if dialog.exec() == QDialog.Accepted and dialog.result_values:
            self.start_import(source, *dialog.result_values)

    def start_import(
        self, source: Path, bpm: int | None, subdiv: float | None,
        transpose: int, fold: bool, quality: str,
        section_start: float, section_end: float | None, replace: bool,
    ) -> None:
        self.set_status("ANALYSE" if source.suffix.casefold() in AUDIO_EXTENSIONS else "IMPORT")
        self.play_button.setEnabled(False)

        replace_target = Path(CFG.sheet) if replace and CFG.sheet else None
        destination = core.mode_sheet_folder(CFG.folder, CFG.instrument_mode)
        instrument_mode = CFG.instrument_mode

        def worker() -> None:
            try:
                result = import_song(
                    source, destination, bpm, subdiv, transpose, fold,
                    quality, section_start, section_end, instrument_mode,
                )
                selected = result.path
                if replace_target:
                    replace_section(replace_target, result.path, section_start, section_end or section_start)
                    result.path.unlink(missing_ok=True)
                    selected = replace_target
                self.queue.put(("import_done", result, selected, bool(replace_target)))
            except Exception as exc:
                self.queue.put(("import_error", str(exc)))

        threading.Thread(target=worker, daemon=True, name="vp-import").start()

    def start_batch_import(
        self, sources: list[Path], profile: str, cancelled: threading.Event,
    ) -> None:
        destination = core.mode_sheet_folder(CFG.folder, CFG.instrument_mode)
        instrument_mode = CFG.instrument_mode
        existing = {
            str(meta["source_path"])
            for path in core.discover_sheets(destination)
            if (meta := core.read_sheet_meta(path)).get("source_path")
            and meta.get("instrument_mode", "piano") == instrument_mode
        }
        self.set_status("STAPELIMPORT")
        self.play_button.setEnabled(False)

        def worker() -> None:
            try:
                outcomes = run_batch_import(
                    sources, destination, instrument_mode,
                    audio_quality=profile if instrument_mode != "drums" else AUTO_QUALITY_PROFILE,
                    drum_profile=profile if instrument_mode == "drums" else DRUM_ORIGINAL_PROFILE,
                    existing_sources=existing, cancelled=cancelled,
                    progress=lambda done, total, outcome: self.queue.put(
                        ("batch_progress", done, total, outcome),
                    ),
                )
                self.queue.put(("batch_done", outcomes))
            except Exception as exc:
                self.queue.put(("batch_error", str(exc)))

        threading.Thread(target=worker, daemon=True, name="vp-batch-import").start()

    def poll_queue(self) -> None:
        try:
            while True:
                message, *data = self.queue.get_nowait()
                if message == "total":
                    self.total_tokens = int(data[0])
                    self.progress.setRange(0, max(1, self.total_tokens))
                elif message == "progress":
                    self.completed_tokens = int(data[0])
                    if not self._scrubbing:
                        self.progress.setValue(self.completed_tokens)
                        self.update_time()
                elif message == "error":
                    QMessageBox.critical(self, "Wiedergabefehler", str(data[0]))
                    self.set_status("FEHLER")
                elif message == "done":
                    self.player = None
                    self.set_status("BEREIT")
                    self.update_play_button()
                elif message == "import_done":
                    result = data[0]
                    selected = Path(data[1])
                    replaced = bool(data[2])
                    self.play_button.setEnabled(True)
                    self.set_status("BEREIT")
                    self.refresh_sheets(selected)
                    profile = self.profile_store.profiles.get(
                        CFG.active_profile, self.profile_store.profiles["Universell"],
                    )
                    issues = analyze_quality(selected, profile)
                    imported_meta = core.read_sheet_meta(selected)
                    quality_text = "\n".join(f"• {category}: {detail}" for category, detail in issues[:4])
                    QMessageBox.information(
                        self, "Abschnitt ersetzt" if replaced else "Sheet erstellt",
                        f"{selected.name}\n\nSpielschritte: {result.notes}\n"
                        f"Akkorde: {result.chords}\nTempo: {result.bpm} BPM\n"
                        f"Tonzentrum: {imported_meta.get('detected_key', 'Unbekannt')}\n"
                        f"Tempowechsel: {'Ja' if imported_meta.get('tempo_changes') else 'Nein'}\n"
                        f"Erkennungssicherheit: {round(float(imported_meta.get('confidence', 0)) * 100)} %\n"
                        f"Raster: {result.subdiv:g}\nOktavierte Noten: {result.folded}\n\n"
                        f"Qualitätsprüfung:\n{quality_text}\n\n"
                        f"Gespeichert unter:\n{selected}",
                    )
                elif message == "import_error":
                    self.play_button.setEnabled(True)
                    self.set_status("FEHLER")
                    QMessageBox.critical(self, "Import fehlgeschlagen", str(data[0]))
                elif message == "batch_progress":
                    dialog = getattr(self, "batch_dialog", None)
                    if dialog:
                        dialog.update_progress(int(data[0]), int(data[1]), data[2])
                elif message == "batch_done":
                    outcomes: list[BatchOutcome] = data[0]
                    self.play_button.setEnabled(True)
                    self.set_status("BEREIT")
                    selected = next((entry.result.path for entry in reversed(outcomes) if entry.result), None)
                    self.refresh_sheets(selected)
                    dialog = getattr(self, "batch_dialog", None)
                    if dialog:
                        dialog.finish(outcomes)
                elif message == "batch_error":
                    self.play_button.setEnabled(True)
                    self.set_status("FEHLER")
                    dialog = getattr(self, "batch_dialog", None)
                    if dialog:
                        dialog.finish([])
                        dialog.summary.setText(f"Stapelimport abgebrochen: {data[0]}")
                elif message == "hub_loaded":
                    dialog = getattr(self, "hub_dialog", None)
                    if dialog:
                        dialog.set_items(data[0])
                elif message == "hub_error":
                    dialog = getattr(self, "hub_dialog", None)
                    if dialog:
                        dialog.status.setText(str(data[0]))
                elif message == "hub_preview_ready":
                    dialog = getattr(self, "hub_dialog", None)
                    if dialog:
                        dialog.preview_ready(data[0], data[1], data[2], data[3])
                    else:
                        data[2].cleanup()
                elif message == "hub_preview_error":
                    dialog = getattr(self, "hub_dialog", None)
                    if dialog:
                        dialog.preview_failed(data[0], data[1], data[2])
                elif message == "hub_import_done":
                    self.play_button.setEnabled(True)
                    self.set_status("BEREIT")
                    self.refresh_sheets(Path(data[0]))
                    dialog = getattr(self, "hub_dialog", None)
                    if dialog:
                        dialog.import_finished(f"{data[1]} wurde in deine Bibliothek importiert.")
                elif message == "hub_import_error":
                    self.play_button.setEnabled(True)
                    self.set_status("FEHLER")
                    dialog = getattr(self, "hub_dialog", None)
                    if dialog:
                        dialog.import_finished(f"Import fehlgeschlagen: {data[0]}")
        except Empty:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self.apply_dwm_corners)
        QTimer.singleShot(0, lambda: self.toggle_topmost(bool(CFG.always_on_top)))

    def apply_dwm_corners(self) -> None:
        try:
            preference = ctypes.c_int(2)
            no_border = ctypes.c_uint(0xFFFFFFFE)
            hwnd = int(self.winId())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(no_border), ctypes.sizeof(no_border))
        except Exception:
            pass

    def closeEvent(self, event: QCloseEvent) -> None:
        QApplication.instance().removeEventFilter(self.tooltip_manager)
        self.tooltip_manager.popup.close()
        self.save_note()
        if self.player:
            self.player.stop()
        if self._listener:
            self._listener.stop()
        try:
            ensure_daily_backup(CFG.folder)
        except OSError:
            pass
        core.save_cfg()
        event.accept()


def create_window_control_icon(kind: str, color: str) -> QIcon:
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if kind == "minimize":
        painter.drawLine(5, 11, 15, 11)
    elif kind == "maximize":
        painter.drawRoundedRect(5, 5, 10, 10, 1, 1)
    elif kind == "restore":
        painter.drawLine(8, 5, 15, 5)
        painter.drawLine(15, 5, 15, 12)
        painter.drawRoundedRect(5, 8, 8, 7, 1, 1)
    elif kind == "close":
        painter.drawLine(6, 6, 14, 14)
        painter.drawLine(14, 6, 6, 14)
    painter.end()
    return QIcon(pixmap)


def create_theme_icon(dark: bool) -> QIcon:
    """Draw a crisp sun or crescent without relying on platform emoji fonts."""
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    if dark:
        color = QColor("#FFD166")
        painter.setPen(QPen(color, 1.8, Qt.SolidLine, Qt.RoundCap))
        painter.setBrush(color)
        painter.drawEllipse(8, 8, 8, 8)
        for start, end in (
            ((12, 3), (12, 5)), ((12, 19), (12, 21)),
            ((3, 12), (5, 12)), ((19, 12), (21, 12)),
            ((5, 5), (7, 7)), ((17, 17), (19, 19)),
            ((19, 5), (17, 7)), ((7, 17), (5, 19)),
        ):
            painter.drawLine(*start, *end)
    else:
        moon = QPainterPath()
        moon.addEllipse(4, 3, 16, 18)
        cutout = QPainterPath()
        cutout.addEllipse(9, 1, 15, 16)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#245EA8"))
        painter.drawPath(moon.subtracted(cutout))

    painter.end()
    return QIcon(pixmap)


def create_folder_icon() -> QIcon:
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#4A9BFF"), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    fill = QColor("#2684FF")
    fill.setAlpha(45)
    painter.setBrush(fill)
    path = QPainterPath()
    path.moveTo(3, 6)
    path.quadTo(3, 4.5, 4.5, 4.5)
    path.lineTo(9, 4.5)
    path.lineTo(11.2, 7)
    path.lineTo(19.5, 7)
    path.quadTo(21, 7, 21, 8.5)
    path.lineTo(21, 18.5)
    path.quadTo(21, 20, 19.5, 20)
    path.lineTo(4.5, 20)
    path.quadTo(3, 20, 3, 18.5)
    path.closeSubpath()
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


def create_upload_icon() -> QIcon:
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#061625"), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawLine(12, 4, 12, 14)
    painter.drawLine(8.5, 7.5, 12, 4)
    painter.drawLine(15.5, 7.5, 12, 4)
    tray = QPainterPath()
    tray.moveTo(5, 14)
    tray.lineTo(5, 18)
    tray.quadTo(5, 20, 7, 20)
    tray.lineTo(17, 20)
    tray.quadTo(19, 20, 19, 18)
    tray.lineTo(19, 14)
    painter.drawPath(tray)
    painter.end()
    return QIcon(pixmap)


def create_open_folder_icon() -> QIcon:
    pixmap = create_folder_icon().pixmap(24, 24)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#4A9BFF"), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawLine(13, 12, 19, 6)
    painter.drawLine(15, 6, 19, 6)
    painter.drawLine(19, 6, 19, 10)
    painter.end()
    return QIcon(pixmap)


def create_refresh_icon() -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#67E8F9"), 2.25, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    top = QPainterPath()
    top.moveTo(5, 13)
    top.cubicTo(6, 7, 10, 5, 16, 5)
    top.cubicTo(21, 5, 25, 8, 27, 13)
    painter.drawPath(top)
    painter.drawLine(22, 12, 27, 13)
    painter.drawLine(27, 13, 29, 8)
    lower = QPainterPath()
    lower.moveTo(27, 19)
    lower.cubicTo(26, 25, 22, 27, 16, 27)
    lower.cubicTo(11, 27, 7, 24, 5, 19)
    painter.drawPath(lower)
    painter.drawLine(10, 20, 5, 19)
    painter.drawLine(5, 19, 3, 24)
    painter.end()
    return QIcon(pixmap)


def create_app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#2684FF"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 17, 17)
    painter.setPen(QColor("#FFFFFF"))
    painter.setFont(QFont("Segoe UI Symbol", 28, QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "♪")
    painter.end()
    return QIcon(pixmap)


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--verify-audio":
        try:
            return 0 if transcribe_audio(Path(sys.argv[2])) else 2
        except Exception:
            return 1

    if len(sys.argv) == 2 and sys.argv[1] == "--verify-studio":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        window = MainWindow()
        from studio import StudioDialog
        dialog = StudioDialog(window)
        valid = dialog.pages.count() == 5 and len(dialog.store.profiles) >= 3
        dialog.close()
        if window._listener:
            window._listener.stop()
        window.hide()
        return 0 if valid else 3

    app = QApplication(sys.argv)
    app.setApplicationName("VP Autoplayer")
    app.setApplicationDisplayName("VP Autoplayer")
    app.setStyle("Fusion")
    icon = create_app_icon()
    app.setWindowIcon(icon)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

# -- by Larsiopuw
