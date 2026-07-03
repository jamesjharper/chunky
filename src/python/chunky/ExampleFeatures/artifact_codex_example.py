from __future__ import annotations

"""Core artifact contracts demonstrated by the example.

These types correspond directly to the artifact specification. Graph creation,
phase selection, terminal remapping, and settings resolution are intentionally
not implemented here; those responsibilities belong to other Chunky specs.
"""

import json
from dataclasses import dataclass
from enum import Enum
from typing import Generic, Protocol, TypeVar, cast

T = TypeVar("T")


class BytesCodec:
    """Usage Example: minimal codec used by the example source artifact."""

    codec_id = "bytes"
    codec_version = 1
    mime_type = "application/octet-stream"
    extension = ".bin"

    async def serialize(self, value: bytes) -> bytes:
        if not isinstance(value, bytes):
            raise TypeError(f"BytesCodec expected bytes, got {type(value).__name__}")
        return value

    async def deserialize(self, raw: bytes) -> bytes:
        return raw


class JsonCodec(Generic[T]):
    """Usage Example: canonical JSON codec sufficient for this example only."""

    codec_id = "canonical-json"
    codec_version = 1
    mime_type = "application/json"
    extension = ".json"

    async def serialize(self, value: T) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )

    async def deserialize(self, raw: bytes) -> T:
        return cast(T, json.loads(raw.decode("utf-8")))
