from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_agent_development_planner import AgentDevelopmentStore
from aura_music_studio.esp_agent_recruitment_academy import RecruitmentAcademyStore
from aura_music_studio.esp_owner_operations_intelligence import OwnerEspOperationsIntelligenceStore, router
from aura_music_studio.esp_role_dashboard_switch import DashboardPreferenceStore
from aura_music_studio.esp_shop_automation import ShopAutomationStore


def _user(accounts: AccountStore, email: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "Owner")


def _seed(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    agent = _user(accounts, "agent@example.com")
    creator = _user(accounts, "creator@example.com")
    now = datetime.now(timezone.utc).isoformat()

    AgentDevelopmentStore(accounts.db_path)
    RecruitmentAcademyStore(accounts.db_path)
    ShopAutomationStore(accounts.db_path)
    DashboardPreferenceStore(accounts.db_path)

    with accounts._connect() as con:
        plan_id = uuid4().hex
        con.execute(
            """INSERT INTO esp_agent_development_plans
               (id,agent_user_id,creator_user_id,objective,notes,status,outcome,baseline_metrics_json,
                baseline_evidence_id,created_at,updated_at,completed_at)
               VALUES (?,?,?,?,?,'active','',?,NULL,?,?,NULL)""",
            (plan_id, agent["id"], creator["id"], "Improve creator retention", "", "{}", now, now),
        )
        con.execute(
            """INSERT INTO esp_agent_development_milestones
               (id,plan_id,horizon_days,category,title,detail,target_metric,baseline_value,target_value,
                due_at,status,evidence_note,created_at,updated_at,completed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,'open','',?,?,NULL)""",
            (uuid4().hex, plan_id, 30, "Retention", "Raise watch time", "", "avg_watch_seconds", 40, 60, "2099-01-01", now, now),
        )
        con.execute(
            """INSERT INTO esp_agent_development_reviews
               (id,plan_id,reviewer_user_id,metrics_json,evidence_id,notes,created_at)
               VALUES (?,?,?,?,NULL,?,?)""",
            (uuid4().hex, plan_id, agent["id"], '{"avg_watch_seconds":50}', "Review", now),
        )
        con.execute(
            """INSERT INTO esp_agent_recruitment_learning
               (user_id,module_id,completed,evidence_note,completed_at,updated_at)
               VALUES (?,?,1,?,?,?)""",
            (agent["id"], "recruit-01", "Done", now, now),
        )
        con.execute(
            """INSERT INTO esp_agent_recruitment_attempts
               (id,user_id,scenario_id,option_index,correct,created_at)
               VALUES (?,?,?,?,1,?)""",
            (uuid4().hex, agent["id"], "scenario-affiliation", 1, now),
        )
        connection_id = uuid4().hex
        con.execute(
            """INSERT INTO esp_shop_connections
               (id,user_id,provider,account_label,external_account_ref,status,scopes_json,error_code,connected_at,updated_at)
               VALUES (?,?,?,?,?,'pending_oauth','[]','',NULL,?)""",
            (connection_id, creator["id"], "shopify", "Main Shop", "shop-1", now),
        )
        con.execute(
            """INSERT INTO esp_shop_workflows
               (id,user_id,name,trigger_type,conditions_json,actions_json,status,created_at,updated_at)
               VALUES (?,?,?,?, '[]','[]','active',?,?)""",
            (uuid4().hex, creator["id"], "Inventory alert", "inventory_low", now, now),
        )
        con.execute(
            """INSERT INTO esp_shop_action_queue
               (id,user_id,provider,action_type,external_object_ref,payload_json,estimated_spend_minor,currency,status,
                approval_note,provider_execution_ref,created_at,approved_at,executed_at,updated_at)
               VALUES (?,?,?,?,?,'{}',525,'GBP','awaiting_approval','','',?,NULL,NULL,?)""",
            (uuid4().hex, creator["id"], "shopify", "purchase_shipping_label", "order-1", now, now),
        )
        con.execute(
            "INSERT INTO esp_dashboard_preferences(user_id,mode,updated_at) VALUES (?,?,?)",
            (creator["id"], "creator", now),
        )
        con.execute(
            "INSERT INTO esp_dashboard_preferences(user_id,mode,updated_at) VALUES (?,?,?)",
            (agent["id"], "agent", now),
        )
    return accounts


def test_owner_operations_snapshot_counts_new_esp_systems(tmp_path):
    accounts = _seed(tmp_path)
    data = OwnerEspOperationsIntelligenceStore(accounts.db_path).snapshot()
    assert data["development"]["plans"] == 1
    assert data["development"]["active_plans"] == 1
    assert data["development"]["open_milestones"] == 1
    assert data["development"]["reviews"] == 1
    assert data["recruitment_academy"]["agents_started"] == 1
    assert data["recruitment_academy"]["module_completions"] == 1
    assert data["recruitment_academy"]["scenario_attempts"] == 1
    assert data["recruitment_academy"]["scenario_accuracy_percent"] == 100.0
    assert data["shop"]["connections"] == 1
    assert data["shop"]["connection_states"]["pending_oauth"] == 1
    assert data["shop"]["active_workflows"] == 1
    assert data["shop"]["awaiting_approval"] == 1
    assert data["shop"]["executed_actions"] == 0
    assert data["dashboard_views"] == {"creator_view": 1, "agent_view": 1, "preferences": 2}


def test_owner_operations_snapshot_has_strict_privacy_and_execution_boundaries(tmp_path):
    accounts = _seed(tmp_path)
    data = OwnerEspOperationsIntelligenceStore(accounts.db_path).snapshot()
    assert data["privacy_boundary"] == "aggregate_operational_metadata_only"
    assert data["private_creator_content_included"] is False
    assert data["raw_backstage_evidence_included"] is False
    assert data["raw_oauth_tokens_included"] is False
    assert data["subscription_grants_esp_access"] is False
    assert data["esp_role_assignment_authority"] == "owner_only"
    assert data["development"]["automatic_penalties"] is False
    assert data["shop"]["provider_execution_claim_requires_reference"] is True


def test_owner_operations_routes_are_owner_namespace_only():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/owner/esp-operations-intelligence" in paths
    assert "/owner/api/esp-operations-intelligence" in paths
    assert all(path is None or path.startswith("/owner/") for path in paths)
