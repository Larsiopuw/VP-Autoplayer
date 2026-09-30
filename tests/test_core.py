from pathlib import Path
from queue import Queue
import random

import pytest

import autoplayer_core as core
from autoplayer_core import CFG, Player, discover_sheets, tokenise


@pytest.fixture(autouse=True)
def keep_playback_tests_independent_of_foreground_window(monkeypatch) -> None:
    monkeypatch.setattr(CFG, "auto_pause", False)


def test_tokeniser_ignores_metadata_and_reads_chords() -> None:
    text = '#META {"bpm": 120}\n# comment\n[t Y] | a\n'
    assert list(tokenise(text)) == [["t", "Y"], "|", "a"]


def test_tokeniser_skips_whitespace() -> None:
    assert list(tokenise("  1 2\n\n3")) == ["1", "2", "3"]


def test_tokeniser_keeps_drum_percussion_key_inside_chords() -> None:
    assert list(tokenise("[h s ;] a")) == [["h", "s", ";"], "a"]


def test_player_can_start_from_seek_position(tmp_path: Path, monkeypatch) -> None:
    sheet = tmp_path / "seek.txt"
    sheet.write_text("a b c d", encoding="utf-8")
    messages: Queue = Queue()
    played: list[str | list[str]] = []
    player = Player(sheet, messages, start_index=2)
    monkeypatch.setattr(player, "_wait_ready", lambda: True)
    monkeypatch.setattr(
        player, "_play_token",
        lambda token, _duration, _strength, _hold=None: played.append(token),
    )

    player.run()

    assert played == ["c", "d"]
    queued = list(messages.queue)
    assert ("progress", 2) in queued
    assert ("progress", 4) in queued


def test_discovery_includes_legacy_extensionless_sheets(tmp_path: Path) -> None:
    legacy = tmp_path / "Runaway - Kanye West"
    legacy.write_text('#META {"bpm": 160}\nl | l | l', encoding="utf-8")
    (tmp_path / "normal.txt").write_text("a b c", encoding="utf-8")
    (tmp_path / "ignore.mid").write_bytes(b"not a sheet")

    assert [path.name for path in discover_sheets(tmp_path)] == [
        "normal.txt", "Runaway - Kanye West",
    ]


