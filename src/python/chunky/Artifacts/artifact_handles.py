from __future__ import annotations

"""Resolved source and target handle implementations.

The protocols and lifecycle semantics are demonstrated design. File I/O and the
specific settings source are small example adapters. Cache reservation behavior
is implemented by the mock cache module, not resolved by these abstractions.
"""

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Generic, Protocol, TypeVar, cast
from uuid import uuid4

from .artifact_models import Codec

T = TypeVar("T")
_UNSET = object()


class ArtifactSourceHandle(Protocol[T]):
    kind: str
    async def get_value(self, codec: Codec[T]) -> T: ...
    async def get_content_fingerprint(self, codec: Codec[T]) -> str: ...
    async def close(self) -> None: ...


class ArtifactTargetHandle(Protocol[T]):
    kind: str
    async def prepare(self,value: T, codec: Codec[T], fingerprint: str,) -> None: ...
    async def commit(self) -> None: ...
    async def abort(self) -> None: ...


class IntermediateArtifactHandle(Generic[T]):
    """duplex same-process publication handle.
    """

    kind = "intermediate-memory"

    def __init__(self) -> None:
        self._pending: T | object = _UNSET
        self._value: T | object = _UNSET
        self._committed = False
        self._aborted = False

    async def prepare(
        self,
        value: T,
        _codec: Codec[T],
        _fingerprint: str,
    ) -> None:
        if self._committed or self._aborted:
            raise RuntimeError("publication handle is already terminal")
        if self._pending is not _UNSET:
            raise RuntimeError("publication handle is already prepared")
        self._pending = value

    async def commit(self) -> None:
        if self._committed or self._aborted:
            return
        if self._pending is _UNSET:
            raise RuntimeError("publication handle has not been prepared")
        self._value = self._pending
        self._pending = _UNSET
        self._committed = True

    async def abort(self) -> None:
        if self._committed or self._aborted:
            return
        self._pending = _UNSET
        self._aborted = True

    async def get_value(self, _codec: Codec[T]) -> T:
        if not self._committed:
            raise RuntimeError("artifact is not committed")
        return cast(T, self._value)

    async def get_content_fingerprint(self, _codec: Codec[T]) -> str:
        # Produced values get identity from pedigree, not output re-hashing.
        return ""

    async def close(self) -> None:
        return None
