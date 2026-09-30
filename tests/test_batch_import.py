from pathlib import Path
from threading import Event

import mido

from batch_import import run_batch_import
from transcription import AUTO_QUALITY_PROFILE, DRUM_ORIGINAL_PROFILE, MIDI_QUALITY_PROFILE


def test_batch_import_handles_mixed_files_and_continues_after_errors(tmp_path: Path) -> None:
    midi = tmp_path / "one.mid"
    audio = tmp_path / "two.wav"
    broken = tmp_path / "bad.mp3"
    for path in (midi, audio, broken):
        path.touch()
    calls = []
    updates = []

    def importer(*args):
        calls.append(args)
        if args[0] == broken:
            raise ValueError("unlesbar")
        return object()

    outcomes = run_batch_import(
        [midi, audio, broken], tmp_path, "piano", importer=importer,
        progress=lambda done, total, outcome: updates.append((done, total, outcome.source)),
    )

    assert [call[6] for call in calls] == [MIDI_QUALITY_PROFILE, AUTO_QUALITY_PROFILE, AUTO_QUALITY_PROFILE]
    assert [outcome.error for outcome in outcomes] == ["", "", "unlesbar"]
    assert [item[:2] for item in updates] == [(1, 3), (2, 3), (3, 3)]


def test_batch_import_skips_existing_and_respects_stop(tmp_path: Path) -> None:
    first = tmp_path / "first.mid"
    second = tmp_path / "second.mid"
    first.touch()
    second.touch()
    cancelled = Event()
    calls = []

    def importer(*args):
        calls.append(args)
        cancelled.set()
        return object()

    outcomes = run_batch_import(
        [first, second, first], tmp_path, "drums",
        existing_sources={str(first)}, cancelled=cancelled, importer=importer,
    )
    assert outcomes[0].skipped
    assert outcomes[1].result is not None
    assert len(outcomes) == 2
    assert calls[0][6] == DRUM_ORIGINAL_PROFILE


def test_batch_import_creates_two_real_midi_sheets(tmp_path: Path) -> None:
    sources = []
    for name in ("alpha", "beta"):
        midi = mido.MidiFile(ticks_per_beat=480)
        track = mido.MidiTrack()
        midi.tracks.append(track)
        for note in (60, 64, 67):
            track.append(mido.Message("note_on", note=note, velocity=90, time=240))
            track.append(mido.Message("note_off", note=note, velocity=0, time=240))
        source = tmp_path / f"{name}.mid"
        midi.save(source)
        sources.append(source)
    destination = tmp_path / "sheets"
    destination.mkdir()

    outcomes = run_batch_import(sources, destination, "piano")

    assert len(outcomes) == 2
    assert all(outcome.result is not None for outcome in outcomes)
    assert all(outcome.result.path.is_file() for outcome in outcomes)
    assert len(list(destination.glob("*.txt"))) == 2


# -- by Larsiopuw
