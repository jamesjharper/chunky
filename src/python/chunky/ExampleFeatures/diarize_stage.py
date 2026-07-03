from __future__ import annotations

from ..Artifacts.binding_models import InputArtifacts, OutputArtifacts
from ..Artifacts.artifact_models import ArtifactCapability, ArtifactRequirement
from ..Stages.stage_models import (
    ResolvedStageRequirements,
    StageExecutionContext,
    StagePlanningContext,
    StageRegistration,
)
from .common import (
    DIARIZATION_SETTINGS,
    PREPARED_AUDIO,
    SPEAKER_SEGMENTS,
    SPEECH_AUDIO_SETTINGS,
    cpu_resources,
    fixed_outputs,
    topology_payload,
)


class DiarizeExecutor:
    async def execute(
        self,
        inputs: InputArtifacts,
        outputs: OutputArtifacts,
        context: StageExecutionContext,
    ) -> None:
        prepared = await inputs.get_artifact(PREPARED_AUDIO).read()
        diarization = await inputs.get_artifact(DIARIZATION_SETTINGS).read()
        payload = topology_payload(
            context,
            stage="diarize",
            extra={
                "prepared_audio_invocation": prepared["invocation_id"],
                "diarization": diarization,
            },
        )
        context.cancellation.raise_if_cancelled()
        await outputs.get_artifact(SPEAKER_SEGMENTS).write(payload)


def diarize_requirements(
    _context: StagePlanningContext,
) -> ResolvedStageRequirements:
    return ResolvedStageRequirements(
        inputs=(
            ArtifactRequirement(
                PREPARED_AUDIO.artifact_id,
                pushed_settings=(SPEECH_AUDIO_SETTINGS.artifact_id,),
            ),
            ArtifactRequirement(DIARIZATION_SETTINGS.artifact_id),
        ),
    )


DIARIZE_STAGE = StageRegistration(
    stage_id="speaker.diarize",
    display_name="Diarize",
    registration_version="1",
    behavior_version="1",
    executor_factory=DiarizeExecutor,
    owned_artifacts=(
        DIARIZATION_SETTINGS,
        SPEAKER_SEGMENTS,
    ),
    input_capabilities=(
        ArtifactCapability(PREPARED_AUDIO.artifact_id),
        ArtifactCapability(DIARIZATION_SETTINGS.artifact_id),
    ),
    output_capabilities=(ArtifactCapability(SPEAKER_SEGMENTS.artifact_id),),
    resolve_requirements=diarize_requirements,
    resolve_output_capabilities=fixed_outputs(
        ArtifactCapability(SPEAKER_SEGMENTS.artifact_id),
    ),
    resolve_resource_policy=cpu_resources,
)
