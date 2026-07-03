from __future__ import annotations

from dataclasses import dataclass, field

from ...Artifacts.artifact_models import ArtifactKey
from .shared_models import InputRequirementKey, ProducerSelection
from .binding_models import (
    ArtifactResolution,
    PlannedInputBinding,
    PlannedOutputBinding,
    RequestedOutputBinding,
)
from .invocation_models import InvocationDraft
from .requirement_models import ArtifactNeed, ProducedArtifact
from ...Stages.stage_models import StagePlanningError


@dataclass(frozen=True)
class InvocationIdentity:
    """Stable identity used to decide whether producer paths share one invocation."""

    stage_id: str
    inherited_settings: tuple[tuple[ArtifactKey, object], ...]
    effective_settings_fingerprint: str

    @staticmethod
    def from_selection(
        selection: ProducerSelection,
        inherited_settings: tuple[tuple[ArtifactKey, object], ...],
        effective_settings_fingerprint: str,
    ) -> "InvocationIdentity":
        return InvocationIdentity(
            stage_id=selection.registration.stage_id,
            inherited_settings=inherited_settings,
            effective_settings_fingerprint=effective_settings_fingerprint,
        )

    @staticmethod
    def from_invocation(invocation: InvocationDraft) -> "InvocationIdentity":
        return InvocationIdentity(
            stage_id=invocation.registration.stage_id,
            inherited_settings=invocation.inherited_settings,
            effective_settings_fingerprint=invocation.effective_settings_fingerprint,
        )


