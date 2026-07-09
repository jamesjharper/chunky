from __future__ import annotations

"""Core artifact contracts demonstrated by the example.

The artifact I/O contracts remain the source of truth. This module only adds
small planning metadata needed by the graph-creation example: phase match mode
and optional settings propagation on requirements.
"""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar
from urllib.parse import unquote, quote

if TYPE_CHECKING:
    from .artifact_handles import ArtifactSourceHandle

T = TypeVar("T")

GLOBAL_PROJECTION_CONTEXT = "global"
A_REF_SCHEME = "a-ref:"
A_INSTANCE_SCHEME = "a-instance:"
A_TASK_SCHEME = "a-task:"
ANY_PHASE = "any"
NO_PHASE = "none"


def _validate_segment(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    cleaned = value.strip()
    if "/" in cleaned:
        raise ValueError(f"{name} must not contain '/': {value!r}")
    return cleaned


def _encode_segment(value: str) -> str:
    return quote(value, safe="._-~")


def _decode_path(value: str) -> list[str]:
    return [unquote(segment) for segment in value.split("/") if segment]


def _parse_phase_ref(value: str) -> int | None:
    if value == ANY_PHASE:
        return None
    return int(value)


def _format_phase_ref(phase: int | None) -> str:
    return ANY_PHASE if phase is None else str(int(phase))


def _parse_phase_concrete(value: str) -> int | None:
    if value == ANY_PHASE:
        raise ValueError("concrete artifact locators must not use the 'any' phase token")
    if value == NO_PHASE:
        return None
    return int(value)


def _format_phase_concrete(phase: int | None) -> str:
    return NO_PHASE if phase is None else str(int(phase))


@dataclass(frozen=True, order=True)
class ArtifactKey:
    """Structured semantic artifact identity.

    ``projection_context`` is retained so request-boundary forms such as
    ``speech_processing/audio_preprocessing/audio_settings`` can be
    parsed and projected forms can be regenerated. The canonical semantic components remain ``domain``,
    ``content_kind`` and optional ``phase``.
    """

    domain: str
    content_kind: str
    phase: int | None = None
    projection_context: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", _validate_segment("domain", self.domain))
        object.__setattr__(
            self,
            "content_kind",
            _validate_segment("content_kind", self.content_kind),
        )
        if self.phase is not None:
            object.__setattr__(self, "phase", int(self.phase))
        if self.projection_context is not None:
            object.__setattr__(
                self,
                "projection_context",
                _validate_segment("projection_context", self.projection_context),
            )

    @classmethod
    def parse(cls, value: str) -> "ArtifactKey":
        """Parse the semantic ``a-ref`` surface.

        Both canonical ``a-ref:/...`` and short path-like forms such as
        ``domain/content_kind`` are accepted. Legacy ``DOMAIN//KIND`` and
        ``DOMAIN//KIND#PHASE`` strings are intentionally not accepted. Use
        ``from_a_ref`` at public boundaries when the caller-facing
        representation is important.
        """

        return cls.from_a_ref(value)

    @classmethod
    def from_a_ref(cls, value: str) -> "ArtifactKey":
        return ArtifactRefLocator.parse(value).artifact_key

    @property
    def override_domain(self) -> str | None:
        """Compatibility name for existing planner code.

        The field now means projection context. It is not part of concrete cache
        identity, but existing settings-propagation code still refers to the old
        name internally.
        """

        return self.projection_context

    @property
    def family(self) -> tuple[str | None, str, str]:
        # Existing planning code expects the projection slot here. Keeping this
        # shape avoids destabilising phased-setting behaviour while locator
        # objects provide projection-insensitive semantic comparison.
        return self.projection_context, self.domain, self.content_kind

    @property
    def canonical_family(self) -> tuple[str, str]:
        return self.domain, self.content_kind

    def without_phase(self) -> "ArtifactKey":
        return ArtifactKey(
            self.domain,
            self.content_kind,
            projection_context=self.projection_context,
        )

    def without_override(self) -> "ArtifactKey":
        return ArtifactKey(self.domain, self.content_kind, self.phase)

    def without_projection(self) -> "ArtifactKey":
        return self.without_override()

    def override(self, override_domain: str) -> "ArtifactKey":
        return self.with_projection(override_domain)

    def with_projection(self, projection_context: str) -> "ArtifactKey":
        return ArtifactKey(
            self.domain,
            self.content_kind,
            self.phase,
            projection_context.strip(),
        )

    def to_a_ref(self, *, projected: bool = False) -> str:
        projection = (
            self.projection_context
            if projected and self.projection_context is not None
            else GLOBAL_PROJECTION_CONTEXT
        )
        return ArtifactRefLocator(projection, self.without_projection()).to_uri()

    def __str__(self) -> str:
        return self.to_a_ref(projected=False)


@dataclass(frozen=True)
class ArtifactRefLocator:
    """Boundary URI for declaration-level semantic artifact references."""

    projection_context: str
    artifact_key: ArtifactKey

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "projection_context",
            _validate_segment("projection_context", self.projection_context),
        )
        object.__setattr__(self, "artifact_key", self.artifact_key.without_projection())

    @classmethod
    def parse(cls, value: str) -> "ArtifactRefLocator":
        if not isinstance(value, str):
            raise ValueError(f"artifact reference must be a string: {value!r}")
        has_scheme = value.startswith(A_REF_SCHEME)
        if not has_scheme and ("//" in value or "#" in value or "::" in value):
            raise ValueError(
                "legacy artifact reference strings are not accepted; "
                "use domain/content_kind[/phase] or a-ref:/domain/content_kind[/phase]"
            )
        path = value[len(A_REF_SCHEME):] if has_scheme else value
        segments = _decode_path(path)
        if len(segments) == 2:
            projection = GLOBAL_PROJECTION_CONTEXT
            domain, content_kind = segments
            phase = None
        elif len(segments) == 3:
            if segments[-1] == ANY_PHASE or _looks_like_int(segments[-1]):
                projection = GLOBAL_PROJECTION_CONTEXT
                domain, content_kind, phase_text = segments
                phase = _parse_phase_ref(phase_text)
            else:
                projection, domain, content_kind = segments
                phase = None
        elif len(segments) == 4:
            projection, domain, content_kind, phase_text = segments
            phase = _parse_phase_ref(phase_text)
        else:
            raise ValueError(
                "artifact reference path must be domain/content_kind[/phase] or "
                f"projection/domain/content_kind[/phase], optionally prefixed by a-ref:/: {value!r}"
            )
        return cls(
            projection,
            ArtifactKey(domain=domain, content_kind=content_kind, phase=phase),
        )

    @classmethod
    def from_key(
        cls,
        artifact_key: ArtifactKey,
        *,
        projection_context: str | None = None,
    ) -> "ArtifactRefLocator":
        return cls(
            projection_context or artifact_key.projection_context or GLOBAL_PROJECTION_CONTEXT,
            artifact_key.without_projection(),
        )

    def to_uri(self, *, projected: bool = False) -> str:
        projection = self.projection_context if projected else GLOBAL_PROJECTION_CONTEXT
        key = self.artifact_key.without_projection()
        return (
            f"{A_REF_SCHEME}/"
            f"{_encode_segment(projection)}/"
            f"{_encode_segment(key.domain)}/"
            f"{_encode_segment(key.content_kind)}/"
            f"{_format_phase_ref(key.phase)}"
        )

    def __str__(self) -> str:
        return self.to_uri(projected=False)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ArtifactRefLocator):
            return NotImplemented
        return self.artifact_key.without_projection() == other.artifact_key.without_projection()

    def __hash__(self) -> int:
        key = self.artifact_key.without_projection()
        return hash((key.domain, key.content_kind, key.phase))


