from __future__ import annotations

"""Shared phase-family semantics for artifact selection and validation."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .artifact_models import (
    ArtifactDeclaration,
    ArtifactKey,
    ArtifactRequirement,
    ArtifactRole,
    PhaseMatchMode,
)


def _phase_value(key: ArtifactKey) -> int:
    return key.phase if key.phase is not None else -1


def capability_covers_requirement(
    capability: ArtifactKey,
    requirement: ArtifactRequirement,
) -> bool:
    requested = requirement.artifact_id
    if capability == requested:
        return True
    if capability.family != requested.family:
        return False
    if capability.phase is None:
        return True
    if requested.phase is None:
        return False
    if requirement.phase_match is PhaseMatchMode.EXACT:
        return capability.phase == requested.phase
    return requested.phase <= capability.phase


@dataclass(frozen=True)
class ArtifactPhaseCatalog:
    artifacts: Mapping[ArtifactKey, ArtifactDeclaration[Any]]

    def __init__(self, artifacts: Mapping[ArtifactKey, ArtifactDeclaration[Any]]) -> None:
        object.__setattr__(self, "artifacts", MappingProxyType(dict(artifacts)))
        phased: dict[tuple[str | None, str, str], list[ArtifactKey]] = {}
        for key, declaration in artifacts.items():
            if declaration.role is ArtifactRole.INTERMEDIATE and key.phase is not None:
                phased.setdefault(key.family, []).append(key)
        object.__setattr__(
            self,
            "phased_families",
            MappingProxyType(
                {
                    family: tuple(sorted(keys, key=_phase_value))
                    for family, keys in phased.items()
                }
            ),
        )

    phased_families: Mapping[tuple[str | None, str, str], tuple[ArtifactKey, ...]]

    def declared_keys_for_family(
        self,
        family: tuple[str | None, str, str],
    ) -> tuple[ArtifactKey, ...]:
        return self.phased_families.get(family, ())

    def declared_phases_for_family(
        self,
        family: tuple[str | None, str, str],
    ) -> tuple[int, ...]:
        return tuple(key.phase for key in self.declared_keys_for_family(family) if key.phase is not None)

    def family_has_phases(
        self,
        family: tuple[str | None, str, str],
    ) -> bool:
        return bool(self.declared_keys_for_family(family))

    def concrete_artifact_is_known(self, key: ArtifactKey) -> bool:
        return key in self.artifacts

    def selector_artifact_is_known(self, key: ArtifactKey) -> bool:
        if key in self.artifacts:
            return True
        phases = self.declared_keys_for_family(key.family)
        if not phases:
            return False
        if key.phase is None:
            return True
        return any(_phase_value(phase) <= key.phase for phase in phases)

    def requestable_output_exists(self, key: ArtifactKey) -> bool:
        if key.phase is None:
            return self.selector_artifact_is_known(key)
        if self.concrete_artifact_is_known(key):
            return True
        phases = self.declared_keys_for_family(key.family)
        if not phases:
            return False
        has_lower = any(_phase_value(phase) <= key.phase for phase in phases)
        has_higher = any(_phase_value(phase) > key.phase for phase in phases)
        return has_lower and has_higher

    def matching_concrete_keys(
        self,
        selector: ArtifactKey,
        phase_match: PhaseMatchMode,
    ) -> tuple[ArtifactKey, ...]:
        phases = self.declared_keys_for_family(selector.family)
        if not phases:
            return ()
        if selector.phase is None:
            return phases
        if phase_match is PhaseMatchMode.EXACT:
            return (selector,) if selector in self.artifacts else ()
        return tuple(
            key
            for key in phases
            if key.phase is not None and key.phase <= selector.phase
        )

    def highest_matching_key(
        self,
        keys: Iterable[ArtifactKey],
        selector: ArtifactKey,
        phase_match: PhaseMatchMode,
    ) -> ArtifactKey | None:
        if selector in keys:
            return selector
        if selector.phase is None or phase_match is PhaseMatchMode.EXACT:
            return None
        candidates = [
            key
            for key in keys
            if key.family == selector.family
            and key.phase is not None
            and key.phase <= selector.phase
        ]
        if not candidates:
            return None
        return max(candidates, key=_phase_value)

    def capability_covers_requirement(
        self,
        capability: ArtifactKey,
        requirement: ArtifactRequirement,
    ) -> bool:
        return capability_covers_requirement(capability, requirement)

    def resolve_output_id(
        self,
        *,
        selector: ArtifactKey,
        declared_output_id: ArtifactKey,
    ) -> ArtifactKey:
        if selector.phase is not None or selector == declared_output_id:
            return declared_output_id
        declaration = self.artifacts.get(declared_output_id)
        if (
            declaration is None
            or declaration.role is not ArtifactRole.INTERMEDIATE
            or declared_output_id.family != selector.family
            or declared_output_id.phase is None
            or selector.phase is not None
        ):
            raise ValueError(
                f"invalid terminal remapping from {declared_output_id} to {selector}"
            )
        return selector

    def validate_resolved_output(
        self,
        *,
        declared_output_id: ArtifactKey,
        resolved_output_id: ArtifactKey,
    ) -> None:
        if resolved_output_id == declared_output_id:
            return
        expected = self.resolve_output_id(
            selector=resolved_output_id,
            declared_output_id=declared_output_id,
        )
        if expected != resolved_output_id:
            raise ValueError(
                f"invalid terminal remapping from {declared_output_id} to {resolved_output_id}"
            )
