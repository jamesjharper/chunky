//! Core stage planning and execution contracts.
//!
//! This is intentionally a focused source-level port. It assumes the artifact
//! modules from the current Rust artifact example exist in the same crate and
//! does not add crate scaffolding, Cargo files, or feature boilerplate.

use crate::artifact_binding_models::{InputArtifacts, OutputArtifacts};
use crate::artifact_models::{
    AnyArtifactDeclaration, ArtifactCapability, ArtifactKey, ArtifactRequirement, ArtifactRole,
    PhaseMatchMode,
};
use crate::fingerprints::FingerprintProvider;
use crate::phase_catalog::capability_covers_requirement;
use crate::runtime::{
    PipelineRuntimeServicesBuilder, PipelineRuntimeSettings, PipelineRuntimeSettingsBuilder,
    RuntimeStageSetting,
};
use crate::stage_manifest::StageManifest;
use anyhow::{anyhow, bail, Result};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::future::Future;
use std::path::PathBuf;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub type BoxFuture<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

/// Executor-facing stage contract.
///
/// The stage receives already-bound readers/writers plus a narrow execution
/// context. It does not reach back into the manifest, planner, cache, or task
/// runner to discover additional inputs or outputs.
pub trait StageExecutor: Send {
    fn execute<'a>(
        &'a mut self,
        inputs: &'a InputArtifacts,
        outputs: &'a OutputArtifacts,
        context: &'a StageExecutionContext,
        runtime_settings: &'a PipelineRuntimeSettings,
    ) -> BoxFuture<'a, Result<()>>;
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TaskCancelledError {
    pub reason: String,
}

impl std::fmt::Display for TaskCancelledError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.reason)
    }
}

impl std::error::Error for TaskCancelledError {}