@dataclass(frozen=True)
class ArtifactInstanceLocator:
    """Concrete artifact-instance locator backed by the final artifact fingerprint."""

    projection_context: str
    artifact_key: ArtifactKey
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "projection_context",
            _validate_segment("projection_context", self.projection_context),
        )
        object.__setattr__(self, "artifact_key", self.artifact_key.without_projection())
        object.__setattr__(self, "fingerprint", _validate_segment("fingerprint", self.fingerprint))

    @property
    def artifact_id(self) -> ArtifactKey:
        return self.artifact_key

    @classmethod
    def parse(cls, value: str) -> "ArtifactInstanceLocator":
        if not isinstance(value, str) or not value.startswith(A_INSTANCE_SCHEME):
            raise ValueError(f"artifact instance must use explicit a-instance scheme: {value!r}")
        segments = _decode_path(value[len(A_INSTANCE_SCHEME):])
        if len(segments) != 6 or segments[4] != "fingerprint":
            raise ValueError(
                "a-instance path must be "
                "/projection/domain/content_kind/phase/fingerprint/fingerprint"
            )
        projection, domain, content_kind, phase_text, _marker, fingerprint = segments
        return cls(
            projection,
            ArtifactKey(domain, content_kind, _parse_phase_concrete(phase_text)),
            fingerprint,
        )

    @classmethod
    def from_key(
        cls,
        artifact_key: ArtifactKey,
        fingerprint: str,
        *,
        projection_context: str | None = None,
    ) -> "ArtifactInstanceLocator":
        return cls(
            projection_context or artifact_key.projection_context or GLOBAL_PROJECTION_CONTEXT,
            artifact_key.without_projection(),
            fingerprint,
        )

    def to_uri(self, *, projected: bool = False) -> str:
        projection = self.projection_context if projected else GLOBAL_PROJECTION_CONTEXT
        key = self.artifact_key.without_projection()
        return (
            f"{A_INSTANCE_SCHEME}/"
            f"{_encode_segment(projection)}/"
            f"{_encode_segment(key.domain)}/"
            f"{_encode_segment(key.content_kind)}/"
            f"{_format_phase_concrete(key.phase)}/"
            f"fingerprint/{_encode_segment(self.fingerprint)}"
        )

    def __str__(self) -> str:
        return self.to_uri(projected=False)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ArtifactInstanceLocator):
            return NotImplemented
        return (
            self.artifact_key.without_projection(),
            self.fingerprint,
        ) == (
            other.artifact_key.without_projection(),
            other.fingerprint,
        )

    def __hash__(self) -> int:
        key = self.artifact_key.without_projection()
        return hash((key.domain, key.content_kind, key.phase, self.fingerprint))


