from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.python.chunky.Bindings.bindings import ArtifactBindingsProvider, close_instances
from src.python.chunky.ExampleFeatures.stage_examples import (
    PREPARED_AUDIO,
    SummaryExampleFeature,
    TOKEN_TEXT,
)
from src.python.chunky.ExampleFeatures.artifact_handles_examples import AtomicFileTargetHandle
from src.python.chunky_bench.benchmark_support import (
    BenchmarkMaterializationFeature,
    TOKEN_TEXT_TARGET,
)
from src.python.chunky.Pipeline.pipeline import (
    ChunkyPipeline,
    EnvironmentVariableRuntimeSettingSourceFeature,
    ExplicitRuntimeSettingsFeature,
    FileCacheFeature,
    RunRequest,
)
from src.python.chunky.Pipeline.runtime import (
    EnvironmentVariableRuntimeSettingSource,
    PipelineRuntimeSettingsBuilder,
    RuntimeSettingSpec,
)
from src.python.chunky.Planning.execution_planner import TaskRunPlanner
from src.python.chunky.Scheduling.task_control import RunExecutionError
from src.python.chunky_demo.demo_support import build_inputs


class PlannerFactoryFeature:
    feature_id = "test.planner-factory"
    configuration_fingerprint = "planner-factory-v1"

    def install(self, manifest_builder, service_locator, env_settings) -> None:
        del manifest_builder, env_settings
        service_locator.set_planner_factory(self._planner_factory)

    @staticmethod
    def _planner_factory(manifest, bindings_provider: ArtifactBindingsProvider) -> TaskRunPlanner:
        return TaskRunPlanner(manifest, bindings_provider.fingerprint_provider)


class ConflictingPlannerFactoryFeature:
    feature_id = "test.planner-factory-conflict"
    configuration_fingerprint = "planner-factory-conflict-v1"

    def install(self, manifest_builder, service_locator, env_settings) -> None:
        del manifest_builder, env_settings
        service_locator.set_planner_factory(self._planner_factory)

    @staticmethod
    def _planner_factory(manifest, bindings_provider: ArtifactBindingsProvider) -> TaskRunPlanner:
        return TaskRunPlanner(manifest, bindings_provider.fingerprint_provider)


class PipelineBuilderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp.name)
        self.audio_path = self.work_dir / "audio.bin"
        self.audio_path.write_bytes(bytes(range(64)))
        self.cache_root = self.work_dir / "cache"

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    def test_runtime_service_slot_conflict_fails_install(self) -> None:
        with self.assertRaises(RuntimeError) as captured:
            (
                ChunkyPipeline.builder()
                .install(FileCacheFeature(self.cache_root))
                .install(PlannerFactoryFeature())
                .install(ConflictingPlannerFactoryFeature())
            )
        self.assertIn("planner_factory", str(captured.exception))

    def test_builder_rejects_mutation_after_build(self) -> None:
        builder = (
            ChunkyPipeline.builder()
            .install(FileCacheFeature(self.cache_root))
            .install(SummaryExampleFeature(api_key="test-api-key"))
        )
        builder.build()
        with self.assertRaises(RuntimeError):
            builder.install(SummaryExampleFeature(api_key="another-test-key"))

    def test_runtime_settings_builder_rejects_mutation_after_freeze(self) -> None:
        builder = PipelineRuntimeSettingsBuilder()
        builder.set("example.value", "configured")
        builder.freeze()
        with self.assertRaises(RuntimeError):
            builder.set("example.other", "later")

    def test_frozen_runtime_settings_can_resolve_multiple_stage_scopes(self) -> None:
        builder = PipelineRuntimeSettingsBuilder()
        builder.install_source(
            EnvironmentVariableRuntimeSettingSource(
                environ={"EXAMPLE_REQUIRED": "resolved-from-env"}
            )
        )
        builder.require(
            RuntimeSettingSpec.string(
                "example.required",
                suggested_env_var="EXAMPLE_REQUIRED",
            ),
            stage_ids=("stage.a",),
        )
        builder.declare(
            RuntimeSettingSpec.string(
                "example.optional",
                default="fallback",
            ),
            stage_ids=("stage.b",),
        )

        frozen = builder.freeze()
        for_stage_a = frozen.resolve_for_stages(("stage.a",))
        for_stage_b = frozen.resolve_for_stages(("stage.b",))

        self.assertEqual("resolved-from-env", for_stage_a.get_string("example.required"))
        self.assertFalse(for_stage_b.has("example.required"))
        self.assertEqual("fallback", for_stage_b.get_string("example.optional"))

    def test_feature_missing_env_setting_does_not_fail_at_build(self) -> None:
        pipeline = (
            ChunkyPipeline.builder()
            .install(FileCacheFeature(self.cache_root))
            .install(SummaryExampleFeature(api_key=None))
            .build()
        )
        self.assertIsNone(pipeline.runtime_settings.get("example.asr.api_key"))

    def test_feature_can_read_required_env_setting_after_build(self) -> None:
        pipeline = (
            ChunkyPipeline.builder()
            .install(
                ExplicitRuntimeSettingsFeature(
                    {"example.asr.api_key": "env-test-key"}
                )
            )
            .install(FileCacheFeature(self.cache_root))
            .install(SummaryExampleFeature(api_key=None))
            .build()
        )
        self.assertEqual("env-test-key", pipeline.runtime_settings.get("example.asr.api_key"))

    def test_feature_rejects_duplicate_explicit_runtime_setting(self) -> None:
        with self.assertRaises(RuntimeError) as captured:
            (
                ChunkyPipeline.builder()
                .install(
                    ExplicitRuntimeSettingsFeature(
                        {"example.asr.api_key": "env-test-key"}
                    )
                )
                .install(FileCacheFeature(self.cache_root))
                .install(SummaryExampleFeature(api_key="feature-test-key"))
            )
        self.assertIn("duplicate runtime setting value", str(captured.exception))

    async def test_feature_missing_env_setting_fails_before_execution_when_stage_is_planned(self) -> None:
        pipeline = (
            ChunkyPipeline.builder()
            .install(FileCacheFeature(self.cache_root))
            .install(SummaryExampleFeature(api_key=None))
            .build()
        )
        with self.assertRaises(RunExecutionError) as captured:
            await pipeline.run(
                RunRequest(
                    inputs=build_inputs(self.audio_path),
                    outputs={TOKEN_TEXT.artifact_id: []},
                )
            )
        self.assertIn("example.asr.api_key", str(captured.exception))
        self.assertIn("EXAMPLE_ASR_API_KEY", str(captured.exception))

    async def test_feature_missing_env_setting_is_ignored_when_stage_is_not_planned(self) -> None:
        pipeline = (
            ChunkyPipeline.builder()
            .install(FileCacheFeature(self.cache_root))
            .install(SummaryExampleFeature(api_key=None))
            .build()
        )
        result = await pipeline.run(
            RunRequest(
                inputs=build_inputs(self.audio_path),
                outputs={PREPARED_AUDIO.artifact_id: []},
            )
        )
        self.assertIn(PREPARED_AUDIO.artifact_id, result.outputs)

    async def test_feature_can_load_required_env_setting_from_environment_source(self) -> None:
        with patch.dict("os.environ", {"EXAMPLE_ASR_API_KEY": "env-source-key"}, clear=False):
            pipeline = (
                ChunkyPipeline.builder()
                .install(EnvironmentVariableRuntimeSettingSourceFeature())
                .install(FileCacheFeature(self.cache_root))
                .install(SummaryExampleFeature(api_key=None))
                .build()
            )
            result = await pipeline.run(
                RunRequest(
                    inputs=build_inputs(self.audio_path),
                    outputs={TOKEN_TEXT.artifact_id: []},
                )
            )
        locator = result.outputs[TOKEN_TEXT.artifact_id].instance_locator
        assert locator is not None
        instance = await pipeline.cache_io.open(locator)
        assert instance.handle.source is not None
        try:
            payload = await instance.handle.source.get_value(TOKEN_TEXT.codec)
        finally:
            await close_instances((instance,))

        self.assertTrue(payload["api_key_configured"])

    async def test_example_feature_uses_explicit_provider_configuration(self) -> None:
        pipeline = (
            ChunkyPipeline.builder()
            .install(FileCacheFeature(self.cache_root))
            .install(
                SummaryExampleFeature(
                    api_key="test-api-key",
                    provider_name="test-asr",
                    concurrency_limit=12,
                )
            )
            .build()
        )
        result = await pipeline.run(
            RunRequest(
                inputs=build_inputs(self.audio_path),
                outputs={TOKEN_TEXT.artifact_id: []},
            )
        )
        locator = result.outputs[TOKEN_TEXT.artifact_id].instance_locator
        assert locator is not None
        instance = await pipeline.cache_io.open(locator)
        assert instance.handle.source is not None
        try:
            payload = await instance.handle.source.get_value(TOKEN_TEXT.codec)
        finally:
            await close_instances((instance,))

        self.assertEqual("test-asr", payload["provider_name"])
        self.assertEqual(12, payload["concurrency_limit"])
        self.assertTrue(payload["api_key_configured"])

    async def test_benchmark_materialization_feature_uses_runtime_settings_transcribe_path(self) -> None:
        pipeline = (
            ChunkyPipeline.builder()
            .install(FileCacheFeature(self.cache_root))
            .install(BenchmarkMaterializationFeature())
            .build()
        )
        output_path = self.work_dir / "benchmark-token-text.json"
        result = await pipeline.run(
            RunRequest(
                inputs=build_inputs(self.audio_path),
                outputs={
                    TOKEN_TEXT_TARGET.artifact_id.domain: {
                        "token_text_snapshot": AtomicFileTargetHandle(output_path),
                    }
                },
            )
        )
        self.assertIn(TOKEN_TEXT_TARGET.artifact_id, result.outputs)
        payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual("benchmark-asr", payload["provider_name"])
        self.assertEqual(16, payload["concurrency_limit"])
        self.assertTrue(payload["api_key_configured"])
