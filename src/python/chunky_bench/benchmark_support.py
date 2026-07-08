from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..chunky.Artifacts.artifact_models import (
    ArtifactCapability,
    ArtifactDeclaration,
    ArtifactKey,
    ArtifactRequirement,
    ArtifactRole,
)
from ..chunky.ExampleFeatures.artifact_codex_example import JsonCodec
from ..chunky.ExampleFeatures.artifact_handles_examples import AtomicFileTargetHandle
from ..chunky.ExampleFeatures.common import (
    MUSIC_SPANS,
    PREPARED_AUDIO,
    SPEAKER_SEGMENTS,
    TIMED_WORDS,
    TOKEN_TEXT,
    WEBVTT_TRANSCRIPT,
    fixed_outputs,
    unbound_resources,
)
from ..chunky.ExampleFeatures.stage_examples import SummaryExampleFeature
from ..chunky.Stages.stage_manifest import StageManifestBuilder
from ..chunky.Stages.stage_models import (
    ResolvedStageRequirements,
    StageExecutionContext,
    StagePlanningContext,
    StageRegistration,
)
from ..chunky_demo.demo_support import DemoFixture, build_inputs
from ..chunky.pipeline import ChunkyPipeline, RunRequest
from ..chunky.Bindings.file_cache_provider import FileCacheProvider

JsonObject = dict[str, Any]

PREPARED_AUDIO_TARGET = ArtifactDeclaration[JsonObject](
    artifact_id=ArtifactKey.parse("benchmark//prepared_audio_snapshot"),
    role=ArtifactRole.TARGET,
    version=1,
    codec=JsonCodec[JsonObject](),
    cacheable=False,
)
TOKEN_TEXT_TARGET = ArtifactDeclaration[JsonObject](
    artifact_id=ArtifactKey.parse("benchmark//token_text_snapshot"),
    role=ArtifactRole.TARGET,
    version=1,
    codec=JsonCodec[JsonObject](),
    cacheable=False,
)
SPEAKER_SEGMENTS_TARGET = ArtifactDeclaration[JsonObject](
    artifact_id=ArtifactKey.parse("benchmark//speaker_segments_snapshot"),
    role=ArtifactRole.TARGET,
    version=1,
    codec=JsonCodec[JsonObject](),
    cacheable=False,
)
MUSIC_SPANS_TARGET = ArtifactDeclaration[JsonObject](
    artifact_id=ArtifactKey.parse("benchmark//music_spans_snapshot"),
    role=ArtifactRole.TARGET,
    version=1,
    codec=JsonCodec[JsonObject](),
    cacheable=False,
)
TIMED_WORDS_TARGET = ArtifactDeclaration[JsonObject](
    artifact_id=ArtifactKey.parse("benchmark//timed_words_snapshot"),
    role=ArtifactRole.TARGET,
    version=1,
    codec=JsonCodec[JsonObject](),
    cacheable=False,
)


@dataclass(frozen=True)
class BenchmarkScenarioDefinition:
    name: str
    output_name: str
    declaration: ArtifactDeclaration[Any]


BENCHMARK_SCENARIOS: dict[str, BenchmarkScenarioDefinition] = {
    "full_pipeline": BenchmarkScenarioDefinition(
        name="full_pipeline",
        output_name="webvtt_transcript",
        declaration=WEBVTT_TRANSCRIPT,
    ),
    "prepare_audio": BenchmarkScenarioDefinition(
        name="prepare_audio",
        output_name="prepared_audio_snapshot",
        declaration=PREPARED_AUDIO_TARGET,
    ),
    "transcribe": BenchmarkScenarioDefinition(
        name="transcribe",
        output_name="token_text_snapshot",
        declaration=TOKEN_TEXT_TARGET,
    ),
    "diarize": BenchmarkScenarioDefinition(
        name="diarize",
        output_name="speaker_segments_snapshot",
        declaration=SPEAKER_SEGMENTS_TARGET,
    ),
    "detect_music": BenchmarkScenarioDefinition(
        name="detect_music",
        output_name="music_spans_snapshot",
        declaration=MUSIC_SPANS_TARGET,
    ),
    "align": BenchmarkScenarioDefinition(
        name="align",
        output_name="timed_words_snapshot",
        declaration=TIMED_WORDS_TARGET,
    ),
}


class ArtifactSnapshotExecutor:
    def __init__(
        self,
        source_declaration: ArtifactDeclaration[JsonObject],
        target_declaration: ArtifactDeclaration[JsonObject],
    ) -> None:
        self._source_declaration = source_declaration
        self._target_declaration = target_declaration

    async def execute(
        self,
        inputs,
        outputs,
        context: StageExecutionContext,
    ) -> None:
        value = await inputs.get_artifact(self._source_declaration).read()
        context.cancellation.raise_if_cancelled()
        await outputs.get_artifact(self._target_declaration).write(value)


