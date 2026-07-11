from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..Artifacts.artifact_models import ArtifactKey, ArtifactRole
from ..Bindings.binding_models import ArtifactInstance
from ..Bindings.bindings import ArtifactBindingsProvider
from ..Stages.stage_manifest import StageManifest


class PlannerDefaultSettingsResolver:
    def __init__(
        self,
        manifest: StageManifest,
        artifact_bindings_provider: ArtifactBindingsProvider,
    ) -> None:
        self._manifest = manifest
        self._artifact_bindings_provider = artifact_bindings_provider

    async def resolve_defaults(
        self,
        supplied: Mapping[ArtifactKey, ArtifactInstance[Any]],
    ) -> dict[ArtifactKey, ArtifactInstance[Any]]:
        resolved: dict[ArtifactKey, ArtifactInstance[Any]] = {}
        for artifact_id, declaration in self._manifest.artifacts.items():
            if (
                declaration.role is ArtifactRole.SETTINGS
                and declaration.default_value_source_provider is not None
                and artifact_id not in supplied
            ):
                source = await declaration.default_value_source_provider.resolve_source(
                    None,
                    declaration=declaration,
                )
                resolved[artifact_id] = await self._artifact_bindings_provider.bind_supplied(
                    declaration,
                    source,
                )
        return resolved
