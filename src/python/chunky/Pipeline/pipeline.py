from __future__ import annotations

"""Pipeline bootstrap facade that emits and owns one frozen stage manifest."""

import asyncio
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from ..Bindings.binding_models import ArtifactInstance
from ..Bindings.bindings import ArtifactBindingsProvider, close_instances
from ..Artifacts.artifact_handles import ArtifactSourceHandle
from ..Artifacts.artifact_models import (
    ArtifactDeclaration,
    ArtifactInstanceLocator,
    ArtifactKey,
    ArtifactRole,
)
from ..Bindings.cache_models import ArtifactCache
from ..Planning.execution_planner import TaskRunPlanner
from ..Planning.settings import PlannerDefaultSettingsResolver
from ..Scheduling.progress_models import (
    ProgressDispatcher,
    RunProgressTracker,
    TaskState,
)
from ..Scheduling.task_control import (
    RunError,
    RunExecutionError,
    RunHandle,
    RunResult,
    RunStatus,
)
from ..Scheduling.task_runner import ExampleTaskRunner, TaskRunResult
from .env_settings_features import (
    EnvironmentVariableRuntimeSettingSource,
    EnvironmentVariableRuntimeSettingSourceFeature,
    ExplicitRuntimeSettingsFeature,
    PipelineRuntimeSettings,
    PipelineRuntimeSettingsBuilder,
    RuntimeSettingSource,
    RuntimeSettingSourceFeature,
)
from .pipeline_models import (
    ArtifactCachePolicy,
    NormalizedRunRequest,
    RequestValidationError,
    RunRequest,
)
from .pipeline_builder import ChunkyPipelineBuilder
from .service_features import ArtifactCacheIO, FileCacheFeature, PipelineRuntimeServices
from ..Stages.stage_manifest import StageManifest
from ..Stages.stage_models import CancellationOrigin, CancellationSource