def snapshot_stage(
    *,
    stage_id: str,
    display_name: str,
    source_declaration: ArtifactDeclaration[JsonObject],
    target_declaration: ArtifactDeclaration[JsonObject],
) -> StageRegistration:
    def resolve_requirements(
        _context: StagePlanningContext,
    ) -> ResolvedStageRequirements:
        return ResolvedStageRequirements(
            inputs=(ArtifactRequirement(source_declaration.artifact_id),),
        )

    def executor_factory() -> ArtifactSnapshotExecutor:
        return ArtifactSnapshotExecutor(source_declaration, target_declaration)

    capability = ArtifactCapability(target_declaration.artifact_id)
    return StageRegistration(
        stage_id=stage_id,
        display_name=display_name,
        registration_version="1",
        behavior_version="1",
        executor_factory=executor_factory,
        owned_artifacts=(target_declaration,),
        input_capabilities=(ArtifactCapability(source_declaration.artifact_id),),
        output_capabilities=(capability,),
        resolve_requirements=resolve_requirements,
        resolve_output_capabilities=fixed_outputs(capability),
        resolve_resource_policy=unbound_resources,
    )


PREPARED_AUDIO_SNAPSHOT_STAGE = snapshot_stage(
    stage_id="benchmark.snapshot.prepare_audio",
    display_name="Snapshot prepared audio",
    source_declaration=PREPARED_AUDIO,
    target_declaration=PREPARED_AUDIO_TARGET,
)
TOKEN_TEXT_SNAPSHOT_STAGE = snapshot_stage(
    stage_id="benchmark.snapshot.transcribe",
    display_name="Snapshot token text",
    source_declaration=TOKEN_TEXT,
    target_declaration=TOKEN_TEXT_TARGET,
)
SPEAKER_SEGMENTS_SNAPSHOT_STAGE = snapshot_stage(
    stage_id="benchmark.snapshot.diarize",
    display_name="Snapshot speaker segments",
    source_declaration=SPEAKER_SEGMENTS,
    target_declaration=SPEAKER_SEGMENTS_TARGET,
)
MUSIC_SPANS_SNAPSHOT_STAGE = snapshot_stage(
    stage_id="benchmark.snapshot.detect_music",
    display_name="Snapshot music spans",
    source_declaration=MUSIC_SPANS,
    target_declaration=MUSIC_SPANS_TARGET,
)
TIMED_WORDS_SNAPSHOT_STAGE = snapshot_stage(
    stage_id="benchmark.snapshot.align",
    display_name="Snapshot timed words",
    source_declaration=TIMED_WORDS,
    target_declaration=TIMED_WORDS_TARGET,
)


class BenchmarkMaterializationFeature:
    feature_id = "example.webvtt-benchmark-materialization"
    configuration_fingerprint = "webvtt-benchmark-materialization-v1"

    def install(self, builder: StageManifestBuilder) -> None:
        SummaryExampleFeature().install(builder)
        builder.add_artifact(PREPARED_AUDIO_TARGET)
        builder.add_artifact(TOKEN_TEXT_TARGET)
        builder.add_artifact(SPEAKER_SEGMENTS_TARGET)
        builder.add_artifact(MUSIC_SPANS_TARGET)
        builder.add_artifact(TIMED_WORDS_TARGET)
        builder.add_stage(PREPARED_AUDIO_SNAPSHOT_STAGE)
        builder.add_stage(TOKEN_TEXT_SNAPSHOT_STAGE)
        builder.add_stage(SPEAKER_SEGMENTS_SNAPSHOT_STAGE)
        builder.add_stage(MUSIC_SPANS_SNAPSHOT_STAGE)
        builder.add_stage(TIMED_WORDS_SNAPSHOT_STAGE)


def scenario_names() -> tuple[str, ...]:
    return tuple(BENCHMARK_SCENARIOS)


def build_benchmark_request(
    audio_path: Path,
    output_path: Path,
    scenario: str,
) -> RunRequest:
    definition = BENCHMARK_SCENARIOS[scenario]
    return RunRequest(
        inputs=build_inputs(audio_path),
        outputs={
            definition.declaration.artifact_id.domain: {
                definition.output_name: AtomicFileTargetHandle(output_path),
            },
        },
    )


async def create_benchmark_fixture(
    work_dir: Path,
    *,
    owner_task_id: str,
    scenario: str,
    clean: bool = True,
) -> DemoFixture:
    from ..chunky_demo.demo_support import DEFAULT_WORK_DIR
    import shutil

    if clean and work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    extension = ".vtt" if scenario == "full_pipeline" else ".json"
    audio_path = work_dir / "input-audio.bin"
    output_path = work_dir / f"output{extension}"
    manifest_path = work_dir / "stage-manifest.json"
    audio_path.write_bytes(bytes(range(256)) * 16)

    cache = FileCacheProvider(work_dir / "cache", owner_task_id=owner_task_id)
    await cache.recover()

    builder = ChunkyPipeline.builder(artifact_cache=cache)
    if scenario == "full_pipeline":
        builder.install(SummaryExampleFeature())
    else:
        builder.install(BenchmarkMaterializationFeature())
    pipeline = builder.build()
    manifest_path.write_text(
        pipeline.stage_manifest.describe_json() + "\n",
        encoding="utf-8",
    )
    return DemoFixture(
        work_dir=work_dir,
        audio_path=audio_path,
        output_path=output_path,
        manifest_path=manifest_path,
        cache=cache,
        pipeline=pipeline,
    )
