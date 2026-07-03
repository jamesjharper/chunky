from __future__ import annotations

from dataclasses import dataclass

from ...Artifacts.artifact_models import ArtifactKey, ArtifactRequirement
from ...Stages.stage_models import StagePlanningContext
from .shared_models import ProducerSelection, instance_id
from .settings import settings_fingerprint


@dataclass(frozen=True)
class ArtifactNeed:
    """One artifact the graph walker needs to resolve.

    This is a planner work item, not the stage's declaration itself. It carries
    the requirement plus the consumer/requested-output context needed to record
    the selected binding after resolution.
    """

    requirement: ArtifactRequirement
    inherited_settings: tuple[tuple[ArtifactKey, object], ...] = ()
    consumer_invocation_key: str | None = None
    requested_output_id: ArtifactKey | None = None

    @property
    def is_required(self) -> bool:
        return self.requirement.required

    @property
    def is_invocation_input(self) -> bool:
        return self.consumer_invocation_key is not None

    @property
    def is_requested_output(self) -> bool:
        return self.requested_output_id is not None


@dataclass(frozen=True)
class ProducedArtifact:
    selection: ProducerSelection
    resolved_artifact_id: ArtifactKey
    resolved_instance_id: str
    effective_settings_fingerprint: str

    @classmethod
    def for_selection(
        cls,
        producer_context: StagePlanningContext,
        selection: ProducerSelection,
    ) -> "ProducedArtifact":
        inherited_settings_fingerprint = settings_fingerprint(
            producer_context.inherited_settings
        )
        effective_settings_fingerprint = (
            settings_fingerprint(producer_context.resolved_settings)
            or inherited_settings_fingerprint
        )

        semantic_id = selection.resolved_output_id
        output_instance_id = instance_id(
            semantic_id,
            effective_settings_fingerprint or inherited_settings_fingerprint,
        )

        return cls(
            selection=selection,
            resolved_artifact_id=semantic_id,
            resolved_instance_id=output_instance_id,
            effective_settings_fingerprint=effective_settings_fingerprint,
        )
