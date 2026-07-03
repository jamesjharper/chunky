//! Configuration and garbage-collection policy for `FileCacheProvider`.

use anyhow::{bail, Result};

#[derive(Clone, Debug)]
pub struct FileCacheProviderConfig {
    pub sqlite_busy_timeout_seconds: f64,
    pub reservation_lease_seconds: f64,
    pub heartbeat_interval_seconds: f64,
    pub wait_poll_interval_seconds: f64,
    pub orphan_grace_seconds: f64,
    pub fsync_data: bool,
    pub fsync_directories: bool,
}

impl Default for FileCacheProviderConfig {
    fn default() -> Self {
        Self {
            sqlite_busy_timeout_seconds: 30.0,
            reservation_lease_seconds: 120.0,
            heartbeat_interval_seconds: 20.0,
            wait_poll_interval_seconds: 0.20,
            orphan_grace_seconds: 300.0,
            fsync_data: true,
            fsync_directories: true,
        }
    }
}

impl FileCacheProviderConfig {
    pub fn validate(&self) -> Result<()> {
        if self.sqlite_busy_timeout_seconds <= 0.0 {
            bail!("sqlite_busy_timeout_seconds must be positive");
        }
        if self.reservation_lease_seconds <= 0.0 {
            bail!("reservation_lease_seconds must be positive");
        }
        if self.heartbeat_interval_seconds <= 0.0 {
            bail!("heartbeat_interval_seconds must be positive");
        }
        if self.heartbeat_interval_seconds >= self.reservation_lease_seconds / 2.0 {
            bail!("heartbeat_interval_seconds must be less than half the lease duration");
        }
        if self.wait_poll_interval_seconds <= 0.0 {
            bail!("wait_poll_interval_seconds must be positive");
        }
        if self.orphan_grace_seconds < 0.0 {
            bail!("orphan_grace_seconds must not be negative");
        }
        Ok(())
    }
}

#[derive(Clone, Debug)]
pub struct FileCacheProviderGarbageCollectionPolicy {
    pub max_age_seconds: Option<f64>,
    pub max_total_bytes: Option<u64>,
    pub max_entry_count: Option<u64>,
    pub invalid_retention_seconds: f64,
    pub orphan_grace_seconds: Option<f64>,
    pub max_delete_entries: Option<usize>,
    pub vacuum: bool,
}

impl Default for FileCacheProviderGarbageCollectionPolicy {
    fn default() -> Self {
        Self {
            max_age_seconds: None,
            max_total_bytes: None,
            max_entry_count: None,
            invalid_retention_seconds: 0.0,
            orphan_grace_seconds: None,
            max_delete_entries: None,
            vacuum: true,
        }
    }
}

impl FileCacheProviderGarbageCollectionPolicy {
    pub fn validate(&self) -> Result<()> {
        if matches!(self.max_age_seconds, Some(value) if value < 0.0) {
            bail!("max_age_seconds must not be negative");
        }
        if self.invalid_retention_seconds < 0.0 {
            bail!("invalid_retention_seconds must not be negative");
        }
        if matches!(self.orphan_grace_seconds, Some(value) if value < 0.0) {
            bail!("orphan_grace_seconds must not be negative");
        }
        if matches!(self.max_delete_entries, Some(0)) {
            bail!("max_delete_entries must be positive");
        }
        Ok(())
    }
}
