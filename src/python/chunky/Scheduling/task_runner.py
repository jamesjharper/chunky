from __future__ import annotations

"""Minimal runner demonstrating the scheduler/stage/cache responsibility seam."""

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from ..Artifacts.binding_models import (
    ArtifactInstance,
    InputArtifacts,
    OutputArtifacts,
    ResolvedStageOutputBindings,
    StageCacheWait,
)
from ..Artifacts.bindings import ArtifactBindingsProvider, abort_writers, close_instances
from ..Artifacts.artifact_handles import ArtifactTargetHandle
from ..Artifacts.artifact_models import ArtifactKey
from ..Stages.stage_manifest import StageManifest
from ..Stages.stage_models import (
    CancellationSource,
    InMemoryStageProgressReporter,
    StageExecutionContext,
    build_stage_logger,
)
from ..Planning.task_run_graph import TaskRunGraph
from ..Planning.TaskRequestGraph.invocation_models import RunnableInvocation
from .progress_models import (
    RunProgressTracker,
    StageBlockedReason,
    TaskState,
)


@dataclass(frozen=True)
class InvocationRunResult:
    instances: tuple[ArtifactInstance[Any], ...]
    disposition: Literal["executed", "no_operation_cache_hit"]
    waited_for_writer: bool


@dataclass(frozen=True)
class TaskRunResult:
    task_id: str
    output_fingerprints: dict[ArtifactKey, str]
    events: tuple[str, ...]


class ArtifactRunState:
    def __init__(
        self,
        supplied: dict[ArtifactKey, ArtifactInstance[Any]],
    ) -> None:
        self._instances = {
            str(artifact_id): instance
            for artifact_id, instance in supplied.items()
        }

    def provide(self, instance_id: str, instance: ArtifactInstance[Any]) -> None:
        self._instances[instance_id] = instance

    def resolve_inputs(
        self,
        invocation: RunnableInvocation,
    ) -> tuple[tuple[ArtifactInstance[Any], ...], dict[ArtifactKey, ArtifactKey]]:
        result: list[ArtifactInstance[Any]] = []
        aliases: dict[ArtifactKey, ArtifactKey] = {}
        seen: set[ArtifactKey] = set()

        for binding in invocation.input_bindings:
            try:
                instance = self._instances[binding.resolved_instance_id]
            except KeyError as exc:
                raise KeyError(
                    f"planned artifact is not available for "
                    f"{invocation.invocation_id}: {binding.resolved_artifact_id}"
                ) from exc
            aliases[binding.selector] = binding.resolved_artifact_id
            if binding.resolved_artifact_id not in seen:
                result.append(instance)
                seen.add(binding.resolved_artifact_id)

        return tuple(result), aliases

    def require(self, instance_id: str) -> ArtifactInstance[Any]:
        try:
            return self._instances[instance_id]
        except KeyError as exc:
            raise KeyError(f"artifact instance is not available: {instance_id}") from exc

    def all_instances(self) -> tuple[ArtifactInstance[Any], ...]:
        return tuple(self._instances.values())


