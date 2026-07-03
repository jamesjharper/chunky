//! Shared planning identities.
//!
//! Shared planning identities.

use crate::artifact_models::{ArtifactKey, ArtifactRequirement, PhaseMatchMode};
use crate::stage_models::ManifestStageRegistration;

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct InputRequirementKey {
    /// Stable identity for one invocation input requirement context.
    pub artifact_id: ArtifactKey,
    pub phase_match: PhaseMatchMode,
    pub pushed_settings: Vec<ArtifactKey>,
}

impl InputRequirementKey {
    pub fn from_requirement(requirement: &ArtifactRequirement) -> Self {
        Self {
            artifact_id: requirement.artifact_id.clone(),
            phase_match: requirement.phase_match,
            pushed_settings: requirement.pushed_settings.clone(),
        }
    }
}

#[derive(Clone)]
pub struct ProducerSelection<'a> {
    pub registration: ManifestStageRegistration<'a>,
    pub declared_output_id: ArtifactKey,
    pub resolved_output_id: ArtifactKey,
}

/// Build the runner-facing instance id for one concrete artifact binding.
pub fn instance_id(artifact_id: &ArtifactKey, effective_settings_fingerprint: &str) -> String {
    if effective_settings_fingerprint.is_empty() {
        artifact_id.to_string()
    } else {
        let prefix: String = effective_settings_fingerprint.chars().take(16).collect();
        format!("{artifact_id}@{prefix}")
    }
}
