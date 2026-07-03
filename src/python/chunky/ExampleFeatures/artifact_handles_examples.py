from __future__ import annotations

"""Resolved source and target handle implementations.

The protocols and lifecycle semantics are demonstrated design. File I/O and the
specific settings source are small example adapters. Cache reservation behavior
is implemented by the mock cache module, not resolved by these abstractions.
"""

import asyncio
import os
from pathlib import Path
from typing import Generic, TypeVar
from uuid import uuid4

from ..Artifacts.artifact_models import Codec
from ..Artifacts.fingerprints import stable_hash_bytes

T = TypeVar("T")


class FileSource(Generic[T]):
    """Usage Example: caller-supplied file source.

    Byte identity is used as semantic content identity for this example. A real
    audio source may instead fingerprint canonical decoded PCM and metadata.
    """

    kind = "supplied-file"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    async def get_value(self, codec: Codec[T]) -> T:
        raw = await asyncio.to_thread(self.path.read_bytes)
        return await codec.deserialize(raw)

    async def get_content_fingerprint(self, _codec: Codec[T]) -> str:
        raw = await asyncio.to_thread(self.path.read_bytes)
        return stable_hash_bytes(raw)

    async def close(self) -> None:
        return None


class AtomicFileTargetHandle(Generic[T]):
    """Usage Example: caller-provided file target with prepare/commit/abort."""

    kind = "atomic-file"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._temporary_path: Path | None = None
        self._committed = False
        self._aborted = False

    async def prepare(
        self,
        value: T,
        codec: Codec[T],
        _fingerprint: str,
    ) -> None:
        if self._committed or self._aborted:
            raise RuntimeError("target is already terminal")
        if self._temporary_path is not None:
            raise RuntimeError("target is already prepared")

        await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)
        raw = await codec.serialize(value)
        temporary_path = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        await asyncio.to_thread(temporary_path.write_bytes, raw)
        self._temporary_path = temporary_path

    async def commit(self) -> None:
        if self._committed or self._aborted:
            return
        if self._temporary_path is None:
            raise RuntimeError("target has not been prepared")

        await asyncio.to_thread(os.replace, self._temporary_path, self.path)
        self._temporary_path = None
        self._committed = True

    async def abort(self) -> None:
        if self._committed or self._aborted:
            return
        if self._temporary_path is not None:
            await asyncio.to_thread(self._temporary_path.unlink, missing_ok=True)
        self._temporary_path = None
        self._aborted = True


class InMemorySource(Generic[T]):
    """Generic supplied/default value source using canonical codec bytes."""

    kind = "supplied-memory"

    def __init__(self, value: T):
        self.value = value

    async def get_value(self, _codec: Codec[T]) -> T:
        return self.value

    async def get_content_fingerprint(self, codec: Codec[T]) -> str:
        raw = await codec.serialize(self.value)
        return stable_hash_bytes(raw)

    async def close(self) -> None:
        return None
