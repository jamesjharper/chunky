from __future__ import annotations

from dataclasses import dataclass, field

from ...Stages.stage_models import ManifestStageRegistration, ResolvedSettingsView, ResourcePolicy
from ...Artifacts.artifact_models import ArtifactKey
from .shared_models import InputRequirementKey
from .binding_models import PlannedInputBinding, PlannedOutputBinding


@dataclass(frozen=True)
class PlannedInvocation:
    invocation_id: str
    registration: ManifestStageRegistration
    input_bindings: tuple[PlannedInputBinding, ...]
    requested_output_bindings: tuple[PlannedOutputBinding, ...]
    effective_settings_fingerprint: str
    resolved_resource_policy: ResourcePolicy
    resolved_settings: ResolvedSettingsView = field(default_factory=ResolvedSettingsView)

    @property
    def requested_output_ids(self) -> tuple[ArtifactKey, ...]:
        return tuple(binding.resolved_artifact_id for binding in self.requested_output_bindings)


@dataclass(frozen=True)
class RunnableInvocation:
    invocation_id: str
    registration: ManifestStageRegistration
    input_bindings: tuple[PlannedInputBinding, ...]
    requested_output_bindings: tuple[PlannedOutputBinding, ...]
    resolved_resource_policy: ResourcePolicy
    pedigree_fingerprint: str
    resolved_settings: ResolvedSettingsView = field(default_factory=ResolvedSettingsView)

    @property
    def requested_output_ids(self) -> tuple[ArtifactKey, ...]:
        return tuple(binding.resolved_artifact_id for binding in self.requested_output_bindings)


@dataclass
class InvocationDraft:
    """Mutable stage invocation while the planner is still discovering its graph."""

    key: str
    registration: ManifestStageRegistration

    inherited_settings: tuple[tuple[ArtifactKey, object], ...]
    effective_settings_fingerprint: str

    # The expected output binding for this invocation.
    output_bindings: dict[ArtifactKey, PlannedOutputBinding] = field(default_factory=dict)

    # The expected input bindings for this invocation.
    input_bindings: dict[
        InputRequirementKey,
        PlannedInputBinding,
    ] = field(default_factory=dict)

    resource_policy: ResourcePolicy | None = None
    resolved_settings: ResolvedSettingsView = field(default_factory=ResolvedSettingsView)
    input_requirements_resolved_for_outputs: frozenset[
        tuple[ArtifactKey, ArtifactKey]
    ] = frozenset()

    def requested_output_signature(self) -> frozenset[tuple[ArtifactKey, ArtifactKey]]:
        """Current declared-to-resolved output bindings requested from this invocation."""

        return frozenset(
            (declared, binding.resolved_artifact_id)
            for declared, binding in self.output_bindings.items()
        )

    def has_unresolved_requirements(self) -> bool:
        return (
            self.requested_output_signature()
            != self.input_requirements_resolved_for_outputs
        )

    def mark_current_requirements_as_resolved(self) -> None:
        self.input_requirements_resolved_for_outputs = self.requested_output_signature()

    def dependency_keys(self) -> frozenset[str]:
        return frozenset(
            binding.producer_invocation_key
            for binding in self.input_bindings.values()
            if binding.producer_invocation_key is not None
            and binding.producer_invocation_key != self.key
        )