def test_library_migration_separates_existing_songs_without_overwrite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(core, "DATA_DIR", tmp_path / "data")
    piano = tmp_path / "Song.txt"
    piano.write_text('a b c', encoding="utf-8")
    drums = tmp_path / "Beat.txt"
    drums.write_text('#META {"instrument_mode": "drums"}\nh s a', encoding="utf-8")
    existing = core.mode_sheet_folder(tmp_path, "piano") / "Song.txt"
    existing.parent.mkdir()
    existing.write_text("existing", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("# Only a comment", encoding="utf-8")

    moved = core.ensure_library_layout(tmp_path)

    assert moved[piano].name == "Song (2).txt"
    assert moved[piano].read_text(encoding="utf-8") == "a b c"
    assert existing.read_text(encoding="utf-8") == "existing"
    assert moved[drums].parent == tmp_path / "Drums"
    assert (tmp_path / "notes.txt").exists()
    assert len(list((core.DATA_DIR / "Backups").glob("Library-Migration-*.zip"))) == 1
    assert core.ensure_library_layout(tmp_path) == {}


def test_player_uses_per_token_dynamic_timing(tmp_path: Path, monkeypatch) -> None:
    sheet = tmp_path / "dynamic.txt"
    sheet.write_text(
        '#META {"dynamic_timing": true, "timings": [0.12, 0.4], "holds": [0.08, 0.25]}\n'
        'a b\n',
        encoding="utf-8",
    )
    captured: list[tuple[float, float | None]] = []
    player = Player(sheet, Queue())
    monkeypatch.setattr(CFG, "human", 0.0)
    monkeypatch.setattr(player, "_wait_ready", lambda: True)
    monkeypatch.setattr(
        player, "_play_dynamic_token",
        lambda _token, duration, _strength, hold, _deadlines:
            captured.append((duration, hold)) or True,
    )

    player.run()

    assert [(round(duration, 3), hold) for duration, hold in captured] == [
        (0.12, 0.08), (0.4, 0.25),
    ]


def test_shift_is_released_immediately_after_shifted_note_press(monkeypatch) -> None:
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(core.CFG, "instrument_mode", "piano")

    class Keyboard:
        def press(self, key) -> None:
            events.append(("press", key))

        def release(self, key) -> None:
            events.append(("release", key))

    monkeypatch.setattr(core, "KEYBOARD", Keyboard())
    core.press("T")
    core.release("T")

    assert events == [
        ("press", core.Key.shift),
        ("press", "t"),
        ("release", core.Key.shift),
        ("release", "t"),
    ]


def test_drum_mode_uses_literal_game_keys(monkeypatch) -> None:
    monkeypatch.setattr(core.CFG, "instrument_mode", "drums")
    monkeypatch.setattr(core.CFG, "key_layout", core.CANONICAL_LAYOUT[::-1])
    monkeypatch.setattr(core.CFG, "drum_keymap", core.DEFAULT_DRUM_KEYMAP.copy())
    assert core.translate_note("h") == "h"
    assert core.translate_note(";") == ";"


def test_drum_pad_can_use_game_specific_space_key(monkeypatch) -> None:
    events = []

    class Keyboard:
        def press(self, key) -> None:
            events.append(("down", key))

        def release(self, key) -> None:
            events.append(("up", key))

    monkeypatch.setattr(core.CFG, "instrument_mode", "drums")
    monkeypatch.setattr(core.CFG, "drum_keymap", {**core.DEFAULT_DRUM_KEYMAP, "h": "space"})
    monkeypatch.setattr(core, "KEYBOARD", Keyboard())
    core.press("h")
    core.release("h")
    assert events == [("down", core.Key.space), ("up", core.Key.space)]


def test_dynamic_wait_releases_held_keys_when_paused(monkeypatch, tmp_path: Path) -> None:
    player = Player(tmp_path / "unused.txt", Queue())
    player.resume_event.clear()
    released = []
    deadlines = {"a": 999999.0}
    monkeypatch.setattr(player, "_release_all", lambda: released.append(True))
    monkeypatch.setattr(player, "_wait_ready", lambda: player.resume_event.set() or True)
    assert player._sleep_with_releases(0.0, deadlines)
    assert released == [True]
    assert deadlines == {}


def test_player_releases_original_key_after_profile_changes(monkeypatch, tmp_path: Path) -> None:
    events = []

    class Keyboard:
        def press(self, key) -> None:
            events.append(("down", key))

        def release(self, key) -> None:
            events.append(("up", key))

    monkeypatch.setattr(core, "KEYBOARD", Keyboard())
    monkeypatch.setattr(CFG, "instrument_mode", "drums")
    monkeypatch.setattr(CFG, "drum_keymap", {**core.DEFAULT_DRUM_KEYMAP, "h": "space"})
    player = Player(tmp_path / "unused.txt", Queue())
    player._key_down("h")
    CFG.drum_keymap = {**core.DEFAULT_DRUM_KEYMAP, "h": "q"}
    player._key_up("h")
    assert events == [("down", core.Key.space), ("up", core.Key.space)]


def test_dynamic_practice_loop_applies_speed(tmp_path: Path, monkeypatch) -> None:
    sheet = tmp_path / "loop.txt"
    sheet.write_text(
        '#META {"dynamic_timing": true, "timings": [0.12, 0.4], "holds": [0.08, 0.25]}\n'
        'a b\n', encoding="utf-8",
    )
    captured: list[float] = []
    player = Player(sheet, Queue(), start_index=0, end_index=2, speed=2.0, loop_count=2)
    monkeypatch.setattr(CFG, "human", 0.0)
    monkeypatch.setattr(CFG, "input_interval", 0.01)
    monkeypatch.setattr(player, "_wait_ready", lambda: True)
    monkeypatch.setattr(player, "_release_all", lambda: None)
    monkeypatch.setattr(
        player, "_play_dynamic_token",
        lambda _token, duration, _strength, _hold, _deadlines: captured.append(duration) or True,
    )

    player.run()

    assert captured == [0.06, 0.2, 0.06, 0.2]


def test_humanize_changes_dynamic_onsets_without_changing_total_time() -> None:
    original = [0.22] * 12
    exact = core.humanize_timing_steps(original, 0.0, 0.018, random.Random(7))
    human = core.humanize_timing_steps(original, 1.0, 0.018, random.Random(7))

    assert exact == original
    assert max(abs(a - b) for a, b in zip(human, original)) > 0.01
    assert min(human) >= 0.018
    assert abs(sum(human) - sum(original)) < 1e-9


def test_player_applies_humanize_to_dynamic_sheet(tmp_path: Path, monkeypatch) -> None:
    sheet = tmp_path / "human.txt"
    sheet.write_text(
        '#META {"dynamic_timing": true, "timings": [0.22, 0.22, 0.22, 0.22], '
        '"holds": [0.1, 0.1, 0.1, 0.1]}\na b c d\n', encoding="utf-8",
    )
    player = Player(sheet, Queue())
    captured = []
    monkeypatch.setattr(CFG, "human", 1.0)
    monkeypatch.setattr(CFG, "input_interval", 0.018)
    monkeypatch.setattr(player, "_wait_ready", lambda: True)
    monkeypatch.setattr(player, "_play_dynamic_token", lambda _token, duration, *_: captured.append(duration) or True)
    monkeypatch.setattr(core.random, "gauss", lambda *_: 0.014)

    player.run()

    assert len(captured) == 4
    assert any(abs(duration - 0.22) > 0.005 for duration in captured)
    assert abs(sum(captured) - 0.88) < 1e-9


def test_playback_delivers_balanced_key_events_for_chords_and_repeats(
    tmp_path: Path, monkeypatch,
) -> None:
    sheet = tmp_path / "delivery.txt"
    sheet.write_text("a [s d] a", encoding="utf-8")
    events: list[tuple[str, object]] = []

    class RecordingKeyboard:
        def press(self, key: object) -> None:
            events.append(("down", key))

        def release(self, key: object) -> None:
            events.append(("up", key))

    monkeypatch.setattr(core, "KEYBOARD", RecordingKeyboard())
    monkeypatch.setattr(CFG, "instrument_mode", "drums")
    monkeypatch.setattr(CFG, "drum_keymap", core.DEFAULT_DRUM_KEYMAP.copy())
    monkeypatch.setattr(CFG, "human", 0.0)
    monkeypatch.setattr(CFG, "bpm", 240)
    monkeypatch.setattr(CFG, "subdiv", 16.0)
    monkeypatch.setattr(CFG, "hold", 0.5)
    monkeypatch.setattr(CFG, "input_interval", 0.001)

    player = Player(sheet, Queue())
    monkeypatch.setattr(player, "_wait_ready", lambda: True)
    player.run()

    assert events == [
        ("down", "a"), ("up", "a"),
        ("down", "s"), ("down", "d"), ("up", "d"), ("up", "s"),
        ("down", "a"), ("up", "a"),
    ]
    assert player._held == []

# -- by Larsiopuw
