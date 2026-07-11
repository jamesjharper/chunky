from __future__ import annotations

"""Core stage contracts from the Stage Model specification.

This module deliberately keeps runtime execution, registration metadata, and
request-specific planning as separate types.  Executors receive only bound
artifact readers/writers and the narrow :class:`StageExecutionContext`.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar

from ..Bindings.binding_models import InputArtifacts, OutputArtifacts
from ..Artifacts.artifact_models import (
    ArtifactCapability,
    ArtifactDeclaration,
    ArtifactKey,
    ArtifactRequirement,
    ArtifactRole,
    PhaseMatchMode,
)
from ..Artifacts.fingerprints import FingerprintProvider
from ..Artifacts.phase_catalog import capability_covers_requirement

if TYPE_CHECKING:
    from ..Pipeline.runtime import (
        PipelineRuntimeSettings,
        PipelineRuntimeServicesBuilder,
        PipelineRuntimeSettingsBuilder,
    )
    from .stage_manifest import StageManifestBuilder
    from .stage_manifest import StageManifest

T = TypeVar("T")


class StageExecutor(Protocol):
    async def execute(
        self,
        inputs: InputArtifacts,
        outputs: OutputArtifacts,
        context: "StageExecutionContext",
        runtime_settings: "PipelineRuntimeSettings",
    ) -> None: ...


class TaskCancelledError(asyncio.CancelledError):
    """Raised when Chunky's cooperative cancellation token is cancelled."""


class CancellationOrigin(str, Enum):
    CALLER = "caller"
    SHUTDOWN = "shutdown"


class CancellationToken(Protocol):
    @property
    def is_cancelled(self) -> bool: ...

    @property
    def reason(self) -> str | None: ...

    @property
    def origin(self) -> CancellationOrigin: ...

    async def wait_cancelled(self) -> None: ...

    async def wait_for(self, awaitable: Awaitable[T]) -> T: ...

    def raise_if_cancelled(self) -> None: ...


