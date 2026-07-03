from __future__ import annotations

"""Artifact fingerprint construction.
"""

import json
from typing import TypeVar
try:
    from blake3 import blake3
except ModuleNotFoundError:  # pragma: no cover - fallback for runnable examples
    import hashlib

    def blake3(raw: bytes):
        return hashlib.blake2b(raw, digest_size=32)

from .artifact_handles import ArtifactSourceHandle
from .artifact_models import ArtifactDeclaration

T = TypeVar("T")


def stable_hash_bytes(raw: bytes) -> str:
    return blake3(raw).hexdigest()


def stable_hash(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return stable_hash_bytes(raw)


class FingerprintProvider:
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
        stage_id: str,
        behavior_version: str,
        input_fingerprints: dict[str, str],
        effective_settings_fingerprint: str,
    ) -> str:
        """Build the planner/runner seam used by the stage example.

        Artifact-specific identity and codec metadata are added later by
        ``build_intermediate_artifact_fingerprint``.  Registration version is
        deliberately excluded because it describes planning metadata rather
        than semantic execution behavior.
        """

        return stable_hash(
            {
                "stage": {
                    "stage_id": stage_id,
                    "behavior_version": behavior_version,
                },
                "inputs": dict(sorted(input_fingerprints.items())),
                "effective_settings_fingerprint": effective_settings_fingerprint,
            }
        )
