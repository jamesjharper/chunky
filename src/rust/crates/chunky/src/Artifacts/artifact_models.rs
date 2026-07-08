//! Core artifact contracts demonstrated by the example.
//!
//! This module mirrors the current Python artifact snippet. It keeps the Rust
//! example intentionally focused: graph creation, planner-driven phase
//! selection, and scheduler-owned defaults are represented as explicit seams
//! rather than as a complete Chunky implementation.

use crate::artifact_handles::{ArtifactSourceHandle, ArtifactTargetHandle};
use anyhow::{anyhow, bail, Result};
use async_trait::async_trait;
use serde::{de::DeserializeOwned, Serialize};
use std::{
    any::{Any, TypeId},
    collections::HashSet,
    fmt::{Display, Formatter},
    marker::PhantomData,
    str::FromStr,
    sync::Arc,
};
use tokio::sync::Mutex;

pub type ArtifactValue = Arc<dyn Any + Send + Sync>;

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct ArtifactKey {
    pub domain: String,
    pub content_kind: String,
    pub phase: Option<u32>,
    pub override_domain: Option<String>,
}

impl ArtifactKey {
    pub fn new(
        domain: impl Into<String>,
        content_kind: impl Into<String>,
        phase: Option<u32>,
        override_domain: Option<String>,
    ) -> Result<Self> {
        let domain = domain.into();
        let content_kind = content_kind.into();
        if domain.is_empty() || content_kind.is_empty() {
            bail!("artifact key contains an empty component");
        }
        if matches!(override_domain.as_deref(), Some("")) {
            bail!("artifact key contains an empty override domain");
        }
        Ok(Self {
            domain,
            content_kind,
            phase,
            override_domain,
        })
    }

    pub fn parse(value: &str) -> Result<Self> {
        value.parse()
    }

    pub fn family(&self) -> (Option<&str>, &str, &str) {
        (
            self.override_domain.as_deref(),
            self.domain.as_str(),
            self.content_kind.as_str(),
        )
    }

    pub fn family_key(&self) -> (Option<String>, String, String) {
        (
            self.override_domain.clone(),
            self.domain.clone(),
            self.content_kind.clone(),
        )
    }

    pub fn without_phase(&self) -> Self {
        Self {
            domain: self.domain.clone(),
            content_kind: self.content_kind.clone(),
            phase: None,
            override_domain: self.override_domain.clone(),
        }
    }

    pub fn without_override(&self) -> Self {
        Self {
            domain: self.domain.clone(),
            content_kind: self.content_kind.clone(),
            phase: self.phase,
            override_domain: None,
        }
    }

    pub fn with_override_domain(&self, override_domain: impl Into<String>) -> Result<Self> {
        let override_domain = override_domain.into().trim().to_owned();
        if override_domain.is_empty() {
            bail!("override_domain must be a non-empty string");
        }
        Ok(Self {
            domain: self.domain.clone(),
            content_kind: self.content_kind.clone(),
            phase: self.phase,
            override_domain: Some(override_domain),
        })
    }
}

impl FromStr for ArtifactKey {
    type Err = anyhow::Error;

    fn from_str(value: &str) -> Result<Self> {
        let parts: Vec<&str> = value.split("//").collect();
        let (override_domain, domain, remainder) = match parts.as_slice() {
            [domain, remainder] => (None, *domain, *remainder),
            [override_domain, domain, remainder] => {
                (Some((*override_domain).to_owned()), *domain, *remainder)
            }
            _ => {
                bail!(
                    "artifact key must be DOMAIN//CONTENT_KIND[#PHASE] or OVERRIDE_DOMAIN//DOMAIN//CONTENT_KIND[#PHASE]: {value:?}"
                );
            }
        };

        if domain.is_empty()
            || remainder.is_empty()
            || matches!(override_domain.as_deref(), Some(""))
        {
            bail!("artifact key contains an empty component: {value:?}");
        }

        if let Some((content_kind, phase_text)) = remainder.rsplit_once('#') {
            if content_kind.is_empty() {
                bail!("artifact content kind is empty: {value:?}");
            }
            let phase = phase_text.parse::<u32>()?;
            return Self::new(domain, content_kind, Some(phase), override_domain);
        }

        Self::new(domain, remainder, None, override_domain)
    }
}

