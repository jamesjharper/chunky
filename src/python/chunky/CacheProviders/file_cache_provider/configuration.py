from __future__ import annotations

"""Configuration and garbage-collection policy for ``FileCacheProvider``."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FileCacheProviderConfig:
    """Runtime and durability settings for a local filesystem cache."""

    sqlite_busy_timeout_seconds: float = 30.0
    reservation_lease_seconds: float = 120.0
    heartbeat_interval_seconds: float = 20.0
    wait_poll_interval_seconds: float = 0.20
    orphan_grace_seconds: float = 300.0
    fsync_data: bool = True
    fsync_directories: bool = True

    def __post_init__(self) -> None:
        if self.sqlite_busy_timeout_seconds <= 0:
            raise ValueError("sqlite_busy_timeout_seconds must be positive")
        if self.reservation_lease_seconds <= 0:
            raise ValueError("reservation_lease_seconds must be positive")
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        if self.heartbeat_interval_seconds >= self.reservation_lease_seconds / 2:
            raise ValueError(
                "heartbeat_interval_seconds must be less than half the lease duration"
            )
        if self.wait_poll_interval_seconds <= 0:
            raise ValueError("wait_poll_interval_seconds must be positive")
        if self.orphan_grace_seconds < 0:
            raise ValueError("orphan_grace_seconds must not be negative")


@dataclass(frozen=True)
class FileCacheProviderGarbageCollectionPolicy:
    """Retention and pressure limits applied by cache garbage collection.

    Retired immutable generations are eligible once no read reservation refers
    to them. Current committed entries are selected by age and then by LRU when
    the configured count or byte limits are exceeded.
    """

    max_age_seconds: float | None = None
    max_total_bytes: int | None = None
    max_entry_count: int | None = None
    invalid_retention_seconds: float = 0.0
    orphan_grace_seconds: float | None = None
    max_delete_entries: int | None = None
    vacuum: bool = True

    def __post_init__(self) -> None:
        if self.max_age_seconds is not None and self.max_age_seconds < 0:
            raise ValueError("max_age_seconds must not be negative")
        if self.max_total_bytes is not None and self.max_total_bytes < 0:
            raise ValueError("max_total_bytes must not be negative")
        if self.max_entry_count is not None and self.max_entry_count < 0:
            raise ValueError("max_entry_count must not be negative")
        if self.invalid_retention_seconds < 0:
            raise ValueError("invalid_retention_seconds must not be negative")
        if self.orphan_grace_seconds is not None and self.orphan_grace_seconds < 0:
            raise ValueError("orphan_grace_seconds must not be negative")
        if self.max_delete_entries is not None and self.max_delete_entries <= 0:
            raise ValueError("max_delete_entries must be positive")
