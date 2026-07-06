//! Example task runner and execution-graph orchestration.
//!
//! The Python source imports `TaskRunGraph` from a planning/execution
//! package that is not present in the supplied scheduling folder. This file
//! keeps that boundary as a compact local shape instead of generating a full
//! planner, scheduler, or execution-manifest implementation.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::{anyhow, bail, Result};
use uuid::Uuid;

use crate::artifact_binding_models::{
    AnyArtifactInstance, AnyArtifactWriter, InputArtifacts, OutputArtifacts,
    ResolvedStageOutputBindings, StageOutputResolution,
};
use crate::artifact_bindings::ArtifactBindingsProvider;
use crate::artifact_handles::ArtifactTargetHandle;
use crate::artifact_models::{AnyArtifactDeclaration, ArtifactKey};
use crate::stage_manifest::StageManifest;
use crate::stage_models::{
    build_stage_logger, CancellationSource, CancellationToken, InMemoryStageProgressReporter,
    StageExecutionContext, StageProgressReporter as StageScopedProgressReporter,
};
use crate::task_request_graph::{RunnableInvocation, TaskOutputBinding};

use crate::progress_models::{RunProgressTracker, StageBlockedReason, TaskState};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum InvocationDisposition {
    Executed,
    NoOperationCacheHit,
}

#[derive(Clone)]
pub struct InvocationRunResult {
    pub instances: Vec<AnyArtifactInstance>,
    pub disposition: InvocationDisposition,
    pub waited_for_writer: bool,
}

#[derive(Clone, Debug)]
pub struct TaskRunResult {
    pub task_id: String,
    pub output_fingerprints: BTreeMap<ArtifactKey, String>,
    pub events: Vec<String>,
}

/// Minimal execution-plan seam consumed by the scheduling example.
///
/// The real task-execution graph should be produced by planning. Scheduling
/// reads it as immutable work; it must not mutate graph shape while running.
pub struct TaskRunGraph<'a> {
    pub manifest_id: String,
    pub invocations: Vec<RunnableInvocation<'a>>,
    pub dependencies_by_invocation: BTreeMap<String, Vec<String>>,
    pub dependents_by_invocation: BTreeMap<String, Vec<String>>,
    pub requested_output_bindings: Vec<TaskOutputBinding>,
}

pub struct ArtifactRunState {
    instances: HashMap<String, AnyArtifactInstance>,
}

impl ArtifactRunState {
    pub fn new(supplied: BTreeMap<ArtifactKey, AnyArtifactInstance>) -> Self {
        let instances = supplied
            .into_iter()
            .map(|(artifact_id, instance)| (artifact_id.to_string(), instance))
            .collect();
        Self { instances }
    }

    pub fn provide(&mut self, instance_id: impl Into<String>, instance: AnyArtifactInstance) {
        self.instances.insert(instance_id.into(), instance);
    }

    pub fn resolve_inputs(
        &self,
        invocation: &RunnableInvocation<'_>,
    ) -> Result<(Vec<AnyArtifactInstance>, HashMap<ArtifactKey, ArtifactKey>)> {
        let mut result = Vec::new();
        let mut aliases = HashMap::new();
        let mut seen = HashSet::new();

        for binding in &invocation.input_bindings {
            let instance = self
                .instances
                .get(&binding.resolved_instance_id)
                .cloned()
                .ok_or_else(|| {
                    anyhow!(
                        "planned artifact is not available for {}: {}",
                        invocation.invocation_id,
                        binding.resolved_artifact_id
                    )
                })?;
            aliases.insert(
                binding.selector.clone(),
                binding.resolved_artifact_id.clone(),
            );
            if seen.insert(binding.resolved_artifact_id.clone()) {
                result.push(instance);
            }
        }

        Ok((result, aliases))
    }

    pub fn require(&self, instance_id: &str) -> Result<AnyArtifactInstance> {
        self.instances
            .get(instance_id)
            .cloned()
            .ok_or_else(|| anyhow!("artifact instance is not available: {instance_id}"))
    }

    pub fn all_instances(&self) -> Vec<AnyArtifactInstance> {
        self.instances.values().cloned().collect()
    }
}

pub struct ExampleTaskRunner {
    manifest: StageManifest,
    artifact_bindings_provider: ArtifactBindingsProvider,
}

impl ExampleTaskRunner {
    pub fn new(
        manifest: StageManifest,
        artifact_bindings_provider: ArtifactBindingsProvider,
    ) -> Self {
        Self {
            manifest,
            artifact_bindings_provider,
        }
    }

