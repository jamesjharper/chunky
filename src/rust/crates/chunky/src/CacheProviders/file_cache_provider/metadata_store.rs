//! SQLite metadata and reservation operations for `FileCacheProvider`.
//!
//! This module is deliberately synchronous. The public provider executes these
//! short transactions through `tokio::task::spawn_blocking`, mirroring the
//! Python implementation's `asyncio.to_thread` boundary.

use super::{
    configuration::FileCacheProviderConfig,
    constants::{ENTRY_COMMITTED, ENTRY_DELETING, ENTRY_INVALID, SCHEMA_VERSION},
    descriptors::{
        CacheHitDescriptor, CacheResolveDescriptor, CacheWaitDescriptor, CacheWriteDescriptor,
    },
    filesystem::FileCacheProviderFilesystem,
};
use crate::artifact_models::AnyArtifactDeclaration;
use anyhow::{anyhow, bail, Result};
use nix::sys::signal;
use nix::unistd::Pid;
use rusqlite::{params, Connection, OptionalExtension, Row};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::Arc,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

pub type ReservationKind = String;

#[derive(Clone)]
pub struct FileCacheProviderMetadataStore {
    pub root: PathBuf,
    pub objects_root: PathBuf,
    pub database_path: PathBuf,
    pub config: Arc<FileCacheProviderConfig>,
    pub filesystem: Arc<FileCacheProviderFilesystem>,
    pub owner_task_id: String,
    pub owner_invocation_id: Option<String>,
    pub owner_stage_id: Option<String>,
    owner_process_id: u32,
    owner_host_id: String,
    owner_process_instance_id: String,
}

