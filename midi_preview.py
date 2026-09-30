"""Play a downloaded MIDI with the Windows General MIDI synthesizer."""

from __future__ import annotations

import ctypes
from pathlib import Path


class MidiPreviewPlayer:
    """Small MCI sequencer wrapper; each dialog owns and closes its device."""

    def __init__(self) -> None:
        self.alias = f"vp_hub_{id(self):x}"
        self.opened = False
        self.winmm = ctypes.WinDLL("winmm")
        self.winmm.mciSendStringW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        self.winmm.mciSendStringW.restype = ctypes.c_uint
        self.winmm.mciGetErrorStringW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
        self.winmm.mciGetErrorStringW.restype = ctypes.c_bool

    def _command(self, command: str) -> None:
        result = self.winmm.mciSendStringW(command, None, 0, None)
        if result:
            detail = ctypes.create_unicode_buffer(256)
            self.winmm.mciGetErrorStringW(result, detail, len(detail))
            raise OSError(f"Windows MIDI: {detail.value or result}")

    def play(self, source: Path) -> None:
        self.stop()
        if not source.is_file() or '"' in str(source):
            raise OSError("Ungültiger MIDI-Dateipfad.")
        self._command(f'open "{source}" type sequencer alias {self.alias}')
        self.opened = True
        try:
            self._command(f"play {self.alias} from 0")
        except OSError:
            self.stop()
            raise

    def stop(self) -> None:
        if self.opened:
            self.winmm.mciSendStringW(f"close {self.alias}", None, 0, None)
            self.opened = False


# -- by Larsiopuw
