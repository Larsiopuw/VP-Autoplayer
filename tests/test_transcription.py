from pathlib import Path

import mido

from transcription import (
    HIGHEST_MIDI,
    LOWEST_MIDI,
    NoteEvent,
    PIANO_KEYS,
    arrange_pitched_events_as_drums,
    available_output_path,
    choose_automatic_profile,
    events_to_tokens,
    events_to_timed_tokens,
    drum_events_to_timed_tokens,
    estimate_key,
    fit_pitch,
    format_sheet,
    infer_subdivision,
    gm_drum_key,
    optimise_extreme_midi,
    read_midi,
    read_drum_midi,
    safe_stem,
)


def test_virtual_piano_map_covers_c2_to_c7() -> None:
    assert len(PIANO_KEYS) == 61
    assert LOWEST_MIDI == 36
    assert HIGHEST_MIDI == 96
    assert PIANO_KEYS[60 - LOWEST_MIDI] == "t"


def test_quantisation_creates_chords_and_rests() -> None:
    events = [
        NoteEvent(0.0, 0.2, 60),
        NoteEvent(0.01, 0.2, 64),
        NoteEvent(0.50, 0.7, 67),
    ]
    tokens, folded = events_to_tokens(events, bpm=120, subdiv=4)
    assert tokens == ["[t u]", "|", "|", "|", "o"]
    assert folded == 0


def test_dynamic_timing_preserves_variable_onset_gaps() -> None:
    events = [
        NoteEvent(0.0, 0.18, 60),
        NoteEvent(0.20, 0.38, 62),
        NoteEvent(0.55, 0.80, 64),
        NoteEvent(1.20, 1.45, 67),
    ]
    tokens, timings, holds, folded = events_to_timed_tokens(events)

    assert tokens == ["t", "y", "u", "o"]
    assert [round(value, 3) for value in timings[:3]] == [0.2, 0.35, 0.65]
    assert [round(value, 3) for value in holds] == [0.18, 0.18, 0.25, 0.25]
    assert folded == 0


def test_dynamic_timing_keeps_fast_repeated_notes_separate() -> None:
    events = [
        NoteEvent(0.000, 0.080, 60, 100),
        NoteEvent(0.055, 0.130, 60, 98),
        NoteEvent(0.110, 0.180, 60, 96),
        NoteEvent(0.112, 0.190, 64, 90),
    ]

    tokens, timings, _holds, _folded = events_to_timed_tokens(events)

    assert len(tokens) == 3
    assert tokens[-1].startswith("[")
    assert timings[:2] == [0.055, 0.055]


def test_chord_does_not_use_same_physical_key_twice() -> None:
    lower_pitch = LOWEST_MIDI + PIANO_KEYS.index("t")
    upper_pitch = LOWEST_MIDI + PIANO_KEYS.index("T")
    events = [
        NoteEvent(0.0, 0.2, lower_pitch, 100),
        NoteEvent(0.0, 0.2, upper_pitch, 90),
    ]

    tokens, _timings, _holds, _folded = events_to_timed_tokens(events)

    assert tokens == ["t"]


def test_key_estimation_uses_weighted_note_durations() -> None:
    events = [NoteEvent(0, 2, 60, 100), NoteEvent(0, 0.2, 67, 50)]
    assert estimate_key(events) == "C"


def test_out_of_range_notes_can_be_octave_fitted() -> None:
    assert fit_pitch(24, fold_octaves=False) == (None, False)
    assert fit_pitch(24, fold_octaves=True) == (36, True)
    assert fit_pitch(108, fold_octaves=True) == (96, True)


def test_sheet_contains_metadata_and_wrapped_tokens() -> None:
    text = format_sheet(["t"] * 17, bpm=100, subdiv=2.5, source="demo.mid")
    lines = text.splitlines()
    assert lines[0].startswith("#META ")
    assert '"bpm": 100' in lines[0]
    assert len(lines) == 3


def test_safe_filename_removes_windows_reserved_characters() -> None:
    assert safe_stem('A<bad>:song?.mid') == "Abadsong.mid"


def test_import_output_never_overwrites_existing_sheet(tmp_path: Path) -> None:
    (tmp_path / "Song.txt").write_text("existing", encoding="utf-8")
    assert available_output_path(tmp_path, "Song").name == "Song (2).txt"


