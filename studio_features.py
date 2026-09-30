"""Universal profiles, library tools, diagnostics and local sheet preview."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import math
from pathlib import Path
import shutil
import wave
import zipfile

import numpy as np

import autoplayer_core as core
from transcription import DRUM_KEYS, PIANO_KEYS, LOWEST_MIDI, format_sheet


PROFILES_FILE = core.DATA_DIR / "instrument_profiles.json"


@dataclass
class InstrumentProfile:
    name: str
    layout: str = core.CANONICAL_LAYOUT
    input_interval: float = 0.018
    retrigger_gap: float = 0.005
    max_chord: int = 6
    description: str = "Universelles Klavierprofil"
    drum_keymap: dict[str, str] = field(default_factory=lambda: core.DEFAULT_DRUM_KEYMAP.copy())

    def normalise(self) -> "InstrumentProfile":
        if len(self.layout) != len(core.CANONICAL_LAYOUT):
            self.layout = core.CANONICAL_LAYOUT
        self.input_interval = max(0.012, min(0.25, float(self.input_interval)))
        self.retrigger_gap = max(0.002, min(0.08, float(self.retrigger_gap)))
        self.max_chord = max(1, min(10, int(self.max_chord)))
        self.drum_keymap = core.normalise_drum_keymap(self.drum_keymap)
        return self


DEFAULT_PROFILES = {
    "Universell": InstrumentProfile("Universell"),
    "Schnelles Instrument": InstrumentProfile(
        "Schnelles Instrument", input_interval=0.016, retrigger_gap=0.004,
        max_chord=6, description="Fuer Spiele mit schneller Eingabeverarbeitung",
    ),
    "Kompatibilitaet": InstrumentProfile(
        "Kompatibilitaet", input_interval=0.040, retrigger_gap=0.012,
        max_chord=4, description="Stabil fuer langsamere oder empfindliche Spiele",
    ),
}


class ProfileStore:
    def __init__(self, path: Path = PROFILES_FILE):
        self.path = path
        self.profiles = self._load()

    def _load(self) -> dict[str, InstrumentProfile]:
        profiles = {name: InstrumentProfile(**asdict(value)) for name, value in DEFAULT_PROFILES.items()}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for item in raw.get("profiles", []):
                profile = InstrumentProfile(**item).normalise()
                profiles[profile.name] = profile
        except (OSError, ValueError, TypeError):
            pass
        return profiles

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"profiles": [asdict(profile.normalise()) for profile in self.profiles.values()]}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def put(self, profile: InstrumentProfile) -> None:
        profile.normalise()
        self.profiles[profile.name] = profile
        self.save()

    def activate(self, name: str) -> InstrumentProfile:
        profile = self.profiles.get(name) or self.profiles["Universell"]
        core.CFG.active_profile = profile.name
        core.CFG.key_layout = profile.layout
        core.CFG.input_interval = profile.input_interval
        core.CFG.retrigger_gap = profile.retrigger_gap
        core.CFG.max_chord = profile.max_chord
        core.CFG.drum_keymap = profile.drum_keymap.copy()
        core.save_cfg()
        return profile


@dataclass
class SheetTimeline:
    tokens: list[str | list[str]]
    timings: list[float]
    holds: list[float]
    meta: dict

    @property
    def duration(self) -> float:
        return sum(self.timings)


def load_timeline(path: Path) -> SheetTimeline:
    text = path.read_text(encoding="utf-8-sig")
    tokens = list(core.tokenise(text))
    meta = core.read_sheet_meta(path)
    default = 60.0 / (max(1, int(meta.get("bpm", 120))) * max(0.1, float(meta.get("subdiv", 4))))
    raw_timings = meta.get("timings")
    raw_holds = meta.get("holds")
    timings = [float(value) for value in raw_timings] \
        if isinstance(raw_timings, list) and len(raw_timings) == len(tokens) else [default] * len(tokens)
    holds = [float(value) for value in raw_holds] \
        if isinstance(raw_holds, list) and len(raw_holds) == len(tokens) else [default * 0.8] * len(tokens)
    return SheetTimeline(tokens, timings, holds, meta)


def token_text(token: str | list[str]) -> str:
    if isinstance(token, list):
        return f"[{' '.join(token)}]"
    return token


def save_timeline(path: Path, timeline: SheetTimeline) -> None:
    meta = dict(timeline.meta)
    meta["dynamic_timing"] = True
    meta["timings"] = [round(max(0.012, value), 4) for value in timeline.timings]
    meta["holds"] = [round(max(0.0, value), 4) for value in timeline.holds]
    lines = [f"#META {json.dumps(meta, ensure_ascii=False)}"]
    flat = [token_text(token) for token in timeline.tokens]
    lines.extend(" ".join(flat[index:index + 16]) for index in range(0, len(flat), 16))
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def analyze_quality(path: Path, profile: InstrumentProfile) -> list[tuple[str, str]]:
    timeline = load_timeline(path)
    issues: list[tuple[str, str]] = []
    fast = sum(value < profile.input_interval for value in timeline.timings)
    dense = sum(isinstance(token, list) and len(token) > profile.max_chord for token in timeline.tokens)
    physical_conflicts = 0
    for token in timeline.tokens:
        if not isinstance(token, list):
            continue
        physical = [core.physical_key(note) for note in token]
        physical_conflicts += int(len(physical) != len(set(physical)))
    if fast:
        issues.append(("Tempo", f"{fast} Anschlaege sind schneller als das aktive Profil erlaubt."))
    if dense:
        issues.append(("Akkorde", f"{dense} Akkorde enthalten mehr als {profile.max_chord} Tasten."))
    if physical_conflicts:
        issues.append(("Tastenbelegung", f"{physical_conflicts} Akkorde teilen sich physische Tasten."))
    folded = int(timeline.meta.get("folded", 0) or 0)
    if folded:
        issues.append(("Tonumfang", f"{folded} Noten wurden automatisch oktaviert."))
    if not issues:
        issues.append(("Sehr gut", "Keine kritischen Spielbarkeitsprobleme erkannt."))
    return issues


def set_song_properties(path: Path, **properties) -> None:
    core.write_sheet_meta(path, extra=properties)


def rename_song(path: Path, new_name: str) -> Path:
    safe = "".join(char for char in new_name.strip() if char not in '<>:"/\\|?*').rstrip(".")
    if not safe:
        raise ValueError("Der Songname darf nicht leer sein.")
    target = path.with_name(safe + path.suffix)
    if target.exists() and target != path:
        raise FileExistsError("Ein Song mit diesem Namen existiert bereits.")
    return path.rename(target)


def trash_song(path: Path) -> Path:
    trash = path.parent / ".Trash"
    trash.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = trash / f"{path.stem}-{stamp}{path.suffix}"
    number = 2
    while target.exists():
        target = trash / f"{path.stem}-{stamp}-{number}{path.suffix}"
        number += 1
    return Path(shutil.move(str(path), str(target)))


def create_backup(folder: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in folder.rglob("*"):
            if path.is_file() and ".Trash" not in path.parts:
                archive.write(path, Path("Sheets") / path.relative_to(folder))
        for extra in (core.DATA_FILE, PROFILES_FILE):
            if extra.exists():
                archive.write(extra, Path("Settings") / extra.name)
    return destination


def ensure_daily_backup(folder: Path, keep: int = 10) -> Path:
    backup_folder = core.DATA_DIR / "Backups"
    destination = backup_folder / f"Auto-Backup-{datetime.now():%Y-%m-%d}.zip"
    if not destination.exists():
        create_backup(folder, destination)
    backups = sorted(backup_folder.glob("Auto-Backup-*.zip"), key=lambda path: path.stat().st_mtime, reverse=True)
    for obsolete in backups[max(1, keep):]:
        obsolete.unlink(missing_ok=True)
    return destination


def restore_backup(archive_path: Path, folder: Path) -> int:
    restored = 0
    root = folder.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            relative = Path(info.filename)
            if not relative.parts or info.is_dir():
                continue
            if relative.parts[0] == "Settings" and relative.name in {core.DATA_FILE.name, PROFILES_FILE.name}:
                target = core.DATA_DIR / relative.name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                continue
            if relative.parts[0] != "Sheets":
                continue
            target = (folder / Path(*relative.parts[1:])).resolve()
            if root not in target.parents and target != root:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            restored += 1
    return restored


def render_preview(path: Path, destination: Path, start: float = 0.0, end: float | None = None) -> Path:
    timeline = load_timeline(path)
    sample_rate = 22050
    end = timeline.duration if end is None else min(timeline.duration, end)
    start = max(0.0, min(start, end))
    audio = np.zeros(max(1, int((end - start) * sample_rate)), dtype=np.float32)
    drum_mode = timeline.meta.get("instrument_mode") == "drums"
    random_generator = np.random.default_rng(2026)
    onset = 0.0
    for token, step, hold in zip(timeline.tokens, timeline.timings, timeline.holds):
        token_start = onset
        onset += step
        if token == "|" or token_start >= end or onset <= start:
            continue
        notes = token if isinstance(token, list) else [token]
        local_start = max(0.0, token_start - start)
        duration = min(max(0.16 if drum_mode else 0.04, hold), end - token_start, 2.5)
        count = max(1, int(duration * sample_rate))
        t = np.arange(count, dtype=np.float32) / sample_rate
        envelope = np.minimum(1.0, t / 0.008) * np.exp(-2.4 * t / max(0.08, duration))
        wave_data = np.zeros(count, dtype=np.float32)
        for note in notes:
            if drum_mode and note in DRUM_KEYS:
                if note == "h":
                    wave_data += np.sin(2.0 * math.pi * (82.0 * t - 24.0 * t * t)) * np.exp(-15.0 * t)
                elif note == "s":
                    wave_data += random_generator.normal(0.0, 0.8, count) * np.exp(-19.0 * t)
                    wave_data += 0.25 * np.sin(2.0 * math.pi * 190.0 * t) * np.exp(-15.0 * t)
                elif note in {"d", "f"}:
                    frequency = 165.0 if note == "d" else 112.0
                    wave_data += np.sin(2.0 * math.pi * frequency * t) * np.exp(-12.0 * t)
                elif note in {"a", "j", "k", "l"}:
                    noise = random_generator.normal(0.0, 0.55, count)
                    high_noise = noise - np.roll(noise, 1)
                    decay = 28.0 if note == "a" else 8.0
                    wave_data += high_noise * np.exp(-decay * t)
                else:
                    wave_data += random_generator.normal(0.0, 0.45, count) * np.exp(-35.0 * t)
                continue
            if note not in PIANO_KEYS:
                continue
            midi = LOWEST_MIDI + PIANO_KEYS.index(note)
            frequency = 440.0 * 2.0 ** ((midi - 69) / 12.0)
            wave_data += np.sin(2.0 * math.pi * frequency * t)
            wave_data += 0.18 * np.sin(4.0 * math.pi * frequency * t)
        if notes:
            wave_data *= envelope * (0.28 / math.sqrt(len(notes)))
        index = int(local_start * sample_rate)
        available = min(len(wave_data), len(audio) - index)
        if available > 0:
            audio[index:index + available] += wave_data[:available]
    peak = float(np.max(np.abs(audio))) if len(audio) else 1.0
    if peak > 0.98:
        audio *= 0.98 / peak
    pcm = (audio * 32767).astype("<i2")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(destination), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())
    return destination


def replace_section(target: Path, replacement: Path, start: float, end: float) -> None:
    original = load_timeline(target)
    patch = load_timeline(replacement)
    onsets: list[float] = []
    current = 0.0
    for duration in original.timings:
        onsets.append(current)
        current += duration
    first = next((index for index, value in enumerate(onsets) if value >= start), len(onsets))
    last = next((index for index, value in enumerate(onsets) if value >= end), len(onsets))
    original.tokens[first:last] = patch.tokens
    original.timings[first:last] = patch.timings
    original.holds[first:last] = patch.holds
    original.meta["edited"] = True
    original.meta["section_replaced"] = [round(start, 3), round(end, 3)]
    save_timeline(target, original)


# -- by Larsiopuw
