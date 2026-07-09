//! Pipeline bootstrap facade.
//!
//! This layer owns a frozen stage manifest, binds caller-provided artifacts,
//! asks the planner for an execution graph, and delegates execution to the
//! example task runner. It is deliberately small: durable background jobs,
//! rich request conversion, and production-grade default-setting resolution are
//! outside this example crate.

use anyhow::{bail, Result};
use async_trait::async_trait;
use std::collections::{BTreeMap, BTreeSet};
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use tokio::sync::watch;
use uuid::Uuid;

use crate::artifact_binding_models::AnyArtifactInstance;
use crate::artifact_bindings::ArtifactBindingsProvider;
use crate::artifact_handles::{ArtifactSourceHandle, ArtifactTargetHandle};
use crate::artifact_models::{AnyArtifactDeclaration, ArtifactKey, ArtifactRole, CachedArtifactReference};
use crate::cache_models::ArtifactCache;
use crate::execution_planner::{DefaultSettingsResolver, TaskRunGraphBuilder, TaskRunPlanner};
use crate::fingerprints::FingerprintProvider;
use crate::progress_models::{ProgressDispatcher, RunProgressTracker};
use crate::stage_manifest::{StageManifest, StageManifestBuilder};
use crate::stage_models::{CancellationSource, PipelineFeature, PlanningRunPolicy};
use crate::task_control::{RunError, RunHandle, RunResult};
use crate::task_runner::{ExampleTaskRunner, TaskRunGraph, TaskRunResult};

#[derive(Clone, Debug, Default)]
pub struct ArtifactCachePolicy {
    pub ignore_existing_cache: bool,
    pub do_not_cache: bool,
    pub desired_ttl_seconds: Option<u64>,
}

#[derive(Clone)]
pub enum ProvidedArtifactRoot {
    Source(Arc<dyn ArtifactSourceHandle>),
    Cached(CachedArtifactReference),
}

impl From<Arc<dyn ArtifactSourceHandle>> for ProvidedArtifactRoot {
    fn from(value: Arc<dyn ArtifactSourceHandle>) -> Self {
        Self::Source(value)
    }
}

impl From<CachedArtifactReference> for ProvidedArtifactRoot {
    fn from(value: CachedArtifactReference) -> Self {
        Self::Cached(value)
    }
}

#[derive(Clone, Default)]
pub struct RunRequest {
    pub inputs: BTreeMap<ArtifactKey, ProvidedArtifactRoot>,
    pub outputs: BTreeMap<ArtifactKey, Vec<Arc<dyn ArtifactTargetHandle>>>,
    pub run_policy: Option<PlanningRunPolicy>,
    pub cache_policy: BTreeMap<ArtifactKey, ArtifactCachePolicy>,
}

impl RunRequest {
    pub fn new(
        inputs: BTreeMap<ArtifactKey, Arc<dyn ArtifactSourceHandle>>,
        outputs: BTreeMap<ArtifactKey, Arc<dyn ArtifactTargetHandle>>,
    ) -> Self {
        Self {
            inputs: inputs
                .into_iter()
                .map(|(key, source)| (key, ProvidedArtifactRoot::Source(source)))
                .collect(),
            outputs: outputs.into_iter().map(|(key, target)| (key, vec![target])).collect(),
            run_policy: None,
            cache_policy: BTreeMap::new(),
        }
    }

    pub fn new_with_output_fanout(
        inputs: BTreeMap<ArtifactKey, Arc<dyn ArtifactSourceHandle>>,
        outputs: BTreeMap<ArtifactKey, Vec<Arc<dyn ArtifactTargetHandle>>>,
    ) -> Self {
        Self {
            inputs: inputs
                .into_iter()
                .map(|(key, source)| (key, ProvidedArtifactRoot::Source(source)))
                .collect(),
            outputs,
            run_policy: None,
            cache_policy: BTreeMap::new(),
        }
    }

    pub fn with_policy(mut self, policy: PlanningRunPolicy) -> Self {
        self.run_policy = Some(policy);
        self
    }

    pub fn with_cache_policy(mut self, cache_policy: BTreeMap<ArtifactKey, ArtifactCachePolicy>) -> Self {
        self.cache_policy = cache_policy;
        self
    }
}

#[derive(Clone)]
pub struct ArtifactCacheIO {
    pipeline: ChunkyPipeline,
}

