"""Scan a source tree for customer or runtime artifacts that must never be committed.

The scan is an allowlist rather than a denylist, because the cost of the two
mistakes is not symmetric.  A false positive costs someone a minute; a customer
workflow, recording, credential, or export that reaches git is a disclosure no
later commit can undo.  So a recording file is permitted only when the synthetic
manifest lists it *and* its bytes still hash to the listed digest.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

#: The one manifest that may bless recording fixtures, relative to the repo root.
SYNTHETIC_MANIFEST_RELATIVE_PATH = "tests/fixtures/recordings/synthetic-manifest.json"

#: The one directory a manifested recording may live in.
SYNTHETIC_RECORDINGS_DIRECTORY = "tests/fixtures/recordings"

#: Never scanned: version control, environments, build output, and tool caches.
PRUNED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "build",
        "dist",
        "deployment",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".idea",
        ".vs",
    }
)

#: Runtime or customer artifacts, rejected wherever they appear.
FORBIDDEN_SUFFIXES = frozenset({".jsonl", ".csv", ".xlsx", ".xls", ".credential"})

#: Runtime JSON documents, rejected by exact file name.
FORBIDDEN_JSON_NAMES = frozenset(
    {"workflow.json", "report.json", "pilot-bundle.json", "pilot-policy.json"}
)

#: Runtime JSON documents, rejected by name suffix.
FORBIDDEN_JSON_SUFFIXES = ("active.json", "terminal.json", "journal.json", "manifest.jsonl")

#: Target previews and failure screenshots.
FORBIDDEN_IMAGE_SUFFIXES = frozenset({".png", ".bmp", ".jpg", ".jpeg"})


def _iter_source_files(root: Path) -> Iterator[Path]:
    """Walk *root*, pruning whole directories rather than filtering afterwards."""

    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in PRUNED_DIRECTORY_NAMES:
                    stack.append(entry)
                continue
            yield entry


def _load_manifest(root: Path) -> dict[str, str]:
    """Return the allowed ``relative path -> sha256`` map, empty when unusable."""

    manifest_path = root / SYNTHETIC_MANIFEST_RELATIVE_PATH
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("synthetic_only") is not True:
        return {}
    entries = payload.get("files")
    if not isinstance(entries, list):
        return {}
    allowed: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        relative = entry.get("path")
        digest = entry.get("sha256")
        if not isinstance(relative, str) or not isinstance(digest, str):
            continue
        if not relative.startswith(f"{SYNTHETIC_RECORDINGS_DIRECTORY}/"):
            continue
        allowed[relative] = digest.casefold()
    return allowed


def _is_manifested_recording(path: Path, relative: str, allowed: dict[str, str]) -> bool:
    expected = allowed.get(relative)
    if expected is None:
        return False
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return False
    return actual == expected


def _is_forbidden(path: Path, relative: str, allowed: dict[str, str]) -> bool:
    suffix = path.suffix.casefold()
    name = path.name.casefold()

    if suffix == ".jsonl":
        return not _is_manifested_recording(path, relative, allowed)
    if suffix in FORBIDDEN_SUFFIXES:
        return True
    if suffix in FORBIDDEN_IMAGE_SUFFIXES:
        return True
    if suffix == ".json":
        if name in FORBIDDEN_JSON_NAMES:
            return True
        return any(name.endswith(marker) for marker in FORBIDDEN_JSON_SUFFIXES)
    return False


def scan_repository_for_runtime_artifacts(root: Path) -> tuple[Path, ...]:
    """Return every path in *root* that must not be committed, sorted."""

    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise ValueError("repository root must be an existing directory")
    allowed = _load_manifest(resolved)
    found = [
        path
        for path in _iter_source_files(resolved)
        if _is_forbidden(path, path.relative_to(resolved).as_posix(), allowed)
    ]
    return tuple(sorted(found))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Scan for committed runtime artifacts")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()

    found = scan_repository_for_runtime_artifacts(arguments.root)
    if not found:
        print("repository hygiene: clean")
        return 0
    print(f"repository hygiene: {len(found)} forbidden artifact(s)")
    for path in found:
        print(f"  {path.relative_to(Path(arguments.root).resolve()).as_posix()}")
    return 1


__all__ = [
    "FORBIDDEN_SUFFIXES",
    "PRUNED_DIRECTORY_NAMES",
    "SYNTHETIC_MANIFEST_RELATIVE_PATH",
    "SYNTHETIC_RECORDINGS_DIRECTORY",
    "scan_repository_for_runtime_artifacts",
]


if __name__ == "__main__":
    raise SystemExit(main())
