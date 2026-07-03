from __future__ import annotations

"""Immutable bindings and stage-facing typed I/O.

Readers and writers receive already-resolved handles. They never look up cache
entries, choose providers, or acquire reservations themselves. Input/output
artifact collections provide a uniform stage execution signature while keeping
the underlying typed readers and writers explicit.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from .artifact_handles import ArtifactSourceHandle, ArtifactTargetHandle
from .artifact_models import ArtifactDeclaration, ArtifactKey

if TYPE_CHECKING:
    from ..CacheProviders.cache_models import CacheWait

T = TypeVar("T")
_UNSET = object()

@dataclass(frozen=True)
class ArtifactHandle(Generic[T]):
    declaration: ArtifactDeclaration[T]
    fingerprint: str
    source: ArtifactSourceHandle[T] | None = None
    targets: tuple[ArtifactTargetHandle[T], ...] = ()

@dataclass(frozen=True)
class ArtifactInstance(Generic[T]):
    handle: ArtifactHandle[T]

    @property
    def fingerprint(self) -> str:
        return self.handle.fingerprint

class ArtifactReader(Generic[T]):
    def __init__(self, handle: ArtifactHandle[T]):
        if handle.source is None:
            raise ValueError("input artifact has no bound source")
        self.handle = handle
        self._value: T | object = _UNSET

    async def read(self) -> T:
        if self._value is _UNSET:
            assert self.handle.source is not None
            self._value = await self.handle.source.get_value(
                self.handle.declaration.codec
            )
        return cast(T, self._value)

class ArtifactWriter(Generic[T]):
    def __init__(self, handle: ArtifactHandle[T]):
        if not handle.targets:
            raise ValueError("output artifact has no bound targets")
        self.handle = handle
        self.written = False

    async def write(self, value: T) -> ArtifactInstance[T]:
        if self.written:
            raise RuntimeError("artifact may be written only once")

        prepared: list[ArtifactTargetHandle[T]] = []
        try:
            # The prepare-all-before-commit rule prevents a target from becoming
            # visible when another target could not even stage the same value.
            for target in self.handle.targets:
                await target.prepare(
                    value,
                    self.handle.declaration.codec,
                    self.handle.fingerprint,
                )
                prepared.append(target)

            # Binding order is semantically meaningful. Cache is bound first;
            # same-process publication is bound last.
            for target in self.handle.targets:
                await target.commit()
        except BaseException:
            for target in reversed(prepared):
                await target.abort()
            raise

        self.written = True
        return ArtifactInstance(self.handle)

    async def abort_if_unwritten(self) -> None:
        if not self.written:
            for target in reversed(self.handle.targets):
                await target.abort()

ArtifactSelector = ArtifactDeclaration[Any] | ArtifactKey | str

def _artifact_key_from_selector(selector: ArtifactSelector) -> ArtifactKey:
    if isinstance(selector, ArtifactDeclaration):
        return selector.artifact_id
    if isinstance(selector, ArtifactKey):
        return selector
    return ArtifactKey.parse(selector)


class InputArtifacts:
    def __init__(
        self,
        readers: dict[ArtifactKey, ArtifactReader[Any]],
        aliases: dict[ArtifactKey, ArtifactKey] | None = None,
    ):
        self._readers = readers
        self._aliases = dict(aliases or {})

    @classmethod
    def from_instances(
        cls,
        instances: list[ArtifactInstance[Any]] | tuple[ArtifactInstance[Any], ...],
        *,
        aliases: dict[ArtifactKey, ArtifactKey] | None = None,
    ) -> "InputArtifacts":
        return cls(
            {
                instance.handle.declaration.artifact_id: ArtifactReader(instance.handle)
                for instance in instances
            },
            aliases,
        )

    def _resolved_key(self, selector: ArtifactSelector) -> ArtifactKey:
        key = _artifact_key_from_selector(selector)
        return self._aliases.get(key, key)

    def get_artifact(self, selector: ArtifactSelector) -> ArtifactReader[Any]:
        requested = _artifact_key_from_selector(selector)
        artifact_id = self._aliases.get(requested, requested)
        try:
            return self._readers[artifact_id]
        except KeyError as exc:
            raise KeyError(
                f"missing input artifact: {requested} (resolved as {artifact_id})"
            ) from exc

    def get_optional_artifact(
        self,
        selector: ArtifactSelector,
    ) -> ArtifactReader[Any] | None:
        return self._readers.get(self._resolved_key(selector))

    def contains(self, selector: ArtifactSelector) -> bool:
        return self._resolved_key(selector) in self._readers


class OutputArtifacts:
    def __init__(
        self,
        writers: dict[ArtifactKey, ArtifactWriter[Any]],
        aliases: dict[ArtifactKey, ArtifactKey] | None = None,
    ):
        self._writers = writers
        self._aliases = dict(aliases or {})

    @classmethod
    def from_writers(
        cls,
        writers: list[ArtifactWriter[Any]] | tuple[ArtifactWriter[Any], ...],
        *,
        aliases: dict[ArtifactKey, ArtifactKey] | None = None,
    ) -> "OutputArtifacts":
        return cls(
            {
                writer.handle.declaration.artifact_id: writer
                for writer in writers
            },
            aliases,
        )

    def _resolved_key(self, selector: ArtifactSelector) -> ArtifactKey:
        key = _artifact_key_from_selector(selector)
        return self._aliases.get(key, key)

    def get_artifact(self, selector: ArtifactSelector) -> ArtifactWriter[Any]:
        requested = _artifact_key_from_selector(selector)
        artifact_id = self._aliases.get(requested, requested)
        try:
            return self._writers[artifact_id]
        except KeyError as exc:
            raise KeyError(
                f"missing output artifact: {requested} (resolved as {artifact_id})"
            ) from exc

    def get_optional_artifact(
        self,
        selector: ArtifactSelector,
    ) -> ArtifactWriter[Any] | None:
        return self._writers.get(self._resolved_key(selector))

    def contains(self, selector: ArtifactSelector) -> bool:
        return self._resolved_key(selector) in self._writers

    def produced_instances(self) -> tuple[ArtifactInstance[Any], ...]:
        missing = [
            artifact_id
            for artifact_id, writer in self._writers.items()
            if not writer.written
        ]
        if missing:
            missing_ids = ", ".join(str(artifact_id) for artifact_id in missing)
            raise RuntimeError(f"stage did not write declared outputs: {missing_ids}")
        return tuple(
            ArtifactInstance(writer.handle)
            for writer in self._writers.values()
        )


@dataclass(frozen=True)
class StageCacheWait:
    artifact_id: ArtifactKey
    cache_wait: CacheWait


@dataclass(frozen=True)
class StageOutputBindings:
    available: tuple[ArtifactInstance[Any], ...]
    writers: tuple[ArtifactWriter[Any], ...]
    waits: tuple[StageCacheWait, ...]

    @property
    def has_waits(self) -> bool:
        return bool(self.waits)

    @property
    def all_available(self) -> bool:
        return not self.writers and not self.waits

    @property
    def all_reserved_for_write(self) -> bool:
        return bool(self.writers) and not self.available and not self.waits

    @property
    def has_partial_cache_hit(self) -> bool:
        return bool(self.available) and bool(self.writers) and not self.waits


@dataclass(frozen=True)
class ResolvedStageOutputBindings:
    available: tuple[ArtifactInstance[Any], ...]
    writers: tuple[ArtifactWriter[Any], ...]
