from __future__ import annotations

"""Request-specific graph creation over a frozen stage manifest.

``ExecutionPlanner`` is now intentionally small.  It orchestrates the four planning
steps and delegates the details to focused collaborators:

1. request scoping and manifest-plan capture;
2. evaluated graph expansion;
3. producer/root selection;
4. task execution graph construction.

Runtime cache lookup, write reservations, waits, and materialization remain in
the runner/artifact-binding seam.
"""

from typing import Any

from ..Artifacts.binding_models import ArtifactInstance
from ..Artifacts.artifact_models import ArtifactKey
from ..Artifacts.fingerprints import FingerprintProvider
from .TaskRequestGraph import (
    PlannedInputBinding,
    PlannedOutputBinding,
    PlannedInvocation,
    PlannerRequestSettings,
    RequestedOutputBinding,
    TaskRequestContext,
    TaskRequestGraph,
    TaskRequestGraphBuilder,
)
from .TaskRequestGraph import PlannerSettingsResolver, ProducerSelector
from .TaskExecutionGraph import TaskExecutionGraphBuilder
from .TaskExecutionGraph import TaskExecutionGraph
from .manifest_graph import StageManifestGraphBuilder
from ..Stages.stage_manifest import StageManifest
from ..Stages.stage_models import PlanningRunPolicy, StagePlanningError

class ExecutionPlanner:
    def __init__(
        self,
        manifest: StageManifest,
        fingerprint_provider: FingerprintProvider | None = None,
    ) -> None:
        self._manifest = manifest
        self._fingerprint_provider = fingerprint_provider or FingerprintProvider()
        self._manifest_graph_builder = StageManifestGraphBuilder(manifest)
        self._settings_resolver = PlannerSettingsResolver(manifest)
        self._producer_selector = ProducerSelector(manifest)
        self._task_request_graph_builder = TaskRequestGraphBuilder(
            manifest,
            self._settings_resolver,
            self._producer_selector,
        )
        self._task_execution_graph_builder = TaskExecutionGraphBuilder(
            manifest,
            self._fingerprint_provider,
        )
        self._manifest_graph = self._manifest_graph_builder.build()

    async def plan(
        self,
        *,
        supplied_instances: dict[ArtifactKey, ArtifactInstance[Any]],
        requested_outputs: set[ArtifactKey],
        run_policy: PlanningRunPolicy | None = None,
        user_supplied_settings: frozenset[ArtifactKey] = frozenset(),
    ) -> TaskExecutionGraph:
        
        self._validate_requested_outputs(requested_outputs)

        request_settings = await PlannerRequestSettings.read_from(
            self._manifest,
            supplied_instances,
            user_supplied_settings=user_supplied_settings,
        )
        context = TaskRequestContext(
            manifest=self._manifest,
            manifest_graph=self._manifest_graph,
            requested_output_ids=tuple(sorted(requested_outputs, key=str)),
            supplied_instances=supplied_instances,
            policy=run_policy or PlanningRunPolicy(),
            request_settings=request_settings,
        )
        task_request_graph = await self._task_request_graph_builder.build(
            context=context,
        )
        return await self._task_execution_graph_builder.build(
            task_request_graph=task_request_graph,
            supplied_instances=supplied_instances,
        )

    def _validate_requested_outputs(self, requested_outputs: set[ArtifactKey]) -> None:
        if not requested_outputs:
            raise StagePlanningError("at least one output must be requested")
        for artifact_id in requested_outputs:
            if not self._manifest.phase_catalog.requestable_output_exists(artifact_id):
                raise StagePlanningError(
                    f"Configured stages do not produce artifact {artifact_id}"
                )


__all__ = [
    "ExecutionPlanner",
    "PlannedInputBinding",
    "PlannedOutputBinding",
    "PlannedInvocation",
    "TaskRequestContext",
    "RequestedOutputBinding",
    "TaskRequestGraph",
    "TaskExecutionGraph",
]
