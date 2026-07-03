//! Public maintenance, statistics, and event models.

use std::sync::Arc;

#[derive(Clone, Debug, Default)]
pub struct FileCacheProviderRecoveryResult {
    pub stale_read_reservations_released: usize,
    pub stale_write_reservations_released: usize,
    pub deleting_entries_recovered: usize,
    pub files_removed: usize,
}

#[derive(Clone, Debug, Default)]
pub struct FileCacheProviderGarbageCollectionResult {
    pub entries_deleted: usize,
    pub bytes_deleted: u64,
    pub orphan_files_deleted: usize,
    pub stale_read_reservations_released: usize,
    pub stale_write_reservations_released: usize,
    pub vacuum_performed: bool,
}

#[derive(Clone, Debug, Default)]
pub struct FileCacheProviderStatistics {
    pub current_entry_count: u64,
    pub retired_entry_count: u64,
    pub invalid_entry_count: u64,
    pub total_entry_bytes: u64,
    pub read_reservation_count: u64,
    pub write_reservation_count: u64,
}

#[derive(Clone, Debug, Default)]
pub struct FileCacheProviderEvent {
    pub event_type: String,
    pub fingerprint: Option<String>,
    pub artifact_id: Option<String>,
    pub reservation_id: Option<String>,
    pub owner_task_id: Option<String>,
    pub reason: Option<String>,
    pub timestamp: f64,
}

pub type FileCacheProviderEventHandler = Arc<dyn Fn(FileCacheProviderEvent) + Send + Sync>;
