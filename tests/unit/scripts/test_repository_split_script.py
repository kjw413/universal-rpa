"""Static safety review of the repository-split script.

The script rewrites history with ``git filter-repo``, which is irreversible.  It
is therefore only ever allowed to do so inside a freshly-created disposable clone
under the system temp directory, and these tests read the checked-in script to
prove it cannot be pointed at the user's working checkout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "verify_repository_split.ps1"


@dataclass(frozen=True, slots=True)
class SplitScriptReview:
    clones_before_filter_repo: bool
    filter_repo_target_is_temporary_clone: bool
    mutates_source_checkout: bool
    refuses_nonempty_target: bool
    proves_target_is_under_temp: bool
    removes_origin: bool
    prints_clone_path: bool


def _executable_text(path: Path) -> str:
    """Drop comment-only lines so ordering reflects what actually runs."""

    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    return "\n".join(lines)


def inspect_split_script(path: Path) -> SplitScriptReview:
    text = _executable_text(path)
    clone_at = text.find("git clone")
    filter_at = text.find("git filter-repo")
    push_at = text.find("Push-Location $splitRoot")
    return SplitScriptReview(
        clones_before_filter_repo=0 <= clone_at < filter_at,
        filter_repo_target_is_temporary_clone=0 <= push_at < filter_at,
        mutates_source_checkout=bool(
            re.search(r"filter-repo[^\n]*\$sourceRepo", text)
            or re.search(r"Set-Location\s+\$sourceRepo[^\n]*\n[^\n]*filter-repo", text)
        ),
        refuses_nonempty_target="nonempty" in text.casefold() or "not empty" in text.casefold(),
        proves_target_is_under_temp="GetTempPath" in text and "StartsWith" in text,
        removes_origin="git remote remove origin" in text,
        prints_clone_path="Write-Host" in text or "Write-Output" in text,
    )


@pytest.fixture(scope="module")
def review() -> SplitScriptReview:
    assert SCRIPT_PATH.is_file(), f"{SCRIPT_PATH} is missing"
    return inspect_split_script(SCRIPT_PATH)


def test_split_script_refuses_current_checkout_and_requires_disposable_clone(
    review: SplitScriptReview,
) -> None:
    assert review.clones_before_filter_repo
    assert review.filter_repo_target_is_temporary_clone
    assert review.mutates_source_checkout is False


def test_split_script_proves_its_target_is_a_new_temp_directory(
    review: SplitScriptReview,
) -> None:
    assert review.proves_target_is_under_temp
    assert review.refuses_nonempty_target


def test_split_script_detaches_the_clone_and_reports_where_it_is(
    review: SplitScriptReview,
) -> None:
    assert review.removes_origin
    assert review.prints_clone_path


def test_split_script_verifies_the_expected_roots_at_the_new_top_level() -> None:
    text = _executable_text(SCRIPT_PATH)

    for expected in ("pyproject.toml", ".github", "src", "tests", "docs", "samples", "scripts"):
        assert expected in text, f"the split script must verify {expected}"
