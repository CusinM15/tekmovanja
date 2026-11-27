# ============================================================
#  zagon.ps1 — Tekmovanja (Stankov memorial) na Windows
#  Naredi VSE: 1) git pull  2) venv + pip install -r requirements.txt  3) python run.py
#  Skripto zaženi iz kateregakoli imenika (sama najde repo ob sebi).
# ============================================================

$ErrorActionPreference = "Stop"

# 0) Greva v koren repozitorija (mapa, kjer leži tale skripta)
Set-Location $PSScriptRoot
Write-Host ""
Write-Host "==============================================" -ForegroundColor DarkGray
Write-Host " Tekmovanja — zagon (repo: $PSScriptRoot)" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor DarkGray

# 1) git pull
Write-Host ""
Write-Host "[1/4] git pull ..." -ForegroundColor Cyan
git pull
if ($LASTEXITCODE -ne 0) {
    Write-Host "GIT PULL JE SPODLETEL — imas lokalne spremembe ali ni povezave?" -ForegroundColor Red
    Write-Host "Preveri z: git status  (in shrani/commitni svoje spremembe)." -ForegroundColor Yellow
    exit 1
}

# 2) Najdi Python (Windows: najprej 'py' launcher, potem 'python')
$python = $null
foreach ($cand in @("py", "python")) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) { $python = $cand; break }
}
if (-not $python) {
    Write-Host "Python ni najden. Namesti ga s https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "in ob namestitvi OBKLJUKAJ 'Add python.exe to PATH'." -ForegroundColor Yellow
    exit 1
}
Write-Host "Python ukaz: $python"

# 3) venv (ustvari samo prvic) + pip install
if (-not (Test-Path "venv")) {
    Write-Host ""
    Write-Host "[2/4] Ustvarjam venv (prvic) ..." -ForegroundColor Cyan
    & $python -m venv venv
    if ($LASTEXITCODE -ne 0) { Write-Host "Napaka pri ustvarjanju venv." -ForegroundColor Red; exit 1 }
}
Write-Host ""
Write-Host "[3/4] Aktiviram venv in nameščam requirements ..." -ForegroundColor Cyan
& ".\venv\Scripts\Activate.ps1"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "PIP INSTALL JE SPODLETEL." -ForegroundColor Red
    exit 1
}

# 4) zagon
Write-Host ""
Write-Host "[4/4] Zaganjam run.py — app na http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "      (ustavi z Ctrl+C)" -ForegroundColor DarkGray
Write-Host ""
python run.py
