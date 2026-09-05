import json

from aura_music_studio.audit import AuditLogger
from aura_music_studio.shared_audit import AuditWriter, redact_sensitive


def test_sensitive_values_are_redacted():
    value = redact_sensitive({"api_key": "abc", "nested": {"password": "p", "safe": 1}})
    assert value == {"api_key": "[REDACTED]", "nested": {"password": "[REDACTED]", "safe": 1}}


def test_audit_writer_preserves_hash_chain_and_redacts(tmp_path):
    logger = AuditLogger(tmp_path)
    writer = AuditWriter(logger)
    writer.write(
        actor_id="owner", role="owner", action="config.change",
        target_type="provider", target_id="youtube", correlation_id="corr1",
        metadata={"api_token": "secret", "status": "enabled"},
    )
    assert logger.verify() is True
    event = json.loads(logger.audit_path.read_text().splitlines()[0])
    assert event["details"]["metadata"]["api_token"] == "[REDACTED]"
    assert event["details"]["metadata"]["status"] == "enabled"
