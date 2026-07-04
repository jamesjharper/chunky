//! Request-specific graph creation over a frozen stage manifest.
//!
//! `TaskRunPlanner` is intentionally small: it orchestrates request settings,
//! manifest graph capture, request graph expansion, and then hands off to the
//! task-execution graph builder.

use anyhow::{bail, Result};
use std::collections::{BTreeMap, BTreeSet};

use crate::artifact_binding_models::AnyArtifactInstance;
use crate::artifact_models::ArtifactKey;
use crate::fingerprints::FingerprintProvider;
use crate::stage_manifest::StageManifest;
use crate::stage_models::PlanningRunPolicy;

use super::manifest_graph::StageManifestGraphBuilder;
use super::task_request_graph::{
    PlannedInputBinding, PlannedInvocation, PlannedOutputBinding, PlannerRequestSettings,
    PlannerSettingsResolver, ProducerSelector, RequestedOutputBinding, TaskRequestContext,
    TaskRequestGraph, TaskRequestGraphBuilder,
};

/// External seam for producing the execution graph from a request graph.
pub trait TaskRunGraphBuilder<'a, G>: Clone {
    fn build(
        self,
        task_request_graph: TaskRequestGraph<'a>,
        supplied_instances: BTreeMap<ArtifactKey, AnyArtifactInstance>,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<G>> + Send + 'a>>;
}

pub struct TaskRunPlanner<'a, G, E> {
    manifest: &'a StageManifest,
    task_execution_graph_builder: E,
    _graph_marker: std::marker::PhantomData<G>,
}

impl<'a, G, E> TaskRunPlanner<'a, G, E>
where
    E: TaskRunGraphBuilder<'a, G>,
{
    pub fn new(
        manifest: &'a StageManifest,
        _fingerprint_provider: Option<FingerprintProvider>,
        task_execution_graph_builder: E,
    ) -> Self {
        Self {
            manifest,
            task_execution_graph_builder,
            _graph_marker: std::marker::PhantomData,
        }
    }

    pub async fn plan(
        &self,
        supplied_instances: BTreeMap<ArtifactKey, AnyArtifactInstance>,
        requested_outputs: BTreeSet<ArtifactKey>,
        run_policy: Option<PlanningRunPolicy>,
        user_supplied_settings: BTreeSet<ArtifactKey>,
    ) -> Result<G> {
        let manifest = self.manifest;
        self.validate_requested_outputs(&requested_outputs)?;

        let request_settings = PlannerRequestSettings::read_from(
            manifest,
            &supplied_instances,
            user_supplied_settings,
        )
        .await?;

        let manifest_graph = StageManifestGraphBuilder::new(manifest).build();
        let context = TaskRequestContext {
            manifest,
            manifest_graph,
            requested_output_ids: requested_outputs.into_iter().collect(),
            supplied_instances: supplied_instances.clone(),
            policy: run_policy.unwrap_or_default(),
            request_settings,
        };

        let settings_resolver = PlannerSettingsResolver::new(manifest);
        let producer_selector = ProducerSelector::new(manifest);
        let task_request_graph_builder =
            TaskRequestGraphBuilder::new(manifest, &settings_resolver, &producer_selector);
        let task_request_graph = task_request_graph_builder.build(&context).await?;

        self.task_execution_graph_builder
            .clone()
            .build(task_request_graph, supplied_instances)
            .await
    }

    fn validate_requested_outputs(&self, requested_outputs: &BTreeSet<ArtifactKey>) -> Result<()> {
        if requested_outputs.is_empty() {
            bail!("at least one output must be requested");
        }
        for artifact_id in requested_outputs {
            if !self
                .manifest
                .phase_catalog
                .requestable_output_exists(artifact_id)
            {
                bail!("Configured stages do not produce artifact {artifact_id}");
            }
        }
        Ok(())
    }
}

// Re-export the same planning types surfaced by the Python module.
pub type TaskRunPlannerPlannedInputBinding = PlannedInputBinding;
pub type TaskRunPlannerPlannedOutputBinding = PlannedOutputBinding;
pub type TaskRunPlannerPlannedInvocation<'a> = PlannedInvocation<'a>;
pub type TaskRunPlannerRequestedOutputBinding = RequestedOutputBinding;
