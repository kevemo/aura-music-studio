from aura_music_studio.capabilities import CapabilityStatus
from aura_music_studio.testing import (
    test_authorization_context as make_auth, test_capability as make_cap,
    test_event as make_event, test_owner_override as make_override, test_user as make_user,
)


def test_shared_fixtures_are_explicit_test_objects():
    assert make_user().user_id == "test-user"
    assert make_auth().plan_id == "free"
    assert make_cap().status is CapabilityStatus.NOT_CONFIGURED
    assert make_cap().provider == "test"
    assert make_event().source == "test"
    assert make_override().override_id.startswith("override-test-")
