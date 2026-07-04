//! Reservation-owning source, target, and wait handles.

use super::{
    descriptors::{CacheHitDescriptor, CacheWriteDescriptor},
    filesystem::FileCacheProviderFilesystem,
    provider::FileCacheProvider,
};
use crate::{
    artifact_handles::{ArtifactSourceHandle, ArtifactTargetHandle},
    artifact_models::{AnyArtifactDeclaration, ArtifactValue, ErasedCodec},
    cache_models::CacheWaitHandle,
    fingerprints::stable_hash_bytes,
};
use anyhow::{anyhow, bail, Result};
use async_trait::async_trait;
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};
use tokio::{
    sync::Mutex,
    task::JoinHandle,
    time::{sleep, Duration},
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReservationKind {
    Read,
    Write,
}

impl ReservationKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Read => "read",
            Self::Write => "write",
        }
    }
}

struct FileCacheProviderReservationHeartbeat {
    active: Arc<AtomicBool>,
    task: Mutex<Option<JoinHandle<()>>>,
}

impl FileCacheProviderReservationHeartbeat {
    fn start(provider: FileCacheProvider, kind: ReservationKind, reservation_id: String) -> Self {
        let active = Arc::new(AtomicBool::new(true));
        let active_for_task = active.clone();
        let task = tokio::spawn(async move {
            while active_for_task.load(Ordering::SeqCst) {
                sleep(Duration::from_secs_f64(
                    provider.config().heartbeat_interval_seconds,
                ))
                .await;
                if !active_for_task.load(Ordering::SeqCst) {
                    return;
                }
                match provider.heartbeat(kind, &reservation_id).await {
                    Ok(true) => {}
                    Ok(false) => return,
                    Err(_) => continue,
                }
            }
        });
        Self {
            active,
            task: Mutex::new(Some(task)),
        }
    }

    async fn stop(&self) {
        self.active.store(false, Ordering::SeqCst);
        if let Some(task) = self.task.lock().await.take() {
            task.abort();
            let _ = task.await;
        }
    }
}

#[derive(Clone)]
pub struct FileCacheProviderWaitHandle {
    provider: FileCacheProvider,
    fingerprint: String,
    observed_reservation_id: String,
}

impl FileCacheProviderWaitHandle {
    pub fn new(
        provider: FileCacheProvider,
        fingerprint: String,
        observed_reservation_id: String,
    ) -> Self {
        Self {
            provider,
            fingerprint,
            observed_reservation_id,
        }
    }
}

#[async_trait]
impl CacheWaitHandle for FileCacheProviderWaitHandle {
    async fn wait(&self) -> Result<()> {
        loop {
            let active = self
                .provider
                .observed_writer_is_active(&self.fingerprint, &self.observed_reservation_id)
                .await?;
            if !active {
                return Ok(());
            }
            sleep(Duration::from_secs_f64(
                self.provider.config().wait_poll_interval_seconds,
            ))
            .await;
        }
    }
}

pub struct FileCacheProviderSourceHandle {
    provider: FileCacheProvider,
    entry_id: String,
    reservation_id: String,
    data_path: PathBuf,
    data_size: u64,
    data_digest: String,
    fingerprint: String,
    closed: AtomicBool,
    heartbeat: FileCacheProviderReservationHeartbeat,
}

impl FileCacheProviderSourceHandle {
    pub fn new(provider: FileCacheProvider, descriptor: CacheHitDescriptor) -> Self {
        let heartbeat = FileCacheProviderReservationHeartbeat::start(
            provider.clone(),
            ReservationKind::Read,
            descriptor.reservation_id.clone(),
        );
        Self {
            provider,
            entry_id: descriptor.entry_id,
            reservation_id: descriptor.reservation_id,
            data_path: descriptor.data_path,
            data_size: descriptor.data_size,
            data_digest: descriptor.data_digest,
            fingerprint: descriptor.fingerprint,
            closed: AtomicBool::new(false),
            heartbeat,
        }
    }

    pub fn data_path(&self) -> &PathBuf {
        &self.data_path
    }

    pub fn reservation_id(&self) -> &str {
        &self.reservation_id
    }
}

#[async_trait]
impl ArtifactSourceHandle for FileCacheProviderSourceHandle {
    fn kind(&self) -> &'static str {
        "file-cache-provider"
    }

    async fn get_value(&self, codec: &dyn ErasedCodec) -> Result<ArtifactValue> {
        if self.closed.load(Ordering::SeqCst) {
            bail!("cache read handle is closed");
        }
        if !self
            .provider
            .touch_read_reservation(&self.reservation_id)
            .await?
        {
            bail!("cache read reservation was lost: {}", self.reservation_id);
        }

        let raw = match tokio::fs::read(&self.data_path).await {
            Ok(raw) => raw,
            Err(error) => {
                let _ = self
                    .provider
                    .invalidate_entry(&self.entry_id, &format!("read failed: {error}"))
                    .await;
                return Err(error.into());
            }
        };
        if raw.len() as u64 != self.data_size {
            let _ = self
                .provider
                .invalidate_entry(&self.entry_id, "read failed: cache entry size mismatch")
                .await;
            bail!("cache entry size mismatch for {}", self.fingerprint);
        }
        let digest = stable_hash_bytes(&raw);
        if digest != self.data_digest {
            let _ = self
                .provider
                .invalidate_entry(&self.entry_id, "read failed: cache entry digest mismatch")
                .await;
            bail!("cache entry digest mismatch for {}", self.fingerprint);
        }
        codec.deserialize_any(&raw)
    }

    async fn get_content_fingerprint(&self, _codec: &dyn ErasedCodec) -> Result<String> {
        Ok(String::new())
    }

    async fn close(&self) -> Result<()> {
        if self.closed.swap(true, Ordering::SeqCst) {
            return Ok(());
        }
        self.heartbeat.stop().await;
        self.provider
            .release_read_reservation(&self.reservation_id)
            .await
    }
}

