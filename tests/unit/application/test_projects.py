from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from universal_rpa.application.projects import ProjectBoundaryError, ProjectService


def test_create_uses_only_the_portable_project_layout(tmp_path: Path) -> None:
    session = ProjectService().create(tmp_path / "project", "테스트")

    assert {path.name for path in session.project_dir.iterdir()} == {
        "workflow.json",
        "targets",
        "inputs",
    }
    assert session.loaded_revision == 1
    assert not session.dirty


def test_import_input_file_copies_verified_bytes_under_inputs(tmp_path: Path) -> None:
    source = tmp_path / "외부 자료.csv"
    payload = b"factory\nA\n"
    source.write_bytes(payload)
    session = ProjectService().create(tmp_path / "project", "테스트")

    relative = ProjectService().import_input_file(session, source)
    imported = session.project_dir / Path(relative.root)

    assert relative.root == f"inputs/{hashlib.sha256(payload).hexdigest()[:12]}-외부_자료.csv"
    assert imported.read_bytes() == payload
    assert not tuple((session.project_dir / "inputs").glob(".import-*.tmp"))


def test_import_rejects_unsupported_or_linked_source(tmp_path: Path) -> None:
    session = ProjectService().create(tmp_path / "project", "테스트")
    unsupported = tmp_path / "input.txt"
    unsupported.write_text("not tabular", encoding="utf-8")
    with pytest.raises(ProjectBoundaryError, match="CSV 또는 XLSX"):
        ProjectService().import_input_file(session, unsupported)

    source = tmp_path / "source.csv"
    source.write_text("value\nA\n", encoding="utf-8")
    linked = tmp_path / "linked.csv"
    try:
        linked.symlink_to(source)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows symbolic-link privilege is unavailable")
        raise
    with pytest.raises(ProjectBoundaryError, match="연결된"):
        ProjectService().import_input_file(session, linked)


def test_save_preserves_loaded_revision_for_optimistic_locking(tmp_path: Path) -> None:
    service = ProjectService()
    session = service.create(tmp_path / "project", "테스트")
    changed = service.with_workflow(
        session,
        session.workflow.model_copy(update={"name": "변경"}),
    )

    saved = service.save(changed)

    assert saved.workflow.name == "변경"
    assert saved.loaded_revision == 2
    assert not saved.dirty
