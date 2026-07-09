from __future__ import annotations

"""Cache-resolution result contracts used by output bootstrap.

"""

from dataclasses import dataclass
from typing import Generic, Protocol, TypeAlias, TypeVar

from .binding_models import ArtifactInstance, ArtifactWriter
from ..Artifacts.artifact_handles import ArtifactSourceHandle, ArtifactTargetHandle
from ..Artifacts.artifact_models import ArtifactDeclaration

T = TypeVar("T")


@dataclass(frozen=True)
class CacheHit(Generic[T]):
    source_handle: ArtifactSourceHandle[T]


@dataclass(frozen=True)
class CacheWriteReservation(Generic[T]):
    target_handle: ArtifactTargetHandle[T]


class CacheWaitHandle(Protocol):
    async def wait(self) -> None: ...


@dataclass(frozen=True)
class CacheWait:
    fingerprint: str
    owner_task_id: str | None
    wait_handle: CacheWaitHandle


CacheResolution: TypeAlias = CacheHit[T] | CacheWriteReservation[T] | CacheWait


class ArtifactCache(Protocol):
    async def resolve(
        self,
        declaration: ArtifactDeclaration[T],
        fingerprint: str,
        *,
        force_write: bool = False,
    ) -> CacheResolution[T]: ...

    async def open(
        self,
        declaration: ArtifactDeclaration[T],
        fingerprint: str,
    ) -> CacheHit[T]: ...


@dataclass(frozen=True)
class CacheableOutputAvailable(Generic[T]):
    instance: ArtifactInstance[T]


@dataclass(frozen=True)
class CacheableOutputProduce(Generic[T]):
    writer: ArtifactWriter[T]


@dataclass(frozen=True)
class CacheableOutputWaiting:
    wait: CacheWait


CacheableOutputBootstrap: TypeAlias = (
    CacheableOutputAvailable[T]
    | CacheableOutputProduce[T]
    | CacheableOutputWaiting
)