struct TargetState {
    temporary_path: Option<PathBuf>,
    data_size: Option<u64>,
    data_digest: Option<String>,
    committed: bool,
    aborted: bool,
}

pub struct FileCacheProviderTargetHandle {
    provider: FileCacheProvider,
    declaration: AnyArtifactDeclaration,
    reservation_id: String,
    entry_id: String,
    fingerprint: String,
    final_path: PathBuf,
    state: Mutex<TargetState>,
    heartbeat: FileCacheProviderReservationHeartbeat,
}

impl FileCacheProviderTargetHandle {
    pub fn new(
        provider: FileCacheProvider,
        declaration: AnyArtifactDeclaration,
        descriptor: CacheWriteDescriptor,
    ) -> Self {
        let heartbeat = FileCacheProviderReservationHeartbeat::start(
            provider.clone(),
            ReservationKind::Write,
            descriptor.reservation_id.clone(),
        );
        Self {
            provider,
            declaration,
            reservation_id: descriptor.reservation_id,
            entry_id: descriptor.entry_id,
            fingerprint: descriptor.fingerprint,
            final_path: descriptor.final_path,
            state: Mutex::new(TargetState {
                temporary_path: None,
                data_size: None,
                data_digest: None,
                committed: false,
                aborted: false,
            }),
            heartbeat,
        }
    }

    pub fn final_path(&self) -> &PathBuf {
        &self.final_path
    }

    pub fn reservation_id(&self) -> &str {
        &self.reservation_id
    }
}

#[async_trait]
impl ArtifactTargetHandle for FileCacheProviderTargetHandle {
    fn kind(&self) -> &'static str {
        "file-cache-provider"
    }

    async fn prepare(
        &self,
        value: ArtifactValue,
        codec: &dyn ErasedCodec,
        fingerprint: &str,
    ) -> Result<()> {
        let mut state = self.state.lock().await;
        if state.committed || state.aborted {
            bail!("cache target is already terminal");
        }
        if state.temporary_path.is_some() {
            bail!("cache target is already prepared");
        }
        if fingerprint != self.fingerprint {
            bail!("writer fingerprint does not match reserved fingerprint");
        }

        let temporary_path = FileCacheProviderFilesystem::new_temporary_path(&self.final_path);
        state.temporary_path = Some(temporary_path.clone());
        drop(state);

        let result = async {
            self.provider
                .record_preparing_write(&self.reservation_id, &temporary_path)
                .await?;
            let raw = codec.serialize_any(&value)?;
            let filesystem = self.provider.filesystem();
            let temporary_path_for_write = temporary_path.clone();
            let (size, digest) = tokio::task::spawn_blocking(move || {
                filesystem.write_temporary_file(&temporary_path_for_write, &raw)
            })
            .await??;
            self.provider
                .record_prepared_write(&self.reservation_id, &temporary_path, size, &digest)
                .await?;
            Ok::<(u64, String), anyhow::Error>((size, digest))
        }
        .await;

        match result {
            Ok((size, digest)) => {
                let mut state = self.state.lock().await;
                state.data_size = Some(size);
                state.data_digest = Some(digest);
                Ok(())
            }
            Err(error) => {
                let _ = self.abort().await;
                Err(error)
            }
        }
    }

    async fn commit(&self) -> Result<()> {
        let (temporary_path, data_size, data_digest) = {
            let state = self.state.lock().await;
            if state.committed || state.aborted {
                return Ok(());
            }
            (
                state
                    .temporary_path
                    .clone()
                    .ok_or_else(|| anyhow!("cache target has not been prepared"))?,
                state
                    .data_size
                    .ok_or_else(|| anyhow!("cache target has not been prepared"))?,
                state
                    .data_digest
                    .clone()
                    .ok_or_else(|| anyhow!("cache target has not been prepared"))?,
            )
        };

        if let Err(error) = self
            .provider
            .commit_write(
                &self.reservation_id,
                &self.entry_id,
                &self.fingerprint,
                &self.declaration,
                &temporary_path,
                &self.final_path,
                data_size,
                &data_digest,
            )
            .await
        {
            let _ = self.abort().await;
            return Err(error);
        }

        let mut state = self.state.lock().await;
        state.temporary_path = None;
        state.committed = true;
        drop(state);
        self.heartbeat.stop().await;
        Ok(())
    }

    async fn abort(&self) -> Result<()> {
        let temporary_path = {
            let mut state = self.state.lock().await;
            if state.committed || state.aborted {
                return Ok(());
            }
            state.aborted = true;
            state.temporary_path.take()
        };
        self.heartbeat.stop().await;
        self.provider
            .abort_write(
                &self.reservation_id,
                &self.entry_id,
                temporary_path.as_deref(),
                &self.final_path,
            )
            .await
    }
}