    pub async fn run(
        &self,
        plan: &TaskRunGraph<'_>,
        supplied_instances: BTreeMap<ArtifactKey, AnyArtifactInstance>,
        output_targets: BTreeMap<ArtifactKey, Arc<dyn ArtifactTargetHandle>>,
        cancellation: Option<CancellationSource>,
        progress_tracker: RunProgressTracker,
        task_id: Option<String>,
    ) -> Result<TaskRunResult> {
        if plan.manifest_id != self.manifest.manifest_id {
            bail!("task plan was created from a different stage manifest");
        }

        let cancellation = cancellation.unwrap_or_default();
        let task_id = task_id
            .unwrap_or_else(|| format!("task-{}", &Uuid::new_v4().simple().to_string()[..12]));
        let mut state = ArtifactRunState::new(supplied_instances);
        let mut events = Vec::new();

        let result = async {
            progress_tracker.set_task_state(TaskState::Running);

            for invocation in &plan.invocations {
                let run = self
                    .run_invocation(
                        &task_id,
                        &state,
                        invocation,
                        &cancellation,
                        &progress_tracker,
                    )
                    .await?;

                let bindings_by_artifact = invocation
                    .requested_output_bindings
                    .iter()
                    .map(|binding| (binding.resolved_artifact_id.clone(), binding))
                    .collect::<HashMap<_, _>>();

                for instance in &run.instances {
                    let artifact_id = instance.artifact_id().clone();
                    let binding = bindings_by_artifact.get(&artifact_id).ok_or_else(|| {
                        anyhow!("runner received an unplanned output instance: {artifact_id}")
                    })?;
                    state.provide(binding.instance_id.clone(), instance.clone());
                }

                if run.waited_for_writer {
                    events.push(format!(
                        "{} waited for an in-flight cache writer",
                        invocation.registration.stage_id()
                    ));
                }
                match run.disposition {
                    InvocationDisposition::NoOperationCacheHit => events.push(format!(
                        "{} skipped: cache hit",
                        invocation.registration.stage_id()
                    )),
                    InvocationDisposition::Executed => {
                        events.push(format!("{} executed", invocation.registration.stage_id()))
                    }
                }
                for binding in &invocation.requested_output_bindings {
                    if binding.terminal_remapped() {
                        events.push(format!(
                            "terminal remap {} -> {}",
                            binding.declared_artifact_id, binding.resolved_artifact_id
                        ));
                    }
                }
            }

            let mut output_fingerprints = BTreeMap::new();
            for requested in &plan.requested_output_bindings {
                cancellation.raise_if_cancelled()?;
                let instance = state.require(&requested.instance_id)?;
                let target = output_targets
                    .get(&requested.requested_artifact_id)
                    .cloned()
                    .ok_or_else(|| {
                        anyhow!(
                            "no caller materialization target supplied for {}",
                            requested.requested_artifact_id
                        )
                    })?;
                self.artifact_bindings_provider
                    .materialize(&instance, target)
                    .await?;
                output_fingerprints.insert(
                    requested.requested_artifact_id.clone(),
                    instance.fingerprint().to_owned(),
                );
                events.push(format!("materialized {}", requested.requested_artifact_id));
            }

            progress_tracker.set_task_state(TaskState::Completed);
            Ok(TaskRunResult {
                task_id: task_id.clone(),
                output_fingerprints,
                events,
            })
        }
        .await;

        if result.is_err() {
            let terminal = if cancellation.is_cancelled() {
                TaskState::Cancelled
            } else {
                TaskState::Failed
            };
            progress_tracker.set_task_state(terminal);
        }

        close_instances(&state.all_instances()).await;
        result
    }

