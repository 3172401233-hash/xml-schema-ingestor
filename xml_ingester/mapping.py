"""Field mapping definitions: XML element paths to staging table columns.

Path grammar (namespace agnostic -- only local names are matched):

* ``order/customer/name``  -> text content of the nested element
* ``order/customer/@id``   -> the ``id`` attribute of the ``customer`` element

Paths are always relative to the record's root element (``order``, ``item``
or ``payment``), so a single mapping works regardless of the namespace the
supplier used in a given schema version.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

# A parsed path is a tuple of local-name segments. The final segment of an
# attribute path is prefixed with ``@``.
ParsedPath = Tuple[str, ...]


def parse_path(path: str) -> ParsedPath:
    """Convert a mapping path string into a normalized segment tuple."""
    segments: List[str] = []
    for segment in path.strip().strip("/").split("/"):
        segment = segment.strip()
        if not segment:
            continue
        if segment.startswith("@"):
            segments.append("@" + segment[1:])
        else:
            segments.append(segment)
    return tuple(segments)


def is_attribute_path(parsed: ParsedPath) -> bool:
    return bool(parsed) and parsed[-1].startswith("@")


@dataclass
class RecordMapping:
    """Column-to-path mapping for one staging table (order/item/payment)."""

    table: str
    root_tag: str
    # column name -> list of candidate XML paths, first match wins
    columns: Dict[str, Sequence[str]]
    _parsed: Dict[str, List[ParsedPath]] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._parsed = {
            column: [parse_path(path) for path in paths]
            for column, paths in self.columns.items()
        }

    def attribute_bindings(self) -> Dict[ParsedPath, List[str]]:
        """Map attribute paths to the columns they populate (for startElement)."""
        bindings: Dict[ParsedPath, List[str]] = {}
        for column, paths in self._parsed.items():
            for parsed in paths:
                if is_attribute_path(parsed):
                    bindings.setdefault(parsed, []).append(column)
        return bindings

    def text_bindings(self) -> Dict[ParsedPath, List[str]]:
        """Map element-text paths to the columns they populate (for endElement)."""
        bindings: Dict[ParsedPath, List[str]] = {}
        for column, paths in self._parsed.items():
            for parsed in paths:
                if not is_attribute_path(parsed):
                    bindings.setdefault(parsed, []).append(column)
        return bindings


@dataclass
class FieldMapping:
    """Complete mapping bundle for one schema version."""

    version: str
    # XML namespace URIs the supplier may use for this version. An empty set
    # means "accept any namespace" (lenient mode for namespace-less files).
    namespaces: frozenset
    order: RecordMapping
    item: RecordMapping
    payment: RecordMapping

    def record_mappings(self) -> List[RecordMapping]:
        return [self.order, self.item, self.payment]
