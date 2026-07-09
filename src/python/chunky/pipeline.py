from __future__ import annotations

"""Pipeline bootstrap facade that emits and owns one frozen stage manifest."""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

from .Bindings.binding_models import ArtifactInstance
from .Bindings.bindings import ArtifactBindingsProvider, close_instances
from .Artifacts.artifact_handles import ArtifactSourceHandle, ArtifactTargetHandle
from .Artifacts.artifact_models import (
    ArtifactDeclaration,
    ArtifactKey,
    ArtifactRole,
    CachedArtifactReference,
)
from .Bindings.cache_models import ArtifactCache
from .Planning.execution_planner import TaskRunPlanner
from .Scheduling.progress_models import (
    ProgressDispatcher,
    RunProgressTracker,
    ensure_progress_tracker,
)
from .Scheduling.task_control import (
    RunError,
    RunExecutionError,
    RunHandle,
    RunResult,
    RunStatus,
)
from .Scheduling.task_runner import ExampleTaskRunner, TaskRunResult
from .Stages.stage_manifest import StageManifest, StageManifestBuilder
from .Stages.stage_models import CancellationOrigin, CancellationSource, PipelineFeature, PlanningRunPolicy


@dataclass(frozen=True)
class ArtifactCachePolicy:
    ignore_existing_cache: bool = False
    do_not_cache: bool = False
    desired_ttl: timedelta | int | float | None = None


@dataclass(frozen=True)
class RequestValidationError(ValueError):
    errors: tuple[str, ...]

    def __str__(self) -> str:
        return "; ".join(self.errors)


@dataclass(frozen=True)
class RunRequest:
    inputs: Mapping[
        ArtifactKey | str,
        ArtifactSourceHandle[Any]
        | CachedArtifactReference
        | Mapping[str, ArtifactSourceHandle[Any] | CachedArtifactReference],
    ]
    outputs: Mapping[
        ArtifactKey | str,
        ArtifactTargetHandle[Any]
        | Sequence[ArtifactTargetHandle[Any]]
        | Mapping[str, ArtifactTargetHandle[Any]],
    ]
    run_policy: PlanningRunPolicy | None = None
    cache_policy: Mapping[
        ArtifactKey | str,
        ArtifactCachePolicy | Mapping[str, ArtifactCachePolicy],
    ] | None = None


ArtifactBindingsProviderFactory = Callable[[ArtifactCache], ArtifactBindingsProvider]
PlannerFactory = Callable[[StageManifest, ArtifactBindingsProvider], TaskRunPlanner]
RunnerFactory = Callable[[StageManifest, ArtifactBindingsProvider], ExampleTaskRunner]


class ArtifactCacheIO:
    """Semantic direct artifact-cache IO facade.

    This facade exposes cache publication and open-by-reference without leaking
    backend paths or reservation records. It uses the same binding provider and
    fingerprint path as run-time provided-root publication.
    """

    def __init__(self, pipeline: "ChunkyPipeline") -> None:
        self._pipeline = pipeline

    async def publish(
        self,
        artifact_id: ArtifactKey | str,
        source: ArtifactSourceHandle[Any],
        *,
        cache_policy: ArtifactCachePolicy | None = None,
    ) -> CachedArtifactReference:
        declaration = self._pipeline.stage_manifest.find_artifact(_artifact_key(artifact_id))
        effective_source = await self._pipeline._resolve_settings_source(
            declaration,
            source,
        )
        if cache_policy is not None and cache_policy.do_not_cache:
            raise ValueError(
                "ArtifactCacheIO.publish always creates a durable cache entry; "
                f"do_not_cache is invalid for {declaration.artifact_id}"
            )
        return await self._pipeline._artifact_bindings_provider.publish_supplied_root(
            declaration,
            effective_source,
        )

    async def open(
        self,
        reference: CachedArtifactReference,
    ) -> ArtifactInstance[Any]:
        declaration = self._pipeline.stage_manifest.find_artifact(reference.artifact_id)
        return await self._pipeline._artifact_bindings_provider.open_cached_reference(
            declaration,
            reference,
        )


