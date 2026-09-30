from pathlib import Path
import wave

import autoplayer_core as core
from studio_features import (
    InstrumentProfile, ProfileStore, SheetTimeline, analyze_quality, create_backup,
    load_timeline, render_preview, replace_section, restore_backup, save_timeline, trash_song,
)


def make_sheet(path: Path, tokens=None, timings=None) -> Path:
    timeline = SheetTimeline(
        tokens or ["t", ["y", "u"], "o"],
        timings or [0.2, 0.3, 0.4],
        [0.15, 0.25, 0.3],
        {"bpm": 120, "subdiv": 4, "dynamic_timing": True},
    )
    save_timeline(path, timeline)
    return path


def test_profile_store_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    store.put(InstrumentProfile(
        "Testspiel", input_interval=0.031, max_chord=3,
        drum_keymap={**core.DEFAULT_DRUM_KEYMAP, "h": "space"},
    ))

    loaded = ProfileStore(path)

    assert loaded.profiles["Testspiel"].input_interval == 0.031
    assert loaded.profiles["Testspiel"].max_chord == 3
    assert loaded.profiles["Testspiel"].drum_keymap["h"] == "space"


def test_profile_activation_applies_drum_mapping(tmp_path: Path, monkeypatch) -> None:
    store = ProfileStore(tmp_path / "profiles.json")
    store.put(InstrumentProfile(
        "Other kit", drum_keymap={**core.DEFAULT_DRUM_KEYMAP, "s": "q"},
    ))
    monkeypatch.setattr(core, "save_cfg", lambda: None)
    for field in ("active_profile", "key_layout", "input_interval", "retrigger_gap", "max_chord", "drum_keymap"):
        monkeypatch.setattr(core.CFG, field, getattr(core.CFG, field))
    store.activate("Other kit")
    assert core.CFG.drum_keymap["s"] == "q"


def test_timeline_roundtrip_and_preview(tmp_path: Path) -> None:
    sheet = make_sheet(tmp_path / "song.txt")
    timeline = load_timeline(sheet)
    preview = render_preview(sheet, tmp_path / "preview.wav")

    assert timeline.duration == 0.9
    with wave.open(str(preview)) as audio:
        assert audio.getframerate() == 22050
        assert audio.getnframes() > 1000


def test_quality_report_uses_game_profile_limits(tmp_path: Path) -> None:
    sheet = make_sheet(tmp_path / "dense.txt", [["t", "y", "u"]], [0.015])
    issues = analyze_quality(sheet, InstrumentProfile("Slow", input_interval=0.04, max_chord=2))

    assert {category for category, _detail in issues} >= {"Tempo", "Akkorde"}


def test_backup_restore_and_section_replacement(tmp_path: Path) -> None:
    source_folder = tmp_path / "library"
    source_folder.mkdir()
    target = make_sheet(source_folder / "target.txt", ["t", "y", "u"], [1.0, 1.0, 1.0])
    patch = make_sheet(tmp_path / "patch.txt", ["o", "p"], [0.2, 0.3])
    replace_section(target, patch, 1.0, 2.0)
    assert load_timeline(target).tokens == ["t", "o", "p", "u"]

    archive = create_backup(source_folder, tmp_path / "backup.zip")
    restored_folder = tmp_path / "restored"
    assert restore_backup(archive, restored_folder) == 1
    assert (restored_folder / "target.txt").exists()


def test_trash_keeps_two_songs_deleted_in_the_same_second(tmp_path: Path) -> None:
    song = tmp_path / "song.txt"
    song.write_text("first", encoding="utf-8")
    first = trash_song(song)
    song.write_text("second", encoding="utf-8")
    second = trash_song(song)

    assert first != second
    assert first.read_text(encoding="utf-8") == "first"
    assert second.read_text(encoding="utf-8") == "second"


# -- by Larsiopuw
