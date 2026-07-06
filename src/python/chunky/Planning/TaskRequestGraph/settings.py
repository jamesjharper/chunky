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
from ...Artifacts.fingerprints import FingerprintProvider
from .request_models import TaskRequestContext
from ...Stages.stage_manifest import StageManifest
from ...Stages.stage_models import (
    ManifestStageRegistration,
    ResolvedSettingEntry,
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
        inherited_settings: ResolvedSettingsView,
    ) -> StagePlanningContext:
        effective_settings = self.resolve_stage_settings(
            declared_setting_ids=registration.declared_setting_ids(),
            default_settings=state.request_settings.default_settings,
            inherited_settings=inherited_settings,
            user_settings=state.request_settings.user_settings,
        )
        return StagePlanningContext(
            stage_id=registration.stage_id,
            stage_fingerprint=state.manifest_graph.stage_fingerprint(registration.stage_id),
            requested_outputs=frozenset(declared_outputs),
            resolved_settings=effective_settings,
            inherited_settings=inherited_settings,
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

        resolved: dict[ArtifactKey, ResolvedSettingEntry] = {}

        for declared in declared_setting_ids:
            default_value = _find_exact_or_single_override(default_settings, declared)
            inherited_value = _find_exact_or_single_override(inherited_settings, declared)
            user_value = _find_exact(user_settings, declared)

            if default_value.present:
                resolved[declared] = ResolvedSettingEntry(
                    value=default_value.value,
                    fingerprint=default_value.fingerprint,
                )
            if user_value.present:
                resolved[declared] = ResolvedSettingEntry(
                    value=user_value.value,
                    fingerprint=user_value.fingerprint,
                )
            if inherited_value.present:
                # Edge-pushed settings are path-specific and intentionally win
                # over broader request-level defaults or user roots.
                resolved[declared] = ResolvedSettingEntry(
                    value=inherited_value.value,
                    fingerprint=inherited_value.fingerprint,
                )

        return ResolvedSettingsView(entries=resolved)

class _MaybeSetting:
    def __init__(
        self,
        present: bool,
        value: object | None = None,
        fingerprint: str | None = None,
    ) -> None:
        self.present = present
        self.value = value
        self.fingerprint = fingerprint


def _find_exact_or_single_override(
    values: ResolvedSettingsView,
    declared: ArtifactKey,
) -> _MaybeSetting:
    exact = values.get(declared)
    if exact is not None:
        return _MaybeSetting(True, exact, values.fingerprint(declared))

    matches = [
        (key, value, values.fingerprint(key))
        for key, value in values.items()
        if key.without_override() == declared.without_override()
    ]
    if not matches:
        return _MaybeSetting(False)
    if len(matches) > 1:
        rendered = ", ".join(
            str(key) for key, _, _ in sorted(matches, key=lambda item: str(item[0]))
        )
        raise StagePlanningError(
            f"multiple override settings resolved for {declared}: {rendered}"
        )
    return _MaybeSetting(True, matches[0][1], matches[0][2])


def settings_fingerprint(settings: ResolvedSettingsView) -> str:
    return FingerprintProvider.build_settings_fingerprint(
        settings.fingerprints(),
    )


def _find_exact(
    values: ResolvedSettingsView,
    declared: ArtifactKey,
) -> _MaybeSetting:
    exact = values.get(declared)
    if exact is not None:
        return _MaybeSetting(True, exact, values.fingerprint(declared))
    return _MaybeSetting(False)