impl ArtifactCacheIO {
    pub async fn publish(
        &self,
        artifact_id: &ArtifactKey,
        source: Arc<dyn ArtifactSourceHandle>,
    ) -> Result<CachedArtifactReference> {
        let declaration = self.pipeline.stage_manifest.find_artifact(artifact_id)?;
        let bindings = ArtifactBindingsProvider::new(self.pipeline.artifact_cache.clone());
        let source = self
            .pipeline
            .resolve_settings_source(&declaration, Some(source))
            .await?;
        bindings.publish_supplied_root(&declaration, source, None).await
    }

    pub async fn open(
        &self,
        reference: &CachedArtifactReference,
    ) -> Result<AnyArtifactInstance> {
        let declaration = self.pipeline.stage_manifest.find_artifact(&reference.artifact_id)?;
        let bindings = ArtifactBindingsProvider::new(self.pipeline.artifact_cache.clone());
        bindings.open_cached_reference(&declaration, reference).await
    }
}

pub struct ChunkyPipelineBuilder {
    artifact_cache: Arc<dyn ArtifactCache>,
    manifest_builder: StageManifestBuilder,
    built: bool,
}

impl ChunkyPipelineBuilder {
    pub fn new(artifact_cache: Arc<dyn ArtifactCache>) -> Self {
        Self {
            artifact_cache,
            manifest_builder: StageManifestBuilder::new(),
            built: false,
        }
    }

    pub fn install(mut self, feature: &dyn PipelineFeature) -> Result<Self> {
        self.assert_not_built()?;
        self.manifest_builder.install_feature(feature)?;
        Ok(self)
    }

    pub fn build(mut self) -> Result<ChunkyPipeline> {
        self.assert_not_built()?;
        let stage_manifest = self.manifest_builder.freeze()?;
        self.built = true;
        Ok(ChunkyPipeline {
            stage_manifest,
            artifact_cache: self.artifact_cache,
        })
    }

    fn assert_not_built(&self) -> Result<()> {
        if self.built {
            bail!("pipeline builder is single-use and has already built");
        }
        Ok(())
    }
}

#[derive(Clone)]
pub struct ChunkyPipeline {
    pub stage_manifest: StageManifest,
    artifact_cache: Arc<dyn ArtifactCache>,
}

impl ChunkyPipeline {
    pub fn builder(artifact_cache: Arc<dyn ArtifactCache>) -> ChunkyPipelineBuilder {
        ChunkyPipelineBuilder::new(artifact_cache)
    }

    pub fn cache_io(&self) -> ArtifactCacheIO {
        ArtifactCacheIO { pipeline: self.clone() }
    }

    pub async fn run(&self, request: RunRequest) -> Result<TaskRunResult> {
        let task_id = format!("task-{}", &Uuid::new_v4().simple().to_string()[..12]);
        let dispatcher = ProgressDispatcher::null();
        let tracker = RunProgressTracker::new(task_id.clone(), dispatcher);
        self.execute(request, task_id, tracker, CancellationSource::default())
            .await
    }

    pub fn run_away(&self, request: RunRequest) -> RunHandle<TaskRunResult> {
        let task_id = format!("task-{}", &Uuid::new_v4().simple().to_string()[..12]);
        let cancellation = crate::stage_models::CancellationSource::default();
        let dispatcher = ProgressDispatcher::new();
        let tracker = RunProgressTracker::new(task_id.clone(), dispatcher.clone());
        let (sender, receiver) = watch::channel::<Option<Arc<RunResult<TaskRunResult>>>>(None);
        let pipeline = self.clone();
        let task_id_for_task = task_id.clone();
        let tracker_for_task = tracker.clone();
        let cancellation_for_task = cancellation.clone();
        let cancellation_for_result = cancellation_for_task.clone();

        tokio::spawn(async move {
            let result = match pipeline
                .execute(
                    request,
                    task_id_for_task.clone(),
                    tracker_for_task,
                    cancellation_for_task,
                )
                .await
            {
                Ok(output) => RunResult::completed(task_id_for_task, output),
                Err(_) if cancellation_for_result.token().is_cancelled() => {
                    RunResult::cancelled(task_id_for_task)
                }
                Err(error) => RunResult::failed(
                    task_id_for_task,
                    vec![RunError {
                        code: "run_failed".to_owned(),
                        message: error.to_string(),
                        stage_id: None,
                    }],
                ),
            };
            let _ = sender.send(Some(Arc::new(result)));
        });

        RunHandle::new(task_id, cancellation, tracker, dispatcher, receiver)
    }

