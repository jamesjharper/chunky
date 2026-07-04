//! Mutable graph-discovery state for request expansion.
//!
//! Mutable graph-discovery state for request expansion.

use anyhow::{bail, Result};
use serde_json::Value;
use std::collections::BTreeMap;

use crate::artifact_models::ArtifactKey;

use super::binding_models::{
    ArtifactResolution, PlannedInputBinding, PlannedOutputBinding, RequestedOutputBinding,
};
use super::invocation_models::InvocationDraft;
use super::requirement_models::{ArtifactNeed, ProducedArtifact};
use super::shared_models::{InputRequirementKey, ProducerSelection};

/// Stable identity used to decide whether producer paths share one invocation.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct InvocationIdentity {
    pub stage_id: String,
    pub inherited_settings: Vec<(ArtifactKey, Value)>,
    pub effective_settings_fingerprint: String,
}

impl InvocationIdentity {
    pub fn from_selection(
        selection: &ProducerSelection<'_>,
        inherited_settings: Vec<(ArtifactKey, Value)>,
        effective_settings_fingerprint: impl Into<String>,
    ) -> Self {
        Self {
            stage_id: selection.registration.stage_id().to_owned(),
            inherited_settings,
            effective_settings_fingerprint: effective_settings_fingerprint.into(),
        }
    }

    pub fn from_invocation(invocation: &InvocationDraft<'_>) -> Self {
        Self {
            stage_id: invocation.registration.stage_id().to_owned(),
            inherited_settings: invocation.inherited_settings.clone(),
            effective_settings_fingerprint: invocation.effective_settings_fingerprint.clone(),
        }
    }
}

/// Mutable graph-discovery state for one request expansion pass.
///
/// Invocation allocation, requested-output bindings, and draft invocation data
/// all belong to the same graph-build transaction, especially for speculative
/// clone/commit behavior.
#[derive(Clone, Default)]
pub struct GraphBuildState<'a> {
    pub invocations_by_key: BTreeMap<String, InvocationDraft<'a>>,
    pub requested_output_bindings: BTreeMap<ArtifactKey, RequestedOutputBinding>,
    pub producer_invocation_by_instance_id: BTreeMap<String, String>,
    pub next_invocation_sequence_by_stage: BTreeMap<String, usize>,
}

impl<'a> GraphBuildState<'a> {
    pub fn replace_with(&mut self, replacement: &GraphBuildState<'a>) {
        *self = replacement.clone();
    }

    pub fn find_invocation_key_by_instance(&self, instance_id: &str) -> Option<String> {
        self.producer_invocation_by_instance_id
            .get(instance_id)
            .cloned()
    }

