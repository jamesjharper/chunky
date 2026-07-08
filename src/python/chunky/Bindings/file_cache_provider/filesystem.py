from __future__ import annotations

"""Filesystem primitives used by the cache provider and metadata store."""

import asyncio
import os
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from ...Artifacts.fingerprints import stable_hash_bytes
from ._constants import SAFE_EXTENSION
from .configuration import FileCacheProviderConfig
from .errors import FileCacheProviderError


class FileCacheProviderFilesystem:
    """Owns cache paths, atomic staging, fsync, and orphan-file cleanup."""

    def __init__(
        self,
        root: Path,
        objects_root: Path,
        config: FileCacheProviderConfig,
    ) -> None:
        self.root = root
        self.objects_root = objects_root
        self.config = config

    def new_generation_path(
        self,
        fingerprint: str,
        entry_id: str,
        extension: str,
    ) -> Path:
        key = stable_hash_bytes(fingerprint.encode("utf-8"))
        safe_extension = extension if SAFE_EXTENSION.fullmatch(extension) else ".blob"
        return self.objects_root / key[:2] / key / f"{entry_id}{safe_extension}"

    def relative_path(self, path: Path) -> str:
        resolved = path.resolve(strict=False)
        try:
            relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise FileCacheProviderError(
                f"cache path escapes cache root: {path}"
            ) from exc
        return relative.as_posix()

    @staticmethod
    def new_temporary_path(final_path: Path) -> Path:
        """Create a unique staging name in the final file's directory.

        Keeping the staging path beside the final generation guarantees that
        ``os.replace`` remains a same-filesystem atomic rename rather than
        degrading into a cross-device copy.
        """

        return final_path.with_name(f".{final_path.name}.{uuid4().hex}.tmp")

    def write_temporary_file(self, temporary_path: Path, raw: bytes) -> tuple[int, str]:
        temporary_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(temporary_path, flags, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=True) as stream:
                stream.write(raw)
                stream.flush()
                if self.config.fsync_data:
                    os.fsync(stream.fileno())
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            temporary_path.unlink(missing_ok=True)
            raise
        return len(raw), stable_hash_bytes(raw)

    @staticmethod
    def fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        fd = os.open(directory, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    async def remove_paths(self, paths: Iterable[Path]) -> int:
        unique = tuple(dict.fromkeys(paths))
        if not unique:
            return 0

        def remove() -> int:
            removed = 0
            for path in unique:
                try:
                    existed = path.exists()
                    path.unlink(missing_ok=True)
                    if existed:
                        removed += 1
                except IsADirectoryError:
                    continue
            return removed

        return await asyncio.to_thread(remove)

    async def cleanup_orphan_files(
        self,
        referenced_paths: set[str],
        grace_seconds: float,
    ) -> int:
        now = __import__("time").time()
        removed = 0
        if not self.objects_root.exists():
            return 0
        for path in self.objects_root.rglob("*"):
            if not path.is_file():
                continue
            relative = self.relative_path(path)
            if relative in referenced_paths:
                continue
            try:
                if now - path.stat().st_mtime < grace_seconds:
                    continue
                await asyncio.to_thread(path.unlink, missing_ok=True)
                removed += 1
            except FileNotFoundError:
                continue
        await asyncio.to_thread(self.remove_empty_object_directories)
        return removed

    def remove_empty_object_directories(self) -> None:
        directories = sorted(
            (path for path in self.objects_root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                pass
