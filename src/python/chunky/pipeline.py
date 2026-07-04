from __future__ import annotations

"""Pipeline bootstrap facade that emits and owns one frozen stage manifest."""

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .Artifacts.binding_models import ArtifactInstance
from .Artifacts.bindings import ArtifactBindingsProvider, close_instances
from .Artifacts.artifact_handles import ArtifactSourceHandle, ArtifactTargetHandle
from .Artifacts.artifact_models import ArtifactDeclaration, ArtifactKey, ArtifactRole
from .CacheProviders.cache_models import ArtifactCache
from .Planning.execution_planner import TaskRunPlanner
from .Scheduling.progress_models import (
    ProgressDispatcher,
    RunProgressTracker,
    ensure_progress_tracker,
)
from .Scheduling.task_control import (
    RunError,
    RunHandle,
    RunResult,
    RunStatus,
)
from .Scheduling.task_runner import ExampleTaskRunner, TaskRunResult
from .Stages.stage_manifest import StageManifest, StageManifestBuilder
from .Stages.stage_models import CancellationSource, PipelineFeature, PlanningRunPolicy


@dataclass(frozen=True)
class RunRequest:
    inputs: Mapping[
        ArtifactKey | str,
        ArtifactSourceHandle[Any] | Mapping[str, ArtifactSourceHandle[Any]],
    ]
    outputs: Mapping[
        ArtifactKey | str,
        ArtifactTargetHandle[Any] | Mapping[str, ArtifactTargetHandle[Any]],
    ]
    run_policy: PlanningRunPolicy | None = None


ArtifactBindingsProviderFactory = Callable[[ArtifactCache], ArtifactBindingsProvider]
PlannerFactory = Callable[[StageManifest, ArtifactBindingsProvider], TaskRunPlanner]
RunnerFactory = Callable[[StageManifest, ArtifactBindingsProvider], ExampleTaskRunner]


class ChunkyPipelineBuilder:
    def __init__(self, *, artifact_cache: ArtifactCache) -> None:
        self._artifact_cache = artifact_cache
        self._manifest_builder = StageManifestBuilder()

    def install(self, feature: PipelineFeature) -> "ChunkyPipelineBuilder":
        self._manifest_builder.install_feature(feature)
        return self

    def build(self) -> "ChunkyPipeline":
        manifest = self._manifest_builder.freeze()
        return ChunkyPipeline(
            stage_manifest=manifest,
            artifact_cache=self._artifact_cache,
        )


