# Mitwirken

Danke für dein Interesse an VP Autoplayer. Öffne für Fehler möglichst zuerst
ein Issue mit Windows-Version, App-Version, Instrumentprofil und konkreten
Schritten zur Reproduktion. Bitte keine privaten Songs, Einstellungen, Logs
mit persönlichen Pfaden oder unlizenzierte Musikdateien hochladen.

Für einen Pull Request:

1. Repository forken und einen thematisch engen Branch anlegen.
2. Python 3.11 und die Abhängigkeiten aus `requirements.txt` verwenden.
3. Geändertes Verhalten mit einem automatisierten Test absichern.
4. `./scripts/verify-release.ps1 -Python python` ausführen.
5. Beschreiben, was sich ändert und wie es manuell geprüft wurde.

Oberflächenänderungen sollten im Dark- und Light-Mode sowie mit Windows-
Skalierung geprüft werden. Für Eingabeänderungen reichen Parser- oder
Erkennungsquoten nicht aus: relevante Key-down-/Key-up-Ereignisse und
freigegebene Zielspiele müssen getestet werden. Jeder Code und jedes Skript
endet mit `-- by Larsiopuw` im passenden Kommentarstil.
