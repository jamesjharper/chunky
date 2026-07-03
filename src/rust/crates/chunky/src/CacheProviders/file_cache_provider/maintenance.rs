//! Recovery, garbage collection, orphan cleanup, and SQLite vacuuming.

use super::{
    configuration::FileCacheProviderGarbageCollectionPolicy,
    maintenance_store::FileCacheProviderMaintenanceStore,
    models::{
        FileCacheProviderEvent, FileCacheProviderGarbageCollectionResult,
        FileCacheProviderRecoveryResult, FileCacheProviderStatistics,
    },
    provider::FileCacheProvider,
};
use anyhow::Result;
use std::path::PathBuf;

#[derive(Clone)]
pub struct FileCacheProviderMaintenance {
    provider: FileCacheProvider,
    store: FileCacheProviderMaintenanceStore,
}

impl FileCacheProviderMaintenance {
    pub fn new(provider: FileCacheProvider) -> Self {
        let store = FileCacheProviderMaintenanceStore::new(provider.metadata_store());
        Self { provider, store }
    }

    pub async fn recover(&self) -> Result<FileCacheProviderRecoveryResult> {
        let store = self.store.clone();
        let plan = tokio::task::spawn_blocking(move || store.build_recovery_plan_sync()).await??;

        let mut paths = Vec::<PathBuf>::new();
        for (_fingerprint, temporary_relative_path, final_relative_path) in &plan.stale_write_rows {
            if let Some(temporary_relative_path) = temporary_relative_path {
                paths.push(self.provider.root().join(temporary_relative_path));
            }
            paths.push(self.provider.root().join(final_relative_path));
        }
        for (_entry_id, relative_path) in &plan.deleting_rows {
            paths.push(self.provider.root().join(relative_path));
        }

        let filesystem = self.provider.filesystem();
        let files_removed = tokio::task::spawn_blocking(move || filesystem.remove_paths(paths)).await??;

        let store = self.store.clone();
        let deleting_entry_ids: Vec<String> = plan.deleting_rows.iter().map(|row| row.0.clone()).collect();
        tokio::task::spawn_blocking(move || store.finalize_recovery_sync(&deleting_entry_ids)).await??;

        Ok(FileCacheProviderRecoveryResult {
            stale_read_reservations_released: plan.stale_read_ids.len(),
            stale_write_reservations_released: plan.stale_write_rows.len(),
            deleting_entries_recovered: plan.deleting_rows.len(),
            files_removed,
        })
    }

    pub async fn garbage_collect(
        &self,
        policy: Option<FileCacheProviderGarbageCollectionPolicy>,
    ) -> Result<FileCacheProviderGarbageCollectionResult> {
        let policy = policy.unwrap_or_default();
        policy.validate()?;
        let recovery = self.recover().await?;
        let store = self.store.clone();
        let policy_for_claim = policy.clone();
        let claimed = tokio::task::spawn_blocking(move || store.claim_gc_entries_sync(&policy_for_claim)).await??;

        let mut deleted_entry_ids = Vec::new();
        let mut failed_entry_ids = Vec::new();
        let mut bytes_deleted = 0;
        for entry in &claimed {
            let path = self.provider.root().join(&entry.relative_path);
            match tokio::fs::remove_file(&path).await {
                Ok(()) => {
                    deleted_entry_ids.push(entry.entry_id.clone());
                    bytes_deleted += entry.data_size;
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                    deleted_entry_ids.push(entry.entry_id.clone());
                    bytes_deleted += entry.data_size;
                }
                Err(_) => failed_entry_ids.push(entry.entry_id.clone()),
            }
        }

        let store = self.store.clone();
        let deleted_for_finalize = deleted_entry_ids.clone();
        tokio::task::spawn_blocking(move || {
            store.finalize_gc_entries_sync(&deleted_for_finalize, &failed_entry_ids)
        })
        .await??;

        let orphan_grace = policy.orphan_grace_seconds.unwrap_or(self.provider.config().orphan_grace_seconds);
        let store = self.store.clone();
        let referenced_paths = tokio::task::spawn_blocking(move || store.referenced_paths_sync()).await??;
        let filesystem = self.provider.filesystem();
        let orphan_files_deleted = tokio::task::spawn_blocking(move || {
            filesystem.cleanup_orphan_files(&referenced_paths, orphan_grace)
        })
        .await??;

        let mut vacuum_performed = false;
        if policy.vacuum {
            let store = self.store.clone();
            tokio::task::spawn_blocking(move || store.vacuum_sync()).await??;
            vacuum_performed = true;
        }

        for entry in &claimed {
            if deleted_entry_ids.contains(&entry.entry_id) {
                self.provider
                    .emit(FileCacheProviderEvent {
                        event_type: "artifact_cache_entry_deleted".to_owned(),
                        owner_task_id: Some(self.provider.owner_task_id().to_owned()),
                        timestamp: crate::file_cache_provider::metadata_store::now_seconds().unwrap_or_default(),
                        ..Default::default()
                    })
                    .await;
            }
        }

        Ok(FileCacheProviderGarbageCollectionResult {
            entries_deleted: deleted_entry_ids.len(),
            bytes_deleted,
            orphan_files_deleted: orphan_files_deleted + recovery.files_removed,
            stale_read_reservations_released: recovery.stale_read_reservations_released,
            stale_write_reservations_released: recovery.stale_write_reservations_released,
            vacuum_performed,
        })
    }

    pub async fn statistics(&self) -> Result<FileCacheProviderStatistics> {
        let store = self.store.clone();
        tokio::task::spawn_blocking(move || store.statistics_sync()).await?
    }
}