class ChunkyPipeline:
    def __init__(
        self,
        *,
        stage_manifest: StageManifest,
        runtime_services: PipelineRuntimeServices,
        runtime_settings: PipelineRuntimeSettings | None = None,
    ) -> None:
        if runtime_settings is None:
            runtime_settings = PipelineRuntimeSettingsBuilder().freeze()

        self.stage_manifest = stage_manifest
        self._runtime_services = runtime_services
        self._runtime_settings = runtime_settings
        self._artifact_cache = runtime_services.artifact_cache
        self._artifact_bindings_provider_factory = (
            runtime_services.artifact_bindings_provider_factory
        )
        self._planner_factory = runtime_services.planner_factory
        self._runner_factory = runtime_services.runner_factory
        self._artifact_bindings_provider = self._build_artifact_bindings_provider(
            self._artifact_cache
        )
        self._planner = self._build_planner(self._artifact_bindings_provider)
        self._runner = self._build_runner(self._artifact_bindings_provider)
        self._runtime_services_ready = False
        self._runtime_services_ready_lock = asyncio.Lock()

    @classmethod
    def builder(
        cls,
    ) -> ChunkyPipelineBuilder:
        return ChunkyPipelineBuilder()

    @property
    def runtime_settings(self) -> PipelineRuntimeSettings:
        return self._runtime_settings

    @property
    def runtime_services(self) -> PipelineRuntimeServices:
        return self._runtime_services

    @property
    def cache_io(self) -> ArtifactCacheIO:
        return ArtifactCacheIO(self)

    async def run(
        self,
        request: RunRequest,
        *,
        cancellation: CancellationSource | None = None,
    ) -> TaskRunResult:
        """Run a request to terminal completion using the task-control path.

        The public synchronous/awaiting surface is deliberately a thin unwrap of
        the same in-process task controller used by ``run_away``. Successful
        completion returns the typed task output; any non-success terminal result
        is surfaced as ``RunExecutionError`` with the structured result attached.
        """

        handle = self._start_run(request, cancellation=cancellation)
        result = await handle.await_to_completion()
        if result.status is RunStatus.COMPLETED and result.output is not None:
            return result.output
        raise RunExecutionError(result)

    def run_away(self, request: RunRequest) -> RunHandle[TaskRunResult]:
        return self._start_run(request, cancellation=CancellationSource())

    def _start_run(
        self,
        request: RunRequest,
        *,
        cancellation: CancellationSource | None = None,
    ) -> RunHandle[TaskRunResult]:
        normalized_request = request.normalize(self.stage_manifest)
        task_id = f"task-{uuid4().hex[:12]}"
        cancellation = cancellation or CancellationSource()
        dispatcher = ProgressDispatcher()
        tracker = RunProgressTracker(task_id, dispatcher)

        async def complete() -> RunResult[TaskRunResult]:
            try:
                output = await self._execute(
                    request,
                    normalized_request=normalized_request,
                    task_id=task_id,
                    cancellation=cancellation,
                    progress_tracker=tracker,
                )
                tracker.set_task_state(TaskState.COMPLETED)
                return RunResult(
                    task_id=task_id,
                    status=RunStatus.COMPLETED,
                    output=output,
                )
            except BaseException as exc:
                if isinstance(exc, RunExecutionError):
                    self._sync_tracker_to_result(tracker, exc.result)
                    return exc.result
                if cancellation.is_cancelled:
                    status = (
                        RunStatus.SHUTDOWN_INTERRUPTED
                        if cancellation.origin is CancellationOrigin.SHUTDOWN
                        else RunStatus.CANCELLED
                    )
                    tracker.set_task_state(TaskState.CANCELLED)
                    return RunResult(
                        task_id=task_id,
                        status=status,
                        errors=(RunError(status.value, str(exc)),),
                    )
                tracker.set_task_state(TaskState.FAILED)
                return RunResult(
                    task_id=task_id,
                    status=RunStatus.FAILED,
                    errors=(RunError(type(exc).__name__, str(exc)),),
                )

        completion = asyncio.create_task(complete())
        return RunHandle(
            task_id=task_id,
            cancellation=cancellation,
            tracker=tracker,
            dispatcher=dispatcher,
            completion=completion,
        )

    @staticmethod
    def _sync_tracker_to_result(
        tracker: RunProgressTracker,
        result: RunResult[TaskRunResult],
    ) -> None:
        if result.status is RunStatus.COMPLETED:
            tracker.set_task_state(TaskState.COMPLETED)
        elif result.status in (RunStatus.CANCELLED, RunStatus.SHUTDOWN_INTERRUPTED):
            tracker.set_task_state(TaskState.CANCELLED)
        else:
            tracker.set_task_state(TaskState.FAILED)

    async def _execute(
        self,
        request: RunRequest,
        *,
        normalized_request: NormalizedRunRequest,
        task_id: str,
        cancellation: CancellationSource,
        progress_tracker: RunProgressTracker,
    ) -> TaskRunResult:
        await self._ensure_runtime_services_ready()
        input_roots = normalized_request.input_sources
        output_targets = normalized_request.output_targets
        cache_policy = normalized_request.cache_policy

        task_cache = self._cache_for_task(task_id)
        artifact_bindings_provider = self._build_artifact_bindings_provider(task_cache)
        planner = self._build_planner(artifact_bindings_provider)
        runner = self._build_runner(artifact_bindings_provider)
        supplied: dict[ArtifactKey, ArtifactInstance[Any]] = {}

        try:
            supplied, user_supplied_settings = await self._bind_supplied_inputs(
                artifact_bindings_provider,
                input_roots,
                cache_policy,
            )

            plan = await planner.plan(
                supplied_instances=supplied,
                requested_outputs=set(output_targets),
                run_policy=request.run_policy,
                user_supplied_settings=user_supplied_settings,
                default_settings_resolver=PlannerDefaultSettingsResolver(
                    self.stage_manifest,
                    artifact_bindings_provider,
                ),
                task_id=task_id,
            )
            self._prepare_runtime_settings(plan)
            self._validate_cache_policy_reachability(cache_policy, plan)
            self._register_plan_progress(progress_tracker, plan)
        except BaseException:
            await close_instances(tuple(supplied.values()))
            raise

        return await runner.run(
            plan=plan,
            supplied_instances=supplied,
            output_targets=output_targets,
            runtime_settings=self._runtime_settings,
            cache_policy=cache_policy,
            cancellation=cancellation,
            progress_tracker=progress_tracker,
            task_id=task_id,
        )

    def _cache_for_task(self, task_id: str) -> ArtifactCache:
        for_task = getattr(self._artifact_cache, "for_task", None)
        if callable(for_task):
            return for_task(task_id)
        return self._artifact_cache

    async def _ensure_runtime_services_ready(self) -> None:
        if self._runtime_services_ready:
            return
        async with self._runtime_services_ready_lock:
            if self._runtime_services_ready:
                return
            recover = getattr(self._artifact_cache, "recover", None)
            if callable(recover):
                await recover()
            self._runtime_services_ready = True

    def _validate_cache_policy_reachability(
        self,
        cache_policy: Mapping[ArtifactKey, ArtifactCachePolicy],
        plan: Any,
    ) -> None:
        if not cache_policy:
            return
        planned: set[ArtifactKey] = set()
        for invocation in plan.invocations:
            for binding in invocation.input_bindings:
                planned.add(binding.resolved_artifact_id)
            for binding in invocation.requested_output_bindings:
                planned.add(binding.resolved_artifact_id)
        for binding in plan.requested_output_bindings:
            planned.add(binding.requested_artifact_id)
        unreachable = sorted((artifact_id for artifact_id in cache_policy if artifact_id not in planned), key=str)
        if unreachable:
            # Accepted policy: known but unreachable policy entries are diagnostic-only.
            # The executable example has no diagnostics sink yet, so this is intentionally non-fatal.
            return

    def _build_artifact_bindings_provider(
        self,
        cache: ArtifactCache,
    ) -> ArtifactBindingsProvider:
        return self._artifact_bindings_provider_factory(cache)

    def _build_planner(
        self,
        artifact_bindings_provider: ArtifactBindingsProvider,
    ) -> TaskRunPlanner:
        return self._planner_factory(self.stage_manifest, artifact_bindings_provider)

    def _build_runner(
        self,
        artifact_bindings_provider: ArtifactBindingsProvider,
    ) -> ExampleTaskRunner:
        return self._runner_factory(self.stage_manifest, artifact_bindings_provider)

    async def _bind_supplied_inputs(
        self,
        artifact_bindings_provider: ArtifactBindingsProvider,
        input_roots: Mapping[ArtifactKey, ArtifactSourceHandle[Any] | ArtifactInstanceLocator],
        cache_policy: Mapping[ArtifactKey, ArtifactCachePolicy] | None = None,
    ) -> tuple[dict[ArtifactKey, ArtifactInstance[Any]], set[ArtifactKey]]:
        supplied: dict[ArtifactKey, ArtifactInstance[Any]] = {}
        user_supplied_settings: set[ArtifactKey] = set()

        for artifact_id, source_or_locator in input_roots.items():
            declaration = self.stage_manifest.find_artifact(artifact_id)
            if declaration.role is ArtifactRole.SETTINGS:
                user_supplied_settings.add(artifact_id)

            if isinstance(source_or_locator, ArtifactInstanceLocator):
                supplied[artifact_id] = await artifact_bindings_provider.open_instance_locator(
                    declaration,
                    source_or_locator,
                )
                continue

            effective_source = await self._resolve_settings_source(
                declaration,
                source_or_locator,
            )

            supplied[artifact_id] = await artifact_bindings_provider.bind_supplied(
                declaration,
                effective_source,
                cache_policy=(cache_policy or {}).get(artifact_id),
            )
        return supplied, user_supplied_settings

    async def _resolve_settings_source(
        self,
        declaration: ArtifactDeclaration[Any],
        source: ArtifactSourceHandle[Any] | None,
    ) -> ArtifactSourceHandle[Any]:
        if (
            declaration.role is not ArtifactRole.SETTINGS
            or declaration.default_value_source_provider is None
        ):
            if source is None:
                raise ValueError(
                    f"missing source for artifact without defaults: {declaration.artifact_id}"
                )
            return source
        return await declaration.default_value_source_provider.resolve_source(
            source,
            declaration=declaration,
        )

    def _prepare_runtime_settings(self, plan: "TaskRunGraph") -> None:
        self._runtime_settings = self._runtime_settings.resolve_for_stages(
            invocation.registration.stage_id for invocation in plan.invocations
        )

    @staticmethod
    def _register_plan_progress(
        progress_tracker: RunProgressTracker,
        plan: Any,
    ) -> None:
        with progress_tracker.batch_update():
            for invocation in plan.invocations:
                progress_tracker.register_stage(
                    invocation.invocation_id,
                    invocation.registration.stage_id,
                    invocation.registration.display_name,
                )

def chunky_run_away(
    request: RunRequest,
    *,
    pipeline: ChunkyPipeline,
) -> RunHandle[TaskRunResult]:
    return pipeline.run_away(request)


async def chunky_run(
    request: RunRequest,
    *,
    pipeline: ChunkyPipeline,
) -> TaskRunResult:
    return await pipeline.run(request)


def _artifact_key(value: ArtifactKey | str) -> ArtifactKey:
    return value if isinstance(value, ArtifactKey) else ArtifactKey.parse(value)