    async fn run_invocation(
        &self,
        task_id: &str,
        state: &ArtifactRunState,
        invocation: &RunnableInvocation<'_>,
        cancellation: &CancellationSource,
        progress_tracker: &RunProgressTracker,
    ) -> Result<InvocationRunResult> {
        progress_tracker.mark_running(&invocation.invocation_id);
        let (input_instances, input_aliases) = state.resolve_inputs(invocation)?;
        let pedigree_fingerprint = &invocation.pedigree_fingerprint;

        let output_declarations = invocation
            .requested_output_bindings
            .iter()
            .map(|binding| {
                self.manifest.find_output_declaration(
                    &binding.declared_artifact_id,
                    &binding.resolved_artifact_id,
                )
            })
            .collect::<Result<Vec<AnyArtifactDeclaration>>>()?;

        let mut waited_for_writer = false;
        let resolution = loop {
            cancellation.raise_if_cancelled()?;
            match self
                .artifact_bindings_provider
                .bind_stage_outputs(&output_declarations, pedigree_fingerprint)
                .await?
            {
                StageOutputResolution::Wait(wait) => {
                    waited_for_writer = true;
                    progress_tracker.mark_blocked(
                        &invocation.invocation_id,
                        StageBlockedReason::WaitingForOtherTask,
                        format!(
                            "waiting for another task to release cache write reservation for {}",
                            wait.artifact_id
                        ),
                        wait.cache_wait.owner_task_id.clone(),
                    );
                    cancellation
                        .wait_for(wait.cache_wait.wait_handle.wait())
                        .await?;
                    progress_tracker.clear_blocked(&invocation.invocation_id);
                }
                StageOutputResolution::Resolved(resolution) => break resolution,
            }
        };

        validate_output_fingerprints(invocation, &resolution)?;

        if resolution.writers.is_empty() {
            progress_tracker.mark_finished(&invocation.invocation_id);
            return Ok(InvocationRunResult {
                instances: resolution.available,
                disposition: InvocationDisposition::NoOperationCacheHit,
                waited_for_writer,
            });
        }

        let output_aliases = invocation
            .requested_output_bindings
            .iter()
            .map(|binding| {
                (
                    binding.declared_artifact_id.clone(),
                    binding.resolved_artifact_id.clone(),
                )
            })
            .collect::<HashMap<_, _>>();
        let outputs =
            OutputArtifacts::from_writers_with_aliases(&resolution.writers, output_aliases);
        let inputs = InputArtifacts::from_instances_with_aliases(&input_instances, input_aliases)?;
        let progress = InMemoryStageProgressReporter::default();
        let workspace = make_scratch_workspace(invocation.registration.stage_id())?;

        let context = StageExecutionContext {
            task_id: task_id.to_owned(),
            invocation_id: invocation.invocation_id.clone(),
            stage_id: invocation.registration.stage_id().to_owned(),
            cancellation: cancellation.token(),
            progress: Arc::new(progress.clone()) as Arc<dyn StageScopedProgressReporter>,
            logger: build_stage_logger(
                task_id,
                &invocation.invocation_id,
                invocation.registration.stage_id(),
            ),
            settings: invocation.resolved_settings.clone(),
            scratch_workspace: Some(workspace.clone()),
        };

        let mut executor = (invocation.registration.registration.executor_factory)();
        let produced = match executor.execute(&inputs, &outputs, &context).await {
            Ok(()) => outputs.produced_instances().await,
            Err(error) => Err(error),
        };
        progress.clear();
        let _ = std::fs::remove_dir_all(&workspace);

        let produced = match produced {
            Ok(produced) => produced,
            Err(error) => {
                abort_writers(&resolution.writers).await;
                if cancellation.is_cancelled() {
                    progress_tracker.mark_cancelled(&invocation.invocation_id);
                } else {
                    progress_tracker.mark_failed(&invocation.invocation_id);
                }
                return Err(error);
            }
        };

        let instances = ArtifactBindingsProvider::merge_stage_output_instances(
            &resolution.available,
            &produced,
        );
        progress_tracker.mark_finished(&invocation.invocation_id);
        Ok(InvocationRunResult {
            instances,
            disposition: InvocationDisposition::Executed,
            waited_for_writer,
        })
    }
}

fn validate_output_fingerprints(
    invocation: &RunnableInvocation<'_>,
    resolution: &ResolvedStageOutputBindings,
) -> Result<()> {
    let expected = invocation
        .requested_output_bindings
        .iter()
        .filter_map(|binding| {
            binding
                .expected_fingerprint
                .as_ref()
                .map(|fingerprint| (binding.resolved_artifact_id.clone(), fingerprint.clone()))
        })
        .collect::<HashMap<_, _>>();

    for instance in &resolution.available {
        if let Some(planned) = expected.get(instance.artifact_id()) {
            if instance.fingerprint() != planned {
                bail!(
                    "cache binding fingerprint disagrees with execution plan for {}",
                    instance.artifact_id()
                );
            }
        }
    }

    for writer in &resolution.writers {
        if let Some(planned) = expected.get(writer.artifact_id()) {
            if writer.handle.fingerprint != *planned {
                bail!(
                    "writer fingerprint disagrees with execution plan for {}",
                    writer.artifact_id()
                );
            }
        }
    }

    Ok(())
}

async fn abort_writers(writers: &[AnyArtifactWriter]) {
    for writer in writers.iter().rev() {
        writer.abort_if_unwritten().await;
    }
}

async fn close_instances(instances: &[AnyArtifactInstance]) {
    for instance in instances.iter().rev() {
        if let Some(source) = instance.handle.source.as_ref() {
            let _ = source.close().await;
        }
    }
}

fn make_scratch_workspace(stage_id: &str) -> Result<PathBuf> {
    let stage_part: String = stage_id
        .chars()
        .map(|ch| {
            if matches!(ch, '.' | ':' | '/') {
                '-'
            } else {
                ch
            }
        })
        .collect();
    let workspace =
        std::env::temp_dir().join(format!("chunky-{stage_part}-{}", Uuid::new_v4().simple()));
    std::fs::create_dir_all(&workspace)?;
    Ok(workspace)
}
