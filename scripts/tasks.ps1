param(
    [ValidateSet("setup", "dev", "run", "test", "lint", "format", "cli")]
    [string]$Task,
    [string]$CliArgs = "",
    [string]$TestArgs = "",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvRoot = Join-Path $projectRoot ".venv"
$pythonExe = Join-Path $venvRoot "Scripts\python.exe"
$pipExe = Join-Path $venvRoot "Scripts\pip.exe"

function Assert-Venv {
    if (-not (Test-Path $pythonExe)) {
        throw "Virtual environment not found at $pythonExe. Run '.\scripts\tasks.ps1 setup' first."
    }
}

Push-Location $projectRoot
try {
    switch ($Task) {
        "setup" {
            py -3 -m venv $venvRoot
            & $pythonExe -m pip install --upgrade pip
            if (Test-Path "requirements.txt") {
                & $pipExe install -r requirements.txt
            }
            elseif (Test-Path "pyproject.toml") {
                & $pipExe install -e .
            }
            else {
                Write-Host "No requirements.txt or pyproject.toml found; skipping dependency install."
            }
        }
        "dev" {
            Assert-Venv
            & $pythonExe -m uvicorn app.main:app --host $HostAddress --port $Port --reload
        }
        "run" {
            Assert-Venv
            & $pythonExe -m uvicorn app.main:app --host $HostAddress --port $Port
        }
        "test" {
            Assert-Venv
            $testArgList = @()
            if ($TestArgs) {
                $testArgList = $TestArgs.Split(" ", [System.StringSplitOptions]::RemoveEmptyEntries)
            }
            & $pythonExe -m pytest @testArgList
        }
        "lint" {
            Assert-Venv
            & $pythonExe -m ruff check .
        }
        "format" {
            Assert-Venv
            & $pythonExe -m ruff format .
        }
        "cli" {
            Assert-Venv
            $cliArgList = @()
            if ($CliArgs) {
                $cliArgList = $CliArgs.Split(" ", [System.StringSplitOptions]::RemoveEmptyEntries)
            }
            & $pythonExe -m app.cli @cliArgList
        }
    }
}
finally {
    Pop-Location
}
