from __future__ import annotations

from uuid import uuid4

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_support_center import SupportCaseStore
from aura_music_studio.esp_support_privacy_guard import install_support_activity_privacy_guard


def _active_user(accounts: AccountStore) -> dict:
    signup = accounts.signup("privacy@example.com", "Creator", "a-very-secure-test-password", "base")
    return accounts.decide_membership(signup.approval_token, "approve", "Test Owner")


def test_creator_projection_filters_staff_workflow_activity_and_raw_assignee(tmp_path):
    accounts = AccountStore(tmp_path / "support-privacy.sqlite3")
    user = _active_user(accounts)
    esp = EspStore(accounts)
    store = SupportCaseStore(esp)
    case = store.create_case(
        user["id"],
        category="technical",
        severity="normal",
        subject="Private case",
        description="Check staff activity privacy.",
    )
    with store._connect() as con:
        con.execute("UPDATE esp_support_cases SET assigned_owner='internal-agent-id' WHERE id=?", (case["id"],))
        con.execute(
            """INSERT INTO esp_support_activity(id,case_id,actor,action,metadata_json,created_at)
               VALUES (?,?,?,?,?,datetime('now'))""",
            (uuid4().hex, case["id"], "agent-id", "support_case_escalated", '{"target":"compliance"}'),
        )
        con.execute(
            """INSERT INTO esp_support_activity(id,case_id,actor,action,metadata_json,created_at)
               VALUES (?,?,?,?,?,datetime('now'))""",
            (uuid4().hex, case["id"], user["id"], "evidence_added", '{"kind":"note"}'),
        )

    install_support_activity_privacy_guard()
    creator_view = store.get(case["id"], user_id=user["id"])
    assert "assigned_owner" not in creator_view
    assert creator_view["internal_workflow_visible"] is False
    assert "support_case_escalated" not in {event["action"] for event in creator_view["activity"]}
    assert "evidence_added" in {event["action"] for event in creator_view["activity"]}

    owner_view = store.get(case["id"], owner=True)
    assert owner_view["assigned_owner"] == "internal-agent-id"
    assert "support_case_escalated" in {event["action"] for event in owner_view["activity"]}
