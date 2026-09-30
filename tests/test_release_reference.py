"""Small, self-generated MIDI references for the full import pipeline."""

from pathlib import Path

import mido

from studio_features import load_timeline
from transcription import import_song


def test_piano_reference_keeps_midi_onsets_and_holds(tmp_path: Path) -> None:
    source = tmp_path / "piano-reference.mid"
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))
    track.append(mido.Message("note_on", note=60, velocity=100, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=240))
    track.append(mido.Message("note_on", note=64, velocity=100, time=240))
    track.append(mido.Message("note_off", note=64, velocity=0, time=240))
    midi.save(source)

    result = import_song(source, tmp_path / "Piano")
    timeline = load_timeline(result.path)

    assert timeline.tokens == ["t", "u"]
    assert abs(timeline.timings[0] - 0.5) < 0.005
    assert abs(timeline.holds[0] - 0.25) < 0.005
    assert timeline.meta["instrument_mode"] == "piano"


def test_original_drum_reference_ignores_melodic_track(tmp_path: Path) -> None:
    source = tmp_path / "drum-reference.mid"
    midi = mido.MidiFile(ticks_per_beat=480)
    drum_track = mido.MidiTrack()
    melody_track = mido.MidiTrack()
    midi.tracks.extend([drum_track, melody_track])
    drum_track.append(mido.Message("note_on", channel=9, note=36, velocity=100, time=0))
    drum_track.append(mido.Message("note_on", channel=9, note=42, velocity=85, time=0))
    drum_track.append(mido.Message("note_off", channel=9, note=36, velocity=0, time=96))
    drum_track.append(mido.Message("note_off", channel=9, note=42, velocity=0, time=0))
    melody_track.append(mido.Message("note_on", channel=0, note=60, velocity=100, time=0))
    melody_track.append(mido.Message("note_off", channel=0, note=60, velocity=0, time=480))
    midi.save(source)

    result = import_song(source, tmp_path / "Drums", instrument_mode="drums")
    timeline = load_timeline(result.path)

    assert timeline.tokens == [["a", "h"]]
    assert timeline.meta["drum_import_mode"] == "original"
    assert timeline.meta["instrument_mode"] == "drums"


# -- by Larsiopuw
