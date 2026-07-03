from __future__ import annotations

"""Bootstrap-time stage registration and immutable manifest creation.

The existing registration model remains authoritative.  This extension adds
family-aware indexes used by graph creation without moving planning metadata
back onto executor classes.
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from types import MappingProxyType
from typing import Any, Callable

from ..Artifacts.artifact_models import (
    ArtifactCapability,
    ArtifactDeclaration,
    ArtifactKey,
    ArtifactRole,
    PhaseMatchMode,
)
from ..Artifacts.phase_catalog import ArtifactPhaseCatalog
from ..Artifacts.fingerprints import stable_hash
from .stage_models import (
    FeatureRecord,
    ManifestStageRegistration,
    PipelineFeature,
    StageManifestError,
    StageRegistration,
)


@dataclass(frozen=True)
class StageProducer:
    stage_id: str
    declared_output_id: ArtifactKey


@dataclass(frozen=True)
class StageConsumer:
    stage_id: str


class StageManifest:
    """Validated immutable runtime manifest and static candidate graph source."""

    manifest_version = "1"

    def __init__(
        self,
        *,
        manifest_id: str,
        features: dict[str, FeatureRecord],
        stages: dict[str, StageRegistration],
        artifacts: dict[ArtifactKey, ArtifactDeclaration[Any]],
        producers: dict[ArtifactKey, tuple[StageProducer, ...]],
        consumers: dict[ArtifactKey, tuple[StageConsumer, ...]],
        default_producers: dict[ArtifactKey, str],
        description: dict[str, object],
    ) -> None:
        self.manifest_id = manifest_id
        self.features = MappingProxyType(dict(features))
        self.stages = MappingProxyType(dict(stages))
        self.artifacts = MappingProxyType(dict(artifacts))
        self.producers = MappingProxyType(dict(producers))
        self.consumers = MappingProxyType(dict(consumers))
        self.default_producers = MappingProxyType(dict(default_producers))
        self.phase_catalog = ArtifactPhaseCatalog(artifacts)
        self.phased_families = self.phase_catalog.phased_families
        self._description = _freeze_json_value(description)

    def find_artifact(
        self,
        selector: ArtifactDeclaration[Any] | ArtifactKey | str,
    ) -> ArtifactDeclaration[Any]:
        key = _artifact_key(selector)
        try:
            return self.artifacts[key]
        except KeyError as exc:
            raise StageManifestError(f"unknown concrete artifact declaration: {key}") from exc

    def find_artifacts_by_role(
        self,
        artifact_ids: Iterable[ArtifactKey],
        *,
        role: ArtifactRole,
    ) -> frozenset[ArtifactKey]:
        return frozenset(
            artifact_id
            for artifact_id in artifact_ids
            if (declaration := self.artifacts.get(artifact_id)) is not None
            and declaration.role is role
        )

    def find_stage(self, stage_id: str) -> ManifestStageRegistration:
        try:
            return ManifestStageRegistration(self, self.stages[stage_id])
        except KeyError as exc:
            raise StageManifestError(f"unknown stage registration: {stage_id}") from exc
        
    def find_producers_for_requirement(self, selector: ArtifactKey, *, phase_match: PhaseMatchMode = PhaseMatchMode.AT_MOST) -> tuple[StageProducer, ...]:
        declaration = self.artifacts.get(selector)
        if declaration is not None and (
            declaration.role is not ArtifactRole.INTERMEDIATE
            or selector.phase is None
            or phase_match is PhaseMatchMode.EXACT
        ):
            return self.producers.get(selector, ())

        candidate_keys = self.phase_catalog.matching_concrete_keys(selector, phase_match)
        if not candidate_keys:
            return self.producers.get(selector, ())

        candidates: list[StageProducer] = []
        for key in candidate_keys:
            candidates.extend(self.producers.get(key, ()))
        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    -(item.declared_output_id.phase or -1),
                    item.stage_id,
                ),
            )
        )

    def find_output_declaration(self, *,  declared_output_id: ArtifactKey, resolved_output_id: ArtifactKey) -> ArtifactDeclaration[Any]:
        declaration = self.find_artifact(declared_output_id)
        if declared_output_id == resolved_output_id:
            return declaration
        try:
            self.phase_catalog.validate_resolved_output(
                declared_output_id=declared_output_id,
                resolved_output_id=resolved_output_id,
            )
        except ValueError as exc:
            raise StageManifestError(
                "invalid terminal remapping from "
                f"{declared_output_id} to {resolved_output_id}"
            ) from exc
        # The stage writes the concrete declaration, but cache/publication identity
        # is the phase-less completed family. Codec/schema remain those of the
        # selected terminal producer.
        return replace(declaration, artifact_id=resolved_output_id)

    def concrete_artifact_is_known(self, selector: ArtifactDeclaration[Any] | ArtifactKey | str,
    ) -> bool:
        key = _artifact_key(selector)
        return self.phase_catalog.concrete_artifact_is_known(key)

    def selector_artifact_is_known(self, key: ArtifactKey) -> bool:
        return self.phase_catalog.selector_artifact_is_known(key)


    def describe(self) -> dict[str, object]:
        return _thaw_json_value(self._description)  # type: ignore[return-value]

    def describe_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.describe(), sort_keys=True, indent=indent)



class StageManifestBuilder:
    """Mutable bootstrap registry. It becomes unusable after ``freeze``."""

    def __init__(self) -> None:
        self._features: dict[str, FeatureRecord] = {}
        self._installing: set[str] = set()
        self._artifacts: dict[ArtifactKey, ArtifactDeclaration[Any]] = {}
        self._stages: dict[str, StageRegistration] = {}
        self._default_producers: dict[ArtifactKey, str] = {}
        self._frozen = False

    def install_feature(self, feature: PipelineFeature) -> "StageManifestBuilder":
        self._assert_mutable()
        feature_id = _non_empty(feature.feature_id, "feature_id")
        configuration_fingerprint = _non_empty(
            feature.configuration_fingerprint,
            f"feature {feature_id} configuration_fingerprint",
        )
        existing = self._features.get(feature_id)
        if existing is not None:
            if existing.configuration_fingerprint != configuration_fingerprint:
                raise StageManifestError(
                    f"feature {feature_id!r} was installed with conflicting configuration"
                )
            return self
        if feature_id in self._installing:
            raise StageManifestError(f"recursive feature dependency: {feature_id}")

        self._installing.add(feature_id)
        try:
            feature.install(self)
            self._features[feature_id] = FeatureRecord(
                feature_id=feature_id,
                configuration_fingerprint=configuration_fingerprint,
            )
        finally:
            self._installing.remove(feature_id)
        return self

    def add_artifact(
        self,
        declaration: ArtifactDeclaration[Any],
    ) -> "StageManifestBuilder":
        self._assert_mutable()
        existing = self._artifacts.get(declaration.artifact_id)
        if existing is None:
            self._artifacts[declaration.artifact_id] = declaration
        elif _artifact_signature(existing) != _artifact_signature(declaration):
            raise StageManifestError(
                f"conflicting declarations for artifact {declaration.artifact_id}"
            )
        return self

    def add_stage(self, registration: StageRegistration) -> "StageManifestBuilder":
        self._assert_mutable()
        if registration.stage_id in self._stages:
            raise StageManifestError(f"duplicate stage id: {registration.stage_id}")
        for declaration in registration.owned_artifacts:
            self.add_artifact(declaration)
        self._stages[registration.stage_id] = registration
        return self

    def set_default_producer(
        self,
        artifact_id: ArtifactKey | str,
        stage_id: str,
    ) -> "StageManifestBuilder":
        self._assert_mutable()
        key = artifact_id if isinstance(artifact_id, ArtifactKey) else ArtifactKey.parse(artifact_id)
        self._default_producers[key] = stage_id
        return self

    def validate(self) -> "StageManifestBuilder":
        self._assert_mutable()
        _validate_registry(self._artifacts, self._stages, self._default_producers)
        return self

    def freeze(self) -> StageManifest:
        self._assert_mutable()
        self.validate()
        producers = _build_producer_index(self._stages)
        consumers = _build_consumer_index(self._stages)
        description = _build_description(
            features=self._features,
            stages=self._stages,
            artifacts=self._artifacts,
            default_producers=self._default_producers,
        )
        identity_description = {
            **description,
            "manifest_version": StageManifest.manifest_version,
        }
        manifest_id = stable_hash(identity_description)
        description = {**identity_description, "manifest_id": manifest_id}
        self._frozen = True
        return StageManifest(
            manifest_id=manifest_id,
            features=self._features,
            stages=self._stages,
            artifacts=self._artifacts,
            producers=producers,
            consumers=consumers,
            default_producers=self._default_producers,
            description=description,
        )

    def _assert_mutable(self) -> None:
        if self._frozen:
            raise StageManifestError("stage manifest builder is already frozen")


def _validate_registry(
    artifacts: dict[ArtifactKey, ArtifactDeclaration[Any]],
    stages: dict[str, StageRegistration],
    default_producers: dict[ArtifactKey, str],
) -> None:
    if not stages:
        raise StageManifestError("manifest must contain at least one stage")

    phase_catalog = ArtifactPhaseCatalog(artifacts)
    for key, declaration in artifacts.items():
        if (
            declaration.role is ArtifactRole.INTERMEDIATE
            and key.phase is None
            and phase_catalog.family_has_phases(key.family)
        ):
            raise StageManifestError(
                "a phased intermediate family is declared through concrete phases; "
                f"the completed selector must not be separately declared: {key}"
            )

    for stage_id, registration in stages.items():
        _non_empty(stage_id, "stage_id")
        _non_empty(registration.display_name, f"stage {stage_id} display_name")
        _non_empty(registration.registration_version, f"stage {stage_id} registration_version")
        _non_empty(registration.behavior_version, f"stage {stage_id} behavior_version")
        if not callable(registration.executor_factory):
            raise StageManifestError(f"stage {stage_id} has no executor factory")
        if not callable(registration.resolve_requirements):
            raise StageManifestError(f"stage {stage_id} has no requirement resolver")
        if not callable(registration.resolve_output_capabilities):
            raise StageManifestError(f"stage {stage_id} has no output resolver")
        if not callable(registration.resolve_resource_policy):
            raise StageManifestError(f"stage {stage_id} has no resource resolver")

        owned_ids = [declaration.artifact_id for declaration in registration.owned_artifacts]
        if len(owned_ids) != len(set(owned_ids)):
            raise StageManifestError(f"stage {stage_id} owns a duplicate artifact declaration")
        for declaration in registration.owned_artifacts:
            authoritative = artifacts.get(declaration.artifact_id)
            if authoritative is None or _artifact_signature(authoritative) != _artifact_signature(declaration):
                raise StageManifestError(
                    f"stage {stage_id} has an inconsistent owned artifact: "
                    f"{declaration.artifact_id}"
                )

        _validate_input_capabilities(
            stage_id,
            registration.input_capabilities,
            artifacts,
            phase_catalog,
        )
        _validate_output_capabilities(
            stage_id,
            registration.output_capabilities,
            artifacts,
            phase_catalog,
        )

    producers = _build_producer_index(stages)
    temporary = StageManifest(
        manifest_id="validation",
        features={},
        stages=stages,
        artifacts=artifacts,
        producers=producers,
        consumers=_build_consumer_index(stages),
        default_producers=default_producers,
        description={},
    )
    _validate_unique_exact_producers(temporary)
    for artifact_id, stage_id in default_producers.items():
        if not temporary.selector_artifact_is_known(artifact_id):
            raise StageManifestError(
                f"default producer references unknown artifact selector: {artifact_id}"
            )
        candidates = {
            producer.stage_id
            for producer in temporary.find_producers_for_requirement(artifact_id)
        }
        if stage_id not in candidates:
            raise StageManifestError(
                f"default producer {stage_id!r} cannot satisfy {artifact_id}"
            )

    _validate_candidate_graph_has_no_cycles(temporary)


def _validate_input_capabilities(
    stage_id: str,
    capabilities: tuple[ArtifactCapability, ...],
    artifacts: dict[ArtifactKey, ArtifactDeclaration[Any]],
    phase_catalog: ArtifactPhaseCatalog,
) -> None:
    ids = [capability.artifact_id for capability in capabilities]
    if len(ids) != len(set(ids)):
        raise StageManifestError(f"stage {stage_id} has duplicate input capabilities")
    for artifact_id in ids:
        if artifact_id in artifacts:
            continue
        phases = phase_catalog.declared_phases_for_family(artifact_id.family)
        if not phases:
            raise StageManifestError(
                f"stage {stage_id} input capability references unknown artifact selector: "
                f"{artifact_id}"
            )
        if artifact_id.phase is not None and not any(phase <= artifact_id.phase for phase in phases):
            raise StageManifestError(
                f"stage {stage_id} input capability has no matching phase: {artifact_id}"
            )


def _validate_output_capabilities(
    stage_id: str,
    capabilities: tuple[ArtifactCapability, ...],
    artifacts: dict[ArtifactKey, ArtifactDeclaration[Any]],
    phase_catalog: ArtifactPhaseCatalog,
) -> None:
    ids = [capability.artifact_id for capability in capabilities]
    if len(ids) != len(set(ids)):
        raise StageManifestError(f"stage {stage_id} has duplicate output capabilities")
    for artifact_id in ids:
        if artifact_id not in artifacts:
            raise StageManifestError(
                f"stage {stage_id} output capability references unknown concrete artifact: "
                f"{artifact_id}"
            )
        declaration = artifacts[artifact_id]
        if (
            declaration.role is ArtifactRole.INTERMEDIATE
            and phase_catalog.family_has_phases(artifact_id.family)
            and artifact_id.phase is None
        ):
            raise StageManifestError(
                f"phased intermediate output must declare a concrete phase: {artifact_id}"
            )


def _build_producer_index(
    stages: dict[str, StageRegistration],
) -> dict[ArtifactKey, tuple[StageProducer, ...]]:
    return _build_stage_index(
        stages=stages,
        capabilities_for=lambda registration: registration.output_capabilities,
        entry_factory=lambda registration, artifact_id: StageProducer(
            stage_id=registration.stage_id,
            declared_output_id=artifact_id,
        ),
    )


def _validate_unique_exact_producers(manifest: StageManifest) -> None:
    for artifact_id, candidates in manifest.producers.items():
        if len(candidates) <= 1:
            continue
        ids = ", ".join(sorted(candidate.stage_id for candidate in candidates))
        raise StageManifestError(
            f"artifact {artifact_id} has multiple registered producers: {ids}"
        )


def _build_consumer_index(
    stages: dict[str, StageRegistration],
) -> dict[ArtifactKey, tuple[StageConsumer, ...]]:
    return _build_stage_index(
        stages=stages,
        capabilities_for=lambda registration: registration.input_capabilities,
        entry_factory=lambda registration, _artifact_id: StageConsumer(
            stage_id=registration.stage_id,
        ),
    )


def _build_stage_index[T](
    *,
    stages: dict[str, StageRegistration],
    capabilities_for: Callable[[StageRegistration], tuple[ArtifactCapability, ...]],
    entry_factory: Callable[[StageRegistration, ArtifactKey], T],
) -> dict[ArtifactKey, tuple[T, ...]]:
    mutable: dict[ArtifactKey, list[T]] = {}
    for registration in stages.values():
        for capability in capabilities_for(registration):
            mutable.setdefault(capability.artifact_id, []).append(
                entry_factory(registration, capability.artifact_id)
            )
    return {
        key: tuple(sorted(values, key=lambda item: item.stage_id))
        for key, values in mutable.items()
    }


def _validate_candidate_graph_has_no_cycles(manifest: StageManifest) -> None:
    graph: dict[str, set[str]] = {stage_id: set() for stage_id in manifest.stages}
    for consumer in manifest.stages.values():
        for capability in consumer.input_capabilities:
            for producer in manifest.find_producers_for_requirement(capability.artifact_id):
                graph[producer.stage_id].add(consumer.stage_id)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(stage_id: str, path: tuple[str, ...]) -> None:
        if stage_id in visiting:
            cycle = " -> ".join((*path, stage_id))
            raise StageManifestError(f"static candidate graph contains a cycle: {cycle}")
        if stage_id in visited:
            return
        visiting.add(stage_id)
        for downstream in sorted(graph[stage_id]):
            visit(downstream, (*path, stage_id))
        visiting.remove(stage_id)
        visited.add(stage_id)

    for stage_id in sorted(graph):
        visit(stage_id, ())


def _build_description(
    *,
    features: dict[str, FeatureRecord],
    stages: dict[str, StageRegistration],
    artifacts: dict[ArtifactKey, ArtifactDeclaration[Any]],
    default_producers: dict[ArtifactKey, str],
) -> dict[str, object]:
    phase_catalog = ArtifactPhaseCatalog(artifacts)
    return {
        "features": [
            {
                "feature_id": record.feature_id,
                "configuration_fingerprint": record.configuration_fingerprint,
            }
            for record in sorted(features.values(), key=lambda item: item.feature_id)
        ],
        "stages": [
            {
                "stage_id": registration.stage_id,
                "display_name": registration.display_name,
                "registration_version": registration.registration_version,
                "behavior_version": registration.behavior_version,
                "owned_artifact_ids": sorted(
                    str(declaration.artifact_id)
                    for declaration in registration.owned_artifacts
                ),
                "input_capability_ids": sorted(
                    str(capability.artifact_id)
                    for capability in registration.input_capabilities
                ),
                "output_capability_ids": sorted(
                    str(capability.artifact_id)
                    for capability in registration.output_capabilities
                ),
                "concurrency_policy": (
                    None
                    if registration.concurrency_policy is None
                    else asdict(registration.concurrency_policy)
                ),
            }
            for registration in sorted(stages.values(), key=lambda item: item.stage_id)
        ],
        "artifacts": [
            _artifact_description(declaration)
            for declaration in sorted(artifacts.values(), key=lambda item: str(item.artifact_id))
        ],
        "phased_families": [
            {
                "family_id": str(ArtifactKey(domain, content_kind)),
                "declared_phases": sorted(phase_catalog.declared_phases_for_family(family)),
            }
            for family in sorted(phase_catalog.phased_families)
            for _override_domain, domain, content_kind in (family,)
        ],
        "default_producers": [
            {"artifact_id": str(key), "stage_id": stage_id}
            for key, stage_id in sorted(default_producers.items(), key=lambda item: str(item[0]))
        ],
    }


def _artifact_description(declaration: ArtifactDeclaration[Any]) -> dict[str, object]:
    return {
        "artifact_id": str(declaration.artifact_id),
        "role": declaration.role.value,
        "version": declaration.version,
        "cacheable": declaration.cacheable,
        "has_default": declaration.default_value_source_provider is not None,
        "default_value_source_provider_id": _provider_identity(
            declaration.default_value_source_provider
        ),
        "codec": {
            "codec_id": declaration.codec.codec_id,
            "codec_version": declaration.codec.codec_version,
            "mime_type": declaration.codec.mime_type,
            "extension": declaration.codec.extension,
        },
    }


def _artifact_signature(declaration: ArtifactDeclaration[Any]) -> tuple[object, ...]:
    return (
        declaration.artifact_id,
        declaration.role,
        declaration.version,
        declaration.cacheable,
        declaration.codec.codec_id,
        declaration.codec.codec_version,
        declaration.codec.mime_type,
        declaration.codec.extension,
        _provider_identity(declaration.default_value_source_provider),
    )


def _provider_identity(provider: object | None) -> str | None:
    if provider is None:
        return None
    stable_id = getattr(provider, "provider_id", None)
    if stable_id is not None:
        return str(stable_id)
    provider_type = type(provider)
    return f"{provider_type.__module__}.{provider_type.__qualname__}"


def _artifact_key(selector: ArtifactDeclaration[Any] | ArtifactKey | str) -> ArtifactKey:
    if isinstance(selector, ArtifactDeclaration):
        return selector.artifact_id
    if isinstance(selector, ArtifactKey):
        return selector
    return ArtifactKey.parse(selector)


def _non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StageManifestError(f"{field_name} must be a non-empty string")
    return value


def _freeze_json_value(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType(
            {str(key): _freeze_json_value(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _thaw_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value
