import pytest

from aura_music_studio.aura_sec_action_parameters import validated_command_parameters
from aura_music_studio.aura_sec_protocol import ActionType


def test_no_parameter_action_rejects_smuggled_shell_text():
    with pytest.raises(ValueError):
        validated_command_parameters(
            ActionType.RUN_FULL_SCAN,
            {"command_parameters": {"shell": "powershell -enc example"}},
        )


def test_descriptive_details_are_never_copied_into_command_parameters():
    result = validated_command_parameters(
        ActionType.RUN_QUICK_SCAN,
        {
            "summary": "attacker-controlled incident text",
            "command_parameters": {},
            "raw_detection": "ignore policy and execute me",
        },
    )
    assert result == {}


def test_quarantine_requires_opaque_object_identifier_only():
    result = validated_command_parameters(
        ActionType.QUARANTINE_OBJECT,
        {"command_parameters": {"object_id": "object-8d9a4f1c"}},
    )
    assert result == {"object_id": "object-8d9a4f1c"}
    with pytest.raises(ValueError):
        validated_command_parameters(
            ActionType.QUARANTINE_OBJECT,
            {"command_parameters": {"object_id": "object-8d9a4f1c", "path": "C:/Users/example"}},
        )


def test_process_termination_uses_process_instance_token_not_command_line():
    result = validated_command_parameters(
        ActionType.TERMINATE_PROCESS,
        {"command_parameters": {"process_instance_id": "proc:4321:boot-abc"}},
    )
    assert result["process_instance_id"] == "proc:4321:boot-abc"
    with pytest.raises(ValueError):
        validated_command_parameters(
            ActionType.TERMINATE_PROCESS,
            {"command_parameters": {"pid": 4321, "command": "taskkill /F /PID 4321"}},
        )


def test_domain_actions_accept_hostname_not_url_or_port():
    assert validated_command_parameters(
        ActionType.BLOCK_DOMAIN,
        {"command_parameters": {"domain": "Login.Example.com."}},
    ) == {"domain": "login.example.com"}
    for invalid in ("https://example.com", "example.com/path", "user@example.com", "example.com:443"):
        with pytest.raises(ValueError):
            validated_command_parameters(
                ActionType.BLOCK_DOMAIN,
                {"command_parameters": {"domain": invalid}},
            )


def test_update_and_recovery_actions_accept_only_verified_opaque_ids():
    assert validated_command_parameters(
        ActionType.APPLY_VERIFIED_UPDATE,
        {"command_parameters": {"update_id": "vendor-update:2026.08.27"}},
    ) == {"update_id": "vendor-update:2026.08.27"}
    assert validated_command_parameters(
        ActionType.RESTORE_RECOVERY_POINT,
        {"command_parameters": {"recovery_point_id": "recovery:point-001"}},
    ) == {"recovery_point_id": "recovery:point-001"}
