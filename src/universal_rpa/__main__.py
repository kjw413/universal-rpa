"""Entry point for both the Studio GUI and the two packaged verification modes."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from universal_rpa.ui.app import main as gui_main

SELF_CHECK_FLAG = "--self-check"
PACKAGED_SMOKE_FLAG = "--packaged-smoke"

_USAGE = "usage: UniversalRPAStudio.exe [--self-check | --packaged-smoke <empty-temp-root>]"


def _run_self_check() -> int:
    from universal_rpa.self_check import run_self_check

    report = run_self_check()
    print(report.to_json())
    return 0 if report.ok else 1


def _run_packaged_smoke(argument: str | None) -> int:
    from universal_rpa.packaged_smoke import SmokeRejected, run_packaged_smoke

    if argument is None:
        print(_USAGE, file=sys.stderr)
        return 2
    try:
        report = run_packaged_smoke(Path(argument))
    except SmokeRejected as rejection:
        # Only the reason is printed; the rejected path is never echoed back.
        print(f"packaged smoke refused: {rejection}", file=sys.stderr)
        return 1
    print(report.to_json())
    return 0 if report.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if SELF_CHECK_FLAG in arguments:
        return _run_self_check()
    if PACKAGED_SMOKE_FLAG in arguments:
        index = arguments.index(PACKAGED_SMOKE_FLAG)
        following = arguments[index + 1] if index + 1 < len(arguments) else None
        return _run_packaged_smoke(following)
    return gui_main(argv)


__all__ = ["PACKAGED_SMOKE_FLAG", "SELF_CHECK_FLAG", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
