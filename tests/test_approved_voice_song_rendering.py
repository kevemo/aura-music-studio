from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aura_music_studio import renderers
from aura_music_studio.creation import CreateSongRequest, build_song_project
from aura_music_studio.models import ArrangementPlan, ProjectManifest
from aura_music_studio.project import ProjectWorkspace
from aura_music_studio.rights import RightsLedger, VoiceProfile


def _voice_source(
    projects_root: Path,
    *,
    name: str = "voice-source",
    allowed_uses: list[str] | None = None,
    reference_outside_project: bool = False,
) -> tuple[Path, RightsLedger, VoiceProfile]:
    source = projects_root / name
    if reference_outside_project:
        reference = projects_root.parent / "outside-reference.wav"
    else:
        reference = source / "input" / "voice_profiles" / "profile" / "reference.wav"
    reference.parent.mkdir(parents=True, exist_ok=True)
    reference.write_bytes(b"voice-reference")
    ledger = RightsLedger(source / ".aura_rights")
    profile = VoiceProfile(
        name="Approved Song Voice",
        owner_label="Voice Owner",
        reference_files=[str(reference.resolve())],
        consent_confirmed=True,
        consent_statement="I explicitly consent to approved singing use for this voice profile.",
        verification_state="attested",
        verification_method="consent_statement_attestation",
        allowed_uses=allowed_uses or ["singing", "backing_harmony", "voice_conversion"],
    )
    return source, ledger, ledger.save_voice(profile)


def _request(profile: VoiceProfile, *, source_project: str = "voice-source") -> CreateSongRequest:
    return CreateSongRequest(
        title="Consent Bound Song",
        lyrics="[Verse]\nThis is an approved voice test.",
        lyrics_rights_confirmed=True,
        vocal_mode="approved_voice",
        voice_profile_id=profile.id,
        voice_profile_project=source_project,
        preferred_engines=["mureka"],
    )


def _render_fixture(
    projects_root: Path,
    profile: VoiceProfile,
    *,
    source_project: str = "voice-source",
    preferred: list[str] | None = None,
) -> tuple[ProjectWorkspace, ProjectManifest, ArrangementPlan]:
    song = projects_root / "song"
    lyrics = song / "input" / "lyrics.txt"
    lyrics.parent.mkdir(parents=True, exist_ok=True)
    lyrics.write_text("[Verse]\nThis is an approved voice test.", encoding="utf-8")
    workspace = ProjectWorkspace(song)
    manifest = ProjectManifest(
        project_name="song",
        title="Consent Bound Song",
        lyrics_file="input/lyrics.txt",
        renderer={"preferred": preferred or ["mureka"]},
        project_dna={
            "vocal_mode": "approved_voice",
            "voice_profile_id": profile.id,
            "voice_profile_project": source_project,
        },
    )
    plan = ArrangementPlan(
        project_name="song",
        tempo_bpm=120.0,
        render_prompt="professional pop production",
    )
    return workspace, manifest, plan


def test_creation_binds_approved_voice_to_authoritative_source_project(tmp_path: Path):
    projects_root = tmp_path / "members" / "member-a"
    _, _, profile = _voice_source(projects_root)

    project = build_song_project(_request(profile), projects_root)

    manifest = yaml.safe_load((project / "project.yaml").read_text(encoding="utf-8"))
    assert manifest["project_dna"]["vocal_mode"] == "approved_voice"
    assert manifest["project_dna"]["voice_profile_id"] == profile.id
    assert manifest["project_dna"]["voice_profile_project"] == "voice-source"
    assert manifest["aura_create_request"]["voice_profile_project"] == "voice-source"


@pytest.mark.parametrize("source_name", ["", ".", "..", "../voice-source", "voice-source/child", "voice-source\\child"])
def test_creation_rejects_invalid_or_traversing_source_project(tmp_path: Path, source_name: str):
    projects_root = tmp_path / "members" / "member-a"
    _, _, profile = _voice_source(projects_root)
    request = _request(profile, source_project=source_name)

    with pytest.raises(ValueError):
        build_song_project(request, projects_root)

    assert not (projects_root / "consent-bound-song").exists()


def test_creation_rejects_missing_source_project(tmp_path: Path):
    projects_root = tmp_path / "members" / "member-a"
    _, _, profile = _voice_source(projects_root)

    with pytest.raises(ValueError, match="source project was not found"):
        build_song_project(_request(profile, source_project="missing-source"), projects_root)