/// Read-only cancellation contract exposed to stages.
///
/// The generic `wait_for` helper is implemented on `CancellationSource` below,
/// rather than on this trait, so the token remains object-safe for storage in
/// `StageExecutionContext`.
pub trait CancellationToken: Send + Sync {
    fn is_cancelled(&self) -> bool;
    fn reason(&self) -> Option<String>;
    fn wait_cancelled<'a>(&'a self) -> BoxFuture<'a, ()>;

    fn raise_if_cancelled(&self) -> Result<()> {
        if self.is_cancelled() {
            let reason = self.reason().unwrap_or_else(|| "task cancelled".to_owned());
            return Err(anyhow!(TaskCancelledError { reason }));
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum CancellationOrigin {
    #[default]
    Caller,
    Shutdown,
}

#[derive(Clone, Default)]
pub struct CancellationSource {
    inner: Arc<Mutex<CancellationState>>,
}

#[derive(Default)]
struct CancellationState {
    cancelled: bool,
    reason: Option<String>,
    origin: CancellationOrigin,
}

impl CancellationSource {
    pub fn token(&self) -> Arc<dyn CancellationToken> {
        Arc::new(self.clone())
    }

    pub fn cancel(&self, reason: Option<String>) -> bool {
        self.cancel_with_origin(reason, CancellationOrigin::Caller)
    }

    pub fn shutdown(&self, reason: Option<String>) -> bool {
        self.cancel_with_origin(
            Some(reason.unwrap_or_else(|| "process shutdown".to_owned())),
            CancellationOrigin::Shutdown,
        )
    }

    pub fn cancel_with_origin(&self, reason: Option<String>, origin: CancellationOrigin) -> bool {
        let mut state = self.inner.lock().expect("cancellation mutex poisoned");
        if state.cancelled {
            return false;
        }
        state.cancelled = true;
        state.reason = reason;
        state.origin = origin;
        true
    }

    pub fn origin(&self) -> CancellationOrigin {
        self.inner
            .lock()
            .expect("cancellation mutex poisoned")
            .origin
    }

    /// Cancellation-aware local wait helper.
    ///
    /// Cancelling this task-local wait must not cancel the other task that owns
    /// a cache writer.
    /// In a production runtime this should use the scheduler/runtime's native
    /// cancellation primitive rather than polling.
    pub fn wait_for<'a, F, T>(&'a self, awaitable: F) -> BoxFuture<'a, Result<T>>
    where
        F: Future<Output = Result<T>> + Send + 'a,
        T: Send + 'a,
    {
        Box::pin(async move {
            self.raise_if_cancelled()?;
            tokio::pin!(awaitable);
            loop {
                tokio::select! {
                    result = &mut awaitable => return result,
                    _ = tokio::time::sleep(Duration::from_millis(25)) => {
                        self.raise_if_cancelled()?;
                    }
                }
            }
        })
    }
}

impl CancellationToken for CancellationSource {
    fn is_cancelled(&self) -> bool {
        self.inner
            .lock()
            .expect("cancellation mutex poisoned")
            .cancelled
    }

    fn reason(&self) -> Option<String> {
        self.inner
            .lock()
            .expect("cancellation mutex poisoned")
            .reason
            .clone()
    }

    fn wait_cancelled<'a>(&'a self) -> BoxFuture<'a, ()> {
        Box::pin(async move {
            while !self.is_cancelled() {
                tokio::time::sleep(Duration::from_millis(25)).await;
            }
        })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct StageSubtaskProgress {
    pub subtask_id: String,
    pub name: String,
    pub percentage: Option<f64>,
}

pub trait StageSubtaskReporter: Send + Sync {
    fn update(&self, name: Option<String>, percentage: Option<f64>) -> Result<()>;
    fn finish(&self);
}

pub trait StageProgressReporter: Send + Sync {
    fn start_subtask(
        &self,
        subtask_id: String,
        name: String,
        percentage: Option<f64>,
    ) -> Result<Box<dyn StageSubtaskReporter>>;
}

#[derive(Clone, Default)]
pub struct InMemoryStageProgressReporter {
    subtasks: Arc<Mutex<BTreeMap<String, StageSubtaskProgress>>>,
}

impl InMemoryStageProgressReporter {
    pub fn snapshot(&self) -> Vec<StageSubtaskProgress> {
        self.subtasks
            .lock()
            .expect("progress mutex poisoned")
            .values()
            .cloned()
            .collect()
    }

    pub fn clear(&self) {
        self.subtasks
            .lock()
            .expect("progress mutex poisoned")
            .clear();
    }

    fn validate_percentage(percentage: Option<f64>) -> Result<()> {
        if let Some(value) = percentage {
            if !(0.0..=100.0).contains(&value) {
                bail!("percentage must be in the inclusive range 0..100");
            }
        }
        Ok(())
    }

    fn update_subtask(
        &self,
        subtask_id: &str,
        name: Option<String>,
        percentage: Option<f64>,
    ) -> Result<()> {
        Self::validate_percentage(percentage)?;
        let mut subtasks = self.subtasks.lock().expect("progress mutex poisoned");
        let current = subtasks
            .get(subtask_id)
            .cloned()
            .ok_or_else(|| anyhow!("unknown subtask: {subtask_id}"))?;
        subtasks.insert(
            subtask_id.to_owned(),
            StageSubtaskProgress {
                subtask_id: current.subtask_id,
                name: name.unwrap_or(current.name),
                percentage: percentage.or(current.percentage),
            },
        );
        Ok(())
    }

    fn finish_subtask(&self, subtask_id: &str) {
        self.subtasks
            .lock()
            .expect("progress mutex poisoned")
            .remove(subtask_id);
    }
}

impl StageProgressReporter for InMemoryStageProgressReporter {
    fn start_subtask(
        &self,
        subtask_id: String,
        name: String,
        percentage: Option<f64>,
    ) -> Result<Box<dyn StageSubtaskReporter>> {
        Self::validate_percentage(percentage)?;
        let mut subtasks = self.subtasks.lock().expect("progress mutex poisoned");
        if subtasks.contains_key(&subtask_id) {
            bail!("subtask already exists: {subtask_id}");
        }
        subtasks.insert(
            subtask_id.clone(),
            StageSubtaskProgress {
                subtask_id: subtask_id.clone(),
                name,
                percentage,
            },
        );
        Ok(Box::new(InMemorySubtaskReporter {
            owner: self.clone(),
            subtask_id,
            finished: Mutex::new(false),
        }))
    }
}

struct InMemorySubtaskReporter {
    owner: InMemoryStageProgressReporter,
    subtask_id: String,
    finished: Mutex<bool>,
}

impl StageSubtaskReporter for InMemorySubtaskReporter {
    fn update(&self, name: Option<String>, percentage: Option<f64>) -> Result<()> {
        if *self.finished.lock().expect("progress mutex poisoned") {
            return Ok(());
        }
        self.owner
            .update_subtask(&self.subtask_id, name, percentage)
    }

    fn finish(&self) {
        let mut finished = self.finished.lock().expect("progress mutex poisoned");
        if *finished {
            return;
        }
        *finished = true;
        self.owner.finish_subtask(&self.subtask_id);
    }
}

pub trait StageLogger: Send + Sync {
    fn debug(&self, message: &str);
    fn info(&self, message: &str);
    fn warning(&self, message: &str);
    fn error(&self, message: &str);
}

#[derive(Clone, Debug)]
pub struct NoopStageLogger;

impl StageLogger for NoopStageLogger {
    fn debug(&self, _message: &str) {}
    fn info(&self, _message: &str) {}
    fn warning(&self, _message: &str) {}
    fn error(&self, _message: &str) {}
}

#[derive(Clone)]
pub struct StageExecutionContext {
    pub task_id: String,
    pub invocation_id: String,
    pub stage_id: String,
    pub cancellation: Arc<dyn CancellationToken>,
    pub progress: Arc<dyn StageProgressReporter>,
    pub logger: Arc<dyn StageLogger>,
    /// Settings resolved during planning and frozen into the invocation.
    pub settings: ResolvedSettingsView,
    pub scratch_workspace: Option<PathBuf>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ResourcePolicy {
    pub classes: BTreeSet<String>,
}

impl Default for ResourcePolicy {
    fn default() -> Self {
        Self::unbound()
    }
}

impl ResourcePolicy {
    pub fn unbound() -> Self {
        Self {
            classes: BTreeSet::from(["unbound".to_owned()]),
        }
    }

    pub fn new(classes: impl IntoIterator<Item = impl Into<String>>) -> Result<Self> {
        let classes: BTreeSet<String> = classes.into_iter().map(Into::into).collect();
        if classes.is_empty() {
            bail!("resource policy must declare at least one class");
        }
        if classes.contains("unbound") && classes.len() != 1 {
            bail!("unbound cannot be combined with constrained resources");
        }
        Ok(Self { classes })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StageConcurrencyPolicy {
    pub max_concurrent_invocations: Option<usize>,
    pub concurrency_key: Option<String>,
    pub singleton_executor_is_safe: bool,
}

impl StageConcurrencyPolicy {
    pub fn new(
        max_concurrent_invocations: Option<usize>,
        concurrency_key: Option<String>,
        singleton_executor_is_safe: bool,
    ) -> Result<Self> {
        if matches!(max_concurrent_invocations, Some(0)) {
            bail!("max_concurrent_invocations must be positive");
        }
        Ok(Self {
            max_concurrent_invocations,
            concurrency_key,
            singleton_executor_is_safe,
        })
    }
}

#[derive(Clone, Default, Debug, Eq, PartialEq)]
pub struct ResolvedSettingsView {
    values: BTreeMap<ArtifactKey, Value>,
    fingerprints: BTreeMap<ArtifactKey, String>,
}

impl ResolvedSettingsView {
    pub fn new(values: BTreeMap<ArtifactKey, Value>) -> Self {
        Self {
            values,
            fingerprints: BTreeMap::new(),
        }
    }

    pub fn new_with_fingerprints(
        values: BTreeMap<ArtifactKey, Value>,
        fingerprints: BTreeMap<ArtifactKey, String>,
    ) -> Self {
        let fingerprints = fingerprints
            .into_iter()
            .filter(|(key, _)| values.contains_key(key))
            .collect();
        Self {
            values,
            fingerprints,
        }
    }

    pub fn contains(&self, selector: &ArtifactKey) -> bool {
        self.values.contains_key(selector)
    }

    pub fn get(&self, selector: &ArtifactKey) -> Option<&Value> {
        self.values.get(selector)
    }

    pub fn require(&self, selector: &ArtifactKey) -> Result<&Value> {
        self.values
            .get(selector)
            .ok_or_else(|| anyhow!("required resolved setting is absent: {selector}"))
    }

    pub fn items(&self) -> Vec<(ArtifactKey, Value)> {
        self.values
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect()
    }

    pub fn fingerprint(&self, selector: &ArtifactKey) -> Option<&str> {
        self.fingerprints.get(selector).map(String::as_str)
    }

    pub fn fingerprint_items(&self) -> Vec<(ArtifactKey, String)> {
        self.fingerprints
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect()
    }

    pub fn fingerprints(&self) -> Result<Vec<String>> {
        let fingerprint_provider = FingerprintProvider;
        self.items()
            .into_iter()
            .map(|(key, value)| {
                Ok(self
                    .fingerprints
                    .get(&key)
                    .cloned()
                    .unwrap_or(fingerprint_provider.build_setting_value_fingerprint(&key, &value)?))
            })
            .collect()
    }

    pub fn filtered(&self, selectors: &BTreeSet<ArtifactKey>) -> Self {
        Self {
            values: self
                .values
                .iter()
                .filter(|(key, _)| selectors.contains(*key))
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect(),
            fingerprints: self
                .fingerprints
                .iter()
                .filter(|(key, _)| selectors.contains(*key))
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect(),
        }
    }

    pub fn remove(&self, selectors: &BTreeSet<ArtifactKey>) -> Self {
        let removed_bases: BTreeSet<ArtifactKey> = selectors
            .iter()
            .map(ArtifactKey::without_override)
            .collect();
        Self {
            values: self
                .values
                .iter()
                .filter(|(key, _)| !removed_bases.contains(&key.without_override()))
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect(),
            fingerprints: self
                .fingerprints
                .iter()
                .filter(|(key, _)| !removed_bases.contains(&key.without_override()))
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect(),
        }
    }

    pub fn merge(&self, overlays: &[ResolvedSettingsView]) -> Self {
        let mut merged = self.values.clone();
        let mut merged_fingerprints = self.fingerprints.clone();
        for overlay in overlays {
            for (key, value) in &overlay.values {
                merged.insert(key.clone(), value.clone());
                match overlay.fingerprints.get(key) {
                    Some(fingerprint) => {
                        merged_fingerprints.insert(key.clone(), fingerprint.clone());
                    }
                    None => {
                        merged_fingerprints.remove(key);
                    }
                }
            }
        }
        Self {
            values: merged,
            fingerprints: merged_fingerprints,
        }
    }
}

#[derive(Clone, Default, Debug, Eq, PartialEq)]
pub struct PlanningRunPolicy {
    pub requested_diagnostics: BTreeSet<ArtifactKey>,
}

impl PlanningRunPolicy {
    pub fn diagnostics_enabled(&self, selector: &ArtifactKey) -> bool {
        self.requested_diagnostics.contains(selector)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct StagePlanningContext {
    pub stage_id: String,
    pub stage_fingerprint: String,
    pub requested_outputs: BTreeSet<ArtifactKey>,
    pub resolved_settings: ResolvedSettingsView,
    pub inherited_settings: ResolvedSettingsView,
    pub run_policy: PlanningRunPolicy,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ResolvedStageRequirements {
    pub inputs: Vec<ArtifactRequirement>,
}

pub type StageExecutorFactory = Arc<dyn Fn() -> Box<dyn StageExecutor + Send> + Send + Sync>;
pub type StageRequirementResolver =
    Arc<dyn Fn(&StagePlanningContext) -> Result<ResolvedStageRequirements> + Send + Sync>;
pub type StageOutputResolver =
    Arc<dyn Fn(&StagePlanningContext) -> Result<Vec<ArtifactCapability>> + Send + Sync>;
pub type StageResourceResolver =
    Arc<dyn Fn(&StagePlanningContext) -> Result<ResourcePolicy> + Send + Sync>;

#[derive(Clone)]
pub struct StageRegistration {
    pub stage_id: String,
    pub display_name: String,
    pub registration_version: String,
    pub behavior_version: String,
    pub executor_factory: StageExecutorFactory,

    pub owned_artifacts: Vec<AnyArtifactDeclaration>,
    pub input_capabilities: Vec<ArtifactCapability>,
    pub output_capabilities: Vec<ArtifactCapability>,

    pub resolve_requirements: StageRequirementResolver,
    pub resolve_output_capabilities: StageOutputResolver,
    pub resolve_resource_policy: StageResourceResolver,

    pub runtime_settings: Vec<RuntimeStageSetting>,
    pub concurrency_policy: Option<StageConcurrencyPolicy>,
}

#[derive(Clone, Copy)]
pub struct ManifestStageRegistration<'a> {
    pub manifest: &'a StageManifest,
    pub registration: &'a StageRegistration,
}

impl<'a> ManifestStageRegistration<'a> {
    pub fn stage_id(&self) -> &str {
        &self.registration.stage_id
    }

    pub fn declared_setting_ids(&self) -> BTreeSet<ArtifactKey> {
        self.manifest.find_artifacts_by_role(
            self.registration
                .input_capabilities
                .iter()
                .map(|capability| capability.artifact_id.clone()),
            ArtifactRole::Settings,
        )
    }

    pub fn resolve_validated_requirements(
        &self,
        context: &StagePlanningContext,
    ) -> Result<ResolvedStageRequirements> {
        let result = (self.registration.resolve_requirements)(context)?;
        let mut seen: BTreeSet<(ArtifactKey, PhaseMatchMode, Vec<ArtifactKey>)> = BTreeSet::new();

        for requirement in &result.inputs {
            if !self
                .registration
                .input_capabilities
                .iter()
                .any(|capability| {
                    capability_covers_requirement(&capability.artifact_id, requirement)
                })
            {
                bail!(
                    "{} resolved input outside its static capability envelope: {}",
                    self.registration.stage_id,
                    requirement.artifact_id
                );
            }

            for pushed in &requirement.pushed_settings {
                let declaration = self.manifest.artifacts.get(pushed).ok_or_else(|| {
                    anyhow!(
                        "{} pushed an unknown settings artifact: {}",
                        self.registration.stage_id,
                        pushed
                    )
                })?;
                if declaration.role != ArtifactRole::Settings {
                    bail!(
                        "{} pushed a non-settings artifact: {}",
                        self.registration.stage_id,
                        pushed
                    );
                }
            }

            let signature = (
                requirement.artifact_id.clone(),
                requirement.phase_match,
                requirement.pushed_settings.clone(),
            );
            if !seen.insert(signature) {
                bail!(
                    "{} resolved duplicate input: {}",
                    self.registration.stage_id,
                    requirement.artifact_id
                );
            }
        }

        Ok(result)
    }

    pub fn resolve_validated_output_capabilities(
        &self,
        context: &StagePlanningContext,
    ) -> Result<Vec<ArtifactCapability>> {
        let result = (self.registration.resolve_output_capabilities)(context)?;
        let allowed: BTreeSet<ArtifactKey> = self
            .registration
            .output_capabilities
            .iter()
            .map(|capability| capability.artifact_id.clone())
            .collect();
        let mut seen = BTreeSet::new();

        for capability in &result {
            if !allowed.contains(&capability.artifact_id) {
                bail!(
                    "{} resolved output outside its static capability envelope: {}",
                    self.registration.stage_id,
                    capability.artifact_id
                );
            }
            if !seen.insert(capability.artifact_id.clone()) {
                bail!(
                    "{} resolved duplicate output: {}",
                    self.registration.stage_id,
                    capability.artifact_id
                );
            }
        }

        Ok(result)
    }

    pub fn resolve_validated_resource_policy(
        &self,
        context: &StagePlanningContext,
    ) -> Result<ResourcePolicy> {
        (self.registration.resolve_resource_policy)(context)
    }
}

pub trait PipelineFeature: Send + Sync {
    fn feature_id(&self) -> &str;
    fn configuration_fingerprint(&self) -> &str;
    fn install(
        &self,
        manifest_builder: &mut dyn StageManifestBuilderAccess,
        service_locator: &mut PipelineRuntimeServicesBuilder,
        env_settings: &mut PipelineRuntimeSettingsBuilder,
    ) -> Result<()>;
}

/// Narrow builder surface exposed to feature installers.
///
/// This avoids making `PipelineFeature` depend on the concrete builder type and
/// mirrors the Python feature protocol's deliberately loose `builder: Any`.
pub trait StageManifestBuilderAccess {
    fn add_artifact_boxed(&mut self, declaration: AnyArtifactDeclaration) -> Result<()>;
    fn add_stage_boxed(&mut self, registration: StageRegistration) -> Result<()>;
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FeatureRecord {
    pub feature_id: String,
    pub configuration_fingerprint: String,
}

pub type StageManifestError = anyhow::Error;
pub type StagePlanningError = anyhow::Error;

pub fn capability_covers_stage_requirement(
    capability: &ArtifactCapability,
    requirement: &ArtifactRequirement,
) -> bool {
    capability_covers_requirement(&capability.artifact_id, requirement)
}

pub fn build_stage_logger(
    _task_id: &str,
    _invocation_id: &str,
    _stage_id: &str,
) -> Arc<dyn StageLogger> {
    Arc::new(NoopStageLogger)
}
