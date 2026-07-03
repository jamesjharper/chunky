from __future__ import annotations

"""Recovery, garbage collection, orphan cleanup, and SQLite vacuuming."""

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .configuration import FileCacheProviderGarbageCollectionPolicy
from .maintenance_store import FileCacheProviderMaintenanceStore
from .models import (
    FileCacheProviderEvent,
    FileCacheProviderGarbageCollectionResult,
    FileCacheProviderRecoveryResult,
    FileCacheProviderStatistics,
)

if TYPE_CHECKING:
    from .provider import FileCacheProvider


class FileCacheProviderMaintenance:
    """Runs maintenance without mixing it into normal cache resolution code."""

    def __init__(self, provider: FileCacheProvider) -> None:
        self._provider = provider
        self._store = FileCacheProviderMaintenanceStore(provider._metadata_store)

    async def recover(self) -> FileCacheProviderRecoveryResult:
        store = self._store
        plan = await asyncio.to_thread(store._build_recovery_plan_sync)

        paths: list[Path] = []
        for _fingerprint, temporary_relative_path, final_relative_path in (
            plan.stale_write_rows
        ):
            if temporary_relative_path:
                paths.append(self._provider.root / temporary_relative_path)
            paths.append(self._provider.root / final_relative_path)
        for _entry_id, relative_path in plan.deleting_rows:
            paths.append(self._provider.root / relative_path)

        files_removed = await self._provider._filesystem.remove_paths(paths)
        await asyncio.to_thread(
            store._finalize_recovery_sync,
            plan.stale_read_ids,
            tuple(row[0] for row in plan.stale_write_rows),
            tuple(row[0] for row in plan.deleting_rows),
        )
        return FileCacheProviderRecoveryResult(
            stale_read_reservations_released=len(plan.stale_read_ids),
            stale_write_reservations_released=len(plan.stale_write_rows),
            deleting_entries_recovered=len(plan.deleting_rows),
            files_removed=files_removed,
        )

    async def garbage_collect(
        self,
        policy: FileCacheProviderGarbageCollectionPolicy | None = None,
    ) -> FileCacheProviderGarbageCollectionResult:
        policy = policy or FileCacheProviderGarbageCollectionPolicy()
        recovery = await self.recover()
        store = self._store
        claimed = await asyncio.to_thread(store._claim_gc_entries_sync, policy)

        deleted_entry_ids: list[str] = []
        failed_entry_ids: list[str] = []
        bytes_deleted = 0
        for entry in claimed:
            path = self._provider.root / entry.relative_path
            try:
                await asyncio.to_thread(path.unlink, missing_ok=True)
                deleted_entry_ids.append(entry.entry_id)
                bytes_deleted += entry.data_size
            except OSError:
                failed_entry_ids.append(entry.entry_id)

        await asyncio.to_thread(
            store._finalize_gc_entries_sync,
            tuple(deleted_entry_ids),
            tuple(failed_entry_ids),
        )

        orphan_grace = (
            self._provider.config.orphan_grace_seconds
            if policy.orphan_grace_seconds is None
            else policy.orphan_grace_seconds
        )
        referenced_paths = await asyncio.to_thread(store._referenced_paths_sync)
        orphan_files_deleted = await self._provider._filesystem.cleanup_orphan_files(
            referenced_paths,
            orphan_grace,
        )

        vacuum_performed = False
        if policy.vacuum:
            # The store performs WAL checkpointing before VACUUM and PRAGMA
            # optimize afterwards. VACUUM necessarily runs outside a transaction.
            await asyncio.to_thread(store._vacuum_sync)
            vacuum_performed = True

        deleted_ids = set(deleted_entry_ids)
        for entry in claimed:
            if entry.entry_id in deleted_ids:
                await self._provider._emit(
                    FileCacheProviderEvent(
                        event_type="artifact_cache_entry_deleted",
                        owner_task_id=self._provider.owner_task_id,
                        timestamp=time.time(),
                    )
                )

        return FileCacheProviderGarbageCollectionResult(
            entries_deleted=len(deleted_entry_ids),
            bytes_deleted=bytes_deleted,
            orphan_files_deleted=orphan_files_deleted + recovery.files_removed,
            stale_read_reservations_released=(
                recovery.stale_read_reservations_released
            ),
            stale_write_reservations_released=(
                recovery.stale_write_reservations_released
            ),
            vacuum_performed=vacuum_performed,
        )

    async def statistics(self) -> FileCacheProviderStatistics:
        return await asyncio.to_thread(self._store._statistics_sync)