impl Display for ArtifactKey {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        if let Some(override_domain) = &self.override_domain {
            write!(formatter, "{override_domain}//")?;
        }
        write!(formatter, "{}//{}", self.domain, self.content_kind)?;
        if let Some(phase) = self.phase {
            write!(formatter, "#{phase}")?;
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ArtifactRole {
    Source,
    Settings,
    Intermediate,
    Target,
    Diagnostic,
}

impl ArtifactRole {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Source => "source",
            Self::Settings => "settings",
            Self::Intermediate => "intermediate",
            Self::Target => "target",
            Self::Diagnostic => "diagnostic",
        }
    }
}

pub trait Codec<T>: Send + Sync + 'static {
    fn codec_id(&self) -> &'static str;
    fn codec_version(&self) -> u32;
    fn mime_type(&self) -> &'static str;
    fn extension(&self) -> &'static str;
    fn serialize(&self, value: &T) -> Result<Vec<u8>>;
    fn deserialize(&self, raw: &[u8]) -> Result<T>;
}

pub trait ErasedCodec: Send + Sync {
    fn codec_id(&self) -> &'static str;
    fn codec_version(&self) -> u32;
    fn mime_type(&self) -> &'static str;
    fn extension(&self) -> &'static str;
    fn value_type_id(&self) -> TypeId;
    fn serialize_any(&self, value: &ArtifactValue) -> Result<Vec<u8>>;
    fn deserialize_any(&self, raw: &[u8]) -> Result<ArtifactValue>;
}

struct CodecAdapter<T, C> {
    codec: C,
    _marker: PhantomData<fn() -> T>,
}

impl<T, C> ErasedCodec for CodecAdapter<T, C>
where
    T: Send + Sync + 'static,
    C: Codec<T>,
{
    fn codec_id(&self) -> &'static str {
        self.codec.codec_id()
    }

    fn codec_version(&self) -> u32 {
        self.codec.codec_version()
    }

    fn mime_type(&self) -> &'static str {
        self.codec.mime_type()
    }

    fn extension(&self) -> &'static str {
        self.codec.extension()
    }

    fn value_type_id(&self) -> TypeId {
        TypeId::of::<T>()
    }

    fn serialize_any(&self, value: &ArtifactValue) -> Result<Vec<u8>> {
        let typed = value
            .as_ref()
            .downcast_ref::<T>()
            .ok_or_else(|| anyhow!("codec received the wrong concrete artifact value type"))?;
        self.codec.serialize(typed)
    }

    fn deserialize_any(&self, raw: &[u8]) -> Result<ArtifactValue> {
        Ok(Arc::new(self.codec.deserialize(raw)?))
    }
}

#[async_trait]
pub trait DefaultValueSourceProvider<T>: Send + Sync {
    async fn resolve_source(
        &self,
        supplied_source: Option<Arc<dyn ArtifactSourceHandle>>,
        declaration: &ArtifactDeclaration<T>,
    ) -> Result<Arc<dyn ArtifactSourceHandle>>;
}

#[async_trait]
pub trait ErasedDefaultValueSourceProvider: Send + Sync {
    async fn resolve_source_any(
        &self,
        supplied_source: Option<Arc<dyn ArtifactSourceHandle>>,
        declaration: &AnyArtifactDeclaration,
    ) -> Result<Arc<dyn ArtifactSourceHandle>>;
}

struct DefaultValueSourceProviderAdapter<T> {
    provider: Arc<dyn DefaultValueSourceProvider<T>>,
    _marker: PhantomData<fn() -> T>,
}

