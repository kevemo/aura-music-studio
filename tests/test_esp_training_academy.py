from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_training_academy import (
    CreateCourse,
    CreateCourseVersion,
    PublishCourseVersion,
    TrainingAcademyStore,
    TrainingLesson,
    TrainingQuestion,
    UpdateCourseVersion,
)


def _user(accounts: AccountStore, email: str, name: str) -> dict:
    signup = accounts.signup(email, name, "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "ESP Test Owner")


def _membership(esp: EspStore, user_id: str, role: str, region: str = "UK+") -> dict:
    with esp._connect() as con:
        con.execute(
            """INSERT INTO esp_memberships(user_id,status,roles,tiktok_handle,region,approved_at,approved_by,updated_at)
               VALUES (?,'active',?,'',?,datetime('now'),'test-owner',datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET status='active',roles=excluded.roles,region=excluded.region,
                 approved_at=excluded.approved_at,approved_by=excluded.approved_by,updated_at=excluded.updated_at""",
            (user_id, role, region),
        )
    return esp.membership(user_id)


def _course_payload() -> CreateCourse:
    return CreateCourse(
        slug="creator-live-foundations",
        title="Creator LIVE Foundations",
        description="Versioned Creator training.",
        role_scopes=["creator"],
        region_scopes=["UK+"],
        required=True,
        pass_percent=80,
        lessons=[
            TrainingLesson(id="lesson-1", title="Prepare", body="Prepare a safe LIVE plan."),
            TrainingLesson(id="lesson-2", title="Review", body="Review the result."),
        ],
        questions=[
            TrainingQuestion(
                id="q1",
                prompt="Which action is required?",
                options=["Ignore policy", "Follow policy"],
                correct_options=[1],
            )
        ],
    )


def test_published_training_version_is_immutable_and_answer_key_is_hidden(tmp_path):
    accounts = AccountStore(tmp_path / "academy.sqlite3")
    esp = EspStore(accounts)
    owner = _user(accounts, "owner@example.com", "Owner")
    creator = _user(accounts, "creator@example.com", "Creator")
    owner_membership = _membership(esp, owner["id"], "owner", "Global")
    creator_membership = _membership(esp, creator["id"], "creator", "UK+")
    store = TrainingAcademyStore(esp)

    draft = store.create_course(_course_payload(), actor=owner["id"])
    assert draft["status"] == "draft"
    assert draft["version"] == 1
    assert draft["questions"][0]["correct_options"] == [1]

    with pytest.raises(PermissionError, match="high_impact_confirmation_required"):
        store.publish(
            draft["course_id"],
            1,
            actor=owner["id"],
            confirmation=PublishCourseVersion(confirm_publish=False),
        )

    store.publish(
        draft["course_id"],
        1,
        actor=owner["id"],
        confirmation=PublishCourseVersion(confirm_publish=True, reason="Approved release"),
    )
    member_course = store.latest_published(draft["course_id"])
    assert member_course["status"] == "published"
    assert "correct_options" not in member_course["questions"][0]
    assert store.audience_matches(member_course, creator_membership) is True
    # The learner catalogue applies role/region scopes even to an Owner account. Owner course
    # administration is intentionally exposed through separate Owner-only APIs instead of
    # bypassing learner audience targeting.
    assert store.audience_matches(member_course, owner_membership) is False

    with pytest.raises(PermissionError, match="immutable"):
        store.update_draft(
            draft["course_id"],
            1,
            UpdateCourseVersion(
                expected_revision=1,
                title="Changed published title",
                lessons=_course_payload().lessons,
                questions=_course_payload().questions,
            ),
            actor=owner["id"],
        )


def test_historical_completion_and_certificate_survive_new_course_version(tmp_path):
    accounts = AccountStore(tmp_path / "academy-history.sqlite3")
    esp = EspStore(accounts)
    owner = _user(accounts, "owner2@example.com", "Owner")
    creator = _user(accounts, "creator2@example.com", "Creator")
    _membership(esp, owner["id"], "owner", "Global")
    _membership(esp, creator["id"], "creator", "UK+")
    store = TrainingAcademyStore(esp)

    draft = store.create_course(_course_payload(), actor=owner["id"])
    course_id = draft["course_id"]
    store.publish(
        course_id,
        1,
        actor=owner["id"],
        confirmation=PublishCourseVersion(confirm_publish=True),
    )
    store.complete_lesson(creator["id"], course_id, 1, "lesson-1")
    first_result = store.submit_exam(creator["id"], course_id, 1, {"q1": [1]})
    assert first_result["passed"] is True
    assert first_result["certificate_id"]
    original_progress = store.progress(creator["id"], course_id, 1)
    assert original_progress["certificate"]["id"] == first_result["certificate_id"]

    v2 = store.new_version(
        course_id,
        CreateCourseVersion(
            title="Creator LIVE Foundations — Updated",
            lessons=[
                *_course_payload().lessons,
                TrainingLesson(id="lesson-3", title="New lesson", body="Added later."),
            ],
        ),
        actor=owner["id"],
    )
    assert v2["version"] == 2
    assert v2["status"] == "draft"
    store.publish(
        course_id,
        2,
        actor=owner["id"],
        confirmation=PublishCourseVersion(confirm_publish=True),
    )

    old_after_publish = store.progress(creator["id"], course_id, 1)
    assert old_after_publish["lessons_total"] == 2
    assert old_after_publish["certificate"]["id"] == first_result["certificate_id"]
    assert store.progress(creator["id"], course_id, 2)["lessons_total"] == 3
    assert store.progress(creator["id"], course_id, 2)["certificate"] is None


def test_training_routes_cover_member_progress_and_owner_version_lifecycle():
    from aura_music_studio.esp_training_academy import router

    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/api/training/courses" in paths
    assert "/command-center/api/training/courses/{course_id}" in paths
    assert "/command-center/api/training/courses/{course_id}/versions/{version}/exam" in paths
    assert "/command-center/api/training/owner/courses" in paths
    assert "/command-center/api/training/owner/courses/{course_id}/versions/{version}/publish" in paths