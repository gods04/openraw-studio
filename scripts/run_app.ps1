$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvDir = Join-Path $RepoRoot ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$InstallStamp = Join-Path $VenvDir ".openraw-editable-installed"
$PyProject = Join-Path $RepoRoot "pyproject.toml"

function Find-SystemPython {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return @("py", "-3")
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @("python")
    }

    throw "Python 3.11+ was not found. Install Python from https://www.python.org/downloads/ and try again."
}

if (-not (Test-Path $PythonExe)) {
    Write-Host "Creating local Python environment in .venv..."
    $systemPython = Find-SystemPython
    $pythonCommand = $systemPython[0]
    $pythonArgs = @()
    if ($systemPython.Length -gt 1) {
        $pythonArgs = $systemPython[1..($systemPython.Length - 1)]
    }
    & $pythonCommand @pythonArgs -m venv $VenvDir
}

$needsInstall = -not (Test-Path $InstallStamp)
if (-not $needsInstall) {
    $needsInstall = (Get-Item $PyProject).LastWriteTimeUtc -gt (Get-Item $InstallStamp).LastWriteTimeUtc
}

if ($needsInstall) {
    Write-Host "Installing OpenRAW Studio locally..."
    & $PythonExe -m pip install -e $RepoRoot
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    New-Item -ItemType File -Force -Path $InstallStamp | Out-Null
}

Write-Host "Opening OpenRAW Studio..."
& $PythonExe -m openraw_studio app
