param(
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8000
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvActivate = Join-Path $projectRoot ".venv\Scripts\Activate.ps1"

if (-not (Test-Path $venvActivate)) {
    Write-Error "No se encontro el entorno virtual en $venvActivate"
    exit 1
}

$ngrokPath = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ngrok.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName

if (-not $ngrokPath) {
    Write-Error "No se encontro ngrok.exe. Instala ngrok primero."
    exit 1
}

$appCommand = @"
Set-Location '$projectRoot'
. '$venvActivate'
python -m app.cli serve --host $HostAddress --port $Port
"@

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    $appCommand
)

Start-Sleep -Seconds 3

$upstreamUrl = "http://127.0.0.1:$Port"
& $ngrokPath http $upstreamUrl
