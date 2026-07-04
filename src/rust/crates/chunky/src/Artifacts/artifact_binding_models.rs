//! Immutable bindings and stage-facing typed I/O.
//!
//! Readers and writers receive already-resolved handles. They never look up
//! cache entries, choose providers, or acquire reservations themselves.
//! Input/output collections are alias-aware so planner-selected IDs and
//! stage-facing selectors can remain separate.

use crate::{
    artifact_handles::{ArtifactSourceHandle, ArtifactTargetHandle},
    artifact_models::{AnyArtifactDeclaration, ArtifactDeclaration, ArtifactKey, ArtifactValue},
    cache_models::CacheWait,
};
use anyhow::{anyhow, bail, Result};
use std::{collections::HashMap, marker::PhantomData, sync::Arc};
use tokio::sync::Mutex;

pub struct ArtifactHandle {
    pub declaration: AnyArtifactDeclaration,
    pub fingerprint: String,
    pub source: Option<Arc<dyn ArtifactSourceHandle>>,
    pub targets: Vec<Arc<dyn ArtifactTargetHandle>>,
}

impl ArtifactHandle {
    pub fn new(
        declaration: AnyArtifactDeclaration,
        fingerprint: impl Into<String>,
        source: Option<Arc<dyn ArtifactSourceHandle>>,
        targets: Vec<Arc<dyn ArtifactTargetHandle>>,
    ) -> Arc<Self> {
        Arc::new(Self {
            declaration,
            fingerprint: fingerprint.into(),
            source,
            targets,
        })
    }
}

pub struct ArtifactInstance<T> {
    pub handle: Arc<ArtifactHandle>,
    _marker: PhantomData<fn() -> T>,
}

impl<T> Clone for ArtifactInstance<T> {
    fn clone(&self) -> Self {
        Self {
            handle: self.handle.clone(),
            _marker: PhantomData,
        }
    }
}

impl<T> ArtifactInstance<T>
where
    T: Send + Sync + 'static,
{
    pub fn new(handle: Arc<ArtifactHandle>) -> Result<Self> {
        if handle.declaration.codec.value_type_id() != std::any::TypeId::of::<T>() {
            bail!("artifact instance type does not match its declaration");
        }
        Ok(Self {
            handle,
            _marker: PhantomData,
        })
    }

    pub fn fingerprint(&self) -> &str {
        &self.handle.fingerprint
    }

    pub fn erase(&self) -> AnyArtifactInstance {
        AnyArtifactInstance {
            handle: self.handle.clone(),
        }
    }
}

#[derive(Clone)]
pub struct AnyArtifactInstance {
    pub handle: Arc<ArtifactHandle>,
}

impl AnyArtifactInstance {
    pub fn artifact_id(&self) -> &ArtifactKey {
        &self.handle.declaration.artifact_id
    }

    pub fn fingerprint(&self) -> &str {
        &self.handle.fingerprint
    }

    pub fn downcast<T>(&self, declaration: &ArtifactDeclaration<T>) -> Result<ArtifactInstance<T>>
    where
        T: Send + Sync + 'static,
    {
        if self.artifact_id() != declaration.artifact_id() {
            bail!(
                "artifact id mismatch: expected {}, got {}",
                declaration.artifact_id(),
                self.artifact_id()
            );
        }
        ArtifactInstance::new(self.handle.clone())
    }
}

pub struct AnyArtifactReader {
    pub handle: Arc<ArtifactHandle>,
    cached: Mutex<Option<ArtifactValue>>,
}

impl AnyArtifactReader {
    pub fn new(handle: Arc<ArtifactHandle>) -> Result<Self> {
        if handle.source.is_none() {
            bail!("input artifact has no bound source");
        }
        Ok(Self {
            handle,
            cached: Mutex::new(None),
        })
    }

    pub async fn read_any(&self) -> Result<ArtifactValue> {
        let mut cached = self.cached.lock().await;
        if let Some(value) = cached.as_ref() {
            return Ok(value.clone());
        }

        let source = self
            .handle
            .source
            .as_ref()
            .ok_or_else(|| anyhow!("input artifact has no bound source"))?;
        let value = source
            .get_value(self.handle.declaration.codec.as_ref())
            .await?;
        *cached = Some(value.clone());
        Ok(value)
    }
}