@dataclass(frozen=True)
class TaskArtifactLocator:
    """Task-scoped locator for one requested output binding."""

    projection_context: str
    artifact_key: ArtifactKey
    task_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "projection_context",
            _validate_segment("projection_context", self.projection_context),
        )
        object.__setattr__(self, "artifact_key", self.artifact_key.without_projection())
        object.__setattr__(self, "task_id", _validate_segment("task_id", self.task_id))

    @property
    def artifact_id(self) -> ArtifactKey:
        return self.artifact_key

    @classmethod
    def parse(cls, value: str) -> "TaskArtifactLocator":
        if not isinstance(value, str) or not value.startswith(A_TASK_SCHEME):
            raise ValueError(f"task artifact must use explicit a-task scheme: {value!r}")
        segments = _decode_path(value[len(A_TASK_SCHEME):])
        if len(segments) != 6 or segments[4] != "task":
            raise ValueError(
                "a-task path must be /projection/domain/content_kind/phase/task/task_id"
            )
        projection, domain, content_kind, phase_text, _marker, task_id = segments
        return cls(
            projection,
            ArtifactKey(domain, content_kind, _parse_phase_concrete(phase_text)),
            task_id,
        )

    @classmethod
    def from_key(
        cls,
        artifact_key: ArtifactKey,
        task_id: str,
        *,
        projection_context: str | None = None,
    ) -> "TaskArtifactLocator":
        return cls(
            projection_context or artifact_key.projection_context or GLOBAL_PROJECTION_CONTEXT,
            artifact_key.without_projection(),
            task_id,
        )

    def to_uri(self, *, projected: bool = False) -> str:
        projection = self.projection_context if projected else GLOBAL_PROJECTION_CONTEXT
        key = self.artifact_key.without_projection()
        return (
            f"{A_TASK_SCHEME}/"
            f"{_encode_segment(projection)}/"
            f"{_encode_segment(key.domain)}/"
            f"{_encode_segment(key.content_kind)}/"
            f"{_format_phase_concrete(key.phase)}/"
            f"task/{_encode_segment(self.task_id)}"
        )

    def __str__(self) -> str:
        return self.to_uri(projected=False)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TaskArtifactLocator):
            return NotImplemented
        return (
            self.artifact_key.without_projection(),
            self.task_id,
        ) == (
            other.artifact_key.without_projection(),
            other.task_id,
        )

    def __hash__(self) -> int:
        key = self.artifact_key.without_projection()
        return hash((key.domain, key.content_kind, key.phase, self.task_id))


def _looks_like_int(value: str) -> bool:
    try:
        int(value)
        return True
    except ValueError:
        return False


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

    @property
    def locator(self) -> ArtifactInstanceLocator:
        return ArtifactInstanceLocator.from_key(
            self.declaration.artifact_id,
            self.fingerprint,
        )

@dataclass(frozen=True)
class ArtifactInstance(Generic[T]):
    handle: ArtifactHandle[T]

    @property
    def fingerprint(self) -> str:
        return self.handle.fingerprint

    @property
    def locator(self) -> ArtifactInstanceLocator:
        return self.handle.locator
