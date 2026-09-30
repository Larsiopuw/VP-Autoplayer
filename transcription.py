#!/usr/bin/env python3
"""Convert MIDI or local audio into Virtual Piano sheet notation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import statistics
import subprocess
import tempfile
from typing import Iterable, Optional

# Virtual Piano's fixed 61-key C2-C7 layout in chromatic order.
PIANO_KEYS = "1!2@34$5%6^78*9(0qQwWeErtTyYuiIoOpPasSdDfgGhHjJklLzZxcCvVbBnm"
LOWEST_MIDI = 36
HIGHEST_MIDI = LOWEST_MIDI + len(PIANO_KEYS) - 1
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
MIDI_EXTENSIONS = {".mid", ".midi"}
SHIFTED_DIGITS = {"!": "1", "@": "2", "$": "4", "%": "5", "^": "6", "*": "8", "(": "9"}


def physical_piano_key(key: str) -> str:
    return SHIFTED_DIGITS.get(key, key.lower() if key.isalpha() else key)


QUALITY_PROFILES = {
    "Melodie": {
        "onset": 0.50, "frame": 0.34, "minimum_length": 65.0,
        "minimum_step": 0.025, "chord_window": 0.030, "max_chord": 1,
    },
    "Ausgewogen": {
        "onset": 0.42, "frame": 0.28, "minimum_length": 45.0,
        "minimum_step": 0.018, "chord_window": 0.032, "max_chord": 5,
    },
    "Vollstaendig": {
        "onset": 0.34, "frame": 0.22, "minimum_length": 35.0,
        "minimum_step": 0.016, "chord_window": 0.028, "max_chord": 6,
    },
    "Sehr schnell": {
        "onset": 0.38, "frame": 0.25, "minimum_length": 28.0,
        "minimum_step": 0.014, "chord_window": 0.024, "max_chord": 5,
    },
}
AUTO_QUALITY_PROFILE = "Automatisch"
MIDI_QUALITY_PROFILE = "MIDI-Direktimport"
MIDI_OPTIMIZED_PROFILE = "MIDI-Spieloptimiert"
DRUM_ORIGINAL_PROFILE = "Original-Schlagzeug"
DRUM_ARRANGEMENT_PROFILE = "Komplettes Lied umwandeln"
DRUM_KEYS = "asdfhjkl;"
DRUM_NAMES = {
    "a": "Hi-Hat", "s": "Snare", "d": "Hohe Tom", "f": "Tiefe Tom",
    "h": "Bassdrum", "j": "Crash links", "k": "Ride", "l": "Crash rechts", ";": "Percussion",
}
KEY_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def choose_automatic_profile(events: Iterable[NoteEvent]) -> str:
    """Choose an audio profile from density, speed and polyphony."""
    ordered = sorted(events, key=lambda event: (event.start, event.pitch))
    if len(ordered) < 2:
        return "Melodie"

    onsets = sorted({round(event.start, 3) for event in ordered})
    gaps = [right - left for left, right in zip(onsets, onsets[1:]) if right > left]
    median_gap = statistics.median(gaps) if gaps else 1.0
    duration = max(event.end for event in ordered) - min(event.start for event in ordered)
    notes_per_second = len(ordered) / max(0.1, duration)

    groups: list[list[NoteEvent]] = []
    for event in ordered:
        if not groups or event.start - groups[-1][0].start > 0.035:
            groups.append([event])
        else:
            groups[-1].append(event)
    chord_ratio = sum(len(group) > 1 for group in groups) / max(1, len(groups))
    median_chord = statistics.median(len(group) for group in groups)

    if median_gap < 0.080 or notes_per_second >= 9.0:
        return "Sehr schnell"
    if median_chord >= 3 or chord_ratio >= 0.42:
        return "Vollstaendig"
    if chord_ratio <= 0.10 and median_chord == 1:
        return "Melodie"
    return "Ausgewogen"


def gm_drum_key(note: int) -> str:
    """Map General-MIDI percussion notes to the pictured nine-key game kit."""
    if note in {35, 36}:
        return "h"
    if note in {37, 38, 39, 40}:
        return "s"
    if note in {41, 43, 45}:
        return "f"
    if note in {47, 48, 50}:
        return "d"
    if note in {42, 44, 46, 54, 69, 70}:
        return "a"
    if note in {49, 55, 57}:
        return "j"
    if note in {51, 53, 59}:
        return "k"
    if note in {52, 67, 68, 71, 72, 80, 81}:
        return "l"
    if note in {60, 62, 64, 65}:
        return "d"
    if note in {61, 63, 66}:
        return "f"
    return ";"


def estimate_key(events: Iterable[NoteEvent]) -> str:
    weights = [0.0] * 12
    for event in events:
        weights[event.pitch % 12] += max(0.02, event.end - event.start) * max(1, event.velocity)
    return KEY_NAMES[max(range(12), key=weights.__getitem__)] if any(weights) else "Unbekannt"


class TranscriptionDependencyError(RuntimeError):
    """Raised when a required importer component is unavailable."""


@dataclass(frozen=True)
class NoteEvent:
    start: float
    end: float
    pitch: int
    velocity: int = 100


@dataclass(frozen=True)
class SheetResult:
    path: Path
    notes: int
    chords: int
    rests: int
    folded: int
    bpm: int
    subdiv: float


def safe_stem(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value).strip().rstrip(".")
    return cleaned or "Imported song"


def available_output_path(destination: Path, stem: str) -> Path:
    candidate = destination / f"{stem}.txt"
    counter = 2
    while candidate.exists():
        candidate = destination / f"{stem} ({counter}).txt"
        counter += 1
    return candidate


def fit_pitch(pitch: int, transpose: int = 0, fold_octaves: bool = True) -> tuple[Optional[int], bool]:
    adjusted = int(pitch) + int(transpose)
    original = adjusted
    if fold_octaves:
        while adjusted < LOWEST_MIDI:
            adjusted += 12
        while adjusted > HIGHEST_MIDI:
            adjusted -= 12
    if not LOWEST_MIDI <= adjusted <= HIGHEST_MIDI:
        return None, False
    return adjusted, adjusted != original


def events_to_tokens(
    events: Iterable[NoteEvent],
    bpm: int,
    subdiv: float,
    transpose: int = 0,
    fold_octaves: bool = True,
) -> tuple[list[str], int]:
    step_seconds = 60.0 / (max(1, bpm) * max(0.1, subdiv))
    slots: dict[int, dict[str, tuple[int, float]]] = defaultdict(dict)
    folded = 0
    for event in events:
        pitch, changed = fit_pitch(event.pitch, transpose, fold_octaves)
        if pitch is None:
            continue
        folded += int(changed)
        slot = max(0, round(max(0.0, event.start) / step_seconds))
        key = PIANO_KEYS[pitch - LOWEST_MIDI]
        quality = (event.velocity, max(0.0, event.end - event.start))
        if quality > slots[slot].get(key, (-1, -1.0)):
            slots[slot][key] = quality

    if not slots:
        return [], folded
    tokens: list[str] = []
    for slot in range(max(slots) + 1):
        candidates = slots.get(slot, {})
        strongest = sorted(
            candidates, key=lambda key: candidates[key], reverse=True
        )[:4]
        notes = sorted(strongest, key=PIANO_KEYS.index)
        if not notes:
            tokens.append("|")
        elif len(notes) == 1:
            tokens.append(notes[0])
        else:
            tokens.append(f"[{' '.join(notes)}]")
    return tokens, folded


def events_to_timed_tokens(
    events: Iterable[NoteEvent],
    transpose: int = 0,
    fold_octaves: bool = True,
    minimum_step: float = 0.018,
    chord_window: float = 0.032,
    max_chord_notes: int = 6,
) -> tuple[list[str], list[float], list[float], int]:
    """Create a playable sequence while preserving every real onset interval."""
    prepared: list[tuple[float, str, int, float]] = []
    folded = 0
    for event in events:
        pitch, changed = fit_pitch(event.pitch, transpose, fold_octaves)
        if pitch is None:
            continue
        folded += int(changed)
        prepared.append((
            max(0.0, event.start), PIANO_KEYS[pitch - LOWEST_MIDI],
            event.velocity, max(0.035, event.end - event.start),
        ))
    prepared.sort(key=lambda item: (item[0], -item[2], PIANO_KEYS.index(item[1])))
    if not prepared:
        return [], [], [], folded

    groups: list[dict] = []
    for onset, key, velocity, hold in prepared:
        # A different pitch inside a very narrow window belongs to one chord.
        # The same pitch must always become a new step so fast repetitions are
        # not swallowed (important for pieces such as Rush E).
        same_chord = bool(
            groups
            and onset - groups[-1]["time"] <= chord_window
            and key not in groups[-1]["notes"]
        )
        if not same_chord:
            groups.append({"time": onset, "notes": {}})
        group = groups[-1]
        quality = (velocity, hold)
        if quality > group["notes"].get(key, (-1, -1.0)):
            group["notes"][key] = quality

    tokens: list[str] = []
    onsets: list[float] = []
    holds: list[float] = []
    for group in groups:
        strongest: list[str] = []
        used_physical_keys: set[str] = set()
        for key in sorted(group["notes"], key=lambda item: group["notes"][item], reverse=True):
            physical = physical_piano_key(key)
            if physical not in used_physical_keys:
                strongest.append(key)
                used_physical_keys.add(physical)
            if len(strongest) == max(1, max_chord_notes):
                break
        notes = sorted(strongest, key=PIANO_KEYS.index)
        if not notes:
            continue
        tokens.append(notes[0] if len(notes) == 1 else f"[{' '.join(notes)}]")
        onsets.append(float(group["time"]))
        holds.append(statistics.median(group["notes"][key][1] for key in strongest))

    if not tokens:
        return [], [], [], folded
    intervals = [max(minimum_step, right - left) for left, right in zip(onsets, onsets[1:])]
    final_interval = statistics.median(intervals) if intervals else max(0.10, holds[0])
    timings = intervals + [final_interval]
    if onsets[0] > 0.05:
        tokens.insert(0, "|")
        timings.insert(0, onsets[0])
        holds.insert(0, 0.0)
    return tokens, timings, holds, folded


def normalise_bpm(value: float) -> int:
    bpm = max(1.0, float(value))
    while bpm < 55:
        bpm *= 2
    while bpm > 190:
        bpm /= 2
    return max(40, min(220, round(bpm)))


def infer_subdivision(events: Iterable[NoteEvent], bpm: int) -> float:
    """Choose the least dense beat grid that still follows the note onsets."""
    onsets = sorted({round(event.start, 4) for event in events})
    if len(onsets) < 2:
        return 4.0
    beat_positions = [onset * max(1, bpm) / 60.0 for onset in onsets]
    best = 4.0
    best_score = float("inf")
    for subdivision in (1.0, 2.0, 3.0, 4.0):
        errors = [
            abs(position * subdivision - round(position * subdivision)) / subdivision
            for position in beat_positions
        ]
        score = statistics.median(errors) + subdivision * 0.05
        if score < best_score:
            best, best_score = subdivision, score
    return best


def clean_events(events: Iterable[NoteEvent]) -> list[NoteEvent]:
    """Remove conversion noise and duplicate micro-notes before quantisation."""
    cleaned: list[NoteEvent] = []
    last_by_pitch: dict[int, NoteEvent] = {}
    for event in sorted(events, key=lambda item: (item.start, item.pitch, -item.velocity)):
        if event.end - event.start < 0.035 or event.velocity < 8:
            continue
        previous = last_by_pitch.get(event.pitch)
        if previous and abs(event.start - previous.start) < 0.025:
            if event.velocity <= previous.velocity and event.end <= previous.end:
                continue
            if previous in cleaned:
                cleaned.remove(previous)
        cleaned.append(event)
        last_by_pitch[event.pitch] = event
    return cleaned


def optimise_extreme_midi(events: Iterable[NoteEvent]) -> tuple[list[NoteEvent], dict]:
    """Reduce unmistakable Black-MIDI effects to a playable keyboard arrangement."""
    original = sorted(events, key=lambda event: (event.start, -event.velocity, event.pitch))
    if len(original) < 2:
        return original, {"optimized": False, "source_notes": len(original)}

    onset_groups: dict[float, list[NoteEvent]] = defaultdict(list)
    for event in original:
        onset_groups[round(event.start, 4)].append(event)
    onsets = sorted(onset_groups)
    gaps = [right - left for left, right in zip(onsets, onsets[1:]) if right > left]
    too_fast = sum(gap < 0.018 for gap in gaps)
    oversized = sum(len(group) > 12 for group in onset_groups.values())
    extreme = len(original) >= 12_000 and (too_fast >= 500 or oversized >= 50)
    if not extreme:
        return original, {
            "optimized": False,
            "source_notes": len(original),
            "fast_onsets": too_fast,
            "oversized_chords": oversized,
        }

    velocities = sorted(event.velocity for event in original)
    percentile_floor = velocities[round((len(velocities) - 1) * 0.80)]
    velocity_floor = min(96, max(min(65, max(velocities)), percentile_floor))
    candidates = [
        event for event in original
        if event.velocity >= velocity_floor and LOWEST_MIDI <= event.pitch <= HIGHEST_MIDI
    ]
    if not candidates:
        candidates = [event for event in original if LOWEST_MIDI <= event.pitch <= HIGHEST_MIDI]

    windows: list[list[NoteEvent]] = []
    for event in candidates:
        if not windows or event.start - windows[-1][0].start >= 0.018:
            windows.append([event])
        else:
            windows[-1].append(event)

    reduced: list[NoteEvent] = []
    for window in windows:
        by_pitch: dict[int, NoteEvent] = {}
        for event in window:
            previous = by_pitch.get(event.pitch)
            if previous is None or (
                event.velocity, event.end - event.start
            ) > (previous.velocity, previous.end - previous.start):
                by_pitch[event.pitch] = event
        ranked = sorted(
            by_pitch.values(),
            key=lambda event: (-event.velocity, -(event.end - event.start), abs(event.pitch - 66)),
        )
        preferred = ranked[:4]
        if ranked:
            preferred.extend((min(ranked, key=lambda event: event.pitch), max(ranked, key=lambda event: event.pitch)))
        selected: list[NoteEvent] = []
        used_pitches: set[int] = set()
        for event in preferred + ranked:
            if event.pitch in used_pitches:
                continue
            selected.append(event)
            used_pitches.add(event.pitch)
            if len(selected) == 6:
                break
        anchor = window[0].start
        reduced.extend(
            NoteEvent(anchor, max(anchor + 0.035, event.end), event.pitch, event.velocity)
            for event in selected
        )

    return reduced, {
        "optimized": True,
        "source_notes": len(original),
        "kept_notes": len(reduced),
        "removed_notes": len(original) - len(reduced),
        "velocity_floor": velocity_floor,
        "fast_onsets": too_fast,
        "oversized_chords": oversized,
    }


def format_sheet(
    tokens: list[str], bpm: int, subdiv: float, source: str,
    timings: Optional[list[float]] = None,
    holds: Optional[list[float]] = None,
    extra_meta: Optional[dict] = None,
) -> str:
    meta = {
        "bpm": int(bpm),
        "subdiv": float(subdiv),
        "note": f"Automatically imported from {source}",
        "generated": True,
    }
    if timings and len(timings) == len(tokens):
        meta["dynamic_timing"] = True
        meta["timings"] = [round(max(0.01, value), 4) for value in timings]
        if holds and len(holds) == len(tokens):
            meta["holds"] = [round(max(0.0, value), 4) for value in holds]
    if extra_meta:
        meta.update(extra_meta)
    lines = [f"#META {json.dumps(meta, ensure_ascii=False)}"]
    for index in range(0, len(tokens), 16):
        lines.append(" ".join(tokens[index:index + 16]))
    return "\n".join(lines).rstrip() + "\n"


def read_midi(path: Path) -> tuple[list[NoteEvent], int]:
    try:
        import mido
    except ImportError as exc:
        raise TranscriptionDependencyError(
            "MIDI import requires 'mido'. Install the project requirements first."
        ) from exc

    midi = mido.MidiFile(path)
    tempo = 500_000
    detected_bpm: int | None = None
    current_seconds = 0.0
    active: dict[tuple[int, int], list[tuple[float, int]]] = defaultdict(list)
    events: list[NoteEvent] = []

    for message in mido.merge_tracks(midi.tracks):
        current_seconds += mido.tick2second(message.time, midi.ticks_per_beat, tempo)
        if message.type == "set_tempo":
            tempo = message.tempo
            if detected_bpm is None:
                detected_bpm = normalise_bpm(mido.tempo2bpm(tempo))
        elif message.type == "note_on" and message.velocity > 0:
            if getattr(message, "channel", -1) == 9:
                continue
            active[(getattr(message, "channel", 0), message.note)].append(
                (current_seconds, message.velocity)
            )
        elif message.type in {"note_off", "note_on"}:
            key = (getattr(message, "channel", 0), message.note)
            if active[key]:
                start, velocity = active[key].pop(0)
                events.append(NoteEvent(start, current_seconds, message.note, velocity))

    # MIDI already contains exact note events. Audio clean-up thresholds would
    # otherwise delete intentional short notes and fast repetitions.
    return sorted(events, key=lambda event: (event.start, event.pitch, -event.velocity)), detected_bpm or 120


def read_drum_midi(path: Path) -> tuple[list[NoteEvent], int]:
    """Read General-MIDI percussion from channel 10 without pitched tracks."""
    try:
        import mido
    except ImportError as exc:
        raise TranscriptionDependencyError("MIDI import requires 'mido'.") from exc
    midi = mido.MidiFile(path)
    tempo = 500_000
    detected_bpm: int | None = None
    current_seconds = 0.0
    active: dict[int, list[tuple[float, int]]] = defaultdict(list)
    events: list[NoteEvent] = []
    for message in mido.merge_tracks(midi.tracks):
        current_seconds += mido.tick2second(message.time, midi.ticks_per_beat, tempo)
        if message.type == "set_tempo":
            tempo = message.tempo
            if detected_bpm is None:
                detected_bpm = normalise_bpm(mido.tempo2bpm(tempo))
        elif getattr(message, "channel", -1) == 9 and message.type == "note_on" and message.velocity > 0:
            active[message.note].append((current_seconds, message.velocity))
        elif getattr(message, "channel", -1) == 9 and message.type in {"note_off", "note_on"}:
            if active[message.note]:
                start, velocity = active[message.note].pop(0)
                events.append(NoteEvent(start, max(start + 0.035, current_seconds), message.note, velocity))
    if not events:
        raise ValueError("Diese MIDI-Datei enthält keine Schlagzeugspur auf dem General-MIDI-Kanal 10.")
    return sorted(events, key=lambda event: (event.start, event.pitch)), detected_bpm or 120


def drum_events_to_timed_tokens(
    events: Iterable[NoteEvent], minimum_step: float = 0.018, chord_window: float = 0.012,
) -> tuple[list[str], list[float], list[float]]:
    prepared = sorted(
        ((max(0.0, event.start), gm_drum_key(event.pitch), event.velocity) for event in events),
        key=lambda item: (item[0], DRUM_KEYS.index(item[1])),
    )
    # Some exported drum MIDIs model one acoustic hit as a very short velocity
    # ramp (for example 27 -> 87 -> 127). A keyboard-controlled game cannot
    # reproduce those sample layers and turns them into an audible key buzz.
    # Merge only same-pad, non-decreasing ramps; real rolls with falling or
    # alternating velocity remain separate.
    consolidated: list[tuple[float, str, int]] = []
    for onset, key, velocity in prepared:
        if consolidated:
            previous_onset, previous_key, previous_velocity = consolidated[-1]
            debounce = 0.090 if key in {"h", "s", "d", "f"} else 0.032
            if (
                key == previous_key
                and onset - previous_onset <= debounce
                and velocity >= previous_velocity
            ):
                consolidated[-1] = (previous_onset, key, velocity)
                continue
        consolidated.append((onset, key, velocity))
    prepared = consolidated
    groups: list[dict] = []
    for onset, key, velocity in prepared:
        if not groups or onset - groups[-1]["time"] > chord_window:
            groups.append({"time": onset, "hits": {}})
        groups[-1]["hits"][key] = max(velocity, groups[-1]["hits"].get(key, 0))
    if not groups:
        return [], [], []
    tokens: list[str] = []
    onsets: list[float] = []
    for group in groups:
        keys = sorted(group["hits"], key=DRUM_KEYS.index)
        tokens.append(keys[0] if len(keys) == 1 else f"[{' '.join(keys)}]")
        onsets.append(group["time"])
    timings = [max(minimum_step, right - left) for left, right in zip(onsets, onsets[1:])]
    timings.append(statistics.median(timings) if timings else 0.12)
    holds = [0.045] * len(tokens)
    if onsets[0] > 0.05:
        tokens.insert(0, "|")
        timings.insert(0, onsets[0])
        holds.insert(0, 0.0)
    return tokens, timings, holds


def arrange_pitched_events_as_drums(
    events: Iterable[NoteEvent], bpm: int,
) -> list[NoteEvent]:
    """Turn pitched MIDI material into a sparse, playable drum arrangement."""
    ordered = sorted(events, key=lambda event: (event.start, event.pitch, -event.velocity))
    if not ordered:
        return []
    pitches = sorted(event.pitch for event in ordered)
    bass_limit = pitches[max(0, len(pitches) // 4 - 1)]
    onset_window = 0.028
    minimum_gap = max(0.065, min(0.120, 60.0 / max(1, bpm) / 7.0))
    melody_pads = (48, 43, 42, 48, 43, 51, 48, 42, 51, 52, 49, 52)
    groups: list[list[NoteEvent]] = []
    for event in ordered:
        if not groups or event.start - groups[-1][0].start > onset_window:
            groups.append([event])
        else:
            groups[-1].append(event)

    arranged: list[NoteEvent] = []
    previous_onset = -10.0
    previous_pad: int | None = None
    for group in groups:
        onset = group[0].start
        if onset - previous_onset < minimum_gap:
            continue
        unique = {event.pitch: event for event in group}
        notes = list(unique.values())
        highest = max(notes, key=lambda event: (event.pitch, event.velocity))
        lowest = min(notes, key=lambda event: (event.pitch, -event.velocity))
        melody_pad = melody_pads[highest.pitch % 12]
        pads: list[tuple[int, int]] = [(melody_pad, highest.velocity)]
        if lowest.pitch <= bass_limit and melody_pad != 36:
            pads.append((36, lowest.velocity))
        phrase_gap = onset - previous_onset
        if len(notes) >= 3 and phrase_gap >= 0.18:
            pads.append((49, max(event.velocity for event in notes)))
        elif phrase_gap >= 0.60 and previous_pad is not None:
            pads.append((52, highest.velocity))
        seen: set[int] = set()
        for pad, velocity in pads:
            if pad in seen:
                continue
            seen.add(pad)
            arranged.append(NoteEvent(onset, onset + 0.045, pad, velocity))
        previous_onset = onset
        previous_pad = melody_pad
    return arranged


def analyze_drum_audio(
    path: Path, section_start: float = 0.0, section_end: Optional[float] = None,
) -> tuple[list[NoteEvent], int]:
    """Detect kick, snare and cymbal energy from a local audio file."""
    try:
        import imageio_ffmpeg
        import librosa
        import numpy as np
    except ImportError as exc:
        raise TranscriptionDependencyError("Die integrierte Drum-Erkennung konnte nicht geladen werden.") from exc
    with tempfile.TemporaryDirectory(prefix="vp-drums-") as temporary:
        decoded = Path(temporary) / "decoded.wav"
        command = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y"]
        if section_start > 0:
            command.extend(["-ss", f"{section_start:.4f}"])
        command.extend(["-i", str(path)])
        if section_end is not None and section_end > section_start:
            command.extend(["-t", f"{section_end - section_start:.4f}"])
        command.extend(["-vn", "-ac", "1", "-ar", "22050", str(decoded)])
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=300, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            raise ValueError("Die Audiodatei konnte für die Drum-Erkennung nicht gelesen werden.")
        audio, sample_rate = librosa.load(decoded, sr=22050, mono=True)
        _harmonic, percussive = librosa.effects.hpss(audio)
        onset_frames = librosa.onset.onset_detect(
            y=percussive, sr=sample_rate, backtrack=True, units="frames", wait=2,
        )
        tempo, _beats = librosa.beat.beat_track(y=percussive, sr=sample_rate)
        tempo_value = float(tempo.flat[0] if hasattr(tempo, "flat") else tempo)
        events: list[NoteEvent] = []
        for frame in onset_frames:
            start_sample = int(librosa.frames_to_samples(frame))
            segment = percussive[start_sample:start_sample + int(sample_rate * 0.09)]
            if len(segment) < 64:
                continue
            spectrum = np.abs(np.fft.rfft(segment * np.hanning(len(segment))))
            frequencies = np.fft.rfftfreq(len(segment), 1.0 / sample_rate)
            total = float(spectrum.sum()) or 1.0
            low = float(spectrum[frequencies < 180].sum()) / total
            mid = float(spectrum[(frequencies >= 180) & (frequencies < 3500)].sum()) / total
            high = float(spectrum[frequencies >= 3500].sum()) / total
            onset = start_sample / sample_rate
            strength = max(50, min(127, round(float(np.sqrt(np.mean(segment ** 2))) * 900)))
            pitches: list[int] = []
            if low >= 0.18:
                pitches.append(36)
            if mid >= 0.42:
                pitches.append(38)
            if high >= 0.20:
                pitches.append(42)
            if not pitches:
                pitches.append(38)
            events.extend(NoteEvent(onset, onset + 0.06, pitch, strength) for pitch in pitches[:2])
    if not events:
        raise ValueError("In dieser Audiodatei konnten keine deutlichen Schlagzeuganschläge erkannt werden.")
    return events, normalise_bpm(tempo_value or 120)


def analyze_audio(
    path: Path, quality: str = "Ausgewogen",
    section_start: float = 0.0, section_end: Optional[float] = None,
) -> tuple[list[NoteEvent], int]:
    try:
        from basic_pitch.inference import predict
        import imageio_ffmpeg
        import librosa
    except ImportError as exc:
        raise TranscriptionDependencyError(
            "Die integrierte Audioerkennung konnte nicht geladen werden. "
            "Bitte verwende den vollstaendigen VP-Autoplayer-Build."
        ) from exc

    with tempfile.TemporaryDirectory(prefix="vp-autoplayer-") as temporary:
        decoded = Path(temporary) / "decoded.wav"
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
            "-y",
        ]
        if section_start > 0:
            command.extend(["-ss", f"{section_start:.4f}"])
        command.extend(["-i", str(path)])
        if section_end is not None and section_end > section_start:
            command.extend(["-t", f"{section_end - section_start:.4f}"])
        command.extend(["-vn", "-ac", "1", "-ar", "22050", str(decoded)])
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=300, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not decoded.exists():
            detail = completed.stderr.strip().splitlines()
            reason = detail[-1] if detail else "Unbekannter Decoderfehler"
            raise ValueError(f"Die Audiodatei konnte nicht gelesen werden: {reason}")
        audio, sample_rate = librosa.load(decoded, sr=22050, mono=True)
        tempo, _beats = librosa.beat.beat_track(y=audio, sr=sample_rate)
        tempo_value = float(tempo.flat[0] if hasattr(tempo, "flat") else tempo)
        automatic = quality == AUTO_QUALITY_PROFILE
        settings = QUALITY_PROFILES["Ausgewogen"] if automatic else QUALITY_PROFILES.get(
            quality, QUALITY_PROFILES["Ausgewogen"]
        )
        _, _, raw_events = predict(
            decoded, onset_threshold=settings["onset"], frame_threshold=settings["frame"],
            minimum_note_length=settings["minimum_length"],
        )
        events = [
            NoteEvent(float(start), float(end), int(pitch), max(1, min(127, round(amplitude * 127))))
            for start, end, pitch, amplitude, _pitch_bends in raw_events
        ]
        if automatic:
            selected = choose_automatic_profile(events)
            selected_settings = QUALITY_PROFILES[selected]
            if selected != "Ausgewogen":
                _, _, raw_events = predict(
                    decoded,
                    onset_threshold=selected_settings["onset"],
                    frame_threshold=selected_settings["frame"],
                    minimum_note_length=selected_settings["minimum_length"],
                )
                events = [
                    NoteEvent(
                        float(start), float(end), int(pitch),
                        max(1, min(127, round(amplitude * 127))),
                    )
                    for start, end, pitch, amplitude, _pitch_bends in raw_events
                ]
    return clean_events(events), normalise_bpm(tempo_value or 120)


def transcribe_audio(path: Path) -> list[NoteEvent]:
    return analyze_audio(path)[0]


def import_drum_song(
    source: Path, destination: Path, bpm: Optional[int] = None, subdiv: Optional[float] = None,
    section_start: float = 0.0, section_end: Optional[float] = None,
    import_profile: str = DRUM_ORIGINAL_PROFILE,
) -> SheetResult:
    suffix = source.suffix.casefold()
    full_arrangement = import_profile == DRUM_ARRANGEMENT_PROFILE
    if suffix in MIDI_EXTENSIONS:
        pitched_events, pitched_bpm = read_midi(source) if full_arrangement else ([], 120)
        try:
            events, detected_bpm = read_drum_midi(source)
        except ValueError:
            if not full_arrangement:
                raise
            events, detected_bpm = [], pitched_bpm
        if section_start > 0 or section_end is not None:
            upper = float("inf") if section_end is None else section_end
            events = [
                NoteEvent(event.start - section_start, event.end - section_start, event.pitch, event.velocity)
                for event in events if section_start <= event.start < upper
            ]
            pitched_events = [
                NoteEvent(event.start - section_start, event.end - section_start, event.pitch, event.velocity)
                for event in pitched_events if section_start <= event.start < upper
            ]
        if full_arrangement:
            events.extend(arrange_pitched_events_as_drums(pitched_events, bpm or pitched_bpm))
        resolved_profile = DRUM_ARRANGEMENT_PROFILE if full_arrangement else DRUM_ORIGINAL_PROFILE
        confidence = 0.90 if full_arrangement else 1.0
    elif suffix in AUDIO_EXTENSIONS:
        events, detected_bpm = analyze_drum_audio(source, section_start, section_end)
        if full_arrangement:
            pitched_events, pitched_bpm = analyze_audio(
                source, AUTO_QUALITY_PROFILE, section_start, section_end,
            )
            events.extend(arrange_pitched_events_as_drums(pitched_events, bpm or pitched_bpm))
        resolved_profile = DRUM_ARRANGEMENT_PROFILE if full_arrangement else DRUM_ORIGINAL_PROFILE
        confidence = 0.76 if full_arrangement else 0.82
    else:
        raise ValueError(f"Unsupported file type: {source.suffix or 'without extension'}")
    if not events:
        raise ValueError("In dieser Datei wurden keine Schlagzeuganschläge gefunden.")
    bpm = normalise_bpm(bpm or detected_bpm)
    subdiv = float(subdiv or infer_subdivision(events, bpm))
    tokens, timings, holds = drum_events_to_timed_tokens(events)
    if not tokens:
        raise ValueError("Aus den erkannten Anschlägen konnte kein Drum-Pattern erstellt werden.")
    destination.mkdir(parents=True, exist_ok=True)
    output = available_output_path(destination, safe_stem(source.stem))
    output.write_text(
        format_sheet(tokens, bpm, subdiv, source.name, timings, holds, {
            "instrument_mode": "drums",
            "quality_profile": resolved_profile,
            "drum_import_mode": "arrangement" if full_arrangement else "original",
            "source_path": str(source),
            "source_start": round(section_start, 3),
            "source_end": None if section_end is None else round(section_end, 3),
            "imported_at": datetime.now().isoformat(timespec="seconds"),
            "confidence": confidence,
            "detected_key": "Drum-Kit",
            "drum_mapping": DRUM_NAMES,
            "folded": 0,
        }), encoding="utf-8",
    )
    return SheetResult(
        path=output, notes=sum(token != "|" for token in tokens),
        chords=sum(token.startswith("[") for token in tokens), rests=tokens.count("|"),
        folded=0, bpm=bpm, subdiv=subdiv,
    )


def import_song(
    source: Path,
    destination: Path,
    bpm: Optional[int] = None,
    subdiv: Optional[float] = None,
    transpose: int = 0,
    fold_octaves: bool = True,
    quality: str = "Ausgewogen",
    section_start: float = 0.0,
    section_end: Optional[float] = None,
    instrument_mode: str = "piano",
) -> SheetResult:
    if instrument_mode == "drums":
        return import_drum_song(
            source, destination, bpm, subdiv, section_start, section_end, quality,
        )
    suffix = source.suffix.casefold()
    midi_optimisation: dict = {}
    if suffix in MIDI_EXTENSIONS:
        events, detected_bpm = read_midi(source)
        if section_start > 0 or section_end is not None:
            upper = float("inf") if section_end is None else section_end
            events = [
                NoteEvent(event.start - section_start, event.end - section_start, event.pitch, event.velocity)
                for event in events if section_start <= event.start < upper
            ]
        events, midi_optimisation = optimise_extreme_midi(events)
        bpm = normalise_bpm(bpm or detected_bpm)
        resolved_quality = (
            MIDI_OPTIMIZED_PROFILE if midi_optimisation.get("optimized") else MIDI_QUALITY_PROFILE
        )
    elif suffix in AUDIO_EXTENSIONS:
        events, detected_bpm = analyze_audio(source, quality, section_start, section_end)
        bpm = normalise_bpm(bpm or detected_bpm)
        resolved_quality = (
            choose_automatic_profile(events)
            if quality == AUTO_QUALITY_PROFILE else quality
        )
    else:
        raise ValueError(f"Unsupported file type: {source.suffix or 'without extension'}")
    if not events:
        raise ValueError("No notes could be detected in this file.")

    subdiv = float(subdiv or infer_subdivision(events, bpm))
    if suffix in MIDI_EXTENSIONS:
        settings = (
            {"minimum_step": 0.018, "chord_window": 0.003, "max_chord": 6}
            if midi_optimisation.get("optimized") else
            {"minimum_step": 0.001, "chord_window": 0.002, "max_chord": len(PIANO_KEYS)}
        )
    else:
        settings = QUALITY_PROFILES.get(resolved_quality, QUALITY_PROFILES["Ausgewogen"])
    tokens, timings, holds, folded = events_to_timed_tokens(
        events, transpose, fold_octaves,
        minimum_step=settings["minimum_step"],
        chord_window=settings["chord_window"],
        max_chord_notes=settings["max_chord"],
    )
    if not tokens:
        raise ValueError("Detected notes are outside the playable C2-C7 range.")
    onset_values = sorted({round(event.start, 4) for event in events})
    onset_gaps = [right - left for left, right in zip(onset_values, onset_values[1:]) if right > left]
    tempo_variation = (
        statistics.pstdev(onset_gaps) / statistics.mean(onset_gaps)
        if len(onset_gaps) > 1 and statistics.mean(onset_gaps) else 0.0
    )
    destination.mkdir(parents=True, exist_ok=True)
    output = available_output_path(destination, safe_stem(source.stem))
    output.write_text(
        format_sheet(
            tokens, bpm, subdiv, source.name, timings, holds,
            {
                "quality_profile": resolved_quality,
                "instrument_mode": "piano",
                "requested_quality_profile": quality,
                "source_path": str(source),
                "source_start": round(section_start, 3),
                "source_end": None if section_end is None else round(section_end, 3),
                "imported_at": datetime.now().isoformat(timespec="seconds"),
                "folded": folded,
                "confidence": (
                    1.0 if suffix in MIDI_EXTENSIONS else
                    round(statistics.median(event.velocity for event in events) / 127, 3)
                ),
                "midi_optimized": bool(midi_optimisation.get("optimized")),
                "midi_optimisation": midi_optimisation,
                "detected_key": estimate_key(events),
                "tempo_variation": round(tempo_variation, 3),
                "tempo_changes": tempo_variation > 0.35,
            },
        ), encoding="utf-8"
    )
    return SheetResult(
        path=output,
        notes=sum(token != "|" for token in tokens),
        chords=sum(token.startswith("[") for token in tokens),
        rests=tokens.count("|"),
        folded=folded,
        bpm=bpm,
        subdiv=subdiv,
    )

# -- by Larsiopuw
