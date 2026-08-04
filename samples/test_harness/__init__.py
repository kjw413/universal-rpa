"""A deterministic, accessible Windows target used by the interactive E2E suite.

The harness exists so the Windows runner can be exercised against real UIA
elements and real native input without ever touching a customer application.
Every control has a stable ``AutomationId`` and accessible name, every observable
effect is a counter or a fixed synthetic string, and nothing it records is
user-derived.
"""

from samples.test_harness.app import HarnessConfig, create_harness_window, main
from samples.test_harness.state import HarnessState

__all__ = ["HarnessConfig", "HarnessState", "create_harness_window", "main"]