#[async_trait]
impl<T> ErasedDefaultValueSourceProvider for DefaultValueSourceProviderAdapter<T>
where
    T: Send + Sync + 'static,
{
    async fn resolve_source_any(
        &self,
        supplied_source: Option<Arc<dyn ArtifactSourceHandle>>,
        declaration: &AnyArtifactDeclaration,
    ) -> Result<Arc<dyn ArtifactSourceHandle>> {
        let typed = ArtifactDeclaration::<T> {
            inner: declaration.clone(),
            default_value_source_provider: Some(self.provider.clone()),
            _marker: PhantomData,
        };
        self.provider.resolve_source(supplied_source, &typed).await
    }
}

pub struct ArtifactDeclarationInner {
    pub artifact_id: ArtifactKey,
    pub role: ArtifactRole,
    pub version: u32,
    pub codec: Arc<dyn ErasedCodec>,
    pub cacheable: bool,
    pub default_value_source_provider: Option<Arc<dyn ErasedDefaultValueSourceProvider>>,
}

pub type AnyArtifactDeclaration = Arc<ArtifactDeclarationInner>;

pub struct ArtifactDeclaration<T> {
    inner: AnyArtifactDeclaration,
    pub default_value_source_provider: Option<Arc<dyn DefaultValueSourceProvider<T>>>,
    _marker: PhantomData<fn() -> T>,
}

impl<T> Clone for ArtifactDeclaration<T> {
    fn clone(&self) -> Self {
        Self {
            inner: self.inner.clone(),
            default_value_source_provider: self.default_value_source_provider.clone(),
            _marker: PhantomData,
        }
    }
}

