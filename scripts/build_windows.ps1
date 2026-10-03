param(
    [switch]$SkipTests,
    [switch]$SkipZip
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
    if ($LASTEXITCODE -ne 0) { throw "Could not create the build environment." }
}

Push-Location $RepoRoot
try {
    Write-Host "Installing packaging dependencies..."
    & $PythonExe -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Could not update pip." }
    & $PythonExe -m pip install -e ".[packaging]"
    if ($LASTEXITCODE -ne 0) { throw "Could not install packaging dependencies." }

    if (-not $SkipTests) {
        Write-Host "Running tests before packaging..."
        & $PythonExe -m unittest discover -s tests
        if ($LASTEXITCODE -ne 0) { throw "Tests failed; refusing to package." }
    }

    Write-Host "Building Windows app bundle..."
    $ResolvedAppDir = [IO.Path]::GetFullPath($AppDir)
    $ResolvedDistRoot = [IO.Path]::GetFullPath($DistDir).TrimEnd('\') + '\'
    if (-not $ResolvedAppDir.StartsWith($ResolvedDistRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Build destination must stay inside the repository dist folder."
    }
    & $PythonExe -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name $AppName `
        --collect-submodules "openraw_studio" `
        --collect-data "openraw_studio.ui" `
        --paths "src" `
        --specpath "build\pyinstaller-spec" `
        "packaging\openraw_app.py"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed; no release was produced." }

    if (-not (Test-Path $AppDir)) {
        throw "PyInstaller did not create the expected app folder: $AppDir"
    }

    foreach ($Document in @("README.md", "LICENSE", "NOTICE")) {
        Copy-Item -LiteralPath (Join-Path $RepoRoot $Document) -Destination (Join-Path $AppDir $Document) -Force
    }

    $ThirdPartyRoot = Join-Path $AppDir "THIRD_PARTY_LICENSES"
    New-Item -ItemType Directory -Path $ThirdPartyRoot -Force | Out-Null
    foreach ($Package in @(
        @{ Name = "NumPy"; Pattern = "numpy-*.dist-info" },
        @{ Name = "Pillow"; Pattern = "pillow-*.dist-info" },
        @{ Name = "Numba"; Pattern = "numba-*.dist-info" },
        @{ Name = "llvmlite"; Pattern = "llvmlite-*.dist-info" },
        @{ Name = "PyOpenCL"; Pattern = "pyopencl-*.dist-info" },
        @{ Name = "pytools"; Pattern = "pytools-*.dist-info" },
        @{ Name = "platformdirs"; Pattern = "platformdirs-*.dist-info" },
        @{ Name = "typing_extensions"; Pattern = "typing_extensions-*.dist-info" }
    )) {
        $DistInfo = Get-ChildItem -Path (Join-Path $BuildVenvDir "Lib\site-packages") -Directory -Filter $Package.Pattern |
            Sort-Object Name -Descending |
            Select-Object -First 1
        if (-not $DistInfo) {
            throw "Could not locate installed license metadata for $($Package.Name)."
        }
        $LicenseSource = Join-Path $DistInfo.FullName "licenses"
        $LicenseDestination = Join-Path $ThirdPartyRoot $Package.Name
        if (Test-Path $LicenseSource) {
            Copy-Item -LiteralPath $LicenseSource -Destination $LicenseDestination -Recurse -Force
        }
        else {
            $LicenseFiles = @(Get-ChildItem -LiteralPath $DistInfo.FullName -File -Filter "LICENSE*")
            if ($LicenseFiles.Count -eq 0) {
                throw "Installed $($Package.Name) package does not include license notices."
            }
            New-Item -ItemType Directory -Path $LicenseDestination -Force | Out-Null
            foreach ($LicenseFile in $LicenseFiles) {
                Copy-Item -LiteralPath $LicenseFile.FullName -Destination $LicenseDestination -Force
            }
        }
    }
    Copy-Item -LiteralPath (Join-Path $RepoRoot "packaging\licenses\siphash24") -Destination $ThirdPartyRoot -Recurse -Force

    if (-not $SkipZip) {
        if (Test-Path $ZipPath) {
            Remove-Item -LiteralPath $ZipPath -Force
        }
        Write-Host "Creating zip package..."
        Compress-Archive -LiteralPath $AppDir -DestinationPath $ZipPath -Force
        Write-Host "Windows package: $ZipPath"
    }
    Write-Host "Desktop executable: $(Join-Path $AppDir ($AppName + '.exe'))"
}
finally {
    Pop-Location
}