class ChunkyPipelineBuilder:
    def __init__(self, *, artifact_cache: ArtifactCache) -> None:
        self._artifact_cache = artifact_cache
        self._manifest_builder = StageManifestBuilder()
        self._built = False

    def install(self, feature: PipelineFeature) -> "ChunkyPipelineBuilder":
        self._assert_not_built()
        self._manifest_builder.install_feature(feature)
        return self

    def build(self) -> "ChunkyPipeline":
        self._assert_not_built()
        manifest = self._manifest_builder.freeze()
        self._built = True
        return ChunkyPipeline(
            stage_manifest=manifest,
            artifact_cache=self._artifact_cache,
        )

    def _assert_not_built(self) -> None:
        if self._built:
            raise RuntimeError("pipeline builder is single-use and has already built")


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

    @property
    def cache_io(self) -> ArtifactCacheIO:
        return ArtifactCacheIO(self)

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
                if isinstance(exc, RunExecutionError):
                    return exc.result
                if cancellation.is_cancelled:
                    status = (
                        RunStatus.SHUTDOWN_INTERRUPTED
                        if cancellation.origin is CancellationOrigin.SHUTDOWN
                        else RunStatus.CANCELLED
                    )
                    return RunResult(
                        task_id=task_id,
                        status=status,
                        errors=(RunError(status.value, str(exc)),),
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
        input_roots, output_targets, cache_policy = self._normalize_request(request)

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
                default_settings_resolver=_PlannerDefaultSettingsResolver(
                    self.stage_manifest,
                    artifact_bindings_provider,
                ),
            )
            self._validate_cache_policy_reachability(cache_policy, plan)
            self._register_plan_progress(progress_tracker, plan)
        except BaseException:
            await close_instances(tuple(supplied.values()))
            raise

        return await runner.run(
            plan=plan,
            supplied_instances=supplied,
            output_targets=output_targets,
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

    def _normalize_request(
        self,
        request: RunRequest,
    ) -> tuple[
        dict[ArtifactKey, ArtifactSourceHandle[Any] | CachedArtifactReference],
        dict[ArtifactKey, tuple[ArtifactTargetHandle[Any], ...]],
        dict[ArtifactKey, ArtifactCachePolicy],
    ]:
        errors: list[str] = []
        input_sources = _collect_flattened_request_artifacts(request.inputs, errors, "inputs")
        output_targets = _collect_flattened_request_outputs(request.outputs, errors)
        cache_policy = _collect_flattened_cache_policy(request.cache_policy or {}, errors)

        declarations: dict[ArtifactKey, ArtifactDeclaration[Any]] = {}
        for artifact_id in (*input_sources.keys(), *output_targets.keys(), *cache_policy.keys()):
            try:
                declarations[artifact_id] = self.stage_manifest.find_artifact(artifact_id)
            except Exception:
                errors.append(f"unknown request artifact: {artifact_id}")

        for artifact_id, source_or_reference in input_sources.items():
            if isinstance(source_or_reference, CachedArtifactReference) and source_or_reference.artifact_id != artifact_id:
                errors.append(
                    "inputs: cached artifact reference artifact_id must match its request key: "
                    f"{source_or_reference.artifact_id} != {artifact_id}"
                )

        for artifact_id, targets in output_targets.items():
            if targets:
                continue
            declaration = declarations.get(artifact_id)
            if declaration is None:
                continue
            if not declaration.cacheable:
                errors.append(
                    f"target-less output {artifact_id} must be cacheable for durable retrieval"
                )
            if cache_policy.get(artifact_id, ArtifactCachePolicy()).do_not_cache:
                errors.append(
                    f"target-less output {artifact_id} conflicts with cache_policy.do_not_cache"
                )

        if not output_targets:
            errors.append("run request must contain at least one output")

        if errors:
            raise RequestValidationError(tuple(errors))
        return input_sources, output_targets, cache_policy

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
        input_roots: Mapping[ArtifactKey, ArtifactSourceHandle[Any] | CachedArtifactReference],
        cache_policy: Mapping[ArtifactKey, ArtifactCachePolicy] | None = None,
    ) -> tuple[dict[ArtifactKey, ArtifactInstance[Any]], set[ArtifactKey]]:
        supplied: dict[ArtifactKey, ArtifactInstance[Any]] = {}
        user_supplied_settings: set[ArtifactKey] = set()

        for artifact_id, source_or_reference in input_roots.items():
            declaration = self.stage_manifest.find_artifact(artifact_id)
            if declaration.role is ArtifactRole.SETTINGS:
                user_supplied_settings.add(artifact_id)

            if isinstance(source_or_reference, CachedArtifactReference):
                supplied[artifact_id] = await artifact_bindings_provider.open_cached_reference(
                    declaration,
                    source_or_reference,
                )
                continue

            effective_source = await self._resolve_settings_source(
                declaration,
                source_or_reference,
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


class _PlannerDefaultSettingsResolver:
    def __init__(
        self,
        manifest: StageManifest,
        artifact_bindings_provider: ArtifactBindingsProvider,
    ) -> None:
        self._manifest = manifest
        self._artifact_bindings_provider = artifact_bindings_provider

    async def resolve_defaults(
        self,
        supplied: Mapping[ArtifactKey, ArtifactInstance[Any]],
    ) -> dict[ArtifactKey, ArtifactInstance[Any]]:
        resolved: dict[ArtifactKey, ArtifactInstance[Any]] = {}
        for artifact_id, declaration in self._manifest.artifacts.items():
            if (
                declaration.role is ArtifactRole.SETTINGS
                and declaration.default_value_source_provider is not None
                and artifact_id not in supplied
            ):
                source = await declaration.default_value_source_provider.resolve_source(
                    None,
                    declaration=declaration,
                )
                resolved[artifact_id] = await self._artifact_bindings_provider.bind_supplied(
                    declaration,
                    source,
                )
        return resolved


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
    errors: list[str] = []
    flattened = _collect_flattened_request_artifacts(artifacts, errors, "artifacts")
    if errors:
        raise RequestValidationError(tuple(errors))
    return flattened


def _collect_flattened_request_artifacts(
    artifacts: Mapping[ArtifactKey | str, Any],
    errors: list[str],
    label: str,
) -> dict[ArtifactKey, Any]:
    flattened: dict[ArtifactKey, Any] = {}
    for domain_or_key, value in artifacts.items():
        _flatten_request_value(flattened, (), domain_or_key, value, errors, label)
    return flattened


def _collect_flattened_request_outputs(
    artifacts: Mapping[ArtifactKey | str, Any],
    errors: list[str],
) -> dict[ArtifactKey, tuple[ArtifactTargetHandle[Any], ...]]:
    raw = _collect_flattened_request_artifacts(artifacts, errors, "outputs")
    outputs: dict[ArtifactKey, tuple[ArtifactTargetHandle[Any], ...]] = {}
    for artifact_id, value in raw.items():
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            targets = tuple(value)
        else:
            targets = (value,)
        outputs[artifact_id] = targets
    return outputs


def _collect_flattened_cache_policy(
    cache_policy: Mapping[ArtifactKey | str, Any],
    errors: list[str],
) -> dict[ArtifactKey, ArtifactCachePolicy]:
    raw = _collect_flattened_request_artifacts(cache_policy, errors, "cache_policy")
    normalized: dict[ArtifactKey, ArtifactCachePolicy] = {}
    for artifact_id, value in raw.items():
        if isinstance(value, ArtifactCachePolicy):
            normalized[artifact_id] = value
        else:
            errors.append(f"cache_policy for {artifact_id} must be an ArtifactCachePolicy")
    return normalized


def _flatten_request_value(
    flattened: dict[ArtifactKey, Any],
    path: tuple[str, ...],
    domain_or_key: ArtifactKey | str,
    value: Any,
    errors: list[str],
    label: str,
) -> None:
    if isinstance(domain_or_key, ArtifactKey):
        if path:
            errors.append(f"{label}: nested request mappings must use string path components")
            return
        if isinstance(value, Mapping):
            errors.append(f"{label}: artifact-key entries must map directly to a handle")
            return
        _add_request_artifact(flattened, domain_or_key, value, errors, label)
        return

    next_path = (*path, domain_or_key)
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            if not isinstance(child_key, str):
                errors.append(f"{label}: nested request mapping keys must be strings")
                continue
            _flatten_request_value(flattened, next_path, child_key, child_value, errors, label)
        return

    if len(next_path) not in (2, 3):
        errors.append(
            f"{label}: request artifact mappings must resolve to DOMAIN//KIND or "
            "OVERRIDE_DOMAIN//DOMAIN//KIND paths"
        )
        return
    try:
        artifact_id = ArtifactKey.parse("//".join(next_path))
    except ValueError as exc:
        errors.append(f"{label}: {exc}")
        return
    _add_request_artifact(flattened, artifact_id, value, errors, label)


def _add_request_artifact(
    artifacts: dict[ArtifactKey, Any],
    artifact_id: ArtifactKey,
    handle: Any,
    errors: list[str],
    label: str,
) -> None:
    if artifact_id in artifacts:
        errors.append(f"{label}: duplicate request artifact: {artifact_id}")
        return
    artifacts[artifact_id] = handle