class ExampleTaskRunner:
    def __init__(
        self,
        *,
        manifest: StageManifest,
        artifact_bindings_provider: ArtifactBindingsProvider,
    ) -> None:
        self._manifest = manifest
        self._artifact_bindings_provider = artifact_bindings_provider

    async def run(
        self,
        *,
        plan: TaskRunGraph,
        supplied_instances: dict[ArtifactKey, ArtifactInstance[Any]],
        output_targets: dict[ArtifactKey, ArtifactTargetHandle[Any]],
        cancellation: CancellationSource | None = None,
        progress_tracker: RunProgressTracker,
        task_id: str | None = None,
    ) -> TaskRunResult:
        if plan.manifest_id != self._manifest.manifest_id:
            raise ValueError("task plan was created from a different stage manifest")

        cancellation = cancellation or CancellationSource()
        task_id = task_id or f"task-{uuid4().hex[:12]}"
        state = ArtifactRunState(supplied_instances)
        events: list[str] = []

        try:
            progress_tracker.set_task_state(TaskState.RUNNING)

            for invocation in plan.invocations:
                run = await self._run_invocation(
                    task_id=task_id,
                    state=state,
                    invocation=invocation,
                    cancellation=cancellation,
                    progress_tracker=progress_tracker,
                )

                # pull out all the output artifacts from this stage invocation
                bindings_by_artifact = {
                    binding.resolved_artifact_id: binding
                    for binding in invocation.requested_output_bindings
                }

                for instance in run.instances:
                    artifact_id = instance.handle.declaration.artifact_id
                    try:
                        binding = bindings_by_artifact[artifact_id]
                    except KeyError as exc:
                        raise RuntimeError(
                            "runner received an unplanned output instance: "
                            f"{artifact_id}"
                        ) from exc
                    state.provide(binding.instance_id, instance)
                if run.waited_for_writer:
                    events.append(
                        f"{invocation.registration.stage_id} waited for an "
                        "in-flight cache writer"
                    )
                if run.disposition == "no_operation_cache_hit":
                    events.append(
                        f"{invocation.registration.stage_id} skipped: cache hit"
                    )
                else:
                    events.append(f"{invocation.registration.stage_id} executed")
                for binding in invocation.requested_output_bindings:
                    if binding.terminal_remapped:
                        events.append(
                            "terminal remap "
                            f"{binding.declared_artifact_id} -> {binding.resolved_artifact_id}"
                        )

            output_fingerprints: dict[ArtifactKey, str] = {}
            for requested in plan.requested_output_bindings:
                artifact_id = requested.requested_artifact_id
                cancellation.raise_if_cancelled()
                instance = state.require(requested.instance_id)
                try:
                    target = output_targets[artifact_id]
                except KeyError as exc:
                    raise KeyError(
                        f"no caller materialization target supplied for {artifact_id}"
                    ) from exc
                await self._artifact_bindings_provider.materialize(instance, target)
                output_fingerprints[artifact_id] = instance.fingerprint
                events.append(f"materialized {artifact_id}")

            progress_tracker.set_task_state(TaskState.COMPLETED)
            return TaskRunResult(
                task_id=task_id,
                output_fingerprints=output_fingerprints,
                events=tuple(events),
            )
        except BaseException:
            terminal = (
                TaskState.CANCELLED
                if cancellation.is_cancelled
                else TaskState.FAILED
            )
            progress_tracker.set_task_state(terminal)
            raise
        finally:
            await close_instances(state.all_instances())

    async def _run_invocation(
        self,
        *,
        task_id: str,
        state: ArtifactRunState,
        invocation: RunnableInvocation,
        cancellation: CancellationSource,
        progress_tracker: RunProgressTracker,
    ) -> InvocationRunResult:
        progress_tracker.mark_running(invocation.invocation_id)
        input_instances, input_aliases = state.resolve_inputs(invocation)
        pedigree_fingerprint = invocation.pedigree_fingerprint
        if pedigree_fingerprint is None:
            raise RuntimeError(
                "execution plan is missing planner-finalized pedigree fingerprint "
                f"for {invocation.invocation_id}"
            )
        output_declarations = [
            self._manifest.find_output_declaration(
                declared_output_id=binding.declared_artifact_id,
                resolved_output_id=binding.resolved_artifact_id,
            )
            for binding in invocation.requested_output_bindings
        ]

        waited_for_writer = False
        while True:
            cancellation.raise_if_cancelled()
            resolution = await self._artifact_bindings_provider.bind_stage_outputs(
                output_declarations,
                pedigree_fingerprint,
            )
            if isinstance(resolution, StageCacheWait):
                waited_for_writer = True
                progress_tracker.mark_blocked(
                    invocation.invocation_id,
                    StageBlockedReason.WAITING_FOR_OTHER_TASK,
                    (
                        "waiting for another task to release cache write "
                        f"reservation for {resolution.artifact_id}"
                    ),
                    blocking_task_id=resolution.cache_wait.owner_task_id,
                )
                await cancellation.wait_for(
                    resolution.cache_wait.wait_handle.wait()
                )
                progress_tracker.clear_blocked(invocation.invocation_id)
                continue
            if not isinstance(resolution, ResolvedStageOutputBindings):
                raise TypeError(f"unsupported output binding: {resolution!r}")
            break

        expected = {
            binding.resolved_artifact_id: binding.expected_fingerprint
            for binding in invocation.requested_output_bindings
        }
        for instance in resolution.available:
            planned = expected.get(instance.handle.declaration.artifact_id)
            if planned is not None and instance.fingerprint != planned:
                raise RuntimeError(
                    "cache binding fingerprint disagrees with execution plan for "
                    f"{instance.handle.declaration.artifact_id}"
                )
        for writer in resolution.writers:
            planned = expected.get(writer.handle.declaration.artifact_id)
            if planned is not None and writer.handle.fingerprint != planned:
                raise RuntimeError(
                    "writer fingerprint disagrees with execution plan for "
                    f"{writer.handle.declaration.artifact_id}"
                )

        if not resolution.writers:
            progress_tracker.mark_finished(invocation.invocation_id)
            return InvocationRunResult(
                instances=resolution.available,
                disposition="no_operation_cache_hit",
                waited_for_writer=waited_for_writer,
            )

        output_aliases = {
            binding.declared_artifact_id: binding.resolved_artifact_id
            for binding in invocation.requested_output_bindings
        }
        outputs = OutputArtifacts.from_writers(
            resolution.writers,
            aliases=output_aliases,
        )
        inputs = InputArtifacts.from_instances(
            input_instances,
            aliases=input_aliases,
        )
        progress = InMemoryStageProgressReporter()

        with tempfile.TemporaryDirectory(
            prefix=f"chunky-{invocation.registration.stage_id.replace('.', '-')}-"
        ) as workspace:
            context = StageExecutionContext(
                task_id=task_id,
                invocation_id=invocation.invocation_id,
                stage_id=invocation.registration.stage_id,
                cancellation=cancellation.token,
                progress=progress,
                logger=build_stage_logger(
                    task_id,
                    invocation.invocation_id,
                    invocation.registration.stage_id,
                ),
                settings=invocation.resolved_settings,
                scratch_workspace=Path(workspace),
            )
            executor = invocation.registration.executor_factory()
            try:
                await executor.execute(inputs, outputs, context)
                produced = outputs.produced_instances()
            except BaseException:
                await abort_writers(resolution.writers)
                if cancellation.is_cancelled:
                    progress_tracker.mark_cancelled(invocation.invocation_id)
                else:
                    progress_tracker.mark_failed(invocation.invocation_id)
                raise
            finally:
                progress.clear()

        instances = self._artifact_bindings_provider.merge_stage_output_instances(
            resolution.available,
            produced,
        )
        progress_tracker.mark_finished(invocation.invocation_id)
        return InvocationRunResult(
            instances=instances,
            disposition="executed",
            waited_for_writer=waited_for_writer,
        )
