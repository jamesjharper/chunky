from __future__ import annotations

from ..Stages.stage_manifest import StageManifestBuilder
from ..Stages.stage_models import PipelineFeature
from .env_settings_features import PipelineRuntimeSettingsBuilder
from .service_features import PipelineRuntimeServicesBuilder


class ChunkyPipelineBuilder:
    def __init__(self) -> None:
        self._manifest_builder = StageManifestBuilder()
        self._runtime_services = PipelineRuntimeServicesBuilder()
        self._env_settings = PipelineRuntimeSettingsBuilder()
        self._installed_feature_ids: set[str] = set()
        self._built = False

    def install(self, feature: PipelineFeature) -> "ChunkyPipelineBuilder":
        self._assert_not_built()
        if feature.feature_id in self._installed_feature_ids:
            raise RuntimeError(f"duplicate pipeline feature: {feature.feature_id}")
        self._installed_feature_ids.add(feature.feature_id)
        try:
            feature.install(
                self._manifest_builder,
                self._runtime_services,
                self._env_settings,
            )
            self._manifest_builder.record_feature(
                feature_id=feature.feature_id,
                configuration_fingerprint=feature.configuration_fingerprint,
            )
        except BaseException:
            self._installed_feature_ids.remove(feature.feature_id)
            raise
        return self

    def build(self):
        from .pipeline import ChunkyPipeline

        self._assert_not_built()
        manifest = self._manifest_builder.freeze()
        for registration in manifest.stages.values():
            self._env_settings.install_stage_settings(
                registration.stage_id,
                registration.runtime_settings,
            )
        runtime_settings = self._env_settings.freeze()
        runtime_services = self._runtime_services.freeze()
        self._built = True

        return ChunkyPipeline(
            stage_manifest=manifest,
            runtime_services=runtime_services,
            runtime_settings=runtime_settings,
        )

    def _assert_not_built(self) -> None:
        if self._built:
            raise RuntimeError("pipeline builder is single-use and has already built")
