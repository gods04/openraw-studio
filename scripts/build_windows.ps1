param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildVenvDir = Join-Path $RepoRoot ".venv-build"
$PythonExe = Join-Path $BuildVenvDir "Scripts\python.exe"
$DistDir = Join-Path $RepoRoot "dist"
$AppName = "OpenRAW Studio"
$AppDir = Join-Path $DistDir $AppName
$ZipPath = Join-Path $DistDir "OpenRAW-Studio-windows-x64.zip"

function Find-SystemPython {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return @("py", "-3.11")
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @("python")
    }

    throw "Python 3.11+ was not found. Install Python from https://www.python.org/downloads/ and try again."
}

if (-not (Test-Path $PythonExe)) {
    Write-Host "Creating build environment in .venv-build..."
    $systemPython = Find-SystemPython
    $pythonCommand = $systemPython[0]
    $pythonArgs = @()
    if ($systemPython.Length -gt 1) {
        $pythonArgs = $systemPython[1..($systemPython.Length - 1)]
    }
    & $pythonCommand @pythonArgs -m venv $BuildVenvDir
}

Push-Location $RepoRoot
try {
    Write-Host "Installing packaging dependencies..."
    & $PythonExe -m pip install --upgrade pip
    & $PythonExe -m pip install -e ".[packaging]"

    if (-not $SkipTests) {
        Write-Host "Running tests before packaging..."
        & $PythonExe -m unittest discover -s tests
    }

    Write-Host "Building Windows app bundle..."
    & $PythonExe -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name $AppName `
        --collect-submodules "openraw_studio" `
        --paths "src" `
        --specpath "build\pyinstaller-spec" `
        "packaging\openraw_app.py"

    if (-not (Test-Path $AppDir)) {
        throw "PyInstaller did not create the expected app folder: $AppDir"
    }

    if (Test-Path $ZipPath) {
        Remove-Item -LiteralPath $ZipPath -Force
    }

    Write-Host "Creating zip package..."
    Compress-Archive -LiteralPath $AppDir -DestinationPath $ZipPath -Force
    Write-Host "Windows package: $ZipPath"
}
finally {
    Pop-Location
}
