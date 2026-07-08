from __future__ import annotations

"""Public maintenance, statistics, and event models."""

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class FileCacheProviderRecoveryResult:
    stale_read_reservations_released: int
    stale_write_reservations_released: int
    deleting_entries_recovered: int
    files_removed: int


@dataclass(frozen=True)
class FileCacheProviderGarbageCollectionResult:
    entries_deleted: int
    bytes_deleted: int
    orphan_files_deleted: int
    stale_read_reservations_released: int
    stale_write_reservations_released: int
    vacuum_performed: bool


@dataclass(frozen=True)
class FileCacheProviderStatistics:
    current_entry_count: int
    retired_entry_count: int
    invalid_entry_count: int
    total_entry_bytes: int
    read_reservation_count: int
    write_reservation_count: int


@dataclass(frozen=True)
class FileCacheProviderEvent:
    event_type: str
    fingerprint: str | None = None
    artifact_id: str | None = None
    reservation_id: str | None = None
    owner_task_id: str | None = None
    reason: str | None = None
    timestamp: float = 0.0


FileCacheProviderEventHandler = Callable[[FileCacheProviderEvent], Any]
