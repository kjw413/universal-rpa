"""Repository hygiene: no customer or runtime artifact may ever be committed.

A workflow, a raw recording, a preview, a credential, a run report, or a customer
CSV/XLSX in git is a disclosure that cannot be undone by a later commit.  So the
scan is an allowlist, not a denylist: the *only* ``.jsonl`` permitted anywhere in
the source tree is one listed in the synthetic manifest with a matching digest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.repository_hygiene import (
    SYNTHETIC_MANIFEST_RELATIVE_PATH,
    scan_repository_for_runtime_artifacts,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PRUNED_ROOTS = (".git", ".venv", "build", "dist", ".pytest_cache", ".mypy_cache", ".ruff_cache")


@pytest.fixture
def synthetic_repo_root(tmp_path: Path) -> Path:
    """A miniature source tree with a valid synthetic recording manifest."""

    root = tmp_path / "repo"
    recordings = root / "tests" / "fixtures" / "recordings"
    recordings.mkdir(parents=True)
    (root / "src" / "universal_rpa").mkdir(parents=True)
    (root / "src" / "universal_rpa" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")

    listed = recordings / "synthetic-session.jsonl"
    listed.write_text('{"synthetic":"only"}\n', encoding="utf-8")
    manifest = root / SYNTHETIC_MANIFEST_RELATIVE_PATH
    manifest.parent.mkdir(parents=True, exist_ok=True)
    import hashlib

    manifest.write_text(
        json.dumps(
            {
                "synthetic_only": True,
                "files": [
                    {
                        "path": "tests/fixtures/recordings/synthetic-session.jsonl",
                        "sha256": hashlib.sha256(listed.read_bytes()).hexdigest(),
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return root


def test_a_clean_synthetic_tree_reports_nothing(synthetic_repo_root: Path) -> None:
    assert scan_repository_for_runtime_artifacts(synthetic_repo_root) == ()


def test_repository_hygiene_allows_only_manifested_synthetic_recording_jsonl(
    synthetic_repo_root: Path,
) -> None:
    unlisted = synthetic_repo_root / "tests/fixtures/recordings/unlisted.jsonl"
    unlisted.write_text('{"text":"customer"}', encoding="utf-8")

    assert unlisted in scan_repository_for_runtime_artifacts(synthetic_repo_root)


def test_a_listed_recording_whose_bytes_changed_is_rejected(
    synthetic_repo_root: Path,
) -> None:
    listed = synthetic_repo_root / "tests/fixtures/recordings/synthetic-session.jsonl"
    listed.write_text('{"text":"customer"}\n', encoding="utf-8")

    assert listed in scan_repository_for_runtime_artifacts(synthetic_repo_root)


def test_a_recording_outside_the_manifested_directory_is_rejected(
    synthetic_repo_root: Path,
) -> None:
    stray = synthetic_repo_root / "src" / "universal_rpa" / "session.jsonl"
    stray.write_text('{"synthetic":"but misplaced"}', encoding="utf-8")

    assert stray in scan_repository_for_runtime_artifacts(synthetic_repo_root)


@pytest.mark.parametrize(
    "name",
    [
        "workflow.json",
        "report.json",
        "checkpoint.active.json",
        "customer-data.csv",
        "customer-data.xlsx",
        "target-preview.png",
        "erp.credential",
    ],
)
def test_runtime_and_customer_artifacts_are_rejected_anywhere(
    synthetic_repo_root: Path, name: str
) -> None:
    artifact = synthetic_repo_root / "src" / name
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"x")

    assert artifact in scan_repository_for_runtime_artifacts(synthetic_repo_root)


@pytest.mark.parametrize("ignored_root", PRUNED_ROOTS)
def test_repository_hygiene_prunes_vcs_build_and_environment_roots(
    synthetic_repo_root: Path,
    ignored_root: str,
) -> None:
    ignored = synthetic_repo_root / ignored_root / "should-not-be-scanned.jsonl"
    ignored.parent.mkdir(parents=True, exist_ok=True)
    ignored.write_text('{"synthetic":"outside source tree"}', encoding="utf-8")

    assert scan_repository_for_runtime_artifacts(synthetic_repo_root) == ()


def test_a_manifest_that_is_not_synthetic_only_rejects_everything_it_lists(
    synthetic_repo_root: Path,
) -> None:
    manifest_path = synthetic_repo_root / SYNTHETIC_MANIFEST_RELATIVE_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["synthetic_only"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    listed = synthetic_repo_root / "tests/fixtures/recordings/synthetic-session.jsonl"
    assert listed in scan_repository_for_runtime_artifacts(synthetic_repo_root)


def test_the_real_repository_carries_no_customer_or_runtime_artifact() -> None:
    found = scan_repository_for_runtime_artifacts(REPOSITORY_ROOT)

    assert found == (), "\n".join(str(path) for path in found)
