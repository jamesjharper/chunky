from __future__ import annotations

from dataclasses import dataclass

from ...Artifacts.artifact_models import ArtifactKey, ArtifactRequirement, PhaseMatchMode
from ...Stages.stage_models import ManifestStageRegistration


@dataclass(frozen=True, order=True)
class InputRequirementKey:
    """Stable identity for one invocation input requirement context."""

    artifact_id: ArtifactKey
    phase_match: PhaseMatchMode
    pushed_settings: tuple[ArtifactKey, ...] = ()

    @classmethod
    def from_requirement(
        cls,
        requirement: ArtifactRequirement,
    ) -> "InputRequirementKey":
        return cls(
            artifact_id=requirement.artifact_id,
            phase_match=requirement.phase_match,
            pushed_settings=requirement.pushed_settings,
        )


@dataclass(frozen=True)
class ProducerSelection:
    registration: ManifestStageRegistration
    declared_output_id: ArtifactKey
    resolved_output_id: ArtifactKey


def instance_id(
    artifact_id: ArtifactKey,
    effective_settings_fingerprint: str,
) -> str:
    """Build a provisional planner binding id before final fingerprints exist.

    The final runner-facing id is replaced by ``a-instance`` during planning
    finalization. This provisional id is intentionally not an ``@``-joined
    legacy concrete identity.
    """

    if not effective_settings_fingerprint:
        return str(artifact_id)
    return f"{artifact_id.to_a_ref(projected=True)}?lineage={effective_settings_fingerprint[:16]}"
