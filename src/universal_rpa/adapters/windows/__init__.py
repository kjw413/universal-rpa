"""Windows input-recording adapters with injectable native boundaries."""

from universal_rpa.adapters.windows.capture import PynputInputCapture
from universal_rpa.adapters.windows.context import (
    ContextConfirmationBarrier,
    FocusContextWatcher,
    UiaFocusCache,
    WindowsWindowContext,
    capture_target_snapshot,
)
from universal_rpa.adapters.windows.dpi import enable_per_monitor_v2_dpi_awareness
from universal_rpa.adapters.windows.window_catalog import (
    ClientGeometry,
    PyWin32WindowFacade,
    Win32WindowCatalog,
    WindowDescriptor,
)

__all__ = [
    "ClientGeometry",
    "ContextConfirmationBarrier",
    "FocusContextWatcher",
    "PyWin32WindowFacade",
    "PynputInputCapture",
    "UiaFocusCache",
    "Win32WindowCatalog",
    "WindowDescriptor",
    "WindowsWindowContext",
    "capture_target_snapshot",
    "enable_per_monitor_v2_dpi_awareness",
]
