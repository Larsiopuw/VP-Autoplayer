"""Advanced Studio UI for editing, previewing and managing instrument sheets."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tempfile
import threading
import time
import winsound

import win32gui

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

import autoplayer_core as core
from studio_features import (
    InstrumentProfile, ProfileStore, SheetTimeline, analyze_quality, create_backup,
    load_timeline, rename_song, render_preview, restore_backup, save_timeline,
    set_song_properties, trash_song,
)
from transcription import DRUM_KEYS, DRUM_NAMES, PIANO_KEYS
from ui_effects import PremiumButton


class PianoRoll(QWidget):
    selectionChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.timeline: SheetTimeline | None = None
        self.selected = -1
        self.selected_note: str | None = None
        self.pixels_per_second = 135.0
        self.instrument_mode = "piano"
        self.setMinimumHeight(390)

    def set_timeline(self, timeline: SheetTimeline) -> None:
        self.timeline = timeline
        self.instrument_mode = "drums" if timeline.meta.get("instrument_mode") == "drums" else "piano"
        self.selected = -1
        self.selected_note = None
        margin = 125 if self.instrument_mode == "drums" else 50
        self.setMinimumWidth(max(900, int(timeline.duration * self.pixels_per_second) + margin))
        self.update()

    def _geometry(self, note: str, onset: float, hold: float) -> tuple[float, float, float, float] | None:
        if self.instrument_mode == "drums":
            if note not in DRUM_KEYS:
                return None
            lane = DRUM_KEYS.index(note)
            return 112 + onset * self.pixels_per_second, 18 + lane * 39.0, 13.0, 17.0
        if note not in PIANO_KEYS:
            return None
        midi = 36 + PIANO_KEYS.index(note)
        return 26 + onset * self.pixels_per_second, 12 + (96 - midi) * 5.7, max(7.0, hold * self.pixels_per_second), 8.0

    def _onsets(self) -> list[float]:
        result: list[float] = []
        current = 0.0
        if self.timeline:
            for duration in self.timeline.timings:
                result.append(current)
                current += duration
        return result

    def mousePressEvent(self, event) -> None:
        if not self.timeline:
            return
        onsets = self._onsets()
        point = event.position()
        candidates: list[tuple[float, int, str]] = []
        for index, (token, onset, hold) in enumerate(zip(
            self.timeline.tokens, onsets, self.timeline.holds,
        )):
            if token == "|":
                continue
            notes = token if isinstance(token, list) else [token]
            for note in notes:
                geometry = self._geometry(note, onset, hold)
                if geometry is None:
                    continue
                left, top, width, height = geometry
                right, bottom = left + width, top + height
                dx = max(left - point.x(), 0.0, point.x() - right)
                dy = max(top - point.y(), 0.0, point.y() - bottom)
                candidates.append((dx * dx + (dy * 2.2) ** 2, index, note))
        if not candidates:
            return
        _distance, self.selected, self.selected_note = min(candidates, key=lambda item: item[0])
        self.selectionChanged.emit(self.selected)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0C1420"))
        painter.setPen(QPen(QColor("#1D2B3E"), 1))
        if self.instrument_mode == "drums":
            for lane, key in enumerate(DRUM_KEYS):
                y = 14 + lane * 39
                painter.drawLine(0, y, self.width(), y)
                painter.setPen(QColor("#91A0B5"))
                painter.drawText(12, y + 25, f"{key.upper()}  {DRUM_NAMES[key]}")
                painter.setPen(QPen(QColor("#1D2B3E"), 1))
        else:
            for midi in range(36, 97):
                y = 16 + (96 - midi) * 5.7
                painter.drawLine(0, int(y), self.width(), int(y))
        if not self.timeline:
            painter.setPen(QColor("#91A0B5"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Kein Song geladen")
            return
        onsets = self._onsets()
        for index, (token, onset, hold) in enumerate(zip(
            self.timeline.tokens, onsets, self.timeline.holds,
        )):
            if token == "|":
                continue
            notes = token if isinstance(token, list) else [token]
            for note in notes:
                geometry = self._geometry(note, onset, hold)
                if geometry is None:
                    continue
                x, y, width, height = geometry
                exact_selection = index == self.selected and note == self.selected_note
                if exact_selection:
                    glow = QColor("#268DFF")
                    glow.setAlpha(75)
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(glow)
                    painter.drawRoundedRect(x - 4, y - 4, width + 8, height + 8, 7, 7)
                    painter.setPen(QPen(QColor("#F3F8FF"), 1.4))
                    painter.setBrush(QColor("#70BDFF"))
                    painter.drawRoundedRect(x, y, width, height, 4, 4)
                else:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor("#3A9BFF" if index == self.selected else "#288DFF"))
                    painter.drawRoundedRect(x, y + 1, width, max(6.0, height - 2), 3, 3)


class StudioDialog(QDialog):
    calibrationFinished = Signal()

    def __init__(self, parent):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint)
        self.main = parent
        self.setWindowTitle("VP Autoplayer - Instrument Studio")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(False)
        self.resize(960, 620)
        self.setMinimumSize(860, 560)
        self.store = ProfileStore()
        self.timeline: SheetTimeline | None = None
        self.undo_stack: list[SheetTimeline] = []
        self._calibration_values = [0.060, 0.045, 0.035, 0.028, 0.022, 0.018, 0.014]
        self._calibration_index = 1
        self.calibrationFinished.connect(self.calibration_feedback)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        shell = QFrame(objectName="Card")
        outer.addWidget(shell)
        root = QVBoxLayout(shell)
        root.setContentsMargins(22, 18, 22, 22)
        self.header = QFrame(objectName="Clear")
        header = QHBoxLayout(self.header)
        header.setContentsMargins(0, 0, 0, 4)
        title = QLabel("Instrument Studio", objectName="SongTitle")
        header.addWidget(title)
        self.song_label = QLabel("", objectName="Muted")
        header.addWidget(self.song_label)
        header.addStretch()
        close = PremiumButton("Schliessen")
        close.clicked.connect(self.close)
        header.addWidget(close)
        root.addWidget(self.header)
        for widget in (self.header, title, self.song_label):
            widget.installEventFilter(self)

        content = QHBoxLayout()
        self.navigation = QListWidget()
        self.navigation.setFixedWidth(190)
        self.navigation.addItems(["Uebersicht", "Piano-Roll", "Ueben & Loop", "Spielprofile", "Bibliothek"])
        self.navigation.setCurrentRow(0)
        self.pages = QStackedWidget()
        content.addWidget(self.navigation)
        content.addWidget(self.pages, 1)
        root.addLayout(content, 1)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)

        self.pages.addWidget(self._overview_page())
        self.pages.addWidget(self._editor_page())
        self.pages.addWidget(self._practice_page())
        self.pages.addWidget(self._profiles_page())
        self.pages.addWidget(self._library_page())
        self.reload()

        parent_geometry = parent.frameGeometry()
        self.move(
            parent_geometry.center().x() - self.width() // 2,
            parent_geometry.center().y() - self.height() // 2,
        )

    def eventFilter(self, watched, event) -> bool:
        if watched in (self.header, self.song_label) or watched.parent() is self.header:
            if event.type() == event.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                handle = self.windowHandle()
                if handle:
                    handle.startSystemMove()
                return True
            if event.type() == event.Type.MouseButtonDblClick and event.button() == Qt.LeftButton:
                self.showNormal() if self.isMaximized() else self.showMaximized()
                return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event) -> None:
        self.main.profile_store = self.store
        self.main.profile_badge.setText(core.CFG.active_profile.upper())
        self.main.refresh_sheets(core.CFG.sheet)
        super().closeEvent(event)

    def _page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 14, 12, 10)
        layout.addWidget(QLabel(title, objectName="CardTitle"))
        text = QLabel(subtitle, objectName="Muted")
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addSpacing(10)
        return page, layout

    def _overview_page(self) -> QWidget:
        page, layout = self._page(
            "Import- und Qualitaetsanalyse",
            "Prueft den Song gegen das aktive Spiel-/Instrumentprofil und spielt eine lokale Vorschau ab.",
        )
        metrics = QHBoxLayout()
        self.metric_steps = self._metric(metrics, "SPIELSCHRITTE")
        self.metric_duration = self._metric(metrics, "DAUER")
        self.metric_profile = self._metric(metrics, "AKTIVES PROFIL")
        self.metric_quality = self._metric(metrics, "IMPORTSTATUS")
        layout.addLayout(metrics)
        self.quality_list = QListWidget()
        self.quality_list.setMaximumHeight(165)
        layout.addWidget(self.quality_list)
        info = QFrame(objectName="Field")
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(16, 13, 16, 13)
        info_layout.addWidget(QLabel("Schnellaktionen", objectName="CardTitle"))
        guide = QLabel(
            "Höre zuerst eine lokale Vorschau. Öffne anschließend die Piano-Roll für einzelne Korrekturen "
            "oder starte unter Üben & Loop direkt einen markierten Abschnitt.", objectName="Muted",
        )
        guide.setWordWrap(True)
        info_layout.addWidget(guide)
        layout.addWidget(info)
        layout.addStretch()
        row = QHBoxLayout()
        preview = PremiumButton("Vorschau abspielen", objectName="Primary")
        stop = PremiumButton("Vorschau stoppen")
        refresh = PremiumButton("Neu analysieren")
        preview.clicked.connect(self.preview)
        stop.clicked.connect(lambda: winsound.PlaySound(None, winsound.SND_PURGE))
        refresh.clicked.connect(self.reload)
        row.addWidget(preview)
        row.addWidget(stop)
        row.addWidget(refresh)
        layout.addLayout(row)
        return page

    @staticmethod
    def _metric(layout: QHBoxLayout, title: str) -> QLabel:
        card = QFrame(objectName="Field")
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 10, 14, 10)
        box.addWidget(QLabel(title, objectName="MutedSmall"))
        value = QLabel("—", objectName="CardTitle")
        box.addWidget(value)
        layout.addWidget(card, 1)
        return value

    def _editor_page(self) -> QWidget:
        page, layout = self._page(
            "Piano-Roll-Editor",
            "Klicke eine Note an. Du kannst sie transponieren, entfernen oder ihr Timing anpassen.",
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        self.roll = PianoRoll()
        self.roll.selectionChanged.connect(self.editor_selection)
        scroll.setWidget(self.roll)
        layout.addWidget(scroll, 1)
        self.selection_label = QLabel("Keine Note ausgewaehlt", objectName="Muted")
        layout.addWidget(self.selection_label)
        row = QHBoxLayout()
        for label, callback in (
            ("- Halbton", lambda: self.transpose_selected(-1)),
            ("+ Halbton", lambda: self.transpose_selected(1)),
            ("Frueher", lambda: self.adjust_timing(-0.01)),
            ("Spaeter", lambda: self.adjust_timing(0.01)),
            ("Entfernen", self.delete_selected),
            ("Rueckgaengig", self.undo),
            ("Speichern", self.save_editor),
        ):
            button = PremiumButton(label, objectName="Primary" if label == "Speichern" else "")
            button.clicked.connect(callback)
            row.addWidget(button)
            if label == "- Halbton":
                self.transpose_down = button
            elif label == "+ Halbton":
                self.transpose_up = button
        layout.addLayout(row)
        return page

    def _practice_page(self) -> QWidget:
        page, layout = self._page(
            "Uebungs- und Loop-Modus",
            "Spielt nur einen markierten Zeitbereich, auf Wunsch langsamer und mehrfach hintereinander.",
        )
        fields = QHBoxLayout()
        self.loop_start = self._field(fields, "START (SEK.)", "0")
        self.loop_end = self._field(fields, "ENDE (SEK.)", "20")
        self.loop_speed = self._field(fields, "GESCHWINDIGKEIT %", "75")
        self.loop_count = self._field(fields, "WIEDERHOLUNGEN", "3")
        layout.addLayout(fields)
        start = PremiumButton("Uebung starten", objectName="Primary")
        start.setFixedHeight(48)
        start.clicked.connect(self.start_practice)
        layout.addWidget(start)
        layout.addStretch()
        return page

    def _profiles_page(self) -> QWidget:
        page, layout = self._page(
            "Universelle Spiel- und Instrumentprofile",
            "Jedes Spiel kann ein eigenes Layout, Eingabetempo und Akkordlimit erhalten.",
        )
        self.profile_list = QListWidget()
        self.profile_list.addItems(list(self.store.profiles))
        self.profile_list.currentTextChanged.connect(self.load_profile)
        layout.addWidget(self.profile_list, 1)
        fields = QHBoxLayout()
        self.profile_name = self._field(fields, "NAME", "Universell")
        self.profile_interval = self._field(fields, "MIN. ABSTAND MS", "18")
        self.profile_gap = self._field(fields, "WIEDERHOLUNG MS", "5")
        self.profile_chord = self._field(fields, "MAX. AKKORD", "6")
        layout.addLayout(fields)
        layout.addWidget(QLabel("TASTENLAYOUT (61 Zeichen)", objectName="MutedSmall"))
        self.profile_layout = QLineEdit(core.CANONICAL_LAYOUT)
        layout.addWidget(self.profile_layout)
        layout.addWidget(QLabel("DRUM-PADS · TASTEN IM ZIELSPIEL", objectName="MutedSmall"))
        pads = QHBoxLayout()
        pads.setSpacing(5)
        self.drum_entries: dict[str, QLineEdit] = {}
        short_names = ("Hi-Hat", "Snare", "Tom ↑", "Tom ↓", "Kick", "Crash", "Ride", "Becken", "Extra")
        for pad, name in zip(core.DRUM_LAYOUT, short_names):
            column = QVBoxLayout()
            column.setSpacing(3)
            caption = QLabel(name, objectName="MutedSmall")
            caption.setAlignment(Qt.AlignCenter)
            column.addWidget(caption)
            entry = QLineEdit(pad.upper() if pad.isalpha() else pad)
            entry.setAlignment(Qt.AlignCenter)
            entry.setMaxLength(6)
            entry.setFixedHeight(37)
            entry.setToolTip(f"{name}: Taste im Spiel. Auch 'space', 'enter' oder 'tab' möglich.")
            column.addWidget(entry)
            pads.addLayout(column, 1)
            self.drum_entries[pad] = entry
        layout.addLayout(pads)
        hint = QLabel("Beispiel: Garry's-Mod-Kit = A/S/D/F/H/J/K/L/; · pro Spielprofil frei anpassbar.", objectName="MutedSmall")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        save = PremiumButton("Speichern", objectName="Primary")
        activate = PremiumButton("Aktivieren")
        fast = PremiumButton("Schnell")
        safe = PremiumButton("Kompatibel")
        calibrate = PremiumButton("Kalibrieren", objectName="Primary")
        save.clicked.connect(self.save_profile)
        activate.clicked.connect(self.activate_profile)
        fast.clicked.connect(lambda: self.calibration_preset(16, 4, 6))
        safe.clicked.connect(lambda: self.calibration_preset(40, 12, 4))
        calibrate.clicked.connect(self.start_calibration)
        for button in (save, activate, fast, safe, calibrate):
            button.setMinimumWidth(0)
            button.setFixedHeight(42)
            button.setStyleSheet("font-size: 10pt; padding-left: 8px; padding-right: 8px;")
            row.addWidget(button, 1)
        layout.addLayout(row)
        return page

    def _library_page(self) -> QWidget:
        page, layout = self._page(
            "Bibliothek und Sicherungen",
            "Favoriten, Playlists, Umbenennen, Papierkorb und komplette ZIP-Backups.",
        )
        self.library_info = QLabel("", objectName="Muted")
        self.library_info.setWordWrap(True)
        layout.addWidget(self.library_info)
        layout.addWidget(QLabel("PLAYLIST / TAGS (mit Komma trennen)", objectName="MutedSmall"))
        self.playlists = QLineEdit()
        layout.addWidget(self.playlists)
        row = QHBoxLayout()
        for label, callback in (
            ("Favorit umschalten", self.toggle_favorite),
            ("Tags speichern", self.save_tags),
            ("Umbenennen", self.rename_current),
            ("In Papierkorb", self.trash_current),
        ):
            button = PremiumButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        backup_row = QHBoxLayout()
        backup = PremiumButton("Bibliothek sichern", objectName="Primary")
        restore = PremiumButton("Backup wiederherstellen")
        backup.clicked.connect(self.backup)
        restore.clicked.connect(self.restore)
        backup_row.addWidget(backup)
        backup_row.addWidget(restore)
        layout.addLayout(backup_row)
        layout.addStretch()
        return page

    @staticmethod
    def _field(layout: QHBoxLayout, label: str, value: str) -> QLineEdit:
        box = QVBoxLayout()
        box.addWidget(QLabel(label, objectName="MutedSmall"))
        entry = QLineEdit(value)
        entry.setAlignment(Qt.AlignCenter)
        box.addWidget(entry)
        layout.addLayout(box, 1)
        return entry

    def current_path(self) -> Path | None:
        return Path(core.CFG.sheet) if core.CFG.sheet and Path(core.CFG.sheet).exists() else None

    def reload(self) -> None:
        path = self.current_path()
        self.song_label.setText(path.stem if path else "Kein Song")
        self.quality_list.clear()
        if not path:
            return
        self.timeline = load_timeline(path)
        self.roll.set_timeline(self.timeline)
        drums = self.timeline.meta.get("instrument_mode") == "drums"
        self.navigation.item(1).setText("Drum-Grid" if drums else "Piano-Roll")
        self.transpose_down.setText("- Pad" if drums else "- Halbton")
        self.transpose_up.setText("+ Pad" if drums else "+ Halbton")
        profile = self.store.profiles.get(core.CFG.active_profile, self.store.profiles["Universell"])
        issues = analyze_quality(path, profile)
        meta = self.timeline.meta
        if meta.get("midi_optimized"):
            details = meta.get("midi_optimisation", {})
            removed = int(details.get("removed_notes", 0) or 0)
            self.quality_list.addItem(
                f"MIDI: Extreme Effektspuren wurden automatisch spielbar reduziert ({removed:,} verworfen)."
            )
        for category, detail in issues:
            self.quality_list.addItem(f"{category}: {detail}")
        self.metric_steps.setText(f"{len(self.timeline.tokens):,}")
        self.metric_duration.setText(f"{int(self.timeline.duration // 60):02d}:{int(self.timeline.duration % 60):02d}")
        self.metric_profile.setText(profile.name)
        quality_profile = str(meta.get("quality_profile", ""))
        if quality_profile.startswith("MIDI"):
            self.metric_quality.setText("MIDI OPT." if meta.get("midi_optimized") else "MIDI DIREKT")
        else:
            self.metric_quality.setText(f"{round(float(meta.get('confidence', 1.0)) * 100)} %")
        self.playlists.setText(", ".join(meta.get("tags", [])))
        self.library_info.setText(
            f"{path.name}\nFavorit: {'Ja' if meta.get('favorite') else 'Nein'}  |  "
            f"Profil: {meta.get('game_profile', core.CFG.active_profile)}  |  Dauer: {self.timeline.duration:.1f} s"
        )

    def preview(self) -> None:
        path = self.current_path()
        if not path:
            return
        try:
            start = max(0.0, float(self.loop_start.text().replace(",", ".")))
            end = min(start + 30.0, float(self.loop_end.text().replace(",", ".")))
        except ValueError:
            start, end = 0.0, 30.0
        output = Path(tempfile.gettempdir()) / "vp-autoplayer-preview.wav"
        render_preview(path, output, start, end)
        winsound.PlaySound(str(output), winsound.SND_FILENAME | winsound.SND_ASYNC)

    def _snapshot(self) -> None:
        if self.timeline:
            self.undo_stack.append(deepcopy(self.timeline))
            self.undo_stack = self.undo_stack[-30:]

    def editor_selection(self, index: int) -> None:
        if not self.timeline or not 0 <= index < len(self.timeline.tokens):
            return
        self.selection_label.setText(
            f"Ausgewählt: {self.roll.selected_note or self.timeline.tokens[index]}  ·  "
            f"Schritt {index + 1}  |  "
            f"Abstand {self.timeline.timings[index] * 1000:.0f} ms"
        )

    def transpose_selected(self, amount: int) -> None:
        if not self.timeline or self.roll.selected < 0:
            return
        self._snapshot()
        token = self.timeline.tokens[self.roll.selected]
        notes = token if isinstance(token, list) else [token]
        note_layout = DRUM_KEYS if self.timeline.meta.get("instrument_mode") == "drums" else PIANO_KEYS
        shifted: list[str] = []
        for note in notes:
            if note not in note_layout:
                continue
            offset = amount if note == self.roll.selected_note or len(notes) == 1 else 0
            shifted.append(note_layout[max(0, min(len(note_layout) - 1, note_layout.index(note) + offset))])
        self.timeline.tokens[self.roll.selected] = shifted if len(shifted) > 1 else (shifted[0] if shifted else "|")
        if self.roll.selected_note in note_layout:
            self.roll.selected_note = note_layout[max(
                0, min(len(note_layout) - 1, note_layout.index(self.roll.selected_note) + amount),
            )]
        self.roll.update()

    def adjust_timing(self, amount: float) -> None:
        if not self.timeline or self.roll.selected < 0:
            return
        self._snapshot()
        index = self.roll.selected
        self.timeline.timings[index] = max(0.012, self.timeline.timings[index] + amount)
        self.editor_selection(index)
        self.roll.update()

    def delete_selected(self) -> None:
        if self.timeline and self.roll.selected >= 0:
            self._snapshot()
            token = self.timeline.tokens[self.roll.selected]
            if isinstance(token, list) and self.roll.selected_note in token:
                remaining = [note for note in token if note != self.roll.selected_note]
                self.timeline.tokens[self.roll.selected] = remaining if len(remaining) > 1 else (remaining[0] if remaining else "|")
            else:
                self.timeline.tokens[self.roll.selected] = "|"
            self.roll.selected_note = None
            self.roll.update()

    def undo(self) -> None:
        if self.undo_stack:
            self.timeline = self.undo_stack.pop()
            self.roll.set_timeline(self.timeline)

    def save_editor(self) -> None:
        path = self.current_path()
        if path and self.timeline:
            self.timeline.meta["edited_at"] = datetime.now().isoformat(timespec="seconds")
            save_timeline(path, self.timeline)
            self.main.load_sheet(path)
            editor = "Drum-Grid" if self.timeline.meta.get("instrument_mode") == "drums" else "Piano-Roll"
            QMessageBox.information(self, "Gespeichert", f"Die {editor}-Änderungen wurden gespeichert.")

    def start_practice(self) -> None:
        try:
            self.main.start_practice_mode(
                float(self.loop_start.text().replace(",", ".")),
                float(self.loop_end.text().replace(",", ".")),
                float(self.loop_speed.text().replace(",", ".")) / 100.0,
                int(self.loop_count.text()),
            )
            self.accept()
        except ValueError:
            QMessageBox.warning(self, "Ungueltige Werte", "Bitte pruefe Start, Ende, Tempo und Wiederholungen.")

    def load_profile(self, name: str) -> None:
        profile = self.store.profiles.get(name)
        if not profile:
            return
        self.profile_name.setText(profile.name)
        self.profile_interval.setText(str(round(profile.input_interval * 1000)))
        self.profile_gap.setText(str(round(profile.retrigger_gap * 1000)))
        self.profile_chord.setText(str(profile.max_chord))
        self.profile_layout.setText(profile.layout)
        for pad, entry in self.drum_entries.items():
            key = profile.drum_keymap.get(pad, pad)
            entry.setText(key.upper() if len(key) == 1 and key.isalpha() else key)

    def save_profile(self) -> None:
        try:
            drum_keymap = {
                pad: entry.text().strip().casefold()
                for pad, entry in self.drum_entries.items()
            }
            if not all(core.is_valid_game_key(value) for value in drum_keymap.values()):
                raise ValueError
            profile = InstrumentProfile(
                self.profile_name.text().strip(), self.profile_layout.text(),
                float(self.profile_interval.text()) / 1000.0,
                float(self.profile_gap.text()) / 1000.0,
                int(self.profile_chord.text()), "Benutzerdefiniertes Profil", drum_keymap,
            ).normalise()
            if not profile.name:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Profil", "Bitte prüfe Profilwerte und Drum-Tasten (ein Zeichen oder space/enter/tab).")
            return
        self.store.put(profile)
        if not self.profile_list.findItems(profile.name, Qt.MatchExactly):
            self.profile_list.addItem(profile.name)
        QMessageBox.information(self, "Profil", "Das Profil wurde gespeichert.")

    def activate_profile(self) -> None:
        self.save_profile()
        profile = self.store.activate(self.profile_name.text().strip())
        path = self.current_path()
        if path:
            set_song_properties(path, game_profile=profile.name)
        self.reload()

    def calibration_preset(self, interval: int, gap: int, chord: int) -> None:
        self.profile_interval.setText(str(interval))
        self.profile_gap.setText(str(gap))
        self.profile_chord.setText(str(chord))

    def start_calibration(self) -> None:
        if not core.CFG.target_title:
            QMessageBox.warning(self, "Kalibrierung", "Waehle im Hauptfenster zuerst ein Zielfenster aus.")
            return
        self._calibration_index = 1
        QMessageBox.information(
            self, "Universelle Kalibrierung",
            "Das Zielspiel wird kurz fokussiert und spielt zwoelf Testanschlaege. "
            "Beobachte, ob wirklich jeder Anschlag ankommt.",
        )
        self._run_calibration_round()

    def _run_calibration_round(self) -> None:
        interval = self._calibration_values[self._calibration_index]
        note = "h" if core.CFG.instrument_mode == "drums" else "t"

        def worker() -> None:
            target = next((handle for handle, title in core.windows() if title == core.CFG.target_title), None)
            if not target:
                self.calibrationFinished.emit()
                return
            try:
                win32gui.SetForegroundWindow(target)
                time.sleep(0.8)
                for _ in range(12):
                    core.press(note)
                    time.sleep(max(0.004, interval * 0.30))
                    core.release(note)
                    time.sleep(max(0.004, interval * 0.70))
            finally:
                try:
                    win32gui.SetForegroundWindow(int(self.winId()))
                except Exception:
                    pass
                self.calibrationFinished.emit()

        threading.Thread(target=worker, daemon=True, name="vp-calibration").start()

    def calibration_feedback(self) -> None:
        interval = self._calibration_values[self._calibration_index]
        answer = QMessageBox.question(
            self, "Kalibrierung",
            f"Wurden bei {round(interval * 1000)} ms Abstand alle zwoelf Anschlaege erkannt?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes and self._calibration_index < len(self._calibration_values) - 1:
            self._calibration_index += 1
            self._run_calibration_round()
            return
        if answer == QMessageBox.No:
            self._calibration_index = max(0, self._calibration_index - 1)
        selected = self._calibration_values[self._calibration_index]
        self.profile_interval.setText(str(round(selected * 1000)))
        self.profile_gap.setText(str(max(3, round(selected * 250))))
        QMessageBox.information(
            self, "Kalibrierung abgeschlossen",
            f"Sicherer Mindestabstand: {round(selected * 1000)} ms. Speichere oder aktiviere das Profil.",
        )

    def toggle_favorite(self) -> None:
        path = self.current_path()
        if path:
            meta = core.read_sheet_meta(path)
            set_song_properties(path, favorite=not bool(meta.get("favorite")))
            self.reload()
            self.main.refresh_sheets(path)

    def save_tags(self) -> None:
        path = self.current_path()
        if path:
            tags = [value.strip() for value in self.playlists.text().split(",") if value.strip()]
            set_song_properties(path, tags=tags)
            self.reload()

    def rename_current(self) -> None:
        path = self.current_path()
        if not path:
            return
        name, _ = QFileDialog.getSaveFileName(self, "Neuen Songnamen waehlen", str(path.with_suffix("")), "Sheet (*.txt)")
        if not name:
            return
        try:
            renamed = rename_song(path, Path(name).stem)
            core.CFG.sheet = renamed
            self.main.refresh_sheets(renamed)
            self.reload()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Umbenennen", str(exc))

    def trash_current(self) -> None:
        path = self.current_path()
        if path and QMessageBox.question(self, "Papierkorb", "Song in den Papierkorb verschieben?") == QMessageBox.Yes:
            trash_song(path)
            core.CFG.sheet = None
            self.main.refresh_sheets()
            self.reload()

    def backup(self) -> None:
        default = Path.home() / "Documents" / f"VP-Autoplayer-Backup-{datetime.now():%Y-%m-%d}.zip"
        destination, _ = QFileDialog.getSaveFileName(self, "Backup speichern", str(default), "ZIP (*.zip)")
        if destination:
            create_backup(core.CFG.folder, Path(destination))
            QMessageBox.information(self, "Backup", "Bibliothek und Einstellungen wurden gesichert.")

    def restore(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, "Backup laden", "", "ZIP (*.zip)")
        if source:
            count = restore_backup(Path(source), core.CFG.folder)
            core.ensure_library_layout(core.CFG.folder)
            self.main.refresh_sheets()
            QMessageBox.information(self, "Backup", f"{count} Songs wurden wiederhergestellt.")


# -- by Larsiopuw
