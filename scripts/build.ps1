# Build the Windows package after every non-pilot gate has passed.
#
# The gates run first on purpose: a build produced from a tree that fails
# validation is worse than no build, because it looks releasable.

[CmdletBinding()]
param(
    [string] $Python = ".\.venv\Scripts\python.exe",
    [switch] $SkipTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $repositoryRoot
try {
    if (-not (Test-Path $Python)) {
        throw "Python interpreter not found at $Python"
    }

    if (-not $SkipTests) {
        Write-Output "== automated gates (non-pilot) =="
        $env:QT_QPA_PLATFORM = "offscreen"
        & $Python -m pytest tests -m "not windows_e2e and not mis_pilot" -q
        if ($LASTEXITCODE -ne 0) { throw "automated tests failed" }
        Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue

        Write-Output "== schema =="
        & $Python scripts/export_schema.py --check
        if ($LASTEXITCODE -ne 0) { throw "workflow schema is out of date" }

        Write-Output "== lint, format, types =="
        & $Python -m ruff check src tests samples scripts
        if ($LASTEXITCODE -ne 0) { throw "ruff check failed" }
        & $Python -m ruff format --check src tests samples scripts
        if ($LASTEXITCODE -ne 0) { throw "ruff format check failed" }
        & $Python -m mypy src
        if ($LASTEXITCODE -ne 0) { throw "mypy failed" }
    }

    Write-Output "== toolchain pins =="
    & $Python -c "import PySide6, sys; sys.exit(0 if PySide6.__version__ == '6.11.1' else 1)"
    if ($LASTEXITCODE -ne 0) { throw "PySide6 is not pinned to 6.11.1" }
    # pyside6-deploy will `pip install Nuitka` unpinned if it is absent, so the
    # pinned version has to already be satisfied before the build starts.
    $nuitkaVersion = (& $Python -m pip show Nuitka | Select-String '^Version:\s*(.+)$').Matches.Groups[1].Value
    if ($nuitkaVersion -ne "4.1.3") { throw "Nuitka is $nuitkaVersion, expected 4.1.3" }

    # pyside6-deploy must be invoked through its console script: the module needs
    # its own scripts directory on sys.path, which `python -m` does not provide.
    # A venv created with --system-site-packages has no copy of it, so fall back
    # to the base interpreter that actually owns the PySide6 install.
    $candidates = @(
        (Join-Path (Split-Path $Python -Parent) "pyside6-deploy.exe"),
        (Join-Path (& $Python -c "import sysconfig; print(sysconfig.get_path('scripts'))") "pyside6-deploy.exe"),
        (Join-Path (& $Python -c "import sys; print(sys.base_prefix)") "Scripts\pyside6-deploy.exe")
    )
    $deploy = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $deploy) {
        throw "pyside6-deploy.exe not found; install PySide6==6.11.1 in this environment"
    }
    Write-Output "using: $deploy"

    # pyside6-deploy rewrites the spec in place, baking in this machine's Python
    # path and icon path. The committed spec must stay machine-independent, so
    # snapshot it and restore it however the build ends.
    $specPath = Join-Path $repositoryRoot "pysidedeploy.spec"
    $specBackup = Get-Content -Raw -Path $specPath
    try {
        Write-Output "== deploy dry run =="
        & $deploy -c pysidedeploy.spec --dry-run --force
        if ($LASTEXITCODE -ne 0) { throw "pyside6-deploy dry run failed" }

        Write-Output "== build =="
        if (Test-Path (Join-Path $repositoryRoot "dist")) {
            Remove-Item -Recurse -Force (Join-Path $repositoryRoot "dist")
        }
        & $deploy -c pysidedeploy.spec --force
        if ($LASTEXITCODE -ne 0) { throw "pyside6-deploy build failed" }
    }
    finally {
        Set-Content -Path $specPath -Value $specBackup -NoNewline
        Remove-Item -Recurse -Force (Join-Path $repositoryRoot "src\universal_rpa\deployment") -ErrorAction SilentlyContinue
    }

    $executables = @(Get-ChildItem -Path (Join-Path $repositoryRoot "dist") -Recurse -Filter "*.exe")
    if ($executables.Count -ne 1) {
        throw "expected exactly one packaged executable, found $($executables.Count)"
    }
    Write-Output "built: $($executables[0].FullName)"
}
finally {
    Pop-Location
}
