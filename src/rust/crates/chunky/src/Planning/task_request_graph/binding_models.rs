//! Concrete request-graph artifact bindings.
//!
//! Binding models produced during request graph planning.

use super::shared_models::instance_id;
use crate::artifact_models::{ArtifactKey, PhaseMatchMode};

/// Resolved artifact instance selected while expanding the request graph.
///
/// This is intentionally narrower than final runner input/output bindings. It
/// answers one artifact need: either a supplied/default artifact root, or an
/// artifact produced by a planned invocation.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArtifactResolution {
    pub resolved_artifact_id: ArtifactKey,
    pub resolved_instance_id: String,
    pub producer_invocation_key: Option<String>,
}

impl ArtifactResolution {
    pub fn supplied(artifact_id: ArtifactKey, artifact_instance_id: impl Into<String>) -> Self {
        Self {
            resolved_artifact_id: artifact_id,
            resolved_instance_id: artifact_instance_id.into(),
            producer_invocation_key: None,
        }
    }

    pub fn supplied_setting(artifact_id: ArtifactKey) -> Self {
        let resolved_instance_id = instance_id(&artifact_id, "");
        Self {
            resolved_artifact_id: artifact_id,
            resolved_instance_id,
            producer_invocation_key: None,
        }
    }

    pub fn produced(
        artifact_id: ArtifactKey,
        artifact_instance_id: impl Into<String>,
        producer_invocation_key: impl Into<String>,
    ) -> Self {
        Self {
            resolved_artifact_id: artifact_id,
            resolved_instance_id: artifact_instance_id.into(),
            producer_invocation_key: Some(producer_invocation_key.into()),
        }
    }

    pub fn unresolved(artifact_id: ArtifactKey) -> MissingOptionalArtifact {
        MissingOptionalArtifact { artifact_id }
    }

    pub fn from_input_binding(binding: &PlannedInputBinding) -> Self {
        Self {
            resolved_artifact_id: binding.resolved_artifact_id.clone(),
            resolved_instance_id: binding.resolved_instance_id.clone(),
            producer_invocation_key: binding.producer_invocation_key.clone(),
        }
    }

    pub fn is_resolved(&self) -> bool {
        true
    }

    pub fn is_produced_by_invocation(&self) -> bool {
        self.producer_invocation_key.is_some()
    }
}

/// Result used when an optional artifact has no valid supplied or producer path.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MissingOptionalArtifact {
    pub artifact_id: ArtifactKey,
}

impl MissingOptionalArtifact {
    pub fn resolved_artifact_id(&self) -> &ArtifactKey {
        &self.artifact_id
    }

    pub fn resolved_instance_id(&self) -> Option<&str> {
        None
    }

    pub fn producer_invocation_key(&self) -> Option<&str> {
        None
    }

    pub fn is_resolved(&self) -> bool {
        false
    }

    pub fn is_produced_by_invocation(&self) -> bool {
        false
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ArtifactRequirementResult {
    Resolved(ArtifactResolution),
    MissingOptional(MissingOptionalArtifact),
}

impl ArtifactRequirementResult {
    pub fn as_resolution(&self) -> Option<&ArtifactResolution> {
        match self {
            Self::Resolved(resolution) => Some(resolution),
            Self::MissingOptional(_) => None,
        }
    }
}

impl From<ArtifactResolution> for ArtifactRequirementResult {
    fn from(value: ArtifactResolution) -> Self {
        Self::Resolved(value)
    }
}

impl From<MissingOptionalArtifact> for ArtifactRequirementResult {
    fn from(value: MissingOptionalArtifact) -> Self {
        Self::MissingOptional(value)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PlannedInputBinding {
    pub selector: ArtifactKey,
    pub resolved_artifact_id: ArtifactKey,
    pub resolved_instance_id: String,
    pub required: bool,
    pub phase_match: PhaseMatchMode,
    pub pushed_settings: Vec<ArtifactKey>,
    pub producer_invocation_key: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PlannedOutputBinding {
    pub declared_artifact_id: ArtifactKey,
    pub resolved_artifact_id: ArtifactKey,
    pub instance_id: String,
    pub expected_fingerprint: Option<String>,
}

impl PlannedOutputBinding {
    pub fn terminal_remapped(&self) -> bool {
        self.declared_artifact_id != self.resolved_artifact_id
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RequestedOutputBinding {
    pub requested_artifact_id: ArtifactKey,
    pub instance_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TaskOutputBinding {
    pub requested_artifact_id: ArtifactKey,
    pub instance_id: String,
}
