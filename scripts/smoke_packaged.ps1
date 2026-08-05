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

function Invoke-PackagedMode {
    <#
        Run one verification mode and return what it actually reported.

        The packaged binary is a GUI-subsystem app -- pysidedeploy.spec builds it
        with --windows-console-mode=disable -- so `& $exe` hands it off without
        waiting, never sets $LASTEXITCODE, and leaves the mode's JSON report with
        no stdout to print to. Start-Process waits for the real exit code, and
        redirecting the streams gives the report somewhere to go, so this checks
        the answer instead of the launch.
    #>
    param(
        [Parameter(Mandatory)] [string] $Exe,
        [Parameter(Mandatory)] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $Label
    )

    $out = New-TemporaryFile
    $err = New-TemporaryFile
    try {
        $process = Start-Process -FilePath $Exe -ArgumentList $Arguments -Wait -PassThru `
            -RedirectStandardOutput $out.FullName -RedirectStandardError $err.FullName
        foreach ($stream in @($out, $err)) {
            $text = Get-Content -Raw -Path $stream.FullName -ErrorAction SilentlyContinue
            if ($text) { Write-Output $text.TrimEnd() }
        }
        if ($process.ExitCode -ne 0) {
            throw "$Label exited $($process.ExitCode)"
        }
    }
    finally {
        Remove-Item -Force -Path $out.FullName, $err.FullName -ErrorAction SilentlyContinue
    }
}

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
    Invoke-PackagedMode -Exe $exe -Arguments @("--self-check") -Label "--self-check"

    Write-Output "== --packaged-smoke =="
    Invoke-PackagedMode -Exe $exe -Arguments @("--packaged-smoke", $smokeRoot) -Label "--packaged-smoke"

    Write-Output "both packaged modes exited 0 from an empty CWD with no PYTHONPATH"
}
finally {
    Pop-Location
    if ($null -ne $previousPythonPath) { $env:PYTHONPATH = $previousPythonPath }
    Remove-Item -Recurse -Force $smokeRoot -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $emptyCwd -ErrorAction SilentlyContinue
}