def test_midi_reader_uses_tempo_and_note_times(tmp_path: Path) -> None:
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(90), time=0))
    track.append(mido.Message("note_on", note=60, velocity=80, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    path = tmp_path / "tempo.mid"
    midi.save(path)

    events, bpm = read_midi(path)
    assert bpm == 90
    assert len(events) == 1
    assert events[0].pitch == 60
    assert round(events[0].end, 3) == round(60 / 90, 3)


def test_grid_is_inferred_from_eighth_note_onsets() -> None:
    events = [NoteEvent(index * 0.25, index * 0.25 + 0.15, 60) for index in range(8)]
    assert infer_subdivision(events, bpm=120) == 2.0


def test_midi_reader_ignores_percussion_channel(tmp_path: Path) -> None:
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.Message("note_on", channel=9, note=36, velocity=100, time=0))
    track.append(mido.Message("note_off", channel=9, note=36, velocity=0, time=120))
    track.append(mido.Message("note_on", channel=0, note=60, velocity=80, time=0))
    track.append(mido.Message("note_off", channel=0, note=60, velocity=0, time=480))
    path = tmp_path / "with-drums.mid"
    midi.save(path)

    events, _bpm = read_midi(path)
    assert [event.pitch for event in events] == [60]


def test_automatic_profile_detects_fast_music() -> None:
    events = [
        NoteEvent(index * 0.05, index * 0.05 + 0.04, 60 + index % 5, 90)
        for index in range(30)
    ]
    assert choose_automatic_profile(events) == "Sehr schnell"


def test_automatic_profile_detects_monophonic_melody() -> None:
    events = [NoteEvent(index * 0.35, index * 0.35 + 0.2, 60 + index % 4, 90) for index in range(8)]
    assert choose_automatic_profile(events) == "Melodie"


def test_midi_reader_keeps_intentional_micro_notes(tmp_path: Path) -> None:
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.Message("note_on", note=60, velocity=100, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=12))
    track.append(mido.Message("note_on", note=60, velocity=100, time=12))
    track.append(mido.Message("note_off", note=60, velocity=0, time=12))
    path = tmp_path / "micro.mid"
    midi.save(path)

    events, _bpm = read_midi(path)
    assert len(events) == 2


def test_normal_midi_is_not_simplified() -> None:
    events = [NoteEvent(index * 0.1, index * 0.1 + 0.08, 60 + index % 5, 90) for index in range(100)]
    reduced, details = optimise_extreme_midi(events)
    assert reduced == events
    assert details["optimized"] is False


def test_black_midi_is_reduced_to_playable_steps() -> None:
    events = [
        NoteEvent(step * 0.002, step * 0.002 + 0.05, pitch, 100)
        for step in range(1_000)
        for pitch in range(48, 61)
    ]
    reduced, details = optimise_extreme_midi(events)
    tokens, timings, _holds, _folded = events_to_timed_tokens(
        reduced, fold_octaves=False, minimum_step=0.018, chord_window=0.003, max_chord_notes=6,
    )
    assert details["optimized"] is True
    assert len(reduced) < len(events)
    assert all(value >= 0.018 for value in timings)
    assert all(len(token.strip("[]").split()) <= 6 for token in tokens if token.startswith("["))


def test_general_midi_drums_match_game_keyboard() -> None:
    assert gm_drum_key(36) == "h"
    assert gm_drum_key(38) == "s"
    assert gm_drum_key(39) == "s"
    assert gm_drum_key(42) == "a"
    assert gm_drum_key(49) == "j"
    assert gm_drum_key(51) == "k"


def test_drum_events_preserve_simultaneous_hits() -> None:
    events = [
        NoteEvent(0.0, 0.06, 36, 110),
        NoteEvent(0.0, 0.06, 42, 90),
        NoteEvent(0.25, 0.31, 38, 100),
    ]
    tokens, timings, holds = drum_events_to_timed_tokens(events)
    assert tokens == ["[a h]", "s"]
    assert timings[0] == 0.25
    assert holds == [0.045, 0.045]


def test_drum_events_collapse_velocity_layers_but_keep_real_repeats() -> None:
    events = [
        NoteEvent(0.000, 0.04, 36, 59),
        NoteEvent(0.015, 0.06, 36, 59),
        NoteEvent(0.030, 0.08, 36, 119),
        NoteEvent(0.360, 0.41, 36, 80),
        NoteEvent(0.720, 0.77, 39, 27),
        NoteEvent(0.758, 0.81, 39, 87),
        NoteEvent(0.796, 0.85, 39, 127),
    ]

    tokens, timings, _holds = drum_events_to_timed_tokens(events)

    assert tokens == ["h", "h", "s"]
    assert [round(value, 3) for value in timings[:2]] == [0.36, 0.36]


def test_full_song_drum_arrangement_adds_playable_melodic_pads() -> None:
    events = [
        NoteEvent(0.00, 0.20, 48, 95),
        NoteEvent(0.00, 0.20, 60, 105),
        NoteEvent(0.25, 0.45, 64, 100),
        NoteEvent(0.50, 0.70, 67, 110),
        NoteEvent(0.75, 0.95, 69, 100),
    ]

    arranged = arrange_pitched_events_as_drums(events, bpm=120)
    mapped = {gm_drum_key(event.pitch) for event in arranged}

    assert "h" in mapped
    assert len(mapped - {"h", "s"}) >= 2
    assert len(arranged) <= 8


def test_drum_midi_reads_only_general_midi_percussion_channel(tmp_path: Path) -> None:
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.Message("note_on", channel=0, note=60, velocity=100, time=0))
    track.append(mido.Message("note_off", channel=0, note=60, velocity=0, time=120))
    track.append(mido.Message("note_on", channel=9, note=36, velocity=110, time=0))
    track.append(mido.Message("note_off", channel=9, note=36, velocity=0, time=120))
    path = tmp_path / "drums.mid"
    midi.save(path)
    events, _bpm = read_drum_midi(path)
    assert [event.pitch for event in events] == [36]

# -- by Larsiopuw
