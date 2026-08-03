from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from universal_rpa.domain.results import OutputCommit
from universal_rpa.infrastructure.checkpoint_store import (
    Checkpoint,
    JsonCheckpointStore,
    ResumeFingerprint,
    TerminalRunRecord,
)

WORKFLOW_ID = UUID("00000000-0000-0000-0000-000000000908")
RUN_ID = UUID("00000000-0000-0000-0000-000000000909")
STEP_ID = UUID("00000000-0000-0000-0000-000000000910")
NOW = datetime(2026, 8, 3, tzinfo=UTC)


def _fingerprint() -> ResumeFingerprint:
    digest = "0" * 64
    return ResumeFingerprint(
        workflow_sha256=digest,
        resolved_inputs_sha256=digest,
        output_root_sha256=digest,
        data_sources=(),
        adapters=(),
        environment_sha256=digest,
    )


def _commit(destination: Path, digest: str) -> OutputCommit:
    return OutputCommit(
        destination=destination,
        format="csv",
        sheet_name=None,
        row_count=1,
        sha256=digest * 64,
        headers_sha256="a" * 64,
        committed=True,
        producer_step_id=STEP_ID,
    )


def test_checkpoint_keeps_latest_commit_and_terminal_is_not_discoverable(
    tmp_path: Path,
) -> None:
    store = JsonCheckpointStore(tmp_path)
    destination = tmp_path / "result.csv"
    checkpoint = Checkpoint(
        workflow_id=WORKFLOW_ID,
        run_id=RUN_ID,
        date_context_today="2026-08-03",
        date_context_run_date="2026-08-03",
        fingerprint=_fingerprint(),
        output_commits=(_commit(destination, "1"), _commit(destination, "2")),
        updated_at=NOW,
    )

    store.save_active(checkpoint)
    loaded = store.load_active(WORKFLOW_ID, RUN_ID)

    assert len(loaded.output_commits) == 1
    assert loaded.output_commits[0].sha256 == "2" * 64
    assert store.discover_active(WORKFLOW_ID) == (loaded,)

    store.mark_terminal(
        TerminalRunRecord(
            workflow_id=WORKFLOW_ID,
            run_id=RUN_ID,
            status="success",
            finished_at=NOW,
        )
    )

    assert store.discover_active(WORKFLOW_ID) == ()
    assert tuple(tmp_path.rglob("*.terminal.json"))
