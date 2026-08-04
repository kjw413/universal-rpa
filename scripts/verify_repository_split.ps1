# Verify that this project can be extracted into a standalone repository.
#
# SAFETY: `git filter-repo` rewrites history irreversibly. This script therefore
# never touches the user's working checkout. It creates a brand-new clone under
# the system temp directory, proves the target really is a new descendant of that
# temp directory, and only then rewrites history — inside the clone. The clone is
# left behind for inspection and its path is printed.

[CmdletBinding()]
param(
    [string] $Python = ".\.venv\Scripts\python.exe",
    [string] $Subdirectory = "universal_rpa"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Resolve-FilterRepo {
    param([string] $Python)

    # `git filter-repo` only works when git-filter-repo is on PATH. Resolving the
    # executable directly means a pip-installed copy in a venv works without the
    # caller having to arrange their PATH first.
    $candidates = @()
    if (Test-Path $Python) {
        $candidates += (Join-Path (Split-Path $Python -Parent) "git-filter-repo.exe")
        $candidates += (Join-Path (& $Python -c "import sysconfig; print(sysconfig.get_path('scripts'))") "git-filter-repo.exe")
    }
    $onPath = (Get-Command git-filter-repo -ErrorAction SilentlyContinue)
    if ($onPath) { $candidates += $onPath.Source }

    $found = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $found) {
        throw "git-filter-repo not found; install it with: python -m pip install git-filter-repo"
    }
    return $found
}

# Resolve the interpreter before changing directory: $Python may be relative to
# the source checkout, and every later command runs inside the clone.
$resolvedPython = $null
if (Test-Path $Python) { $resolvedPython = (Resolve-Path $Python).Path }

$sourceRepo = (git rev-parse --show-toplevel).Trim()
if ([string]::IsNullOrWhiteSpace($sourceRepo)) {
    throw "not inside a git repository"
}
$sourceRepo = (Resolve-Path $sourceRepo).Path

$tempRoot = (Resolve-Path ([IO.Path]::GetTempPath())).Path
$splitRoot = Join-Path $tempRoot ("universal-rpa-split-" + [guid]::NewGuid())

# Prove the target is a NEW directory strictly beneath the system temp root, and
# is not the source worktree under any spelling.
if (-not $splitRoot.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "refusing to operate outside the system temp directory"
}
if ($splitRoot.TrimEnd('\') -ieq $sourceRepo.TrimEnd('\')) {
    throw "refusing to rewrite the source worktree"
}
if (Test-Path $splitRoot) {
    $existing = @(Get-ChildItem -Force $splitRoot)
    if ($existing.Count -gt 0) {
        throw "refusing a nonempty target directory: $splitRoot"
    }
}

Write-Output "source repository : $sourceRepo"
Write-Output "disposable clone  : $splitRoot"

# Clone FIRST. Every later command runs inside the clone.
git clone --no-local $sourceRepo $splitRoot
if ($LASTEXITCODE -ne 0) { throw "git clone failed" }

Push-Location $splitRoot
try {
    if ((Resolve-Path .).Path.TrimEnd('\') -ieq $sourceRepo.TrimEnd('\')) {
        throw "refusing to rewrite the source worktree"
    }

    # When the project sits in a `universal_rpa/` subdirectory of a larger parent
    # repository, history is rewritten so that subdirectory becomes the root.
    # When the checkout is already the project root there is nothing to extract,
    # and rewriting would only discard history for no benefit.
    if (Test-Path (Join-Path $splitRoot $Subdirectory)) {
        $filterRepo = Resolve-FilterRepo -Python $Python
        Write-Output "== rewriting history to hoist $Subdirectory/ =="
        & $filterRepo --path "$Subdirectory/" --path-rename "${Subdirectory}:" --force
        if ($LASTEXITCODE -ne 0) { throw "git filter-repo failed" }
    }
    else {
        Write-Output "== no $Subdirectory/ subdirectory: this checkout is already standalone =="
    }
    git remote remove origin 2>$null

    Write-Output "== new top level =="
    foreach ($expected in @("pyproject.toml", ".github", "src", "tests", "docs", "samples", "scripts")) {
        if (-not (Test-Path $expected)) {
            throw "expected '$expected' at the new repository root"
        }
        Write-Output "  ok: $expected"
    }

    $splitPython = Join-Path $splitRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $splitPython)) { $splitPython = $resolvedPython }
    if ($splitPython -and (Test-Path $splitPython)) {
        Write-Output "== schema and unit checks in the clone =="
        # PYTHONPATH points at the CLONE's src so the checks exercise the extracted
        # tree. Without it an editable install would silently test the source
        # checkout instead, and the verification would prove nothing.
        $previousPythonPath = $env:PYTHONPATH
        $env:PYTHONPATH = (Join-Path $splitRoot "src")
        $env:QT_QPA_PLATFORM = "offscreen"
        try {
            & $splitPython scripts/export_schema.py --check
            if ($LASTEXITCODE -ne 0) { throw "schema check failed in the split clone" }
            & $splitPython -m pytest tests/unit -q -m "not windows_e2e and not mis_pilot"
            if ($LASTEXITCODE -ne 0) { throw "unit tests failed in the split clone" }
        }
        finally {
            if ($null -ne $previousPythonPath) { $env:PYTHONPATH = $previousPythonPath }
            else { Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue }
            Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
        }
    }
    else {
        Write-Warning "no interpreter available in the clone; skipped schema and unit checks"
    }
}
finally {
    Pop-Location
}

Write-Output ""
Write-Output "split clone left for inspection at: $splitRoot"
Write-Output "the source checkout at $sourceRepo was not modified"
