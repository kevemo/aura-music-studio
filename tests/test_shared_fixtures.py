from aura_music_studio.capabilities import CapabilityStatus
from aura_music_studio.testing import (
    test_authorization_context as make_authorization_context,
    test_capability as make_capability,
    test_event as make_event,
    test_owner_override as make_owner_override,
    test_user as make_user,
)


def test_shared_fixtures_are_explicit_test_objects():
    assert make_user().user_id == "test-user"
    assert make_authorization_context().user_id == "test-user"
    assert make_capability().status is CapabilityStatus.NOT_CONFIGURED
    assert make_capability().provider == "test"
    assert make_event().source == "test"
    assert make_owner_override().override_id.startswith("override-test-")