    pub fn find_invocation_by_key(&self, invocation_key: &str) -> Result<&InvocationDraft<'a>> {
        self.invocations_by_key
            .get(invocation_key)
            .ok_or_else(|| anyhow::anyhow!("unknown invocation draft: {invocation_key}"))
    }

    pub fn find_invocation_by_key_mut(
        &mut self,
        invocation_key: &str,
    ) -> Result<&mut InvocationDraft<'a>> {
        self.invocations_by_key
            .get_mut(invocation_key)
            .ok_or_else(|| anyhow::anyhow!("unknown invocation draft: {invocation_key}"))
    }

    pub fn find_invocation(
        &self,
        selection: &ProducerSelection<'a>,
        inherited_settings: Vec<(ArtifactKey, Value)>,
        effective_settings_fingerprint: &str,
    ) -> Option<&InvocationDraft<'a>> {
        let identity = InvocationIdentity::from_selection(
            selection,
            inherited_settings,
            effective_settings_fingerprint,
        );
        self.invocations_by_key
            .values()
            .find(|invocation| InvocationIdentity::from_invocation(invocation) == identity)
    }

    pub fn find_or_create_invocation(
        &mut self,
        producer_artifact: ProducedArtifact<'a>,
        inherited_settings: Vec<(ArtifactKey, Value)>,
    ) -> Result<&InvocationDraft<'a>> {
        if let Some(existing_key) = self
            .find_invocation(
                &producer_artifact.selection,
                inherited_settings.clone(),
                &producer_artifact.effective_settings_fingerprint,
            )
            .map(|invocation| invocation.key.clone())
        {
            self.record_invocation_output(&existing_key, &producer_artifact)?;
            return self.find_invocation_by_key(&existing_key);
        }

        let key = self.record_producer_invocation(
            producer_artifact.selection.registration.stage_id(),
            &producer_artifact.resolved_instance_id,
        );
        let invocation = InvocationDraft::new(
            key.clone(),
            producer_artifact.selection.registration.clone(),
            inherited_settings,
            producer_artifact.effective_settings_fingerprint.clone(),
        );
        self.invocations_by_key.insert(key.clone(), invocation);
        self.record_invocation_output(&key, &producer_artifact)?;
        self.find_invocation_by_key(&key)
    }

    fn record_producer_invocation(&mut self, stage_id: &str, instance_id: &str) -> String {
        let sequence = self
            .next_invocation_sequence_by_stage
            .entry(stage_id.to_owned())
            .or_insert(0);
        *sequence += 1;
        let invocation_key = format!("{stage_id}@{sequence}");
        self.producer_invocation_by_instance_id
            .insert(instance_id.to_owned(), invocation_key.clone());
        invocation_key
    }

    pub fn record_invocation_output(
        &mut self,
        invocation_key: &str,
        producer_artifact: &ProducedArtifact<'a>,
    ) -> Result<()> {
        let invocation = self.find_invocation_by_key_mut(invocation_key)?;
        let selection = &producer_artifact.selection;

        if let Some(existing) = invocation
            .output_bindings
            .get(&selection.declared_output_id)
        {
            if existing.resolved_artifact_id != producer_artifact.resolved_artifact_id
                || existing.instance_id != producer_artifact.resolved_instance_id
            {
                bail!(
                    "{} has conflicting bindings for {}: {} vs {}",
                    invocation.registration.stage_id(),
                    selection.declared_output_id,
                    existing.resolved_artifact_id,
                    producer_artifact.resolved_artifact_id
                );
            }
            return Ok(());
        }

        invocation.output_bindings.insert(
            selection.declared_output_id.clone(),
            PlannedOutputBinding {
                declared_artifact_id: selection.declared_output_id.clone(),
                resolved_artifact_id: producer_artifact.resolved_artifact_id.clone(),
                instance_id: producer_artifact.resolved_instance_id.clone(),
                expected_fingerprint: None,
            },
        );
        Ok(())
    }

    pub fn record_requested_output_binding(
        &mut self,
        requested_output_id: ArtifactKey,
        resolution: &ArtifactResolution,
    ) {
        self.requested_output_bindings.insert(
            requested_output_id.clone(),
            RequestedOutputBinding {
                requested_artifact_id: requested_output_id,
                instance_id: resolution.resolved_instance_id.clone(),
            },
        );
    }

    pub fn record_invocation_input_binding(
        &mut self,
        need: &ArtifactNeed,
        resolution: &ArtifactResolution,
    ) -> Result<()> {
        let consumer_invocation_key = need
            .consumer_invocation_key
            .as_deref()
            .ok_or_else(|| anyhow::anyhow!("artifact need has no consumer invocation key"))?;
        let invocation = self.find_invocation_by_key_mut(consumer_invocation_key)?;
        let requirement = &need.requirement;

        invocation.input_bindings.insert(
            InputRequirementKey::from_requirement(requirement),
            PlannedInputBinding {
                selector: requirement.artifact_id.clone(),
                resolved_artifact_id: resolution.resolved_artifact_id.clone(),
                resolved_instance_id: resolution.resolved_instance_id.clone(),
                required: requirement.required,
                phase_match: requirement.phase_match,
                pushed_settings: requirement.pushed_settings.clone(),
                producer_invocation_key: resolution.producer_invocation_key.clone(),
            },
        );
        Ok(())
    }

    pub fn input_binding_for(&self, need: &ArtifactNeed) -> Option<&PlannedInputBinding> {
        let invocation_key = need.consumer_invocation_key.as_ref()?;
        let invocation = self.invocations_by_key.get(invocation_key)?;
        let binding_key = InputRequirementKey::from_requirement(&need.requirement);
        invocation.input_bindings.get(&binding_key)
    }
}
