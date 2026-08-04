"""Gate tests for the read-only MIS pilot evidence verifier.

The verifier is the last gate before the MVP may be declared complete, so it is
adversarial by construction.  It reads five safe documents an operator exported
from a supervised pilot and proves they describe *one* coherent run of *one*
approved workflow on the expected OS -- without ever trusting the bundle to point
it at a file, and without copying an observed digest into the summary it writes.

Two properties matter more than the individual checks:

* Every path in the manifest is attacker-controlled input.  A bundle that escapes
  its own directory, or reaches a file through a link, is rejected before it is
  opened -- not sanitized afterwards.
* Path rejection is textual and platform-independent.  The pilot runs on Windows
  but these tests run everywhere, so a drive letter or UNC prefix must be refused
  on Linux too; relying on ``Path.is_absolute()`` would silently pass here and
  only fail on the machine that matters.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from scripts.verify_mis_pilot_report import (
    MAXIMUM_DOCUMENT_BYTES,
    PilotPolicy,
    render_pilot_summary,
    verify_pilot_bundle,
)

WORKFLOW_ID = "3f3f0f4a-0000-4000-8000-00000000a001"
APP_VERSION = "0.1.0"
WORKFLOW_REVISION = 3
FINGERPRINT = "a" * 64
APPROVED_TOKEN = "APPROVED-2026-07"
REQUIRED_HEADERS = ("공장", "기간", "생산수량")
ALLOWED_OUTPUT_ROOT = "C:/UniversalRPA-Pilot/output"

DOCUMENT_NAMES = (
    "validation_report",
    "step_test_report",
    "multi_run_report",
    "resume_report",
    "self_check_report",
)


def canonical_header_digest(headers: Sequence[str]) -> str:
    """Mirror ``adapters.tabular.output.canonical_header_hash`` exactly."""

    payload = json.dumps(list(headers), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def pilot_policy(**overrides: Any) -> PilotPolicy:
    fields: dict[str, Any] = {
        "required_headers": frozenset(REQUIRED_HEADERS),
        "required_token_sha256": token_digest(APPROVED_TOKEN),
        "minimum_rows": 1,
        "allowed_output_root": Path(ALLOWED_OUTPUT_ROOT),
        "workflow_revision": WORKFLOW_REVISION,
    }
    fields.update(overrides)
    return PilotPolicy(**fields)


def _started(minutes: int) -> str:
    return (datetime(2026, 8, 4, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes)).isoformat()


def _common(kind: str, minutes: int, run_suffix: str) -> dict[str, Any]:
    return {
        "report_schema_version": "1",
        "kind": kind,
        "app_version": APP_VERSION,
        "os": "windows-11-x64",
        "environment_fingerprint": FINGERPRINT,
        "workflow_id": WORKFLOW_ID,
        "workflow_revision": WORKFLOW_REVISION,
        "run_id": f"3f3f0f4a-0000-4000-8000-0000000{run_suffix}",
        "status": "success",
        "started_at": _started(minutes),
        "finished_at": _started(minutes + 1),
        "action_count": 4,
    }


def _output(relative_path: str = "실적/2026-07.csv", row_count: int = 20) -> dict[str, Any]:
    return {
        "relative_path": relative_path,
        "format": "csv",
        "sheet_name": None,
        "row_count": row_count,
        "sha256": "b" * 64,
        "headers_sha256": canonical_header_digest(REQUIRED_HEADERS),
        "committed": True,
    }


def validation_document() -> dict[str, Any]:
    return _common("validation", 0, "00b001") | {"action_count": 0}


def step_test_document() -> dict[str, Any]:
    return _common("step_test", 10, "00b002") | {
        "action_count": 1,
        "factory_count": 1,
        "period_count": 1,
        "row_count": 20,
        "headers_sha256": canonical_header_digest(REQUIRED_HEADERS),
        "token_sha256": token_digest(APPROVED_TOKEN),
    }


def multi_run_document() -> dict[str, Any]:
    return _common("multi_run", 20, "00b003") | {
        "outputs": [_output()],
        "completed_cursors": ["loop#0", "loop#1", "loop#2"],
    }


def resume_document() -> dict[str, Any]:
    return _common("resume", 30, "00b004") | {
        "outputs": [_output()],
        "resumed_after_cursor": "loop#2",
        "completed_cursors": ["loop#3", "loop#4"],
    }


def self_check_document() -> dict[str, Any]:
    return _common("self_check", 40, "00b005") | {
        "action_count": 0,
        "checks": [
            {"name": "workflow_schema_v1", "ok": True, "detail": "1"},
            {"name": "builtin_adapters", "ok": True, "detail": "windows, clipboard, tabular"},
            {"name": "app_data_write", "ok": True, "detail": "atomic write and delete"},
            {"name": "dpi_awareness", "ok": True, "detail": "per-monitor-v2"},
        ],
    }


def default_documents() -> dict[str, dict[str, Any]]:
    return {
        "validation_report": validation_document(),
        "step_test_report": step_test_document(),
        "multi_run_report": multi_run_document(),
        "resume_report": resume_document(),
        "self_check_report": self_check_document(),
    }


def _is_plain_relative(relative: str) -> bool:
    """Whether *relative* is safe for this test helper to actually create."""

    normalized = relative.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return False
    return ".." not in normalized.split("/") and normalized.split(".")[0].upper() != "CON"


def write_bundle(
    root: Path,
    *,
    documents: Mapping[str, dict[str, Any]] | None = None,
    omit: str | None = None,
    paths: Mapping[str, str] | None = None,
    manifest_overrides: Mapping[str, Any] | None = None,
) -> Path:
    """Write a manifest plus its evidence documents and return the manifest path."""

    root.mkdir(parents=True, exist_ok=True)
    payload = dict(documents or default_documents())
    relative = {name: f"{name.replace('_', '-')}.json" for name in DOCUMENT_NAMES}
    relative.update(paths or {})

    for name, document in payload.items():
        if name == omit:
            continue
        destination = root / relative[name]
        # An escape path is only ever *declared*, never created: the verifier must
        # reject it from the manifest text alone, before it touches the filesystem.
        # Trying to materialize `/etc/report.json` would test the sandbox, not the
        # verifier.
        if not _is_plain_relative(relative[name]):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    manifest: dict[str, Any] = {
        "bundle_schema_version": "1",
        "app_version": APP_VERSION,
        "os": "windows-11-x64",
        "environment_fingerprint": FINGERPRINT,
        "workflow_id": WORKFLOW_ID,
        "documents": {name: relative[name] for name in DOCUMENT_NAMES if name != omit},
    }
    manifest.update(manifest_overrides or {})
    manifest_path = root / "pilot-bundle.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest_path


MUTATIONS: dict[str, tuple[str, str, Any]] = {
    "header_hash": ("step_test_report", "headers_sha256", "c" * 64),
    "token_hash": ("step_test_report", "token_sha256", "d" * 64),
    "row_count_zero": ("step_test_report", "row_count", 0),
    "validation_action_count": ("validation_report", "action_count", 7),
}


def write_complete_bundle(root: Path, *, mutation: str | None = None) -> Path:
    documents = default_documents()
    if mutation == "self_check_false":
        documents["self_check_report"]["checks"][3]["ok"] = False
    elif mutation is not None:
        name, field, value = MUTATIONS[mutation]
        documents[name][field] = value
    return write_bundle(root, documents=documents)


# --------------------------------------------------------------------------- #
# Bundle completeness
# --------------------------------------------------------------------------- #


def test_a_complete_bundle_passes_every_gate(tmp_path: Path) -> None:
    result = verify_pilot_bundle(write_complete_bundle(tmp_path), pilot_policy(), "windows-11-x64")

    assert result.failures == ()
    assert result.ok


@pytest.mark.parametrize("omitted", DOCUMENT_NAMES)
def test_bundle_requires_all_five_distinct_documents(tmp_path: Path, omitted: str) -> None:
    bundle = write_bundle(tmp_path, omit=omitted)

    result = verify_pilot_bundle(bundle, pilot_policy(), "windows-11-x64")

    assert not result.ok
    assert f"{omitted}_missing" in result.failures


def test_two_names_pointing_at_one_file_is_not_five_documents(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, paths={"resume_report": "multi-run-report.json"})

    result = verify_pilot_bundle(bundle, pilot_policy(), "windows-11-x64")

    assert not result.ok
    assert "duplicate_evidence_path" in result.failures


# --------------------------------------------------------------------------- #
# Cross-document evidence
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("mutation", "failure"),
    [
        ("header_hash", "required_headers"),
        ("token_hash", "required_token"),
        ("row_count_zero", "minimum_rows"),
        ("validation_action_count", "validation_only"),
        ("self_check_false", "package_self_check"),
    ],
)
def test_bundle_checks_header_token_rows_and_each_evidence(
    tmp_path: Path, mutation: str, failure: str
) -> None:
    bundle = write_complete_bundle(tmp_path, mutation=mutation)

    result = verify_pilot_bundle(bundle, pilot_policy(), "windows-11-x64")

    assert not result.ok
    assert failure in result.failures


def test_a_validation_run_that_did_not_succeed_is_not_a_pass(tmp_path: Path) -> None:
    documents = default_documents()
    documents["validation_report"]["status"] = "failed"

    result = verify_pilot_bundle(
        write_bundle(tmp_path, documents=documents), pilot_policy(), "windows-11-x64"
    )

    assert "validation_only" in result.failures


@pytest.mark.parametrize(("field", "value"), [("factory_count", 2), ("period_count", 3)])
def test_step_test_must_cover_exactly_one_factory_and_one_period(
    tmp_path: Path, field: str, value: int
) -> None:
    documents = default_documents()
    documents["step_test_report"][field] = value

    result = verify_pilot_bundle(
        write_bundle(tmp_path, documents=documents), pilot_policy(), "windows-11-x64"
    )

    assert "step_test_scope" in result.failures


def test_workflow_identity_must_be_the_same_across_every_document(tmp_path: Path) -> None:
    documents = default_documents()
    documents["resume_report"]["workflow_id"] = "3f3f0f4a-0000-4000-8000-00000000ffff"

    result = verify_pilot_bundle(
        write_bundle(tmp_path, documents=documents), pilot_policy(), "windows-11-x64"
    )

    assert "workflow_identity" in result.failures


def test_workflow_revision_must_match_the_externally_approved_revision(tmp_path: Path) -> None:
    result = verify_pilot_bundle(
        write_complete_bundle(tmp_path), pilot_policy(workflow_revision=9), "windows-11-x64"
    )

    assert "workflow_revision" in result.failures


@pytest.mark.parametrize(
    ("field", "failure"),
    [
        ("app_version", "app_version"),
        ("environment_fingerprint", "environment_fingerprint"),
        ("report_schema_version", "schema_version"),
    ],
)
def test_one_pilot_means_one_build_and_one_machine(
    tmp_path: Path, field: str, failure: str
) -> None:
    documents = default_documents()
    documents["multi_run_report"][field] = "9"

    result = verify_pilot_bundle(
        write_bundle(tmp_path, documents=documents), pilot_policy(), "windows-11-x64"
    )

    assert failure in result.failures


def test_expected_os_must_match_every_document(tmp_path: Path) -> None:
    result = verify_pilot_bundle(write_complete_bundle(tmp_path), pilot_policy(), "windows-10-x64")

    assert not result.ok
    assert "expected_os" in result.failures


def test_runs_must_be_chronological(tmp_path: Path) -> None:
    documents = default_documents()
    documents["resume_report"]["started_at"] = _started(-5)

    result = verify_pilot_bundle(
        write_bundle(tmp_path, documents=documents), pilot_policy(), "windows-11-x64"
    )

    assert "run_chronology" in result.failures


def test_each_document_must_describe_a_distinct_run(tmp_path: Path) -> None:
    documents = default_documents()
    documents["resume_report"]["run_id"] = documents["multi_run_report"]["run_id"]

    result = verify_pilot_bundle(
        write_bundle(tmp_path, documents=documents), pilot_policy(), "windows-11-x64"
    )

    assert "duplicate_run_id" in result.failures


# --------------------------------------------------------------------------- #
# Output containment and resume safety
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "relative_path",
    ["../escape.csv", "C:/elsewhere/out.csv", "//server/share/out.csv", "/absolute/out.csv"],
)
def test_every_output_must_sit_beneath_the_approved_output_root(
    tmp_path: Path, relative_path: str
) -> None:
    documents = default_documents()
    documents["multi_run_report"]["outputs"] = [_output(relative_path=relative_path)]

    result = verify_pilot_bundle(
        write_bundle(tmp_path, documents=documents), pilot_policy(), "windows-11-x64"
    )

    assert "output_root_containment" in result.failures


def test_an_uncommitted_output_is_not_evidence_of_an_output(tmp_path: Path) -> None:
    documents = default_documents()
    output = _output() | {"committed": False}
    documents["multi_run_report"]["outputs"] = [output]

    result = verify_pilot_bundle(
        write_bundle(tmp_path, documents=documents), pilot_policy(), "windows-11-x64"
    )

    assert "output_commit_invalid" in result.failures


@pytest.mark.parametrize("digest_field", ["sha256", "headers_sha256"])
def test_an_output_digest_must_be_a_real_sha256(tmp_path: Path, digest_field: str) -> None:
    documents = default_documents()
    documents["multi_run_report"]["outputs"] = [_output() | {digest_field: "not-a-digest"}]

    result = verify_pilot_bundle(
        write_bundle(tmp_path, documents=documents), pilot_policy(), "windows-11-x64"
    )

    assert "output_commit_invalid" in result.failures


def test_resume_must_start_after_the_last_completed_cursor(tmp_path: Path) -> None:
    documents = default_documents()
    documents["resume_report"]["resumed_after_cursor"] = "loop#0"

    result = verify_pilot_bundle(
        write_bundle(tmp_path, documents=documents), pilot_policy(), "windows-11-x64"
    )

    assert "resume_cursor" in result.failures


def test_resume_may_never_replay_a_completed_iteration(tmp_path: Path) -> None:
    documents = default_documents()
    documents["resume_report"]["completed_cursors"] = ["loop#2", "loop#3"]

    result = verify_pilot_bundle(
        write_bundle(tmp_path, documents=documents), pilot_policy(), "windows-11-x64"
    )

    assert "duplicate_completed_cursor" in result.failures


# --------------------------------------------------------------------------- #
# Path safety: the manifest is untrusted input
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "escape",
    [
        "../report.json",
        "C:/other/report.json",
        "c:report.json",
        "//server/share/report.json",
        "\\\\server\\share\\report.json",
        "/etc/report.json",
        "nested/../../report.json",
        "CON",
        "nul.json",
    ],
)
def test_bundle_paths_must_be_regular_files_beneath_evidence_root(
    tmp_path: Path, escape: str
) -> None:
    bundle = write_bundle(tmp_path, paths={"validation_report": escape})

    result = verify_pilot_bundle(bundle, pilot_policy(), "windows-10-x64")

    assert not result.ok
    assert "unsafe_evidence_path" in result.failures


def test_a_document_reached_through_a_link_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    bundle_root = tmp_path / "bundle"
    write_bundle(bundle_root)
    (bundle_root / "validation-report.json").replace(outside / "validation-report.json")
    try:
        (bundle_root / "validation-report.json").symlink_to(outside / "validation-report.json")
    except (OSError, NotImplementedError):  # pragma: no cover - unprivileged Windows
        pytest.skip("symlink creation is unavailable")

    result = verify_pilot_bundle(
        bundle_root / "pilot-bundle.json", pilot_policy(), "windows-11-x64"
    )

    assert not result.ok
    assert "unsafe_evidence_path" in result.failures


def test_a_directory_is_not_an_evidence_document(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    write_bundle(bundle_root)
    (bundle_root / "validation-report.json").unlink()
    (bundle_root / "validation-report.json").mkdir()

    result = verify_pilot_bundle(
        bundle_root / "pilot-bundle.json", pilot_policy(), "windows-11-x64"
    )

    assert "unsafe_evidence_path" in result.failures


def test_an_oversized_document_is_refused_without_being_parsed(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    bundle = write_bundle(bundle_root)
    padded = validation_document() | {"padding": "x" * (MAXIMUM_DOCUMENT_BYTES + 1)}
    (bundle_root / "validation-report.json").write_text(json.dumps(padded), encoding="utf-8")

    result = verify_pilot_bundle(bundle, pilot_policy(), "windows-11-x64")

    assert not result.ok
    assert "document_too_large" in result.failures


def test_a_manifest_that_is_not_readable_json_fails_closed(tmp_path: Path) -> None:
    manifest = tmp_path / "pilot-bundle.json"
    manifest.write_text("{not json", encoding="utf-8")

    result = verify_pilot_bundle(manifest, pilot_policy(), "windows-11-x64")

    assert not result.ok
    assert "bundle_unreadable" in result.failures


def test_a_missing_manifest_fails_closed(tmp_path: Path) -> None:
    result = verify_pilot_bundle(tmp_path / "absent.json", pilot_policy(), "windows-11-x64")

    assert not result.ok
    assert "bundle_unreadable" in result.failures


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "field",
    [
        "clipboard_text",
        "password",
        "secret",
        "token",
        "text",
        "selector",
        "uia_selector",
        "window_title",
        "raw_message",
        "exception",
    ],
)
def test_a_document_carrying_a_forbidden_field_is_refused(tmp_path: Path, field: str) -> None:
    documents = default_documents()
    documents["multi_run_report"][field] = "leaked"

    result = verify_pilot_bundle(
        write_bundle(tmp_path, documents=documents), pilot_policy(), "windows-11-x64"
    )

    assert not result.ok
    assert "forbidden_field" in result.failures


def test_a_forbidden_field_nested_anywhere_is_still_refused(tmp_path: Path) -> None:
    documents = default_documents()
    documents["multi_run_report"]["outputs"][0]["evidence"] = {
        "rows": [{"clipboard_text": "고객 실적"}]
    }

    result = verify_pilot_bundle(
        write_bundle(tmp_path, documents=documents), pilot_policy(), "windows-11-x64"
    )

    assert "forbidden_field" in result.failures


@pytest.mark.parametrize(
    "absolute", ["C:\\Users\\operator\\실적.csv", "C:/Program Files/MIS/mis.exe", "//mis/share"]
)
def test_an_absolute_customer_path_anywhere_is_refused(tmp_path: Path, absolute: str) -> None:
    documents = default_documents()
    documents["multi_run_report"]["note"] = absolute

    result = verify_pilot_bundle(
        write_bundle(tmp_path, documents=documents), pilot_policy(), "windows-11-x64"
    )

    assert not result.ok
    assert "absolute_path_value" in result.failures


# --------------------------------------------------------------------------- #
# The generated summary is itself an artifact that must stay safe
# --------------------------------------------------------------------------- #


def test_summary_records_versions_os_counts_and_pass_fail(tmp_path: Path) -> None:
    result = verify_pilot_bundle(write_complete_bundle(tmp_path), pilot_policy(), "windows-11-x64")

    summary = render_pilot_summary(result, "windows-11-x64")

    assert "windows-11-x64" in summary
    assert APP_VERSION in summary
    assert "PASS" in summary


def test_summary_never_copies_an_observed_digest_or_a_token(tmp_path: Path) -> None:
    """The summary is committed to the repository; a digest there is a hash to crack."""

    result = verify_pilot_bundle(write_complete_bundle(tmp_path), pilot_policy(), "windows-11-x64")

    summary = render_pilot_summary(result, "windows-11-x64")

    assert APPROVED_TOKEN not in summary
    assert token_digest(APPROVED_TOKEN) not in summary
    assert canonical_header_digest(REQUIRED_HEADERS) not in summary
    assert "b" * 64 not in summary
    assert WORKFLOW_ID not in summary
    assert UUID(WORKFLOW_ID).hex not in summary


def test_a_failing_bundle_summary_names_the_failures_it_found(tmp_path: Path) -> None:
    result = verify_pilot_bundle(
        write_complete_bundle(tmp_path, mutation="token_hash"), pilot_policy(), "windows-11-x64"
    )

    summary = render_pilot_summary(result, "windows-11-x64")

    assert "FAIL" in summary
    assert "required_token" in summary


def test_failures_are_reported_sorted_and_deduplicated(tmp_path: Path) -> None:
    documents = default_documents()
    documents["multi_run_report"]["password"] = "leaked"
    documents["resume_report"]["password"] = "leaked"

    result = verify_pilot_bundle(
        write_bundle(tmp_path, documents=documents), pilot_policy(), "windows-11-x64"
    )

    assert result.failures == tuple(sorted(set(result.failures)))