pub struct ArtifactReader<T> {
    pub handle: Arc<ArtifactHandle>,
    cached: Mutex<Option<Arc<T>>>,
}

impl<T> ArtifactReader<T>
where
    T: Send + Sync + 'static,
{
    pub fn new(handle: Arc<ArtifactHandle>) -> Result<Self> {
        if handle.source.is_none() {
            bail!("input artifact has no bound source");
        }
        if handle.declaration.codec.value_type_id() != std::any::TypeId::of::<T>() {
            bail!("reader type does not match artifact declaration");
        }
        Ok(Self {
            handle,
            cached: Mutex::new(None),
        })
    }

    pub async fn read(&self) -> Result<Arc<T>> {
        let mut cached = self.cached.lock().await;
        if let Some(value) = cached.as_ref() {
            return Ok(value.clone());
        }

        let source = self
            .handle
            .source
            .as_ref()
            .ok_or_else(|| anyhow!("input artifact has no bound source"))?;
        let value = source
            .get_value(self.handle.declaration.codec.as_ref())
            .await?;
        let typed = Arc::downcast::<T>(value)
            .map_err(|_| anyhow!("source returned the wrong concrete artifact value type"))?;
        *cached = Some(typed.clone());
        Ok(typed)
    }
}

struct WriterState {
    written: bool,
}

#[derive(Clone)]
pub struct AnyArtifactWriter {
    pub handle: Arc<ArtifactHandle>,
    state: Arc<Mutex<WriterState>>,
}

impl AnyArtifactWriter {
    pub fn new(handle: Arc<ArtifactHandle>) -> Result<Self> {
        if handle.targets.is_empty() {
            bail!("output artifact has no bound targets");
        }
        Ok(Self {
            handle,
            state: Arc::new(Mutex::new(WriterState { written: false })),
        })
    }

    pub fn artifact_id(&self) -> &ArtifactKey {
        &self.handle.declaration.artifact_id
    }

    pub async fn is_written(&self) -> bool {
        self.state.lock().await.written
    }

    pub async fn write_any(&self, value: ArtifactValue) -> Result<AnyArtifactInstance> {
        let mut state = self.state.lock().await;
        if state.written {
            bail!("artifact may be written only once");
        }
        if value.as_ref().type_id() != self.handle.declaration.codec.value_type_id() {
            bail!("writer received the wrong concrete artifact value type");
        }

        let mut prepared: Vec<Arc<dyn ArtifactTargetHandle>> = Vec::new();
        for target in &self.handle.targets {
            if let Err(error) = target
                .prepare(
                    value.clone(),
                    self.handle.declaration.codec.as_ref(),
                    &self.handle.fingerprint,
                )
                .await
            {
                for prepared_target in prepared.iter().rev() {
                    let _ = prepared_target.abort().await;
                }
                return Err(error);
            }
            prepared.push(target.clone());
        }

        for target in &self.handle.targets {
            if let Err(error) = target.commit().await {
                for prepared_target in prepared.iter().rev() {
                    let _ = prepared_target.abort().await;
                }
                return Err(error);
            }
        }

        state.written = true;
        Ok(AnyArtifactInstance {
            handle: self.handle.clone(),
        })
    }

    pub async fn abort_if_unwritten(&self) {
        let state = self.state.lock().await;
        if state.written {
            return;
        }
        for target in self.handle.targets.iter().rev() {
            let _ = target.abort().await;
        }
    }

    pub fn downcast<T>(&self, declaration: &ArtifactDeclaration<T>) -> Result<ArtifactWriter<T>>
    where
        T: Send + Sync + 'static,
    {
        if self.artifact_id() != declaration.artifact_id() {
            bail!(
                "artifact id mismatch: expected {}, got {}",
                declaration.artifact_id(),
                self.artifact_id()
            );
        }
        if self.handle.declaration.codec.value_type_id() != std::any::TypeId::of::<T>() {
            bail!("writer type does not match artifact declaration");
        }
        Ok(ArtifactWriter {
            inner: self.clone(),
            _marker: PhantomData,
        })
    }
}

#[derive(Clone)]
pub struct ArtifactWriter<T> {
    inner: AnyArtifactWriter,
    _marker: PhantomData<fn() -> T>,
}

