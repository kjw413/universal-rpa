from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from universal_rpa.application.workflow_codec import export_workflow_schema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "docs" / "schemas" / "workflow-v1.schema.json"


def test_exported_schema_snapshot_matches_public_model_behavior() -> None:
    generated = export_workflow_schema()

    assert generated.endswith(b"\n")
    assert SCHEMA_PATH.read_bytes() == generated


def test_schema_export_check_compares_bytes() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/export_schema.py", "--check"],
        capture_output=True,
        check=False,
        cwd=PROJECT_ROOT,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
