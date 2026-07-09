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
    hash::{Hash, Hasher},
    marker::PhantomData,
    str::FromStr,
    sync::Arc,
};
use tokio::sync::Mutex;

pub type ArtifactValue = Arc<dyn Any + Send + Sync>;

pub const GLOBAL_PROJECTION_CONTEXT: &str = "global";
pub const A_REF_SCHEME: &str = "a-ref:";
pub const A_INSTANCE_SCHEME: &str = "a-instance:";
pub const A_TASK_SCHEME: &str = "a-task:";
pub const ANY_PHASE: &str = "any";
pub const NO_PHASE: &str = "none";

fn validate_segment(name: &str, value: impl Into<String>) -> Result<String> {
    let value = value.into().trim().to_owned();
    if value.is_empty() {
        bail!("{name} must be a non-empty string");
    }
    if value.contains('/') {
        bail!("{name} must not contain '/': {value:?}");
    }
    Ok(value)
}

fn decode_path(path: &str) -> Vec<String> {
    path.split('/')
        .filter(|segment| !segment.is_empty())
        .map(ToOwned::to_owned)
        .collect()
}

fn looks_like_phase(value: &str) -> bool {
    value == ANY_PHASE || value.parse::<u32>().is_ok()
}

fn parse_ref_phase(value: &str) -> Result<Option<u32>> {
    if value == ANY_PHASE {
        Ok(None)
    } else {
        Ok(Some(value.parse::<u32>()?))
    }
}

fn parse_concrete_phase(value: &str) -> Result<Option<u32>> {
    if value == ANY_PHASE {
        bail!("concrete artifact locators must not use the 'any' phase token");
    }
    if value == NO_PHASE {
        Ok(None)
    } else {
        Ok(Some(value.parse::<u32>()?))
    }
}

fn format_ref_phase(phase: Option<u32>) -> String {
    phase.map(|value| value.to_string()).unwrap_or_else(|| ANY_PHASE.to_owned())
}

fn format_concrete_phase(phase: Option<u32>) -> String {
    phase.map(|value| value.to_string()).unwrap_or_else(|| NO_PHASE.to_owned())
}

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct ArtifactKey {
    pub domain: String,
    pub content_kind: String,
    pub phase: Option<u32>,
    /// Request-boundary projection context. Older planner code still refers to
    /// this as override_domain, but it is not part of concrete artifact cache
    /// identity.
    pub override_domain: Option<String>,
}

impl ArtifactKey {
    pub fn new(
        domain: impl Into<String>,
        content_kind: impl Into<String>,
        phase: Option<u32>,
        override_domain: Option<String>,
    ) -> Result<Self> {
        let domain = validate_segment("domain", domain)?;
        let content_kind = validate_segment("content_kind", content_kind)?;
        let override_domain = match override_domain {
            Some(value) => Some(validate_segment("projection_context", value)?),
            None => None,
        };
        Ok(Self {
            domain,
            content_kind,
            phase,
            override_domain,
        })
    }

    /// Parser for the public semantic reference surface.
    /// Both canonical `a-ref:/...` and short path-like forms such as
    /// `domain/content_kind` are accepted. Legacy DOMAIN//KIND and
    /// DOMAIN//KIND#PHASE strings are intentionally not accepted.
    pub fn parse(value: &str) -> Result<Self> {
        Self::from_a_ref(value)
    }

