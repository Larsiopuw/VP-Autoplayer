#!/usr/bin/env python3
"""Playback engine and persistent settings for VP Autoplayer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import math
import os
from pathlib import Path
from queue import Queue
import random
import shutil
import sys
import threading
import time
import zipfile
from typing import Iterator, Optional

import win32gui
from pynput.keyboard import Controller, Key

SOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
if getattr(sys, "frozen", False):
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path(sys.executable).parent)) / "VP Autoplayer"
else:
    DATA_DIR = Path(__file__).parent
DATA_FILE = DATA_DIR / "vp_autoplayer.json"
DEFAULT_SHEETS_DIR = DATA_DIR / "Sheets"

SHIFT_MAP: dict[str, str] = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6",
    "&": "7", "*": "8", "(": "9", ")": "0", "_": "-", "+": "=",
    "{": "[", "}": "]", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/",
}
SHIFT_REV = {value: key for key, value in SHIFT_MAP.items()}
NEIGHBOURS: dict[str, list[str]] = {
    "a": ["s", "q", "w", "z"], "s": ["a", "d", "w", "e", "x", "z"],
    "d": ["s", "f", "e", "r", "c", "x"], "f": ["d", "g", "r", "t", "v", "c"],
    "g": ["f", "h", "t", "y", "b", "v"], "h": ["g", "j", "y", "u", "n", "b"],
    "j": ["h", "k", "u", "i", "m", "n"], "k": ["j", "l", "i", "o", ",", "m"],
    "l": ["k", "o", "p", "."], "q": ["w", "a"], "w": ["q", "e", "s", "a"],
    "e": ["w", "r", "d", "s"], "r": ["e", "t", "f", "d"],
    "t": ["r", "y", "g", "f"], "y": ["t", "u", "h", "g"],
    "u": ["y", "i", "j", "h"], "i": ["u", "o", "k", "j"],
    "o": ["i", "p", "l", "k"], "p": ["o", "l"],
}

KEYBOARD = Controller()
CANONICAL_LAYOUT = "1!2@34$5%6^78*9(0qQwWeErtTyYuiIoOpPasSdDfgGhHjJklLzZxcCvVbBnm"
DRUM_LAYOUT = "asdfhjkl;"
DEFAULT_DRUM_KEYMAP = dict(zip(DRUM_LAYOUT, DRUM_LAYOUT))
NAMED_GAME_KEYS = {"space": Key.space, "enter": Key.enter, "tab": Key.tab}


def is_valid_game_key(value: str) -> bool:
    return value in NAMED_GAME_KEYS or (
        len(value) == 1 and value.isascii() and value.isprintable()
    )


def normalise_drum_keymap(value: object) -> dict[str, str]:
    """Keep old and partially edited profiles playable without unsafe key names."""
    source = value if isinstance(value, dict) else {}
    result = DEFAULT_DRUM_KEYMAP.copy()
    for pad in DRUM_LAYOUT:
        candidate = str(source.get(pad, result[pad])).strip().casefold()
        if is_valid_game_key(candidate):
            result[pad] = candidate
    return result


@dataclass
class Config:
    folder: Path = field(default_factory=lambda: DEFAULT_SHEETS_DIR)
    sheet: Optional[Path] = None
    bpm: int = 120
    subdiv: float = 4.0
    human: float = 0.20
    hold: float = 0.80
    toggle: str = "f4"
    auto_pause: bool = True
    dark: bool = True
    always_on_top: bool = True
    target_title: Optional[str] = field(default=None, repr=False)
    active_profile: str = "Universell"
    key_layout: str = CANONICAL_LAYOUT
    input_interval: float = 0.018
    retrigger_gap: float = 0.005
    max_chord: int = 6
    playback_speed: float = 1.0
    instrument_mode: str = "piano"
    drum_keymap: dict[str, str] = field(default_factory=lambda: DEFAULT_DRUM_KEYMAP.copy())

    def sec_per_token(self) -> float:
        bpm = max(1, int(self.bpm))
        subdiv = max(0.1, float(self.subdiv))
        return 60.0 / (bpm * subdiv)


CFG = Config()


def humanize_timing_steps(
    durations: list[float], strength: float, minimum_interval: float,
    generator: object = random,
) -> list[float]:
    """Shift onsets naturally without changing total length or note order."""
    if strength <= 0 or len(durations) < 2:
        return durations.copy()
    strength = min(1.0, strength)
    offsets = [0.0] * (len(durations) + 1)
    for index in range(1, len(durations)):
        margin = max(0.0, min(durations[index - 1], durations[index]) - minimum_interval)
        limit = min(0.030 * strength, margin * 0.35)
        feel = generator.gauss(0.0, 0.014 * strength)
        phrase = 0.006 * strength * math.sin(index * 0.65)
        offsets[index] = max(-limit, min(limit, feel + phrase))
    return [
        durations[index] + offsets[index + 1] - offsets[index]
        for index in range(len(durations))
    ]


def _prepare_data_directory() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DEFAULT_SHEETS_DIR.exists():
        return
    bundled_sheets = SOURCE_DIR / "Sheets"
    if bundled_sheets.exists() and bundled_sheets != DEFAULT_SHEETS_DIR:
        shutil.copytree(bundled_sheets, DEFAULT_SHEETS_DIR)
    else:
        DEFAULT_SHEETS_DIR.mkdir(parents=True, exist_ok=True)


def _load_config() -> None:
    if not DATA_FILE.exists():
        return
    try:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        known = Config.__dataclass_fields__
        for key, value in raw.items():
            if key in known:
                if key in {"folder", "sheet"} and value:
                    path = Path(value)
                    value = path if path.is_absolute() else DATA_DIR / path
                setattr(CFG, key, value)
        CFG.drum_keymap = normalise_drum_keymap(CFG.drum_keymap)
    except (OSError, ValueError, TypeError) as exc:
        print(f"Settings could not be loaded: {exc}", file=sys.stderr)


def save_cfg() -> None:
    """Persist known settings atomically."""
    payload = {
        key: str(value) if isinstance(value, Path) else value
        for key, value in asdict(CFG).items()
    }
    temporary = DATA_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(DATA_FILE)


def read_sheet_meta(path: Path) -> dict:
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            if line.lstrip().startswith("#META"):
                _, raw = line.split(None, 1)
                value = json.loads(raw)
                return value if isinstance(value, dict) else {}
            break
    except (OSError, ValueError, IndexError, TypeError):
        pass
    return {}


def write_sheet_meta(
    path: Path,
    bpm: Optional[int] = None,
    subdiv: Optional[float] = None,
    note: Optional[str] = None,
    timings: Optional[list[float]] = None,
    holds: Optional[list[float]] = None,
    extra: Optional[dict] = None,
) -> None:
    if not path or not path.exists():
        return
    meta = read_sheet_meta(path)
    if bpm is not None:
        meta["bpm"] = int(bpm)
    if subdiv is not None:
        meta["subdiv"] = float(subdiv)
    if note is not None:
        meta["note"] = note.strip()
    if timings is not None:
        meta["dynamic_timing"] = True
        meta["timings"] = [round(max(0.01, float(value)), 4) for value in timings]
    if holds is not None:
        meta["holds"] = [round(max(0.0, float(value)), 4) for value in holds]
    if extra:
        meta.update(extra)

    lines = path.read_text(encoding="utf-8-sig").splitlines()
    header = f"#META {json.dumps(meta, ensure_ascii=False)}"
    first_content = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first_content is not None and lines[first_content].lstrip().startswith("#META"):
        lines[first_content] = header
    else:
        lines.insert(0, header)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def tokenise(text: str) -> Iterator[str | list[str]]:
    """Parse Virtual Piano notation into notes, chords and rests."""
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        index = 0
        while index < len(line):
            char = line[index]
            if char == "[":
                chord: list[str] = []
                index += 1
                while index < len(line) and line[index] != "]":
                    candidate = line[index]
                    if not candidate.isspace() and (
                        candidate.isalnum() or candidate in SHIFT_MAP or candidate in DRUM_LAYOUT
                    ):
                        chord.append(candidate)
                    index += 1
                if chord:
                    yield chord
            elif not char.isspace():
                yield char
            index += 1


def discover_sheets(folder: Path) -> list[Path]:
    """Return readable VP sheets, including legacy files without an extension."""
    try:
        candidates = [
            path for path in folder.iterdir()
            if path.is_file() and path.suffix.casefold() in {"", ".txt"}
        ]
    except OSError:
        return []

    sheets: list[Path] = []
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8-sig")
            if any(tokenise(text)):
                sheets.append(path)
        except (OSError, UnicodeError):
            continue
    return sorted(sheets, key=lambda path: path.name.casefold())


def mode_sheet_folder(root: Path, mode: str) -> Path:
    """Keep both instrument libraries under the user-selected library root."""
    return root / ("Drums" if mode == "drums" else "Piano")


def ensure_library_layout(root: Path) -> dict[Path, Path]:
    """Move legacy root-level sheets once, without replacing any existing song."""
    root.mkdir(parents=True, exist_ok=True)
    for mode in ("piano", "drums"):
        mode_sheet_folder(root, mode).mkdir(parents=True, exist_ok=True)
    moved: dict[Path, Path] = {}
    legacy = discover_sheets(root)
    if legacy:
        backups = DATA_DIR / "Backups"
        backups.mkdir(parents=True, exist_ok=True)
        snapshot = backups / f"Library-Migration-{datetime.now():%Y%m%d-%H%M%S-%f}.zip"
        with zipfile.ZipFile(snapshot, "x", zipfile.ZIP_DEFLATED) as archive:
            for source in legacy:
                archive.write(source, source.name)
    for source in legacy:
        mode = "drums" if read_sheet_meta(source).get("instrument_mode") == "drums" else "piano"
        folder = mode_sheet_folder(root, mode)
        target = folder / source.name
        suffix = 2
        while target.exists():
            target = folder / f"{source.stem} ({suffix}){source.suffix}"
            suffix += 1
        source.rename(target)
        moved[source] = target
    return moved


def windows() -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []

    def collect(handle: int, _: object) -> None:
        title = win32gui.GetWindowText(handle).strip()
        if title and win32gui.IsWindowVisible(handle):
            result.append((handle, title))

    win32gui.EnumWindows(collect, None)
    return sorted(result, key=lambda item: item[1].casefold())


def translate_note(character: str) -> str:
    """Translate canonical VP notation through the active universal layout."""
    return translate_note_for_layout(
        character, CFG.instrument_mode, CFG.key_layout, CFG.drum_keymap,
    )


def translate_note_for_layout(
    character: str, mode: str, piano_layout: str, drum_keymap: dict[str, str],
) -> str:
    if mode == "drums":
        keymap = drum_keymap if isinstance(drum_keymap, dict) else DEFAULT_DRUM_KEYMAP
        return keymap.get(character.casefold(), character.casefold())
    layout = piano_layout if len(piano_layout) == len(CANONICAL_LAYOUT) else CANONICAL_LAYOUT
    try:
        return layout[CANONICAL_LAYOUT.index(character)]
    except ValueError:
        return character


def press(character: str) -> None:
    press_mapped(translate_note(character))


def press_mapped(character: str) -> None:
    if character in NAMED_GAME_KEYS:
        KEYBOARD.press(NAMED_GAME_KEYS[character])
    elif character in SHIFT_MAP:
        KEYBOARD.press(Key.shift)
        KEYBOARD.press(SHIFT_MAP[character])
        KEYBOARD.release(Key.shift)
    elif character.isupper():
        KEYBOARD.press(Key.shift)
        KEYBOARD.press(character.lower())
        KEYBOARD.release(Key.shift)
    else:
        KEYBOARD.press(character)


def release(character: str) -> None:
    release_mapped(translate_note(character))


def release_mapped(character: str) -> None:
    if character in NAMED_GAME_KEYS:
        KEYBOARD.release(NAMED_GAME_KEYS[character])
    elif character in SHIFT_MAP:
        KEYBOARD.release(SHIFT_MAP[character])
    elif character.isupper():
        KEYBOARD.release(character.lower())
    else:
        KEYBOARD.release(character)


def physical_key(character: str) -> str:
    """Return the actual keyboard key used by a VP notation character."""
    return physical_identity(translate_note(character))


def physical_identity(character: str) -> str:
    if character in SHIFT_MAP:
        return SHIFT_MAP[character]
    return character.lower() if character.isalpha() else character


class Player(threading.Thread):
    """Interruptible playback worker that never touches Tk directly."""

    def __init__(
        self, sheet: Path, queue: Queue, start_index: int = 0,
        end_index: Optional[int] = None, speed: Optional[float] = None,
        loop_count: int = 1,
    ):
        super().__init__(daemon=True, name="vp-playback")
        self.sheet = sheet
        self.queue = queue
        self.start_index = max(0, int(start_index))
        self.end_index = None if end_index is None else max(self.start_index + 1, int(end_index))
        self.speed = max(0.25, min(2.0, float(speed or CFG.playback_speed)))
        self.loop_count = max(1, min(100, int(loop_count)))
        self.resume_event = threading.Event()
        self.resume_event.set()
        self.stop_event = threading.Event()
        self._held: list[tuple[str, str]] = []
        self._held_lock = threading.RLock()
        self._mode = CFG.instrument_mode
        self._piano_layout = CFG.key_layout
        self._drum_keymap = normalise_drum_keymap(CFG.drum_keymap)

    def _mapped(self, note: str) -> str:
        return translate_note_for_layout(note, self._mode, self._piano_layout, self._drum_keymap)

    @property
    def is_playing(self) -> bool:
        return self.resume_event.is_set() and not self.stop_event.is_set()

    def toggle(self) -> None:
        if self.resume_event.is_set():
            self.resume_event.clear()
        else:
            self.resume_event.set()

    def stop(self) -> None:
        self.stop_event.set()
        self.resume_event.set()
        self._release_all()

    @staticmethod
    def _human_strength() -> float:
        return max(0.0, min(1.0, CFG.human))

    def _wait_ready(self) -> bool:
        while not self.stop_event.is_set():
            if self.resume_event.is_set() and self._target_focused():
                return True
            self.stop_event.wait(0.05)
        return False

    @staticmethod
    def _target_focused() -> bool:
        return not (
            CFG.auto_pause and CFG.target_title
            and win32gui.GetWindowText(win32gui.GetForegroundWindow()) != CFG.target_title
        )

    def _sleep(self, seconds: float) -> bool:
        remaining = max(0.0, seconds)
        last = time.perf_counter()
        while not self.stop_event.is_set():
            if not self.resume_event.is_set() or not self._target_focused():
                self._release_all()
                if not self._wait_ready():
                    return False
                last = time.perf_counter()
            now = time.perf_counter()
            remaining -= now - last
            if remaining <= 0:
                return True
            last = now
            self.stop_event.wait(min(remaining, 0.008))
        return False

    def _key_down(self, note: str) -> None:
        mapped = self._mapped(note)
        with self._held_lock:
            press_mapped(mapped)
            self._held.append((note, mapped))

    def _key_up(self, note: str) -> None:
        with self._held_lock:
            for index, (held_note, mapped) in enumerate(self._held):
                if held_note == note:
                    try:
                        release_mapped(mapped)
                    finally:
                        self._held.pop(index)
                    break

    def _release_all(self) -> None:
        with self._held_lock:
            for _note, mapped in reversed(self._held[:]):
                try:
                    release_mapped(mapped)
                except Exception:
                    pass
            self._held.clear()

    def _sleep_with_releases(self, seconds: float, deadlines: dict[str, float]) -> bool:
        """Wait accurately while releasing sustained notes at their own deadlines."""
        remaining = max(0.0, seconds)
        last = time.perf_counter()
        while not self.stop_event.is_set():
            if not self.resume_event.is_set() or not self._target_focused():
                self._release_all()
                deadlines.clear()
                if not self._wait_ready():
                    return False
                last = time.perf_counter()
            now = time.perf_counter()
            remaining -= now - last
            last = now
            for note, release_at in list(deadlines.items()):
                if release_at <= now:
                    self._key_up(note)
                    deadlines.pop(note, None)
            if remaining <= 0:
                return True
            next_release = min(deadlines.values(), default=now + remaining)
            self.stop_event.wait(min(remaining, max(0.001, next_release - now), 0.008))
        return False

    def _play_dynamic_token(
        self, token: str | list[str], duration: float, strength: float,
        hold_override: Optional[float], deadlines: dict[str, float],
    ) -> bool:
        """Play one onset without cutting off notes that sustain into the next onset."""
        started = time.perf_counter()
        if token != "|":
            notes = (token if isinstance(token, list) else [token])[:max(1, CFG.max_chord)]
            chord_spread = min(0.012, 0.012 * strength)
            for index, note in enumerate(notes):
                conflicting = next(
                    (held for held, mapped in self._held
                     if physical_identity(mapped) == physical_identity(self._mapped(note))),
                    None,
                )
                if conflicting is not None:
                    self._key_up(conflicting)
                    deadlines.pop(conflicting, None)
                    if not self._sleep(CFG.retrigger_gap):
                        return False
                self._key_down(note)
                hold_scale = max(0.05, CFG.hold / 0.80)
                hold_variation = random.uniform(1.0 - 0.18 * strength, 1.0 + 0.18 * strength)
                hold_time = max(0.025, (hold_override or duration * 0.80) * hold_scale * hold_variation)
                deadlines[note] = time.perf_counter() + min(8.0, hold_time)
                if index < len(notes) - 1 and chord_spread and not self._sleep(chord_spread):
                    return False
        return self._sleep_with_releases(
            max(0.0, duration - (time.perf_counter() - started)), deadlines,
        )

    def _play_token(
        self, token: str | list[str], duration: float, strength: float,
        hold_override: Optional[float] = None,
    ) -> None:
        if token == "|":
            self._sleep(duration)
            return

        notes = token if isinstance(token, list) else [token]
        chord_spread = 0.035 * strength
        started = time.perf_counter()
        for index, note in enumerate(notes):
            self._key_down(note)
            if index < len(notes) - 1 and chord_spread:
                self._sleep(random.uniform(0.0, chord_spread))

        hold_jitter = random.uniform(-0.15, 0.15) * strength
        if hold_override is None:
            hold_time = duration * max(0.05, min(1.0, CFG.hold + hold_jitter))
        else:
            hold_scale = max(0.05, CFG.hold / 0.80 + hold_jitter)
            hold_time = min(duration, max(0.035, hold_override * hold_scale))
        hold_time = max(0.035, hold_time)
        self._sleep(max(0.0, hold_time - (time.perf_counter() - started)))
        self._release_all()
        self._sleep(max(0.0, duration - (time.perf_counter() - started)))

    def run(self) -> None:
        try:
            tokens = list(tokenise(self.sheet.read_text(encoding="utf-8-sig")))
            if not tokens:
                raise ValueError("The selected sheet contains no playable notes.")
            meta = read_sheet_meta(self.sheet)
            raw_timings = meta.get("timings")
            raw_holds = meta.get("holds")
            timings = [float(value) for value in raw_timings] \
                if isinstance(raw_timings, list) and len(raw_timings) == len(tokens) else None
            holds = [float(value) for value in raw_holds] \
                if isinstance(raw_holds, list) and len(raw_holds) == len(tokens) else None
            self.queue.put(("total", len(tokens)))
            start_index = min(self.start_index, len(tokens))
            end_index = min(self.end_index or len(tokens), len(tokens))
            self.queue.put(("progress", start_index))
            started = time.perf_counter()
            for loop_number in range(self.loop_count):
                release_deadlines: dict[str, float] = {}
                adjusted_timings = humanize_timing_steps(
                    [max(CFG.input_interval, float(value) / self.speed)
                     for value in timings[start_index:end_index]],
                    self._human_strength(), CFG.input_interval,
                ) if timings else None
                if loop_number:
                    self.queue.put(("progress", start_index))
                for index, original in enumerate(
                    tokens[start_index:end_index], start=start_index + 1,
                ):
                    if not self._wait_ready():
                        break
                    token = original
                    strength = self._human_strength()
                    token_offset = index - 1
                    raw_duration = timings[token_offset] if timings else CFG.sec_per_token()
                    duration = (
                        adjusted_timings[index - start_index - 1]
                        if adjusted_timings is not None else
                        max(CFG.input_interval, float(raw_duration) / self.speed)
                    )
                    hold_override = (holds[token_offset] / self.speed) if holds else None
                    if strength and not timings:
                        duration *= 1 + 0.04 * strength * math.sin((time.perf_counter() - started) * 1.5)

                    if timings:
                        if not self._play_dynamic_token(
                            token, duration, strength, hold_override, release_deadlines,
                        ):
                            break
                        self.queue.put(("progress", index))
                        continue

                    onset = random.gauss(0.0, 0.025 * strength)
                    if onset > 0 and not self._sleep(onset):
                        break
                    self._play_token(
                        token, max(CFG.input_interval, duration - max(0.0, onset)),
                        strength, hold_override,
                    )
                    self.queue.put(("progress", index))
                self._release_all()
                if self.stop_event.is_set():
                    break
        except Exception as exc:
            self.queue.put(("error", str(exc)))
        finally:
            self._release_all()
            self.queue.put(("done",))


_prepare_data_directory()
_load_config()

# -- by Larsiopuw
