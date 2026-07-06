from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..Artifacts.artifact_models import ArtifactKey
from ..Artifacts.binding_models import ArtifactInstance
from ..Artifacts.fingerprints import FingerprintProvider
from ..Stages.stage_manifest import StageManifest
from .TaskRequestGraph.binding_models import PlannedOutputBinding, TaskOutputBinding
from .TaskRequestGraph.invocation_models import RunnableInvocation
from .TaskRequestGraph.task_request_graph import TaskRequestGraph


@dataclass(frozen=True)
class TaskRunGraph:
    manifest_id: str
    invocations: tuple[RunnableInvocation, ...]
    dependencies_by_invocation: dict[str, tuple[str, ...]]
    dependents_by_invocation: dict[str, tuple[str, ...]]
    requested_output_bindings: tuple[TaskOutputBinding, ...]


class TaskRunGraphBuilder:
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
    ) -> TaskRunGraph:
        fingerprints_by_instance = {
            str(artifact_id): instance.fingerprint
            for artifact_id, instance in supplied_instances.items()
        }

        invocations: list[RunnableInvocation] = []
        for invocation in task_request_graph.invocations:
            input_fingerprints: dict[str, str] = {}
            for binding in invocation.input_bindings:
                fingerprint = fingerprints_by_instance.get(binding.resolved_instance_id)
                if fingerprint is not None:
                    input_fingerprints[str(binding.selector)] = fingerprint

            pedigree = self._fingerprint_provider.build_stage_pedigree_fingerprint(
                stage_fingerprint=invocation.stage_fingerprint,
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
                    PlannedOutputBinding(
                        declared_artifact_id=binding.declared_artifact_id,
                        resolved_artifact_id=binding.resolved_artifact_id,
                        instance_id=binding.instance_id,
                        expected_fingerprint=expected,
                    )
                )
                fingerprints_by_instance[binding.instance_id] = expected

            invocations.append(
                RunnableInvocation(
                    invocation_id=invocation.invocation_id,
                    registration=invocation.registration,
                    input_bindings=invocation.input_bindings,
                    pedigree_fingerprint=pedigree,
                    requested_output_bindings=tuple(planned_outputs),
                    resolved_resource_policy=invocation.resolved_resource_policy,
                    resolved_settings=invocation.resolved_settings,
                )
            )

        return TaskRunGraph(
            manifest_id=task_request_graph.manifest_id,
            invocations=tuple(invocations),
            dependencies_by_invocation=dict(task_request_graph.dependencies_by_invocation),
            dependents_by_invocation=dict(task_request_graph.dependents_by_invocation),
            requested_output_bindings=tuple(
                TaskOutputBinding(
                    requested_artifact_id=binding.requested_artifact_id,
                    instance_id=binding.instance_id,
                )
                for binding in task_request_graph.requested_output_bindings
            ),
        )


__all__ = [
    "TaskRunGraph",
    "TaskRunGraphBuilder",
    "TaskOutputBinding",
]