@pytest.mark.parametrize(
    "voice_profile_id,voice_profile_project",
    [(None, "voice-source"), ("profile-id", None)],
)
def test_creation_requires_profile_and_source_binding(
    tmp_path: Path,
    voice_profile_id: str | None,
    voice_profile_project: str | None,
):
    projects_root = tmp_path / "members" / "member-a"
    request = CreateSongRequest(
        title="Consent Bound Song",
        lyrics="approved lyrics",
        lyrics_rights_confirmed=True,
        vocal_mode="approved_voice",
        voice_profile_id=voice_profile_id,
        voice_profile_project=voice_profile_project,
    )
    with pytest.raises(ValueError, match="consent-approved Aura Voice Profile and its source project"):
        build_song_project(request, projects_root)


def test_creation_rejects_revoked_profile_without_creating_song(tmp_path: Path):
    projects_root = tmp_path / "members" / "member-a"
    _, ledger, profile = _voice_source(projects_root)
    ledger.revoke_voice(profile.id, "Consent withdrawn before song creation")

    with pytest.raises(PermissionError, match="revoked"):
        build_song_project(_request(profile), projects_root)

    assert not (projects_root / "consent-bound-song").exists()


def test_creation_rejects_profile_not_allowed_for_singing(tmp_path: Path):
    projects_root = tmp_path / "members" / "member-a"
    _, _, profile = _voice_source(projects_root, allowed_uses=["backing_harmony"])

    with pytest.raises(PermissionError, match="singing"):
        build_song_project(_request(profile), projects_root)


def test_creation_requires_lyrics_for_approved_voice(tmp_path: Path):
    projects_root = tmp_path / "members" / "member-a"
    _, _, profile = _voice_source(projects_root)
    request = CreateSongRequest(
        title="Consent Bound Song",
        vocal_mode="approved_voice",
        voice_profile_id=profile.id,
        voice_profile_project="voice-source",
    )

    with pytest.raises(ValueError, match="requires lyrics"):
        build_song_project(request, projects_root)

    assert not (projects_root / "consent-bound-song").exists()


def test_mureka_approved_voice_rechecks_consent_and_uses_vocal_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects_root = tmp_path / "members" / "member-a"
    _, _, profile = _voice_source(projects_root)
    workspace, manifest, plan = _render_fixture(projects_root, profile)
    calls: list[tuple] = []

    class FakeMurekaClient:
        def __init__(self):
            calls.append(("init",))

        def clone_vocal(self, path: Path, description: str) -> str:
            calls.append(("clone", path, description))
            return "vocal-123"

        def lyrics_to_song(self, output: Path, **kwargs) -> Path:
            calls.append(("generate", kwargs))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"rendered")
            return output

    monkeypatch.setattr(renderers, "MurekaClient", FakeMurekaClient)

    result = renderers.MurekaRenderer().render(workspace, manifest, plan)

    assert result.audio_path.read_bytes() == b"rendered"
    assert calls[0] == ("init",)
    assert calls[1][0] == "clone"
    assert projects_root / "voice-source" in calls[1][1].parents
    assert calls[2][0] == "generate"
    assert calls[2][1]["vocal_id"] == "vocal-123"
    assert result.metadata["consent_checked_before_clone"] is True
    assert result.metadata["consent_checked_before_generation"] is True
    assert result.metadata["generic_vocal_fallback_allowed"] is False


def test_revoked_before_clone_never_calls_voice_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects_root = tmp_path / "members" / "member-a"
    _, ledger, profile = _voice_source(projects_root)
    workspace, manifest, plan = _render_fixture(projects_root, profile)
    ledger.revoke_voice(profile.id, "Consent withdrawn before provider execution")
    constructed = False

    class ForbiddenMurekaClient:
        def __init__(self):
            nonlocal constructed
            constructed = True
            raise AssertionError("provider must not be constructed after revocation")

    monkeypatch.setattr(renderers, "MurekaClient", ForbiddenMurekaClient)

    with pytest.raises(PermissionError, match="revoked"):
        renderers.MurekaRenderer().render(workspace, manifest, plan)

    assert constructed is False


