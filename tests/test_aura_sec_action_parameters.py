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


def test_command_parameters_must_be_object():
    with pytest.raises(ValueError, match="must be an object"):
        validated_command_parameters(ActionType.RUN_QUICK_SCAN, {"command_parameters": "shell text"})


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


def test_domain_actions_accept_hostname_not_url_credentials_port_path_or_ip():
    assert validated_command_parameters(
        ActionType.BLOCK_DOMAIN,
        {"command_parameters": {"domain": "Login.Example.com."}},
    ) == {"domain": "login.example.com"}
    invalid_targets = (
        "https://example.com",
        "example.com/path",
        "user@example.com",
        "example.com:443",
        "127.0.0.1",
        "192.168.1.20",
    )
    for invalid in invalid_targets:
        with pytest.raises(ValueError):
            validated_command_parameters(
                ActionType.BLOCK_DOMAIN,
                {"command_parameters": {"domain": invalid}},
            )


def test_domain_action_rejects_unknown_fields_and_shell_metacharacters():
    with pytest.raises(ValueError):
        validated_command_parameters(
            ActionType.BLOCK_DOMAIN,
            {"command_parameters": {"domain": "example.com", "url": "https://example.com"}},
        )
    for invalid in ("example.com;whoami", "example.com&&whoami", "$(whoami).example.com"):
        with pytest.raises(ValueError):
            validated_command_parameters(
                ActionType.BLOCK_DOMAIN,
                {"command_parameters": {"domain": invalid}},
            )


def test_update_and_recovery_actions_accept_only_verified_opaque_ids():
    assert validated_command_parameters(
        ActionType.APPLY_VERIFIED_UPDATE,
        {"command_parameters": {"update_id": "vendor-update:2026.08.29"}},
    ) == {"update_id": "vendor-update:2026.08.29"}
    assert validated_command_parameters(
        ActionType.RESTORE_RECOVERY_POINT,
        {"command_parameters": {"recovery_point_id": "recovery:point-001"}},
    ) == {"recovery_point_id": "recovery:point-001"}
