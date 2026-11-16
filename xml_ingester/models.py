"""Staging record and ingestion result data models."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Logical target tables for the staging layer.
TABLE_ORDER = "stg_order"
TABLE_ITEM = "stg_order_item"
TABLE_PAYMENT = "stg_order_payment"


@dataclass
class StagingRecord:
    """One row destined for a staging table.

    ``record_key`` is stable across repeated runs and repeated nodes: records
    describing the same logical entity always receive the same key, so the
    downstream loader can upsert instead of inserting duplicates.
    """

    table: str
    record_key: str
    fields: Dict[str, Any] = field(default_factory=dict)
    parent_key: Optional[str] = None
    schema_version: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table": self.table,
            "record_key": self.record_key,
            "parent_key": self.parent_key,
            "schema_version": self.schema_version,
            "fields": dict(self.fields),
        }


@dataclass
class IngestResult:
    """Outcome of one streaming ingestion call."""

    records: List[StagingRecord] = field(default_factory=list)
    complete: bool = True
    truncated: bool = False
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def orders(self) -> List[StagingRecord]:
        return [r for r in self.records if r.table == TABLE_ORDER]

    @property
    def items(self) -> List[StagingRecord]:
        return [r for r in self.records if r.table == TABLE_ITEM]

    @property
    def payments(self) -> List[StagingRecord]:
        return [r for r in self.records if r.table == TABLE_PAYMENT]
