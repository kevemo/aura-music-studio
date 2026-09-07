from __future__ import annotations

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_agent_operations import AgentOperationsStore
from aura_music_studio.esp_agent_roster import AgentRosterStore
from aura_music_studio.esp_backstage_evidence import BackstageEvidenceStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_creator_discovery import CreateLeadRequest, CreatorDiscoveryStore
from aura_music_studio.esp_level_up import EspAgentAssignmentStore
from aura_music_studio.esp_niche import EspNicheStore
from aura_music_studio.esp_progress import EspProgressStore
from aura_music_studio.esp_owner_network_intelligence import OwnerEspNetworkIntelligenceStore, router


def _active(accounts: AccountStore, esp: EspStore, email: str, role: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    _request, token = esp.request_access(user["id"], role, email.split("@")[0], "UK+", "test")
    esp.decide(token, "approve", role, "Owner")
    return user


def _fixture(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    EspNicheStore(esp)
    agent = _active(accounts, esp, "agent@example.com", "agent")
    creator = _active(accounts, esp, "creator@example.com", "creator")
    creator_missing = _active(accounts, esp, "creator2@example.com", "creator")
    both = _active(accounts, esp, "both@example.com", "both")

    assignments = EspAgentAssignmentStore(esp)
    assignments.assign(agent["id"], creator["id"], actor="Owner")
    roster = AgentRosterStore(esp, assignments)
    progress = EspProgressStore(esp)
    evidence = BackstageEvidenceStore(esp, assignments, progress)
    operations = AgentOperationsStore(roster)
    discovery = CreatorDiscoveryStore(esp.db_path)

    evidence.record(
        agent["id"], creator["id"], owner=False, source_kind="manual", source_label="Manage Creator review",
        captured_at="2099-01-01T00:00:00+00:00", period_label="Current snapshot",
        metrics={"avg_watch_seconds": 70, "duration_minutes": 120}, extraction_status="manual_confirmed",
    )
    roster.add_followup(agent["id"], creator["id"], title="Review next LIVE")
    operations.add_checkin(
        agent["id"], creator["id"], checkin_type="performance", summary="Reviewed latest creator data",
        next_action="Test one retention improvement",
    )
    operations.start_plan(
        agent["id"], creator["id"], pathway="optimise", objective="Improve retention with one controlled change"
    )
    discovery.create(
        agent["id"],
        CreateLeadRequest(
            tiktok_handle="publiclead",
            display_name="Public Lead",
            region="UK+",
            niche="music",
            source_kind="public_profile",
            source_ref="https://example.com/publiclead",
        ),
    )
    accounts.record_usage(creator["id"], "music_project_created")
    accounts.record_usage(creator["id"], "aura_creative_directive")

    with accounts._connect() as con:
        con.execute(
            """INSERT INTO esp_training_progress(user_id,resource_id,status,percent,updated_at)
               VALUES (?,?,?, ?, datetime('now'))""",
            (creator["id"], "creator-companion", "started", 75),
        )
        con.execute(
            """INSERT INTO esp_training_progress(user_id,resource_id,status,percent,updated_at)
               VALUES (?,?,?, ?, datetime('now'))""",
            (agent["id"], "agent-apprentice", "completed", 100),
        )
    return accounts, esp, agent, creator, creator_missing, both


def test_owner_network_snapshot_aggregates_roles_and_usage(tmp_path):
    accounts, _esp, _agent, _creator, _missing, _both = _fixture(tmp_path)
    data = OwnerEspNetworkIntelligenceStore(accounts.db_path).snapshot()
    assert data["roles"]["active_esp"] == 4
    assert data["roles"]["creators"] == 3
    assert data["roles"]["agents"] == 2
    assert data["roles"]["both"] == 1
    assert data["usage"]["events"] == 2
    assert data["usage"]["active_users"] == 1
    assert {row["event_type"] for row in data["usage"]["event_types"]} == {
        "music_project_created", "aura_creative_directive"
    }


def test_owner_network_snapshot_tracks_evidence_freshness_without_backstage_claim(tmp_path):
    accounts, _esp, _agent, creator, creator_missing, _both = _fixture(tmp_path)
    data = OwnerEspNetworkIntelligenceStore(accounts.db_path).snapshot()
    assert data["evidence"]["direct_backstage_access"] is False
    assert data["evidence"]["records"] == 1
    assert data["evidence"]["creators_with_evidence"] == 1
    assert data["evidence"]["freshness"]["current"] == 1
    assert data["evidence"]["freshness"]["missing"] == 2
    needs = {row["user_id"] for row in data["evidence"]["needs_update"]}
    assert creator_missing["id"] in needs
    assert creator["id"] not in needs


def test_owner_network_snapshot_tracks_mentoring_training_and_recruitment(tmp_path):
    accounts, _esp, _agent, _creator, _missing, _both = _fixture(tmp_path)
    data = OwnerEspNetworkIntelligenceStore(accounts.db_path).snapshot()
    assert data["mentoring"]["active_assignments"] == 1
    assert data["mentoring"]["assigned_agents"] == 1
    assert data["mentoring"]["assigned_creators"] == 1
    assert data["mentoring"]["open_checkins"] == 1
    assert data["mentoring"]["open_followups"] == 1
    assert data["mentoring"]["active_success_plans"] == 1
    assert data["training"]["records"] == 2
    assert data["training"]["learners"] == 2
    assert data["training"]["average_percent"] == 87.5
    assert data["recruitment"]["leads"] == 1
    assert data["recruitment"]["pipeline"]["new"] == 1


def test_owner_network_snapshot_preserves_privacy_and_entitlement_boundaries(tmp_path):
    accounts, _esp, _agent, _creator, _missing, _both = _fixture(tmp_path)
    data = OwnerEspNetworkIntelligenceStore(accounts.db_path).snapshot()
    assert data["privacy_boundary"] == "aggregate_operational_metadata_only"
    assert data["private_creative_content_included"] is False
    assert data["esp_role_assignment_authority"] == "owner_only"
    assert data["subscription_grants_esp_access"] is False


def test_owner_network_router_registers_owner_only_intelligence_paths():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/owner/esp-intelligence" in paths
    assert "/owner/api/esp-intelligence" in paths
