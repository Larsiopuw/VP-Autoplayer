"""Sequential, fault-tolerant imports for a selected set of audio/MIDI files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

from transcription import (
    AUDIO_EXTENSIONS, AUTO_QUALITY_PROFILE, DRUM_ORIGINAL_PROFILE,
    MIDI_EXTENSIONS, MIDI_QUALITY_PROFILE, SheetResult, import_song,
)


SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS | MIDI_EXTENSIONS


@dataclass(frozen=True)
class BatchOutcome:
    source: Path
    result: SheetResult | None = None
    error: str = ""
    skipped: bool = False


def run_batch_import(
    sources: list[Path], destination: Path, instrument_mode: str,
    audio_quality: str = AUTO_QUALITY_PROFILE,
    drum_profile: str = DRUM_ORIGINAL_PROFILE,
    fold_octaves: bool = True,
    existing_sources: set[str] | None = None,
    cancelled: Event | None = None,
    progress: Callable[[int, int, BatchOutcome], None] | None = None,
    importer: Callable = import_song,
) -> list[BatchOutcome]:
    """Continue after individual errors and report every selected file once."""
    outcomes: list[BatchOutcome] = []
    seen = {str(Path(path).resolve()).casefold() for path in (existing_sources or set())}
    total = len(sources)
    for source in sources:
        if cancelled and cancelled.is_set():
            break
        source = Path(source)
        key = str(source.resolve()).casefold()
        if key in seen:
            outcome = BatchOutcome(source, error="Bereits importiert", skipped=True)
        elif source.suffix.casefold() not in SUPPORTED_EXTENSIONS:
            outcome = BatchOutcome(source, error="Dateiformat wird nicht unterstützt")
        elif not source.is_file():
            outcome = BatchOutcome(source, error="Datei nicht gefunden")
        else:
            quality = (
                drum_profile if instrument_mode == "drums" else
                MIDI_QUALITY_PROFILE if source.suffix.casefold() in MIDI_EXTENSIONS else
                audio_quality
            )
            try:
                result = importer(
                    source, destination, None, None, 0,
                    fold_octaves if instrument_mode != "drums" else False,
                    quality, 0.0, None, instrument_mode,
                )
                outcome = BatchOutcome(source, result=result)
                seen.add(key)
            except Exception as exc:
                outcome = BatchOutcome(source, error=str(exc))
        outcomes.append(outcome)
        if progress:
            progress(len(outcomes), total, outcome)
    return outcomes


# -- by Larsiopuw