    async fn execute(
        &self,
        request: RunRequest,
        task_id: String,
        progress_tracker: RunProgressTracker,
        cancellation: CancellationSource,
    ) -> Result<TaskRunResult> {
        if request.outputs.is_empty() {
            bail!("run request must contain at least one output");
        }

        self.validate_cached_inputs(&request.inputs)?;
        let bindings = ArtifactBindingsProvider::new(self.artifact_cache.clone());
        let (supplied, user_supplied_settings) =
            self.bind_supplied_inputs(&bindings, &request).await?;
        self.validate_cache_policy(&request.cache_policy)?;
        let requested_outputs = request.outputs.keys().cloned().collect::<BTreeSet<_>>();

        let execution_graph_builder = ExampleTaskRunGraphBuilder {
            fingerprint_provider: bindings.fingerprint_provider().clone(),
        };
        let planner = TaskRunPlanner::new(
            &self.stage_manifest,
            Some(bindings.fingerprint_provider().clone()),
            execution_graph_builder,
        );
        let plan = planner
            .plan(
                supplied.clone(),
                requested_outputs,
                request.run_policy,
                user_supplied_settings,
                Some(Arc::new(PipelineDefaultSettingsResolver {
                    manifest: self.stage_manifest.clone(),
                    bindings: bindings.clone(),
                })),
            )
            .await?;

        self.register_plan_progress(&progress_tracker, &plan);
        let runner = ExampleTaskRunner::new(self.stage_manifest.clone(), bindings);
        runner
            .run(
                &plan,
                supplied,
                request.outputs,
                request.cache_policy,
                Some(cancellation),
                progress_tracker,
                Some(task_id),
            )
            .await
    }

    fn validate_cached_inputs(&self, inputs: &BTreeMap<ArtifactKey, ProvidedArtifactRoot>) -> Result<()> {
        for (artifact_id, root) in inputs {
            self.stage_manifest.find_artifact(artifact_id)?;
            if let ProvidedArtifactRoot::Cached(reference) = root {
                if reference.artifact_id != *artifact_id {
                    bail!(
                        "cached artifact reference artifact_id must match request key: {} != {}",
                        reference.artifact_id,
                        artifact_id
                    );
                }
            }
        }
        Ok(())
    }

    fn validate_cache_policy(&self, cache_policy: &BTreeMap<ArtifactKey, ArtifactCachePolicy>) -> Result<()> {
        for artifact_id in cache_policy.keys() {
            self.stage_manifest.find_artifact(artifact_id)?;
        }
        Ok(())
    }

    async fn bind_supplied_inputs(
        &self,
        bindings: &ArtifactBindingsProvider,
        request: &RunRequest,
    ) -> Result<(
        BTreeMap<ArtifactKey, AnyArtifactInstance>,
        BTreeSet<ArtifactKey>,
    )> {
        let mut supplied = BTreeMap::new();
        let mut user_supplied_settings = BTreeSet::new();

        for (artifact_id, root) in &request.inputs {
            let declaration = self.stage_manifest.find_artifact(artifact_id)?;
            if declaration.role == ArtifactRole::Settings {
                user_supplied_settings.insert(artifact_id.clone());
            }
            let instance = match root {
                ProvidedArtifactRoot::Source(source) => {
                    let source = self
                        .resolve_settings_source(&declaration, Some(source.clone()))
                        .await?;
                    bindings
                        .bind_supplied_any_with_cache_policy(
                            &declaration,
                            source,
                            request.cache_policy.get(artifact_id),
                        )
                        .await?
                }
                ProvidedArtifactRoot::Cached(reference) => {
                    bindings.open_cached_reference(&declaration, reference).await?
                }
            };
            supplied.insert(artifact_id.clone(), instance);
        }

        Ok((supplied, user_supplied_settings))
    }

    async fn resolve_settings_source(
        &self,
        declaration: &AnyArtifactDeclaration,
        source: Option<Arc<dyn ArtifactSourceHandle>>,
    ) -> Result<Arc<dyn ArtifactSourceHandle>> {
        if declaration.role != ArtifactRole::Settings {
            return source.ok_or_else(|| {
                anyhow::anyhow!(
                    "missing source for artifact without defaults: {}",
                    declaration.artifact_id
                )
            });
        }

        match &declaration.default_value_source_provider {
            Some(provider) => provider.resolve_source_any(source, declaration).await,
            None => source.ok_or_else(|| {
                anyhow::anyhow!(
                    "missing source for artifact without defaults: {}",
                    declaration.artifact_id
                )
            }),
        }
    }

    fn register_plan_progress(
        &self,
        progress_tracker: &RunProgressTracker,
        plan: &TaskRunGraph<'_>,
    ) {
        progress_tracker.batch_update(|| {
            for invocation in &plan.invocations {
                progress_tracker.register_stage(
                    invocation.invocation_id.clone(),
                    invocation.registration.stage_id().to_owned(),
                    invocation.registration.registration.display_name.clone(),
                );
            }
        });
    }
}