class ChunkyPipeline:
    def __init__(
        self,
        *,
        stage_manifest: StageManifest,
        artifact_cache: ArtifactCache,
        artifact_bindings_provider_factory: ArtifactBindingsProviderFactory | None = None,
        planner_factory: PlannerFactory | None = None,
        runner_factory: RunnerFactory | None = None,
    ) -> None:
        self.stage_manifest = stage_manifest
        self._artifact_cache = artifact_cache
        self._artifact_bindings_provider_factory = (
            artifact_bindings_provider_factory
            or self._default_artifact_bindings_provider_factory
        )
        self._planner_factory = planner_factory or self._default_planner_factory
        self._runner_factory = runner_factory or self._default_runner_factory
        self._artifact_bindings_provider = self._build_artifact_bindings_provider(
            artifact_cache
        )
        self._planner = self._build_planner(self._artifact_bindings_provider)
        self._runner = self._build_runner(self._artifact_bindings_provider)

    @classmethod
    def builder(
        cls,
        *,
        artifact_cache: ArtifactCache,
    ) -> ChunkyPipelineBuilder:
        return ChunkyPipelineBuilder(artifact_cache=artifact_cache)

    async def run(
        self,
        request: RunRequest,
        *,
        cancellation: CancellationSource | None = None,
    ) -> TaskRunResult:
        task_id = f"task-{uuid4().hex[:12]}"
        return await self._execute(
            request,
            task_id=task_id,
            cancellation=cancellation or CancellationSource(),
            progress_tracker=ensure_progress_tracker(task_id, None),
        )

    def run_away(self, request: RunRequest) -> RunHandle[TaskRunResult]:
        task_id = f"task-{uuid4().hex[:12]}"
        cancellation = CancellationSource()
        dispatcher = ProgressDispatcher()
        tracker = RunProgressTracker(task_id, dispatcher)

        async def complete() -> RunResult[TaskRunResult]:
            try:
                output = await self._execute(
                    request,
                    task_id=task_id,
                    cancellation=cancellation,
                    progress_tracker=tracker,
                )
                return RunResult(
                    task_id=task_id,
                    status=RunStatus.COMPLETED,
                    output=output,
                )
            except BaseException as exc:
                if cancellation.is_cancelled:
                    return RunResult(
                        task_id=task_id,
                        status=RunStatus.CANCELLED,
                        errors=(RunError("cancelled", str(exc)),),
                    )
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

    async def _execute(
        self,
        request: RunRequest,
        *,
        task_id: str,
        cancellation: CancellationSource,
        progress_tracker: RunProgressTracker,
    ) -> TaskRunResult:
        input_sources = _flatten_request_artifacts(request.inputs)
        output_targets = _flatten_request_artifacts(request.outputs)

        task_cache = self._cache_for_task(task_id)
        artifact_bindings_provider = self._build_artifact_bindings_provider(task_cache)
        planner = self._build_planner(artifact_bindings_provider)
        runner = self._build_runner(artifact_bindings_provider)
        supplied: dict[ArtifactKey, ArtifactInstance[Any]] = {}

        try:
            supplied, user_supplied_settings = await self._bind_supplied_inputs(
                artifact_bindings_provider,
                input_sources,
            )
            supplied = await self._bind_default_settings(
                artifact_bindings_provider,
                supplied,
            )

            plan = await planner.plan(
                supplied_instances=supplied,
                requested_outputs=set(output_targets),
                run_policy=request.run_policy,
                user_supplied_settings=user_supplied_settings,
            )
            self._register_plan_progress(progress_tracker, plan)
        except BaseException:
            await close_instances(tuple(supplied.values()))
            raise

        return await runner.run(
            plan=plan,
            supplied_instances=supplied,
            output_targets=output_targets,
            cancellation=cancellation,
            progress_tracker=progress_tracker,
            task_id=task_id,
        )

    def _cache_for_task(self, task_id: str) -> ArtifactCache:
        for_task = getattr(self._artifact_cache, "for_task", None)
        if callable(for_task):
            return for_task(task_id)
        return self._artifact_cache

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

    @staticmethod
    def _default_artifact_bindings_provider_factory(
        cache: ArtifactCache,
    ) -> ArtifactBindingsProvider:
        return ArtifactBindingsProvider(cache)

    @staticmethod
    def _default_planner_factory(
        stage_manifest: StageManifest,
        artifact_bindings_provider: ArtifactBindingsProvider,
    ) -> TaskRunPlanner:
        return TaskRunPlanner(
            stage_manifest,
            artifact_bindings_provider.fingerprint_provider,
        )

    @staticmethod
    def _default_runner_factory(
        stage_manifest: StageManifest,
        artifact_bindings_provider: ArtifactBindingsProvider,
    ) -> ExampleTaskRunner:
        return ExampleTaskRunner(
            manifest=stage_manifest,
            artifact_bindings_provider=artifact_bindings_provider,
        )

    async def _bind_supplied_inputs(
        self,
        artifact_bindings_provider: ArtifactBindingsProvider,
        input_sources: Mapping[ArtifactKey, ArtifactSourceHandle[Any]],
    ) -> tuple[dict[ArtifactKey, ArtifactInstance[Any]], set[ArtifactKey]]:
        supplied: dict[ArtifactKey, ArtifactInstance[Any]] = {}
        user_supplied_settings: set[ArtifactKey] = set()

        for artifact_id, source in input_sources.items():
            declaration = self.stage_manifest.find_artifact(artifact_id)
            if declaration.role is ArtifactRole.SETTINGS:
                user_supplied_settings.add(artifact_id)
            effective_source = await self._resolve_settings_source(
                declaration,
                source,
            )

            supplied[artifact_id] = await artifact_bindings_provider.bind_supplied(
                declaration,
                effective_source,
            )
        return supplied, user_supplied_settings

    async def _bind_default_settings(
        self,
        artifact_bindings_provider: ArtifactBindingsProvider,
        supplied: dict[ArtifactKey, ArtifactInstance[Any]],
    ) -> dict[ArtifactKey, ArtifactInstance[Any]]:
        with_defaults = dict(supplied)

        # Default settings are graph roots, exactly like caller-supplied
        # settings. The focused example binds all declared defaults before
        # planning; a fuller planner might resolve only relevant defaults.
        for artifact_id, declaration in self.stage_manifest.artifacts.items():
            if (
                declaration.role is ArtifactRole.SETTINGS
                and declaration.default_value_source_provider is not None
                and artifact_id not in with_defaults
            ):
                effective_source = await self._resolve_settings_source(
                    declaration,
                    None,
                )
                with_defaults[artifact_id] = await artifact_bindings_provider.bind_supplied(
                    declaration,
                    effective_source,
                )
        return with_defaults

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


def _flatten_request_artifacts(
    artifacts: Mapping[ArtifactKey | str, Any],
) -> dict[ArtifactKey, Any]:
    flattened: dict[ArtifactKey, Any] = {}
    for domain_or_key, value in artifacts.items():
        _flatten_request_value(flattened, (), domain_or_key, value)
    return flattened


def _flatten_request_value(
    flattened: dict[ArtifactKey, Any],
    path: tuple[str, ...],
    domain_or_key: ArtifactKey | str,
    value: Any,
) -> None:
    if isinstance(domain_or_key, ArtifactKey):
        if path:
            raise ValueError("nested request mappings must use string path components")
        if isinstance(value, Mapping):
            raise ValueError("artifact-key request entries must map directly to a handle")
        _add_request_artifact(flattened, domain_or_key, value)
        return

    next_path = (*path, domain_or_key)
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            if not isinstance(child_key, str):
                raise ValueError("nested request mapping keys must be strings")
            _flatten_request_value(flattened, next_path, child_key, child_value)
        return

    if len(next_path) not in (2, 3):
        raise ValueError(
            "request artifact mappings must resolve to DOMAIN//KIND or "
            "OVERRIDE_DOMAIN//DOMAIN//KIND paths"
        )
    _add_request_artifact(
        flattened,
        ArtifactKey.parse("//".join(next_path)),
        value,
    )


def _add_request_artifact(
    artifacts: dict[ArtifactKey, Any],
    artifact_id: ArtifactKey,
    handle: Any,
) -> None:
    if artifact_id in artifacts:
        raise ValueError(f"duplicate request artifact: {artifact_id}")
    artifacts[artifact_id] = handle
