# 启动问答。用法：
#   .\run.ps1                      交互模式
#   .\run.ps1 "设备多久保养一次"     单次提问
#
# 这里做的事只有一件：把几个环境变量指到项目内，然后启动 src\cli.py。
# 不设的话，HuggingFace 会往 C:\Users\... 写缓存。

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONIOENCODING = 'utf-8'
$env:HF_HOME = Join-Path $root '.cache\huggingface'
$env:UV_CACHE_DIR = Join-Path $root '.cache\uv'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host "找不到 .venv，先按 README 里的「快速开始」把环境装好" -ForegroundColor Red
    exit 1
}

& $python (Join-Path $root 'src\cli.py') @args
