"""Merged executable Python example of Chunky's core contracts."""

from __future__ import annotations

from .Bindings.binding_models import (
    ArtifactHandle,
    ArtifactInstance,
    ArtifactReader,
    ArtifactWriter,
    InputArtifacts,
    OutputArtifacts,
)
from .Artifacts.artifact_models import (
    ArtifactCapability,
    ArtifactDeclaration,
    ArtifactKey,
    ArtifactRefLocator,
    ArtifactInstanceLocator,
    TaskArtifactLocator,
    ArtifactRequirement,
    ArtifactRole,
    PhaseMatchMode,
)
from .Bindings.file_cache_provider import (
    FileCacheProvider,
    FileCacheProviderConfig,
    FileCacheProviderGarbageCollectionPolicy,
)
from .pipeline import (
    ArtifactCacheIO,
    ArtifactCachePolicy,
    ChunkyPipeline,
    ChunkyPipelineBuilder,
    RequestValidationError,
    RunRequest,
    chunky_run,
    chunky_run_away,
)
from .Scheduling.task_control import RunExecutionError, RunHandle, RunResult, RunStatus
from .Stages.stage_manifest import StageManifest, StageManifestBuilder
from .Stages.stage_models import (
    ManifestStageRegistration,
    CancellationSource,
    ResourcePolicy,
    ResolvedStageRequirements,
    StageExecutionContext,
    StagePlanningContext,
    StageRegistration,
)
from .Planning.TaskRequestGraph.binding_models import (
    PlannedInputBinding,
    PlannedOutputBinding,
    RequestedOutputBinding,
    TaskOutputBinding,
)
from .Planning.TaskRequestGraph.invocation_models import PlannedInvocation, RunnableInvocation
from .Planning.task_run_graph import TaskRunGraph
from .Planning.TaskRequestGraph import TaskRequestGraph

EvaluatedPlan = TaskRequestGraph

__all__ = [
    "ArtifactCacheIO",
    "ArtifactCachePolicy",
    "ArtifactCapability",
    "ArtifactDeclaration",
    "ArtifactHandle",
    "ArtifactInstance",
    "ArtifactKey",
    "ArtifactRefLocator",
    "ArtifactInstanceLocator",
    "TaskArtifactLocator",
    "ArtifactReader",
    "ArtifactRequirement",
    "ArtifactRole",
    "ArtifactWriter",
    "CancellationSource",
    "ChunkyPipeline",
    "ChunkyPipelineBuilder",
    "FileCacheProvider",
    "FileCacheProviderConfig",
    "FileCacheProviderGarbageCollectionPolicy",
    "InputArtifacts",
    "ManifestStageRegistration",
    "OutputArtifacts",
    "PhaseMatchMode",
    "PlannedInputBinding",
    "PlannedOutputBinding",
    "PlannedInvocation",
    "RequestedOutputBinding",
    "RunnableInvocation",
    "RequestValidationError",
    "ResourcePolicy",
    "ResolvedStageRequirements",
    "RunExecutionError",
    "RunHandle",
    "RunRequest",
    "RunResult",
    "RunStatus",
    "StageExecutionContext",
    "StageManifest",
    "StageManifestBuilder",
    "StagePlanningContext",
    "StageRegistration",
    "TaskRequestGraph",
    "TaskOutputBinding",
    "TaskRunGraph",
    "EvaluatedPlan",
    "chunky_run",
    "chunky_run_away",
]
