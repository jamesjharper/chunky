//! Invocation models for request graph construction.
//!
//! Invocation models for request graph construction.

use crate::artifact_models::ArtifactKey;
use crate::stage_models::{ManifestStageRegistration, ResolvedSettingsView, ResourcePolicy};
use std::collections::{BTreeMap, BTreeSet};

use super::binding_models::{PlannedInputBinding, PlannedOutputBinding};
use super::shared_models::InputRequirementKey;

#[derive(Clone)]
pub struct PlannedInvocation<'a> {
    pub invocation_id: String,
    pub registration: ManifestStageRegistration<'a>,
    pub stage_fingerprint: String,
    pub input_bindings: Vec<PlannedInputBinding>,
    pub requested_output_bindings: Vec<PlannedOutputBinding>,
    pub effective_settings_fingerprint: String,
    pub resolved_resource_policy: ResourcePolicy,
    pub resolved_settings: ResolvedSettingsView,
}

impl<'a> PlannedInvocation<'a> {
    pub fn input_ids(&self) -> Vec<ArtifactKey> {
        self.input_bindings
            .iter()
            .map(|binding| binding.resolved_artifact_id.clone())
            .collect()
    }

    pub fn requested_output_ids(&self) -> Vec<ArtifactKey> {
        self.requested_output_bindings
            .iter()
            .map(|binding| binding.resolved_artifact_id.clone())
            .collect()
    }
}

#[derive(Clone)]
pub struct RunnableInvocation<'a> {
    pub invocation_id: String,
    pub registration: ManifestStageRegistration<'a>,
    pub input_bindings: Vec<PlannedInputBinding>,
    pub requested_output_bindings: Vec<PlannedOutputBinding>,
    pub resolved_resource_policy: ResourcePolicy,
    pub pedigree_fingerprint: String,
    pub resolved_settings: ResolvedSettingsView,
}

impl<'a> RunnableInvocation<'a> {
    pub fn input_ids(&self) -> Vec<ArtifactKey> {
        self.input_bindings
            .iter()
            .map(|binding| binding.resolved_artifact_id.clone())
            .collect()
    }

    pub fn requested_output_ids(&self) -> Vec<ArtifactKey> {
        self.requested_output_bindings
            .iter()
            .map(|binding| binding.resolved_artifact_id.clone())
            .collect()
    }
}

/// Mutable stage invocation while the planner is still discovering its graph.
#[derive(Clone)]
pub struct InvocationDraft<'a> {
    pub key: String,
    pub registration: ManifestStageRegistration<'a>,
    pub inherited_settings: ResolvedSettingsView,
    pub effective_settings_fingerprint: String,
    pub stage_fingerprint: String,
    pub output_bindings: BTreeMap<ArtifactKey, PlannedOutputBinding>,
    pub input_bindings: BTreeMap<InputRequirementKey, PlannedInputBinding>,
    pub resource_policy: Option<ResourcePolicy>,
    pub resolved_settings: ResolvedSettingsView,
    pub input_requirements_resolved_for_outputs: BTreeSet<(ArtifactKey, ArtifactKey)>,
}

impl<'a> InvocationDraft<'a> {
    pub fn new(
        key: impl Into<String>,
        registration: ManifestStageRegistration<'a>,
        inherited_settings: ResolvedSettingsView,
        effective_settings_fingerprint: impl Into<String>,
        stage_fingerprint: impl Into<String>,
    ) -> Self {
        Self {
            key: key.into(),
            registration,
            inherited_settings,
            effective_settings_fingerprint: effective_settings_fingerprint.into(),
            stage_fingerprint: stage_fingerprint.into(),
            output_bindings: BTreeMap::new(),
            input_bindings: BTreeMap::new(),
            resource_policy: None,
            resolved_settings: ResolvedSettingsView::default(),
            input_requirements_resolved_for_outputs: BTreeSet::new(),
        }
    }

    pub fn requested_output_signature(&self) -> BTreeSet<(ArtifactKey, ArtifactKey)> {
        self.output_bindings
            .iter()
            .map(|(declared, binding)| (declared.clone(), binding.resolved_artifact_id.clone()))
            .collect()
    }

    pub fn has_unresolved_requirements(&self) -> bool {
        self.requested_output_signature() != self.input_requirements_resolved_for_outputs
    }

    pub fn mark_current_requirements_as_resolved(&mut self) {
        self.input_requirements_resolved_for_outputs = self.requested_output_signature();
    }

    pub fn dependency_keys(&self) -> BTreeSet<String> {
        self.input_bindings
            .values()
            .filter_map(|binding| binding.producer_invocation_key.clone())
            .filter(|key| key != &self.key)
            .collect()
    }
}
