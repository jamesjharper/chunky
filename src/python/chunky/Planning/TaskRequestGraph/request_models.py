from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ...Artifacts.artifact_models import ArtifactKey, ArtifactRole
from ...Artifacts.binding_models import ArtifactInstance, ArtifactReader
from ..manifest_graph import StageManifestGraph
from ...Stages.stage_manifest import StageManifest
from ...Stages.stage_models import (
    PlanningRunPolicy,
    ResolvedSettingEntry,
    ResolvedSettingsView,
)


@dataclass(frozen=True)
class PlannerRequestSettings:
    """Request-scoped settings already read from supplied/default artifacts."""

    default_settings: ResolvedSettingsView
    user_settings: ResolvedSettingsView

    @classmethod
    async def read_from(
        cls,
        manifest: StageManifest,
        supplied_instances: dict[ArtifactKey, ArtifactInstance[Any]],
        *,
        user_supplied_settings: Iterable[ArtifactKey],
    ) -> "PlannerRequestSettings":
        user_supplied_settings = frozenset(user_supplied_settings)
        defaults: dict[ArtifactKey, ResolvedSettingEntry] = {}
        user: dict[ArtifactKey, ResolvedSettingEntry] = {}
        setting_ids = manifest.find_artifacts_by_role(
            supplied_instances,
            role=ArtifactRole.SETTINGS,
        )

        for artifact_id, instance in supplied_instances.items():
            if artifact_id not in setting_ids:
                continue
            if artifact_id in user_supplied_settings:
                values = user
            else:
                values = defaults
            values[artifact_id] = ResolvedSettingEntry(
                value=await ArtifactReader(instance.handle).read(),
                fingerprint=instance.fingerprint,
            )

        return cls(
            ResolvedSettingsView(entries=defaults),
            ResolvedSettingsView(entries=user),
        )

    def merge_with_inherited(
        self,
        inherited_settings: ResolvedSettingsView,
    ) -> ResolvedSettingsView:
        return self.default_settings.merge(inherited_settings, self.user_settings)


@dataclass(frozen=True)
class TaskRequestContext:
    """Immutable inputs shared by one evaluated-plan expansion pass."""

    manifest: StageManifest
    manifest_graph: StageManifestGraph
    requested_output_ids: tuple[ArtifactKey, ...]
    supplied_instances: dict[ArtifactKey, ArtifactInstance[Any]]
    policy: PlanningRunPolicy
    request_settings: PlannerRequestSettings
