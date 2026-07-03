from __future__ import annotations

"""Reservation-owning source, target, and wait handles."""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Generic, Literal, TypeVar

from ...Artifacts.artifact_models import ArtifactDeclaration, Codec
from ...Artifacts.fingerprints import stable_hash_bytes
from ..cache_models import CacheWaitHandle
from ._descriptors import CacheHitDescriptor, CacheWriteDescriptor
from .errors import (
    FileCacheProviderEntryCorruptError,
    FileCacheProviderReservationLostError,
)

if TYPE_CHECKING:
    from .provider import FileCacheProvider

T = TypeVar("T")
ReservationKind = Literal["read", "write"]


class _FileCacheProviderReservationHeartbeat:
    """Refreshes a reservation lease while its owning handle remains live."""

    def __init__(
        self,
        provider: FileCacheProvider,
        kind: ReservationKind,
        reservation_id: str,
    ) -> None:
        self._provider = provider
        self._kind = kind
        self._reservation_id = reservation_id
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(
                    self._provider.config.heartbeat_interval_seconds
                )
                active = await self._provider._heartbeat(
                    self._kind,
                    self._reservation_id,
                )
                if not active:
                    return
            except asyncio.CancelledError:
                return
            except Exception:
                # A transient database failure is retried on the next interval.
                # Read and commit operations still verify ownership themselves.
                continue

    async def stop(self) -> None:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


class FileCacheProviderWaitHandle(CacheWaitHandle):
    """Cross-process wait implemented by polling the observed write lease."""

    def __init__(
        self,
        provider: FileCacheProvider,
        fingerprint: str,
        observed_reservation_id: str,
    ) -> None:
        self._provider = provider
        self._fingerprint = fingerprint
        self._observed_reservation_id = observed_reservation_id

    async def wait(self) -> None:
        while True:
            active = await self._provider._observed_writer_is_active(
                self._fingerprint,
                self._observed_reservation_id,
            )
            if not active:
                return
            # This sleep is the cancellation seam. Cancelling this waiter does
            # not cancel or release the other task's write reservation.
            await asyncio.sleep(
                self._provider.config.wait_poll_interval_seconds
            )


class FileCacheProviderSourceHandle(Generic[T]):
    """Cache-hit source handle that owns a shared SQLite read reservation."""

    kind = "file-cache-provider"

    def __init__(
        self,
        provider: FileCacheProvider,
        descriptor: CacheHitDescriptor,
    ) -> None:
        self._provider = provider
        self._entry_id = descriptor.entry_id
        self._reservation_id = descriptor.reservation_id
        self._data_path = descriptor.data_path
        self._data_size = descriptor.data_size
        self._data_digest = descriptor.data_digest
        self._fingerprint = descriptor.fingerprint
        self._closed = False
        self._heartbeat = _FileCacheProviderReservationHeartbeat(
            provider,
            "read",
            self._reservation_id,
        )

    @property
    def data_path(self) -> Path:
        return self._data_path

    @property
    def reservation_id(self) -> str:
        return self._reservation_id

    async def get_value(self, codec: Codec[T]) -> T:
        if self._closed:
            raise RuntimeError("cache read handle is closed")
        if not await self._provider._touch_read_reservation(self._reservation_id):
            raise FileCacheProviderReservationLostError(
                f"cache read reservation was lost: {self._reservation_id}"
            )

        try:
            raw = await asyncio.to_thread(self._data_path.read_bytes)
            if len(raw) != self._data_size:
                raise FileCacheProviderEntryCorruptError(
                    f"cache entry size mismatch for {self._fingerprint}"
                )
            digest = stable_hash_bytes(raw)
            if digest != self._data_digest:
                raise FileCacheProviderEntryCorruptError(
                    f"cache entry digest mismatch for {self._fingerprint}"
                )
            return await codec.deserialize(raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._provider.invalidate_entry(
                self._entry_id,
                reason=f"read failed: {type(exc).__name__}: {exc}",
            )
            raise

    async def get_content_fingerprint(self, _codec: Codec[T]) -> str:
        # The final semantic fingerprint was validated before this handle was
        # created. Cache-backed sources do not re-hash produced content.
        return ""

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._heartbeat.stop()
        await self._provider._release_read_reservation(self._reservation_id)


class FileCacheProviderTargetHandle(Generic[T]):
    """Reserved cache target with prepare/commit/abort lifecycle semantics."""

    kind = "file-cache-provider"

    def __init__(
        self,
        provider: FileCacheProvider,
        declaration: ArtifactDeclaration[T],
        descriptor: CacheWriteDescriptor,
    ) -> None:
        self._provider = provider
        self._declaration = declaration
        self._reservation_id = descriptor.reservation_id
        self._entry_id = descriptor.entry_id
        self._fingerprint = descriptor.fingerprint
        self._final_path = descriptor.final_path
        self._temporary_path: Path | None = None
        self._data_size: int | None = None
        self._data_digest: str | None = None
        self._committed = False
        self._aborted = False
        self._heartbeat = _FileCacheProviderReservationHeartbeat(
            provider,
            "write",
            self._reservation_id,
        )

    @property
    def final_path(self) -> Path:
        return self._final_path

    @property
    def temporary_path(self) -> Path | None:
        return self._temporary_path

    @property
    def reservation_id(self) -> str:
        return self._reservation_id

    async def prepare(
        self,
        value: T,
        codec: Codec[T],
        fingerprint: str,
    ) -> None:
        if self._committed or self._aborted:
            raise RuntimeError("cache target is already terminal")
        if self._temporary_path is not None:
            raise RuntimeError("cache target is already prepared")
        if fingerprint != self._fingerprint:
            raise ValueError("writer fingerprint does not match reserved fingerprint")

        temporary_path = self._provider._filesystem.new_temporary_path(
            self._final_path
        )
        self._temporary_path = temporary_path
        try:
            # Register before creation so an aggressive orphan scan cannot
            # remove a newly staged file before it appears in SQLite metadata.
            await self._provider._record_preparing_write(
                self._reservation_id,
                temporary_path,
            )
            raw = await codec.serialize(value)
            size, digest = await asyncio.to_thread(
                self._provider._filesystem.write_temporary_file,
                temporary_path,
                raw,
            )
            await self._provider._record_prepared_write(
                self._reservation_id,
                temporary_path,
                size,
                digest,
            )
        except BaseException:
            # ArtifactWriter cannot add a failing target to its prepared list,
            # so this target must release its own reservation on prepare error.
            await asyncio.shield(self.abort())
            raise

        self._data_size = size
        self._data_digest = digest

    async def commit(self) -> None:
        if self._committed or self._aborted:
            return
        if (
            self._temporary_path is None
            or self._data_size is None
            or self._data_digest is None
        ):
            raise RuntimeError("cache target has not been prepared")

        try:
            await self._provider._commit_write(
                reservation_id=self._reservation_id,
                entry_id=self._entry_id,
                fingerprint=self._fingerprint,
                declaration=self._declaration,
                temporary_path=self._temporary_path,
                final_path=self._final_path,
                data_size=self._data_size,
                data_digest=self._data_digest,
            )
        except BaseException:
            await asyncio.shield(self.abort())
            raise

        self._temporary_path = None
        self._committed = True
        await self._heartbeat.stop()

    async def abort(self) -> None:
        if self._committed or self._aborted:
            return
        self._aborted = True
        await self._heartbeat.stop()
        temporary_path = self._temporary_path
        self._temporary_path = None
        await self._provider._abort_write(
            reservation_id=self._reservation_id,
            entry_id=self._entry_id,
            temporary_path=temporary_path,
            final_path=self._final_path,
        )