impl<T> ArtifactWriter<T>
where
    T: Send + Sync + 'static,
{
    pub fn handle(&self) -> &Arc<ArtifactHandle> {
        &self.inner.handle
    }

    pub async fn write(&self, value: T) -> Result<ArtifactInstance<T>> {
        let instance = self.inner.write_any(Arc::new(value)).await?;
        ArtifactInstance::new(instance.handle)
    }

    pub async fn abort_if_unwritten(&self) {
        self.inner.abort_if_unwritten().await;
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Hash)]
pub enum ArtifactSelector {
    Key(ArtifactKey),
    Text(String),
}

impl ArtifactSelector {
    pub fn to_artifact_key(&self) -> Result<ArtifactKey> {
        match self {
            Self::Key(key) => Ok(key.clone()),
            Self::Text(value) => ArtifactKey::parse(value),
        }
    }
}

impl From<ArtifactKey> for ArtifactSelector {
    fn from(value: ArtifactKey) -> Self {
        Self::Key(value)
    }
}

impl From<&ArtifactKey> for ArtifactSelector {
    fn from(value: &ArtifactKey) -> Self {
        Self::Key(value.clone())
    }
}

impl From<&str> for ArtifactSelector {
    fn from(value: &str) -> Self {
        Self::Text(value.to_owned())
    }
}

pub struct InputArtifacts {
    readers: HashMap<ArtifactKey, AnyArtifactReader>,
    aliases: HashMap<ArtifactKey, ArtifactKey>,
}

impl InputArtifacts {
    pub fn from_instances(instances: &[AnyArtifactInstance]) -> Result<Self> {
        Self::from_instances_with_aliases(instances, HashMap::new())
    }

    pub fn from_instances_with_aliases(
        instances: &[AnyArtifactInstance],
        aliases: HashMap<ArtifactKey, ArtifactKey>,
    ) -> Result<Self> {
        let mut readers = HashMap::new();
        for instance in instances {
            readers.insert(
                instance.artifact_id().clone(),
                AnyArtifactReader::new(instance.handle.clone())?,
            );
        }
        Ok(Self { readers, aliases })
    }

    fn resolved_key(&self, selector: impl Into<ArtifactSelector>) -> Result<ArtifactKey> {
        let key = selector.into().to_artifact_key()?;
        Ok(self.aliases.get(&key).cloned().unwrap_or(key))
    }

    pub fn get_artifact<T>(&self, declaration: &ArtifactDeclaration<T>) -> Result<ArtifactReader<T>>
    where
        T: Send + Sync + 'static,
    {
        let artifact_id = self
            .aliases
            .get(declaration.artifact_id())
            .unwrap_or_else(|| declaration.artifact_id());
        let reader = self.readers.get(artifact_id).ok_or_else(|| {
            anyhow!(
                "missing input artifact: {} (resolved as {})",
                declaration.artifact_id(),
                artifact_id
            )
        })?;
        ArtifactReader::new(reader.handle.clone())
    }

    pub fn get_artifact_by_key(
        &self,
        selector: impl Into<ArtifactSelector>,
    ) -> Result<AnyArtifactReader> {
        let requested = selector.into().to_artifact_key()?;
        let artifact_id = self
            .aliases
            .get(&requested)
            .cloned()
            .unwrap_or(requested.clone());
        let reader = self.readers.get(&artifact_id).ok_or_else(|| {
            anyhow!(
                "missing input artifact: {} (resolved as {})",
                requested,
                artifact_id
            )
        })?;
        AnyArtifactReader::new(reader.handle.clone())
    }

    pub fn get_optional_artifact_by_key(
        &self,
        selector: impl Into<ArtifactSelector>,
    ) -> Result<Option<AnyArtifactReader>> {
        let artifact_id = self.resolved_key(selector)?;
        self.readers
            .get(&artifact_id)
            .map(|reader| AnyArtifactReader::new(reader.handle.clone()))
            .transpose()
    }

    pub fn contains(&self, selector: impl Into<ArtifactSelector>) -> Result<bool> {
        let artifact_id = self.resolved_key(selector)?;
        Ok(self.readers.contains_key(&artifact_id))
    }
}

#[derive(Clone)]
pub struct OutputArtifacts {
    writers: HashMap<ArtifactKey, AnyArtifactWriter>,
    aliases: HashMap<ArtifactKey, ArtifactKey>,
}

