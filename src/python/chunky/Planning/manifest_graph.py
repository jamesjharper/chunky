from __future__ import annotations

"""Request scoping for graph planning."""

from dataclasses import dataclass

from ..Artifacts.artifact_models import ArtifactKey
from ..Artifacts.fingerprints import FingerprintProvider
from ..Stages.stage_manifest import StageManifest


@dataclass(frozen=True)
class ManifestProducerEdge:
    stage_id: str
    output_artifact_id: ArtifactKey


@dataclass(frozen=True)
class ManifestConsumerEdge:
    stage_id: str
    input_selector: ArtifactKey


@dataclass(frozen=True)
class StageManifestGraph:
    """Static manifest connectivity before a request is applied."""

    manifest_id: str
    stage_ids: tuple[str, ...]
    stage_fingerprints_by_id: dict[str, str]
    producer_edges: tuple[ManifestProducerEdge, ...]
    consumer_edges: tuple[ManifestConsumerEdge, ...]

    def stage_fingerprint(self, stage_id: str) -> str:
        return self.stage_fingerprints_by_id[stage_id]


class StageManifestGraphBuilder:
    def __init__(self, manifest: StageManifest) -> None:
        self._manifest = manifest

    def build(self) -> StageManifestGraph:
        return StageManifestGraph(
            manifest_id=self._manifest.manifest_id,
            stage_ids=tuple(sorted(self._manifest.stages)),
            stage_fingerprints_by_id={
                stage_id: FingerprintProvider.build_stage_fingerprint(
                    stage_id=stage_id,
                    behavior_version=registration.behavior_version,
                )
                for stage_id, registration in sorted(self._manifest.stages.items())
            },
            producer_edges=tuple(
                ManifestProducerEdge(
                    stage_id=producer.stage_id,
                    output_artifact_id=artifact_id,
                )
                for artifact_id, producers in sorted(
                    self._manifest.producers.items(), key=lambda item: str(item[0])
                )
                for producer in producers
            ),
            consumer_edges=tuple(
                ManifestConsumerEdge(
                    stage_id=consumer.stage_id,
                    input_selector=selector,
                )
                for selector, consumers in sorted(
                    self._manifest.consumers.items(), key=lambda item: str(item[0])
                )
                for consumer in consumers
            ),
        )


__all__ = [
    "ManifestConsumerEdge",
    "ManifestProducerEdge",
    "StageManifestGraph",
    "StageManifestGraphBuilder",
]
