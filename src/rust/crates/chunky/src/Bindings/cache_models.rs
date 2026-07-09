//! Cache-resolution result contracts used by artifact output bootstrap.
//!
//! Cache lookup and reservation ownership remain below the stage-facing reader
//! and writer contracts: callers receive an already-reserved hit, an exclusive
//! write reservation, or an explicit wait handle.

use crate::{
    artifact_binding_models::{AnyArtifactInstance, AnyArtifactWriter},
    artifact_handles::{ArtifactSourceHandle, ArtifactTargetHandle},
    artifact_models::AnyArtifactDeclaration,
};
use anyhow::{bail, Result};
use async_trait::async_trait;
use std::sync::Arc;

#[derive(Clone)]
pub struct CacheHit {
    pub source_handle: Arc<dyn ArtifactSourceHandle>,
}

#[derive(Clone)]
pub struct CacheWriteReservation {
    pub target_handle: Arc<dyn ArtifactTargetHandle>,
}

#[async_trait]
pub trait CacheWaitHandle: Send + Sync {
    async fn wait(&self) -> Result<()>;
}

#[derive(Clone)]
pub struct CacheWait {
    pub fingerprint: String,
    pub owner_task_id: Option<String>,
    pub wait_handle: Arc<dyn CacheWaitHandle>,
}

#[derive(Clone)]
pub enum CacheResolution {
    Hit(CacheHit),
    Write(CacheWriteReservation),
    Wait(CacheWait),
}

#[async_trait]
pub trait ArtifactCache: Send + Sync {
    async fn resolve(
        &self,
        declaration: &AnyArtifactDeclaration,
        fingerprint: &str,
        force_write: bool,
    ) -> Result<CacheResolution>;

    async fn open(
        &self,
        declaration: &AnyArtifactDeclaration,
        fingerprint: &str,
    ) -> Result<CacheHit> {
        match self.resolve(declaration, fingerprint, false).await? {
            CacheResolution::Hit(hit) => Ok(hit),
            CacheResolution::Wait(_) => {
                bail!("cache entry is currently being written and cannot be opened: {fingerprint}")
            }
            CacheResolution::Write(reservation) => {
                let _ = reservation.target_handle.abort().await;
                bail!("cache entry does not exist for explicit reference: {fingerprint}")
            }
        }
    }
}

#[derive(Clone)]
pub enum CacheableOutputBootstrap {
    Available(AnyArtifactInstance),
    Produce(AnyArtifactWriter),
    Waiting(CacheWait),
}
