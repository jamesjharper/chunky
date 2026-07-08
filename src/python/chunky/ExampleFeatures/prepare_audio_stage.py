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
    AUDIO_SETTINGS,
    PREPARED_AUDIO,
    SOURCE_AUDIO,
    cpu_resources,
    fixed_outputs,
    topology_payload,
)


class PrepareAudioExecutor:
    async def execute(
        self,
        inputs: InputArtifacts,
        outputs: OutputArtifacts,
        context: StageExecutionContext,
    ) -> None:
        raw = await inputs.get_artifact(SOURCE_AUDIO).read()
        audio_settings = context.settings.require(AUDIO_SETTINGS.artifact_id)
        payload = topology_payload(
            context,
            stage="prepare_audio",
            extra={
                "source_bytes": len(raw),
                "audio_settings": dict(audio_settings),
            },
        )
        context.cancellation.raise_if_cancelled()
        await outputs.get_artifact(PREPARED_AUDIO).write(payload)


def prepare_audio_requirements(
    _context: StagePlanningContext,
) -> ResolvedStageRequirements:
    return ResolvedStageRequirements(
        inputs=(ArtifactRequirement(SOURCE_AUDIO.artifact_id),),
    )


PREPARE_AUDIO_STAGE = StageRegistration(
    stage_id="audio.prepare",
    display_name="Prepare audio",
    registration_version="1",
    behavior_version="1",
    executor_factory=PrepareAudioExecutor,
    owned_artifacts=(PREPARED_AUDIO,),
    input_capabilities=(
        ArtifactCapability(SOURCE_AUDIO.artifact_id),
        ArtifactCapability(AUDIO_SETTINGS.artifact_id),
    ),
    output_capabilities=(ArtifactCapability(PREPARED_AUDIO.artifact_id),),
    resolve_requirements=prepare_audio_requirements,
    resolve_output_capabilities=fixed_outputs(
        ArtifactCapability(PREPARED_AUDIO.artifact_id),
    ),
    resolve_resource_policy=cpu_resources,
)
