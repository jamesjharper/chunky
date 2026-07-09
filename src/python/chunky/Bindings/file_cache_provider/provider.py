from __future__ import annotations

"""Public local filesystem cache provider.

``FileCacheProvider`` stores immutable serialized generations under ``objects/``
and uses SQLite WAL metadata for cross-process lookup, reservation, commit,
recovery, and garbage-collection coordination.
"""

import asyncio
import inspect
import os
import socket
import time
from pathlib import Path
from typing import Any, Literal, TypeVar
from uuid import uuid4

from ...Artifacts.artifact_models import ArtifactDeclaration
from ..cache_models import (
    CacheHit,
    CacheResolution,
    CacheWait,
    CacheWriteReservation,
)
from ._descriptors import (
    CacheHitDescriptor,
    CacheWaitDescriptor,
    CacheWriteDescriptor,
)
from .configuration import (
    FileCacheProviderConfig,
    FileCacheProviderGarbageCollectionPolicy,
)
from .filesystem import FileCacheProviderFilesystem
from .handles import (
    FileCacheProviderSourceHandle,
    FileCacheProviderTargetHandle,
    FileCacheProviderWaitHandle,
)
from .maintenance import FileCacheProviderMaintenance
from .metadata_store import FileCacheProviderMetadataStore
from .models import (
    FileCacheProviderEvent,
    FileCacheProviderEventHandler,
    FileCacheProviderGarbageCollectionResult,
    FileCacheProviderRecoveryResult,
    FileCacheProviderStatistics,
)

T = TypeVar("T")
ReservationKind = Literal["read", "write"]