    pub fn from_a_ref(value: &str) -> Result<Self> {
        Ok(ArtifactRefLocator::parse(value)?.artifact_key)
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

    pub fn canonical_family(&self) -> (&str, &str) {
        (self.domain.as_str(), self.content_kind.as_str())
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

    pub fn without_projection(&self) -> Self {
        self.without_override()
    }

    pub fn with_override_domain(&self, override_domain: impl Into<String>) -> Result<Self> {
        let override_domain = validate_segment("projection_context", override_domain)?;
        Ok(Self {
            domain: self.domain.clone(),
            content_kind: self.content_kind.clone(),
            phase: self.phase,
            override_domain: Some(override_domain),
        })
    }

    pub fn to_a_ref(&self, projected: bool) -> String {
        let projection = if projected {
            self.override_domain
                .as_deref()
                .unwrap_or(GLOBAL_PROJECTION_CONTEXT)
        } else {
            GLOBAL_PROJECTION_CONTEXT
        };
        ArtifactRefLocator::from_key(self, Some(projection.to_owned())).to_uri(projected)
    }
}

impl FromStr for ArtifactKey {
    type Err = anyhow::Error;

    fn from_str(value: &str) -> Result<Self> {
        Self::parse(value)
    }
}

impl Display for ArtifactKey {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.to_a_ref(false))
    }
}

#[derive(Clone, Debug, Eq)]
pub struct ArtifactRefLocator {
    pub projection_context: String,
    pub artifact_key: ArtifactKey,
}

impl ArtifactRefLocator {
    pub fn new(projection_context: impl Into<String>, artifact_key: ArtifactKey) -> Result<Self> {
        Ok(Self {
            projection_context: validate_segment("projection_context", projection_context)?,
            artifact_key: artifact_key.without_projection(),
        })
    }

    pub fn parse(value: &str) -> Result<Self> {
        let has_scheme = value.starts_with(A_REF_SCHEME);
        if !has_scheme && (value.contains("//") || value.contains('#') || value.contains("::")) {
            bail!(
                "legacy artifact reference strings are not accepted; use domain/content_kind[/phase] or a-ref:/domain/content_kind[/phase]"
            );
        }
        let path = value.strip_prefix(A_REF_SCHEME).unwrap_or(value);
        let segments = decode_path(path);
        let (projection, domain, content_kind, phase) = match segments.as_slice() {
            [domain, content_kind] => (
                GLOBAL_PROJECTION_CONTEXT.to_owned(),
                domain.clone(),
                content_kind.clone(),
                None,
            ),
            [a, b, c] if looks_like_phase(c) => (
                GLOBAL_PROJECTION_CONTEXT.to_owned(),
                a.clone(),
                b.clone(),
                parse_ref_phase(c)?,
            ),
            [projection, domain, content_kind] => (
                projection.clone(),
                domain.clone(),
                content_kind.clone(),
                None,
            ),
            [projection, domain, content_kind, phase] => (
                projection.clone(),
                domain.clone(),
                content_kind.clone(),
                parse_ref_phase(phase)?,
            ),
            _ => bail!(
                "artifact reference path must be domain/content_kind[/phase] or projection/domain/content_kind[/phase], optionally prefixed by a-ref:/: {value:?}"
            ),
        };
        Self::new(projection, ArtifactKey::new(domain, content_kind, phase, None)?)
    }

    pub fn from_key(artifact_key: &ArtifactKey, projection_context: Option<String>) -> Self {
        Self {
            projection_context: projection_context
                .or_else(|| artifact_key.override_domain.clone())
                .unwrap_or_else(|| GLOBAL_PROJECTION_CONTEXT.to_owned()),
            artifact_key: artifact_key.without_projection(),
        }
    }

    pub fn to_uri(&self, projected: bool) -> String {
        let projection = if projected {
            self.projection_context.as_str()
        } else {
            GLOBAL_PROJECTION_CONTEXT
        };
        format!(
            "{A_REF_SCHEME}/{}/{}/{}/{}",
            projection,
            self.artifact_key.domain,
            self.artifact_key.content_kind,
            format_ref_phase(self.artifact_key.phase)
        )
    }
}

impl Display for ArtifactRefLocator {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.to_uri(false))
    }
}

impl PartialEq for ArtifactRefLocator {
    fn eq(&self, other: &Self) -> bool {
        self.artifact_key.without_projection() == other.artifact_key.without_projection()
    }
}

