from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.python.chunky.Bindings.bindings import close_instances
from src.python.chunky.ExampleFeatures.artifact_handles_examples import FileSource, InMemorySource
from src.python.chunky.ExampleFeatures.stage_examples import (
    ALIGNMENT_SETTINGS,
    ASR_SETTINGS,
    AUDIO_SETTINGS,
    AudioSettings,
    DIARIZATION_SETTINGS,
    LANGUAGE_SETTINGS,
    MUSIC_AUDIO_SETTINGS,
    RENDER_SETTINGS,
    SPEECH_AUDIO_SETTINGS,
    VTT_SETTINGS,
    WEBVTT_TRANSCRIPT,
    SummaryExampleFeature,
)
from src.python.chunky.Pipeline.pipeline import (
    ChunkyPipeline,
    FileCacheFeature,
    RunRequest,
)
from src.python.chunky.Planning.settings import PlannerDefaultSettingsResolver


class FixedAudioSettingsDefaultValueSourceProvider:
    def __init__(self, *, sample_rate_hz: int, channels: int = 1) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.channels = channels

    async def resolve_source(self, supplied_source, *, declaration):
        resolved = AudioSettings({
            "sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
        })
        if supplied_source is not None:
            supplied_value = await supplied_source.get_value(declaration.codec)
            resolved.update(dict(supplied_value))
        return InMemorySource(resolved)


class PlannerRefactorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp.name)
        self.audio_path = self.work_dir / "audio.bin"
        self.audio_path.write_bytes(bytes(range(64)))

        self.cache_root = self.work_dir / "cache"
        self.pipeline = (
            ChunkyPipeline.builder()
            .install(FileCacheFeature(self.cache_root))
            .install(SummaryExampleFeature(api_key="test-api-key"))
            .build()
        )
        self.cache = self.pipeline.runtime_services.artifact_cache

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    def _request(self, *, include_base_audio_settings: bool = False) -> RunRequest:
        inputs = {
            "source": {"audio": FileSource(self.audio_path)},
            "speech_processing": {
                "audio_preprocessing": {
                    "audio_settings": InMemorySource(
                        AudioSettings({"sample_rate_hz": 16000})
                    ),
                },
            },
            "music_processing": {
                "audio_preprocessing": {
                    "audio_settings": InMemorySource(
                        AudioSettings({"sample_rate_hz": 48000, "channels": 2})
                    ),
                },
            },
            "settings": {
                "asr": InMemorySource({"model": "placeholder-asr"}),
                "language": InMemorySource({"language": "en"}),
                "diarization": InMemorySource({"model": "placeholder-diarizer"}),
                "alignment": InMemorySource({"mode": "word"}),
                "vtt": InMemorySource({"include_speakers": True}),
                "render": InMemorySource({"style": "caption-rich"}),
            },
        }
        if include_base_audio_settings:
            inputs["audio_preprocessing"] = {
                "audio_settings": InMemorySource(
                    AudioSettings({"sample_rate_hz": 22050, "channels": 1})
                )
            }

        return RunRequest(
            inputs=inputs,
            outputs={str(WEBVTT_TRANSCRIPT.artifact_id): object()},
        )

    async def _plan(self, request: RunRequest):
        await self.pipeline._ensure_runtime_services_ready()
        provider = self.pipeline._build_artifact_bindings_provider(self.cache)
        planner = self.pipeline._build_planner(provider)
        input_sources = request.normalize(self.pipeline.stage_manifest).input_sources
        supplied = {}
        try:
            supplied, user_settings = await self.pipeline._bind_supplied_inputs(
                provider,
                input_sources,
            )
            return await planner.plan(
                supplied_instances=supplied,
                requested_outputs={WEBVTT_TRANSCRIPT.artifact_id},
                user_supplied_settings=frozenset(user_settings),
                default_settings_resolver=PlannerDefaultSettingsResolver(
                    self.pipeline.stage_manifest,
                    provider,
                ),
            )
        finally:
            await close_instances(tuple(supplied.values()))


    async def test_override_can_replace_default_value_source_provider(self) -> None:
        inherited = AUDIO_SETTINGS.override(override_domain="inherited")
        self.assertIs(
            inherited.default_value_source_provider,
            AUDIO_SETTINGS.default_value_source_provider,
        )

        provider = FixedAudioSettingsDefaultValueSourceProvider(sample_rate_hz=8000)
        overridden = AUDIO_SETTINGS.override(
            override_domain="speech_processing",
            default_value_source_provider=provider,
        )
        self.assertIs(overridden.default_value_source_provider, provider)

        assert overridden.default_value_source_provider is not None
        resolved_source = await overridden.default_value_source_provider.resolve_source(
            None,
            declaration=overridden,
        )
        self.assertEqual(
            {"sample_rate_hz": 8000, "channels": 1},
            dict(await resolved_source.get_value(overridden.codec)),
        )

        supplied_override = InMemorySource(AudioSettings({"channels": 2}))
        resolved_source = await overridden.default_value_source_provider.resolve_source(
            supplied_override,
            declaration=overridden,
        )
        self.assertEqual(
            {"sample_rate_hz": 8000, "channels": 2},
            dict(await resolved_source.get_value(overridden.codec)),
        )

    async def test_distinct_pushed_audio_settings_create_distinct_prepare_invocations(self) -> None:
        plan = await self._plan(self._request())
        prepares = [
            invocation
            for invocation in plan.invocations
            if invocation.registration.stage_id == "audio.prepare"
        ]

        self.assertEqual(2, len(prepares))
        resolved_audio_settings = [
            dict(invocation.resolved_settings.require(AUDIO_SETTINGS.artifact_id))
            for invocation in prepares
        ]
        self.assertCountEqual(
            [16000, 48000],
            [settings["sample_rate_hz"] for settings in resolved_audio_settings],
        )
        self.assertIn(
            {"sample_rate_hz": 48000, "channels": 2},
            resolved_audio_settings,
        )

    async def test_inherited_edge_settings_override_user_supplied_base_setting(self) -> None:
        plan = await self._plan(self._request(include_base_audio_settings=True))
        prepares = [
            invocation
            for invocation in plan.invocations
            if invocation.registration.stage_id == "audio.prepare"
        ]

        self.assertEqual(2, len(prepares))
        resolved_audio_settings = [
            dict(invocation.resolved_settings.require(AUDIO_SETTINGS.artifact_id))
            for invocation in prepares
        ]
        self.assertCountEqual(
            [16000, 48000],
            [settings["sample_rate_hz"] for settings in resolved_audio_settings],
        )
        self.assertIn(
            {"sample_rate_hz": 48000, "channels": 2},
            resolved_audio_settings,
        )


if __name__ == "__main__":
    unittest.main()
