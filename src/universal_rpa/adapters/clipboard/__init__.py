"""Safe clipboard extraction adapter."""

from .adapter import CLIPBOARD_ADAPTER_VERSION, ClipboardAutomationAdapter, ClipboardSnapshot
from .table_parser import parse_clipboard_table

__all__ = [
    "CLIPBOARD_ADAPTER_VERSION",
    "ClipboardAutomationAdapter",
    "ClipboardSnapshot",
    "parse_clipboard_table",
]
