import json
from urllib.error import HTTPError, URLError

import pytest
import mido

from midi_hub import HubItem, download_midi, hub_error_message, parse_catalog
from midi_preview import MidiPreviewPlayer


def test_catalog_rejects_unsafe_filenames() -> None:
    data = json.dumps([
        {"id": 1, "name": "Okay", "midiFilename": "song.mid", "downloads": 10},
        {"id": 2, "name": "Bad", "midiFilename": "../secret.mid"},
        {"id": 3, "name": "Bad", "midiFilename": "folder\\secret.mid"},
        {"id": 4, "name": "Bad", "midiFilename": "song.exe"},
    ]).encode()
    items = parse_catalog(data)
    assert len(items) == 1
    assert items[0].url.endswith("song.mid")


def test_download_only_accepts_midi_header(tmp_path, monkeypatch) -> None:
    item = HubItem(1, "Test", "", "", "", "test.mid", 0)
    target = tmp_path / "test.mid"
    monkeypatch.setattr("midi_hub._limited_response", lambda *_: b"not midi")
    with pytest.raises(ValueError, match="MIDI"):
        download_midi(item, target)
    assert not target.exists()
    monkeypatch.setattr("midi_hub._limited_response", lambda *_: b"MThd\x00\x00")
    assert download_midi(item, target) == target
    assert target.read_bytes().startswith(b"MThd")


def test_hub_preview_plays_original_midi_without_import(tmp_path) -> None:
    source = tmp_path / "demo.mid"
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    midi.save(source)
    player = MidiPreviewPlayer()
    commands: list[str] = []

    class RecordingWinMM:
        def mciSendStringW(self, command, _buffer, _length, _callback) -> int:
            commands.append(command)
            return 0

    player.winmm = RecordingWinMM()
    player.play(source)
    assert player.opened
    player.stop()
    assert not player.opened
    assert commands == [
        f'open "{source}" type sequencer alias {player.alias}',
        f"play {player.alias} from 0",
        f"close {player.alias}",
    ]
    assert not (tmp_path / "Piano").exists()


def test_hub_errors_are_actionable_without_raw_http_text() -> None:
    missing = HTTPError("https://api.nanomidi.net/missing.mid", 404, "Not Found", None, None)
    message = hub_error_message(missing)
    assert "nicht verfügbar" in message
    assert "HTTP" not in message
    assert "404" not in message
    assert "Internetverbindung" in hub_error_message(URLError("offline"))


# -- by Larsiopuw
