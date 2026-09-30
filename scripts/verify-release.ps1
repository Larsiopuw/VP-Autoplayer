param(
    [string]$Python = "python",
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $projectRoot
try {
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests fehlgeschlagen." }

    & $Python -m compileall -q autoplayer_core.py batch_import.py gui.py midi_hub.py midi_preview.py studio.py studio_features.py transcription.py ui_effects.py
    if ($LASTEXITCODE -ne 0) { throw "Python-Syntaxpruefung fehlgeschlagen." }

    if ($Build) {
        & $Python -m PyInstaller --noconfirm --clean gui.spec
        if ($LASTEXITCODE -ne 0) { throw "Windows-Build fehlgeschlagen." }

        $exe = Join-Path $projectRoot "dist\VP Autoplayer.exe"
        if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
            throw "Die Release-EXE fehlt: $exe"
        }
        $oldQtPlatform = $env:QT_QPA_PLATFORM
        try {
            $env:QT_QPA_PLATFORM = "offscreen"
            & $exe --verify-studio
            if ($LASTEXITCODE -ne 0) { throw "EXE-Studio-Smoke-Test fehlgeschlagen." }
        } finally {
            $env:QT_QPA_PLATFORM = $oldQtPlatform
        }

        $version = (Get-Item -LiteralPath $exe).VersionInfo
        if ($version.ProductName -ne "VP Autoplayer") {
            throw "Produktmetadaten der EXE fehlen."
        }
        $signature = Get-AuthenticodeSignature -LiteralPath $exe
        $checksum = Get-FileHash -LiteralPath $exe -Algorithm SHA256
        Write-Host "EXE: $exe"
        Write-Host "Version: $($version.ProductVersion)"
        Write-Host "Signatur: $($signature.Status)"
        Write-Host "SHA256: $($checksum.Hash)"
    }
} finally {
    Pop-Location
}

# -- by Larsiopuw
