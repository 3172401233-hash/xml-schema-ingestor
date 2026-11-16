"""Shared helpers for the test suite."""

import io
import os

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "samples")


def sample_path(name: str) -> str:
    return os.path.join(SAMPLE_DIR, name)


def read_sample(name: str) -> bytes:
    with open(sample_path(name), "rb") as handle:
        return handle.read()


class ChunkedReader(io.RawIOBase):
    """Binary stream that only serves fixed-size chunks.

    Wraps in-memory bytes so the SAX parser must ingest the feed
    incrementally, mirroring how a real network/disk stream behaves.
    """

    def __init__(self, data: bytes, chunk_size: int = 7) -> None:
        super().__init__()
        self._data = data
        self._chunk_size = chunk_size
        self._pos = 0
        self.read_call_count = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer):
        self.read_call_count += 1
        if self._pos >= len(self._data):
            return 0
        chunk = self._data[self._pos : self._pos + self._chunk_size]
        buffer[: len(chunk)] = chunk
        self._pos += len(chunk)
        return len(chunk)


class OneByteReader(ChunkedReader):
    """Extreme case: the parser receives one byte per read call."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data, chunk_size=1)