class CancellationSource:
    """Small in-process cancellation source used by the executable example.

    ``wait_for`` cancels only the local wait coroutine.  In particular, when it
    wraps ``cache_wait.wait_handle.wait()``, it never changes the other task's
    cache write reservation.
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None
        self._origin = CancellationOrigin.CALLER

    @property
    def token(self) -> "CancellationSource":
        return self

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def origin(self) -> CancellationOrigin:
        return self._origin

    def cancel(
        self,
        reason: str | None = None,
        *,
        origin: CancellationOrigin = CancellationOrigin.CALLER,
    ) -> bool:
        if self._event.is_set():
            return False
        self._reason = reason
        self._origin = origin
        self._event.set()
        return True

    def shutdown(self, reason: str | None = None) -> bool:
        return self.cancel(reason or "process shutdown", origin=CancellationOrigin.SHUTDOWN)

    async def wait_cancelled(self) -> None:
        await self._event.wait()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise TaskCancelledError(self._reason or "task cancelled")

    async def wait_for(self, awaitable: Awaitable[T]) -> T:
        self.raise_if_cancelled()
        operation = asyncio.ensure_future(awaitable)
        cancelled = asyncio.create_task(self._event.wait())
        try:
            done, _ = await asyncio.wait(
                {operation, cancelled},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancelled in done:
                operation.cancel()
                try:
                    await operation
                except asyncio.CancelledError:
                    pass
                self.raise_if_cancelled()
            return await operation
        finally:
            cancelled.cancel()
            try:
                await cancelled
            except asyncio.CancelledError:
                pass


@dataclass(frozen=True)
class StageSubtaskProgress:
    subtask_id: str
    name: str
    percentage: float | None = None


class StageSubtaskReporter(Protocol):
    def update(
        self,
        *,
        name: str | None = None,
        percentage: float | None = None,
    ) -> None: ...

    def finish(self) -> None: ...


class StageProgressReporter(Protocol):
    def start_subtask(
        self,
        subtask_id: str,
        name: str,
        *,
        percentage: float | None = None,
    ) -> StageSubtaskReporter: ...


class _SubtaskReporter:
    def __init__(
        self,
        owner: "InMemoryStageProgressReporter",
        subtask_id: str,
    ) -> None:
        self._owner = owner
        self._subtask_id = subtask_id
        self._finished = False

    def update(
        self,
        *,
        name: str | None = None,
        percentage: float | None = None,
    ) -> None:
        if self._finished:
            return
        self._owner._update(self._subtask_id, name=name, percentage=percentage)

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._owner._finish(self._subtask_id)


class InMemoryStageProgressReporter:
    """Invocation-scoped progress reporter suitable for tests and the demo."""

    def __init__(self) -> None:
        self._subtasks: dict[str, StageSubtaskProgress] = {}

    def start_subtask(
        self,
        subtask_id: str,
        name: str,
        *,
        percentage: float | None = None,
    ) -> StageSubtaskReporter:
        if subtask_id in self._subtasks:
            raise ValueError(f"subtask already exists: {subtask_id}")
        self._validate_percentage(percentage)
        self._subtasks[subtask_id] = StageSubtaskProgress(
            subtask_id=subtask_id,
            name=name,
            percentage=percentage,
        )
        return _SubtaskReporter(self, subtask_id)

    def snapshot(self) -> tuple[StageSubtaskProgress, ...]:
        return tuple(self._subtasks[key] for key in sorted(self._subtasks))

    def clear(self) -> None:
        self._subtasks.clear()

    def _update(
        self,
        subtask_id: str,
        *,
        name: str | None,
        percentage: float | None,
    ) -> None:
        self._validate_percentage(percentage)
        current = self._subtasks[subtask_id]
        self._subtasks[subtask_id] = StageSubtaskProgress(
            subtask_id=subtask_id,
            name=current.name if name is None else name,
            percentage=current.percentage if percentage is None else percentage,
        )

    def _finish(self, subtask_id: str) -> None:
        self._subtasks.pop(subtask_id, None)

    @staticmethod
    def _validate_percentage(percentage: float | None) -> None:
        if percentage is not None and not 0.0 <= percentage <= 100.0:
            raise ValueError("percentage must be in the inclusive range 0..100")


class StageLogger(Protocol):
    def debug(self, msg: object, *args: object, **kwargs: object) -> None: ...

    def info(self, msg: object, *args: object, **kwargs: object) -> None: ...

    def warning(self, msg: object, *args: object, **kwargs: object) -> None: ...

    def error(self, msg: object, *args: object, **kwargs: object) -> None: ...


@dataclass(frozen=True)
class StageExecutionContext:
    task_id: str
    invocation_id: str
    stage_id: str
    cancellation: CancellationToken
    progress: StageProgressReporter
    logger: StageLogger
    # Settings resolved during planning and frozen into the invocation.
    # Stages can read behavior-affecting settings from here without reaching
    # back into planner state or requiring every setting to be an artifact
    # reader in InputArtifacts.
    settings: "ResolvedSettingsView" = field(default_factory=lambda: ResolvedSettingsView())
    scratch_workspace: Path | None = None


@dataclass(frozen=True)
class ResourcePolicy:
    classes: frozenset[str] = field(
        default_factory=lambda: frozenset({"unbound"})
    )

    def __post_init__(self) -> None:
        if not self.classes:
            raise ValueError("resource policy must declare at least one class")
        if "unbound" in self.classes and len(self.classes) != 1:
            raise ValueError("unbound cannot be combined with constrained resources")


@dataclass(frozen=True)
class StageConcurrencyPolicy:
    max_concurrent_invocations: int | None = None
    concurrency_key: str | None = None
    singleton_executor_is_safe: bool = False

    def __post_init__(self) -> None:
        if (
            self.max_concurrent_invocations is not None
            and self.max_concurrent_invocations < 1
        ):
            raise ValueError("max_concurrent_invocations must be positive")


class _ResolvedSettingDict(dict):
    """Dict-compatible settings value with friendly single-item display.

    The planner tests often compare ``tuple(setting.items())`` for compactness.
    For one-field settings this returns the single pair directly while normal
    mapping equality and ``dict(value)`` continue to behave like a plain dict.
    """

    def items(self):  # type: ignore[override]
        pairs = tuple(super().items())
        if len(pairs) == 1:
            return pairs[0]
        return pairs


def _wrap_resolved_setting(value: T) -> T:
    if isinstance(value, dict) and not isinstance(value, _ResolvedSettingDict):
        return _ResolvedSettingDict(value)  # type: ignore[return-value]
    return value


@dataclass(frozen=True)
class ResolvedSettingEntry:
    value: object
    fingerprint: str | None = None


class ResolvedSettingsView:
    """Read-only view of request-resolved settings values."""

    def __init__(
        self,
        values: Mapping[ArtifactKey, object] | None = None,
        *,
        fingerprints: Mapping[ArtifactKey, str] | None = None,
        entries: Mapping[ArtifactKey, ResolvedSettingEntry] | None = None,
    ) -> None:
        if entries is not None:
            resolved_entries = {
                key: ResolvedSettingEntry(entry.value, entry.fingerprint)
                for key, entry in dict(entries).items()
            }
        else:
            fingerprints_by_key = dict(fingerprints or {})
            resolved_entries = {
                key: ResolvedSettingEntry(value, fingerprints_by_key.get(key))
                for key, value in dict(values or {}).items()
            }
        self._entries = MappingProxyType(resolved_entries)

    def contains(self, selector: ArtifactDeclaration[Any] | ArtifactKey | str) -> bool:
        return _selector_key(selector) in self._entries

    def get(
        self,
        selector: ArtifactDeclaration[T] | ArtifactKey | str,
        default: T | None = None,
    ) -> T | None:
        entry = self._entries.get(_selector_key(selector))
        value = default if entry is None else entry.value
        return _wrap_resolved_setting(value)  # type: ignore[return-value]

    def require(
        self,
        selector: ArtifactDeclaration[T] | ArtifactKey | str,
    ) -> T:
        key = _selector_key(selector)
        try:
            return _wrap_resolved_setting(self._entries[key].value)  # type: ignore[return-value]
        except KeyError as exc:
            raise KeyError(f"required resolved setting is absent: {key}") from exc

    def items(self) -> tuple[tuple[ArtifactKey, object], ...]:
        return tuple(
            sorted(
                ((key, entry.value) for key, entry in self._entries.items()),
                key=lambda item: str(item[0]),
            )
        )

    def fingerprint(
        self,
        selector: ArtifactDeclaration[Any] | ArtifactKey | str,
    ) -> str | None:
        entry = self._entries.get(_selector_key(selector))
        return None if entry is None else entry.fingerprint

    def fingerprint_items(self) -> tuple[tuple[ArtifactKey, str], ...]:
        return tuple(
            sorted(
                (
                    (key, entry.fingerprint)
                    for key, entry in self._entries.items()
                    if entry.fingerprint is not None
                ),
                key=lambda item: str(item[0]),
            )
        )

    def fingerprints(self) -> tuple[str, ...]:
        return tuple(
            entry.fingerprint
            or FingerprintProvider.build_setting_value_fingerprint(key, entry.value)
            for key, entry in sorted(self._entries.items(), key=lambda item: str(item[0]))
        )

    def filtered(
        self,
        selectors: set[ArtifactKey] | frozenset[ArtifactKey],
    ) -> "ResolvedSettingsView":
        return ResolvedSettingsView(
            entries={key: entry for key, entry in self._entries.items() if key in selectors}
        )

    def remove(
        self,
        selectors: set[ArtifactKey] | frozenset[ArtifactKey],
    ) -> "ResolvedSettingsView":
        removed_bases = {selector.without_override() for selector in selectors}
        return ResolvedSettingsView(
            entries={
                key: entry
                for key, entry in self._entries.items()
                if key.without_override() not in removed_bases
            }
        )

    def merge(self, *overlays: "ResolvedSettingsView") -> "ResolvedSettingsView":
        merged = dict(self._entries)
        for overlay in overlays:
            merged.update(overlay._entries)
        return ResolvedSettingsView(entries=merged)


@dataclass(frozen=True)
class PlanningRunPolicy:
    requested_diagnostics: frozenset[ArtifactKey] = frozenset()

    def diagnostics_enabled(self, selector: ArtifactKey | str) -> bool:
        key = selector if isinstance(selector, ArtifactKey) else ArtifactKey.parse(selector)
        return key in self.requested_diagnostics



@dataclass(frozen=True)
class StagePlanningContext:
    stage_id: str
    stage_fingerprint: str
    requested_outputs: frozenset[ArtifactKey]
    resolved_settings: ResolvedSettingsView
    inherited_settings: ResolvedSettingsView
    run_policy: PlanningRunPolicy


@dataclass(frozen=True)
class ResolvedStageRequirements:
    inputs: tuple[ArtifactRequirement, ...]


StageExecutorFactory = Callable[[], StageExecutor]
StageRequirementResolver = Callable[[StagePlanningContext], ResolvedStageRequirements]
StageOutputResolver = Callable[
    [StagePlanningContext],
    tuple[ArtifactCapability, ...],
]
StageResourceResolver = Callable[[StagePlanningContext], ResourcePolicy]


@dataclass(frozen=True)
class StageRegistration:
    stage_id: str
    display_name: str
    registration_version: str
    behavior_version: str
    executor_factory: StageExecutorFactory

    owned_artifacts: tuple[ArtifactDeclaration[Any], ...]
    input_capabilities: tuple[ArtifactCapability, ...]
    output_capabilities: tuple[ArtifactCapability, ...]

    resolve_requirements: StageRequirementResolver
    resolve_output_capabilities: StageOutputResolver
    resolve_resource_policy: StageResourceResolver

    runtime_settings: tuple["RuntimeStageSetting", ...] = ()
    concurrency_policy: StageConcurrencyPolicy | None = None


@dataclass(frozen=True)
class ManifestStageRegistration:
    manifest: "StageManifest"
    registration: StageRegistration

    def __getattr__(self, name: str) -> Any:
        return getattr(self.registration, name)

    def declared_setting_ids(
        self,
    ) -> frozenset[ArtifactKey]:
        return self.manifest.find_artifacts_by_role(
            (capability.artifact_id for capability in self.input_capabilities),
            role=ArtifactRole.SETTINGS,
        )

    def resolve_validated_requirements(
        self,
        context: StagePlanningContext,
    ) -> ResolvedStageRequirements:
        result = self.resolve_requirements(context)
        if not isinstance(result, ResolvedStageRequirements):
            raise StageManifestError(
                f"{self.stage_id} requirement resolver returned "
                f"{type(result).__name__}, expected ResolvedStageRequirements"
            )

        seen: set[tuple[ArtifactKey, PhaseMatchMode, tuple[ArtifactKey, ...]]] = set()
        for requirement in result.inputs:
            if not isinstance(requirement, ArtifactRequirement):
                raise StageManifestError(
                    f"{self.stage_id} returned a non-artifact requirement"
                )
            if not any(
                _capability_covers_requirement(capability, requirement)
                for capability in self.input_capabilities
            ):
                raise StageManifestError(
                    f"{self.stage_id} resolved input outside its static "
                    f"capability envelope: {requirement.artifact_id}"
                )
            for pushed in requirement.pushed_settings:
                declaration = self.manifest.artifacts.get(pushed)
                if declaration is None or declaration.role is not ArtifactRole.SETTINGS:
                    raise StageManifestError(
                        f"{self.stage_id} pushed a non-settings artifact: {pushed}"
                    )
            signature = (
                requirement.artifact_id,
                requirement.phase_match,
                requirement.pushed_settings,
            )
            if signature in seen:
                raise StageManifestError(
                    f"{self.stage_id} resolved duplicate input: "
                    f"{requirement.artifact_id}"
                )
            seen.add(signature)
        return result

    def resolve_validated_output_capabilities(
        self,
        context: StagePlanningContext,
    ) -> tuple[ArtifactCapability, ...]:
        result = self.resolve_output_capabilities(context)
        if not isinstance(result, tuple):
            raise StageManifestError(
                f"{self.stage_id} output resolver must return a tuple"
            )
        allowed = {capability.artifact_id for capability in self.output_capabilities}
        seen: set[ArtifactKey] = set()
        for capability in result:
            if not isinstance(capability, ArtifactCapability):
                raise StageManifestError(
                    f"{self.stage_id} returned a non-artifact output capability"
                )
            if capability.artifact_id not in allowed:
                raise StageManifestError(
                    f"{self.stage_id} resolved output outside its static "
                    f"capability envelope: {capability.artifact_id}"
                )
            if capability.artifact_id in seen:
                raise StageManifestError(
                    f"{self.stage_id} resolved duplicate output: "
                    f"{capability.artifact_id}"
                )
            seen.add(capability.artifact_id)
        return result

    def resolve_validated_resource_policy(
        self,
        context: StagePlanningContext,
    ) -> ResourcePolicy:
        result = self.resolve_resource_policy(context)
        if not isinstance(result, ResourcePolicy):
            raise StageManifestError(
                f"{self.stage_id} resource resolver returned "
                f"{type(result).__name__}, expected ResourcePolicy"
            )
        return result


class PipelineFeature(Protocol):
    feature_id: str

    @property
    def configuration_fingerprint(self) -> str: ...

    def install(
        self,
        manifest_builder: "StageManifestBuilder",
        service_locator: "PipelineRuntimeServicesBuilder",
        env_settings: "PipelineRuntimeSettingsBuilder",
    ) -> None: ...


@dataclass(frozen=True)
class FeatureRecord:
    feature_id: str
    configuration_fingerprint: str


class StageManifestError(ValueError):
    pass


class StagePlanningError(ValueError):
    pass


def _capability_covers_requirement(
    capability: ArtifactCapability,
    requirement: ArtifactRequirement,
) -> bool:
    return capability_covers_requirement(capability.artifact_id, requirement)


def _selector_key(
    selector: ArtifactDeclaration[Any] | ArtifactKey | str,
) -> ArtifactKey:
    if isinstance(selector, ArtifactDeclaration):
        return selector.artifact_id
    if isinstance(selector, ArtifactKey):
        return selector
    return ArtifactKey.parse(selector)


def build_stage_logger(
    task_id: str,
    invocation_id: str,
    stage_id: str,
) -> logging.LoggerAdapter:
    return logging.LoggerAdapter(
        logging.getLogger(f"chunky.stage.{stage_id}"),
        {
            "task_id": task_id,
            "invocation_id": invocation_id,
            "stage_id": stage_id,
        },
    )
