//! Planner work-item requirement models.
//!
//! Planner work-item requirement models.

use crate::artifact_models::{ArtifactKey, ArtifactRequirement};
use crate::stage_models::{ResolvedSettingsView, StagePlanningContext};

use super::settings::settings_fingerprint;
use super::shared_models::{instance_id, ProducerSelection};

/// One artifact the graph walker needs to resolve.
///
/// This is a planner work item, not the stage's declaration itself. It carries
/// the requirement plus consumer/requested-output context needed to record the
/// selected binding after resolution.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArtifactNeed {
    pub requirement: ArtifactRequirement,
    pub inherited_settings: ResolvedSettingsView,
    pub consumer_invocation_key: Option<String>,
    pub requested_output_id: Option<ArtifactKey>,
}

impl ArtifactNeed {
    pub fn is_required(&self) -> bool {
        self.requirement.required
    }

    pub fn is_invocation_input(&self) -> bool {
        self.consumer_invocation_key.is_some()
    }

    pub fn is_requested_output(&self) -> bool {
        self.requested_output_id.is_some()
    }
}

#[derive(Clone)]
pub struct ProducedArtifact<'a> {
    pub selection: ProducerSelection<'a>,
    pub resolved_artifact_id: ArtifactKey,
    pub resolved_instance_id: String,
    pub stage_fingerprint: String,
    pub effective_settings_fingerprint: String,
}

impl<'a> ProducedArtifact<'a> {
    pub fn for_selection(
        producer_context: &StagePlanningContext,
        selection: ProducerSelection<'a>,
    ) -> Self {
        let inherited_settings_fingerprint =
            settings_fingerprint(&producer_context.inherited_settings);
        let mut effective_settings_fingerprint =
            settings_fingerprint(&producer_context.resolved_settings);
        if effective_settings_fingerprint.is_empty() {
            effective_settings_fingerprint = inherited_settings_fingerprint.clone();
        }

        let semantic_id = selection.resolved_output_id.clone();
        let output_instance_id = instance_id(
            &semantic_id,
            if effective_settings_fingerprint.is_empty() {
                &inherited_settings_fingerprint
            } else {
                &effective_settings_fingerprint
            },
        );

        Self {
            selection,
            resolved_artifact_id: semantic_id,
            resolved_instance_id: output_instance_id,
            stage_fingerprint: producer_context.stage_fingerprint.clone(),
            effective_settings_fingerprint,
        }
    }
}
