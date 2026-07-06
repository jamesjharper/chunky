from __future__ import annotations

"""Artifact fingerprint construction.
"""

import json
from dataclasses import asdict, is_dataclass
from typing import TypeVar
try:
    from blake3 import blake3
except ModuleNotFoundError:  # pragma: no cover - fallback for runnable examples
    import hashlib

    def blake3(raw: bytes):
        return hashlib.blake2b(raw, digest_size=32)

from .artifact_handles import ArtifactSourceHandle
from .artifact_models import ArtifactDeclaration, ArtifactKey

T = TypeVar("T")

def stable_hash_bytes(raw: bytes) -> str:
    return blake3(raw).hexdigest()

def stable_hash(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return stable_hash_bytes(raw)


class FingerprintProvider:
    @staticmethod
    def build_manifest_fingerprint(
        description: dict[str, object],
        *,
        manifest_version: str,
    ) -> str:
        return stable_hash(
            {
                **description,
                "manifest_version": manifest_version,
            }
        )

    @staticmethod
    def build_stage_fingerprint(
        *,
        stage_id: str,
        behavior_version: str,
    ) -> str:
        return stable_hash(
            {
                "stage": {
                    "stage_id": stage_id,
                    "behavior_version": behavior_version,
                }
            }
        )

    @staticmethod
    def build_setting_value_fingerprint(
        key: ArtifactKey,
        value: object,
    ) -> str:
        return stable_hash(
            {
                "artifact_id": str(key),
                "value": FingerprintProvider._normalize_setting_value(value),
            }
        )

    @staticmethod
    def build_settings_fingerprint(
        fingerprints: tuple[str, ...],
    ) -> str:
        if not fingerprints:
            return ""
        return stable_hash(list(fingerprints))

    async def build_intermediate_artifact_fingerprint(
        self,
        declaration: ArtifactDeclaration[T],
        pedigree_fingerprint: str,
    ) -> str:
        return stable_hash(
            {
                "artifact": {
                    "artifact_id": str(declaration.artifact_id),
                    "role": declaration.role.value,
                    "version": declaration.version,
                    "codec": {
                        "codec_id": declaration.codec.codec_id,
                        "codec_version": declaration.codec.codec_version,
                        "mime_type": declaration.codec.mime_type,
                        "extension": declaration.codec.extension,
                    },
                },
                "pedigree": pedigree_fingerprint,
            }
        )

    async def build_supplied_artifact_fingerprint(
        self,
        declaration: ArtifactDeclaration[T],
        source_handle: ArtifactSourceHandle[T],
    ) -> str:
        content_fingerprint = await source_handle.get_content_fingerprint(
            declaration.codec
        )
        return stable_hash(
            {
                "artifact": {
                    "artifact_id": str(declaration.artifact_id),
                    "role": declaration.role.value,
                    "version": declaration.version,
                },
                "content": content_fingerprint,
            }
        )


    def build_stage_pedigree_fingerprint(
        self,
        *,
        stage_fingerprint: str,
        input_fingerprints: dict[str, str],
        settings_fingerprint: str,
    ) -> str:
        return stable_hash(
            {
                "stage": stage_fingerprint,
                "inputs": dict(sorted(input_fingerprints.items())),
                "settings": settings_fingerprint,
            }
        )

    @staticmethod
    def _normalize_setting_value(value: object) -> object:
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, dict):
            return {
                str(key): FingerprintProvider._normalize_setting_value(inner)
                for key, inner in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                FingerprintProvider._normalize_setting_value(inner)
                for inner in value
            ]
        return value
