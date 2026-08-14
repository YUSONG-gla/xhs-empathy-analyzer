param(
    [int]$Port = 8000
)

$backendPath = Join-Path $PSScriptRoot "heart\backend"
Set-Location $backendPath
python -m uvicorn main:app --host 0.0.0.0 --port $Port
