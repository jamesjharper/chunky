import pytest

from chunky.Artifacts.artifact_models import (
    ArtifactInstanceLocator,
    ArtifactKey,
    ArtifactRefLocator,
    TaskArtifactLocator,
)


def test_short_a_ref_forms_parse_and_canonicalize() -> None:
    assert str(ArtifactKey.parse("source/audio")) == "a-ref:/global/source/audio/any"
    assert str(ArtifactKey.parse("utterance_detections/utterance_stream/201")) == (
        "a-ref:/global/utterance_detections/utterance_stream/201"
    )

    projected = ArtifactRefLocator.parse(
        "speech_processing/audio_preprocessing/audio_settings"
    )
    assert projected.projection_context == "speech_processing"
    assert projected.artifact_key == ArtifactKey("audio_preprocessing", "audio_settings")
    assert projected.to_uri(projected=True) == (
        "a-ref:/speech_processing/audio_preprocessing/audio_settings/any"
    )


def test_canonical_a_ref_still_parses() -> None:
    assert ArtifactKey.parse("a-ref:/global/source/audio/any") == ArtifactKey(
        "source", "audio"
    )


def test_legacy_artifact_key_strings_are_rejected() -> None:
    with pytest.raises(ValueError):
        ArtifactKey.parse("source//audio")
    with pytest.raises(ValueError):
        ArtifactKey.parse("utterance_detections//utterance_stream#201")


def test_concrete_locators_still_require_their_scheme() -> None:
    with pytest.raises(ValueError):
        ArtifactInstanceLocator.parse("global/source/audio/none/fingerprint/fp")
    with pytest.raises(ValueError):
        TaskArtifactLocator.parse("global/render/webvtt_transcript/none/task/task-1")
