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
    ASR_SETTINGS,
    LANGUAGE_SETTINGS,
    PREPARED_AUDIO,
    SPEECH_AUDIO_SETTINGS,
    TOKEN_TEXT,
    cpu_resources,
    fixed_outputs,
    topology_payload,
)


class TranscribeExecutor:
    async def execute(
        self,
        inputs: InputArtifacts,
        outputs: OutputArtifacts,
        context: StageExecutionContext,
    ) -> None:
        prepared = await inputs.get_artifact(PREPARED_AUDIO).read()
        asr = await inputs.get_artifact(ASR_SETTINGS).read()
        language = await inputs.get_artifact(LANGUAGE_SETTINGS).read()
        payload = topology_payload(
            context,
            stage="transcribe",
            extra={
                "prepared_audio_invocation": prepared["invocation_id"],
                "asr": asr,
                "language": language,
            },
        )
        context.cancellation.raise_if_cancelled()
        await outputs.get_artifact(TOKEN_TEXT).write(payload)


def transcribe_requirements(
    _context: StagePlanningContext,
) -> ResolvedStageRequirements:
    return ResolvedStageRequirements(
        inputs=(
            ArtifactRequirement(
                PREPARED_AUDIO.artifact_id,
                pushed_settings=(SPEECH_AUDIO_SETTINGS.artifact_id,),
            ),
            ArtifactRequirement(ASR_SETTINGS.artifact_id),
            ArtifactRequirement(LANGUAGE_SETTINGS.artifact_id),
        ),
    )


TRANSCRIBE_STAGE = StageRegistration(
    stage_id="asr.transcribe",
    display_name="Transcribe",
    registration_version="1",
    behavior_version="1",
    executor_factory=TranscribeExecutor,
    owned_artifacts=(
        ASR_SETTINGS,
        LANGUAGE_SETTINGS,
        TOKEN_TEXT,
    ),
    input_capabilities=(
        ArtifactCapability(PREPARED_AUDIO.artifact_id),
        ArtifactCapability(ASR_SETTINGS.artifact_id),
        ArtifactCapability(LANGUAGE_SETTINGS.artifact_id),
    ),
    output_capabilities=(ArtifactCapability(TOKEN_TEXT.artifact_id),),
    resolve_requirements=transcribe_requirements,
    resolve_output_capabilities=fixed_outputs(
        ArtifactCapability(TOKEN_TEXT.artifact_id),
    ),
    resolve_resource_policy=cpu_resources,
)
