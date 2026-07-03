from __future__ import annotations

"""Settings resolution for planning.

The planner uses settings in two distinct ways:

* ``inherited_settings`` are propagated along artifact edges, often as override
  settings such as ``speech_processing//audio_preprocessing//audio_settings``.
* ``resolved_settings`` are the settings a concrete stage invocation consumes
  and therefore contribute to its semantic fingerprint.

The precedence is: defaults < user-configured settings < inherited settings.
Path-specific pushed settings are the final layer so downstream context can
override broader request-level user configuration.
"""

from ...Artifacts.artifact_models import ArtifactKey
from ...Artifacts.fingerprints import stable_hash
from .request_models import TaskRequestContext
from ...Stages.stage_manifest import StageManifest
from ...Stages.stage_models import (
    ManifestStageRegistration,
    _ResolvedSettingDict,
    ResolvedSettingsView,
    StagePlanningContext,
    StagePlanningError,
)


class PlannerSettingsResolver:
    def __init__(self, manifest: StageManifest) -> None:
        self._manifest = manifest

    def build_stage_context(
        self,
        state: TaskRequestContext,
        registration: ManifestStageRegistration,
        declared_outputs: set[ArtifactKey],
        inherited_settings: tuple[tuple[ArtifactKey, object], ...],
    ) -> StagePlanningContext:
        inherited_view = ResolvedSettingsView(dict(inherited_settings))
        effective_settings = self.resolve_stage_settings(
            declared_setting_ids=registration.declared_setting_ids(),
            default_settings=state.request_settings.default_settings,
            inherited_settings=inherited_view,
            user_settings=state.request_settings.user_settings,
        )
        return StagePlanningContext(
            stage_id=registration.stage_id,
            requested_outputs=frozenset(declared_outputs),
            resolved_settings=effective_settings,
            inherited_settings=inherited_view,
            run_policy=state.policy,
        )

    def resolve_stage_settings(
        self,
        *,
        declared_setting_ids: frozenset[ArtifactKey],
        default_settings: ResolvedSettingsView,
        inherited_settings: ResolvedSettingsView,
        user_settings: ResolvedSettingsView,
    ) -> ResolvedSettingsView:

        if not declared_setting_ids:
            return ResolvedSettingsView()

        default_items = dict(default_settings.items())
        inherited_items = dict(inherited_settings.items())
        user_items = dict(user_settings.items())
        resolved: dict[ArtifactKey, object] = {}

        for declared in declared_setting_ids:
            default_value = _find_exact_or_single_override(default_items, declared)
            inherited_value = _find_exact_or_single_override(inherited_items, declared)
            user_value = _find_exact(user_items, declared)

            if default_value.present:
                resolved[declared] = default_value.value
            if user_value.present:
                resolved[declared] = user_value.value
            if inherited_value.present:
                # Edge-pushed settings are path-specific and intentionally win
                # over broader request-level defaults or user roots.
                resolved[declared] = inherited_value.value

        return ResolvedSettingsView(resolved)

class _MaybeSetting:
    def __init__(self, present: bool, value: object | None = None) -> None:
        self.present = present
        self.value = value


def _find_exact_or_single_override(
    values: dict[ArtifactKey, object],
    declared: ArtifactKey,
) -> _MaybeSetting:
    if declared in values:
        return _MaybeSetting(True, values[declared])

    matches = [
        (key, value)
        for key, value in values.items()
        if key.without_override() == declared.without_override()
    ]
    if not matches:
        return _MaybeSetting(False)
    if len(matches) > 1:
        rendered = ", ".join(str(key) for key, _ in sorted(matches, key=lambda item: str(item[0])))
        raise StagePlanningError(
            f"multiple override settings resolved for {declared}: {rendered}"
        )
    return _MaybeSetting(True, matches[0][1])


def settings_fingerprint(settings: ResolvedSettingsView) -> str:
    items = settings.items()
    if not items:
        return ""
    
    # This implementation might be problematic as it requires internal knowledge of the setting 
    # maybe revisit later. If implemented in a language like rust this might just need every setting to support serde json or something
    return stable_hash(
        {
            "settings": {
                str(key): dict(value) if isinstance(value, _ResolvedSettingDict) else value
                for key, value in items
            }
        }
    )


def _find_exact(
    values: dict[ArtifactKey, object],
    declared: ArtifactKey,
) -> _MaybeSetting:
    if declared in values:
        return _MaybeSetting(True, values[declared])
    return _MaybeSetting(False)
