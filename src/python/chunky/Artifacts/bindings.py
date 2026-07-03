from __future__ import annotations

"""Focused artifact binding and materialization helpers.

These helpers receive planner/cache results. They do not build the graph,
calculate pedigree, schedule stages, or choose caller output targets.
"""

from typing import Any, TypeVar

from .binding_models import (
    ArtifactHandle,
    ArtifactInstance,
    ArtifactReader,
    ResolvedStageOutputBindings,
    ArtifactWriter,
    StageCacheWait,
    StageOutputBindings,
)
from .artifact_handles import (
    ArtifactSourceHandle,
    ArtifactTargetHandle,
    IntermediateArtifactHandle,
)
from .artifact_models import ArtifactDeclaration
from ..CacheProviders.cache_models import (
    ArtifactCache,
    CacheHit,
    CacheWait,
    CacheWriteReservation,
    CacheableOutputAvailable,
    CacheableOutputBootstrap,
    CacheableOutputProduce,
    CacheableOutputWaiting,
)
from .fingerprints import (
    FingerprintProvider,
)

T = TypeVar("T")


class ArtifactBindingsProvider:
    """Cache-backed artifact bootstrap and materialization runtime.

    This mirrors the mock bindings provider shape more closely: callers interact with a
    single object that resolves stage outputs and materializes provided targets.
    The scheduler still owns waiting/retry policy; this provider only performs
    the artifact-resolution work.
    """

    def __init__(
        self,
        cache: ArtifactCache,
        fingerprint_provider: FingerprintProvider | None = None,
    ) -> None:
        self._cache = cache
        self._fingerprint_provider = fingerprint_provider or FingerprintProvider()

    @property
    def fingerprint_provider(self) -> FingerprintProvider:
        return self._fingerprint_provider

    async def _bind_cacheable_output(
        self,
        declaration: ArtifactDeclaration[T],
        pedigree_fingerprint: str,
        *,
        force_cache_overwrite: bool = False,
    ) -> CacheableOutputBootstrap[T]:
        """Bind one cacheable stage output into available, produce, or waiting."""

        # The publication handle acts as the produced artifact's source-handle
        # contribution during fingerprinting. It returns empty content by design.
        publication = IntermediateArtifactHandle[T]()
        fingerprint = await self._fingerprint_provider.build_intermediate_artifact_fingerprint(
            declaration,
            pedigree_fingerprint,
        )
        resolution = await self._cache.resolve(
            declaration,
            fingerprint,
            force_write=force_cache_overwrite,
        )

        if isinstance(resolution, CacheHit):
            return CacheableOutputAvailable(
                ArtifactInstance(
                    ArtifactHandle(
                        declaration=declaration,
                        fingerprint=fingerprint,
                        source=resolution.source_handle,
                    )
                )
            )

        if isinstance(resolution, CacheWriteReservation):
            handle = ArtifactHandle(
                declaration=declaration,
                fingerprint=fingerprint,
                source=publication,
                targets=(
                    # Durable semantic representation commits first.
                    resolution.target_handle,
                    # Downstream stages see the value only after durable commit.
                    publication,
                ),
            )
            return CacheableOutputProduce(ArtifactWriter(handle))

        if isinstance(resolution, CacheWait):
            # The wait handle is only a notification; it owns no source or writer.
            return CacheableOutputWaiting(resolution)

        raise TypeError(f"unsupported cache resolution: {resolution!r}")

    async def _bind_cacheable_stage_outputs(
        self,
        output_declarations: list[ArtifactDeclaration[Any]],
        pedigree_fingerprint: str,
        *,
        force_cache_overwrite: bool = False,
    ) -> StageOutputBindings:
        """Resolve one atomic stage-output bootstrap pass."""

        available: list[ArtifactInstance[Any]] = []
        writers: list[ArtifactWriter[Any]] = []
        waits: list[StageCacheWait] = []

        for declaration in output_declarations:
            bootstrap = await self._bind_cacheable_output(
                declaration,
                pedigree_fingerprint,
                force_cache_overwrite=force_cache_overwrite,
            )
            if isinstance(bootstrap, CacheableOutputAvailable):
                available.append(bootstrap.instance)
            elif isinstance(bootstrap, CacheableOutputProduce):
                writers.append(bootstrap.writer)
            else:
                waits.append(
                    StageCacheWait(
                        artifact_id=declaration.artifact_id,
                        cache_wait=bootstrap.wait,
                    )
                )

        return StageOutputBindings(
            available=tuple(available),
            writers=tuple(writers),
            waits=tuple(waits),
        )

    async def _bind_non_cacheable_stage_outputs(
        self,
        output_declarations: list[ArtifactDeclaration[Any]],
        pedigree_fingerprint: str,
    ) -> tuple[ArtifactWriter[Any], ...]:
        writers: list[ArtifactWriter[Any]] = []

        for declaration in output_declarations:
            publication = IntermediateArtifactHandle[Any]()
            fingerprint = await self._fingerprint_provider.build_intermediate_artifact_fingerprint(
                declaration,
                pedigree_fingerprint,
            )
            writers.append(
                ArtifactWriter(
                    ArtifactHandle(
                        declaration=declaration,
                        fingerprint=fingerprint,
                        source=publication,
                        targets=(publication,),
                    )
                )
            )

        return tuple(writers)

    async def bind_supplied(
        self,
        declaration: ArtifactDeclaration[T],
        source: ArtifactSourceHandle[T],
    ) -> ArtifactInstance[T]:
        """Bind a supplied graph root using content and an empty pedigree."""

        fingerprint = await self._fingerprint_provider.build_supplied_artifact_fingerprint(
            declaration,
            source,
        )
        return ArtifactInstance(
            ArtifactHandle(
                declaration=declaration,
                fingerprint=fingerprint,
                source=source,
            )
        )

    async def bind_stage_outputs(
        self,
        output_declarations: list[ArtifactDeclaration[Any]],
        pedigree_fingerprint: str,
    ) -> ResolvedStageOutputBindings | StageCacheWait:
        cacheable_outputs, non_cacheable_outputs = _partition_output_declarations(
            output_declarations
        )
        force_cache_overwrite = False

        while True:
            bindings = await self._bind_cacheable_stage_outputs(
                cacheable_outputs,
                pedigree_fingerprint,
                force_cache_overwrite=force_cache_overwrite,
            )
            force_cache_overwrite = False

            if bindings.has_waits:
                # A production cache hit owns a durable read reservation. This
                # pass is being discarded, so provisional hit handles must be
                # closed before waiting and retrying the whole output set.
                await close_instances(bindings.available)
                await abort_writers(bindings.writers)
                return bindings.waits[0]

            if bindings.has_partial_cache_hit:
                # Partial hits are refreshed as one coherent cacheable output
                # set. Release both provisional readers and writers before the
                # force-write retry.
                await close_instances(bindings.available)
                await abort_writers(bindings.writers)
                force_cache_overwrite = True
                continue

            non_cacheable_writers = await self._bind_non_cacheable_stage_outputs(
                non_cacheable_outputs,
                pedigree_fingerprint,
            )

            if bindings.all_available and not non_cacheable_writers:
                return ResolvedStageOutputBindings(bindings.available, ())

            if bindings.all_reserved_for_write:
                return ResolvedStageOutputBindings(
                    (),
                    bindings.writers + non_cacheable_writers,
                )

            if bindings.all_available:
                return ResolvedStageOutputBindings(
                    bindings.available,
                    non_cacheable_writers,
                )

            raise AssertionError("unexpected stage output bootstrap state")

    async def materialize(
        self,
        instance: ArtifactInstance[T],
        target: ArtifactTargetHandle[T],
    ) -> None:
        """Write one already-available semantic artifact to a caller target."""

        value = await ArtifactReader(instance.handle).read()
        materialization_handle = ArtifactHandle(
            declaration=instance.handle.declaration,
            fingerprint=instance.fingerprint,
            source=instance.handle.source,
            targets=(target,),
        )
        await ArtifactWriter(materialization_handle).write(value)

    @staticmethod
    def merge_stage_output_instances(
        available: tuple[ArtifactInstance[Any], ...],
        produced: tuple[ArtifactInstance[Any], ...],
    ) -> tuple[ArtifactInstance[Any], ...]:
        merged = {
            instance.handle.declaration.artifact_id: instance
            for instance in available
        }
        for instance in produced:
            merged[instance.handle.declaration.artifact_id] = instance
        return tuple(merged.values())


def _partition_output_declarations(
    output_declarations: list[ArtifactDeclaration[Any]],
) -> tuple[list[ArtifactDeclaration[Any]], list[ArtifactDeclaration[Any]]]:
    cacheable: list[ArtifactDeclaration[Any]] = []
    non_cacheable: list[ArtifactDeclaration[Any]] = []

    for declaration in output_declarations:
        if declaration.cacheable:
            cacheable.append(declaration)
        else:
            non_cacheable.append(declaration)

    return cacheable, non_cacheable


async def abort_writers(
    writers: list[ArtifactWriter[Any]] | tuple[ArtifactWriter[Any], ...],
) -> None:
    for writer in reversed(writers):
        await writer.abort_if_unwritten()


async def close_instances(
    instances: list[ArtifactInstance[Any]] | tuple[ArtifactInstance[Any], ...],
) -> None:
    """Close task-local source handles for a discarded bootstrap pass."""

    seen: set[int] = set()
    for instance in reversed(instances):
        source = instance.handle.source
        if source is None or id(source) in seen:
            continue
        seen.add(id(source))
        await source.close()
