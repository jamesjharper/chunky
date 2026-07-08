from __future__ import annotations

from ..Bindings.binding_models import InputArtifacts, OutputArtifacts
from ..Artifacts.artifact_models import ArtifactCapability, ArtifactRequirement
from ..Stages.stage_models import (
    ResolvedStageRequirements,
    StageExecutionContext,
    StagePlanningContext,
    StageRegistration,
)
from .common import (
    MUSIC_AUDIO_SETTINGS,
    MUSIC_SPANS,
    PREPARED_AUDIO,
    cpu_resources,
    fixed_outputs,
    topology_payload,
)


class DetectMusicExecutor:
    async def execute(
        self,
        inputs: InputArtifacts,
        outputs: OutputArtifacts,
        context: StageExecutionContext,
    ) -> None:
        prepared = await inputs.get_artifact(PREPARED_AUDIO).read()
        payload = topology_payload(
            context,
            stage="detect_music",
            extra={
                "prepared_audio_invocation": prepared["invocation_id"],
            },
        )
        context.cancellation.raise_if_cancelled()
        await outputs.get_artifact(MUSIC_SPANS).write(payload)


def detect_music_requirements(
    _context: StagePlanningContext,
) -> ResolvedStageRequirements:
    return ResolvedStageRequirements(
        inputs=(
            ArtifactRequirement(
                PREPARED_AUDIO.artifact_id,
                pushed_settings=(MUSIC_AUDIO_SETTINGS.artifact_id,),
            ),
        ),
    )


DETECT_MUSIC_STAGE = StageRegistration(
    stage_id="audio.detect_music",
    display_name="Detect music",
    registration_version="1",
    behavior_version="1",
    executor_factory=DetectMusicExecutor,
    owned_artifacts=(MUSIC_SPANS,),
    input_capabilities=(ArtifactCapability(PREPARED_AUDIO.artifact_id),),
    output_capabilities=(ArtifactCapability(MUSIC_SPANS.artifact_id),),
    resolve_requirements=detect_music_requirements,
    resolve_output_capabilities=fixed_outputs(
        ArtifactCapability(MUSIC_SPANS.artifact_id),
    ),
    resolve_resource_policy=cpu_resources,
)
