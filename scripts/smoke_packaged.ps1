# Run the packaged executable in isolation and inspect what it actually shipped.
#
# The isolation matters more than the smoke itself: an EXE launched from the
# repository root with PYTHONPATH set will happily import the parent source tree
# and "pass" while shipping nothing.  So this runs from a fresh empty directory
# with PYTHONPATH removed, which is the only way the run proves the bundle is
# self-contained.

[CmdletBinding()]
param(
    [string] $DistRoot = "dist"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$forbiddenRoots = @("recordings", "artifacts", "projects", ".superpowers", "tests", "samples")
$forbiddenModules = @("tests", "samples", "scripts")

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$distPath = Join-Path $repositoryRoot $DistRoot
if (-not (Test-Path $distPath)) {
    throw "distribution directory not found: $distPath. Run scripts\build.ps1 first."
}

$executables = @(Get-ChildItem -Path $distPath -Recurse -Filter "*.exe")
if ($executables.Count -ne 1) {
    throw "expected exactly one packaged executable, found $($executables.Count)"
}
$exe = $executables[0].FullName
Write-Output "packaged executable: $exe"

Write-Output "== distribution manifest =="
$manifestRoot = $executables[0].Directory.FullName
$topLevel = Get-ChildItem -Path $manifestRoot | Select-Object -ExpandProperty Name
foreach ($name in $topLevel) {
    if ($forbiddenRoots -contains $name) {
        throw "forbidden root '$name' is present in the distribution"
    }
}
foreach ($module in $forbiddenModules) {
    $leaked = @(Get-ChildItem -Path $manifestRoot -Recurse -Directory -Filter $module -ErrorAction SilentlyContinue)
    if ($leaked.Count -gt 0) {
        throw "parent-repository module '$module' leaked into the distribution"
    }
}
Write-Output "manifest clean: $($topLevel.Count) top-level entries"

$smokeRoot = Join-Path ([IO.Path]::GetTempPath()) ("universal-rpa-smoke-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $smokeRoot | Out-Null
$emptyCwd = Join-Path ([IO.Path]::GetTempPath()) ("universal-rpa-cwd-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $emptyCwd | Out-Null

$previousPythonPath = $env:PYTHONPATH
Push-Location $emptyCwd
try {
    Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue

    Write-Output "== --self-check =="
    & $exe --self-check
    if ($LASTEXITCODE -ne 0) { throw "--self-check exited $LASTEXITCODE" }

    Write-Output "== --packaged-smoke =="
    & $exe --packaged-smoke $smokeRoot
    if ($LASTEXITCODE -ne 0) { throw "--packaged-smoke exited $LASTEXITCODE" }

    Write-Output "both packaged modes exited 0 from an empty CWD with no PYTHONPATH"
}
finally {
    Pop-Location
    if ($null -ne $previousPythonPath) { $env:PYTHONPATH = $previousPythonPath }
    Remove-Item -Recurse -Force $smokeRoot -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $emptyCwd -ErrorAction SilentlyContinue
}
