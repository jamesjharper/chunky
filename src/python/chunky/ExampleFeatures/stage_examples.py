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
from .transcribe_stage import TRANSCRIBE_STAGE, TranscribeExecutor

if TYPE_CHECKING:
    from ..Stages.stage_manifest import StageManifestBuilder


class WebVttTopologyExampleFeature:
    feature_id = "example.webvtt-topology"
    configuration_fingerprint = "webvtt-topology-example-v1"

    def install(self, builder: "StageManifestBuilder") -> None:
        builder.add_artifact(SOURCE_AUDIO)
        builder.add_artifact(AUDIO_SETTINGS)
        builder.add_artifact(SPEECH_AUDIO_SETTINGS)
        builder.add_artifact(MUSIC_AUDIO_SETTINGS)
        builder.add_stage(PREPARE_AUDIO_STAGE)
        builder.add_stage(TRANSCRIBE_STAGE)
        builder.add_stage(DIARIZE_STAGE)
        builder.add_stage(DETECT_MUSIC_STAGE)
        builder.add_stage(ALIGN_STAGE)
        builder.add_stage(RENDER_VTT_STAGE)


# Retained as an import-stable alias for the existing demo module.
SummaryExampleFeature = WebVttTopologyExampleFeature

__all__ = [
    "ALIGNMENT_SETTINGS",
    "ALIGN_STAGE",
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
    "TRANSCRIBE_STAGE",
    "TranscribeExecutor",
    "VTT_SETTINGS",
    "WEBVTT_TRANSCRIPT",
    "WebVttTopologyExampleFeature",
    "AlignExecutor",
]
