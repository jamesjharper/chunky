from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..Artifacts.artifact_handles import ArtifactSourceHandle
from ..Artifacts.artifact_models import ArtifactInstanceLocator, ArtifactKey
from ..Bindings.bindings import ArtifactBindingsProvider
from ..Bindings.cache_models import ArtifactCache
from ..Bindings.file_cache_provider import FileCacheProvider, FileCacheProviderConfig
from ..Planning.execution_planner import TaskRunPlanner
from ..Scheduling.task_runner import ExampleTaskRunner
from ..Stages.stage_manifest import StageManifest, StageManifestBuilder

if TYPE_CHECKING:
    from .env_settings_features import PipelineRuntimeSettingsBuilder
    from .pipeline import ChunkyPipeline
    from .pipeline_models import ArtifactCachePolicy


ArtifactBindingsProviderFactory = Callable[[ArtifactCache], ArtifactBindingsProvider]
PlannerFactory = Callable[[StageManifest, ArtifactBindingsProvider], TaskRunPlanner]
RunnerFactory = Callable[[StageManifest, ArtifactBindingsProvider], ExampleTaskRunner]
DiagnosticsFactory = Callable[["ChunkyPipeline"], Any]


class ArtifactCacheIO:
    """Semantic direct artifact-cache IO facade."""

    def __init__(self, pipeline: "ChunkyPipeline") -> None:
        self._pipeline = pipeline

    async def publish(
        self,
        artifact_id: ArtifactKey | str,
        source: ArtifactSourceHandle[Any],
        *,
        cache_policy: "ArtifactCachePolicy" | None = None,
    ) -> ArtifactInstanceLocator:
        await self._pipeline._ensure_runtime_services_ready()
        del source
        declaration = self._pipeline.stage_manifest.find_artifact(
            artifact_id if isinstance(artifact_id, ArtifactKey) else ArtifactKey.parse(artifact_id)
        )
        if cache_policy is not None and cache_policy.do_not_cache:
            raise ValueError(
                "ArtifactCacheIO.publish always creates a durable cache entry; "
                f"do_not_cache is invalid for {declaration.artifact_id}"
            )
        raise NotImplementedError(
            "ArtifactCacheIO.publish is intentionally stubbed until the cache "
            "and handle contracts define streaming materialization. The current "
            "value-based prepare/write path would require loading the entire "
            "artifact into memory before writing it to the cache."
        )

    async def open(self, locator: ArtifactInstanceLocator):
        await self._pipeline._ensure_runtime_services_ready()
        declaration = self._pipeline.stage_manifest.find_artifact(locator.artifact_id)
        return await self._pipeline._artifact_bindings_provider.open_instance_locator(
            declaration,
            locator,
        )


class FileCacheFeature:
    feature_id = "chunky.runtime.file-cache"

    def __init__(
        self,
        root: str | Path,
        *,
        config: FileCacheProviderConfig | None = None,
    ) -> None:
        self._root = Path(root)
        self._artifact_cache = FileCacheProvider(
            self._root,
            config=config,
        )
        self.configuration_fingerprint = f"file-cache-runtime-service:{self._root.resolve()}"

    def install(
        self,
        manifest_builder: StageManifestBuilder,
        service_locator: "PipelineRuntimeServicesBuilder",
        env_settings: PipelineRuntimeSettingsBuilder,
    ) -> None:
        del manifest_builder, env_settings
        service_locator.set_artifact_cache(self._artifact_cache)


def default_artifact_bindings_provider_factory(
    cache: ArtifactCache,
) -> ArtifactBindingsProvider:
    return ArtifactBindingsProvider(cache)


def default_planner_factory(
    stage_manifest: StageManifest,
    artifact_bindings_provider: ArtifactBindingsProvider,
) -> TaskRunPlanner:
    return TaskRunPlanner(
        stage_manifest,
        artifact_bindings_provider.fingerprint_provider,
    )


def default_runner_factory(
    stage_manifest: StageManifest,
    artifact_bindings_provider: ArtifactBindingsProvider,
) -> ExampleTaskRunner:
    return ExampleTaskRunner(
        manifest=stage_manifest,
        artifact_bindings_provider=artifact_bindings_provider,
    )


@dataclass(frozen=True)
class PipelineRuntimeServices:
    artifact_cache: ArtifactCache
    artifact_bindings_provider_factory: ArtifactBindingsProviderFactory
    planner_factory: PlannerFactory
    runner_factory: RunnerFactory
    diagnostics_factory: DiagnosticsFactory | None = None


class PipelineRuntimeServicesBuilder:
    def __init__(self) -> None:
        self._artifact_cache: ArtifactCache | None = None
        self._artifact_bindings_provider_factory: ArtifactBindingsProviderFactory | None = None
        self._planner_factory: PlannerFactory | None = None
        self._runner_factory: RunnerFactory | None = None
        self._diagnostics_factory: DiagnosticsFactory | None = None
        self._frozen: PipelineRuntimeServices | None = None

    def set_artifact_cache(
        self,
        artifact_cache: ArtifactCache,
    ) -> "PipelineRuntimeServicesBuilder":
        self._set_slot("artifact_cache", artifact_cache)
        return self

    def set_artifact_bindings_provider_factory(
        self,
        factory: ArtifactBindingsProviderFactory,
    ) -> "PipelineRuntimeServicesBuilder":
        self._set_slot("artifact_bindings_provider_factory", factory)
        return self

    def set_planner_factory(
        self,
        factory: PlannerFactory,
    ) -> "PipelineRuntimeServicesBuilder":
        self._set_slot("planner_factory", factory)
        return self

    def set_runner_factory(
        self,
        factory: RunnerFactory,
    ) -> "PipelineRuntimeServicesBuilder":
        self._set_slot("runner_factory", factory)
        return self

    def set_diagnostics_factory(
        self,
        factory: DiagnosticsFactory,
    ) -> "PipelineRuntimeServicesBuilder":
        self._set_slot("diagnostics_factory", factory)
        return self

    def freeze(
        self,
        *,
        default_artifact_bindings_provider_factory: ArtifactBindingsProviderFactory = (
            default_artifact_bindings_provider_factory
        ),
        default_planner_factory: PlannerFactory = default_planner_factory,
        default_runner_factory: RunnerFactory = default_runner_factory,
        default_diagnostics_factory: DiagnosticsFactory | None = None,
    ) -> PipelineRuntimeServices:
        if self._frozen is not None:
            return self._frozen
        if self._artifact_cache is None:
            raise RuntimeError("artifact_cache runtime service must be configured")
        self._frozen = PipelineRuntimeServices(
            artifact_cache=self._artifact_cache,
            artifact_bindings_provider_factory=(
                self._artifact_bindings_provider_factory
                or default_artifact_bindings_provider_factory
            ),
            planner_factory=self._planner_factory or default_planner_factory,
            runner_factory=self._runner_factory or default_runner_factory,
            diagnostics_factory=self._diagnostics_factory or default_diagnostics_factory,
        )
        return self._frozen

    def _set_slot(self, slot_name: str, value: object) -> None:
        self._assert_mutable()
        if getattr(self, f"_{slot_name}") is not None:
            raise RuntimeError(f"runtime service slot already configured: {slot_name}")
        setattr(self, f"_{slot_name}", value)

    def _assert_mutable(self) -> None:
        if self._frozen is not None:
            raise RuntimeError("runtime services builder is already frozen")
