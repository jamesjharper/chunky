from __future__ import annotations

"""Core artifact contracts demonstrated by the example.

The artifact I/O contracts remain the source of truth. This module only adds
small planning metadata needed by the graph-creation example: phase match mode
and optional settings propagation on requirements.
"""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

if TYPE_CHECKING:
    from .artifact_handles import ArtifactSourceHandle

T = TypeVar("T")

@dataclass(frozen=True, order=True)
class ArtifactKey:
    domain: str
    content_kind: str
    phase: int | None = None
    override_domain: str | None = None

    @classmethod
    def parse(cls, value: str) -> "ArtifactKey":
        parts = value.split("//")
        if len(parts) == 2:
            override_domain = None
            domain, remainder = parts
        elif len(parts) == 3:
            override_domain, domain, remainder = parts
        else:
            raise ValueError(
                "artifact key must be DOMAIN//CONTENT_KIND[#PHASE] or "
                f"OVERRIDE_DOMAIN//DOMAIN//CONTENT_KIND[#PHASE]: {value!r}"
            )

        if not domain or not remainder or override_domain == "":
            raise ValueError(f"artifact key contains an empty component: {value!r}")

        if "#" in remainder:
            content_kind, phase_text = remainder.rsplit("#", 1)
            if not content_kind:
                raise ValueError(f"artifact content kind is empty: {value!r}")
            return cls(domain, content_kind, int(phase_text), override_domain)

        return cls(domain, remainder, None, override_domain)

    @property
    def family(self) -> tuple[str | None, str, str]:
        return self.override_domain, self.domain, self.content_kind

    def without_phase(self) -> "ArtifactKey":
        return ArtifactKey(
            self.domain,
            self.content_kind,
            override_domain=self.override_domain,
        )

    def without_override(self) -> "ArtifactKey":
        return ArtifactKey(self.domain, self.content_kind, self.phase)

    def override(self, override_domain: str) -> "ArtifactKey":
        if not override_domain.strip():
            raise ValueError("override_domain must be a non-empty string")
        return ArtifactKey(
            self.domain,
            self.content_kind,
            self.phase,
            override_domain.strip(),
        )

    def __str__(self) -> str:
        suffix = "" if self.phase is None else f"#{self.phase}"
        prefix = "" if self.override_domain is None else f"{self.override_domain}//"
        return f"{prefix}{self.domain}//{self.content_kind}{suffix}"

class ArtifactRole(str, Enum):
    SOURCE = "source"
    SETTINGS = "settings"
    INTERMEDIATE = "intermediate"
    TARGET = "target"
    DIAGNOSTIC = "diagnostic"

class Codec(Protocol[T]):
    """Canonical serialization contract for storage/process boundaries."""

    codec_id: str
    codec_version: int
    mime_type: str
    extension: str

    async def serialize(self, value: T) -> bytes: ...

    async def deserialize(self, raw: bytes) -> T: ...


@dataclass(frozen=True)
class ArtifactCapability:
    artifact_id: ArtifactKey


class PhaseMatchMode(str, Enum):
    """How a phased intermediate requirement is matched by graph creation.

    ``AT_MOST`` is the default for a numbered selector.  A phase-less
    intermediate selector represents the completed family regardless of this
    field.  ``EXACT`` is included because the graph specification reserves an
    explicit exact-phase seam rather than changing artifact ID syntax.
    """

    AT_MOST = "at_most"
    EXACT = "exact"


@dataclass(frozen=True)
class ArtifactRequirement:
    artifact_id: ArtifactKey
    required: bool = True
    phase_match: PhaseMatchMode = PhaseMatchMode.AT_MOST
    # Pushed settings are copied from the current stage's effective settings
    # onto this upstream requirement edge during backward planning.
    pushed_settings: tuple[ArtifactKey, ...] = ()

    def __post_init__(self) -> None:
        if len(self.pushed_settings) != len(set(self.pushed_settings)):
            raise ValueError("artifact requirement contains duplicate pushed settings")

class DefaultValueSourceProvider(Protocol[T]):
    async def resolve_source(
        self,
        supplied_source: "ArtifactSourceHandle[T] | None",
        *,
        declaration: "ArtifactDeclaration[T]",
    ) -> "ArtifactSourceHandle[T]": ...


@dataclass(frozen=True)
class ArtifactDeclaration(Generic[T]):
    artifact_id: ArtifactKey
    role: ArtifactRole
    version: int
    codec: Codec[T]
    cacheable: bool = True
    default_value_source_provider: DefaultValueSourceProvider[T] | None = None

    def __post_init__(self) -> None:
        if (
            self.artifact_id.phase is not None
            and self.role is not ArtifactRole.INTERMEDIATE
        ):
            raise ValueError("only intermediate artifact declarations may use phases")

    def override(
        self,
        *,
        override_domain: str,
        default_value_source_provider: DefaultValueSourceProvider[T] | None = None,
    ) -> "ArtifactDeclaration[T]":
        return ArtifactDeclaration(
            artifact_id=self.artifact_id.override(override_domain),
            role=self.role,
            version=self.version,
            codec=self.codec,
            cacheable=self.cacheable,
            default_value_source_provider=(
                default_value_source_provider
                if default_value_source_provider is not None
                else self.default_value_source_provider
            ),
        )



@dataclass(frozen=True)
class ArtifactHandle(Generic[T]):
    declaration: ArtifactDeclaration[T]
    fingerprint: str
    source: ArtifactSourceHandle[T] | None = None
    targets: tuple[ArtifactTargetHandle[T], ...] = ()

@dataclass(frozen=True)
class ArtifactInstance(Generic[T]):
    handle: ArtifactHandle[T]

    @property
    def fingerprint(self) -> str:
        return self.handle.fingerprint