class FileCacheProvider:
    """Local filesystem cache with SQLite coordination and lease recovery.

    Create one task-scoped instance with :meth:`for_task` when a cache root is
    shared by a task manager. Every instance and process using the same root
    shares the SQLite database and immutable object directory.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        owner_task_id: str | None = None,
        owner_invocation_id: str | None = None,
        owner_stage_id: str | None = None,
        config: FileCacheProviderConfig | None = None,
        event_handler: FileCacheProviderEventHandler | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.objects_root = self.root / "objects"
        self.database_path = self.root / "cache.sqlite3"
        self.config = config or FileCacheProviderConfig()
        self.owner_task_id = owner_task_id or f"cache-client-{uuid4().hex}"
        self.owner_invocation_id = owner_invocation_id
        self.owner_stage_id = owner_stage_id
        self._event_handler = event_handler
        self._owner_process_id = os.getpid()
        self._owner_host_id = socket.gethostname()
        self._owner_process_instance_id = uuid4().hex

        self.objects_root.mkdir(parents=True, exist_ok=True)
        self._filesystem = FileCacheProviderFilesystem(
            self.root,
            self.objects_root,
            self.config,
        )
        self._metadata_store = FileCacheProviderMetadataStore(
            root=self.root,
            objects_root=self.objects_root,
            database_path=self.database_path,
            config=self.config,
            filesystem=self._filesystem,
            owner_task_id=self.owner_task_id,
            owner_invocation_id=self.owner_invocation_id,
            owner_stage_id=self.owner_stage_id,
            owner_process_id=self._owner_process_id,
            owner_host_id=self._owner_host_id,
            owner_process_instance_id=self._owner_process_instance_id,
        )
        self._maintenance = FileCacheProviderMaintenance(self)

    def for_task(
        self,
        task_id: str,
        *,
        invocation_id: str | None = None,
        stage_id: str | None = None,
    ) -> FileCacheProvider:
        return FileCacheProvider(
            self.root,
            owner_task_id=task_id,
            owner_invocation_id=invocation_id,
            owner_stage_id=stage_id,
            config=self.config,
            event_handler=self._event_handler,
        )

    async def resolve(
        self,
        declaration: ArtifactDeclaration[T],
        fingerprint: str,
        *,
        force_write: bool = False,
    ) -> CacheResolution[T]:
        descriptor, cleanup_paths = await asyncio.to_thread(
            self._metadata_store._resolve_sync,
            declaration,
            fingerprint,
            force_write,
        )
        await self._filesystem.remove_paths(cleanup_paths)

        if isinstance(descriptor, CacheHitDescriptor):
            await self._emit(
                FileCacheProviderEvent(
                    event_type="artifact_cache_hit",
                    fingerprint=fingerprint,
                    artifact_id=str(declaration.artifact_id),
                    reservation_id=descriptor.reservation_id,
                    owner_task_id=self.owner_task_id,
                    timestamp=time.time(),
                )
            )
            return CacheHit(FileCacheProviderSourceHandle(self, descriptor))

        if isinstance(descriptor, CacheWriteDescriptor):
            await self._emit(
                FileCacheProviderEvent(
                    event_type="artifact_cache_write_reserved",
                    fingerprint=fingerprint,
                    artifact_id=str(declaration.artifact_id),
                    reservation_id=descriptor.reservation_id,
                    owner_task_id=self.owner_task_id,
                    reason="force_write" if force_write else "cache_miss",
                    timestamp=time.time(),
                )
            )
            return CacheWriteReservation(
                FileCacheProviderTargetHandle(self, declaration, descriptor)
            )

        if not isinstance(descriptor, CacheWaitDescriptor):
            raise TypeError(f"unsupported cache descriptor: {descriptor!r}")

        await self._emit(
            FileCacheProviderEvent(
                event_type="artifact_cache_wait_started",
                fingerprint=fingerprint,
                artifact_id=str(declaration.artifact_id),
                reservation_id=descriptor.reservation_id,
                owner_task_id=descriptor.owner_task_id,
                timestamp=time.time(),
            )
        )
        return CacheWait(
            fingerprint=fingerprint,
            owner_task_id=descriptor.owner_task_id,
            wait_handle=FileCacheProviderWaitHandle(
                self,
                fingerprint,
                descriptor.reservation_id,
            ),
        )


    async def open(
        self,
        declaration: ArtifactDeclaration[T],
        fingerprint: str,
    ) -> CacheHit[T]:
        """Open an existing committed cache entry by semantic reference.

        Unlike ``resolve`` this is a strict read path for explicit cached-root
        references. It validates metadata and acquires a read reservation, but
        never creates a write reservation for a missing entry.
        """

        descriptor, cleanup_paths = await asyncio.to_thread(
            self._metadata_store._open_existing_sync,
            declaration,
            fingerprint,
        )
        await self._filesystem.remove_paths(cleanup_paths)
        if isinstance(descriptor, CacheHitDescriptor):
            await self._emit(
                FileCacheProviderEvent(
                    event_type="artifact_cache_hit",
                    fingerprint=fingerprint,
                    artifact_id=str(declaration.artifact_id),
                    reservation_id=descriptor.reservation_id,
                    owner_task_id=self.owner_task_id,
                    reason="direct_open",
                    timestamp=time.time(),
                )
            )
            return CacheHit(FileCacheProviderSourceHandle(self, descriptor))
        if isinstance(descriptor, CacheWaitDescriptor):
            raise FileCacheProviderError(
                f"cache entry is currently being written and cannot be opened: {fingerprint}"
            )
        raise FileCacheProviderError(
            f"cache entry does not exist for explicit reference: {fingerprint}"
        )

    async def invalidate_entry(self, entry_id: str, *, reason: str) -> bool:
        changed = await asyncio.to_thread(
            self._metadata_store._invalidate_entry_sync,
            entry_id,
            reason,
        )
        if changed:
            await self._emit(
                FileCacheProviderEvent(
                    event_type="artifact_cache_entry_invalid",
                    owner_task_id=self.owner_task_id,
                    reason=reason,
                    timestamp=time.time(),
                )
            )
        return changed

    async def recover(self) -> FileCacheProviderRecoveryResult:
        return await self._maintenance.recover()

    async def garbage_collect(
        self,
        policy: FileCacheProviderGarbageCollectionPolicy | None = None,
    ) -> FileCacheProviderGarbageCollectionResult:
        return await self._maintenance.garbage_collect(policy)

    async def statistics(self) -> FileCacheProviderStatistics:
        return await self._maintenance.statistics()

    # Handle-facing operations -------------------------------------------------

    async def _heartbeat(
        self,
        kind: ReservationKind,
        reservation_id: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._metadata_store._heartbeat_sync,
            kind,
            reservation_id,
        )

    async def _touch_read_reservation(self, reservation_id: str) -> bool:
        return await asyncio.to_thread(
            self._metadata_store._touch_read_reservation_sync,
            reservation_id,
        )

    async def _release_read_reservation(self, reservation_id: str) -> None:
        released = await asyncio.to_thread(
            self._metadata_store._release_read_reservation_sync,
            reservation_id,
        )
        if released:
            await self._emit(
                FileCacheProviderEvent(
                    event_type="artifact_cache_read_released",
                    reservation_id=reservation_id,
                    owner_task_id=self.owner_task_id,
                    timestamp=time.time(),
                )
            )

    async def _record_preparing_write(
        self,
        reservation_id: str,
        temporary_path: Path,
    ) -> None:
        await asyncio.to_thread(
            self._metadata_store._record_preparing_write_sync,
            reservation_id,
            temporary_path,
        )

    async def _record_prepared_write(
        self,
        reservation_id: str,
        temporary_path: Path,
        data_size: int,
        data_digest: str,
    ) -> None:
        await asyncio.to_thread(
            self._metadata_store._record_prepared_write_sync,
            reservation_id,
            temporary_path,
            data_size,
            data_digest,
        )

    async def _commit_write(
        self,
        *,
        reservation_id: str,
        entry_id: str,
        fingerprint: str,
        declaration: ArtifactDeclaration[Any],
        temporary_path: Path,
        final_path: Path,
        data_size: int,
        data_digest: str,
    ) -> None:
        await asyncio.to_thread(
            self._metadata_store._commit_write_sync,
            reservation_id,
            entry_id,
            fingerprint,
            declaration,
            temporary_path,
            final_path,
            data_size,
            data_digest,
        )
        await self._emit(
            FileCacheProviderEvent(
                event_type="artifact_cache_committed",
                fingerprint=fingerprint,
                artifact_id=str(declaration.artifact_id),
                reservation_id=reservation_id,
                owner_task_id=self.owner_task_id,
                timestamp=time.time(),
            )
        )

    async def _abort_write(
        self,
        *,
        reservation_id: str,
        entry_id: str,
        temporary_path: Path | None,
        final_path: Path,
    ) -> None:
        paths = await asyncio.to_thread(
            self._metadata_store._abort_write_sync,
            reservation_id,
            entry_id,
            temporary_path,
            final_path,
        )
        await self._filesystem.remove_paths(paths)
        await self._emit(
            FileCacheProviderEvent(
                event_type="artifact_cache_write_released",
                reservation_id=reservation_id,
                owner_task_id=self.owner_task_id,
                reason="aborted",
                timestamp=time.time(),
            )
        )

    async def _observed_writer_is_active(
        self,
        fingerprint: str,
        observed_reservation_id: str,
    ) -> bool:
        active, cleanup_paths = await asyncio.to_thread(
            self._metadata_store._observed_writer_is_active_sync,
            fingerprint,
            observed_reservation_id,
        )
        await self._filesystem.remove_paths(cleanup_paths)
        if not active:
            await self._emit(
                FileCacheProviderEvent(
                    event_type="artifact_cache_wait_finished",
                    fingerprint=fingerprint,
                    reservation_id=observed_reservation_id,
                    owner_task_id=self.owner_task_id,
                    timestamp=time.time(),
                )
            )
        return active

    async def _emit(self, event: FileCacheProviderEvent) -> None:
        if self._event_handler is None:
            return

        async def dispatch() -> None:
            try:
                result = await asyncio.to_thread(self._event_handler, event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                # Diagnostics must not fail or delay cache state transitions.
                return

        asyncio.create_task(dispatch())
