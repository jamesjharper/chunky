from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .artifact_codex_example import BytesCodec, JsonCodec
from .artifact_handles_examples import InMemorySource
from ..Artifacts.artifact_handles import ArtifactSourceHandle
from ..Artifacts.artifact_models import (
    ArtifactCapability,
    ArtifactDeclaration,
    ArtifactKey,
    ArtifactRole,
)
from ..Stages.stage_models import (
    ResourcePolicy,
    StageExecutionContext,
    StagePlanningContext,
)


class TextCodec:
    codec_id = "utf8-text"
    codec_version = 1
    mime_type = "text/plain"
    extension = ".txt"

    async def serialize(self, value: str) -> bytes:
        if not isinstance(value, str):
            raise TypeError(f"TextCodec expected str, got {type(value).__name__}")
        return value.encode("utf-8")

    async def deserialize(self, raw: bytes) -> str:
        return raw.decode("utf-8")


JsonObject = dict[str, Any]
SettingsObject = dict[str, Any]


class AudioSettings(dict[str, Any]):
    """Audio settings that preserve omitted fields until resolution."""

    defaults = {
        "sample_rate_hz": 44100,
        "channels": 1,
    }

    def __init__(self, values: Mapping[str, Any] | None = None):
        super().__init__(dict(values or {}))

    def resolved(self) -> "AudioSettings":
        resolved = dict(type(self).defaults)
        resolved.update(self)
        return AudioSettings(resolved)


class AudioSettingsDefaultValueSourceProvider:
    provider_id = "audio-settings-default-source-v1"

    async def resolve_source(
        self,
        supplied_source: ArtifactSourceHandle[AudioSettings] | None,
        *,
        declaration: ArtifactDeclaration[AudioSettings],
    ) -> ArtifactSourceHandle[AudioSettings]:
        if supplied_source is None:
            return InMemorySource(AudioSettings().resolved())
        supplied_value = await supplied_source.get_value(declaration.codec)
        return InMemorySource(_coerce_audio_settings(supplied_value).resolved())


def _coerce_audio_settings(value: object) -> AudioSettings:
    if isinstance(value, AudioSettings):
        return value
    if isinstance(value, Mapping):
        return AudioSettings(value)
    raise TypeError(f"audio settings must be mapping-like, got {type(value).__name__}")

SOURCE_AUDIO = ArtifactDeclaration[bytes](
    artifact_id=ArtifactKey.parse("source//audio"),
    role=ArtifactRole.SOURCE,
    version=1,
    codec=BytesCodec(),
    cacheable=False,
)

AUDIO_SETTINGS = ArtifactDeclaration[AudioSettings](
    artifact_id=ArtifactKey.parse("audio_preprocessing//audio_settings"),
    role=ArtifactRole.SETTINGS,
    version=1,
    codec=JsonCodec[AudioSettings](),
    cacheable=True,
    default_value_source_provider=AudioSettingsDefaultValueSourceProvider(),
)

SPEECH_AUDIO_SETTINGS = AUDIO_SETTINGS.override(
    override_domain="speech_processing",
)

MUSIC_AUDIO_SETTINGS = AUDIO_SETTINGS.override(
    override_domain="music_processing",
)

ASR_SETTINGS = ArtifactDeclaration[SettingsObject](
    artifact_id=ArtifactKey.parse("settings//asr"),
    role=ArtifactRole.SETTINGS,
    version=1,
    codec=JsonCodec[SettingsObject](),
    cacheable=False,
)

LANGUAGE_SETTINGS = ArtifactDeclaration[SettingsObject](
    artifact_id=ArtifactKey.parse("settings//language"),
    role=ArtifactRole.SETTINGS,
    version=1,
    codec=JsonCodec[SettingsObject](),
    cacheable=False,
)

DIARIZATION_SETTINGS = ArtifactDeclaration[SettingsObject](
    artifact_id=ArtifactKey.parse("settings//diarization"),
    role=ArtifactRole.SETTINGS,
    version=1,
    codec=JsonCodec[SettingsObject](),
    cacheable=False,
)

ALIGNMENT_SETTINGS = ArtifactDeclaration[SettingsObject](
    artifact_id=ArtifactKey.parse("settings//alignment"),
    role=ArtifactRole.SETTINGS,
    version=1,
    codec=JsonCodec[SettingsObject](),
    cacheable=False,
)

VTT_SETTINGS = ArtifactDeclaration[SettingsObject](
    artifact_id=ArtifactKey.parse("settings//vtt"),
    role=ArtifactRole.SETTINGS,
    version=1,
    codec=JsonCodec[SettingsObject](),
    cacheable=False,
)

RENDER_SETTINGS = ArtifactDeclaration[SettingsObject](
    artifact_id=ArtifactKey.parse("settings//render"),
    role=ArtifactRole.SETTINGS,
    version=1,
    codec=JsonCodec[SettingsObject](),
    cacheable=False,
)

PREPARED_AUDIO = ArtifactDeclaration[JsonObject](
    artifact_id=ArtifactKey.parse("audio//prepared"),
    role=ArtifactRole.INTERMEDIATE,
    version=1,
    codec=JsonCodec[JsonObject](),
)

TOKEN_TEXT = ArtifactDeclaration[JsonObject](
    artifact_id=ArtifactKey.parse("transcript//token_text"),
    role=ArtifactRole.INTERMEDIATE,
    version=1,
    codec=JsonCodec[JsonObject](),
)

SPEAKER_SEGMENTS = ArtifactDeclaration[JsonObject](
    artifact_id=ArtifactKey.parse("speaker//segments"),
    role=ArtifactRole.INTERMEDIATE,
    version=1,
    codec=JsonCodec[JsonObject](),
)

MUSIC_SPANS = ArtifactDeclaration[JsonObject](
    artifact_id=ArtifactKey.parse("audio//music_spans"),
    role=ArtifactRole.INTERMEDIATE,
    version=1,
    codec=JsonCodec[JsonObject](),
)

TIMED_WORDS = ArtifactDeclaration[JsonObject](
    artifact_id=ArtifactKey.parse("transcript//timed_words"),
    role=ArtifactRole.INTERMEDIATE,
    version=1,
    codec=JsonCodec[JsonObject](),
)

WEBVTT_TRANSCRIPT = ArtifactDeclaration[str](
    artifact_id=ArtifactKey.parse("render//webvtt_transcript"),
    role=ArtifactRole.TARGET,
    version=1,
    codec=TextCodec(),
    cacheable=False,
)


def fixed_outputs(*capabilities: ArtifactCapability):
    def resolve(
        _context: StagePlanningContext,
    ) -> tuple[ArtifactCapability, ...]:
        return capabilities

    return resolve


def unbound_resources(
    _context: StagePlanningContext,
) -> ResourcePolicy:
    return ResourcePolicy(classes=frozenset({"unbound"}))


def cpu_resources(
    _context: StagePlanningContext,
) -> ResourcePolicy:
    return ResourcePolicy(classes=frozenset({"cpu_compute_bound"}))


def topology_payload(
    context: StageExecutionContext,
    *,
    stage: str,
    extra: dict[str, object] | None = None,
) -> JsonObject:
    return {
        "stage": stage,
        "invocation_id": context.invocation_id,
        **(extra or {}),
    }
