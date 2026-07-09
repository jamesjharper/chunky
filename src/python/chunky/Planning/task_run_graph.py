from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ..Artifacts.artifact_models import ArtifactInstanceLocator, ArtifactKey, TaskArtifactLocator
from ..Bindings.binding_models import ArtifactInstance
from ..Artifacts.fingerprints import FingerprintProvider
from ..Stages.stage_manifest import StageManifest
from .TaskRequestGraph.binding_models import PlannedInputBinding, PlannedOutputBinding, TaskOutputBinding
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
        task_id: str | None = None,
    ) -> TaskRunGraph:
        # Provisional graph expansion can refer to supplied artifacts by their
        # semantic a-ref. Planning finalization rewrites every binding it can to
        # a concrete a-instance locator using the final artifact fingerprint.
        fingerprints_by_instance: dict[str, str] = {}
        final_instance_ids_by_provisional: dict[str, str] = {}

        for artifact_id, instance in supplied_instances.items():
            provisional = str(artifact_id)
            concrete = str(ArtifactInstanceLocator.from_key(artifact_id, instance.fingerprint))
            fingerprints_by_instance[provisional] = instance.fingerprint
            fingerprints_by_instance[concrete] = instance.fingerprint
            final_instance_ids_by_provisional[provisional] = concrete

        invocations: list[RunnableInvocation] = []
        for invocation in task_request_graph.invocations:
            finalized_inputs: list[PlannedInputBinding] = []
            input_fingerprints: dict[str, str] = {}
            for binding in invocation.input_bindings:
                final_input_id = final_instance_ids_by_provisional.get(
                    binding.resolved_instance_id,
                    binding.resolved_instance_id,
                )
                fingerprint = fingerprints_by_instance.get(final_input_id)
                if fingerprint is None:
                    fingerprint = fingerprints_by_instance.get(binding.resolved_instance_id)
                if fingerprint is not None:
                    input_fingerprints[str(binding.selector)] = fingerprint
                finalized_inputs.append(
                    replace(binding, resolved_instance_id=final_input_id)
                )

            pedigree = self._fingerprint_provider.build_stage_pedigree_fingerprint(
                stage_fingerprint=invocation.stage_fingerprint,
                input_fingerprints=input_fingerprints,
                settings_fingerprint=invocation.effective_settings_fingerprint,
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
                concrete_instance_id = str(
                    ArtifactInstanceLocator.from_key(
                        binding.resolved_artifact_id,
                        expected,
                    )
                )
                planned_outputs.append(
                    PlannedOutputBinding(
                        declared_artifact_id=binding.declared_artifact_id,
                        resolved_artifact_id=binding.resolved_artifact_id,
                        instance_id=concrete_instance_id,
                        expected_fingerprint=expected,
                    )
                )
                final_instance_ids_by_provisional[binding.instance_id] = concrete_instance_id
                fingerprints_by_instance[concrete_instance_id] = expected

            invocations.append(
                RunnableInvocation(
                    invocation_id=invocation.invocation_id,
                    registration=invocation.registration,
                    input_bindings=tuple(finalized_inputs),
                    pedigree_fingerprint=pedigree,
                    requested_output_bindings=tuple(planned_outputs),
                    resolved_resource_policy=invocation.resolved_resource_policy,
                    resolved_settings=invocation.resolved_settings,
                )
            )

        requested_bindings: list[TaskOutputBinding] = []
        for binding in task_request_graph.requested_output_bindings:
            final_instance_id = final_instance_ids_by_provisional.get(
                binding.instance_id,
                binding.instance_id,
            )
            requested_bindings.append(
                TaskOutputBinding(
                    requested_artifact_id=binding.requested_artifact_id,
                    instance_id=final_instance_id,
                    task_locator=(
                        TaskArtifactLocator.from_key(
                            binding.requested_artifact_id,
                            task_id,
                        )
                        if task_id is not None
                        else None
                    ),
                )
            )

        return TaskRunGraph(
            manifest_id=task_request_graph.manifest_id,
            invocations=tuple(invocations),
            dependencies_by_invocation=dict(task_request_graph.dependencies_by_invocation),
            dependents_by_invocation=dict(task_request_graph.dependents_by_invocation),
            requested_output_bindings=tuple(requested_bindings),
        )


__all__ = [
    "TaskRunGraph",
    "TaskRunGraphBuilder",
    "TaskOutputBinding",
]
