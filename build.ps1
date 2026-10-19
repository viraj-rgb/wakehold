# Builds dist\WakeHold\WakeHold.exe (no Python needed to run it).
# Usage:  .\build.ps1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

python -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; not building.' }

python tools\make_icon.py
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }

$excludes = 'tkinter','matplotlib','scipy','pandas','numpy','IPython','pytest','PyQt6.QtWebEngineCore',
            'PyQt6.QtWebEngineWidgets','PyQt6.QtQml','PyQt6.QtQuick','PyQt6.QtPdf','PyQt6.QtMultimedia',
            'PyQt6.Qt3DCore','PyQt6.QtNetwork','PyQt6.QtOpenGL','PyQt6.QtSql'
$excludeArgs = $excludes | ForEach-Object { '--exclude-module'; $_ }

python -m PyInstaller --noconfirm --clean --windowed --name WakeHold --icon assets\wakehold.ico @excludeArgs app.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

$size = (Get-ChildItem dist\WakeHold -Recurse | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Built dist\WakeHold\WakeHold.exe ({0:N0} MB folder)" -f $size)
