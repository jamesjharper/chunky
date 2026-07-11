from __future__ import annotations

"""Executable topology example matching the staged WebVTT pipeline sketch."""

from typing import TYPE_CHECKING

from .align_stage import ALIGN_STAGE, AlignExecutor
from .common import (
    ALIGNMENT_SETTINGS,
    ASR_SETTINGS,
    AUDIO_SETTINGS,
    AudioSettings,
    DIARIZATION_SETTINGS,
    LANGUAGE_SETTINGS,
    MUSIC_AUDIO_SETTINGS,
    MUSIC_SPANS,
    PREPARED_AUDIO,
    RENDER_SETTINGS,
    SOURCE_AUDIO,
    SPEAKER_SEGMENTS,
    SPEECH_AUDIO_SETTINGS,
    TIMED_WORDS,
    TOKEN_TEXT,
    VTT_SETTINGS,
    WEBVTT_TRANSCRIPT,
    JsonObject,
    SettingsObject,
    TextCodec,
)
from .detect_music_stage import DETECT_MUSIC_STAGE, DetectMusicExecutor
from .diarize_stage import DIARIZE_STAGE, DiarizeExecutor
from .prepare_audio_stage import PREPARE_AUDIO_STAGE, PrepareAudioExecutor
from .render_vtt_stage import RENDER_VTT_STAGE, RenderVttExecutor
from .transcribe_stage import (
    ASR_API_KEY_SETTING,
    ASR_CONCURRENCY_LIMIT_SETTING,
    ASR_PROVIDER_NAME_SETTING,
    TranscribeExecutor,
    build_transcribe_stage,
)

if TYPE_CHECKING:
    from ..Pipeline.runtime import PipelineRuntimeServicesBuilder, PipelineRuntimeSettingsBuilder
    from ..Stages.stage_manifest import StageManifestBuilder


class WebVttTopologyExampleFeature:
    feature_id = "example.webvtt-topology"
    configuration_fingerprint = "webvtt-topology-example-v1"

    def __init__(
        self,
        *,
        provider_name: str | None = None,
        api_key: str | None = None,
        concurrency_limit: int | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._api_key = api_key
        self._concurrency_limit = concurrency_limit

    def install(
        self,
        manifest_builder: "StageManifestBuilder",
        service_locator: "PipelineRuntimeServicesBuilder",
        env_settings: "PipelineRuntimeSettingsBuilder",
    ) -> None:
        del service_locator
        if self._provider_name is not None:
            env_settings.set(ASR_PROVIDER_NAME_SETTING, self._provider_name)
        if self._api_key is not None:
            env_settings.set(ASR_API_KEY_SETTING, self._api_key)
        if self._concurrency_limit is not None:
            env_settings.set(ASR_CONCURRENCY_LIMIT_SETTING, self._concurrency_limit)

        manifest_builder.add_artifact(SOURCE_AUDIO)
        manifest_builder.add_artifact(AUDIO_SETTINGS)
        manifest_builder.add_artifact(SPEECH_AUDIO_SETTINGS)
        manifest_builder.add_artifact(MUSIC_AUDIO_SETTINGS)
        manifest_builder.add_stage(PREPARE_AUDIO_STAGE)
        manifest_builder.add_stage(build_transcribe_stage())
        manifest_builder.add_stage(DIARIZE_STAGE)
        manifest_builder.add_stage(DETECT_MUSIC_STAGE)
        manifest_builder.add_stage(ALIGN_STAGE)
        manifest_builder.add_stage(RENDER_VTT_STAGE)


SummaryExampleFeature = WebVttTopologyExampleFeature

__all__ = [
    "ALIGNMENT_SETTINGS",
    "ALIGN_STAGE",
    "ASR_API_KEY_SETTING",
    "ASR_CONCURRENCY_LIMIT_SETTING",
    "ASR_PROVIDER_NAME_SETTING",
    "ASR_SETTINGS",
    "AUDIO_SETTINGS",
    "AudioSettings",
    "DIARIZATION_SETTINGS",
    "DETECT_MUSIC_STAGE",
    "DetectMusicExecutor",
    "DIARIZE_STAGE",
    "DiarizeExecutor",
    "JsonObject",
    "LANGUAGE_SETTINGS",
    "MUSIC_AUDIO_SETTINGS",
    "MUSIC_SPANS",
    "PREPARED_AUDIO",
    "PREPARE_AUDIO_STAGE",
    "PrepareAudioExecutor",
    "RENDER_SETTINGS",
    "RENDER_VTT_STAGE",
    "RenderVttExecutor",
    "SOURCE_AUDIO",
    "SPEAKER_SEGMENTS",
    "SPEECH_AUDIO_SETTINGS",
    "SettingsObject",
    "SummaryExampleFeature",
    "TextCodec",
    "TIMED_WORDS",
    "TOKEN_TEXT",
    "TranscribeExecutor",
    "VTT_SETTINGS",
    "WEBVTT_TRANSCRIPT",
    "WebVttTopologyExampleFeature",
    "AlignExecutor",
]