impl<T> ArtifactDeclaration<T>
where
    T: Send + Sync + 'static,
{
    pub fn new<C>(
        artifact_id: ArtifactKey,
        role: ArtifactRole,
        version: u32,
        codec: C,
        cacheable: bool,
    ) -> Result<Self>
    where
        C: Codec<T>,
    {
        if artifact_id.phase.is_some() && role != ArtifactRole::Intermediate {
            bail!("only intermediate artifact declarations may use phases");
        }

        Ok(Self {
            inner: Arc::new(ArtifactDeclarationInner {
                artifact_id,
                role,
                version,
                codec: Arc::new(CodecAdapter::<T, C> {
                    codec,
                    _marker: PhantomData,
                }),
                cacheable,
                default_value_source_provider: None,
            }),
            default_value_source_provider: None,
            _marker: PhantomData,
        })
    }

    fn erased_default_value_source_provider(
        provider: Option<Arc<dyn DefaultValueSourceProvider<T>>>,
    ) -> Option<Arc<dyn ErasedDefaultValueSourceProvider>> {
        provider.map(|provider| {
            Arc::new(DefaultValueSourceProviderAdapter::<T> {
                provider,
                _marker: PhantomData,
            }) as Arc<dyn ErasedDefaultValueSourceProvider>
        })
    }

    pub fn with_default_value_source_provider(
        mut self,
        provider: Arc<dyn DefaultValueSourceProvider<T>>,
    ) -> Self {
        let default_value_source_provider = Some(provider);
        self.inner = Arc::new(ArtifactDeclarationInner {
            artifact_id: self.inner.artifact_id.clone(),
            role: self.inner.role,
            version: self.inner.version,
            codec: self.inner.codec.clone(),
            cacheable: self.inner.cacheable,
            default_value_source_provider: Self::erased_default_value_source_provider(
                default_value_source_provider.clone(),
            ),
        });
        self.default_value_source_provider = default_value_source_provider;
        self
    }

    pub fn artifact_id(&self) -> &ArtifactKey {
        &self.inner.artifact_id
    }

    pub fn role(&self) -> ArtifactRole {
        self.inner.role
    }

    pub fn version(&self) -> u32 {
        self.inner.version
    }

    pub fn cacheable(&self) -> bool {
        self.inner.cacheable
    }

    pub fn erase(&self) -> AnyArtifactDeclaration {
        self.inner.clone()
    }

    pub fn with_override_domain(&self, override_domain: impl Into<String>) -> Result<Self> {
        self.with_override_domain_and_default_value_source_provider(override_domain, None)
    }

    pub fn with_override_domain_and_default_value_source_provider(
        &self,
        override_domain: impl Into<String>,
        default_value_source_provider: Option<Arc<dyn DefaultValueSourceProvider<T>>>,
    ) -> Result<Self> {
        let artifact_id = self
            .inner
            .artifact_id
            .with_override_domain(override_domain)?;
        let default_value_source_provider =
            default_value_source_provider.or_else(|| self.default_value_source_provider.clone());

        Ok(Self {
            inner: Arc::new(ArtifactDeclarationInner {
                artifact_id,
                role: self.inner.role,
                version: self.inner.version,
                codec: self.inner.codec.clone(),
                cacheable: self.inner.cacheable,
                default_value_source_provider: Self::erased_default_value_source_provider(
                    default_value_source_provider.clone(),
                ),
            }),
            default_value_source_provider,
            _marker: PhantomData,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArtifactCapability {
    pub artifact_id: ArtifactKey,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum PhaseMatchMode {
    AtMost,
    Exact,
}

impl PhaseMatchMode {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::AtMost => "at_most",
            Self::Exact => "exact",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArtifactRequirement {
    pub artifact_id: ArtifactKey,
    pub required: bool,
    pub phase_match: PhaseMatchMode,
    pub pushed_settings: Vec<ArtifactKey>,
}

impl ArtifactRequirement {
    pub fn new(
        artifact_id: ArtifactKey,
        required: bool,
        phase_match: PhaseMatchMode,
        pushed_settings: Vec<ArtifactKey>,
    ) -> Result<Self> {
        let unique: HashSet<_> = pushed_settings.iter().collect();
        if unique.len() != pushed_settings.len() {
            bail!("artifact requirement contains duplicate pushed settings");
        }
        Ok(Self {
            artifact_id,
            required,
            phase_match,
            pushed_settings,
        })
    }

    pub fn required(artifact_id: ArtifactKey) -> Self {
        Self {
            artifact_id,
            required: true,
            phase_match: PhaseMatchMode::AtMost,
            pushed_settings: Vec::new(),
        }
    }

    pub fn optional(artifact_id: ArtifactKey) -> Self {
        Self {
            artifact_id,
            required: false,
            phase_match: PhaseMatchMode::AtMost,
            pushed_settings: Vec::new(),
        }
    }

    pub fn exact_phase(mut self) -> Self {
        self.phase_match = PhaseMatchMode::Exact;
        self
    }

    pub fn with_pushed_settings(mut self, pushed_settings: Vec<ArtifactKey>) -> Result<Self> {
        let unique: HashSet<_> = pushed_settings.iter().collect();
        if unique.len() != pushed_settings.len() {
            bail!("artifact requirement contains duplicate pushed settings");
        }
        self.pushed_settings = pushed_settings;
        Ok(self)
    }
}

pub fn canonical_json_bytes<T: Serialize>(value: &T) -> Result<Vec<u8>> {
    let canonical_value = serde_json::to_value(value)?;
    Ok(serde_json::to_vec(&canonical_value)?)
}

pub fn deserialize_json<T: DeserializeOwned>(raw: &[u8]) -> Result<T> {
    Ok(serde_json::from_slice(raw)?)
}


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
    pub(crate) cached: Mutex<Option<ArtifactValue>>,
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_and_formats_artifact_keys() {
        let key = ArtifactKey::parse("analysis//audio_summary#100").unwrap();
        assert_eq!(key.domain, "analysis");
        assert_eq!(key.content_kind, "audio_summary");
        assert_eq!(key.phase, Some(100));
        assert_eq!(key.override_domain, None);
        assert_eq!(key.to_string(), "analysis//audio_summary#100");
        assert_eq!(key.without_phase().to_string(), "analysis//audio_summary");
    }

    #[test]
    fn parses_override_domain_artifact_keys() {
        let key = ArtifactKey::parse("asr//audio//prepared#10").unwrap();
        assert_eq!(key.override_domain.as_deref(), Some("asr"));
        assert_eq!(key.without_override().to_string(), "audio//prepared#10");
        assert_eq!(key.without_phase().to_string(), "asr//audio//prepared");
    }
}
