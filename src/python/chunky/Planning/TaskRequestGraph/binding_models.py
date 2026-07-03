from __future__ import annotations

from dataclasses import dataclass

from ...Artifacts.artifact_models import ArtifactKey, PhaseMatchMode
from .shared_models import instance_id


@dataclass(frozen=True)
class ArtifactResolution:
    """Resolved artifact instance selected while expanding the request graph.

    This is intentionally narrower than the final input/output binding models.  It
    represents the answer to an artifact need: either a supplied/default artifact
    root, or an artifact produced by a planned invocation.
    """

    resolved_artifact_id: ArtifactKey
    resolved_instance_id: str
    producer_invocation_key: str | None = None

    @staticmethod
    def supplied(
        artifact_id: ArtifactKey,
        artifact_instance_id: str,
    ) -> "ArtifactResolution":
        return ArtifactResolution(
            resolved_artifact_id=artifact_id,
            resolved_instance_id=artifact_instance_id,
            producer_invocation_key=None,
        )

    @staticmethod
    def supplied_setting(
        artifact_id: ArtifactKey,
    ) -> "ArtifactResolution":
        return ArtifactResolution(
            resolved_artifact_id=artifact_id,
            resolved_instance_id=instance_id(artifact_id, ""),
            producer_invocation_key=None,
        )

    @staticmethod
    def produced(
        artifact_id: ArtifactKey,
        artifact_instance_id: str,
        producer_invocation_key: str,
    ) -> "ArtifactResolution":
        return ArtifactResolution(
            resolved_artifact_id=artifact_id,
            resolved_instance_id=artifact_instance_id,
            producer_invocation_key=producer_invocation_key,
        )

    @staticmethod
    def unresolved(artifact_id: ArtifactKey) -> "MissingOptionalArtifact":
        return MissingOptionalArtifact(artifact_id)

    @property
    def is_resolved(self) -> bool:
        return True

    @staticmethod
    def from_input_binding(
        binding: "PlannedInputBinding",
    ) -> "ArtifactResolution":
        return ArtifactResolution(
            resolved_artifact_id=binding.resolved_artifact_id,
            resolved_instance_id=binding.resolved_instance_id,
            producer_invocation_key=binding.producer_invocation_key,
        )

    @property
    def is_produced_by_invocation(self) -> bool:
        return self.producer_invocation_key is not None


@dataclass(frozen=True)
class MissingOptionalArtifact:
    """Result used when an optional artifact has no valid supplied or producer path."""

    artifact_id: ArtifactKey

    @property
    def resolved_artifact_id(self) -> ArtifactKey:
        return self.artifact_id

    @property
    def resolved_instance_id(self) -> None:
        return None

    @property
    def producer_invocation_key(self) -> None:
        return None

    @property
    def is_resolved(self) -> bool:
        return False

    @property
    def is_produced_by_invocation(self) -> bool:
        return False


ArtifactRequirementResult = ArtifactResolution | MissingOptionalArtifact


@dataclass(frozen=True)
class PlannedInputBinding:
    selector: ArtifactKey
    resolved_artifact_id: ArtifactKey
    resolved_instance_id: str
    required: bool
    phase_match: PhaseMatchMode
    pushed_settings: tuple[ArtifactKey, ...] = ()
    producer_invocation_key: str | None = None


@dataclass(frozen=True)
class PlannedOutputBinding:
    declared_artifact_id: ArtifactKey
    resolved_artifact_id: ArtifactKey
    instance_id: str
    expected_fingerprint: str | None = None

    @property
    def terminal_remapped(self) -> bool:
        return self.declared_artifact_id != self.resolved_artifact_id


@dataclass(frozen=True)
class RequestedOutputBinding:
    requested_artifact_id: ArtifactKey
    instance_id: str
