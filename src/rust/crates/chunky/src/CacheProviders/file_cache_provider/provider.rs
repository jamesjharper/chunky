//! Public local filesystem cache provider.
//!
//! `FileCacheProvider` stores immutable serialized generations under
//! `objects/` and uses SQLite WAL metadata for cross-process lookup,
//! reservation, commit, recovery, and garbage-collection coordination.

use super::{
    configuration::{FileCacheProviderConfig, FileCacheProviderGarbageCollectionPolicy},
    descriptors::{CacheResolveDescriptor, CacheHitDescriptor, CacheWaitDescriptor, CacheWriteDescriptor},
    filesystem::FileCacheProviderFilesystem,
    handles::{FileCacheProviderSourceHandle, FileCacheProviderTargetHandle, FileCacheProviderWaitHandle, ReservationKind},
    maintenance::FileCacheProviderMaintenance,
    metadata_store::{now_seconds, FileCacheProviderMetadataStore},
    models::{
        FileCacheProviderEvent, FileCacheProviderEventHandler,
        FileCacheProviderGarbageCollectionResult, FileCacheProviderRecoveryResult,
        FileCacheProviderStatistics,
    },
};
use crate::{
    artifact_models::AnyArtifactDeclaration,
    cache_models::{ArtifactCache, CacheHit, CacheResolution, CacheWait, CacheWriteReservation},
};
use anyhow::Result;
use async_trait::async_trait;
use std::{
    path::{Path, PathBuf},
    sync::Arc,
};
use uuid::Uuid;

#[derive(Clone)]
pub struct FileCacheProvider {
    inner: Arc<FileCacheProviderInner>,
}

struct FileCacheProviderInner {
    root: PathBuf,
    objects_root: PathBuf,
    database_path: PathBuf,
    config: Arc<FileCacheProviderConfig>,
    owner_task_id: String,
    event_handler: Option<FileCacheProviderEventHandler>,
    filesystem: Arc<FileCacheProviderFilesystem>,
    metadata_store: Arc<FileCacheProviderMetadataStore>,
}

