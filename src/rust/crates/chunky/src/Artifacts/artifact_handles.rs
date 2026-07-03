//! Resolved source and target handle contracts.
//!
//! Readers and writers receive these handles after provider/cache resolution.
//! They do not perform cache lookup or acquire reservations themselves.

use crate::artifact_models::{ArtifactValue, ErasedCodec};
use anyhow::{bail, Result};
use async_trait::async_trait;
use std::sync::Arc;
use tokio::sync::Mutex;

#[async_trait]
pub trait ArtifactSourceHandle: Send + Sync {
    fn kind(&self) -> &'static str;
    async fn get_value(&self, codec: &dyn ErasedCodec) -> Result<ArtifactValue>;
    async fn get_content_fingerprint(&self, codec: &dyn ErasedCodec) -> Result<String>;
    async fn close(&self) -> Result<()>;
}

#[async_trait]
pub trait ArtifactTargetHandle: Send + Sync {
    fn kind(&self) -> &'static str;
    async fn prepare(
        &self,
        value: ArtifactValue,
        codec: &dyn ErasedCodec,
        fingerprint: &str,
    ) -> Result<()>;
    async fn commit(&self) -> Result<()>;
    async fn abort(&self) -> Result<()>;
}

#[derive(Default)]
struct IntermediateState {
    pending: Option<ArtifactValue>,
    value: Option<ArtifactValue>,
    committed: bool,
    aborted: bool,
}

/// Duplex same-process publication handle.
///
/// Produced values get their semantic identity from pedigree rather than by
/// hashing the output bytes again.
pub struct IntermediateArtifactHandle {
    state: Mutex<IntermediateState>,
}

impl IntermediateArtifactHandle {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            state: Mutex::new(IntermediateState::default()),
        })
    }
}

#[async_trait]
impl ArtifactTargetHandle for IntermediateArtifactHandle {
    fn kind(&self) -> &'static str {
        "intermediate-memory"
    }

    async fn prepare(
        &self,
        value: ArtifactValue,
        _codec: &dyn ErasedCodec,
        _fingerprint: &str,
    ) -> Result<()> {
        let mut state = self.state.lock().await;
        if state.committed || state.aborted {
            bail!("publication handle is already terminal");
        }
        if state.pending.is_some() {
            bail!("publication handle is already prepared");
        }
        state.pending = Some(value);
        Ok(())
    }

    async fn commit(&self) -> Result<()> {
        let mut state = self.state.lock().await;
        if state.committed || state.aborted {
            return Ok(());
        }
        let pending = state
            .pending
            .take()
            .ok_or_else(|| anyhow::anyhow!("publication handle has not been prepared"))?;
        state.value = Some(pending);
        state.committed = true;
        Ok(())
    }

    async fn abort(&self) -> Result<()> {
        let mut state = self.state.lock().await;
        if state.committed || state.aborted {
            return Ok(());
        }
        state.pending = None;
        state.aborted = true;
        Ok(())
    }
}

#[async_trait]
impl ArtifactSourceHandle for IntermediateArtifactHandle {
    fn kind(&self) -> &'static str {
        "intermediate-memory"
    }

    async fn get_value(&self, _codec: &dyn ErasedCodec) -> Result<ArtifactValue> {
        let state = self.state.lock().await;
        if !state.committed {
            bail!("artifact is not committed");
        }
        state
            .value
            .clone()
            .ok_or_else(|| anyhow::anyhow!("committed artifact has no value"))
    }

    async fn get_content_fingerprint(&self, _codec: &dyn ErasedCodec) -> Result<String> {
        Ok(String::new())
    }

    async fn close(&self) -> Result<()> {
        Ok(())
    }
}
