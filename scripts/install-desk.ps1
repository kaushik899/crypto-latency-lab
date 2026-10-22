# Likely Profits — install (first time) or update, then start the trade desk on this computer (Windows).
#   irm https://raw.githubusercontent.com/kaushik899/crypto-latency-lab/main/scripts/install-desk.ps1 | iex
# Needs git and Python 3.10+. Everything stays in %USERPROFILE%\likely-profits; keys go in Windows Credential Manager.
$ErrorActionPreference = "Stop"
$Dir = if ($env:LIKELY_PROFITS_DIR) { $env:LIKELY_PROFITS_DIR } else { Join-Path $HOME "likely-profits" }
$Repo = "https://github.com/kaushik899/crypto-latency-lab.git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Host "Please install Git: https://git-scm.com/download/win"; return }
$Py = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { $null }
if (-not $Py) { Write-Host "Please install Python 3.10+: https://www.python.org/downloads/ (tick 'Add to PATH')"; return }
if (Test-Path (Join-Path $Dir ".git")) { Write-Host "Updating $Dir ..."; git -C $Dir pull -q --ff-only }
else { Write-Host "Installing into $Dir ..."; git clone -q --depth 1 $Repo $Dir }
Set-Location $Dir
if (-not (Test-Path ".venv")) { & $Py -m venv .venv }
$VPy = Join-Path $Dir ".venv\Scripts\python.exe"
& $VPy -m pip install -q --upgrade pip
& $VPy -m pip install -q -r requirements.txt keyring
Write-Host "Starting the desk. Your browser opens Likely Profits already paired."
& $VPy -m latlab.desk @args