impl FileCacheProvider {
    pub fn new(root: impl AsRef<Path>) -> Result<Self> {
        Self::with_options(root, None, None, None, None, None)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn with_options(
        root: impl AsRef<Path>,
        owner_task_id: Option<String>,
        owner_invocation_id: Option<String>,
        owner_stage_id: Option<String>,
        config: Option<FileCacheProviderConfig>,
        event_handler: Option<FileCacheProviderEventHandler>,
    ) -> Result<Self> {
        let root = dunce::canonicalize(root.as_ref()).unwrap_or_else(|_| root.as_ref().to_path_buf());
        let objects_root = root.join("objects");
        let database_path = root.join("cache.sqlite3");
        let config = Arc::new(config.unwrap_or_default());
        config.validate()?;
        let owner_task_id = owner_task_id.unwrap_or_else(|| format!("cache-client-{}", Uuid::new_v4().simple()));
        let owner_process_id = std::process::id();
        let owner_host_id = hostname::get()
            .ok()
            .and_then(|value| value.into_string().ok())
            .unwrap_or_else(|| "localhost".to_owned());
        let owner_process_instance_id = Uuid::new_v4().simple().to_string();
        std::fs::create_dir_all(&objects_root)?;
        let filesystem = Arc::new(FileCacheProviderFilesystem::new(
            root.clone(),
            objects_root.clone(),
            config.clone(),
        ));
        let metadata_store = Arc::new(FileCacheProviderMetadataStore::new(
            root.clone(),
            objects_root.clone(),
            database_path.clone(),
            config.clone(),
            filesystem.clone(),
            owner_task_id.clone(),
            owner_invocation_id.clone(),
            owner_stage_id.clone(),
            owner_process_id,
            owner_host_id,
            owner_process_instance_id,
        )?);
        Ok(Self {
            inner: Arc::new(FileCacheProviderInner {
                root,
                objects_root,
                database_path,
                config,
                owner_task_id,
                event_handler,
                filesystem,
                metadata_store,
            }),
        })
    }

    pub fn for_task(
        &self,
        task_id: impl Into<String>,
        invocation_id: Option<String>,
        stage_id: Option<String>,
    ) -> Result<Self> {
        Self::with_options(
            &self.inner.root,
            Some(task_id.into()),
            invocation_id,
            stage_id,
            Some((*self.inner.config).clone()),
            self.inner.event_handler.clone(),
        )
    }

    pub fn root(&self) -> &Path {
        &self.inner.root
    }

    pub fn objects_root(&self) -> &Path {
        &self.inner.objects_root
    }

    pub fn database_path(&self) -> &Path {
        &self.inner.database_path
    }

    pub fn config(&self) -> Arc<FileCacheProviderConfig> {
        self.inner.config.clone()
    }

    pub fn owner_task_id(&self) -> &str {
        &self.inner.owner_task_id
    }

    pub fn filesystem(&self) -> Arc<FileCacheProviderFilesystem> {
        self.inner.filesystem.clone()
    }

    pub fn metadata_store(&self) -> Arc<FileCacheProviderMetadataStore> {
        self.inner.metadata_store.clone()
    }

    pub async fn resolve(
        &self,
        declaration: &AnyArtifactDeclaration,
        fingerprint: &str,
        force_write: bool,
    ) -> Result<CacheResolution> {
        let metadata_store = self.metadata_store();
        let declaration_for_blocking = declaration.clone();
        let fingerprint_for_blocking = fingerprint.to_owned();
        let (descriptor, cleanup_paths) = tokio::task::spawn_blocking(move || {
            metadata_store.resolve_sync(&declaration_for_blocking, &fingerprint_for_blocking, force_write)
        })
        .await??;

        let filesystem = self.filesystem();
        tokio::task::spawn_blocking(move || filesystem.remove_paths(cleanup_paths)).await??;

        match descriptor {
            CacheResolveDescriptor::Hit(descriptor) => self.resolve_hit(declaration, fingerprint, descriptor).await,
            CacheResolveDescriptor::Write(descriptor) => self.resolve_write(declaration, fingerprint, force_write, descriptor).await,
            CacheResolveDescriptor::Wait(descriptor) => self.resolve_wait(declaration, fingerprint, descriptor).await,
        }
    }

    async fn resolve_hit(
        &self,
        declaration: &AnyArtifactDeclaration,
        fingerprint: &str,
        descriptor: CacheHitDescriptor,
    ) -> Result<CacheResolution> {
        self.emit(FileCacheProviderEvent {
            event_type: "artifact_cache_hit".to_owned(),
            fingerprint: Some(fingerprint.to_owned()),
            artifact_id: Some(declaration.artifact_id.to_string()),
            reservation_id: Some(descriptor.reservation_id.clone()),
            owner_task_id: Some(self.owner_task_id().to_owned()),
            timestamp: now_seconds().unwrap_or_default(),
            ..Default::default()
        })
        .await;
        Ok(CacheResolution::Hit(CacheHit {
            source_handle: Arc::new(FileCacheProviderSourceHandle::new(self.clone(), descriptor)),
        }))
    }

    async fn resolve_write(
        &self,
        declaration: &AnyArtifactDeclaration,
        fingerprint: &str,
        force_write: bool,
        descriptor: CacheWriteDescriptor,
    ) -> Result<CacheResolution> {
        self.emit(FileCacheProviderEvent {
            event_type: "artifact_cache_write_reserved".to_owned(),
            fingerprint: Some(fingerprint.to_owned()),
            artifact_id: Some(declaration.artifact_id.to_string()),
            reservation_id: Some(descriptor.reservation_id.clone()),
            owner_task_id: Some(self.owner_task_id().to_owned()),
            reason: Some(if force_write { "force_write" } else { "cache_miss" }.to_owned()),
            timestamp: now_seconds().unwrap_or_default(),
        })
        .await;
        Ok(CacheResolution::Write(CacheWriteReservation {
            target_handle: Arc::new(FileCacheProviderTargetHandle::new(
                self.clone(),
                declaration.clone(),
                descriptor,
            )),
        }))
    }

    async fn resolve_wait(
        &self,
        declaration: &AnyArtifactDeclaration,
        fingerprint: &str,
        descriptor: CacheWaitDescriptor,
    ) -> Result<CacheResolution> {
        self.emit(FileCacheProviderEvent {
            event_type: "artifact_cache_wait_started".to_owned(),
            fingerprint: Some(fingerprint.to_owned()),
            artifact_id: Some(declaration.artifact_id.to_string()),
            reservation_id: Some(descriptor.reservation_id.clone()),
            owner_task_id: descriptor.owner_task_id.clone(),
            timestamp: now_seconds().unwrap_or_default(),
            ..Default::default()
        })
        .await;
        Ok(CacheResolution::Wait(CacheWait {
            fingerprint: descriptor.fingerprint,
            owner_task_id: descriptor.owner_task_id,
            wait_handle: Arc::new(FileCacheProviderWaitHandle::new(
                self.clone(),
                fingerprint.to_owned(),
                descriptor.reservation_id,
            )),
        }))
    }

    pub async fn invalidate_entry(&self, entry_id: &str, reason: &str) -> Result<bool> {
        let metadata_store = self.metadata_store();
        let entry_id = entry_id.to_owned();
        let reason_owned = reason.to_owned();
        let reason_for_blocking = reason_owned.clone();
        let changed = tokio::task::spawn_blocking(move || {
            metadata_store.invalidate_entry_sync(&entry_id, &reason_for_blocking)
        })
        .await??;
        if changed {
            self.emit(FileCacheProviderEvent {
                event_type: "artifact_cache_entry_invalid".to_owned(),
                owner_task_id: Some(self.owner_task_id().to_owned()),
                reason: Some(reason_owned),
                timestamp: now_seconds().unwrap_or_default(),
                ..Default::default()
            })
            .await;
        }
        Ok(changed)
    }

    pub async fn recover(&self) -> Result<FileCacheProviderRecoveryResult> {
        FileCacheProviderMaintenance::new(self.clone()).recover().await
    }

    pub async fn garbage_collect(
        &self,
        policy: Option<FileCacheProviderGarbageCollectionPolicy>,
    ) -> Result<FileCacheProviderGarbageCollectionResult> {
        FileCacheProviderMaintenance::new(self.clone()).garbage_collect(policy).await
    }

    pub async fn statistics(&self) -> Result<FileCacheProviderStatistics> {
        FileCacheProviderMaintenance::new(self.clone()).statistics().await
    }

    pub async fn heartbeat(&self, kind: ReservationKind, reservation_id: &str) -> Result<bool> {
        let metadata_store = self.metadata_store();
        let reservation_id = reservation_id.to_owned();
        let kind = kind.as_str().to_owned();
        tokio::task::spawn_blocking(move || metadata_store.heartbeat_sync(&kind, &reservation_id)).await?
    }

    pub async fn touch_read_reservation(&self, reservation_id: &str) -> Result<bool> {
        let metadata_store = self.metadata_store();
        let reservation_id = reservation_id.to_owned();
        tokio::task::spawn_blocking(move || metadata_store.touch_read_reservation_sync(&reservation_id)).await?
    }

    pub async fn release_read_reservation(&self, reservation_id: &str) -> Result<()> {
        let metadata_store = self.metadata_store();
        let reservation_id_owned = reservation_id.to_owned();
        let reservation_id_for_blocking = reservation_id_owned.clone();
        let released = tokio::task::spawn_blocking(move || {
            metadata_store.release_read_reservation_sync(&reservation_id_for_blocking)
        })
        .await??;
        if released {
            self.emit(FileCacheProviderEvent {
                event_type: "artifact_cache_read_released".to_owned(),
                reservation_id: Some(reservation_id_owned),
                owner_task_id: Some(self.owner_task_id().to_owned()),
                timestamp: now_seconds().unwrap_or_default(),
                ..Default::default()
            })
            .await;
        }
        Ok(())
    }

    pub async fn record_preparing_write(&self, reservation_id: &str, temporary_path: &Path) -> Result<()> {
        let metadata_store = self.metadata_store();
        let reservation_id = reservation_id.to_owned();
        let temporary_path = temporary_path.to_path_buf();
        tokio::task::spawn_blocking(move || {
            metadata_store.record_preparing_write_sync(&reservation_id, &temporary_path)
        })
        .await?
    }

    pub async fn record_prepared_write(
        &self,
        reservation_id: &str,
        temporary_path: &Path,
        data_size: u64,
        data_digest: &str,
    ) -> Result<()> {
        let metadata_store = self.metadata_store();
        let reservation_id = reservation_id.to_owned();
        let temporary_path = temporary_path.to_path_buf();
        let data_digest = data_digest.to_owned();
        tokio::task::spawn_blocking(move || {
            metadata_store.record_prepared_write_sync(&reservation_id, &temporary_path, data_size, &data_digest)
        })
        .await?
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn commit_write(
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
        let metadata_store = self.metadata_store();
        let reservation_id_owned = reservation_id.to_owned();
        let entry_id = entry_id.to_owned();
        let fingerprint_owned = fingerprint.to_owned();
        let declaration_for_commit = declaration.clone();
        let artifact_id = declaration.artifact_id.to_string();
        let temporary_path = temporary_path.to_path_buf();
        let final_path = final_path.to_path_buf();
        let data_digest = data_digest.to_owned();
        tokio::task::spawn_blocking(move || {
            metadata_store.commit_write_sync(
                &reservation_id_owned,
                &entry_id,
                &fingerprint_owned,
                &declaration_for_commit,
                &temporary_path,
                &final_path,
                data_size,
                &data_digest,
            )
        })
        .await??;
        self.emit(FileCacheProviderEvent {
            event_type: "artifact_cache_committed".to_owned(),
            fingerprint: Some(fingerprint.to_owned()),
            artifact_id: Some(artifact_id),
            reservation_id: Some(reservation_id.to_owned()),
            owner_task_id: Some(self.owner_task_id().to_owned()),
            timestamp: now_seconds().unwrap_or_default(),
            ..Default::default()
        })
        .await;
        Ok(())
    }

    pub async fn abort_write(
        &self,
        reservation_id: &str,
        entry_id: &str,
        temporary_path: Option<&Path>,
        final_path: &Path,
    ) -> Result<()> {
        let metadata_store = self.metadata_store();
        let reservation_id_owned = reservation_id.to_owned();
        let reservation_id_for_blocking = reservation_id_owned.clone();
        let entry_id = entry_id.to_owned();
        let temporary_path = temporary_path.map(Path::to_path_buf);
        let final_path = final_path.to_path_buf();
        let cleanup_paths = tokio::task::spawn_blocking(move || {
            metadata_store.abort_write_sync(&reservation_id_for_blocking, &entry_id, temporary_path.as_deref(), &final_path)
        })
        .await??;
        let filesystem = self.filesystem();
        tokio::task::spawn_blocking(move || filesystem.remove_paths(cleanup_paths)).await??;
        self.emit(FileCacheProviderEvent {
            event_type: "artifact_cache_write_released".to_owned(),
            reservation_id: Some(reservation_id_owned),
            owner_task_id: Some(self.owner_task_id().to_owned()),
            reason: Some("aborted".to_owned()),
            timestamp: now_seconds().unwrap_or_default(),
            ..Default::default()
        })
        .await;
        Ok(())
    }

    pub async fn observed_writer_is_active(&self, fingerprint: &str, observed_reservation_id: &str) -> Result<bool> {
        let metadata_store = self.metadata_store();
        let fingerprint_owned = fingerprint.to_owned();
        let observed_reservation_id_owned = observed_reservation_id.to_owned();
        let fingerprint_for_blocking = fingerprint_owned.clone();
        let observed_reservation_id_for_blocking = observed_reservation_id_owned.clone();
        let (active, cleanup_paths) = tokio::task::spawn_blocking(move || {
            metadata_store.observed_writer_is_active_sync(&fingerprint_for_blocking, &observed_reservation_id_for_blocking)
        })
        .await??;
        let filesystem = self.filesystem();
        tokio::task::spawn_blocking(move || filesystem.remove_paths(cleanup_paths)).await??;
        if !active {
            self.emit(FileCacheProviderEvent {
                event_type: "artifact_cache_wait_finished".to_owned(),
                fingerprint: Some(fingerprint_owned),
                reservation_id: Some(observed_reservation_id_owned),
                owner_task_id: Some(self.owner_task_id().to_owned()),
                timestamp: now_seconds().unwrap_or_default(),
                ..Default::default()
            })
            .await;
        }
        Ok(active)
    }

    pub async fn emit(&self, event: FileCacheProviderEvent) {
        let Some(handler) = self.inner.event_handler.clone() else {
            return;
        };
        tokio::spawn(async move {
            let _ = tokio::task::spawn_blocking(move || handler(event)).await;
        });
    }
}

#[async_trait]
impl ArtifactCache for FileCacheProvider {
    async fn resolve(
        &self,
        declaration: &AnyArtifactDeclaration,
        fingerprint: &str,
        force_write: bool,
    ) -> Result<CacheResolution> {
        FileCacheProvider::resolve(self, declaration, fingerprint, force_write).await
    }
}
