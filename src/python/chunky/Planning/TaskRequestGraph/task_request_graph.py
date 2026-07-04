from __future__ import annotations

from dataclasses import dataclass

from ...Artifacts.artifact_models import (
    ArtifactKey,
    ArtifactRequirement,
    PhaseMatchMode,
)
from .settings import PlannerSettingsResolver
from .binding_models import RequestedOutputBinding
from .graph_state_models import GraphBuildState
from .invocation_models import InvocationDraft, PlannedInvocation
from .request_models import TaskRequestContext
from ...Stages.stage_manifest import StageManifest
from ...Stages.stage_models import (
    StagePlanningError,
)
from .graph_walker import GraphWorkQueue, StageManifestGraphWalker
from .producers import ProducerSelector


@dataclass(frozen=True)
class TaskRequestGraph:
    """Concrete request-specific invocation graph before final fingerprints."""

    manifest_id: str
    invocations: tuple[PlannedInvocation, ...]
    dependencies_by_invocation: dict[str, tuple[str, ...]]
    dependents_by_invocation: dict[str, tuple[str, ...]]
    requested_output_bindings: tuple[RequestedOutputBinding, ...]

    @property
    def requested_output_ids(self) -> tuple[ArtifactKey, ...]:
        return tuple(binding.requested_artifact_id for binding in self.requested_output_bindings)


class TaskRequestGraphBuilder:
    def __init__(
        self,
        manifest: StageManifest,
        settings_resolver: PlannerSettingsResolver,
        producer_selector: ProducerSelector,
    ) -> None:
        self._manifest = manifest
        self._settings_resolver = settings_resolver
        self._producer_selector = producer_selector

    # Public API.

    async def build(
        self,
        *,
        context: TaskRequestContext,
    ) -> TaskRequestGraph:
        work_queue = GraphWorkQueue.from_requested_outputs(
            context.requested_output_ids,
            self._requested_output_requirement,
        )
        state = StageManifestGraphWalker(
            context,
            self._settings_resolver,
            self._producer_selector,
            work_queue,
        ).expand()

        return self._finalize_graph(state)

    def _finalize_graph(
        self,
        graph: GraphBuildState,
    ) -> TaskRequestGraph:
        ordered_candidate_keys = self._topological_stage_order(graph.invocations_by_key)
        invocation_ids_by_candidate = self._assign_invocation_ids(
            graph,
            ordered_candidate_keys,
        )

        invocations = tuple(
            self._planned_invocation_for_draft(
                invocation_ids_by_candidate[candidate_key],
                graph.invocations_by_key[candidate_key],
            )
            for candidate_key in ordered_candidate_keys
        )

        dependencies_by_invocation = self._dependencies_by_invocation(
            graph,
            invocation_ids_by_candidate,
        )
        dependents_by_invocation = self._dependents_by_invocation(
            dependencies_by_invocation
        )

        return TaskRequestGraph(
            manifest_id=self._manifest.manifest_id,
            invocations=invocations,
            dependencies_by_invocation=dependencies_by_invocation,
            dependents_by_invocation=dependents_by_invocation,
            requested_output_bindings=tuple(
                graph.requested_output_bindings[key]
                for key in sorted(graph.requested_output_bindings, key=str)
            ),
        )

    def _assign_invocation_ids(
        self,
        graph: GraphBuildState,
        ordered_candidate_keys: tuple[str, ...],
    ) -> dict[str, str]:
        return {
            candidate_key: (
                f"{graph.invocations_by_key[candidate_key].registration.stage_id}:"
                f"{sequence:03d}"
            )
            for sequence, candidate_key in enumerate(ordered_candidate_keys, start=1)
        }

    def _planned_invocation_for_draft(
        self,
        invocation_id: str,
        invocation: InvocationDraft,
    ) -> PlannedInvocation:
        assert invocation.resource_policy is not None
        input_bindings = tuple(
            sorted(
                invocation.input_bindings.values(),
                key=lambda item: (not item.required, str(item.selector)),
            )
        )
        return PlannedInvocation(
            invocation_id=invocation_id,
            registration=invocation.registration,
            input_bindings=input_bindings,
            requested_output_bindings=tuple(
                sorted(
                    invocation.output_bindings.values(),
                    key=lambda item: str(item.declared_artifact_id),
                )
            ),
            effective_settings_fingerprint=invocation.effective_settings_fingerprint,
            resolved_resource_policy=invocation.resource_policy,
            resolved_settings=invocation.resolved_settings,
        )

    def _dependencies_by_invocation(
        self,
        graph: GraphBuildState,
        invocation_ids_by_candidate: dict[str, str],
    ) -> dict[str, tuple[str, ...]]:
        return {
            invocation_ids_by_candidate[key]: tuple(
                sorted(
                    invocation_ids_by_candidate[dependency]
                    for dependency in candidate.dependency_keys()
                )
            )
            for key, candidate in graph.invocations_by_key.items()
        }

    def _dependents_by_invocation(
        self,
        dependencies_by_invocation: dict[str, tuple[str, ...]],
    ) -> dict[str, tuple[str, ...]]:
        return {
            invocation_id: tuple(
                sorted(
                    downstream_id
                    for downstream_id, dependencies in dependencies_by_invocation.items()
                    if invocation_id in dependencies
                )
            )
            for invocation_id in dependencies_by_invocation
        }

    def _topological_stage_order(
        self,
        invocations_by_key: dict[str, InvocationDraft],
    ) -> tuple[str, ...]:
        remaining = {
            key: set(invocation.dependency_keys())
            for key, invocation in invocations_by_key.items()
        }
        ordered: list[str] = []

        while remaining:
            ready = sorted(
                key for key, dependencies in remaining.items() if not dependencies
            )
            if not ready:
                rendered = "; ".join(
                    f"{key} <- {sorted(dependencies)}"
                    for key, dependencies in sorted(remaining.items())
                )
                raise StagePlanningError(
                    f"request-specific graph is cyclic or unresolved: {rendered}"
                )
            for key in ready:
                ordered.append(key)
                del remaining[key]
            for dependencies in remaining.values():
                dependencies.difference_update(ready)

        return tuple(ordered)

    # Requested output helpers.

    def _requested_output_requirement(
        self,
        output_id: ArtifactKey,
    ) -> ArtifactRequirement:
        concrete_phase_requested = (
            output_id.phase is not None
            and self._manifest.concrete_artifact_is_known(output_id)
        )
        return ArtifactRequirement(
            output_id,
            phase_match=(
                PhaseMatchMode.EXACT
                if concrete_phase_requested
                else PhaseMatchMode.AT_MOST
            ),
        )
