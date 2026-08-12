# Setup-Venv.ps1 -- create the virtual environment on F: (kept off C: to avoid
# bloat) and install requirements. Idempotent. Run from the program dir:
#   pwsh -NoProfile -File .\tools\Setup-Venv.ps1
$ErrorActionPreference = 'Stop'
$prog = Split-Path -Parent $PSScriptRoot
$dataDir = 'F:\My_Programs\Phenomonological_Chess_Coach_Data'
$venv = Join-Path $dataDir 'venv'
$py = Join-Path $venv 'Scripts\python.exe'

Write-Host "Program dir : $prog"
Write-Host "Data dir    : $dataDir"
Write-Host "Venv        : $venv"

New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
foreach ($d in 'logs','games','runs','glossary','cache') {
  New-Item -ItemType Directory -Force -Path (Join-Path $dataDir $d) | Out-Null
}

if (-not (Test-Path $py)) {
  Write-Host "Creating venv..."
  python -m venv $venv
}
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install -r (Join-Path $prog 'requirements.txt')
Write-Host "Done. Python: $py"
& $py -c "import chess, flask, requests, waitress; print('deps ok', chess.__version__)"