impl Hash for ArtifactRefLocator {
    fn hash<H: Hasher>(&self, state: &mut H) {
        let key = self.artifact_key.without_projection();
        key.domain.hash(state);
        key.content_kind.hash(state);
        key.phase.hash(state);
    }
}

#[derive(Clone, Debug, Eq)]
pub struct ArtifactInstanceLocator {
    pub projection_context: String,
    pub artifact_key: ArtifactKey,
    pub fingerprint: String,
}

impl ArtifactInstanceLocator {
    pub fn new(
        projection_context: impl Into<String>,
        artifact_key: ArtifactKey,
        fingerprint: impl Into<String>,
    ) -> Result<Self> {
        Ok(Self {
            projection_context: validate_segment("projection_context", projection_context)?,
            artifact_key: artifact_key.without_projection(),
            fingerprint: validate_segment("fingerprint", fingerprint)?,
        })
    }

    pub fn from_key(artifact_key: &ArtifactKey, fingerprint: impl Into<String>, projection_context: Option<String>) -> Result<Self> {
        Self::new(
            projection_context
                .or_else(|| artifact_key.override_domain.clone())
                .unwrap_or_else(|| GLOBAL_PROJECTION_CONTEXT.to_owned()),
            artifact_key.without_projection(),
            fingerprint,
        )
    }

    pub fn parse(value: &str) -> Result<Self> {
        if !value.starts_with(A_INSTANCE_SCHEME) {
            bail!("artifact instance must use explicit a-instance scheme: {value:?}");
        }
        let segments = decode_path(&value[A_INSTANCE_SCHEME.len()..]);
        match segments.as_slice() {
            [projection, domain, content_kind, phase, marker, fingerprint] if marker == "fingerprint" => {
                Self::new(
                    projection.clone(),
                    ArtifactKey::new(domain.clone(), content_kind.clone(), parse_concrete_phase(phase)?, None)?,
                    fingerprint.clone(),
                )
            }
            _ => bail!("a-instance path must be /projection/domain/content_kind/phase/fingerprint/fingerprint"),
        }
    }

    pub fn artifact_id(&self) -> &ArtifactKey {
        &self.artifact_key
    }

    pub fn to_uri(&self, projected: bool) -> String {
        let projection = if projected {
            self.projection_context.as_str()
        } else {
            GLOBAL_PROJECTION_CONTEXT
        };
        format!(
            "{A_INSTANCE_SCHEME}/{}/{}/{}/{}/fingerprint/{}",
            projection,
            self.artifact_key.domain,
            self.artifact_key.content_kind,
            format_concrete_phase(self.artifact_key.phase),
            self.fingerprint,
        )
    }
}

impl Display for ArtifactInstanceLocator {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.to_uri(false))
    }
}

impl PartialEq for ArtifactInstanceLocator {
    fn eq(&self, other: &Self) -> bool {
        self.artifact_key.without_projection() == other.artifact_key.without_projection()
            && self.fingerprint == other.fingerprint
    }
}

impl Hash for ArtifactInstanceLocator {
    fn hash<H: Hasher>(&self, state: &mut H) {
        let key = self.artifact_key.without_projection();
        key.domain.hash(state);
        key.content_kind.hash(state);
        key.phase.hash(state);
        self.fingerprint.hash(state);
    }
}

#[derive(Clone, Debug, Eq)]
pub struct TaskArtifactLocator {
    pub projection_context: String,
    pub artifact_key: ArtifactKey,
    pub task_id: String,
}

impl TaskArtifactLocator {
    pub fn new(
        projection_context: impl Into<String>,
        artifact_key: ArtifactKey,
        task_id: impl Into<String>,
    ) -> Result<Self> {
        Ok(Self {
            projection_context: validate_segment("projection_context", projection_context)?,
            artifact_key: artifact_key.without_projection(),
            task_id: validate_segment("task_id", task_id)?,
        })
    }

