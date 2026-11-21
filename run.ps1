# Start the CLI.
# Usage:
#   .\run.ps1
#   .\run.ps1 "How often should secondary maintenance be performed?"
#
# All caches are kept inside this project.

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONIOENCODING = 'utf-8'
$env:HF_HOME = Join-Path $root '.cache\huggingface'
$env:UV_CACHE_DIR = Join-Path $root '.cache\uv'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host "Missing .venv. Follow the README to install the project environment." -ForegroundColor Red
    exit 1
}

& $python (Join-Path $root 'src\cli.py') @args
