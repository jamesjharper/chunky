from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.python.chunky.Artifacts.artifact_models import ArtifactInstanceLocator
from src.python.chunky.Bindings.file_cache_provider.provider import FileCacheProvider
from src.python.chunky.ExampleFeatures.artifact_handles_examples import FileSource
from src.python.chunky.ExampleFeatures.stage_examples import (
    PREPARED_AUDIO,
    WEBVTT_TRANSCRIPT,
    SummaryExampleFeature,
)
from src.python.chunky.pipeline import (
    ArtifactCachePolicy,
    ChunkyPipeline,
    RequestValidationError,
    RunRequest,
)
from src.python.chunky.Scheduling.task_control import RunExecutionError
from src.python.chunky.Scheduling.task_runner import OutputStatus, TargetMaterializationStatus


class FailingTarget:
    kind = "failing-target"

    async def prepare(self, value: object, codec: Any, fingerprint: str) -> None:
        raise RuntimeError("target write failed")

    async def commit(self) -> None:
        return None

    async def abort(self) -> None:
        return None


class TargetlessOutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp.name)
        self.audio_path = self.work_dir / "audio.bin"
        self.audio_path.write_bytes(bytes(range(32)))
        self.cache = FileCacheProvider(self.work_dir / "cache", owner_task_id="test")
        await self.cache.recover()
        self.pipeline = ChunkyPipeline.builder(artifact_cache=self.cache).install(
            SummaryExampleFeature()
        ).build()

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    def _inputs(self):
        return {PREPARED_AUDIO.artifact_id: []}

    def _source_inputs(self):
        return {"source": {"audio": FileSource(self.audio_path)}}

    async def test_targetless_cacheable_output_returns_instance_locator(self) -> None:
        result = await self.pipeline.run(
            RunRequest(
                inputs=self._source_inputs(),
                outputs={PREPARED_AUDIO.artifact_id: []},
            )
        )

        output = result.outputs[PREPARED_AUDIO.artifact_id]
        self.assertEqual(OutputStatus.SUCCEEDED, output.status)
        self.assertEqual(result.output_fingerprints[PREPARED_AUDIO.artifact_id], output.fingerprint)
        self.assertIsInstance(output.instance_locator, ArtifactInstanceLocator)
        self.assertEqual(PREPARED_AUDIO.artifact_id, output.instance_locator.artifact_id)
        self.assertEqual(output.fingerprint, output.instance_locator.fingerprint)
        self.assertEqual((), output.targets)

    async def test_targetless_output_rejects_non_cacheable_declaration(self) -> None:
        with self.assertRaises(RequestValidationError) as captured:
            await self.pipeline.run(
                RunRequest(
                    inputs=self._source_inputs(),
                    outputs={WEBVTT_TRANSCRIPT.artifact_id: []},
                )
            )
        self.assertIn("must be cacheable", str(captured.exception))

    async def test_targetless_output_rejects_do_not_cache_policy(self) -> None:
        with self.assertRaises(RequestValidationError) as captured:
            await self.pipeline.run(
                RunRequest(
                    inputs=self._source_inputs(),
                    outputs={PREPARED_AUDIO.artifact_id: []},
                    cache_policy={PREPARED_AUDIO.artifact_id: ArtifactCachePolicy(do_not_cache=True)},
                )
            )
        self.assertIn("conflicts with cache_policy.do_not_cache", str(captured.exception))

    async def test_materialization_failure_preserves_committed_reference(self) -> None:
        with self.assertRaises(RunExecutionError) as captured:
            await self.pipeline.run(
                RunRequest(
                    inputs=self._source_inputs(),
                    outputs={PREPARED_AUDIO.artifact_id: [FailingTarget()]},
                )
            )

        partial = captured.exception.result.output
        assert partial is not None
        output = partial.outputs[PREPARED_AUDIO.artifact_id]
        self.assertEqual(OutputStatus.FAILED, output.status)
        self.assertIsNotNone(output.instance_locator)
        self.assertEqual(output.fingerprint, output.instance_locator.fingerprint)
        self.assertEqual(TargetMaterializationStatus.FAILED, output.targets[0].status)

    async def test_progress_snapshot_includes_available_output_reference(self) -> None:
        handle = self.pipeline.run_away(
            RunRequest(
                inputs=self._source_inputs(),
                outputs={PREPARED_AUDIO.artifact_id: []},
            )
        )
        result = await handle.await_to_completion()
        self.assertEqual("completed", result.status.value)

        snapshot = handle.get_progress()
        self.assertEqual(1, len(snapshot.available_outputs))
        available = snapshot.available_outputs[0]
        self.assertEqual(PREPARED_AUDIO.artifact_id, available.artifact_id)
        self.assertEqual(
            result.output.outputs[PREPARED_AUDIO.artifact_id].instance_locator,
            available.instance_locator,
        )

    async def test_direct_cache_publish_rejects_do_not_cache_reference(self) -> None:
        with self.assertRaises(ValueError):
            await self.pipeline.cache_io.publish(
                PREPARED_AUDIO.artifact_id,
                FileSource(self.audio_path),
                cache_policy=ArtifactCachePolicy(do_not_cache=True),
            )


if __name__ == "__main__":
    unittest.main()