    pub fn from_key(artifact_key: &ArtifactKey, task_id: impl Into<String>, projection_context: Option<String>) -> Result<Self> {
        Self::new(
            projection_context
                .or_else(|| artifact_key.override_domain.clone())
                .unwrap_or_else(|| GLOBAL_PROJECTION_CONTEXT.to_owned()),
            artifact_key.without_projection(),
            task_id,
        )
    }

    pub fn parse(value: &str) -> Result<Self> {
        if !value.starts_with(A_TASK_SCHEME) {
            bail!("task artifact must use explicit a-task scheme: {value:?}");
        }
        let segments = decode_path(&value[A_TASK_SCHEME.len()..]);
        match segments.as_slice() {
            [projection, domain, content_kind, phase, marker, task_id] if marker == "task" => {
                Self::new(
                    projection.clone(),
                    ArtifactKey::new(domain.clone(), content_kind.clone(), parse_concrete_phase(phase)?, None)?,
                    task_id.clone(),
                )
            }
            _ => bail!("a-task path must be /projection/domain/content_kind/phase/task/task_id"),
        }
    }

    pub fn artifact_id(&self) -> &ArtifactKey {
        &self.artifact_key
    }

    pub fn to_uri(&self, projected: bool) -> String {
        let projection = if projected {
            self.projection_context.as_str()
        } else {
            GLOBAL_PROJECTION_CONTEXT
        };
        format!(
            "{A_TASK_SCHEME}/{}/{}/{}/{}/task/{}",
            projection,
            self.artifact_key.domain,
            self.artifact_key.content_kind,
            format_concrete_phase(self.artifact_key.phase),
            self.task_id,
        )
    }
}

impl Display for TaskArtifactLocator {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.to_uri(false))
    }
}

impl PartialEq for TaskArtifactLocator {
    fn eq(&self, other: &Self) -> bool {
        self.artifact_key.without_projection() == other.artifact_key.without_projection()
            && self.task_id == other.task_id
    }
}

impl Hash for TaskArtifactLocator {
    fn hash<H: Hasher>(&self, state: &mut H) {
        let key = self.artifact_key.without_projection();
        key.domain.hash(state);
        key.content_kind.hash(state);
        key.phase.hash(state);
        self.task_id.hash(state);
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

    pub fn locator(&self) -> Result<ArtifactInstanceLocator> {
        ArtifactInstanceLocator::from_key(&self.declaration.artifact_id, self.fingerprint.clone(), None)
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

    pub fn locator(&self) -> Result<ArtifactInstanceLocator> {
        self.handle.locator()
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

    pub fn locator(&self) -> Result<ArtifactInstanceLocator> {
        self.handle.locator()
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
        let key = ArtifactKey::parse("analysis/audio_summary/100").unwrap();
        assert_eq!(key.domain, "analysis");
        assert_eq!(key.content_kind, "audio_summary");
        assert_eq!(key.phase, Some(100));
        assert_eq!(key.override_domain, None);
        assert_eq!(key.to_string(), "a-ref:/global/analysis/audio_summary/100");
        assert_eq!(ArtifactKey::parse("analysis/audio_summary").unwrap().to_string(), "a-ref:/global/analysis/audio_summary/any");
        assert_eq!(key.without_phase().to_string(), "a-ref:/global/analysis/audio_summary/any");
    }

    #[test]
    fn parses_override_domain_artifact_keys() {
        let locator = ArtifactRefLocator::parse("asr/audio/prepared/10").unwrap();
        let key = locator.artifact_key.with_override_domain(locator.projection_context).unwrap();
        assert_eq!(key.override_domain.as_deref(), Some("asr"));
        assert_eq!(key.without_override().to_string(), "a-ref:/global/audio/prepared/10");
        assert_eq!(key.without_phase().to_a_ref(true), "a-ref:/asr/audio/prepared/any");
    }
}
