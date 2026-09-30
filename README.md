# VP Autoplayer

Windows-Desktop-App für virtuelle Instrumente in Spielen · Piano und Drums ·
lokaler Audio-/MIDI-Import

[![Windows verification](https://github.com/Larsiopuw/VP-Autoplayer/actions/workflows/windows-ci.yml/badge.svg)](https://github.com/Larsiopuw/VP-Autoplayer/actions/workflows/windows-ci.yml)
[![MIT](https://img.shields.io/badge/Lizenz-MIT-blue.svg)](LICENSE)

Ein moderner, universeller Windows-Autoplayer für virtuelle Klaviere und andere
Instrumente in Spielen. Die Anwendung ist nicht an ein bestimmtes Spiel
gebunden: Tastenlayout, Eingabegeschwindigkeit und Akkordgrenzen lassen sich pro
Spiel oder Instrument als eigenes Profil speichern.

> Automatisierte Eingaben können gegen Regeln eines Spiels oder Servers
> verstoßen. Verwende die App nur dort, wo es erlaubt ist.

## Funktionen

- native, rahmenlose Qt-Oberfläche mit Dark- und Light-Mode
- universelle Spiel- und Instrumentprofile mit frei definierbarem 61-Tasten-Layout
- geführte Kalibrierung der sicheren Eingabegeschwindigkeit im Zielfenster
- Audio-Import für MP3, WAV, FLAC, OGG und M4A
- MIDI-Import inklusive Tempoänderungen
- MIDI-Hub mit Suche und Original-MIDI-Vorschau vor dem Import (Internet nötig)
- vier Erkennungsprofile: Melodie, Ausgewogen, Vollständig und Sehr schnell
- dynamische Anschlagszeiten, gehaltene Noten und schnelle Wiederholungen
- abschnittsweises Neu-Erkennen und Ersetzen eines vorhandenen Songs
- Qualitätsprüfung für Timing, Akkorde, Tonumfang und Tastenbelegung
- lokale Klangvorschau ohne geöffnetes Spiel
- Piano-Roll-Editor mit Transposition, Timing, Löschen und Undo
- Übungsmodus mit Start, Ende, Geschwindigkeit und Wiederholungen
- Favoriten, Tags/Playlists, Suche und mehrere Sortierungen
- Drag-and-drop für Audio- und MIDI-Dateien
- Papierkorb statt endgültigem Löschen
- manuelle ZIP-Sicherungen und tägliche automatische Backups
- songbezogene Presets für Humanize, Tastendauer, Tempo und Spielprofil

## Daten

Die fertige EXE speichert alles unter `%LOCALAPPDATA%\VP Autoplayer`:

- `Sheets\Piano` – Piano-Songs
- `Sheets\Drums` – Drum-Songs
- `Sheets\Piano\.Trash` und `Sheets\Drums\.Trash` – gelöschte Songs
- `Backups` – tägliche Sicherungen und einmaliger Snapshot vor der Ordner-Migration
- `instrument_profiles.json` – Spiel- und Instrumentprofile
- `vp_autoplayer.json` – globale Einstellungen

## Installation aus dem Quellcode

Für die vollständige lokale Audioerkennung wird Python 3.11 verwendet:

```powershell
py -3.11 -m venv .venv311
.venv311\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python gui.py
```

Voraussetzung: Windows 10/11 mit Python 3.11. Für den Quellcode-Start ist
keine EXE nötig. Eine geprüfte Windows-Binärdatei wird künftig über
[Releases](https://github.com/Larsiopuw/VP-Autoplayer/releases) angeboten;
ein CI-Artefakt ist lediglich ein unsignierter Test-Build.

Die Audioanalyse läuft vollständig lokal. Es werden keine Musikdateien
hochgeladen. Der MIDI-Hub lädt seinen Katalog und eine ausgewählte MIDI-Datei
von nanoMIDI.net; nicht jeder Katalogeintrag ist jederzeit verfügbar.

## Bedienung

1. Bibliotheksordner und Zielfenster auswählen.
2. Im Instrument Studio ein passendes Profil aktivieren oder kalibrieren.
3. Einen vorhandenen Song wählen oder Audio/MIDI hineinziehen.
4. Den Import im gewünschten Qualitätsprofil durchführen.
5. Das Ergebnis lokal vorhören und bei Bedarf in der Piano-Roll korrigieren.
6. Direkt abspielen oder einen Übungsbereich als Loop starten.

Die Ordnerauswahl zeigt die Dateien im jeweiligen Verzeichnis zur Orientierung.
Mit „Diesen Ordner auswählen“ wird der aktuell geöffnete Ordner zur Bibliothek.
„Bibliotheksordner öffnen“ zeigt den Piano- oder Drum-Unterordner im Windows-Explorer. Wenn du
Dateien dort löschst oder hinzufügst, aktualisiere die Liste über den Refresh-Knopf.
Über das Rechtsklickmenü verschobene Songs liegen wiederherstellbar im
`.Trash`-Ordner des jeweiligen Instruments.

Humanize verändert beim Abspielen im Zielfenster die Einsatzzeitpunkte und
Tastendauern kontrolliert. Bei sehr schnellen Noten wird die Abweichung begrenzt.
Die Original-MIDI-Vorschau im Hub bleibt unverändert. Reine Tastatureingaben
können keine MIDI-Anschlagstärke an ein Spiel übertragen; ob Tastendauer hörbar
ist, hängt vom Instrument im Spiel ab.

## Sheet-Format

```text
#META {"bpm": 100, "dynamic_timing": true, "timings": [0.2, 0.25]}
[A F G] r r u o | s d f
```

- einzelne Zeichen sind Noten
- Zeichen in eckigen Klammern sind Akkorde
- `|` ist eine Pause
- `#META` speichert Timing, Presets, Quelle, Tags und Bearbeitungsinformationen

## Tests und Build

```powershell
python -m pytest -q
pyinstaller gui.spec
```

Der Windows-Workflow führt Tests, Syntaxprüfung, EXE-Build und einen
Studio-Smoke-Test aus. Lokal lässt sich derselbe Ablauf mit
`./scripts/verify-release.ps1 -Python ./.venv311/Scripts/python.exe -Build`
starten. Die EXE enthält Basic Pitch, TensorFlow und FFmpeg, damit die
Audioerkennung ohne zusätzliche Installation funktioniert.

Der derzeitige Stand ist ein öffentlicher Quellcode-Release, **noch keine
signierte Produktionsfreigabe**. Vor einem stabilen Binär-Release sind reale
Key-down-/Key-up-Messungen in benannten Zielspielen, lizenzfreie Audio-/MIDI-
Referenzfälle mit gemessener Qualität und eine Publisher-Signatur erforderlich.
Automatische Tests mit aufgezeichnetem Keyboard prüfen bereits, dass beim
Abspielen von Akkorden und Wiederholungen keine Tasten gehalten bleiben. Sie
ersetzen jedoch keinen Test im Spiel. Der Online-MIDI-Hub hängt außerdem vom
externen Katalog und der Verfügbarkeit einzelner Dateien ab.

## Projekt und Herkunft

- [Fehler melden](https://github.com/Larsiopuw/VP-Autoplayer/issues/new/choose)
- [Mitwirken](CONTRIBUTING.md)
- [Herkunft und Rechte](NOTICE.md)

Dieses Projekt ist eine Weiterentwicklung von
[DayLimes VP-Autoplayer](https://github.com/DayLime/VP-Autoplayer) unter der
[MIT-Lizenz](LICENSE). VP Autoplayer ist weder mit Spielen noch mit
VirtualPiano.net offiziell verbunden. Prüfe die Regeln deines Zielspiels vor
dem Einsatz.

© 2025–2026 VP Autoplayer contributors
