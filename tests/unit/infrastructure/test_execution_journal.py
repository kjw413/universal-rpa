from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from universal_rpa.infrastructure.execution_journal import (
    InProgressAction,
    InProgressIterationJournal,
    JsonExecutionJournalStore,
)


def test_journal_roundtrip_contains_only_resume_safety_metadata(tmp_path: Path) -> None:
    workflow_id = UUID("00000000-0000-0000-0000-000000000911")
    run_id = UUID("00000000-0000-0000-0000-000000000912")
    now = datetime(2026, 8, 3, tzinfo=UTC)
    journal = InProgressIterationJournal(
        workflow_id=workflow_id,
        run_id=run_id,
        cursor=(),
        actions=(
            InProgressAction(
                step_id=UUID("00000000-0000-0000-0000-000000000913"),
                action_type="windows.click",
                idempotent=False,
                state="inflight",
            ),
        ),
        started_at=now,
        updated_at=now,
    )
    store = JsonExecutionJournalStore(tmp_path)

    store.save(journal)
    loaded = store.load(workflow_id, run_id)
    encoded = journal.model_dump_json()

    assert loaded == journal
    assert set(json.loads(encoded)) == {
        "journal_schema_version",
        "workflow_id",
        "run_id",
        "cursor",
        "actions",
        "started_at",
        "updated_at",
    }
    assert "password" not in encoded.casefold()
    assert "target" not in encoded.casefold()
    assert "variable" not in encoded.casefold()