impl OutputArtifacts {
    pub fn from_writers(writers: &[AnyArtifactWriter]) -> Self {
        Self::from_writers_with_aliases(writers, HashMap::new())
    }

    pub fn from_writers_with_aliases(
        writers: &[AnyArtifactWriter],
        aliases: HashMap<ArtifactKey, ArtifactKey>,
    ) -> Self {
        Self {
            writers: writers
                .iter()
                .cloned()
                .map(|writer| (writer.artifact_id().clone(), writer))
                .collect(),
            aliases,
        }
    }

    fn resolved_key(&self, selector: impl Into<ArtifactSelector>) -> Result<ArtifactKey> {
        let key = selector.into().to_artifact_key()?;
        Ok(self.aliases.get(&key).cloned().unwrap_or(key))
    }

    pub fn get_artifact<T>(&self, declaration: &ArtifactDeclaration<T>) -> Result<ArtifactWriter<T>>
    where
        T: Send + Sync + 'static,
    {
        let artifact_id = self
            .aliases
            .get(declaration.artifact_id())
            .unwrap_or_else(|| declaration.artifact_id());
        let writer = self.writers.get(artifact_id).ok_or_else(|| {
            anyhow!(
                "missing output artifact: {} (resolved as {})",
                declaration.artifact_id(),
                artifact_id
            )
        })?;
        writer.downcast(declaration)
    }

    pub fn get_artifact_by_key(
        &self,
        selector: impl Into<ArtifactSelector>,
    ) -> Result<AnyArtifactWriter> {
        let requested = selector.into().to_artifact_key()?;
        let artifact_id = self
            .aliases
            .get(&requested)
            .cloned()
            .unwrap_or(requested.clone());
        self.writers.get(&artifact_id).cloned().ok_or_else(|| {
            anyhow!(
                "missing output artifact: {} (resolved as {})",
                requested,
                artifact_id
            )
        })
    }

    pub fn get_optional_artifact_by_key(
        &self,
        selector: impl Into<ArtifactSelector>,
    ) -> Result<Option<AnyArtifactWriter>> {
        let artifact_id = self.resolved_key(selector)?;
        Ok(self.writers.get(&artifact_id).cloned())
    }

    pub fn contains(&self, selector: impl Into<ArtifactSelector>) -> Result<bool> {
        let artifact_id = self.resolved_key(selector)?;
        Ok(self.writers.contains_key(&artifact_id))
    }

    pub async fn produced_instances(&self) -> Result<Vec<AnyArtifactInstance>> {
        let mut missing = Vec::new();
        let mut produced = Vec::with_capacity(self.writers.len());

        for writer in self.writers.values() {
            if writer.is_written().await {
                produced.push(AnyArtifactInstance {
                    handle: writer.handle.clone(),
                });
            } else {
                missing.push(writer.artifact_id().to_string());
            }
        }

        if !missing.is_empty() {
            bail!(
                "stage did not write declared outputs: {}",
                missing.join(", ")
            );
        }

        Ok(produced)
    }
}

#[derive(Clone)]
pub struct ProvidedStageOutput {
    pub declaration: AnyArtifactDeclaration,
    pub target: Arc<dyn ArtifactTargetHandle>,
}

#[derive(Clone)]
pub struct StageCacheWait {
    pub artifact_id: ArtifactKey,
    pub cache_wait: CacheWait,
}

pub struct StageOutputBindings {
    pub available: Vec<AnyArtifactInstance>,
    pub writers: Vec<AnyArtifactWriter>,
    pub waits: Vec<StageCacheWait>,
}

impl StageOutputBindings {
    pub fn has_waits(&self) -> bool {
        !self.waits.is_empty()
    }

    pub fn all_available(&self) -> bool {
        self.writers.is_empty() && self.waits.is_empty()
    }

    pub fn all_reserved_for_write(&self) -> bool {
        !self.writers.is_empty() && self.available.is_empty() && self.waits.is_empty()
    }

    pub fn has_partial_cache_hit(&self) -> bool {
        !self.available.is_empty() && !self.writers.is_empty() && self.waits.is_empty()
    }
}

pub struct ResolvedStageOutputBindings {
    pub available: Vec<AnyArtifactInstance>,
    pub writers: Vec<AnyArtifactWriter>,
}

pub enum StageOutputResolution {
    Resolved(ResolvedStageOutputBindings),
    Wait(StageCacheWait),
}
