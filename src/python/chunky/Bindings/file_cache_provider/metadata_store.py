from __future__ import annotations

"""SQLite metadata and reservation operations for ``FileCacheProvider``.

This module is deliberately synchronous. The public provider runs these short
SQLite transactions in worker threads, keeping the stage-facing API async while
using Python's standard-library SQLite driver for cross-process coordination.
"""

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, TypeVar, cast
from uuid import uuid4

from ...Artifacts.artifact_models import ArtifactDeclaration
from ._constants import (
    ENTRY_COMMITTED,
    ENTRY_DELETING,
    ENTRY_INVALID,
    SCHEMA_VERSION,
)
from ._descriptors import (
    CacheHitDescriptor,
    CacheWaitDescriptor,
    CacheWriteDescriptor,
)
from .configuration import FileCacheProviderConfig
from .errors import (
    FileCacheProviderError,
    FileCacheProviderReservationLostError,
)
from .filesystem import FileCacheProviderFilesystem

T = TypeVar("T")
ReservationKind = str


class FileCacheProviderMetadataStore:
    """Owns the SQLite schema and all atomic metadata transitions."""

    def __init__(
        self,
        *,
        root: Path,
        objects_root: Path,
        database_path: Path,
        config: FileCacheProviderConfig,
        filesystem: FileCacheProviderFilesystem,
        owner_task_id: str,
        owner_invocation_id: str | None,
        owner_stage_id: str | None,
        owner_process_id: int,
        owner_host_id: str,
        owner_process_instance_id: str,
    ) -> None:
        self.root = root
        self.objects_root = objects_root
        self.database_path = database_path
        self.config = config
        self.filesystem = filesystem
        self.owner_task_id = owner_task_id
        self.owner_invocation_id = owner_invocation_id
        self.owner_stage_id = owner_stage_id
        self._owner_process_id = owner_process_id
        self._owner_host_id = owner_host_id
        self._owner_process_instance_id = owner_process_instance_id

        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.config.sqlite_busy_timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            f"PRAGMA busy_timeout = {int(self.config.sqlite_busy_timeout_seconds * 1000)}"
        )
        return connection

    def _initialize_database(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.objects_root.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.config.sqlite_busy_timeout_seconds

        while True:
            try:
                with closing(self._connect()) as connection:
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.execute("PRAGMA synchronous = FULL")
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS cache_schema (
                            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                            schema_version INTEGER NOT NULL
                        );

                        CREATE TABLE IF NOT EXISTS cache_entries (
                            entry_id TEXT PRIMARY KEY,
                            fingerprint TEXT NOT NULL,
                            artifact_id TEXT NOT NULL,
                            artifact_role TEXT NOT NULL,
                            artifact_version INTEGER NOT NULL,
                            codec_id TEXT NOT NULL,
                            codec_version INTEGER NOT NULL,
                            mime_type TEXT NOT NULL,
                            extension TEXT NOT NULL,
                            relative_path TEXT NOT NULL UNIQUE,
                            data_size INTEGER NOT NULL,
                            data_digest TEXT NOT NULL,
                            status TEXT NOT NULL,
                            is_current INTEGER NOT NULL DEFAULT 0,
                            created_at REAL NOT NULL,
                            last_used_at REAL NOT NULL,
                            retired_at REAL,
                            status_changed_at REAL NOT NULL,
                            producing_task_id TEXT,
                            producing_invocation_id TEXT,
                            producing_stage_id TEXT,
                            quarantine_reason TEXT
                        );

                        CREATE UNIQUE INDEX IF NOT EXISTS cache_entries_one_current
                            ON cache_entries(fingerprint)
                            WHERE is_current = 1;
                        CREATE INDEX IF NOT EXISTS cache_entries_lru
                            ON cache_entries(status, is_current, last_used_at);
                        CREATE INDEX IF NOT EXISTS cache_entries_fingerprint
                            ON cache_entries(fingerprint);

                        CREATE TABLE IF NOT EXISTS read_reservations (
                            reservation_id TEXT PRIMARY KEY,
                            entry_id TEXT NOT NULL,
                            fingerprint TEXT NOT NULL,
                            owner_task_id TEXT,
                            owner_invocation_id TEXT,
                            owner_stage_id TEXT,
                            owner_process_id INTEGER NOT NULL,
                            owner_host_id TEXT NOT NULL,
                            owner_process_instance_id TEXT NOT NULL,
                            acquired_at REAL NOT NULL,
                            heartbeat_at REAL NOT NULL,
                            FOREIGN KEY(entry_id) REFERENCES cache_entries(entry_id)
                                ON DELETE RESTRICT
                        );
                        CREATE INDEX IF NOT EXISTS read_reservations_entry
                            ON read_reservations(entry_id);
                        CREATE INDEX IF NOT EXISTS read_reservations_owner_task
                            ON read_reservations(owner_task_id);

                        CREATE TABLE IF NOT EXISTS write_reservations (
                            fingerprint TEXT PRIMARY KEY,
                            reservation_id TEXT NOT NULL UNIQUE,
                            pending_entry_id TEXT NOT NULL UNIQUE,
                            final_relative_path TEXT NOT NULL UNIQUE,
                            temporary_relative_path TEXT,
                            prepared_size INTEGER,
                            prepared_digest TEXT,
                            owner_task_id TEXT,
                            owner_invocation_id TEXT,
                            owner_stage_id TEXT,
                            owner_process_id INTEGER NOT NULL,
                            owner_host_id TEXT NOT NULL,
                            owner_process_instance_id TEXT NOT NULL,
                            acquired_at REAL NOT NULL,
                            heartbeat_at REAL NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS write_reservations_owner_task
                            ON write_reservations(owner_task_id);
                        """
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO cache_schema(singleton, schema_version)
                        VALUES (1, ?)
                        """,
                        (SCHEMA_VERSION,),
                    )
                    row = connection.execute(
                        "SELECT schema_version FROM cache_schema WHERE singleton = 1"
                    ).fetchone()
                    if row is None or int(row[0]) != SCHEMA_VERSION:
                        raise FileCacheProviderError(
                            "unsupported cache database schema version: "
                            f"{None if row is None else row[0]}"
                        )
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def _resolve_sync(
        self,
        declaration: ArtifactDeclaration[T],
        fingerprint: str,
        force_write: bool,
    ) -> tuple[CacheHitDescriptor | CacheWriteDescriptor | CacheWaitDescriptor, tuple[Path, ...]]:
        if not fingerprint:
            raise ValueError("fingerprint must not be empty")
        cleanup_paths: list[Path] = []
        now = time.time()

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                writer = connection.execute(
                    "SELECT * FROM write_reservations WHERE fingerprint = ?",
                    (fingerprint,),
                ).fetchone()
                if writer is not None and self._reservation_row_is_stale(writer, now):
                    connection.execute(
                        "DELETE FROM write_reservations WHERE reservation_id = ?",
                        (writer["reservation_id"],),
                    )
                    cleanup_paths.extend(self._paths_from_write_row(writer))
                    writer = None

                if not force_write:
                    entry = connection.execute(
                        """
                        SELECT * FROM cache_entries
                        WHERE fingerprint = ?
                          AND is_current = 1
                          AND status = ?
                        """,
                        (fingerprint, ENTRY_COMMITTED),
                    ).fetchone()
                    if entry is not None:
                        validation_error = self._validate_entry_row(
                            entry,
                            declaration,
                            fingerprint,
                        )
                        if validation_error is None:
                            reservation_id = uuid4().hex
                            connection.execute(
                                """
                                INSERT INTO read_reservations(
                                    reservation_id, entry_id, fingerprint,
                                    owner_task_id, owner_invocation_id,
                                    owner_stage_id, owner_process_id,
                                    owner_host_id, owner_process_instance_id,
                                    acquired_at, heartbeat_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    reservation_id,
                                    entry["entry_id"],
                                    fingerprint,
                                    self.owner_task_id,
                                    self.owner_invocation_id,
                                    self.owner_stage_id,
                                    self._owner_process_id,
                                    self._owner_host_id,
                                    self._owner_process_instance_id,
                                    now,
                                    now,
                                ),
                            )
                            connection.execute(
                                "UPDATE cache_entries SET last_used_at = ? WHERE entry_id = ?",
                                (now, entry["entry_id"]),
                            )
                            connection.commit()
                            return (
                                CacheHitDescriptor(
                                    entry_id=entry["entry_id"],
                                    reservation_id=reservation_id,
                                    data_path=self.root / entry["relative_path"],
                                    data_size=int(entry["data_size"]),
                                    data_digest=cast(str, entry["data_digest"]),
                                    fingerprint=fingerprint,
                                ),
                                tuple(cleanup_paths),
                            )

                        connection.execute(
                            """
                            UPDATE cache_entries
                            SET status = ?, is_current = 0,
                                status_changed_at = ?, quarantine_reason = ?
                            WHERE entry_id = ?
                            """,
                            (
                                ENTRY_INVALID,
                                now,
                                validation_error,
                                entry["entry_id"],
                            ),
                        )

                if writer is not None:
                    connection.commit()
                    return (
                        CacheWaitDescriptor(
                            reservation_id=writer["reservation_id"],
                            fingerprint=fingerprint,
                            owner_task_id=writer["owner_task_id"],
                        ),
                        tuple(cleanup_paths),
                    )

                reservation_id = uuid4().hex
                entry_id = uuid4().hex
                final_path = self.filesystem.new_generation_path(
                    fingerprint,
                    entry_id,
                    declaration.codec.extension,
                )
                final_relative_path = self.filesystem.relative_path(final_path)
                connection.execute(
                    """
                    INSERT INTO write_reservations(
                        fingerprint, reservation_id, pending_entry_id,
                        final_relative_path, owner_task_id,
                        owner_invocation_id, owner_stage_id,
                        owner_process_id, owner_host_id,
                        owner_process_instance_id, acquired_at, heartbeat_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fingerprint,
                        reservation_id,
                        entry_id,
                        final_relative_path,
                        self.owner_task_id,
                        self.owner_invocation_id,
                        self.owner_stage_id,
                        self._owner_process_id,
                        self._owner_host_id,
                        self._owner_process_instance_id,
                        now,
                        now,
                    ),
                )
                connection.commit()
                return (
                    CacheWriteDescriptor(
                        reservation_id=reservation_id,
                        entry_id=entry_id,
                        fingerprint=fingerprint,
                        final_path=final_path,
                    ),
                    tuple(cleanup_paths),
                )
            except BaseException:
                connection.rollback()
                raise


    def _open_existing_sync(
        self,
        declaration: ArtifactDeclaration[T],
        fingerprint: str,
    ) -> tuple[CacheHitDescriptor | CacheWaitDescriptor | None, tuple[Path, ...]]:
        if not fingerprint:
            raise ValueError("fingerprint must not be empty")
        cleanup_paths: list[Path] = []
        now = time.time()

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                writer = connection.execute(
                    "SELECT * FROM write_reservations WHERE fingerprint = ?",
                    (fingerprint,),
                ).fetchone()
                if writer is not None and self._reservation_row_is_stale(writer, now):
                    connection.execute(
                        "DELETE FROM write_reservations WHERE reservation_id = ?",
                        (writer["reservation_id"],),
                    )
                    cleanup_paths.extend(self._paths_from_write_row(writer))
                    writer = None

                entry = connection.execute(
                    """
                    SELECT * FROM cache_entries
                    WHERE fingerprint = ?
                      AND is_current = 1
                      AND status = ?
                    """,
                    (fingerprint, ENTRY_COMMITTED),
                ).fetchone()
                if entry is not None:
                    validation_error = self._validate_entry_row(
                        entry,
                        declaration,
                        fingerprint,
                    )
                    if validation_error is None:
                        reservation_id = uuid4().hex
                        connection.execute(
                            """
                            INSERT INTO read_reservations(
                                reservation_id, entry_id, fingerprint,
                                owner_task_id, owner_invocation_id,
                                owner_stage_id, owner_process_id,
                                owner_host_id, owner_process_instance_id,
                                acquired_at, heartbeat_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                reservation_id,
                                entry["entry_id"],
                                fingerprint,
                                self.owner_task_id,
                                self.owner_invocation_id,
                                self.owner_stage_id,
                                self._owner_process_id,
                                self._owner_host_id,
                                self._owner_process_instance_id,
                                now,
                                now,
                            ),
                        )
                        connection.execute(
                            "UPDATE cache_entries SET last_used_at = ? WHERE entry_id = ?",
                            (now, entry["entry_id"]),
                        )
                        connection.commit()
                        return (
                            CacheHitDescriptor(
                                entry_id=entry["entry_id"],
                                reservation_id=reservation_id,
                                data_path=self.root / entry["relative_path"],
                                data_size=int(entry["data_size"]),
                                data_digest=cast(str, entry["data_digest"]),
                                fingerprint=fingerprint,
                            ),
                            tuple(cleanup_paths),
                        )
                    connection.execute(
                        """
                        UPDATE cache_entries
                        SET status = ?, is_current = 0,
                            status_changed_at = ?, quarantine_reason = ?
                        WHERE entry_id = ?
                        """,
                        (
                            ENTRY_INVALID,
                            now,
                            validation_error,
                            entry["entry_id"],
                        ),
                    )

                if writer is not None:
                    connection.commit()
                    return (
                        CacheWaitDescriptor(
                            reservation_id=writer["reservation_id"],
                            fingerprint=fingerprint,
                            owner_task_id=writer["owner_task_id"],
                        ),
                        tuple(cleanup_paths),
                    )

                connection.commit()
                return None, tuple(cleanup_paths)
            except BaseException:
                connection.rollback()
                raise

    def _validate_entry_row(
        self,
        row: sqlite3.Row,
        declaration: ArtifactDeclaration[Any],
        fingerprint: str,
    ) -> str | None:
        expected = {
            "fingerprint": fingerprint,
            "artifact_id": str(declaration.artifact_id),
            "artifact_role": declaration.role.value,
            "artifact_version": declaration.version,
            "codec_id": declaration.codec.codec_id,
            "codec_version": declaration.codec.codec_version,
            "mime_type": declaration.codec.mime_type,
            "extension": declaration.codec.extension,
        }
        for field, expected_value in expected.items():
            if row[field] != expected_value:
                return (
                    f"metadata mismatch for {field}: "
                    f"expected {expected_value!r}, found {row[field]!r}"
                )

        path = self.root / row["relative_path"]
        try:
            stat = path.stat()
        except FileNotFoundError:
            return "data file is missing"
        if not path.is_file():
            return "data path is not a regular file"
        if stat.st_size != int(row["data_size"]):
            return "serialized size does not match metadata"
        return None

    def _record_preparing_write_sync(
        self,
        reservation_id: str,
        temporary_path: Path,
    ) -> None:
        relative_temp = self.filesystem.relative_path(temporary_path)
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                UPDATE write_reservations
                SET temporary_relative_path = ?, heartbeat_at = ?
                WHERE reservation_id = ?
                  AND owner_process_instance_id = ?
                """,
                (
                    relative_temp,
                    time.time(),
                    reservation_id,
                    self._owner_process_instance_id,
                ),
            )
            if cursor.rowcount != 1:
                raise FileCacheProviderReservationLostError(
                    f"cache write reservation was lost: {reservation_id}"
                )

    def _record_prepared_write_sync(
        self,
        reservation_id: str,
        temporary_path: Path,
        data_size: int,
        data_digest: str,
    ) -> None:
        relative_temp = self.filesystem.relative_path(temporary_path)
        now = time.time()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE write_reservations
                    SET temporary_relative_path = ?, prepared_size = ?,
                        prepared_digest = ?, heartbeat_at = ?
                    WHERE reservation_id = ?
                      AND owner_process_instance_id = ?
                    """,
                    (
                        relative_temp,
                        data_size,
                        data_digest,
                        now,
                        reservation_id,
                        self._owner_process_instance_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise FileCacheProviderReservationLostError(
                        f"cache write reservation was lost: {reservation_id}"
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _commit_write_sync(
        self,
        reservation_id: str,
        entry_id: str,
        fingerprint: str,
        declaration: ArtifactDeclaration[Any],
        temporary_path: Path,
        final_path: Path,
        data_size: int,
        data_digest: str,
    ) -> None:
        now = time.time()
        moved = False
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            reservation = connection.execute(
                "SELECT * FROM write_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if (
                reservation is None
                or reservation["fingerprint"] != fingerprint
                or reservation["pending_entry_id"] != entry_id
                or reservation["owner_process_instance_id"]
                != self._owner_process_instance_id
            ):
                raise FileCacheProviderReservationLostError(
                    f"cache write reservation was lost: {reservation_id}"
                )
            if self.root / reservation["final_relative_path"] != final_path:
                raise FileCacheProviderError("reserved final cache path changed unexpectedly")
            if reservation["temporary_relative_path"] is None:
                raise FileCacheProviderError("write reservation was not marked prepared")
            if self.root / reservation["temporary_relative_path"] != temporary_path:
                raise FileCacheProviderError("prepared temporary cache path changed unexpectedly")
            if int(reservation["prepared_size"]) != data_size:
                raise FileCacheProviderError("prepared size changed unexpectedly")
            if reservation["prepared_digest"] != data_digest:
                raise FileCacheProviderError("prepared digest changed unexpectedly")

            final_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary_path, final_path)
            moved = True
            if self.config.fsync_directories:
                self.filesystem.fsync_directory(final_path.parent)

            connection.execute(
                """
                UPDATE cache_entries
                SET is_current = 0, retired_at = ?, status_changed_at = ?
                WHERE fingerprint = ? AND is_current = 1
                """,
                (now, now, fingerprint),
            )
            connection.execute(
                """
                INSERT INTO cache_entries(
                    entry_id, fingerprint, artifact_id, artifact_role,
                    artifact_version, codec_id, codec_version, mime_type,
                    extension, relative_path, data_size, data_digest,
                    status, is_current, created_at, last_used_at,
                    status_changed_at, producing_task_id,
                    producing_invocation_id, producing_stage_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    fingerprint,
                    str(declaration.artifact_id),
                    declaration.role.value,
                    declaration.version,
                    declaration.codec.codec_id,
                    declaration.codec.codec_version,
                    declaration.codec.mime_type,
                    declaration.codec.extension,
                    self.filesystem.relative_path(final_path),
                    data_size,
                    data_digest,
                    ENTRY_COMMITTED,
                    now,
                    now,
                    now,
                    self.owner_task_id,
                    self.owner_invocation_id,
                    self.owner_stage_id,
                ),
            )
            connection.execute(
                "DELETE FROM write_reservations WHERE reservation_id = ?",
                (reservation_id,),
            )
            connection.commit()
            return
        except BaseException:
            try:
                connection.rollback()
            finally:
                connection.close()

            # Resolve the only ambiguous case: SQLite may have committed before
            # an exception was surfaced. A matching entry proves success.
            if self._entry_commit_exists(entry_id, fingerprint):
                return
            if moved:
                try:
                    final_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        finally:
            try:
                connection.close()
            except Exception:
                pass

    def _entry_commit_exists(self, entry_id: str, fingerprint: str) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM cache_entries
                WHERE entry_id = ? AND fingerprint = ? AND status = ?
                """,
                (entry_id, fingerprint, ENTRY_COMMITTED),
            ).fetchone()
            return row is not None

    def _abort_write_sync(
        self,
        reservation_id: str,
        entry_id: str,
        temporary_path: Path | None,
        final_path: Path,
    ) -> tuple[Path, ...]:
        cleanup: list[Path] = []
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM write_reservations WHERE reservation_id = ?",
                    (reservation_id,),
                ).fetchone()
                if row is not None:
                    if row["pending_entry_id"] != entry_id:
                        raise FileCacheProviderError("write reservation entry identity changed")
                    if row["owner_process_instance_id"] != self._owner_process_instance_id:
                        raise FileCacheProviderReservationLostError(
                            f"cannot abort another owner's reservation: {reservation_id}"
                        )
                    if row["temporary_relative_path"]:
                        cleanup.append(self.root / row["temporary_relative_path"])
                    cleanup.append(self.root / row["final_relative_path"])
                    connection.execute(
                        "DELETE FROM write_reservations WHERE reservation_id = ?",
                        (reservation_id,),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

        if temporary_path is not None:
            cleanup.append(temporary_path)
        # final_path is immutable and unique to this uncommitted entry ID. It is
        # safe to remove only when the entry was not committed.
        if not self._entry_commit_exists(entry_id, self._fingerprint_for_entry_id(entry_id)):
            cleanup.append(final_path)
        return tuple(dict.fromkeys(cleanup))

    def _fingerprint_for_entry_id(self, entry_id: str) -> str:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT fingerprint FROM cache_entries WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()
            return "" if row is None else cast(str, row["fingerprint"])

    def _heartbeat_sync(
        self,
        kind: ReservationKind,
        reservation_id: str,
    ) -> bool:
        table = "read_reservations" if kind == "read" else "write_reservations"
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                f"""
                UPDATE {table}
                SET heartbeat_at = ?
                WHERE reservation_id = ?
                  AND owner_process_instance_id = ?
                """,
                (time.time(), reservation_id, self._owner_process_instance_id),
            )
            return cursor.rowcount == 1

    def _touch_read_reservation_sync(self, reservation_id: str) -> bool:
        now = time.time()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT entry_id FROM read_reservations
                    WHERE reservation_id = ?
                      AND owner_process_instance_id = ?
                    """,
                    (reservation_id, self._owner_process_instance_id),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return False
                connection.execute(
                    "UPDATE read_reservations SET heartbeat_at = ? WHERE reservation_id = ?",
                    (now, reservation_id),
                )
                connection.execute(
                    "UPDATE cache_entries SET last_used_at = ? WHERE entry_id = ?",
                    (now, row["entry_id"]),
                )
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise

    def _release_read_reservation_sync(self, reservation_id: str) -> bool:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                DELETE FROM read_reservations
                WHERE reservation_id = ?
                  AND owner_process_instance_id = ?
                """,
                (reservation_id, self._owner_process_instance_id),
            )
            return cursor.rowcount == 1

    def _observed_writer_is_active_sync(
        self,
        fingerprint: str,
        observed_reservation_id: str,
    ) -> tuple[bool, tuple[Path, ...]]:
        cleanup: list[Path] = []
        now = time.time()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT * FROM write_reservations
                    WHERE fingerprint = ? AND reservation_id = ?
                    """,
                    (fingerprint, observed_reservation_id),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return False, ()
                if self._reservation_row_is_stale(row, now):
                    connection.execute(
                        "DELETE FROM write_reservations WHERE reservation_id = ?",
                        (observed_reservation_id,),
                    )
                    cleanup.extend(self._paths_from_write_row(row))
                    connection.commit()
                    return False, tuple(cleanup)
                connection.commit()
                return True, ()
            except BaseException:
                connection.rollback()
                raise

    def _invalidate_entry_sync(self, entry_id: str, reason: str) -> bool:
        now = time.time()
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                UPDATE cache_entries
                SET status = ?, is_current = 0, status_changed_at = ?,
                    quarantine_reason = ?
                WHERE entry_id = ? AND status != ?
                """,
                (
                    ENTRY_INVALID,
                    now,
                    reason,
                    entry_id,
                    ENTRY_DELETING,
                ),
            )
            return cursor.rowcount == 1

    def _reservation_row_is_stale(self, row: sqlite3.Row, now: float) -> bool:
        if (
            row["owner_host_id"] == self._owner_host_id
            and not self._pid_is_alive(int(row["owner_process_id"]))
        ):
            return True
        return (
            now - float(row["heartbeat_at"])
            > self.config.reservation_lease_seconds
        )

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _paths_from_write_row(self, row: sqlite3.Row) -> tuple[Path, ...]:
        paths = [self.root / row["final_relative_path"]]
        if row["temporary_relative_path"]:
            paths.append(self.root / row["temporary_relative_path"])
        return tuple(paths)
