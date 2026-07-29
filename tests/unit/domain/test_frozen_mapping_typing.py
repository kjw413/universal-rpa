from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_frozen_mapping_factory_exposes_only_runtime_safe_value_types(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "frozen_mapping_contract.py"
    probe.write_text(
        dedent(
            """
            from collections.abc import Mapping
            from typing import assert_type

            from pydantic import BaseModel

            from universal_rpa.domain.types import (
                FrozenJsonObject,
                FrozenMapping,
                JsonValue,
            )


            class ExampleParameters(BaseModel):
                pass


            class_map: Mapping[str, type[BaseModel]] = {
                "example.action": ExampleParameters,
            }
            json_map: Mapping[str, JsonValue] = {
                "nested": {"items": ["safe"]},
            }
            mixed_map: Mapping[
                str,
                type[BaseModel] | list[str],
            ] = {
                "model": ExampleParameters,
                "items": ["safe"],
            }

            assert_type(
                FrozenMapping.from_mapping(class_map),
                FrozenMapping[str, type[BaseModel]],
            )
            assert_type(
                FrozenMapping.from_mapping(json_map),
                FrozenJsonObject,
            )
            assert_type(
                FrozenMapping.from_mapping(mixed_map),
                FrozenMapping[str, object],
            )
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--show-error-codes",
            str(probe),
        ],
        capture_output=True,
        check=False,
        cwd=PROJECT_ROOT,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
