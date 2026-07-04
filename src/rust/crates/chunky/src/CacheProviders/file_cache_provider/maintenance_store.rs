//! Maintenance-only SQLite operations for `FileCacheProvider`.

use super::{
    configuration::FileCacheProviderGarbageCollectionPolicy,
    constants::{ENTRY_COMMITTED, ENTRY_DELETING, ENTRY_INVALID},
    descriptors::{CacheRecoveryPlan, ClaimedCacheEntry},
    metadata_store::{
        now_seconds, CacheEntryRow, FileCacheProviderMetadataStore, WriteReservationRow,
    },
    models::FileCacheProviderStatistics,
};
use anyhow::Result;
use rusqlite::{params, OptionalExtension};
use std::{
    collections::{HashMap, HashSet},
    sync::Arc,
};

#[derive(Clone)]
pub struct FileCacheProviderMaintenanceStore {
    metadata_store: Arc<FileCacheProviderMetadataStore>,
}

impl FileCacheProviderMaintenanceStore {
    pub fn new(metadata_store: Arc<FileCacheProviderMetadataStore>) -> Self {
        Self { metadata_store }
    }

    pub fn build_recovery_plan_sync(&self) -> Result<CacheRecoveryPlan> {
        let now = now_seconds()?;
        let mut plan = CacheRecoveryPlan::default();
        let connection = self.metadata_store.connect()?;
        connection.execute_batch("BEGIN IMMEDIATE")?;
        let result = (|| -> Result<CacheRecoveryPlan> {
            let mut read_stmt = connection.prepare("SELECT * FROM read_reservations")?;
            let read_rows = read_stmt.query_map([], |row| {
                Ok((
                    row.get::<_, String>("reservation_id")?,
                    row.get::<_, String>("owner_host_id")?,
                    row.get::<_, i64>("owner_process_id")? as u32,
                    row.get::<_, f64>("heartbeat_at")?,
                ))
            })?;
            for row in read_rows {
                let (reservation_id, host_id, process_id, heartbeat_at) = row?;
                if self.metadata_store.reservation_row_is_stale(
                    &host_id,
                    process_id,
                    heartbeat_at,
                    now,
                ) {
                    plan.stale_read_ids.push(reservation_id);
                }
            }
            for reservation_id in &plan.stale_read_ids {
                connection.execute(
                    "DELETE FROM read_reservations WHERE reservation_id = ?",
                    params![reservation_id],
                )?;
            }

            let mut write_stmt = connection.prepare("SELECT * FROM write_reservations")?;
            let write_rows = write_stmt.query_map([], WriteReservationRow::from_row)?;
            for row in write_rows {
                let row = row?;
                if self.metadata_store.reservation_row_is_stale(
                    &row.owner_host_id,
                    row.owner_process_id,
                    row.heartbeat_at,
                    now,
                ) {
                    plan.stale_write_rows.push((
                        row.fingerprint.clone(),
                        row.temporary_relative_path.clone(),
                        row.final_relative_path.clone(),
                    ));
                    connection.execute(
                        "DELETE FROM write_reservations WHERE reservation_id = ?",
                        params![row.reservation_id],
                    )?;
                }
            }

            let mut deleting_stmt = connection
                .prepare("SELECT entry_id, relative_path FROM cache_entries WHERE status = ?")?;
            let deleting_rows = deleting_stmt.query_map(params![ENTRY_DELETING], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })?;
            for row in deleting_rows {
                let (entry_id, relative_path) = row?;
                let has_reader: Option<i64> = connection
                    .query_row(
                        "SELECT 1 FROM read_reservations WHERE entry_id = ? LIMIT 1",
                        params![entry_id],
                        |row| row.get(0),
                    )
                    .optional()?;
                if has_reader.is_none() {
                    plan.deleting_rows.push((entry_id, relative_path));
                }
            }
            connection.execute_batch("COMMIT")?;
            Ok(plan)
        })();
        if result.is_err() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        result
    }

    pub fn finalize_recovery_sync(&self, deleting_entry_ids: &[String]) -> Result<()> {
        if deleting_entry_ids.is_empty() {
            return Ok(());
        }
        let connection = self.metadata_store.connect()?;
        for entry_id in deleting_entry_ids {
            connection.execute(
                "DELETE FROM cache_entries WHERE entry_id = ? AND status = ?",
                params![entry_id, ENTRY_DELETING],
            )?;
        }
        Ok(())
    }

    pub fn claim_gc_entries_sync(
        &self,
        policy: &FileCacheProviderGarbageCollectionPolicy,
    ) -> Result<Vec<ClaimedCacheEntry>> {
        let now = now_seconds()?;
        let mut selected: HashMap<String, CacheEntryRow> = HashMap::new();
        let connection = self.metadata_store.connect()?;
        connection.execute_batch("BEGIN IMMEDIATE")?;
        let result = (|| -> Result<Vec<ClaimedCacheEntry>> {
            let mut stmt = connection.prepare(
                r#"
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
                "#,
            )?;
            let eligible_rows: Vec<CacheEntryRow> = stmt
                .query_map(
                    params![ENTRY_COMMITTED, ENTRY_INVALID],
                    CacheEntryRow::from_row,
                )?
                .collect::<rusqlite::Result<Vec<_>>>()?;

            for row in &eligible_rows {
                if !row.is_current {
                    selected.insert(row.entry_id.clone(), row.clone());
                } else if row.status == ENTRY_INVALID
                    && now - row.status_changed_at >= policy.invalid_retention_seconds
                {
                    selected.insert(row.entry_id.clone(), row.clone());
                } else if policy
                    .max_age_seconds
                    .is_some_and(|age| now - row.last_used_at >= age)
                {
                    selected.insert(row.entry_id.clone(), row.clone());
                }
            }

            let mut current_count: i64 = connection.query_row(
                "SELECT COUNT(*) FROM cache_entries WHERE status = ? AND is_current = 1",
                params![ENTRY_COMMITTED],
                |row| row.get(0),
            )?;
            let mut current_bytes: i64 = connection.query_row(
                "SELECT COALESCE(SUM(data_size), 0) FROM cache_entries WHERE status = ? AND is_current = 1",
                params![ENTRY_COMMITTED],
                |row| row.get(0),
            )?;

            for row in selected.values() {
                if row.is_current && row.status == ENTRY_COMMITTED {
                    current_count -= 1;
                    current_bytes -= row.data_size as i64;
                }
            }

            for row in eligible_rows.iter() {
                if !(row.is_current
                    && row.status == ENTRY_COMMITTED
                    && !selected.contains_key(&row.entry_id))
                {
                    continue;
                }
                let over_count = policy
                    .max_entry_count
                    .is_some_and(|limit| current_count > limit as i64);
                let over_bytes = policy
                    .max_total_bytes
                    .is_some_and(|limit| current_bytes > limit as i64);
                if !over_count && !over_bytes {
                    break;
                }
                selected.insert(row.entry_id.clone(), row.clone());
                current_count -= 1;
                current_bytes -= row.data_size as i64;
            }

            let mut rows: Vec<CacheEntryRow> = selected.into_values().collect();
            rows.sort_by(|left, right| {
                left.last_used_at
                    .partial_cmp(&right.last_used_at)
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then_with(|| {
                        left.created_at
                            .partial_cmp(&right.created_at)
                            .unwrap_or(std::cmp::Ordering::Equal)
                    })
            });
            if let Some(max_delete_entries) = policy.max_delete_entries {
                rows.truncate(max_delete_entries);
            }

            let mut claimed = Vec::new();
            for row in rows {
                let changed = connection.execute(
                    r#"
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
                    "#,
                    params![
                        ENTRY_DELETING,
                        now,
                        row.entry_id,
                        ENTRY_COMMITTED,
                        ENTRY_INVALID
                    ],
                )?;
                if changed == 1 {
                    claimed.push(ClaimedCacheEntry {
                        entry_id: row.entry_id,
                        relative_path: row.relative_path,
                        data_size: row.data_size,
                    });
                }
            }
            connection.execute_batch("COMMIT")?;
            Ok(claimed)
        })();
        if result.is_err() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        result
    }

    pub fn finalize_gc_entries_sync(
        &self,
        deleted_entry_ids: &[String],
        failed_entry_ids: &[String],
    ) -> Result<()> {
        let now = now_seconds()?;
        let connection = self.metadata_store.connect()?;
        connection.execute_batch("BEGIN IMMEDIATE")?;
        let result = (|| -> Result<()> {
            for entry_id in deleted_entry_ids {
                connection.execute(
                    "DELETE FROM cache_entries WHERE entry_id = ? AND status = ?",
                    params![entry_id, ENTRY_DELETING],
                )?;
            }
            for entry_id in failed_entry_ids {
                connection.execute(
                    r#"
                    UPDATE cache_entries
                    SET status = ?, status_changed_at = ?, quarantine_reason = ?
                    WHERE entry_id = ? AND status = ?
                    "#,
                    params![
                        ENTRY_INVALID,
                        now,
                        "garbage collection could not remove data file",
                        entry_id,
                        ENTRY_DELETING,
                    ],
                )?;
            }
            connection.execute_batch("COMMIT")?;
            Ok(())
        })();
        if result.is_err() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        result
    }

    pub fn statistics_sync(&self) -> Result<FileCacheProviderStatistics> {
        let connection = self.metadata_store.connect()?;
        let current = count_query(
            &connection,
            "SELECT COUNT(*) FROM cache_entries WHERE status = ? AND is_current = 1",
            ENTRY_COMMITTED,
        )?;
        let retired = count_query(
            &connection,
            "SELECT COUNT(*) FROM cache_entries WHERE status = ? AND is_current = 0",
            ENTRY_COMMITTED,
        )?;
        let invalid = count_query(
            &connection,
            "SELECT COUNT(*) FROM cache_entries WHERE status = ?",
            ENTRY_INVALID,
        )?;
        let total_entry_bytes: i64 = connection.query_row(
            "SELECT COALESCE(SUM(data_size), 0) FROM cache_entries",
            [],
            |row| row.get(0),
        )?;
        let reads: i64 =
            connection.query_row("SELECT COUNT(*) FROM read_reservations", [], |row| {
                row.get(0)
            })?;
        let writes: i64 =
            connection.query_row("SELECT COUNT(*) FROM write_reservations", [], |row| {
                row.get(0)
            })?;
        Ok(FileCacheProviderStatistics {
            current_entry_count: current as u64,
            retired_entry_count: retired as u64,
            invalid_entry_count: invalid as u64,
            total_entry_bytes: total_entry_bytes as u64,
            read_reservation_count: reads as u64,
            write_reservation_count: writes as u64,
        })
    }

    pub fn vacuum_sync(&self) -> Result<()> {
        let connection = self.metadata_store.connect()?;
        connection.execute_batch("PRAGMA wal_checkpoint(TRUNCATE); VACUUM; PRAGMA optimize;")?;
        Ok(())
    }

    pub fn referenced_paths_sync(&self) -> Result<HashSet<String>> {
        let connection = self.metadata_store.connect()?;
        let mut paths = HashSet::new();
        let mut entries = connection.prepare("SELECT relative_path FROM cache_entries")?;
        for row in entries.query_map([], |row| row.get::<_, String>(0))? {
            paths.insert(row?);
        }
        let mut writes = connection.prepare(
            "SELECT final_relative_path, temporary_relative_path FROM write_reservations",
        )?;
        let rows = writes.query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?))
        })?;
        for row in rows {
            let (final_path, temporary_path) = row?;
            paths.insert(final_path);
            if let Some(temporary_path) = temporary_path {
                paths.insert(temporary_path);
            }
        }
        Ok(paths)
    }
}

fn count_query(connection: &rusqlite::Connection, sql: &str, status: &str) -> Result<i64> {
    Ok(connection.query_row(sql, params![status], |row| row.get(0))?)
}
