"""Local tabular input providers and the output-only automation adapter."""

from universal_rpa.adapters.tabular.adapter import (
    TABULAR_ADAPTER_VERSION,
    TabularAutomationAdapter,
)
from universal_rpa.adapters.tabular.data_sources import TabularDataSourceProvider
from universal_rpa.adapters.tabular.output import (
    AtomicTableWriter,
    TableOutputSpec,
    canonical_header_hash,
)

__all__ = [
    "TABULAR_ADAPTER_VERSION",
    "AtomicTableWriter",
    "TableOutputSpec",
    "TabularAutomationAdapter",
    "TabularDataSourceProvider",
    "canonical_header_hash",
]