@dataclass
class GraphBuildState:
    """Mutable graph-discovery state for one request expansion pass.

    Invocation allocation, requested-output bindings, and draft invocation data
    all belong to the same graph-build transaction, especially for speculative
    clone/commit behavior.
    """

    invocations_by_key: dict[str, InvocationDraft] = field(default_factory=dict)
    requested_output_bindings: dict[ArtifactKey, RequestedOutputBinding] = field(
        default_factory=dict
    )
    producer_invocation_by_instance_id: dict[str, str] = field(default_factory=dict)
    next_invocation_sequence_by_stage: dict[str, int] = field(default_factory=dict)

    def clone(self) -> "GraphBuildState":
        invocations_by_key: dict[str, InvocationDraft] = {}
        for key, invocation in self.invocations_by_key.items():
            invocations_by_key[key] = InvocationDraft(
                key=invocation.key,
                registration=invocation.registration,
                inherited_settings=invocation.inherited_settings,
                effective_settings_fingerprint=invocation.effective_settings_fingerprint,
                output_bindings=dict(invocation.output_bindings),
                input_bindings=dict(invocation.input_bindings),
                resource_policy=invocation.resource_policy,
                resolved_settings=invocation.resolved_settings,
                input_requirements_resolved_for_outputs=(
                    invocation.input_requirements_resolved_for_outputs
                ),
            )

        return GraphBuildState(
            invocations_by_key=invocations_by_key,
            requested_output_bindings=dict(self.requested_output_bindings),
            producer_invocation_by_instance_id=dict(
                self.producer_invocation_by_instance_id
            ),
            next_invocation_sequence_by_stage=dict(
                self.next_invocation_sequence_by_stage
            ),
        )

    def replace_with(self, replacement: "GraphBuildState") -> None:
        cloned = replacement.clone()
        self.invocations_by_key = cloned.invocations_by_key
        self.requested_output_bindings = cloned.requested_output_bindings
        self.producer_invocation_by_instance_id = (
            cloned.producer_invocation_by_instance_id
        )
        self.next_invocation_sequence_by_stage = (
            cloned.next_invocation_sequence_by_stage
        )

    def find_invocation_key_by_instance(self, instance_id_: str) -> str | None:
        return self.producer_invocation_by_instance_id.get(instance_id_)

    def find_invocation_by_key(self, invocation_key: str) -> InvocationDraft:
        return self.invocations_by_key[invocation_key]

    def find_invocation(
        self,
        selection: ProducerSelection,
        inherited_settings: tuple[tuple[ArtifactKey, object], ...],
        effective_settings_fingerprint: str,
    ) -> InvocationDraft | None:
        identity = InvocationIdentity.from_selection(
            selection,
            inherited_settings,
            effective_settings_fingerprint,
        )
        for invocation in self.invocations_by_key.values():
            if InvocationIdentity.from_invocation(invocation) == identity:
                return invocation
        return None

    def find_or_create_invocation(
        self,
        producer_artifact: ProducedArtifact,
        inherited_settings: tuple[tuple[ArtifactKey, object], ...],
    ) -> InvocationDraft:
        existing = self.find_invocation(
            producer_artifact.selection,
            inherited_settings,
            producer_artifact.effective_settings_fingerprint,
        )
        if existing is not None:
            self.record_invocation_output(existing.key, producer_artifact)
            return existing

        key = self._record_producer_invocation(
            producer_artifact.selection.registration.stage_id,
            producer_artifact.resolved_instance_id,
        )
        invocation = InvocationDraft(
            key=key,
            registration=producer_artifact.selection.registration,
            inherited_settings=inherited_settings,
            effective_settings_fingerprint=(
                producer_artifact.effective_settings_fingerprint
            ),
        )
        self.invocations_by_key[invocation.key] = invocation
        self.record_invocation_output(invocation.key, producer_artifact)
        return invocation

    def _record_producer_invocation(
        self,
        stage_id: str,
        instance_id_: str,
    ) -> str:
        sequence = self.next_invocation_sequence_by_stage.get(stage_id, 0) + 1
        self.next_invocation_sequence_by_stage[stage_id] = sequence

        invocation_key = f"{stage_id}@{sequence}"
        self.producer_invocation_by_instance_id[instance_id_] = invocation_key
        return invocation_key

    def record_invocation_output(
        self,
        invocation_key: str,
        producer_artifact: ProducedArtifact,
    ) -> None:
        invocation = self.invocations_by_key[invocation_key]
        selection = producer_artifact.selection
        existing = invocation.output_bindings.get(selection.declared_output_id)
        if existing is not None:
            if (
                existing.resolved_artifact_id != producer_artifact.resolved_artifact_id
                or existing.instance_id != producer_artifact.resolved_instance_id
            ):
                raise StagePlanningError(
                    f"{invocation.registration.stage_id} has conflicting bindings for "
                    f"{selection.declared_output_id}: "
                    f"{existing.resolved_artifact_id} vs "
                    f"{producer_artifact.resolved_artifact_id}"
                )
            return

        invocation.output_bindings[selection.declared_output_id] = PlannedOutputBinding(
            declared_artifact_id=selection.declared_output_id,
            resolved_artifact_id=producer_artifact.resolved_artifact_id,
            instance_id=producer_artifact.resolved_instance_id,
        )

    def record_requested_output_binding(
        self,
        requested_output_id: ArtifactKey,
        resolution: ArtifactResolution,
    ) -> None:
        self.requested_output_bindings[requested_output_id] = RequestedOutputBinding(
            requested_artifact_id=requested_output_id,
            instance_id=resolution.resolved_instance_id,
        )

    def record_invocation_input_binding(
        self,
        need: ArtifactNeed,
        resolution: ArtifactResolution,
    ) -> None:
        assert need.consumer_invocation_key is not None
        invocation = self.invocations_by_key[need.consumer_invocation_key]
        requirement = need.requirement

        invocation.input_bindings[
            InputRequirementKey.from_requirement(requirement)
        ] = PlannedInputBinding(
            selector=requirement.artifact_id,
            resolved_artifact_id=resolution.resolved_artifact_id,
            resolved_instance_id=resolution.resolved_instance_id,
            required=requirement.required,
            phase_match=requirement.phase_match,
            pushed_settings=requirement.pushed_settings,
            producer_invocation_key=resolution.producer_invocation_key,
        )

    def input_binding_for(
        self,
        need: ArtifactNeed,
    ) -> PlannedInputBinding | None:
        if need.consumer_invocation_key is None:
            return None

        invocation = self.invocations_by_key.get(need.consumer_invocation_key)
        if invocation is None:
            return None

        binding_key = InputRequirementKey.from_requirement(need.requirement)
        return invocation.input_bindings.get(binding_key)