struct PipelineDefaultSettingsResolver {
    manifest: StageManifest,
    bindings: ArtifactBindingsProvider,
}

#[async_trait]
impl DefaultSettingsResolver for PipelineDefaultSettingsResolver {
    async fn resolve_defaults(
        &self,
        supplied_instances: &BTreeMap<ArtifactKey, AnyArtifactInstance>,
    ) -> Result<BTreeMap<ArtifactKey, AnyArtifactInstance>> {
        let mut resolved = BTreeMap::new();
        for (artifact_id, declaration) in &self.manifest.artifacts {
            if declaration.role == ArtifactRole::Settings
                && declaration.default_value_source_provider.is_some()
                && !supplied_instances.contains_key(artifact_id)
            {
                let source = match &declaration.default_value_source_provider {
                    Some(provider) => provider.resolve_source_any(None, declaration).await?,
                    None => continue,
                };
                let instance = self.bindings.bind_supplied_any(declaration, source).await?;
                resolved.insert(artifact_id.clone(), instance);
            }
        }
        Ok(resolved)
    }
}

#[derive(Clone)]
struct ExampleTaskRunGraphBuilder {
    fingerprint_provider: FingerprintProvider,
}

impl<'a> TaskRunGraphBuilder<'a, TaskRunGraph<'a>> for ExampleTaskRunGraphBuilder {
    fn build(
        self,
        task_request_graph: crate::task_request_graph::TaskRequestGraph<'a>,
        supplied_instances: BTreeMap<ArtifactKey, AnyArtifactInstance>,
    ) -> Pin<Box<dyn Future<Output = Result<TaskRunGraph<'a>>> + Send + 'a>> {
        let fingerprint_provider = self.fingerprint_provider.clone();
        Box::pin(async move {
            let mut fingerprints_by_instance = supplied_instances
                .iter()
                .map(|(artifact_id, instance)| {
                    (artifact_id.to_string(), instance.fingerprint().to_owned())
                })
                .collect::<BTreeMap<_, _>>();

            let mut invocations = Vec::new();
            for invocation in task_request_graph.invocations {
                let mut input_fingerprints = BTreeMap::new();
                for binding in invocation.input_bindings.iter() {
                    if let Some(fingerprint) =
                        fingerprints_by_instance.get(&binding.resolved_instance_id)
                    {
                        input_fingerprints
                            .insert(binding.selector.to_string(), fingerprint.clone());
                    }
                }
                let pedigree = fingerprint_provider.build_stage_pedigree_fingerprint(
                    &invocation.stage_fingerprint,
                    &input_fingerprints,
                    &invocation.effective_settings_fingerprint,
                )?;

                let mut requested_output_bindings = Vec::new();
                for binding in &invocation.requested_output_bindings {
                    let mut binding = binding.clone();
                    let declaration = invocation.registration.manifest.find_output_declaration(
                        &binding.declared_artifact_id,
                        &binding.resolved_artifact_id,
                    )?;
                    let expected = fingerprint_provider
                        .build_intermediate_artifact_fingerprint(&declaration, &pedigree)
                        .await?;
                    binding.expected_fingerprint = Some(expected.clone());
                    fingerprints_by_instance.insert(binding.instance_id.clone(), expected);
                    requested_output_bindings.push(binding);
                }
                invocations.push(crate::task_request_graph::RunnableInvocation {
                    invocation_id: invocation.invocation_id,
                    registration: invocation.registration,
                    input_bindings: invocation.input_bindings,
                    requested_output_bindings,
                    resolved_resource_policy: invocation.resolved_resource_policy,
                    pedigree_fingerprint: pedigree,
                    resolved_settings: invocation.resolved_settings,
                });
            }

            Ok(TaskRunGraph {
                manifest_id: task_request_graph.manifest_id,
                invocations,
                dependencies_by_invocation: task_request_graph.dependencies_by_invocation,
                dependents_by_invocation: task_request_graph.dependents_by_invocation,
                requested_output_bindings: task_request_graph
                    .requested_output_bindings
                    .into_iter()
                    .map(|binding| crate::task_request_graph::TaskOutputBinding {
                        requested_artifact_id: binding.requested_artifact_id,
                        instance_id: binding.instance_id,
                    })
                    .collect(),
            })
        })
    }
}

pub fn chunky_run_away(request: RunRequest, pipeline: &ChunkyPipeline) -> RunHandle<TaskRunResult> {
    pipeline.run_away(request)
}

pub async fn chunky_run(request: RunRequest, pipeline: &ChunkyPipeline) -> Result<TaskRunResult> {
    pipeline.run(request).await
}
