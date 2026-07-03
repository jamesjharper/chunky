//! Internal immutable values passed between SQLite operations and handles.

use std::path::PathBuf;

#[derive(Clone, Debug)]
pub struct CacheHitDescriptor {
    pub entry_id: String,
    pub reservation_id: String,
    pub data_path: PathBuf,
    pub data_size: u64,
    pub data_digest: String,
    pub fingerprint: String,
}

#[derive(Clone, Debug)]
pub struct CacheWriteDescriptor {
    pub reservation_id: String,
    pub entry_id: String,
    pub fingerprint: String,
    pub final_path: PathBuf,
}

#[derive(Clone, Debug)]
pub struct CacheWaitDescriptor {
    pub reservation_id: String,
    pub fingerprint: String,
    pub owner_task_id: Option<String>,
}

#[derive(Clone, Debug)]
pub struct ClaimedCacheEntry {
    pub entry_id: String,
    pub relative_path: String,
    pub data_size: u64,
}

#[derive(Clone, Debug, Default)]
pub struct CacheRecoveryPlan {
    pub stale_read_ids: Vec<String>,
    pub stale_write_rows: Vec<(String, Option<String>, String)>,
    pub deleting_rows: Vec<(String, String)>,
}

#[derive(Clone, Debug)]
pub enum CacheResolveDescriptor {
    Hit(CacheHitDescriptor),
    Write(CacheWriteDescriptor),
    Wait(CacheWaitDescriptor),
}
