//! Concrete request-specific invocation graph before final fingerprints.
//!
//! Concrete request-specific invocation graph before final fingerprints.

use anyhow::{bail, Result};
use std::collections::{BTreeMap, BTreeSet};

use crate::artifact_models::{ArtifactKey, ArtifactRequirement, PhaseMatchMode};
use crate::stage_manifest::StageManifest;

use super::binding_models::RequestedOutputBinding;
use super::graph_state_models::GraphBuildState;
use super::graph_walker::{GraphWorkQueue, StageManifestGraphWalker};
use super::invocation_models::{InvocationDraft, PlannedInvocation};
use super::producers::ProducerSelector;
use super::request_models::TaskRequestContext;
use super::settings::PlannerSettingsResolver;

#[derive(Clone)]
pub struct TaskRequestGraph<'a> {
    pub manifest_id: String,
    pub invocations: Vec<PlannedInvocation<'a>>,
    pub dependencies_by_invocation: BTreeMap<String, Vec<String>>,
    pub dependents_by_invocation: BTreeMap<String, Vec<String>>,
    pub requested_output_bindings: Vec<RequestedOutputBinding>,
}

impl<'a> TaskRequestGraph<'a> {
    pub fn requested_output_ids(&self) -> Vec<ArtifactKey> {
        self.requested_output_bindings
            .iter()
            .map(|binding| binding.requested_artifact_id.clone())
            .collect()
    }
}

pub struct TaskRequestGraphBuilder<'ctx, 'a> {
    manifest: &'a StageManifest,
    settings_resolver: &'ctx PlannerSettingsResolver<'a>,
    producer_selector: &'ctx ProducerSelector<'a>,
}

impl<'ctx, 'a> TaskRequestGraphBuilder<'ctx, 'a> {
    pub fn new(
        manifest: &'a StageManifest,
        settings_resolver: &'ctx PlannerSettingsResolver<'a>,
        producer_selector: &'ctx ProducerSelector<'a>,
    ) -> Self {
        Self {
            manifest,
            settings_resolver,
            producer_selector,
        }
    }

