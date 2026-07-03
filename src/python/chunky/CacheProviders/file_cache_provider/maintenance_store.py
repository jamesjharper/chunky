from __future__ import annotations

"""Maintenance-only SQLite operations for ``FileCacheProvider``."""

import sqlite3
import time
from contextlib import closing

from ._constants import ENTRY_COMMITTED, ENTRY_DELETING, ENTRY_INVALID
from ._descriptors import CacheRecoveryPlan, ClaimedCacheEntry
from .configuration import FileCacheProviderGarbageCollectionPolicy
from .metadata_store import FileCacheProviderMetadataStore
from .models import FileCacheProviderStatistics


class FileCacheProviderMaintenanceStore:
    """Isolates recovery, GC selection, statistics, and VACUUM SQL."""

    def __init__(self, metadata_store: FileCacheProviderMetadataStore) -> None:
        self._metadata_store = metadata_store

    def _connect(self) -> sqlite3.Connection:
        return self._metadata_store._connect()

    def _reservation_row_is_stale(self, row: sqlite3.Row, now: float) -> bool:
        return self._metadata_store._reservation_row_is_stale(row, now)

    def _build_recovery_plan_sync(self) -> CacheRecoveryPlan:
        now = time.time()
        stale_read_ids: list[str] = []
        stale_write_rows: list[tuple[str, str | None, str]] = []
        deleting_rows: list[tuple[str, str]] = []

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for row in connection.execute("SELECT * FROM read_reservations"):
                    if self._reservation_row_is_stale(row, now):
                        stale_read_ids.append(row["reservation_id"])
                for reservation_id in stale_read_ids:
                    connection.execute(
                        "DELETE FROM read_reservations WHERE reservation_id = ?",
                        (reservation_id,),
                    )

                for row in connection.execute("SELECT * FROM write_reservations"):
                    if self._reservation_row_is_stale(row, now):
                        stale_write_rows.append(
                            (
                                row["fingerprint"],
                                row["temporary_relative_path"],
                                row["final_relative_path"],
                            )
                        )
                        connection.execute(
                            "DELETE FROM write_reservations WHERE reservation_id = ?",
                            (row["reservation_id"],),
                        )

                for row in connection.execute(
                    "SELECT entry_id, relative_path FROM cache_entries WHERE status = ?",
                    (ENTRY_DELETING,),
                ):
                    if connection.execute(
                        "SELECT 1 FROM read_reservations WHERE entry_id = ? LIMIT 1",
                        (row["entry_id"],),
                    ).fetchone() is None:
                        deleting_rows.append((row["entry_id"], row["relative_path"]))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

        return CacheRecoveryPlan(
            stale_read_ids=tuple(stale_read_ids),
            stale_write_rows=tuple(stale_write_rows),
            deleting_rows=tuple(deleting_rows),
        )

    def _finalize_recovery_sync(
        self,
        _stale_read_ids: tuple[str, ...],
        _stale_write_fingerprints: tuple[str, ...],
        deleting_entry_ids: tuple[str, ...],
    ) -> None:
        if not deleting_entry_ids:
            return
        with closing(self._connect()) as connection:
            connection.executemany(
                "DELETE FROM cache_entries WHERE entry_id = ? AND status = ?",
                ((entry_id, ENTRY_DELETING) for entry_id in deleting_entry_ids),
            )

    def _claim_gc_entries_sync(
        self,
        policy: FileCacheProviderGarbageCollectionPolicy,
    ) -> tuple[ClaimedCacheEntry, ...]:
        now = time.time()
        selected: dict[str, sqlite3.Row] = {}
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                eligible_rows = connection.execute(
                    """
                    SELECT e.*
                    FROM cache_entries e
                    WHERE e.status IN (?, ?)
                      AND NOT EXISTS (
                          SELECT 1 FROM read_reservations r
                          WHERE r.entry_id = e.entry_id
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM write_reservations w
                          WHERE w.fingerprint = e.fingerprint
                      )
                    ORDER BY e.last_used_at ASC, e.created_at ASC
                    """,
                    (ENTRY_COMMITTED, ENTRY_INVALID),
                ).fetchall()

                for row in eligible_rows:
                    if not row["is_current"]:
                        selected[row["entry_id"]] = row
                    elif row["status"] == ENTRY_INVALID and (
                        now - float(row["status_changed_at"])
                        >= policy.invalid_retention_seconds
                    ):
                        selected[row["entry_id"]] = row
                    elif policy.max_age_seconds is not None and (
                        now - float(row["last_used_at"]) >= policy.max_age_seconds
                    ):
                        selected[row["entry_id"]] = row

                current_rows = [
                    row
                    for row in eligible_rows
                    if row["is_current"]
                    and row["status"] == ENTRY_COMMITTED
                    and row["entry_id"] not in selected
                ]
                current_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM cache_entries
                    WHERE status = ? AND is_current = 1
                    """,
                    (ENTRY_COMMITTED,),
                ).fetchone()[0]
                current_bytes = connection.execute(
                    """
                    SELECT COALESCE(SUM(data_size), 0) FROM cache_entries
                    WHERE status = ? AND is_current = 1
                    """,
                    (ENTRY_COMMITTED,),
                ).fetchone()[0]

                # Age-selected current entries already reduce the post-GC
                # pressure totals; account for them before applying LRU limits.
                for row in selected.values():
                    if row["is_current"] and row["status"] == ENTRY_COMMITTED:
                        current_count -= 1
                        current_bytes -= int(row["data_size"])

                for row in current_rows:
                    over_count = (
                        policy.max_entry_count is not None
                        and current_count > policy.max_entry_count
                    )
                    over_bytes = (
                        policy.max_total_bytes is not None
                        and current_bytes > policy.max_total_bytes
                    )
                    if not over_count and not over_bytes:
                        break
                    selected[row["entry_id"]] = row
                    current_count -= 1
                    current_bytes -= int(row["data_size"])

                rows = list(selected.values())
                if policy.max_delete_entries is not None:
                    rows = rows[: policy.max_delete_entries]

                claimed: list[ClaimedCacheEntry] = []
                for row in rows:
                    cursor = connection.execute(
                        """
                        UPDATE cache_entries
                        SET status = ?, is_current = 0, status_changed_at = ?
                        WHERE entry_id = ?
                          AND status IN (?, ?)
                          AND NOT EXISTS (
                              SELECT 1 FROM read_reservations r
                              WHERE r.entry_id = cache_entries.entry_id
                          )
                          AND NOT EXISTS (
                              SELECT 1 FROM write_reservations w
                              WHERE w.fingerprint = cache_entries.fingerprint
                          )
                        """,
                        (
                            ENTRY_DELETING,
                            now,
                            row["entry_id"],
                            ENTRY_COMMITTED,
                            ENTRY_INVALID,
                        ),
                    )
                    if cursor.rowcount == 1:
                        claimed.append(
                            ClaimedCacheEntry(
                                entry_id=row["entry_id"],
                                relative_path=row["relative_path"],
                                data_size=int(row["data_size"]),
                            )
                        )
                connection.commit()
                return tuple(claimed)
            except BaseException:
                connection.rollback()
                raise

    def _finalize_gc_entries_sync(
        self,
        deleted_entry_ids: tuple[str, ...],
        failed_entry_ids: tuple[str, ...],
    ) -> None:
        now = time.time()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.executemany(
                    "DELETE FROM cache_entries WHERE entry_id = ? AND status = ?",
                    ((entry_id, ENTRY_DELETING) for entry_id in deleted_entry_ids),
                )
                connection.executemany(
                    """
                    UPDATE cache_entries
                    SET status = ?, status_changed_at = ?,
                        quarantine_reason = ?
                    WHERE entry_id = ? AND status = ?
                    """,
                    (
                        (
                            ENTRY_INVALID,
                            now,
                            "garbage collection could not remove data file",
                            entry_id,
                            ENTRY_DELETING,
                        )
                        for entry_id in failed_entry_ids
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _statistics_sync(self) -> FileCacheProviderStatistics:
        with closing(self._connect()) as connection:
            current = connection.execute(
                "SELECT COUNT(*) FROM cache_entries WHERE status = ? AND is_current = 1",
                (ENTRY_COMMITTED,),
            ).fetchone()[0]
            retired = connection.execute(
                "SELECT COUNT(*) FROM cache_entries WHERE status = ? AND is_current = 0",
                (ENTRY_COMMITTED,),
            ).fetchone()[0]
            invalid = connection.execute(
                "SELECT COUNT(*) FROM cache_entries WHERE status = ?",
                (ENTRY_INVALID,),
            ).fetchone()[0]
            total_bytes = connection.execute(
                "SELECT COALESCE(SUM(data_size), 0) FROM cache_entries"
            ).fetchone()[0]
            reads = connection.execute(
                "SELECT COUNT(*) FROM read_reservations"
            ).fetchone()[0]
            writes = connection.execute(
                "SELECT COUNT(*) FROM write_reservations"
            ).fetchone()[0]
        return FileCacheProviderStatistics(
            current_entry_count=int(current),
            retired_entry_count=int(retired),
            invalid_entry_count=int(invalid),
            total_entry_bytes=int(total_bytes),
            read_reservation_count=int(reads),
            write_reservation_count=int(writes),
        )

    def _vacuum_sync(self) -> None:
        # VACUUM must run outside a transaction. WAL checkpointing first keeps
        # the database and WAL compact after deletion-heavy collections.
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("VACUUM")
            connection.execute("PRAGMA optimize")

    def _referenced_paths_sync(self) -> set[str]:
        """Return every data or staging path currently referenced by SQLite."""

        with closing(self._connect()) as connection:
            paths = {
                row[0]
                for row in connection.execute(
                    "SELECT relative_path FROM cache_entries"
                )
            }
            for row in connection.execute(
                """
                SELECT final_relative_path, temporary_relative_path
                FROM write_reservations
                """
            ):
                paths.add(row[0])
                if row[1]:
                    paths.add(row[1])
            return paths