impl FileCacheProviderMetadataStore {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        root: PathBuf,
        objects_root: PathBuf,
        database_path: PathBuf,
        config: Arc<FileCacheProviderConfig>,
        filesystem: Arc<FileCacheProviderFilesystem>,
        owner_task_id: String,
        owner_invocation_id: Option<String>,
        owner_stage_id: Option<String>,
        owner_process_id: u32,
        owner_host_id: String,
        owner_process_instance_id: String,
    ) -> Result<Self> {
        let store = Self {
            root,
            objects_root,
            database_path,
            config,
            filesystem,
            owner_task_id,
            owner_invocation_id,
            owner_stage_id,
            owner_process_id,
            owner_host_id,
            owner_process_instance_id,
        };
        store.initialize_database()?;
        Ok(store)
    }

    pub fn connect(&self) -> Result<Connection> {
        let connection = Connection::open(&self.database_path)?;
        connection.execute_batch("PRAGMA foreign_keys = ON")?;
        connection.busy_timeout(Duration::from_secs_f64(
            self.config.sqlite_busy_timeout_seconds,
        ))?;
        Ok(connection)
    }

    fn initialize_database(&self) -> Result<()> {
        fs::create_dir_all(&self.root)?;
        fs::create_dir_all(&self.objects_root)?;
        let deadline =
            Instant::now() + Duration::from_secs_f64(self.config.sqlite_busy_timeout_seconds);

        loop {
            let result = (|| -> Result<()> {
                let connection = self.connect()?;
                connection.execute_batch(
                    r#"
                    PRAGMA journal_mode = WAL;
                    PRAGMA synchronous = FULL;

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
                    "#,
                )?;
                connection.execute(
                    "INSERT OR IGNORE INTO cache_schema(singleton, schema_version) VALUES (1, ?)",
                    params![SCHEMA_VERSION],
                )?;
                let version: Option<i64> = connection
                    .query_row(
                        "SELECT schema_version FROM cache_schema WHERE singleton = 1",
                        [],
                        |row| row.get(0),
                    )
                    .optional()?;
                if version != Some(SCHEMA_VERSION) {
                    bail!("unsupported cache database schema version: {version:?}");
                }
                Ok(())
            })();

            match result {
                Ok(()) => return Ok(()),
                Err(error)
                    if error.to_string().to_lowercase().contains("locked")
                        && Instant::now() < deadline =>
                {
                    std::thread::sleep(Duration::from_millis(50));
                }
                Err(error) => return Err(error),
            }
        }
    }

    pub fn resolve_sync(
        &self,
        declaration: &AnyArtifactDeclaration,
        fingerprint: &str,
        force_write: bool,
    ) -> Result<(CacheResolveDescriptor, Vec<PathBuf>)> {
        if fingerprint.is_empty() {
            bail!("fingerprint must not be empty");
        }
        let mut cleanup_paths = Vec::new();
        let now = now_seconds()?;
        let connection = self.connect()?;
        connection.execute_batch("BEGIN IMMEDIATE")?;
        let result = (|| -> Result<(CacheResolveDescriptor, Vec<PathBuf>)> {
            let writer: Option<WriteReservationRow> = connection
                .query_row(
                    "SELECT * FROM write_reservations WHERE fingerprint = ?",
                    params![fingerprint],
                    WriteReservationRow::from_row,
                )
                .optional()?;
            let writer = if let Some(writer) = writer {
                if self.reservation_row_is_stale(
                    &writer.owner_host_id,
                    writer.owner_process_id,
                    writer.heartbeat_at,
                    now,
                ) {
                    connection.execute(
                        "DELETE FROM write_reservations WHERE reservation_id = ?",
                        params![writer.reservation_id],
                    )?;
                    cleanup_paths.extend(self.paths_from_write_row(&writer));
                    None
                } else {
                    Some(writer)
                }
            } else {
                None
            };

            if !force_write {
                let entry: Option<CacheEntryRow> = connection
                    .query_row(
                        r#"
                        SELECT * FROM cache_entries
                        WHERE fingerprint = ? AND is_current = 1 AND status = ?
                        "#,
                        params![fingerprint, ENTRY_COMMITTED],
                        CacheEntryRow::from_row,
                    )
                    .optional()?;
                if let Some(entry) = entry {
                    if let Some(validation_error) =
                        self.validate_entry_row(&entry, declaration, fingerprint)?
                    {
                        connection.execute(
                            r#"
                            UPDATE cache_entries
                            SET status = ?, is_current = 0,
                                status_changed_at = ?, quarantine_reason = ?
                            WHERE entry_id = ?
                            "#,
                            params![ENTRY_INVALID, now, validation_error, entry.entry_id],
                        )?;
                    } else {
                        let reservation_id = Uuid::new_v4().simple().to_string();
                        connection.execute(
                            r#"
                            INSERT INTO read_reservations(
                                reservation_id, entry_id, fingerprint,
                                owner_task_id, owner_invocation_id,
                                owner_stage_id, owner_process_id,
                                owner_host_id, owner_process_instance_id,
                                acquired_at, heartbeat_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            "#,
                            params![
                                reservation_id,
                                entry.entry_id,
                                fingerprint,
                                self.owner_task_id,
                                self.owner_invocation_id,
                                self.owner_stage_id,
                                self.owner_process_id,
                                self.owner_host_id,
                                self.owner_process_instance_id,
                                now,
                                now,
                            ],
                        )?;
                        connection.execute(
                            "UPDATE cache_entries SET last_used_at = ? WHERE entry_id = ?",
                            params![now, entry.entry_id],
                        )?;
                        connection.execute_batch("COMMIT")?;
                        return Ok((
                            CacheResolveDescriptor::Hit(CacheHitDescriptor {
                                entry_id: entry.entry_id,
                                reservation_id,
                                data_path: self.root.join(entry.relative_path),
                                data_size: entry.data_size,
                                data_digest: entry.data_digest,
                                fingerprint: fingerprint.to_owned(),
                            }),
                            cleanup_paths,
                        ));
                    }
                }
            }

            if let Some(writer) = writer {
                connection.execute_batch("COMMIT")?;
                return Ok((
                    CacheResolveDescriptor::Wait(CacheWaitDescriptor {
                        reservation_id: writer.reservation_id,
                        fingerprint: fingerprint.to_owned(),
                        owner_task_id: writer.owner_task_id,
                    }),
                    cleanup_paths,
                ));
            }

            let reservation_id = Uuid::new_v4().simple().to_string();
            let entry_id = Uuid::new_v4().simple().to_string();
            let final_path = self.filesystem.new_generation_path(
                fingerprint,
                &entry_id,
                declaration.codec.extension(),
            );
            let final_relative_path = self.filesystem.relative_path(&final_path)?;
            connection.execute(
                r#"
                INSERT INTO write_reservations(
                    fingerprint, reservation_id, pending_entry_id,
                    final_relative_path, owner_task_id,
                    owner_invocation_id, owner_stage_id,
                    owner_process_id, owner_host_id,
                    owner_process_instance_id, acquired_at, heartbeat_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                "#,
                params![
                    fingerprint,
                    reservation_id,
                    entry_id,
                    final_relative_path,
                    self.owner_task_id,
                    self.owner_invocation_id,
                    self.owner_stage_id,
                    self.owner_process_id,
                    self.owner_host_id,
                    self.owner_process_instance_id,
                    now,
                    now,
                ],
            )?;
            connection.execute_batch("COMMIT")?;
            Ok((
                CacheResolveDescriptor::Write(CacheWriteDescriptor {
                    reservation_id,
                    entry_id,
                    fingerprint: fingerprint.to_owned(),
                    final_path,
                }),
                cleanup_paths,
            ))
        })();
        if result.is_err() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        result
    }

    fn validate_entry_row(
        &self,
        row: &CacheEntryRow,
        declaration: &AnyArtifactDeclaration,
        fingerprint: &str,
    ) -> Result<Option<String>> {
        let expected = [
            (
                "fingerprint",
                fingerprint.to_owned(),
                row.fingerprint.clone(),
            ),
            (
                "artifact_id",
                declaration.artifact_id.to_string(),
                row.artifact_id.clone(),
            ),
            (
                "artifact_role",
                declaration.role.as_str().to_owned(),
                row.artifact_role.clone(),
            ),
            (
                "artifact_version",
                declaration.version.to_string(),
                row.artifact_version.to_string(),
            ),
            (
                "codec_id",
                declaration.codec.codec_id().to_owned(),
                row.codec_id.clone(),
            ),
            (
                "codec_version",
                declaration.codec.codec_version().to_string(),
                row.codec_version.to_string(),
            ),
            (
                "mime_type",
                declaration.codec.mime_type().to_owned(),
                row.mime_type.clone(),
            ),
            (
                "extension",
                declaration.codec.extension().to_owned(),
                row.extension.clone(),
            ),
        ];
        for (field, expected_value, found_value) in expected {
            if expected_value != found_value {
                return Ok(Some(format!(
                    "metadata mismatch for {field}: expected {expected_value:?}, found {found_value:?}"
                )));
            }
        }
        let path = self.root.join(&row.relative_path);
        let metadata = match fs::metadata(&path) {
            Ok(value) => value,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Ok(Some("data file is missing".to_owned()));
            }
            Err(error) => return Err(error.into()),
        };
        if !metadata.is_file() {
            return Ok(Some("data path is not a regular file".to_owned()));
        }
        if metadata.len() != row.data_size {
            return Ok(Some("serialized size does not match metadata".to_owned()));
        }
        Ok(None)
    }

    pub fn record_preparing_write_sync(
        &self,
        reservation_id: &str,
        temporary_path: &Path,
    ) -> Result<()> {
        let relative_temp = self.filesystem.relative_path(temporary_path)?;
        let connection = self.connect()?;
        let changed = connection.execute(
            r#"
            UPDATE write_reservations
            SET temporary_relative_path = ?, heartbeat_at = ?
            WHERE reservation_id = ? AND owner_process_instance_id = ?
            "#,
            params![
                relative_temp,
                now_seconds()?,
                reservation_id,
                self.owner_process_instance_id
            ],
        )?;
        if changed != 1 {
            bail!("cache write reservation was lost: {reservation_id}");
        }
        Ok(())
    }

    pub fn record_prepared_write_sync(
        &self,
        reservation_id: &str,
        temporary_path: &Path,
        data_size: u64,
        data_digest: &str,
    ) -> Result<()> {
        let relative_temp = self.filesystem.relative_path(temporary_path)?;
        let connection = self.connect()?;
        connection.execute_batch("BEGIN IMMEDIATE")?;
        let result = (|| -> Result<()> {
            let changed = connection.execute(
                r#"
                UPDATE write_reservations
                SET temporary_relative_path = ?, prepared_size = ?,
                    prepared_digest = ?, heartbeat_at = ?
                WHERE reservation_id = ? AND owner_process_instance_id = ?
                "#,
                params![
                    relative_temp,
                    data_size,
                    data_digest,
                    now_seconds()?,
                    reservation_id,
                    self.owner_process_instance_id,
                ],
            )?;
            if changed != 1 {
                bail!("cache write reservation was lost: {reservation_id}");
            }
            connection.execute_batch("COMMIT")?;
            Ok(())
        })();
        if result.is_err() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        result
    }

    #[allow(clippy::too_many_arguments)]
    pub fn commit_write_sync(
        &self,
        reservation_id: &str,
        entry_id: &str,
        fingerprint: &str,
        declaration: &AnyArtifactDeclaration,
        temporary_path: &Path,
        final_path: &Path,
        data_size: u64,
        data_digest: &str,
    ) -> Result<()> {
        let now = now_seconds()?;
        let mut moved = false;
        let connection = self.connect()?;
        connection.execute_batch("BEGIN IMMEDIATE")?;
        let result = (|| -> Result<()> {
            let reservation: WriteReservationRow = connection
                .query_row(
                    "SELECT * FROM write_reservations WHERE reservation_id = ?",
                    params![reservation_id],
                    WriteReservationRow::from_row,
                )
                .optional()?
                .ok_or_else(|| anyhow!("cache write reservation was lost: {reservation_id}"))?;
            if reservation.fingerprint != fingerprint
                || reservation.pending_entry_id != entry_id
                || reservation.owner_process_instance_id != self.owner_process_instance_id
            {
                bail!("cache write reservation was lost: {reservation_id}");
            }
            if self.root.join(&reservation.final_relative_path) != final_path {
                bail!("reserved final cache path changed unexpectedly");
            }
            let temporary_relative_path = reservation
                .temporary_relative_path
                .clone()
                .ok_or_else(|| anyhow!("write reservation was not marked prepared"))?;
            if self.root.join(&temporary_relative_path) != temporary_path {
                bail!("prepared temporary cache path changed unexpectedly");
            }
            if reservation.prepared_size != Some(data_size) {
                bail!("prepared size changed unexpectedly");
            }
            if reservation.prepared_digest.as_deref() != Some(data_digest) {
                bail!("prepared digest changed unexpectedly");
            }

            if let Some(parent) = final_path.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::rename(temporary_path, final_path)?;
            moved = true;
            if self.config.fsync_directories {
                FileCacheProviderFilesystem::fsync_directory(
                    final_path.parent().unwrap_or_else(|| Path::new(".")),
                )?;
            }

            connection.execute(
                r#"
                UPDATE cache_entries
                SET is_current = 0, retired_at = ?, status_changed_at = ?
                WHERE fingerprint = ? AND is_current = 1
                "#,
                params![now, now, fingerprint],
            )?;
            connection.execute(
                r#"
                INSERT INTO cache_entries(
                    entry_id, fingerprint, artifact_id, artifact_role,
                    artifact_version, codec_id, codec_version, mime_type,
                    extension, relative_path, data_size, data_digest,
                    status, is_current, created_at, last_used_at,
                    status_changed_at, producing_task_id,
                    producing_invocation_id, producing_stage_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                "#,
                params![
                    entry_id,
                    fingerprint,
                    declaration.artifact_id.to_string(),
                    declaration.role.as_str(),
                    declaration.version,
                    declaration.codec.codec_id(),
                    declaration.codec.codec_version(),
                    declaration.codec.mime_type(),
                    declaration.codec.extension(),
                    self.filesystem.relative_path(final_path)?,
                    data_size,
                    data_digest,
                    ENTRY_COMMITTED,
                    now,
                    now,
                    now,
                    self.owner_task_id,
                    self.owner_invocation_id,
                    self.owner_stage_id,
                ],
            )?;
            connection.execute(
                "DELETE FROM write_reservations WHERE reservation_id = ?",
                params![reservation_id],
            )?;
            connection.execute_batch("COMMIT")?;
            Ok(())
        })();
        if let Err(error) = result {
            let _ = connection.execute_batch("ROLLBACK");
            if self.entry_commit_exists(entry_id, fingerprint)? {
                return Ok(());
            }
            if moved {
                let _ = fs::remove_file(final_path);
            }
            return Err(error);
        }
        Ok(())
    }

    pub fn entry_commit_exists(&self, entry_id: &str, fingerprint: &str) -> Result<bool> {
        let connection = self.connect()?;
        let exists: Option<i64> = connection
            .query_row(
                "SELECT 1 FROM cache_entries WHERE entry_id = ? AND fingerprint = ? AND status = ?",
                params![entry_id, fingerprint, ENTRY_COMMITTED],
                |row| row.get(0),
            )
            .optional()?;
        Ok(exists.is_some())
    }

    pub fn abort_write_sync(
        &self,
        reservation_id: &str,
        entry_id: &str,
        temporary_path: Option<&Path>,
        final_path: &Path,
    ) -> Result<Vec<PathBuf>> {
        let mut cleanup = Vec::new();
        let connection = self.connect()?;
        connection.execute_batch("BEGIN IMMEDIATE")?;
        let result = (|| -> Result<()> {
            let row: Option<WriteReservationRow> = connection
                .query_row(
                    "SELECT * FROM write_reservations WHERE reservation_id = ?",
                    params![reservation_id],
                    WriteReservationRow::from_row,
                )
                .optional()?;
            if let Some(row) = row {
                if row.pending_entry_id != entry_id {
                    bail!("write reservation entry identity changed");
                }
                if row.owner_process_instance_id != self.owner_process_instance_id {
                    bail!("cannot abort another owner's reservation: {reservation_id}");
                }
                if let Some(temp) = row.temporary_relative_path {
                    cleanup.push(self.root.join(temp));
                }
                cleanup.push(self.root.join(row.final_relative_path));
                connection.execute(
                    "DELETE FROM write_reservations WHERE reservation_id = ?",
                    params![reservation_id],
                )?;
            }
            connection.execute_batch("COMMIT")?;
            Ok(())
        })();
        if result.is_err() {
            let _ = connection.execute_batch("ROLLBACK");
            result?;
        }
        if let Some(temporary_path) = temporary_path {
            cleanup.push(temporary_path.to_path_buf());
        }
        if !self.entry_commit_exists(entry_id, &self.fingerprint_for_entry_id(entry_id)?)? {
            cleanup.push(final_path.to_path_buf());
        }
        dedup_paths(cleanup)
    }

    pub fn fingerprint_for_entry_id(&self, entry_id: &str) -> Result<String> {
        let connection = self.connect()?;
        Ok(connection
            .query_row(
                "SELECT fingerprint FROM cache_entries WHERE entry_id = ?",
                params![entry_id],
                |row| row.get(0),
            )
            .optional()?
            .unwrap_or_default())
    }

    pub fn heartbeat_sync(&self, kind: &str, reservation_id: &str) -> Result<bool> {
        let table = if kind == "read" {
            "read_reservations"
        } else {
            "write_reservations"
        };
        let connection = self.connect()?;
        let sql = format!(
            "UPDATE {table} SET heartbeat_at = ? WHERE reservation_id = ? AND owner_process_instance_id = ?"
        );
        Ok(connection.execute(
            &sql,
            params![
                now_seconds()?,
                reservation_id,
                self.owner_process_instance_id
            ],
        )? == 1)
    }

    pub fn touch_read_reservation_sync(&self, reservation_id: &str) -> Result<bool> {
        let now = now_seconds()?;
        let connection = self.connect()?;
        connection.execute_batch("BEGIN IMMEDIATE")?;
        let result = (|| -> Result<bool> {
            let entry_id: Option<String> = connection
                .query_row(
                    r#"
                    SELECT entry_id FROM read_reservations
                    WHERE reservation_id = ? AND owner_process_instance_id = ?
                    "#,
                    params![reservation_id, self.owner_process_instance_id],
                    |row| row.get(0),
                )
                .optional()?;
            let Some(entry_id) = entry_id else {
                connection.execute_batch("COMMIT")?;
                return Ok(false);
            };
            connection.execute(
                "UPDATE read_reservations SET heartbeat_at = ? WHERE reservation_id = ?",
                params![now, reservation_id],
            )?;
            connection.execute(
                "UPDATE cache_entries SET last_used_at = ? WHERE entry_id = ?",
                params![now, entry_id],
            )?;
            connection.execute_batch("COMMIT")?;
            Ok(true)
        })();
        if result.is_err() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        result
    }

    pub fn release_read_reservation_sync(&self, reservation_id: &str) -> Result<bool> {
        let connection = self.connect()?;
        Ok(connection.execute(
            r#"
            DELETE FROM read_reservations
            WHERE reservation_id = ? AND owner_process_instance_id = ?
            "#,
            params![reservation_id, self.owner_process_instance_id],
        )? == 1)
    }

    pub fn observed_writer_is_active_sync(
        &self,
        fingerprint: &str,
        observed_reservation_id: &str,
    ) -> Result<(bool, Vec<PathBuf>)> {
        let mut cleanup = Vec::new();
        let now = now_seconds()?;
        let connection = self.connect()?;
        connection.execute_batch("BEGIN IMMEDIATE")?;
        let result = (|| -> Result<(bool, Vec<PathBuf>)> {
            let row: Option<WriteReservationRow> = connection
                .query_row(
                    "SELECT * FROM write_reservations WHERE fingerprint = ? AND reservation_id = ?",
                    params![fingerprint, observed_reservation_id],
                    WriteReservationRow::from_row,
                )
                .optional()?;
            let Some(row) = row else {
                connection.execute_batch("COMMIT")?;
                return Ok((false, Vec::new()));
            };
            if self.reservation_row_is_stale(
                &row.owner_host_id,
                row.owner_process_id,
                row.heartbeat_at,
                now,
            ) {
                connection.execute(
                    "DELETE FROM write_reservations WHERE reservation_id = ?",
                    params![observed_reservation_id],
                )?;
                cleanup.extend(self.paths_from_write_row(&row));
                connection.execute_batch("COMMIT")?;
                return Ok((false, cleanup));
            }
            connection.execute_batch("COMMIT")?;
            Ok((true, Vec::new()))
        })();
        if result.is_err() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        result
    }

    pub fn invalidate_entry_sync(&self, entry_id: &str, reason: &str) -> Result<bool> {
        let connection = self.connect()?;
        Ok(connection.execute(
            r#"
            UPDATE cache_entries
            SET status = ?, is_current = 0, status_changed_at = ?, quarantine_reason = ?
            WHERE entry_id = ? AND status != ?
            "#,
            params![
                ENTRY_INVALID,
                now_seconds()?,
                reason,
                entry_id,
                ENTRY_DELETING
            ],
        )? == 1)
    }

    pub fn reservation_row_is_stale(
        &self,
        owner_host_id: &str,
        owner_process_id: u32,
        heartbeat_at: f64,
        now: f64,
    ) -> bool {
        if owner_host_id == self.owner_host_id && !pid_is_alive(owner_process_id) {
            return true;
        }
        now - heartbeat_at > self.config.reservation_lease_seconds
    }

    fn paths_from_write_row(&self, row: &WriteReservationRow) -> Vec<PathBuf> {
        let mut paths = vec![self.root.join(&row.final_relative_path)];
        if let Some(temp) = &row.temporary_relative_path {
            paths.push(self.root.join(temp));
        }
        paths
    }
}

#[derive(Clone, Debug)]
pub struct CacheEntryRow {
    pub entry_id: String,
    pub fingerprint: String,
    pub artifact_id: String,
    pub artifact_role: String,
    pub artifact_version: u32,
    pub codec_id: String,
    pub codec_version: u32,
    pub mime_type: String,
    pub extension: String,
    pub relative_path: String,
    pub data_size: u64,
    pub data_digest: String,
    pub status: String,
    pub is_current: bool,
    pub created_at: f64,
    pub last_used_at: f64,
    pub status_changed_at: f64,
}

impl CacheEntryRow {
    pub fn from_row(row: &Row<'_>) -> rusqlite::Result<Self> {
        Ok(Self {
            entry_id: row.get("entry_id")?,
            fingerprint: row.get("fingerprint")?,
            artifact_id: row.get("artifact_id")?,
            artifact_role: row.get("artifact_role")?,
            artifact_version: row.get::<_, i64>("artifact_version")? as u32,
            codec_id: row.get("codec_id")?,
            codec_version: row.get::<_, i64>("codec_version")? as u32,
            mime_type: row.get("mime_type")?,
            extension: row.get("extension")?,
            relative_path: row.get("relative_path")?,
            data_size: row.get::<_, i64>("data_size")? as u64,
            data_digest: row.get("data_digest")?,
            status: row.get("status")?,
            is_current: row.get::<_, i64>("is_current")? != 0,
            created_at: row.get("created_at")?,
            last_used_at: row.get("last_used_at")?,
            status_changed_at: row.get("status_changed_at")?,
        })
    }
}

#[derive(Clone, Debug)]
pub struct WriteReservationRow {
    pub fingerprint: String,
    pub reservation_id: String,
    pub pending_entry_id: String,
    pub final_relative_path: String,
    pub temporary_relative_path: Option<String>,
    pub prepared_size: Option<u64>,
    pub prepared_digest: Option<String>,
    pub owner_task_id: Option<String>,
    pub owner_process_id: u32,
    pub owner_host_id: String,
    pub owner_process_instance_id: String,
    pub heartbeat_at: f64,
}

impl WriteReservationRow {
    pub fn from_row(row: &Row<'_>) -> rusqlite::Result<Self> {
        Ok(Self {
            fingerprint: row.get("fingerprint")?,
            reservation_id: row.get("reservation_id")?,
            pending_entry_id: row.get("pending_entry_id")?,
            final_relative_path: row.get("final_relative_path")?,
            temporary_relative_path: row.get("temporary_relative_path")?,
            prepared_size: row
                .get::<_, Option<i64>>("prepared_size")?
                .map(|value| value as u64),
            prepared_digest: row.get("prepared_digest")?,
            owner_task_id: row.get("owner_task_id")?,
            owner_process_id: row.get::<_, i64>("owner_process_id")? as u32,
            owner_host_id: row.get("owner_host_id")?,
            owner_process_instance_id: row.get("owner_process_instance_id")?,
            heartbeat_at: row.get("heartbeat_at")?,
        })
    }
}

pub fn now_seconds() -> Result<f64> {
    Ok(SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs_f64())
}

pub fn pid_is_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    match signal::kill(Pid::from_raw(pid as i32), None) {
        Ok(()) => true,
        Err(nix::errno::Errno::ESRCH) => false,
        Err(nix::errno::Errno::EPERM) => true,
        Err(_) => true,
    }
}

fn dedup_paths(paths: Vec<PathBuf>) -> Result<Vec<PathBuf>> {
    let mut seen = std::collections::HashSet::new();
    let mut result = Vec::new();
    for path in paths {
        if seen.insert(path.clone()) {
            result.push(path);
        }
    }
    Ok(result)
}
