from __future__ import annotations

"""Internal immutable values passed between SQLite operations and handles."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CacheHitDescriptor:
    entry_id: str
    reservation_id: str
    data_path: Path
    data_size: int
    data_digest: str
    fingerprint: str


@dataclass(frozen=True)
class CacheWriteDescriptor:
    reservation_id: str
    entry_id: str
    fingerprint: str
    final_path: Path


@dataclass(frozen=True)
class CacheWaitDescriptor:
    reservation_id: str
    fingerprint: str
    owner_task_id: str | None


@dataclass(frozen=True)
class ClaimedCacheEntry:
    entry_id: str
    relative_path: str
    data_size: int


@dataclass(frozen=True)
class CacheRecoveryPlan:
    stale_read_ids: tuple[str, ...]
    stale_write_rows: tuple[tuple[str, str | None, str], ...]
    deleting_rows: tuple[tuple[str, str], ...]
