# Extracting Universal RPA Studio into a standalone repository

This project currently lives inside a parent repository. It is written so it can
be lifted out unchanged: nothing in `src/universal_rpa` imports a parent module,
and the packaged executable proves it — `scripts\smoke_packaged.ps1` launches the
EXE from an empty working directory with `PYTHONPATH` removed, so an accidental
dependency on the parent tree fails the smoke rather than passing silently.

## Running the verification

```powershell
.\scripts\verify_repository_split.ps1
```

The script prints the path of the disposable clone it created and leaves it for
inspection.

## Why the script never touches your checkout

`git filter-repo` rewrites history irreversibly. There is no safe way to "try it
and see" in a working repository, so the script is built to make that impossible:

1. It resolves the source worktree with `git rev-parse --show-toplevel` and
   resolves the system temp root.
2. It composes a target path from the temp root and a fresh GUID, then *proves*
   the target starts with the temp root, is not the source worktree under any
   spelling, and is not an existing nonempty directory.
3. It runs `git clone --no-local` **before** any rewrite.
4. Only then does it `Push-Location` into the clone and run `git filter-repo`.
   Every rewriting command runs with the clone as the working directory.
5. It removes `origin` so the detached clone cannot push anywhere.

`tests/unit/scripts/test_repository_split_script.py` reads the checked-in script
and asserts this ordering statically, with comment lines stripped so a comment
mentioning `filter-repo` cannot make the script look safe or unsafe.

## What the extracted repository must contain

At the new top level:

| Path | Contents |
| --- | --- |
| `pyproject.toml` | Package metadata and the pinned build toolchain |
| `.github/` | `windows.yml` and `package-windows.yml` |
| `src/` | `universal_rpa` and nothing else |
| `tests/` | unit, contract, ui, integration |
| `docs/` | architecture, schemas, pilot, validation |
| `samples/` | the deterministic test harness |
| `scripts/` | schema export, build, smoke, split verification |

The script asserts each of these exists in the clone, then runs the schema check
and the unit suite there so the extraction is verified by execution, not by
inspection alone.

## Prerequisite

`git filter-repo` is not bundled with Git:

```powershell
python -m pip install git-filter-repo
```

## What is deliberately excluded

The distribution must never carry `recordings`, `artifacts`, `projects`, or
`.superpowers`, and must never carry the `tests`, `samples`, or `scripts` modules
into the packaged bundle. `scripts\smoke_packaged.ps1` scans the real
distribution manifest for these and fails the build if any appear, and
`tests/unit/test_packaged_smoke.py` asserts the same set from Python.
