"""Verify a read-only MIS pilot evidence bundle and emit a redacted summary.

This is the last gate before the MVP may be declared complete.  An operator runs
a supervised pilot against a real MIS on a real desktop, exports five safe
documents, and this script proves they describe *one* coherent run of *one*
externally-approved workflow on the expected OS.

Three design decisions are load-bearing:

* **The manifest is untrusted input.**  Every path is rejected before it is
  opened, never sanitized after.  Rejection is textual and platform-independent:
  the pilot runs on Windows but this code is tested everywhere, so a drive letter
  or UNC prefix must be refused on Linux too.  Trusting ``Path.is_absolute()``
  would pass the test suite and fail on the only machine that matters.

* **The policy is external.**  Required headers and the approved token arrive as
  digests from a file the operator controls separately from the bundle.  A bundle
  cannot assert its own correctness, and the script never copies an observed
  digest into the summary -- a digest in a committed file is just a hash waiting
  to be cracked against a small input space like a factory name.

* **Fail closed.**  Anything unreadable, oversized, ambiguous, or unrecognized is
  a failure and never a pass.  A missing check is indistinguishable from a passing
  one to a reader of the summary, so there is no ``N/A``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from itertools import permutations
from pathlib import Path, PurePosixPath
from typing import Any, Literal

ExpectedOs = Literal["windows-10-x64", "windows-11-x64"]

#: The five documents a complete bundle must contain, in supervised order.
REQUIRED_DOCUMENTS = (
    "validation_report",
    "step_test_report",
    "multi_run_report",
    "resume_report",
    "self_check_report",
)

#: A safe document is small. Anything larger is refused before it is parsed, so a
#: hostile bundle cannot exhaust memory during a sign-off.
MAXIMUM_DOCUMENT_BYTES = 10 * 1024 * 1024

#: The four checks a correct package self-check reports.
REQUIRED_SELF_CHECKS = (
    "workflow_schema_v1",
    "builtin_adapters",
    "app_data_write",
    "dpi_awareness",
)

#: Keys that must never appear in an exported document, at any depth. These are
#: the projector's redaction boundary restated as an assertion: if one shows up,
#: the export path regressed and the bundle is not safe to sign off or commit.
FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "clipboard_text",
        "clipboard_body",
        "credential",
        "exception",
        "password",
        "raw_message",
        "secret",
        "selector",
        "text",
        "token",
        "traceback",
        "uia_selector",
        "window_title",
    }
)

#: Digest-bearing keys are exempt from the absolute-path scan below: a 64-char hex
#: digest cannot be a path, and `token_sha256` is required evidence.
_DIGEST_FIELD_NAMES = frozenset({"headers_sha256", "sha256", "token_sha256"})

#: Above this many approved columns, enumerating orders stops being tractable
#: (8! = 40320 and it grows factorially), so the policy must pin the order.
_MAX_PERMUTED_HEADERS = 7

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

#: Windows device names, which name a device rather than a file even with a suffix.
_DEVICE_STEMS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{digit}" for digit in "123456789"}
    | {f"lpt{digit}" for digit in "123456789"}
)


@dataclass(frozen=True, slots=True)
class PilotPolicy:
    """Externally approved expectations, supplied independently of the bundle."""

    required_headers: frozenset[str]
    required_token_sha256: str
    minimum_rows: int
    allowed_output_root: Path
    workflow_revision: int
    #: Optional exact approved column order. The header digest covers an ordered
    #: row, so pinning the order makes the comparison exact instead of accepting
    #: any arrangement of the approved set. Required once the set grows past
    #: ``_MAX_PERMUTED_HEADERS``, where enumerating orders stops being tractable.
    required_header_order: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class PilotEvidenceBundle:
    validation_report: Path
    step_test_report: Path
    multi_run_report: Path
    resume_report: Path
    self_check_report: Path


@dataclass(frozen=True, slots=True)
class PilotGateResult:
    ok: bool
    failures: tuple[str, ...]
    app_version: str = ""
    observed_os: str = ""
    document_count: int = 0
    total_iterations: int = 0
    output_count: int = 0


@dataclass(slots=True)
class _Findings:
    """Accumulates failure codes, deduplicated and sorted on read."""

    codes: set[str] = field(default_factory=set)

    def add(self, code: str) -> None:
        self.codes.add(code)

    def sorted(self) -> tuple[str, ...]:
        return tuple(sorted(self.codes))


# --------------------------------------------------------------------------- #
# Path safety
# --------------------------------------------------------------------------- #


def _is_textually_unsafe(relative: str) -> bool:
    """Reject absolute, UNC, device, and traversing paths on every platform.

    ``Path`` semantics are platform-dependent, and the pilot runs on Windows while
    this function is tested on Linux.  So the check is textual: ``C:/x`` and
    ``//server/share`` must be refused here even though POSIX ``Path`` considers
    them ordinary relative names.
    """

    if not relative or relative.strip() != relative:
        return True
    normalized = relative.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("//"):
        return True
    # A drive letter, with or without a slash: `C:/x` and the equally dangerous
    # drive-relative `c:x`.
    if re.match(r"^[A-Za-z]:", normalized):
        return True
    segments = normalized.split("/")
    for segment in segments:
        if segment in {"", ".", ".."}:
            return True
        if segment.split(".")[0].casefold() in _DEVICE_STEMS:
            return True
    return False


def _is_reparse_point(path: Path) -> bool:
    """Whether *path* itself is a symlink or Windows reparse point."""

    try:
        stat_result = path.lstat()
    except OSError:
        return False
    reparse_attribute = getattr(stat_result, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(
        getattr(stat_result, "st_file_attributes", 0) & reparse_attribute
    )


def _resolve_evidence_path(relative: str, root: Path) -> Path | None:
    """Resolve *relative* beneath *root*, or ``None`` when it is not safe.

    Every intermediate segment is checked for a reparse point, so a document
    cannot be reached *through* a link even when the leaf is an ordinary file.
    """

    if _is_textually_unsafe(relative):
        return None
    if _is_reparse_point(root):
        return None

    current = root
    for segment in relative.replace("\\", "/").split("/"):
        current = current / segment
        if _is_reparse_point(current):
            return None

    try:
        resolved_root = root.resolve()
        resolved = (root / PurePosixPath(relative.replace("\\", "/"))).resolve()
        resolved.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    return resolved


# --------------------------------------------------------------------------- #
# Document loading
# --------------------------------------------------------------------------- #


def _load_document(path: Path, findings: _Findings) -> Mapping[str, Any] | None:
    try:
        size = path.stat().st_size
    except OSError:
        findings.add("document_unreadable")
        return None
    if size > MAXIMUM_DOCUMENT_BYTES:
        findings.add("document_too_large")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        findings.add("document_unreadable")
        return None
    if not isinstance(payload, dict):
        findings.add("document_unreadable")
        return None
    return payload


def _walk(value: Any, key: str | None = None) -> Iterator[tuple[str | None, Any]]:
    yield key, value
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from _walk(child, str(child_key))
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, key)


def _looks_absolute(text: str) -> bool:
    candidate = text.strip()
    if re.match(r"^[A-Za-z]:[\\/]", candidate):
        return True
    return candidate.startswith("\\\\") or candidate.startswith("//")


def _audit_redaction(document: Mapping[str, Any], findings: _Findings) -> None:
    """Reject a document that carries a secret, a body, or a customer path."""

    for key, value in _walk(document):
        if key is not None and key.casefold() in FORBIDDEN_FIELD_NAMES:
            findings.add("forbidden_field")
        if (
            isinstance(value, str)
            and _looks_absolute(value)
            and (key is None or key.casefold() not in _DIGEST_FIELD_NAMES)
        ):
            findings.add("absolute_path_value")


# --------------------------------------------------------------------------- #
# Cross-document checks
# --------------------------------------------------------------------------- #


def _single_value(documents: Mapping[str, Mapping[str, Any]], field_name: str) -> tuple[bool, str]:
    """Whether every document agrees on *field_name*, plus the first value seen."""

    observed = [str(document.get(field_name, "")) for document in documents.values()]
    if not observed:
        return False, ""
    return len(set(observed)) == 1, observed[0]


def _check_identity(
    documents: Mapping[str, Mapping[str, Any]],
    policy: PilotPolicy,
    expected_os: str,
    findings: _Findings,
) -> tuple[str, str]:
    consistent_workflow, _ = _single_value(documents, "workflow_id")
    if not consistent_workflow:
        findings.add("workflow_identity")

    consistent_revision, _ = _single_value(documents, "workflow_revision")
    if not consistent_revision:
        findings.add("workflow_identity")
    if any(
        int(document.get("workflow_revision", -1)) != policy.workflow_revision
        for document in documents.values()
    ):
        findings.add("workflow_revision")

    consistent_app, app_version = _single_value(documents, "app_version")
    if not consistent_app:
        findings.add("app_version")

    consistent_schema, _ = _single_value(documents, "report_schema_version")
    if not consistent_schema:
        findings.add("schema_version")

    consistent_fingerprint, _ = _single_value(documents, "environment_fingerprint")
    if not consistent_fingerprint:
        findings.add("environment_fingerprint")

    consistent_os, observed_os = _single_value(documents, "os")
    if not consistent_os or observed_os != expected_os:
        findings.add("expected_os")

    return app_version, observed_os


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _check_chronology(documents: Mapping[str, Mapping[str, Any]], findings: _Findings) -> None:
    run_ids = [str(document.get("run_id", "")) for document in documents.values()]
    if len(set(run_ids)) != len(run_ids):
        findings.add("duplicate_run_id")

    ordered = [
        _parse_timestamp(documents[name].get("started_at"))
        for name in REQUIRED_DOCUMENTS
        if name in documents
    ]
    if any(stamp is None for stamp in ordered):
        findings.add("run_chronology")
        return
    stamps = [stamp for stamp in ordered if stamp is not None]
    if stamps != sorted(stamps):
        findings.add("run_chronology")


def _check_validation(document: Mapping[str, Any], findings: _Findings) -> None:
    if document.get("status") != "success" or int(document.get("action_count", -1)) != 0:
        findings.add("validation_only")


def _check_step_test(document: Mapping[str, Any], policy: PilotPolicy, findings: _Findings) -> None:
    if document.get("status") != "success":
        findings.add("step_test_scope")
    if int(document.get("factory_count", -1)) != 1 or int(document.get("period_count", -1)) != 1:
        findings.add("step_test_scope")

    acceptable = _acceptable_header_digests(policy)
    if acceptable is None:
        # Distinct from `required_headers`: the columns may well be correct, but
        # the policy has not said which order counts and the set is too large to
        # enumerate. Reporting the header mismatch here would send the operator
        # looking at their MIS export when the fix is in the policy file.
        findings.add("header_order_unpinned")
    elif str(document.get("headers_sha256", "")) not in acceptable:
        findings.add("required_headers")

    if str(document.get("token_sha256", "")) != policy.required_token_sha256:
        findings.add("required_token")

    if int(document.get("row_count", 0)) < max(policy.minimum_rows, 1):
        findings.add("minimum_rows")


def canonical_header_hash(headers: Sequence[str]) -> str:
    """Mirror ``adapters.tabular.output.canonical_header_hash`` exactly."""

    payload = json.dumps(list(headers), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _acceptable_header_digests(policy: PilotPolicy) -> frozenset[str] | None:
    """Header digests for the approved column set, or ``None`` when unknowable.

    ``required_headers`` is a set, but the digest covers an *ordered* row, so the
    set alone cannot fix the order.  A policy that pins ``required_header_order``
    gets an exact one-digest comparison.  Otherwise, for a handful of approved
    columns, accept the digest of any permutation: still bounded, and it rejects
    a bundle whose columns differ at all.

    Past ``_MAX_PERMUTED_HEADERS`` neither is possible, and this returns ``None``
    rather than quietly falling back to alphabetical order.  That fallback would
    reject a perfectly good bundle from any MIS that does not happen to export
    its columns sorted, and would blame the headers for a gap in the policy.
    """

    if policy.required_header_order is not None:
        if set(policy.required_header_order) != set(policy.required_headers):
            return frozenset()
        return frozenset({canonical_header_hash(list(policy.required_header_order))})
    headers = sorted(policy.required_headers)
    if len(headers) > _MAX_PERMUTED_HEADERS:
        return None
    return frozenset(canonical_header_hash(list(order)) for order in permutations(headers))


def _check_outputs(document: Mapping[str, Any], policy: PilotPolicy, findings: _Findings) -> int:
    outputs = document.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        findings.add("output_commit_invalid")
        return 0

    for entry in outputs:
        if not isinstance(entry, dict):
            findings.add("output_commit_invalid")
            continue
        if entry.get("committed") is not True:
            findings.add("output_commit_invalid")
        for digest_field in ("sha256", "headers_sha256"):
            if not _SHA256_PATTERN.match(str(entry.get(digest_field, ""))):
                findings.add("output_commit_invalid")
        if int(entry.get("row_count", 0)) < max(policy.minimum_rows, 1):
            findings.add("minimum_rows")

        relative = str(entry.get("relative_path", ""))
        if _is_textually_unsafe(relative):
            findings.add("output_root_containment")
            continue
        combined = PurePosixPath(policy.allowed_output_root.as_posix()) / relative
        try:
            combined.relative_to(PurePosixPath(policy.allowed_output_root.as_posix()))
        except ValueError:  # pragma: no cover - guarded textually above
            findings.add("output_root_containment")

    return len(outputs)


def _cursor_list(document: Mapping[str, Any], key: str) -> list[str]:
    value = document.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _check_resume(
    multi_run: Mapping[str, Any], resume: Mapping[str, Any], findings: _Findings
) -> None:
    completed_before = _cursor_list(multi_run, "completed_cursors")
    completed_after = _cursor_list(resume, "completed_cursors")
    resumed_after = str(resume.get("resumed_after_cursor", ""))

    if not completed_before or resumed_after != completed_before[-1]:
        findings.add("resume_cursor")
    if set(completed_before) & set(completed_after):
        findings.add("duplicate_completed_cursor")
    if len(set(completed_after)) != len(completed_after):
        findings.add("duplicate_completed_cursor")


def _check_self_check(document: Mapping[str, Any], findings: _Findings) -> None:
    checks = document.get("checks")
    if not isinstance(checks, list):
        findings.add("package_self_check")
        return
    observed = {
        str(entry.get("name")): entry.get("ok") is True
        for entry in checks
        if isinstance(entry, dict)
    }
    if set(observed) != set(REQUIRED_SELF_CHECKS) or not all(observed.values()):
        findings.add("package_self_check")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def verify_pilot_bundle(
    bundle_path: Path,
    policy: PilotPolicy,
    expected_os: ExpectedOs,
) -> PilotGateResult:
    """Verify a five-document pilot bundle, failing closed on anything unexpected."""

    findings = _Findings()
    bundle_path = Path(bundle_path)

    try:
        manifest = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return PilotGateResult(ok=False, failures=("bundle_unreadable",))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("documents"), dict):
        return PilotGateResult(ok=False, failures=("bundle_unreadable",))

    root = bundle_path.parent
    declared: Mapping[str, Any] = manifest["documents"]

    resolved: dict[str, Path] = {}
    seen: dict[Path, str] = {}
    for name in REQUIRED_DOCUMENTS:
        relative = declared.get(name)
        if not isinstance(relative, str):
            findings.add(f"{name}_missing")
            continue
        path = _resolve_evidence_path(relative, root)
        if path is None:
            findings.add("unsafe_evidence_path")
            findings.add(f"{name}_missing")
            continue
        if path in seen:
            findings.add("duplicate_evidence_path")
        seen[path] = name
        resolved[name] = path

    documents: dict[str, Mapping[str, Any]] = {}
    for name, path in resolved.items():
        document = _load_document(path, findings)
        if document is None:
            findings.add(f"{name}_missing")
            continue
        _audit_redaction(document, findings)
        documents[name] = document

    if not documents:
        return PilotGateResult(ok=False, failures=findings.sorted())

    app_version, observed_os = _check_identity(documents, policy, expected_os, findings)
    _check_chronology(documents, findings)

    if "validation_report" in documents:
        _check_validation(documents["validation_report"], findings)
    if "step_test_report" in documents:
        _check_step_test(documents["step_test_report"], policy, findings)

    output_count = 0
    total_iterations = 0
    for name in ("multi_run_report", "resume_report"):
        if name in documents:
            output_count += _check_outputs(documents[name], policy, findings)
            total_iterations += len(_cursor_list(documents[name], "completed_cursors"))

    if "multi_run_report" in documents and "resume_report" in documents:
        _check_resume(documents["multi_run_report"], documents["resume_report"], findings)
    if "self_check_report" in documents:
        _check_self_check(documents["self_check_report"], findings)

    failures = findings.sorted()
    return PilotGateResult(
        ok=not failures,
        failures=failures,
        app_version=app_version,
        observed_os=observed_os,
        document_count=len(documents),
        total_iterations=total_iterations,
        output_count=output_count,
    )


def render_pilot_summary(result: PilotGateResult, expected_os: str) -> str:
    """Render a redacted Markdown sign-off record.

    Deliberately carries versions, the OS, counts, and pass/fail only.  No digest,
    token, workflow ID, or path is reproduced: this file is committed, and a digest
    over a small input space (a factory name, a period) is recoverable.
    """

    verdict = "PASS" if result.ok else "FAIL"
    lines = [
        f"# Read-only MIS pilot sign-off ({expected_os})",
        "",
        "Generated by `scripts/verify_mis_pilot_report.py`. This record intentionally",
        "contains no digest, token, workflow identifier, or path.",
        "",
        f"- Result: **{verdict}**",
        f"- Expected OS: `{expected_os}`",
        f"- Observed OS: `{result.observed_os or 'unknown'}`",
        f"- Application version: `{result.app_version or 'unknown'}`",
        f"- Evidence documents verified: {result.document_count} of {len(REQUIRED_DOCUMENTS)}",
        f"- Completed iterations across runs: {result.total_iterations}",
        f"- Verified output commits: {result.output_count}",
        "- Runtime LLM dependency: none (verified by build, not by this record)",
        "",
    ]
    if result.failures:
        lines.append("## Failures")
        lines.append("")
        lines.extend(f"- `{code}`" for code in result.failures)
    else:
        lines.append("All gate checks passed.")
    lines.append("")
    return "\n".join(lines)


def _load_policy(path: Path) -> PilotPolicy:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    order = payload.get("required_header_order")
    return PilotPolicy(
        required_headers=frozenset(payload["required_headers"]),
        required_token_sha256=str(payload["required_token_sha256"]).casefold(),
        minimum_rows=int(payload["minimum_rows"]),
        allowed_output_root=Path(payload["allowed_output_root"]),
        workflow_revision=int(payload["workflow_revision"]),
        required_header_order=tuple(str(item) for item in order) if order is not None else None,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a read-only MIS pilot bundle")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument(
        "--expected-os", choices=["windows-10-x64", "windows-11-x64"], required=True
    )
    parser.add_argument("--summary", type=Path, default=None)
    arguments = parser.parse_args(argv)

    try:
        policy = _load_policy(arguments.policy)
    except (OSError, ValueError, KeyError):
        print("pilot gate: the policy file could not be read")
        return 2

    expected_os: ExpectedOs = arguments.expected_os
    result = verify_pilot_bundle(arguments.bundle, policy, expected_os)

    if arguments.summary is not None:
        arguments.summary.parent.mkdir(parents=True, exist_ok=True)
        arguments.summary.write_text(render_pilot_summary(result, expected_os), encoding="utf-8")

    if result.ok:
        print(f"pilot gate: PASS ({expected_os})")
        return 0
    print(f"pilot gate: FAIL ({expected_os})")
    for code in result.failures:
        print(f"  {code}")
    return 1


__all__ = [
    "FORBIDDEN_FIELD_NAMES",
    "MAXIMUM_DOCUMENT_BYTES",
    "REQUIRED_DOCUMENTS",
    "REQUIRED_SELF_CHECKS",
    "PilotEvidenceBundle",
    "PilotGateResult",
    "PilotPolicy",
    "canonical_header_hash",
    "render_pilot_summary",
    "verify_pilot_bundle",
]


if __name__ == "__main__":
    raise SystemExit(main())
