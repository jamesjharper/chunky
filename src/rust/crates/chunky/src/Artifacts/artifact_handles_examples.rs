//! Small source and target adapters used by the executable example.

use crate::{
    artifact_handles::{ArtifactSourceHandle, ArtifactTargetHandle},
    artifact_models::{ArtifactValue, ErasedCodec},
    fingerprints::stable_hash_bytes,
};
use anyhow::{bail, Result};
use async_trait::async_trait;
use std::{
    path::{Path, PathBuf},
    sync::Arc,
};
use tokio::sync::Mutex;
use uuid::Uuid;

pub struct FileSource {
    path: PathBuf,
}

impl FileSource {
    pub fn new(path: impl Into<PathBuf>) -> Arc<Self> {
        Arc::new(Self { path: path.into() })
    }
}

#[async_trait]
impl ArtifactSourceHandle for FileSource {
    fn kind(&self) -> &'static str {
        "supplied-file"
    }

    async fn get_value(&self, codec: &dyn ErasedCodec) -> Result<ArtifactValue> {
        let raw = tokio::fs::read(&self.path).await?;
        codec.deserialize_any(&raw)
    }

    async fn get_content_fingerprint(&self, _codec: &dyn ErasedCodec) -> Result<String> {
        let raw = tokio::fs::read(&self.path).await?;
        Ok(stable_hash_bytes(&raw))
    }

    async fn close(&self) -> Result<()> {
        Ok(())
    }
}

pub struct InMemorySource<T> {
    kind: &'static str,
    value: Arc<T>,
}

impl<T> InMemorySource<T>
where
    T: Send + Sync + 'static,
{
    pub fn new(kind: &'static str, value: T) -> Arc<Self> {
        Arc::new(Self {
            kind,
            value: Arc::new(value),
        })
    }
}

#[async_trait]
impl<T> ArtifactSourceHandle for InMemorySource<T>
where
    T: Send + Sync + 'static,
{
    fn kind(&self) -> &'static str {
        self.kind
    }

    async fn get_value(&self, _codec: &dyn ErasedCodec) -> Result<ArtifactValue> {
        Ok(self.value.clone())
    }

    async fn get_content_fingerprint(&self, codec: &dyn ErasedCodec) -> Result<String> {
        let value: ArtifactValue = self.value.clone();
        let raw = codec.serialize_any(&value)?;
        Ok(stable_hash_bytes(&raw))
    }

    async fn close(&self) -> Result<()> {
        Ok(())
    }
}

pub struct CacheFileSourceHandle {
    data_path: PathBuf,
    closed: Mutex<bool>,
}

impl CacheFileSourceHandle {
    pub fn new(data_path: impl Into<PathBuf>) -> Arc<Self> {
        Arc::new(Self {
            data_path: data_path.into(),
            closed: Mutex::new(false),
        })
    }
}

#[async_trait]
impl ArtifactSourceHandle for CacheFileSourceHandle {
    fn kind(&self) -> &'static str {
        "cache-file"
    }

    async fn get_value(&self, codec: &dyn ErasedCodec) -> Result<ArtifactValue> {
        if *self.closed.lock().await {
            bail!("cache read handle is closed");
        }
        let raw = tokio::fs::read(&self.data_path).await?;
        codec.deserialize_any(&raw)
    }

    async fn get_content_fingerprint(&self, _codec: &dyn ErasedCodec) -> Result<String> {
        Ok(String::new())
    }

    async fn close(&self) -> Result<()> {
        *self.closed.lock().await = true;
        Ok(())
    }
}

#[derive(Default)]
struct AtomicFileState {
    temporary_path: Option<PathBuf>,
    committed: bool,
    aborted: bool,
}

pub struct AtomicFileTargetHandle {
    path: PathBuf,
    state: Mutex<AtomicFileState>,
}

impl AtomicFileTargetHandle {
    pub fn new(path: impl Into<PathBuf>) -> Arc<Self> {
        Arc::new(Self {
            path: path.into(),
            state: Mutex::new(AtomicFileState::default()),
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}

#[async_trait]
impl ArtifactTargetHandle for AtomicFileTargetHandle {
    fn kind(&self) -> &'static str {
        "atomic-file"
    }

    async fn prepare(
        &self,
        value: ArtifactValue,
        codec: &dyn ErasedCodec,
        _fingerprint: &str,
    ) -> Result<()> {
        let mut state = self.state.lock().await;
        if state.committed || state.aborted {
            bail!("target is already terminal");
        }
        if state.temporary_path.is_some() {
            bail!("target is already prepared");
        }

        if let Some(parent) = self.path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let raw = codec.serialize_any(&value)?;
        let file_name = self
            .path
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("artifact");
        let temporary_path = self
            .path
            .with_file_name(format!(".{file_name}.{}.tmp", Uuid::new_v4().simple()));
        tokio::fs::write(&temporary_path, raw).await?;
        state.temporary_path = Some(temporary_path);
        Ok(())
    }

    async fn commit(&self) -> Result<()> {
        let mut state = self.state.lock().await;
        if state.committed || state.aborted {
            return Ok(());
        }
        let temporary_path = state
            .temporary_path
            .take()
            .ok_or_else(|| anyhow::anyhow!("target has not been prepared"))?;
        tokio::fs::rename(temporary_path, &self.path).await?;
        state.committed = true;
        Ok(())
    }

    async fn abort(&self) -> Result<()> {
        let mut state = self.state.lock().await;
        if state.committed || state.aborted {
            return Ok(());
        }
        if let Some(temporary_path) = state.temporary_path.take() {
            let _ = tokio::fs::remove_file(temporary_path).await;
        }
        state.aborted = true;
        Ok(())
    }
}
