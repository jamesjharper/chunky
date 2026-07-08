from __future__ import annotations

import json
import unittest
from collections.abc import Iterable
from typing import Any

from src.python.chunky.Artifacts.artifact_models import ArtifactDeclaration, ArtifactKey, ArtifactRole
from python.chunky.Bindings.bindings import ArtifactBindingsProvider
from src.python.chunky.Bindings.cache_models import (
    CacheHit,
    CacheWait,
    CacheWriteReservation,
)


class JsonishCodec:
    codec_id = "test-jsonish"
    codec_version = 1
    mime_type = "application/json"
    extension = ".json"

    async def serialize(self, value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    async def deserialize(self, raw: bytes) -> object:
        return json.loads(raw.decode("utf-8"))


class RecordingSource:
    kind = "recording-source"

    def __init__(self, value: object) -> None:
        self.value = value
        self.closed = False
        self.read_count = 0

    async def get_value(self, _codec: JsonishCodec) -> object:
        if self.closed:
            raise RuntimeError("source is closed")
        self.read_count += 1
        return self.value

    async def get_content_fingerprint(self, codec: JsonishCodec) -> str:
        return (await codec.serialize(self.value)).hex()

    async def close(self) -> None:
        self.closed = True


class RecordingTarget:
    kind = "recording-target"

    def __init__(self, name: str) -> None:
        self.name = name
        self.prepared: list[tuple[object, str]] = []
        self.committed = False
        self.aborted = False

    async def prepare(self, value: object, _codec: JsonishCodec, fingerprint: str) -> None:
        if self.committed or self.aborted:
            raise RuntimeError(f"target {self.name} is terminal")
        self.prepared.append((value, fingerprint))

    async def commit(self) -> None:
        if self.aborted:
            return
        if not self.prepared:
            raise RuntimeError(f"target {self.name} was not prepared")
        self.committed = True

    async def abort(self) -> None:
        if self.committed:
            return
        self.aborted = True


class RecordingWaitHandle:
    def __init__(self) -> None:
        self.waited = False

    async def wait(self) -> None:
        self.waited = True


class ScriptedCache:
    def __init__(self, outcomes: Iterable[object]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[ArtifactKey, str, bool]] = []

    async def resolve(
        self,
        declaration: ArtifactDeclaration[Any],
        fingerprint: str,
        *,
        force_write: bool = False,
    ) -> object:
        self.calls.append((declaration.artifact_id, fingerprint, force_write))
        if not self._outcomes:
            raise AssertionError("cache script exhausted")
        return self._outcomes.pop(0)


def _decl(
    artifact_id: str,
    *,
    role: ArtifactRole = ArtifactRole.INTERMEDIATE,
    cacheable: bool = True,
) -> ArtifactDeclaration[Any]:
    return ArtifactDeclaration(
        artifact_id=ArtifactKey.parse(artifact_id),
        role=role,
        version=1,
        codec=JsonishCodec(),
        cacheable=cacheable,
    )


class ExecutionCacheBindingContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_requested_cacheable_outputs_hit_returns_available_instances_and_no_writers(self) -> None:
        out_a = _decl("graph//out_a")
        out_b = _decl("graph//out_b")
        source_a = RecordingSource({"out": "a"})
        source_b = RecordingSource({"out": "b"})
        cache = ScriptedCache([CacheHit(source_a), CacheHit(source_b)])
        provider = ArtifactBindingsProvider(cache)

        resolved = await provider.bind_stage_outputs([out_a, out_b], "stage-pedigree")

        self.assertEqual(2, len(resolved.available))
        self.assertEqual(0, len(resolved.writers))
        self.assertEqual([False, False], [call[2] for call in cache.calls])

    async def test_partial_cache_hit_for_multi_output_stage_retries_with_force_write_for_complete_set(self) -> None:
        out_a = _decl("graph//out_a")
        out_b = _decl("graph//out_b")
        provisional_hit = RecordingSource({"out": "a-old"})
        provisional_writer = RecordingTarget("out-b-provisional")
        force_writer_a = RecordingTarget("out-a-force")
        force_writer_b = RecordingTarget("out-b-force")
        cache = ScriptedCache(
            [
                CacheHit(provisional_hit),
                CacheWriteReservation(provisional_writer),
                CacheWriteReservation(force_writer_a),
                CacheWriteReservation(force_writer_b),
            ]
        )
        provider = ArtifactBindingsProvider(cache)

        resolved = await provider.bind_stage_outputs([out_a, out_b], "stage-pedigree")

        self.assertTrue(provisional_hit.closed)
        self.assertTrue(provisional_writer.aborted)
        self.assertEqual(0, len(resolved.available))
        self.assertEqual(2, len(resolved.writers))
        self.assertEqual([False, False, True, True], [call[2] for call in cache.calls])

    async def test_cache_wait_aborts_provisional_writers_and_returns_wait_without_starting_duplicate_writer(self) -> None:
        out_a = _decl("graph//out_a")
        out_b = _decl("graph//out_b")
        provisional_writer = RecordingTarget("out-a-provisional")
        wait_handle = RecordingWaitHandle()
        cache_wait = CacheWait(
            fingerprint="blocked-fingerprint",
            owner_task_id="other-task",
            wait_handle=wait_handle,
        )
        cache = ScriptedCache(
            [
                CacheWriteReservation(provisional_writer),
                cache_wait,
            ]
        )
        provider = ArtifactBindingsProvider(cache)

        result = await provider.bind_stage_outputs([out_a, out_b], "stage-pedigree")

        self.assertEqual(out_b.artifact_id, result.artifact_id)
        self.assertIs(cache_wait, result.cache_wait)
        self.assertTrue(provisional_writer.aborted)
        self.assertFalse(wait_handle.waited, "the scheduler, not binding, owns cache waiting")

    async def test_cache_metadata_mismatch_is_observed_as_miss_and_bound_as_writer(self) -> None:
        out_a = _decl("graph//out_a")
        replacement_writer = RecordingTarget("replacement-after-metadata-mismatch")
        # The cache implementation owns metadata validation.  This scripted cache
        # represents a mismatched entry by returning a write reservation instead
        # of a hit for the requested fingerprint.
        cache = ScriptedCache([CacheWriteReservation(replacement_writer)])
        provider = ArtifactBindingsProvider(cache)

        resolved = await provider.bind_stage_outputs([out_a], "stage-pedigree")

        self.assertEqual(0, len(resolved.available))
        self.assertEqual(1, len(resolved.writers))
        self.assertEqual([(out_a.artifact_id, False)], [(call[0], call[2]) for call in cache.calls])

    async def test_mixed_cacheable_hit_and_non_cacheable_output_runs_stage_for_non_cacheable_writer_only(self) -> None:
        cacheable = _decl("graph//cacheable_out", cacheable=True)
        non_cacheable = _decl("graph//diagnostic_out", role=ArtifactRole.DIAGNOSTIC, cacheable=False)
        source = RecordingSource({"cached": True})
        cache = ScriptedCache([CacheHit(source)])
        provider = ArtifactBindingsProvider(cache)

        resolved = await provider.bind_stage_outputs([cacheable, non_cacheable], "stage-pedigree")

        self.assertEqual(1, len(resolved.available))
        self.assertEqual(1, len(resolved.writers))
        self.assertEqual(non_cacheable.artifact_id, resolved.writers[0].handle.declaration.artifact_id)

    async def test_same_semantic_target_can_be_materialized_to_multiple_sinks_without_changing_fingerprint(self) -> None:
        out_a = _decl("graph//out_a", role=ArtifactRole.TARGET)
        source = RecordingSource({"semantic": "value"})
        cache = ScriptedCache([CacheHit(source)])
        provider = ArtifactBindingsProvider(cache)
        resolved = await provider.bind_stage_outputs([out_a], "stage-pedigree")
        [instance] = resolved.available
        first_sink = RecordingTarget("first-caller-path")
        second_sink = RecordingTarget("second-caller-path")

        await provider.materialize(instance, first_sink)
        await provider.materialize(instance, second_sink)

        self.assertEqual(1, len(cache.calls), "caller materialization must not re-resolve semantic cache identity")
        self.assertEqual(instance.fingerprint, first_sink.prepared[0][1])
        self.assertEqual(instance.fingerprint, second_sink.prepared[0][1])
        self.assertNotEqual(first_sink.name, second_sink.name)


if __name__ == "__main__":
    unittest.main()
