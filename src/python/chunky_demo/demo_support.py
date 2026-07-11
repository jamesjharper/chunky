from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from ..chunky.ExampleFeatures.artifact_handles_examples import (
    AtomicFileTargetHandle,
    FileSource,
    InMemorySource,
)
from ..chunky.ExampleFeatures.stage_examples import (
    AudioSettings,
    SummaryExampleFeature,
)
from ..chunky.Bindings.file_cache_provider import FileCacheProvider
from ..chunky.Pipeline.pipeline import ChunkyPipeline, FileCacheFeature, RunRequest


DEFAULT_WORK_DIR = Path("/tmp/chunky-complete-example-python")


@dataclass(frozen=True)
class DemoFixture:
    work_dir: Path
    audio_path: Path
    output_path: Path
    manifest_path: Path
    cache: FileCacheProvider
    pipeline: ChunkyPipeline


def build_inputs(audio_path: Path) -> dict[str, object]:
    return {
        "source": {
            "audio": FileSource(audio_path),
        },
        "audio_preprocessing": {
            "audio_settings": InMemorySource(
                AudioSettings({"sample_rate_hz": 44100, "channels": 1})
            ),
        },
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
                    AudioSettings(
                        {
                            "sample_rate_hz": 48000,
                            "channels": 2,
                        }
                    )
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


def build_request(audio_path: Path, output_path: Path) -> RunRequest:
    return RunRequest(
        inputs=build_inputs(audio_path),
        outputs={
            "render": {
                "webvtt_transcript": AtomicFileTargetHandle(output_path),
            },
        },
    )

async def create_demo_fixture(
    work_dir: Path,
    *,
    owner_task_id: str,
    clean: bool = True,
) -> DemoFixture:
    if clean and work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    audio_path = work_dir / "input-audio.bin"
    output_path = work_dir / "output.vtt"
    manifest_path = work_dir / "stage-manifest.json"
    audio_path.write_bytes(bytes(range(256)) * 16)

    pipeline = (
        ChunkyPipeline.builder()
        .install(FileCacheFeature(work_dir / "cache"))
        .install(
            SummaryExampleFeature(
                provider_name="demo-asr",
                api_key="demo-api-key",
                concurrency_limit=8,
            )
        )
        .build()
    )
    await pipeline._ensure_runtime_services_ready()
    manifest_path.write_text(
        pipeline.stage_manifest.describe_json() + "\n",
        encoding="utf-8",
    )
    cache = pipeline.runtime_services.artifact_cache

    return DemoFixture(
        work_dir=work_dir,
        audio_path=audio_path,
        output_path=output_path,
        manifest_path=manifest_path,
        cache=cache,
        pipeline=pipeline,
    )
