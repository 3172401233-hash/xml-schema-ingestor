"""Public entry point for streaming order-XML ingestion."""

from typing import IO, Optional, Union

import xml.sax
from xml.sax import SAXParseException
from xml.sax.xmlreader import InputSource

from .handler import OrderFeedHandler
from .mapping import FieldMapping
from .models import IngestResult
from .presets import get_preset

# Expat messages that indicate the feed ended before the document was closed.
_TRUNCATION_MESSAGES = frozenset(
    {
        "no element found",
        "unclosed token",
        "no memory",
        "token length too large",
    }
)


def _is_truncation(exc: SAXParseException) -> bool:
    inner = exc.getException()
    message = (str(inner) if inner is not None else exc.getMessage()) or ""
    # ExpatError strings look like "no element found: line 3, column 8".
    head = message.split(":", 1)[0].strip().lower()
    return head in _TRUNCATION_MESSAGES


def _as_input_source(stream: Union[IO[bytes], IO[str]]) -> InputSource:
    source = InputSource()
    if not callable(getattr(stream, "read", None)):
        raise TypeError("stream must be a file-like object with a read() method")
    # Text streams expose an encoding attribute (io.StringIO included); binary
    # streams such as BytesIO / open(..., "rb") do not.
    if hasattr(stream, "encoding"):
        source.setCharacterStream(stream)
    else:
        source.setByteStream(stream)
    return source


def ingest_orders(
    stream: Union[IO[bytes], IO[str]],
    schema_version: str,
    field_mapping: Optional[FieldMapping] = None,
) -> IngestResult:
    """Parse an order XML stream into staging records.

    Parameters
    ----------
    stream:
        A binary or text file-like object. It is fed to the SAX parser
        incrementally, so files much larger than memory are supported.
    schema_version:
        Key of a built-in preset (``"v1"`` / ``"v2"``) when ``field_mapping``
        is omitted; otherwise recorded on every emitted record.
    field_mapping:
        Optional custom :class:`FieldMapping`. When supplied it takes
        precedence over ``schema_version`` presets.

    Returns an :class:`IngestResult`. A truncated tail never raises: fully
    closed orders are returned and ``result.truncated`` is set to ``True``.
    Other well-formedness errors are also captured on the result rather than
    propagated, with the records recovered so far retained.
    """
    mapping = field_mapping if field_mapping is not None else get_preset(schema_version)

    handler = OrderFeedHandler(mapping)
    parser = xml.sax.make_parser()
    parser.setFeature(xml.sax.handler.feature_namespaces, True)
    parser.setContentHandler(handler)

    try:
        parser.parse(_as_input_source(stream))
    except SAXParseException as exc:
        truncated = _is_truncation(exc)
        handler.result.complete = False
        handler.result.truncated = truncated
        handler.result.error = f"{type(exc.getException()).__name__}: {exc.getMessage()}"

    return handler.result