def test_revoked_between_clone_and_generation_never_generates_song(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects_root = tmp_path / "members" / "member-a"
    _, ledger, profile = _voice_source(projects_root)
    workspace, manifest, plan = _render_fixture(projects_root, profile)
    generated = False

    class RevokingMurekaClient:
        def clone_vocal(self, path: Path, description: str) -> str:
            ledger.revoke_voice(profile.id, "Consent withdrawn while clone was in flight")
            return "vocal-123"

        def lyrics_to_song(self, output: Path, **kwargs) -> Path:
            nonlocal generated
            generated = True
            raise AssertionError("song generation must not run after consent revocation")

    monkeypatch.setattr(renderers, "MurekaClient", RevokingMurekaClient)

    with pytest.raises(PermissionError, match="revoked"):
        renderers.MurekaRenderer().render(workspace, manifest, plan)

    assert generated is False


def test_renderer_rejects_cross_project_reference_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects_root = tmp_path / "members" / "member-a"
    _, _, profile = _voice_source(projects_root, reference_outside_project=True)
    workspace, manifest, plan = _render_fixture(projects_root, profile)
    constructed = False

    class ForbiddenMurekaClient:
        def __init__(self):
            nonlocal constructed
            constructed = True
            raise AssertionError("provider must not receive an unconfined reference")

    monkeypatch.setattr(renderers, "MurekaClient", ForbiddenMurekaClient)

    with pytest.raises(PermissionError, match="project-confined"):
        renderers.MurekaRenderer().render(workspace, manifest, plan)

    assert constructed is False


def test_background_renderer_resolves_source_as_same_tenant_sibling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects_root = tmp_path / "members" / "member-a"
    source, _, profile = _voice_source(projects_root)
    workspace, manifest, plan = _render_fixture(projects_root, profile)
    cloned_path: Path | None = None

    class FakeMurekaClient:
        def clone_vocal(self, path: Path, description: str) -> str:
            nonlocal cloned_path
            cloned_path = path
            return "vocal-123"

        def lyrics_to_song(self, output: Path, **kwargs) -> Path:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"rendered")
            return output

    monkeypatch.setattr(renderers, "MurekaClient", FakeMurekaClient)

    renderers.MurekaRenderer().render(workspace, manifest, plan)

    assert cloned_path is not None
    assert source.resolve() in cloned_path.parents
    assert workspace.root.parent == source.resolve().parent


def test_approved_voice_never_falls_back_to_generic_renderer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects_root = tmp_path / "members" / "member-a"
    _, _, profile = _voice_source(projects_root)
    workspace, manifest, plan = _render_fixture(
        projects_root,
        profile,
        preferred=["acestep_api", "mureka", "yue"],
    )
    generic_called = False

    def forbidden_generic(*_args, **_kwargs):
        nonlocal generic_called
        generic_called = True
        raise AssertionError("generic renderer must never service an approved-voice request")

    monkeypatch.setenv("AURA_ACESTEP_API_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("AURA_YUE_CMD", "fake-yue")
    monkeypatch.delenv("MUREKA_API_KEY", raising=False)
    monkeypatch.setattr(renderers.AceStepApiRenderer, "render", forbidden_generic)
    monkeypatch.setattr(renderers.ExternalCommandRenderer, "render", forbidden_generic)

    with pytest.raises(RuntimeError, match="Renderer unavailable: mureka"):
        renderers.render_with_failover(workspace, manifest, plan)

    assert generic_called is False


def test_approved_voice_mureka_failure_does_not_fall_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects_root = tmp_path / "members" / "member-a"
    _, _, profile = _voice_source(projects_root)
    workspace, manifest, plan = _render_fixture(
        projects_root,
        profile,
        preferred=["mureka", "acestep_api"],
    )
    generic_called = False

    def fail_mureka(*_args, **_kwargs):
        raise RuntimeError("voice provider failed")

    def forbidden_generic(*_args, **_kwargs):
        nonlocal generic_called
        generic_called = True
        raise AssertionError("generic fallback must remain disabled")

    monkeypatch.setenv("MUREKA_API_KEY", "test-key")
    monkeypatch.setenv("AURA_ACESTEP_API_URL", "http://127.0.0.1:9999")
    monkeypatch.setattr(renderers.MurekaRenderer, "render", fail_mureka)
    monkeypatch.setattr(renderers.AceStepApiRenderer, "render", forbidden_generic)

    with pytest.raises(RuntimeError, match="voice provider failed"):
        renderers.render_with_failover(workspace, manifest, plan)

    assert generic_called is False


def test_legacy_approved_voice_manifest_without_source_fails_closed(tmp_path: Path):
    projects_root = tmp_path / "members" / "member-a"
    _, _, profile = _voice_source(projects_root)
    workspace, manifest, plan = _render_fixture(projects_root, profile)
    manifest.project_dna.pop("voice_profile_project")

    with pytest.raises(PermissionError, match="authoritative Voice House source binding"):
        renderers.render_with_failover(workspace, manifest, plan)


def test_tampered_source_binding_cannot_escape_current_member_workspace(tmp_path: Path):
    projects_root = tmp_path / "members" / "member-a"
    _, _, profile = _voice_source(projects_root)
    workspace, manifest, plan = _render_fixture(projects_root, profile, source_project="../member-b/voice-source")

    with pytest.raises(PermissionError, match="source project reference is invalid"):
        renderers.MurekaRenderer().render(workspace, manifest, plan)
