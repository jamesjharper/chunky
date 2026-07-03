from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ..Artifacts.artifact_models import ArtifactKey
from ..Artifacts.binding_models import ArtifactInstance
from ..Artifacts.fingerprints import FingerprintProvider
from ..Stages.stage_manifest import StageManifest
from .TaskRequestGraph.binding_models import PlannedOutputBinding, RequestedOutputBinding
from .TaskRequestGraph.invocation_models import PlannedInvocation
from .TaskRequestGraph.task_request_graph import TaskRequestGraph


@dataclass(frozen=True)
class TaskExecutionGraph:
    manifest_id: str
    invocations: tuple[PlannedInvocation, ...]
    requested_output_bindings: tuple[RequestedOutputBinding, ...]


class TaskExecutionGraphBuilder:
    def __init__(
        self,
        manifest: StageManifest,
        fingerprint_provider: FingerprintProvider,
    ) -> None:
        self._manifest = manifest
        self._fingerprint_provider = fingerprint_provider

    async def build(
        self,
        *,
        task_request_graph: TaskRequestGraph,
        supplied_instances: dict[ArtifactKey, ArtifactInstance[Any]],
    ) -> TaskExecutionGraph:
        fingerprints_by_instance = {
            str(artifact_id): instance.fingerprint
            for artifact_id, instance in supplied_instances.items()
        }

        invocations: list[PlannedInvocation] = []
        for invocation in task_request_graph.invocations:
            input_fingerprints: dict[str, str] = {}
            for binding in (
                *invocation.required_input_bindings,
                *invocation.optional_input_bindings,
            ):
                fingerprint = fingerprints_by_instance.get(binding.resolved_instance_id)
                if fingerprint is not None:
                    input_fingerprints[str(binding.selector)] = fingerprint

            pedigree = self._fingerprint_provider.build_stage_pedigree_fingerprint(
                stage_id=invocation.registration.stage_id,
                behavior_version=invocation.registration.behavior_version,
                input_fingerprints=input_fingerprints,
                effective_settings_fingerprint=invocation.effective_settings_fingerprint,
            )

            planned_outputs: list[PlannedOutputBinding] = []
            for binding in invocation.requested_output_bindings:
                declaration = self._manifest.find_output_declaration(
                    declared_output_id=binding.declared_artifact_id,
                    resolved_output_id=binding.resolved_artifact_id,
                )
                expected = await self._fingerprint_provider.build_intermediate_artifact_fingerprint(
                    declaration,
                    pedigree,
                )
                planned_outputs.append(
                    replace(binding, expected_fingerprint=expected)
                )
                fingerprints_by_instance[binding.instance_id] = expected

            invocations.append(
                replace(
                    invocation,
                    pedigree_fingerprint=pedigree,
                    requested_output_bindings=tuple(planned_outputs),
                )
            )

        return TaskExecutionGraph(
            manifest_id=task_request_graph.manifest_id,
            invocations=tuple(invocations),
            requested_output_bindings=task_request_graph.requested_output_bindings,
        )


__all__ = [
    "TaskExecutionGraph",
    "TaskExecutionGraphBuilder",
]
