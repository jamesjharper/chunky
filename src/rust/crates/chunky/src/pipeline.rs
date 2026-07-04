//! Pipeline bootstrap facade.
//!
//! This layer owns a frozen stage manifest, binds caller-provided artifacts,
//! asks the planner for an execution graph, and delegates execution to the
//! example task runner. It is deliberately small: durable background jobs,
//! rich request conversion, and production-grade default-setting resolution are
//! outside this example crate.

use anyhow::{bail, Result};
use std::collections::{BTreeMap, BTreeSet};
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use tokio::sync::watch;
use uuid::Uuid;

use crate::artifact_binding_models::AnyArtifactInstance;
use crate::artifact_bindings::ArtifactBindingsProvider;
use crate::artifact_handles::{ArtifactSourceHandle, ArtifactTargetHandle};
use crate::artifact_models::{AnyArtifactDeclaration, ArtifactKey, ArtifactRole};
use crate::cache_models::ArtifactCache;
use crate::execution_planner::{TaskRunGraphBuilder, TaskRunPlanner};
use crate::fingerprints::FingerprintProvider;
use crate::progress_models::{ProgressDispatcher, RunProgressTracker};
use crate::stage_manifest::{StageManifest, StageManifestBuilder};
use crate::stage_models::{CancellationSource, PipelineFeature, PlanningRunPolicy};
use crate::task_control::{RunError, RunHandle, RunResult};
use crate::task_runner::{ExampleTaskRunner, TaskRunGraph, TaskRunResult};

#[derive(Clone, Default)]
pub struct RunRequest {
    pub inputs: BTreeMap<ArtifactKey, Arc<dyn ArtifactSourceHandle>>,
    pub outputs: BTreeMap<ArtifactKey, Arc<dyn ArtifactTargetHandle>>,
    pub run_policy: Option<PlanningRunPolicy>,
}

impl RunRequest {
    pub fn new(
        inputs: BTreeMap<ArtifactKey, Arc<dyn ArtifactSourceHandle>>,
        outputs: BTreeMap<ArtifactKey, Arc<dyn ArtifactTargetHandle>>,
    ) -> Self {
        Self {
            inputs,
            outputs,
            run_policy: None,
        }
    }

    pub fn with_policy(mut self, policy: PlanningRunPolicy) -> Self {
        self.run_policy = Some(policy);
        self
    }
}

pub struct ChunkyPipelineBuilder {
    artifact_cache: Arc<dyn ArtifactCache>,
    manifest_builder: StageManifestBuilder,
}

impl ChunkyPipelineBuilder {
    pub fn new(artifact_cache: Arc<dyn ArtifactCache>) -> Self {
        Self {
            artifact_cache,
            manifest_builder: StageManifestBuilder::new(),
        }
    }

    pub fn install(mut self, feature: &dyn PipelineFeature) -> Result<Self> {
        self.manifest_builder.install_feature(feature)?;
        Ok(self)
    }

    pub fn build(mut self) -> Result<ChunkyPipeline> {
        Ok(ChunkyPipeline {
            stage_manifest: self.manifest_builder.freeze()?,
            artifact_cache: self.artifact_cache,
        })
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

        let bindings = ArtifactBindingsProvider::new(self.artifact_cache.clone());
        let (supplied, user_supplied_settings) =
            self.bind_supplied_inputs(&bindings, &request).await?;
        let supplied = self.bind_default_settings(&bindings, supplied).await?;
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
            )
            .await?;

        self.register_plan_progress(&progress_tracker, &plan);
        let runner = ExampleTaskRunner::new(self.stage_manifest.clone(), bindings);
        runner
            .run(
                &plan,
                supplied,
                request.outputs,
                Some(cancellation),
                progress_tracker,
                Some(task_id),
            )
            .await
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

        for (artifact_id, source) in &request.inputs {
            let declaration = self.stage_manifest.find_artifact(artifact_id)?;
            if declaration.role == ArtifactRole::Settings {
                user_supplied_settings.insert(artifact_id.clone());
            }
            let source = self
                .resolve_settings_source(&declaration, Some(source.clone()))
                .await?;
            let instance = bindings.bind_supplied_any(&declaration, source).await?;
            supplied.insert(artifact_id.clone(), instance);
        }

        Ok((supplied, user_supplied_settings))
    }

    async fn bind_default_settings(
        &self,
        bindings: &ArtifactBindingsProvider,
        mut supplied: BTreeMap<ArtifactKey, AnyArtifactInstance>,
    ) -> Result<BTreeMap<ArtifactKey, AnyArtifactInstance>> {
        for (artifact_id, declaration) in &self.stage_manifest.artifacts {
            if declaration.role == ArtifactRole::Settings
                && declaration.default_value_source_provider.is_some()
                && !supplied.contains_key(artifact_id)
            {
                let source = self.resolve_settings_source(declaration, None).await?;
                let instance = bindings.bind_supplied_any(declaration, source).await?;
                supplied.insert(artifact_id.clone(), instance);
            }
        }
        Ok(supplied)
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
                    invocation.registration.stage_id(),
                    &invocation.registration.registration.behavior_version,
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