    pub async fn build(&self, context: &TaskRequestContext<'a>) -> Result<TaskRequestGraph<'a>> {
        let work_queue = GraphWorkQueue::from_requested_outputs(
            context.requested_output_ids.clone(),
            |output_id| self.requested_output_requirement(output_id),
        );
        let state = StageManifestGraphWalker::new(
            context,
            self.settings_resolver,
            self.producer_selector,
            work_queue,
            None,
        )
        .expand()?;

        self.finalize_graph(state)
    }

    fn finalize_graph(&self, graph: GraphBuildState<'a>) -> Result<TaskRequestGraph<'a>> {
        let ordered_candidate_keys = self.topological_stage_order(&graph.invocations_by_key)?;
        let invocation_ids_by_candidate =
            self.assign_invocation_ids(&graph, &ordered_candidate_keys);

        let mut invocations = Vec::new();
        for candidate_key in &ordered_candidate_keys {
            let invocation = graph
                .invocations_by_key
                .get(candidate_key)
                .expect("ordered candidate came from graph map");
            invocations.push(self.planned_invocation_for_draft(
                invocation_ids_by_candidate[candidate_key].clone(),
                invocation,
            )?);
        }

        let dependencies_by_invocation =
            self.dependencies_by_invocation(&graph, &invocation_ids_by_candidate);
        let dependents_by_invocation = self.dependents_by_invocation(&dependencies_by_invocation);

        let mut requested_output_bindings: Vec<_> =
            graph.requested_output_bindings.values().cloned().collect();
        requested_output_bindings
            .sort_by(|left, right| left.requested_artifact_id.cmp(&right.requested_artifact_id));

        Ok(TaskRequestGraph {
            manifest_id: self.manifest.manifest_id.clone(),
            invocations,
            dependencies_by_invocation,
            dependents_by_invocation,
            requested_output_bindings,
        })
    }

    fn assign_invocation_ids(
        &self,
        graph: &GraphBuildState<'a>,
        ordered_candidate_keys: &[String],
    ) -> BTreeMap<String, String> {
        ordered_candidate_keys
            .iter()
            .enumerate()
            .map(|(index, candidate_key)| {
                let stage_id = graph.invocations_by_key[candidate_key]
                    .registration
                    .stage_id();
                (
                    candidate_key.clone(),
                    format!("{stage_id}:{:03}", index + 1),
                )
            })
            .collect()
    }

    fn planned_invocation_for_draft(
        &self,
        invocation_id: String,
        invocation: &InvocationDraft<'a>,
    ) -> Result<PlannedInvocation<'a>> {
        let resource_policy = invocation.resource_policy.clone().ok_or_else(|| {
            anyhow::anyhow!(
                "invocation {} has no resolved resource policy",
                invocation.key
            )
        })?;

        let mut input_bindings: Vec<_> = invocation.input_bindings.values().cloned().collect();
        input_bindings.sort_by(|left, right| {
            (!left.required)
                .cmp(&(!right.required))
                .then_with(|| left.selector.cmp(&right.selector))
        });

        let mut requested_output_bindings: Vec<_> =
            invocation.output_bindings.values().cloned().collect();
        requested_output_bindings
            .sort_by(|left, right| left.declared_artifact_id.cmp(&right.declared_artifact_id));

        Ok(PlannedInvocation {
            invocation_id,
            registration: invocation.registration.clone(),
            input_bindings,
            requested_output_bindings,
            effective_settings_fingerprint: invocation.effective_settings_fingerprint.clone(),
            resolved_resource_policy: resource_policy,
            resolved_settings: invocation.resolved_settings.clone(),
        })
    }

    fn dependencies_by_invocation(
        &self,
        graph: &GraphBuildState<'a>,
        invocation_ids_by_candidate: &BTreeMap<String, String>,
    ) -> BTreeMap<String, Vec<String>> {
        graph
            .invocations_by_key
            .iter()
            .map(|(key, candidate)| {
                let mut dependencies: Vec<String> = candidate
                    .dependency_keys()
                    .into_iter()
                    .filter_map(|dependency| invocation_ids_by_candidate.get(&dependency).cloned())
                    .collect();
                dependencies.sort();
                (invocation_ids_by_candidate[key].clone(), dependencies)
            })
            .collect()
    }

    fn dependents_by_invocation(
        &self,
        dependencies_by_invocation: &BTreeMap<String, Vec<String>>,
    ) -> BTreeMap<String, Vec<String>> {
        dependencies_by_invocation
            .keys()
            .map(|invocation_id| {
                let mut downstream: Vec<String> = dependencies_by_invocation
                    .iter()
                    .filter(|(_, dependencies)| dependencies.contains(invocation_id))
                    .map(|(downstream_id, _)| downstream_id.clone())
                    .collect();
                downstream.sort();
                (invocation_id.clone(), downstream)
            })
            .collect()
    }

    fn topological_stage_order(
        &self,
        invocations_by_key: &BTreeMap<String, InvocationDraft<'a>>,
    ) -> Result<Vec<String>> {
        let mut remaining: BTreeMap<String, BTreeSet<String>> = invocations_by_key
            .iter()
            .map(|(key, invocation)| (key.clone(), invocation.dependency_keys()))
            .collect();
        let mut ordered = Vec::new();

        while !remaining.is_empty() {
            let ready: Vec<String> = remaining
                .iter()
                .filter(|(_, dependencies)| dependencies.is_empty())
                .map(|(key, _)| key.clone())
                .collect();

            if ready.is_empty() {
                let rendered = remaining
                    .iter()
                    .map(|(key, dependencies)| format!("{key} <- {dependencies:?}"))
                    .collect::<Vec<_>>()
                    .join("; ");
                bail!("request-specific graph is cyclic or unresolved: {rendered}");
            }

            for key in ready {
                ordered.push(key.clone());
                remaining.remove(&key);
            }
            for dependencies in remaining.values_mut() {
                for key in &ordered {
                    dependencies.remove(key);
                }
            }
        }

        Ok(ordered)
    }

    fn requested_output_requirement(&self, output_id: ArtifactKey) -> ArtifactRequirement {
        let concrete_phase_requested =
            output_id.phase.is_some() && self.manifest.concrete_artifact_is_known(&output_id);
        ArtifactRequirement {
            artifact_id: output_id,
            required: true,
            phase_match: if concrete_phase_requested {
                PhaseMatchMode::Exact
            } else {
                PhaseMatchMode::AtMost
            },
            pushed_settings: Vec::new(),
        }
    }
}
