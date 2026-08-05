from __future__ import annotations

import ast
import json
import subprocess
import sys
from importlib.metadata import metadata
from pathlib import Path
from sys import stdlib_module_names

from packaging.specifiers import SpecifierSet

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_IMPORT_ROOTS = stdlib_module_names | {
    "PySide6",
    "comtypes",
    "openpyxl",
    "pydantic",
    "pynput",
    "pywinauto",
    "pythoncom",
    "typing_extensions",
    "universal_rpa",
    "win32api",
    "win32con",
    "win32cred",
    "win32event",
    "win32file",
    "win32gui",
    "win32process",
    "win32security",
}


def test_package_exposes_schema_major_and_version() -> None:
    import universal_rpa

    assert universal_rpa.WORKFLOW_SCHEMA_MAJOR == "1"
    assert universal_rpa.__version__ == "0.1.0"


def test_root_import_is_lightweight_and_resolves_only_to_this_project() -> None:
    probe = """
import json
import sys
from pathlib import Path

import universal_rpa

forbidden = {
    "PySide6", "pywinauto", "_common", "production_daily_rpa",
    "utility_daily_rpa", "wip_daily_rpa",
}
print(json.dumps({
    "module_file": str(Path(universal_rpa.__file__).resolve()),
    "loaded_forbidden_modules": sorted(forbidden & sys.modules.keys()),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        check=False,
        cwd=PROJECT_ROOT,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["module_file"]).is_relative_to(
        (PROJECT_ROOT / "src" / "universal_rpa").resolve()
    )
    assert payload["loaded_forbidden_modules"] == []


def test_source_import_graph_has_no_undeclared_parent_modules() -> None:
    for source in (PROJECT_ROOT / "src" / "universal_rpa").rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.partition(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = {node.module.partition(".")[0]}
            else:
                continue
            assert roots <= ALLOWED_IMPORT_ROOTS, (source, roots - ALLOWED_IMPORT_ROOTS)


def test_supported_python_range_is_exact() -> None:
    assert SpecifierSet(metadata("universal-rpa-studio")["Requires-Python"]) == SpecifierSet(
        ">=3.12,<3.14"
    )
