"""Opt-in catalog access for nanoMIDI; songs are never bundled or preloaded."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


CATALOG_URL = "https://api.nanomidi.net/api/midiData"
MIDI_BASE_URL = "https://api.nanomidi.net/api/midis/"
MAX_CATALOG_BYTES = 3_000_000
MAX_MIDI_BYTES = 12_000_000


def hub_error_message(error: Exception, action: str = "Vorschau") -> str:
    """Translate transport and format failures into short, actionable UI text."""
    if isinstance(error, HTTPError):
        if error.code in (404, 410):
            if action == "Katalog":
                return "Der MIDI-Hub-Katalog ist derzeit nicht verfügbar. Bitte versuche es später erneut."
            return f"{action} nicht verfügbar: Die MIDI-Datei fehlt derzeit im Hub. Wähle einen anderen Song."
        if error.code == 429:
            return "Der MIDI-Hub ist gerade ausgelastet. Bitte versuche es später erneut."
        return "Der MIDI-Hub antwortet momentan nicht. Bitte versuche es später erneut."
    if isinstance(error, (URLError, TimeoutError, ConnectionError)):
        return "Verbindung zum MIDI-Hub fehlgeschlagen. Prüfe deine Internetverbindung und versuche es erneut."
    if isinstance(error, ValueError):
        if action == "Katalog":
            return "Der MIDI-Hub-Katalog konnte nicht gelesen werden. Bitte versuche es später erneut."
        if action == "Import":
            return "Import nicht möglich: Die MIDI-Datei konnte nicht verarbeitet werden."
        return f"{action} nicht verfügbar: Die MIDI-Datei konnte nicht sicher geladen werden."
    return f"{action} derzeit nicht möglich. Bitte versuche einen anderen Song."


@dataclass(frozen=True)
class HubItem:
    identifier: int
    title: str
    artists: str
    arranger: str
    uploader: str
    filename: str
    downloads: int

    @property
    def url(self) -> str:
        return MIDI_BASE_URL + quote(self.filename, safe="")


def _limited_response(url: str, limit: int) -> bytes:
    request = Request(url, headers={"User-Agent": "VP-Autoplayer/1.0", "Accept": "application/json, audio/midi"})
    with urlopen(request, timeout=15) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or final.hostname != "api.nanomidi.net":
            raise ValueError("Der MIDI-Hub hat auf eine unerwartete Adresse weitergeleitet.")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Die Antwort ist größer als das erlaubte Limit.")
    return data


def parse_catalog(data: bytes) -> list[HubItem]:
    raw = json.loads(data)
    if not isinstance(raw, list):
        raise ValueError("Ungültiger MIDI-Katalog.")
    items: list[HubItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        filename = str(entry.get("midiFilename") or "")
        if not filename or filename != Path(filename).name or "\\" in filename:
            continue
        if Path(filename).suffix.casefold() not in {".mid", ".midi"}:
            continue
        try:
            identifier = int(entry["id"])
            downloads = max(0, int(entry.get("downloads") or 0))
        except (KeyError, TypeError, ValueError):
            continue
        items.append(HubItem(
            identifier=identifier,
            title=str(entry.get("name") or filename)[:160],
            artists=str(entry.get("artists") or "")[:160],
            arranger=str(entry.get("arranger") or "")[:160],
            uploader=str(entry.get("uploader") or "")[:160],
            filename=filename,
            downloads=downloads,
        ))
    return items


def fetch_catalog() -> list[HubItem]:
    return parse_catalog(_limited_response(CATALOG_URL, MAX_CATALOG_BYTES))


def download_midi(item: HubItem, target: Path) -> Path:
    """Download exactly one selected MIDI, with a size and format check."""
    data = _limited_response(item.url, MAX_MIDI_BYTES)
    if not data.startswith(b"MThd"):
        raise ValueError("Der Download enthält keine gültige MIDI-Datei.")
    target.write_bytes(data)
    return target


# -- by Larsiopuw
