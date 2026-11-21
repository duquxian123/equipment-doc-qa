# Start the local API. All caches stay inside this project.
# Usage: .\run_api.ps1 [-Port 8000]
param(
    [int]$Port = 8000
)

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

& $python -m uvicorn api:app --app-dir (Join-Path $root 'src') --host 127.0.0.1 --port $Port
