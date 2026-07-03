"""Merged executable Python example of Chunky's core contracts."""

from __future__ import annotations

from .Artifacts.binding_models import (
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
    ArtifactRequirement,
    ArtifactRole,
    PhaseMatchMode,
)
from .CacheProviders.file_cache_provider import (
    FileCacheProvider,
    FileCacheProviderConfig,
    FileCacheProviderGarbageCollectionPolicy,
)
from .pipeline import (
    ChunkyPipeline,
    ChunkyPipelineBuilder,
    RunRequest,
    chunky_run,
    chunky_run_away,
)
from .Scheduling.task_control import RunHandle, RunResult, RunStatus
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
)
from .Planning.TaskRequestGraph.invocation_models import PlannedInvocation
from .Planning.TaskExecutionGraph import TaskExecutionGraph
from .Planning.TaskRequestGraph import TaskRequestGraph

EvaluatedPlan = TaskRequestGraph

__all__ = [
    "ArtifactCapability",
    "ArtifactDeclaration",
    "ArtifactHandle",
    "ArtifactInstance",
    "ArtifactKey",
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
    "ResourcePolicy",
    "ResolvedStageRequirements",
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
    "TaskExecutionGraph",
    "EvaluatedPlan",
    "chunky_run",
    "chunky_run_away",
]
